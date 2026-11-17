from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import Campaign, JobState
from .geography import coordinates, distance_m, maps_url


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    target_count INTEGER NOT NULL CHECK (target_count > 0),
    config_json TEXT NOT NULL,
    approved_at TEXT,
    cancelled_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    source TEXT NOT NULL,
    query_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','running','waiting','completed','partial','failed','blocked','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT,
    error TEXT,
    paused_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS jobs_state_next_run_idx ON jobs(state, next_run_at);

CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    open_until TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_usage (
    source TEXT NOT NULL,
    period TEXT NOT NULL,
    credits INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, period)
);

CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone TEXT,
    website TEXT,
    category TEXT,
    address TEXT,
    review_rating TEXT,
    review_count TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    name TEXT NOT NULL,
    title TEXT,
    seniority TEXT,
    location TEXT,
    linkedin_url TEXT,
    verification_status TEXT NOT NULL DEFAULT 'candidate',
    confidence REAL NOT NULL DEFAULT 0.5,
    review_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_companies (
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    fit_score INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.5,
    review_status TEXT NOT NULL DEFAULT 'pending',
    exclusion_reason TEXT,
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, company_id)
);

CREATE TABLE IF NOT EXISTS campaign_people (
    person_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    identity_key TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    location TEXT,
    linkedin_url TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'candidate',
    confidence REAL NOT NULL DEFAULT 0.5,
    review_status TEXT NOT NULL DEFAULT 'pending',
    source_excerpt TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (campaign_id, identity_key)
);

CREATE TABLE IF NOT EXISTS website_scans (
    domain TEXT NOT NULL,
    role_key TEXT NOT NULL,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    status TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    scanned_at TEXT NOT NULL,
    PRIMARY KEY (domain, role_key)
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    observed_value TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE (entity_type, entity_id, field, source_url, observed_value)
);

CREATE TABLE IF NOT EXISTS email_checks (
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    email TEXT NOT NULL,
    status TEXT NOT NULL,
    mail_host TEXT,
    error TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, email)
);

CREATE TABLE IF NOT EXISTS maps_results (
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    fingerprint TEXT NOT NULL,
    company_id TEXT REFERENCES companies(company_id),
    title TEXT NOT NULL,
    phone TEXT,
    emails TEXT,
    website TEXT,
    category TEXT,
    address TEXT,
    review_rating TEXT,
    review_count TEXT,
    source_url TEXT,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, fingerprint)
);
"""

MAX_JOB_ATTEMPTS = 3
CIRCUIT_BREAKER_SECONDS = 900


def connect(path: Path | str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path: Path | str) -> None:
    with closing(connect(path)) as connection, connection:
        connection.executescript(SCHEMA)
        _add_column(connection, "maps_results", "company_id", "TEXT REFERENCES companies(company_id)")
        geography_missing = "latitude" not in {row["name"] for row in connection.execute("PRAGMA table_info(companies)")}
        for column, definition in (("latitude", "REAL"), ("longitude", "REAL"), ("maps_url", "TEXT"), ("place_id", "TEXT")):
            _add_column(connection, "companies", column, definition)
        if geography_missing:
            # Recover existing locations from preserved source rows, without recollecting.
            for row in connection.execute("SELECT company_id, raw_json FROM maps_results WHERE company_id IS NOT NULL ORDER BY updated_at"):
                update_company_geography(connection, row["company_id"], json.loads(row["raw_json"]))
        _add_column(connection, "campaigns", "approved_at", "TEXT")
        _add_column(connection, "campaigns", "cancelled_at", "TEXT")
        _add_column(connection, "campaigns", "results_frozen_at", "TEXT")
        _add_column(connection, "campaign_companies", "result_json", "TEXT")
        _add_column(connection, "jobs", "progress_json", "TEXT")
        _add_column(connection, "campaign_companies", "selected", "INTEGER NOT NULL DEFAULT 1")
        _add_column(connection, "campaign_companies", "selection_reason", "TEXT")
        _add_column(connection, "jobs", "paused_at", "TEXT")
        _add_column(connection, "jobs", "lease_owner", "TEXT")
        _add_column(connection, "jobs", "lease_expires_at", "TEXT")
        _add_column(connection, "jobs", "started_at", "TEXT")
        _add_column(connection, "jobs", "finished_at", "TEXT")
        _add_column(connection, "maps_results", "company_id", "TEXT REFERENCES companies(company_id)")
        _add_column(connection, "people", "verification_status", "TEXT NOT NULL DEFAULT 'candidate'")
        _add_column(connection, "people", "confidence", "REAL NOT NULL DEFAULT 0.5")
        _add_column(connection, "people", "review_status", "TEXT NOT NULL DEFAULT 'pending'")
        connection.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE state = 'running' AND lease_expires_at IS NULL",
            (datetime.now(timezone.utc).isoformat(),),
        )
        connection.execute(
            """
            UPDATE campaigns SET approved_at = created_at
            WHERE approved_at IS NULL
              AND json_extract(config_json, '$.manual_approval_required') = 0
            """
        )
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT OR IGNORE INTO campaign_companies (
                campaign_id, company_id, fit_score, confidence, review_status, added_at, updated_at
            )
            SELECT j.campaign_id, m.company_id, 0, 0.5, 'pending', MIN(m.created_at), ?
            FROM maps_results AS m
            JOIN jobs AS j ON j.job_id = m.job_id
            WHERE m.company_id IS NOT NULL
            GROUP BY j.campaign_id, m.company_id
            """,
            (now,),
        )


