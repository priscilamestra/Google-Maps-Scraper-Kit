from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .db import connect, initialize
from .contacts import clean_emails


FIELDS = {
    "title": "name",
    "phone": "phone",
    "website": "website",
    "category": "category",
    "address": "address",
    "review_rating": "review_rating",
    "review_count": "review_count",
    "source_url": "maps_url",
}

SOCIAL_FIELDS = ("instagram", "facebook", "linkedin", "tiktok", "youtube", "twitter", "x")


def normalize_maps_results(path: Path | str, job_id: str) -> dict[str, int]:
    initialize(path)
    now = datetime.now(timezone.utc).isoformat()
    evidence_added = 0
    with closing(connect(path)) as connection, connection:
        rows = connection.execute(
            "SELECT * FROM maps_results WHERE job_id = ?", (job_id,)
        ).fetchall()
        for result in rows:
            company_id = _upsert_company(connection, dict(result), now)
            connection.execute(
                "UPDATE maps_results SET company_id = ? WHERE job_id = ? AND fingerprint = ?",
                (company_id, job_id, result["fingerprint"]),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO campaign_companies (
                    campaign_id, company_id, fit_score, confidence, review_status, added_at, updated_at
                )
                SELECT campaign_id, ?, 0, 0.5, 'pending', ?, ? FROM jobs WHERE job_id = ?
                """,
                (company_id, now, now, job_id),
            )
            for source_field, evidence_field in FIELDS.items():
                value = (result[source_field] or "").strip()
                if not value:
                    continue
                source_url = (result["source_url"] or "").strip()
                evidence_id = hashlib.sha256(
                    "\x1f".join((company_id, evidence_field, source_url, value)).encode("utf-8")
                ).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence (
                        evidence_id, entity_type, entity_id, field, source_url,
                        source_provider, observed_value, observed_at
                    ) VALUES (?, 'company', ?, ?, ?, 'google_maps', ?, ?)
                    """,
                    (evidence_id, company_id, evidence_field, source_url, value, now),
                )
                evidence_added += cursor.rowcount
            raw = json.loads(result["raw_json"])
            contacts = {"emails": result["emails"], **{field: raw.get(field) for field in SOCIAL_FIELDS}}
            for field, raw_value in contacts.items():
                value = clean_emails(raw_value or "") if field == "emails" else (raw_value or "").strip()
                if not value:
                    continue
                source_url = (result["website"] or result["source_url"] or "").strip()
                evidence_id = hashlib.sha256(
                    "\x1f".join((company_id, field, source_url, value)).encode("utf-8")
                ).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence (
                        evidence_id, entity_type, entity_id, field, source_url,
                        source_provider, observed_value, observed_at
                    ) VALUES (?, 'company', ?, ?, ?, 'website', ?, ?)
                    """,
                    (evidence_id, company_id, field, source_url, value, now),
                )
                evidence_added += cursor.rowcount
        companies = connection.execute(
            "SELECT COUNT(DISTINCT company_id) FROM maps_results WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
    return {"companies": companies, "evidence_added": evidence_added}


def _upsert_company(connection: Any, result: dict[str, Any], now: str) -> str:
    identity = result["fingerprint"]
    existing = connection.execute(
        "SELECT company_id FROM companies WHERE identity_key = ?", (identity,)
    ).fetchone()
    company_id = existing["company_id"] if existing else str(uuid4())
    values = (
        company_id,
        identity,
        result["title"],
        result["phone"],
        result["website"],
        result["category"],
        result["address"],
        result["review_rating"],
        result["review_count"],
        now,
        now,
    )
    connection.execute(
        """
        INSERT INTO companies (
            company_id, identity_key, name, phone, website, category, address,
            review_rating, review_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(identity_key) DO UPDATE SET
            name = excluded.name,
            phone = CASE WHEN excluded.phone != '' THEN excluded.phone ELSE companies.phone END,
            website = CASE WHEN excluded.website != '' THEN excluded.website ELSE companies.website END,
            category = CASE WHEN excluded.category != '' THEN excluded.category ELSE companies.category END,
            address = CASE WHEN excluded.address != '' THEN excluded.address ELSE companies.address END,
            review_rating = CASE WHEN excluded.review_rating != '' THEN excluded.review_rating ELSE companies.review_rating END,
            review_count = CASE WHEN excluded.review_count != '' THEN excluded.review_count ELSE companies.review_count END,
            updated_at = excluded.updated_at
        """,
        values,
    )
    return company_id