def _add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def update_company_geography(connection, company_id, raw):
    lat, lon = coordinates(raw)
    place_id = raw.get("place_id")
    connection.execute(
        """UPDATE companies SET latitude = COALESCE(?, latitude), longitude = COALESCE(?, longitude),
           maps_url = COALESCE(?, maps_url), place_id = COALESCE(?, place_id) WHERE company_id = ?""",
        (lat, lon, maps_url(raw), place_id if isinstance(place_id, str) and place_id else None, company_id),
    )


def create_campaign(path: Path | str, campaign: Campaign) -> tuple[str, list[str]]:
    initialize(path)
    planned = campaign.maps_jobs()
    for excluded_id in campaign.exclude_campaign_ids:
        get_campaign(path, excluded_id)
    campaign_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    config = {
        "name": campaign.name,
        "mode": campaign.mode,
        "target_count": campaign.target_count,
        "business_terms": list(campaign.business_terms),
        "location": dict(campaign.location),
        "schema_version": 2,
        "manual_approval_required": campaign.manual_approval_required,
        "collect_emails": campaign.collect_emails,
        "collect_socials": campaign.collect_socials,
        "negative_terms": list(campaign.negative_terms),
        "required_fields": list(campaign.required_fields),
        "search_area": dict(campaign.search_area) if campaign.search_area else None,
        "neighborhoods": list(campaign.neighborhoods),
        "collection_limit": campaign.collection_limit or campaign.target_count,
        "language": campaign.language,
        **{key: getattr(campaign, key) for key in ("depth", "grid_size", "deep_scan", "max_queries", "min_rating", "min_reviews", "no_website", "verify_emails", "exclude_campaign_ids", "match_business_terms", "category_terms")},
    }
    job_ids: list[str] = []

    with closing(connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO campaigns (
                campaign_id, name, mode, target_count, config_json, approved_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                campaign.name,
                campaign.mode,
                campaign.target_count,
                json.dumps(config),
                None if campaign.manual_approval_required else now,
                now,
            ),
        )
        for query in planned:
            job_id = str(uuid4())
            job_ids.append(job_id)
            connection.execute(
                "INSERT INTO jobs (job_id, campaign_id, source, query_json, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, campaign_id, "maps", json.dumps(query), JobState.QUEUED, now, now),
            )

    return campaign_id, job_ids


def list_jobs(path: Path | str, campaign_id: str | None = None) -> list[dict[str, Any]]:
    initialize(path)
    with closing(connect(path)) as connection:
        where = "WHERE campaign_id = ?" if campaign_id else ""
        parameters = (campaign_id,) if campaign_id else ()
        rows = connection.execute(
            f"""
            SELECT job_id, campaign_id, source, state, attempts, next_run_at,
                   paused_at, lease_expires_at, started_at, finished_at, error
            FROM jobs {where} ORDER BY created_at
            """,
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


def claim_next_maps_job(path: Path | str, lease_seconds: int = 300) -> dict[str, Any] | None:
    return claim_next_job(path, "maps", lease_seconds)


def claim_next_job(path: Path | str, source: str, lease_seconds: int = 300) -> dict[str, Any] | None:
    initialize(path)
    now_value = datetime.now(timezone.utc)
    now = now_value.isoformat()
    owner = str(uuid4())
    expires = (now_value + timedelta(seconds=lease_seconds)).isoformat()
    with closing(connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE jobs SET state = ?, error = ?, lease_owner = NULL,
                            lease_expires_at = NULL, updated_at = ?
            WHERE source = ? AND state = 'running'
              AND lease_expires_at <= ? AND attempts >= ?
              AND campaign_id IN (SELECT campaign_id FROM campaigns WHERE mode='companies' AND json_extract(config_json,'$.schema_version')=2)
            """,
            (
                JobState.FAILED,
                "worker lease expired after maximum attempts",
                now,
                source,
                now,
                MAX_JOB_ATTEMPTS,
            ),
        )
        row = connection.execute(
            """
            SELECT j.job_id, j.campaign_id, j.source, j.query_json, j.state, j.attempts,
                   c.target_count, c.config_json
            FROM jobs AS j
            JOIN campaigns AS c USING (campaign_id)
            WHERE j.source = ? AND c.approved_at IS NOT NULL AND c.cancelled_at IS NULL
              AND c.mode = 'companies' AND json_extract(c.config_json, '$.schema_version') = 2
              AND NOT EXISTS (
                  SELECT 1 FROM source_health AS h
                  WHERE h.source = j.source AND h.open_until > ?
              )
              AND (
                (j.state = 'queued' AND (j.next_run_at IS NULL OR j.next_run_at <= ?))
                OR (j.state = 'waiting' AND j.paused_at IS NULL AND j.next_run_at <= ?)
                OR (j.state = 'running' AND j.lease_expires_at <= ? AND j.attempts < ?)
            )
            ORDER BY CASE j.state WHEN 'queued' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END, j.created_at
            LIMIT 1
            """,
            (source, now, now, now, now, MAX_JOB_ATTEMPTS),
        ).fetchone()
        if row is None:
            connection.commit()
            freeze_completed_campaigns(path)
            return None
        connection.execute(
            """
            UPDATE jobs SET state = ?, attempts = attempts + 1, error = NULL,
                            next_run_at = NULL, lease_owner = ?, lease_expires_at = ?,
                            started_at = COALESCE(started_at, ?), finished_at = NULL, updated_at = ?
            WHERE job_id = ?
            """,
            (JobState.RUNNING, owner, expires, now, now, row["job_id"]),
        )
        connection.commit()
    # Preserve newly exhausted jobs before another campaign can update the global catalogue.
    freeze_completed_campaigns(path)
    claimed = dict(row)
    claimed["attempts"] += 1
    claimed["lease_owner"] = owner
    claimed["recovered"] = row["state"] == JobState.RUNNING
    return claimed


def enqueue_website_job(path: Path | str, campaign_id: str) -> str:
    initialize(path)
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        existing = connection.execute(
            "SELECT job_id FROM jobs WHERE campaign_id = ? AND source = 'website'",
            (campaign_id,),
        ).fetchone()
        if existing:
            return existing["job_id"]
        company_ids = [
            row["company_id"]
            for row in connection.execute(
                """
                SELECT DISTINCT m.company_id
                FROM maps_results AS m
                JOIN jobs AS j ON j.job_id = m.job_id
                WHERE j.campaign_id = ? AND m.company_id IS NOT NULL
                ORDER BY m.company_id
                """,
                (campaign_id,),
            )
        ]
        job_id = str(uuid4())
        config = json.loads(connection.execute("SELECT config_json FROM campaigns WHERE campaign_id = ?", (campaign_id,)).fetchone()[0])
        query = json.dumps({"company_ids": company_ids, "collect_emails": config.get("collect_emails", False), "collect_socials": config.get("collect_socials", False)})
        connection.execute(
            "INSERT INTO jobs (job_id, campaign_id, source, query_json, state, created_at, updated_at) VALUES (?, ?, 'website', ?, ?, ?, ?)",
            (job_id, campaign_id, query, JobState.QUEUED, now, now),
        )
    return job_id


def renew_job_lease(path: Path | str, job_id: str, owner: str, lease_seconds: int = 300) -> None:
    now_value = datetime.now(timezone.utc)
    expires = (now_value + timedelta(seconds=lease_seconds)).isoformat()
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE jobs SET lease_expires_at = ?, updated_at = ?
            WHERE job_id = ? AND state = ? AND lease_owner = ?
            """,
            (expires, now_value.isoformat(), job_id, JobState.RUNNING, owner),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"job lease lost: {job_id}")


def finish_job(
    path: Path | str,
    job_id: str,
    owner: str,
    state: JobState,
    error: str | None = None,
) -> None:
    if state not in {JobState.COMPLETED, JobState.FAILED, JobState.PARTIAL}:
        raise ValueError("finish_job requires completed, failed or partial")
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE jobs SET state = ?, error = ?, lease_owner = NULL,
                            lease_expires_at = NULL, finished_at = ?, updated_at = ?
            WHERE job_id = ? AND state = ? AND lease_owner = ?
            """,
            (state, error, now, now, job_id, JobState.RUNNING, owner),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"job lease lost: {job_id}")
    with closing(connect(path)) as connection:
        campaign_id = connection.execute("SELECT campaign_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0]
    freeze_campaign_results(path, campaign_id)


def approve_campaign(path: Path | str, campaign_id: str) -> None:
    if get_campaign(path, campaign_id)["read_only"]:
        raise ValueError("Legacy campaigns are read-only; create a new company extraction")
    initialize(path)
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            "UPDATE campaigns SET approved_at = COALESCE(approved_at, ?) WHERE campaign_id = ?",
            (now, campaign_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"campaign not found: {campaign_id}")


def pause_job(path: Path | str, job_id: str) -> None:
    initialize(path)
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE jobs SET state = ?, paused_at = ?, next_run_at = NULL, updated_at = ?
            WHERE job_id = ? AND state IN ('queued', 'waiting')
            """,
            (JobState.WAITING, now, now, job_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"job cannot be paused unless queued or waiting: {job_id}")


def resume_job(path: Path | str, job_id: str) -> None:
    initialize(path)
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE jobs SET state = ?, paused_at = NULL, next_run_at = NULL, updated_at = ?
            WHERE job_id = ? AND state = 'waiting' AND paused_at IS NOT NULL
              AND campaign_id IN (SELECT campaign_id FROM campaigns WHERE mode = 'companies' AND json_extract(config_json, '$.schema_version') = 2)
            """,
            (JobState.QUEUED, now, job_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"job is not manually paused: {job_id}")


def defer_or_fail_job(path: Path | str, job_id: str, owner: str, error: str) -> dict[str, Any]:
    now_value = datetime.now(timezone.utc)
    now = now_value.isoformat()
    with closing(connect(path)) as connection, connection:
        row = connection.execute(
            "SELECT source, attempts FROM jobs WHERE job_id = ? AND state = ? AND lease_owner = ?",
            (job_id, JobState.RUNNING, owner),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"job lease lost: {job_id}")

        failures = _record_source_failure(connection, row["source"], error, now_value)
        if row["attempts"] >= MAX_JOB_ATTEMPTS:
            state, next_run = JobState.FAILED, None
        else:
            delay = min(60 * (2 ** (row["attempts"] - 1)), 900) + secrets.randbelow(31)
            state = JobState.WAITING
            next_run = (now_value + timedelta(seconds=delay)).isoformat()
        connection.execute(
            """
            UPDATE jobs SET state = ?, next_run_at = ?, error = ?, lease_owner = NULL,
                            lease_expires_at = NULL, finished_at = CASE WHEN ? = 'failed' THEN ? ELSE NULL END,
                            updated_at = ?
            WHERE job_id = ? AND lease_owner = ?
            """,
            (state, next_run, error, state, now, now, job_id, owner),
        )
    if state == JobState.FAILED:
        with closing(connect(path)) as connection:
            campaign_id = connection.execute("SELECT campaign_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0]
        freeze_campaign_results(path, campaign_id)
    return {"state": state, "next_run_at": next_run, "source_failures": failures}


def record_source_success(path: Path | str, source: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO source_health (source, consecutive_failures, open_until, last_error, updated_at)
            VALUES (?, 0, NULL, NULL, ?)
            ON CONFLICT(source) DO UPDATE SET
                consecutive_failures = 0, open_until = NULL, last_error = NULL, updated_at = excluded.updated_at
            """,
            (source, now),
        )


def _record_source_failure(
    connection: sqlite3.Connection, source: str, error: str, now: datetime
) -> int:
    row = connection.execute(
        "SELECT consecutive_failures FROM source_health WHERE source = ?", (source,)
    ).fetchone()
    failures = (row["consecutive_failures"] if row else 0) + 1
    open_until = (
        (now + timedelta(seconds=CIRCUIT_BREAKER_SECONDS)).isoformat()
        if failures >= MAX_JOB_ATTEMPTS
        else None
    )
    connection.execute(
        """
        INSERT INTO source_health (source, consecutive_failures, open_until, last_error, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            consecutive_failures = excluded.consecutive_failures,
            open_until = excluded.open_until,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (source, failures, open_until, error, now.isoformat()),
    )
    return failures


def list_source_health(path: Path | str) -> list[dict[str, Any]]:
    initialize(path)
    with closing(connect(path)) as connection:
        rows = connection.execute(
            "SELECT source, consecutive_failures, open_until, last_error, updated_at FROM source_health"
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_maps_results(path: Path | str, job_id: str, rows: list[dict[str, str]]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        for row in rows:
            connection.execute(
                """
                INSERT INTO maps_results (
                    job_id, fingerprint, title, phone, emails, website, category, address,
                    review_rating, review_count, source_url, raw_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, fingerprint) DO UPDATE SET
                    title = excluded.title,
                    phone = excluded.phone,
                    emails = excluded.emails,
                    website = excluded.website,
                    category = excluded.category,
                    address = excluded.address,
                    review_rating = excluded.review_rating,
                    review_count = excluded.review_count,
                    source_url = excluded.source_url,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    row["fingerprint"],
                    row["title"],
                    row.get("phone"),
                    row.get("emails"),
                    row.get("website"),
                    row.get("category"),
                    row.get("address"),
                    row.get("review_rating"),
                    row.get("review_count"),
                    row.get("source_url"),
                    row["raw_json"],
                    now,
                    now,
                ),
            )
        return connection.execute(
            "SELECT COUNT(*) FROM maps_results WHERE job_id = ?", (job_id,)
        ).fetchone()[0]


def list_maps_results(path: Path | str, job_id: str) -> list[dict[str, Any]]:
    initialize(path)
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT title, phone, emails, website, category, address,
                   review_rating, review_count, source_url
            FROM maps_results WHERE job_id = ? ORDER BY title
            """,
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_companies(path: Path | str) -> list[dict[str, Any]]:
    initialize(path)
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT company_id, name, phone, website, category, address,
                   review_rating, review_count
            FROM companies ORDER BY name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_campaign_company_ids(path: Path | str, campaign_id: str) -> list[str]:
    initialize(path)
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT m.company_id
            FROM maps_results AS m
            JOIN jobs AS j ON j.job_id = m.job_id
            WHERE j.campaign_id = ? AND m.company_id IS NOT NULL
            ORDER BY m.company_id
            """,
            (campaign_id,),
        ).fetchall()
    return [row["company_id"] for row in rows]


def list_evidence(path: Path | str, company_id: str) -> list[dict[str, Any]]:
    initialize(path)
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT field, source_url, source_provider, observed_value, observed_at
            FROM evidence
            WHERE entity_type = 'company' AND entity_id = ?
            ORDER BY field, source_url
            """,
            (company_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_campaigns(path: Path | str) -> list[dict[str, Any]]:
    initialize(path)
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT campaign_id, name, mode, target_count, config_json,
                   approved_at, cancelled_at, created_at, results_frozen_at
            FROM campaigns ORDER BY created_at DESC
            """
        ).fetchall()
        return [_campaign_payload(connection, row) for row in rows]


def get_campaign(path: Path | str, campaign_id: str) -> dict[str, Any]:
    initialize(path)
    with closing(connect(path)) as connection:
        row = connection.execute(
            """
            SELECT campaign_id, name, mode, target_count, config_json,
                   approved_at, cancelled_at, created_at, results_frozen_at
            FROM campaigns WHERE campaign_id = ?
            """,
            (campaign_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        return _campaign_payload(connection, row)


def _campaign_payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    jobs = connection.execute(
        "SELECT job_id, source, state, paused_at, next_run_at, attempts, started_at, finished_at, error, query_json, progress_json FROM jobs WHERE campaign_id = ? ORDER BY created_at",
        (row["campaign_id"],),
    ).fetchall()
    states = [job["state"] for job in jobs]
    mode = row["mode"]
    sources = {job["source"] for job in jobs}
    config = json.loads(row["config_json"])
    read_only = mode != "companies" or config.get("schema_version") != 2
    expected = sources if read_only else {"maps"} | ({"website"} if config.get("collect_emails") or config.get("collect_socials") else set())
    if row["cancelled_at"]:
        status = "cancelled"
    elif row["approved_at"] is None:
        status = "pending_approval"
    elif any(state == JobState.RUNNING for state in states):
        status = "running"
    elif any(state in {JobState.QUEUED, JobState.WAITING} for state in states) or sources != expected:
        status = "queued"
    elif any(state in {JobState.FAILED, JobState.PARTIAL, JobState.BLOCKED} for state in states):
        status = "partial"
    else:
        status = "completed"
    if mode == "people":
        result_count = connection.execute(
            "SELECT COUNT(*) FROM campaign_people WHERE campaign_id = ?", (row["campaign_id"],)
        ).fetchone()[0]
    else:
        result_count = connection.execute(
            "SELECT COUNT(*) FROM campaign_companies WHERE campaign_id = ? AND exclusion_reason IS NULL AND selected = 1 AND review_status != 'rejected'",
            (row["campaign_id"],),
        ).fetchone()[0]
    payload = dict(row)
    payload["config"] = json.loads(payload.pop("config_json"))
    payload["read_only"] = read_only
    payload["status"] = status
    payload["result_count"] = result_count
    payload["target_reached"] = result_count >= row["target_count"]
    payload["shortfall"] = max(0, row["target_count"] - result_count)
    payload["jobs"] = []
    for job in jobs:
        item = dict(job)
        item["query"] = json.loads(item.pop("query_json"))
        item["progress"] = json.loads(item.pop("progress_json") or "{}")
        payload["jobs"].append(item)
    payload["progress"] = {"finished": sum(state in {"completed", "partial", "failed", "blocked", "cancelled"} for state in states), "total": len(states)}
    return payload


def _current_campaign_results(path: Path | str, campaign_id: str) -> list[dict[str, Any]]:
    from .contacts import normalize_phone, normalize_url, social_contact, clean_emails, SOCIAL_HOSTS, WEBSITE_SCAN_VERSION
    campaign = get_campaign(path, campaign_id)
    if campaign["mode"] == "people":
        return []
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """SELECT c.*, cc.review_status, cc.exclusion_reason, cc.selected, cc.selection_reason
               FROM campaign_companies cc JOIN companies c USING(company_id)
               WHERE cc.campaign_id=? ORDER BY c.name COLLATE NOCASE, c.company_id""", (campaign_id,)
        ).fetchall()
        scans = {row["role_key"]: dict(row) for row in connection.execute("SELECT * FROM website_scans WHERE role_key LIKE '%institutional-v%'")}
        checks = {row["email"]: dict(row) for row in connection.execute("SELECT * FROM email_checks WHERE campaign_id=?", (campaign_id,))}
        details = {}
        for detail in connection.execute("SELECT m.company_id,m.raw_json FROM maps_results m JOIN jobs j USING(job_id) WHERE j.campaign_id=? ORDER BY m.updated_at", (campaign_id,)):
            raw = json.loads(detail["raw_json"])
            details[detail["company_id"]] = {key: raw[key] for key in ("open_hours", "popular_times", "images", "categories", "description", "reviews_per_rating", "price_range", "complete_address", "status") if raw.get(key)}
        evidence = connection.execute(
            """SELECT e.* FROM evidence e JOIN campaign_companies cc ON cc.company_id=e.entity_id
               WHERE cc.campaign_id=? AND e.entity_type='company' ORDER BY e.field,e.source_url,e.observed_value""", (campaign_id,)
        ).fetchall()
    grouped = {}
    for item in evidence:
        grouped.setdefault(item["entity_id"], []).append(dict(item))
    results = []
    for row in rows:
        item = dict(row)
        for key in ("identity_key", "created_at"):
            item.pop(key, None)
        item["collected_at"] = item.pop("updated_at")
        entries = []
        allowed_fields = {"name", "phone", "website", "category", "address", "review_rating", "review_count", "maps_url", "emails", *SOCIAL_HOSTS.values()}
        for observed in grouped.get(item["company_id"], []):
            if observed["field"] not in allowed_fields or observed["source_provider"] not in {"google_maps", "website", "website_playwright"}:
                continue
            if observed["field"] in SOCIAL_HOSTS.values():
                institutional = [social_contact(url.strip())[1] for url in observed["observed_value"].split(",")]
                observed["observed_value"] = ", ".join(sorted({url for url in institutional if url}))
                if not observed["observed_value"]:
                    continue
            entries.append(observed)
        values = {key: set() for key in ("emails","phones","instagram","facebook","linkedin","youtube","tiktok","twitter","x","whatsapp")}
        phone = normalize_phone(item.get("phone"))
        if phone:
            values["phones"].add(phone)
        for entry in entries:
            field,value = entry["field"],entry["observed_value"]
            if field == "emails":
                values["emails"].update(x.strip() for x in clean_emails(value).split(",") if x.strip())
            elif field == "phone":
                if normalize_phone(value):
                    values["phones"].add(normalize_phone(value))
            elif field in values:
                for url in value.split(","):
                    actual,cleaned = social_contact(url.strip())
                    if actual in values and cleaned:
                        values[actual].add(cleaned)
        item.update({key: ", ".join(sorted(found)) for key,found in values.items()})
        item["published_emails"] = item["emails"]
        item["email_checks"] = [checks[email] for email in sorted(values["emails"]) if email in checks]
        if campaign["config"].get("verify_emails"):
            item["emails"] = ", ".join(email for email in sorted(values["emails"]) if checks.get(email, {}).get("status") != "invalid")
        item["email_host"] = ", ".join(sorted({check["mail_host"] for check in item["email_checks"] if check.get("mail_host")}))
        item["maps_details"] = details.get(item["company_id"], {})
        item["source_urls"] = sorted({e["source_url"] for e in entries if e["source_url"]})
        item["evidence"] = entries
        scan_key = json.dumps([WEBSITE_SCAN_VERSION, normalize_url(item.get("website")), campaign["config"].get("collect_emails", False), campaign["config"].get("collect_socials", False)])
        scan = scans.get(scan_key, {})
        item["website_scan_status"] = scan.get("status")
        item["website_scan_error"] = scan.get("error")
        item["website_pages"] = scan.get("pages")
        item["website_scanned_at"] = scan.get("scanned_at")
        area = campaign["config"].get("search_area")
        item["distance_m"] = distance_m(item["latitude"], item["longitude"], area) if area else None
        item["within_radius"] = item["distance_m"] <= area["radius_m"] if area and item["distance_m"] is not None else None
        results.append(item)
    return results



def freeze_campaign_results(path: Path | str, campaign_id: str) -> None:
    """Freeze a terminal extraction once; subsequent collections cannot rewrite its observations."""
    campaign = get_campaign(path, campaign_id)
    if campaign["results_frozen_at"] or campaign["status"] in {"queued", "running", "waiting", "pending_approval"}:
        return
    if any(job["state"] == "running" for job in campaign["jobs"]):
        return  # A cancelled campaign may still be importing its current external operation.
    rows = _current_campaign_results(path, campaign_id)
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        # BEGIN before reading protects against two HTTP requests freezing the same list.
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT results_frozen_at FROM campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()[0]:
            return
        connection.executemany(
            "UPDATE campaign_companies SET result_json=? WHERE campaign_id=? AND company_id=?",
            [(json.dumps(row, ensure_ascii=False, sort_keys=True), campaign_id, row["company_id"]) for row in rows],
        )
        connection.execute("UPDATE campaigns SET results_frozen_at=? WHERE campaign_id=?", (now, campaign_id))


def freeze_completed_campaigns(path: Path | str) -> None:
    initialize(path)
    with closing(connect(path)) as connection:
        ids = [row[0] for row in connection.execute("SELECT campaign_id FROM campaigns WHERE results_frozen_at IS NULL")]
    for campaign_id in ids:
        freeze_campaign_results(path, campaign_id)


def list_campaign_results(path: Path | str, campaign_id: str) -> list[dict[str, Any]]:
    freeze_campaign_results(path, campaign_id)
    campaign = get_campaign(path, campaign_id)
    if not campaign["results_frozen_at"]:
        return _current_campaign_results(path, campaign_id)
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """SELECT company_id, result_json, review_status, exclusion_reason, selected, selection_reason
               FROM campaign_companies WHERE campaign_id=?""", (campaign_id,)
        ).fetchall()
    results = []
    for row in rows:
        if row["result_json"] is None:
            continue
        item = json.loads(row["result_json"])
        for key in ("review_status", "exclusion_reason", "selected", "selection_reason"):
            item[key] = row[key]
        results.append(item)
    return sorted(results, key=lambda item: (item["name"].casefold(), item["company_id"]))


def update_result_review(
    path: Path | str, campaign_id: str, company_id: str, status: str
) -> None:
    if get_campaign(path, campaign_id)["read_only"]:
        raise ValueError("Legacy campaigns are read-only")
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError("review status must be pending, approved or rejected")
    initialize(path)
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE campaign_companies SET review_status = ?, updated_at = ?
            WHERE campaign_id = ? AND company_id = ?
            """,
            (status, datetime.now(timezone.utc).isoformat(), campaign_id, company_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("campaign result not found")
        target = connection.execute("SELECT target_count FROM campaigns WHERE campaign_id = ?", (campaign_id,)).fetchone()[0]
        select_campaign_results(connection, campaign_id, target)


def select_campaign_results(connection, campaign_id, target):
    chosen = connection.execute(
        """SELECT cc.company_id FROM campaign_companies cc JOIN companies c USING(company_id)
           WHERE cc.campaign_id = ? AND cc.exclusion_reason IS NULL AND cc.review_status != 'rejected'
           ORDER BY COALESCE(json_extract(cc.result_json, '$.name'), c.name) COLLATE NOCASE, cc.company_id LIMIT ?""",
        (campaign_id, target),
    ).fetchall()
    connection.execute(
        """UPDATE campaign_companies SET selected = 0, selection_reason = CASE
           WHEN exclusion_reason IS NOT NULL THEN exclusion_reason
           WHEN review_status = 'rejected' THEN 'review_rejected' ELSE 'beyond_target' END
           WHERE campaign_id = ?""", (campaign_id,),
    )
    connection.executemany(
        "UPDATE campaign_companies SET selected = 1, selection_reason = NULL WHERE campaign_id = ? AND company_id = ?",
        [(campaign_id, row["company_id"]) for row in chosen],
    )


def cancel_campaign(path: Path | str, campaign_id: str) -> None:
    if get_campaign(path, campaign_id)["read_only"]:
        raise ValueError("Legacy campaigns are read-only")
    initialize(path)
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            "UPDATE campaigns SET cancelled_at = COALESCE(cancelled_at, ?) WHERE campaign_id = ?",
            (now, campaign_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"campaign not found: {campaign_id}")
        connection.execute(
            """
            UPDATE jobs SET state = 'cancelled', finished_at = ?, updated_at = ?
            WHERE campaign_id = ? AND state IN ('queued', 'waiting')
            """,
            (now, now, campaign_id),
        )
    freeze_campaign_results(path, campaign_id)


def delete_campaign(path: Path | str, campaign_id: str) -> None:
    """Delete one campaign and data that is no longer shared by another campaign."""
    initialize(path)
    with closing(connect(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        campaign = connection.execute(
            "SELECT campaign_id FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if campaign is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        if connection.execute(
            "SELECT 1 FROM jobs WHERE campaign_id = ? AND state = 'running'", (campaign_id,)
        ).fetchone():
            raise RuntimeError("campaign has a running job; cancel it before deleting")

        company_ids = [
            row[0]
            for row in connection.execute(
                "SELECT company_id FROM campaign_companies WHERE campaign_id = ?", (campaign_id,)
            )
        ]
        job_ids = [
            row[0]
            for row in connection.execute(
                "SELECT job_id FROM jobs WHERE campaign_id = ?", (campaign_id,)
            )
        ]
        if job_ids:
            connection.executemany("DELETE FROM maps_results WHERE job_id = ?", ((job_id,) for job_id in job_ids))
        connection.execute("DELETE FROM email_checks WHERE campaign_id = ?", (campaign_id,))
        connection.execute("DELETE FROM campaign_people WHERE campaign_id = ?", (campaign_id,))
        connection.execute("DELETE FROM campaign_companies WHERE campaign_id = ?", (campaign_id,))
        connection.execute("DELETE FROM jobs WHERE campaign_id = ?", (campaign_id,))
        connection.execute("DELETE FROM campaigns WHERE campaign_id = ?", (campaign_id,))

        for company_id in company_ids:
            shared = connection.execute(
                "SELECT 1 FROM campaign_companies WHERE company_id = ? UNION SELECT 1 FROM people WHERE company_id = ? UNION SELECT 1 FROM maps_results WHERE company_id = ? LIMIT 1",
                (company_id, company_id, company_id),
            ).fetchone()
            if shared:
                continue
            connection.execute("DELETE FROM maps_results WHERE company_id = ?", (company_id,))
            connection.execute("DELETE FROM website_scans WHERE company_id = ?", (company_id,))
            connection.execute(
                "DELETE FROM evidence WHERE entity_type = 'company' AND entity_id = ?", (company_id,)
            )
            connection.execute("DELETE FROM companies WHERE company_id = ?", (company_id,))


def list_source_usage(path: Path | str) -> list[dict[str, Any]]:
    initialize(path)
    with closing(connect(path)) as connection:
        rows = connection.execute(
            "SELECT source, period, credits FROM source_usage ORDER BY period DESC, source"
        ).fetchall()
    return [dict(row) for row in rows]
