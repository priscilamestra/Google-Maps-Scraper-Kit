from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from .db import (
    claim_next_job,
    connect,
    consume_source_credit,
    defer_or_fail_job,
    finish_job,
    record_source_success,
)
from .models import JobState


Search = Callable[[str], dict[str, Any]]


def run_next_tavily_job(
    path: Path | str,
    *,
    searcher: Search | None = None,
    api_key: str | None = None,
    monthly_limit: int | None = None,
) -> dict[str, Any] | None:
    job = claim_next_job(path, "tavily")
    if job is None:
        return None
    query = json.loads(job["query_json"])
    key = api_key if api_key is not None else _setting("TAVILY_API_KEY")
    limit = monthly_limit if monthly_limit is not None else int(_setting("TAVILY_MONTHLY_LIMIT", "1000"))
    if searcher is None and not key:
        message = "TAVILY_API_KEY is not configured"
        finish_job(path, job["job_id"], job["lease_owner"], JobState.PARTIAL, message)
        return {"job_id": job["job_id"], "state": JobState.PARTIAL, "queries": 0, "people": 0, "error": message}

    search = searcher or (lambda text: _search(text, key))
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    stats = {"queries": 0, "people": 0, "evidence_added": 0}
    try:
        roles = tuple(str(role).strip() for role in query.get("roles", ()) if str(role).strip())
        location = query.get("location", {}) if isinstance(query.get("location", {}), dict) else {}
        if query.get("standalone"):
            stats, exhausted = _run_standalone(
                path, job["campaign_id"], query, roles, location, search, period, limit
            )
            target = int(query.get("target_count") or 1)
            under_target = stats["people"] < target
            state = JobState.PARTIAL if exhausted or under_target else JobState.COMPLETED
            error = (
                "Tavily monthly credit limit reached"
                if exhausted
                else f"target not reached: {stats['people']}/{target}"
                if under_target
                else None
            )
            finish_job(path, job["job_id"], job["lease_owner"], state, error)
            record_source_success(path, "tavily")
            return {"job_id": job["job_id"], "state": state, **stats, "error": error}
        with closing(connect(path)) as connection:
            ids = tuple(query.get("company_ids", ()))
            companies = [] if not ids else connection.execute(
                f"SELECT company_id, name FROM companies WHERE company_id IN ({','.join('?' for _ in ids)}) ORDER BY name",
                ids,
            ).fetchall()
        exhausted = False
        for company in companies:
            if not consume_source_credit(path, "tavily", period, limit):
                exhausted = True
                break
            text = _query(company["name"], roles, location)
            response = search(text)
            stats["queries"] += 1
            stored = _store(path, dict(company), roles, location, response.get("results", ()))
            stats["people"] += stored["people"]
            stats["evidence_added"] += stored["evidence_added"]
        state = JobState.PARTIAL if exhausted else JobState.COMPLETED
        error = "Tavily monthly credit limit reached" if exhausted else None
        finish_job(path, job["job_id"], job["lease_owner"], state, error)
        record_source_success(path, "tavily")
        return {"job_id": job["job_id"], "state": state, **stats, "error": error}
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as error:
        outcome = defer_or_fail_job(path, job["job_id"], job["lease_owner"], str(error))
        raise RuntimeError(f"tavily job {outcome['state']}: {error}") from error


def _query(company: str, roles: tuple[str, ...], location: dict[str, Any] | None = None) -> str:
    role_query = " OR ".join(f'"{role}"' for role in roles)
    place = " ".join(str(value).strip() for value in (location or {}).values() if str(value).strip())
    return f'site:linkedin.com/in/ "{company}" ({role_query}) "{place}"'.strip()


def _run_standalone(
    path: Path | str,
    campaign_id: str,
    query: dict[str, Any],
    roles: tuple[str, ...],
    location: dict[str, Any],
    search: Search,
    period: str,
    limit: int,
) -> tuple[dict[str, int], bool]:
    target = int(query.get("target_count") or 1)
    terms = tuple(str(term).strip() for term in query.get("business_terms", ()) if str(term).strip()) or ("",)
    stats = {"queries": 0, "people": 0, "evidence_added": 0}
    exhausted = False
    for role in roles:
        for term in terms:
            if stats["people"] >= target:
                return stats, exhausted
            if not consume_source_credit(path, "tavily", period, limit):
                return stats, True
            place = " ".join(str(location.get(key) or "").strip() for key in ("city", "state", "country") if location.get(key))
            text = f'site:linkedin.com/in/ "{role}" "{term}" "{place}"'.strip()
            response = search(text)
            stats["queries"] += 1
            stored = _store_standalone(
                path, campaign_id, roles, location, response.get("results", ()), target - stats["people"]
            )
            stats["people"] += stored
    return stats, exhausted


def _store_standalone(
    path: Path | str,
    campaign_id: str,
    roles: tuple[str, ...],
    location: dict[str, Any],
    results: Any,
    remaining: int,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    people = 0
    with closing(connect(path)) as connection, connection:
        for result in results if isinstance(results, list) else ():
            if people >= remaining or not isinstance(result, dict):
                break
            url = _linkedin_url(str(result.get("url") or ""))
            title = str(result.get("title") or "").strip()
            content = str(result.get("content") or "").strip()
            searchable = _normalize(f"{title} {content}")
            role = next((role for role in roles if _normalize(role) in searchable), "")
            name = re.split(r"\s+(?:[-|–—])\s+", title, maxsplit=1)[0].strip()
            if not url or not role or not name or "linkedin" in _normalize(name):
                continue
            city = _normalize(str(location.get("city") or ""))
            place_match = bool(city and city in searchable)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO campaign_people (
                    person_id, campaign_id, identity_key, name, title, location, linkedin_url,
                    verification_status, confidence, source_excerpt, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), campaign_id, url.casefold(), name, role,
                    ", ".join(str(location.get(key) or "").strip() for key in ("city", "state") if location.get(key)),
                    url, "likely" if place_match else "candidate", 0.8 if place_match else 0.5,
                    content[:500] or None, now, now,
                ),
            )
            people += cursor.rowcount
    return people


def _search(query: str, api_key: str) -> dict[str, Any]:
    body = json.dumps(
        {
            "query": query,
            "search_depth": "basic",
            "max_results": 20,
            "include_domains": ["linkedin.com"],
            "include_domains_mode": "restrict",
            "include_answer": False,
            "include_raw_content": False,
        }
    ).encode()
    request = Request(
        "https://api.tavily.com/search",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def _store(
    path: Path | str,
    company: dict[str, str],
    roles: tuple[str, ...],
    location: dict[str, Any],
    results: Any,
) -> dict[str, int]:
    now = datetime.now(timezone.utc).isoformat()
    people = 0
    evidence_added = 0
    with closing(connect(path)) as connection, connection:
        for result in results if isinstance(results, list) else ():
            if not isinstance(result, dict):
                continue
            url = _linkedin_url(str(result.get("url") or ""))
            title = str(result.get("title") or "").strip()
            content = str(result.get("content") or "").strip()
            searchable = _normalize(f"{title} {content}")
            role = next((role for role in roles if _normalize(role) in searchable), "")
            if not url or _normalize(company["name"]) not in searchable or not role:
                continue
            name = re.split(r"\s+(?:[-|–—])\s+", title, maxsplit=1)[0].strip()
            if not name or "linkedin" in _normalize(name) or _normalize(name) == _normalize(company["name"]):
                continue
            place_terms = [_normalize(str(location.get(key) or "")) for key in ("city", "state")]
            place_match = any(term and term in searchable for term in place_terms)
            verification_status = "likely" if place_match else "candidate"
            confidence = 0.8 if place_match else 0.55
            existing = connection.execute("SELECT person_id FROM people WHERE identity_key = ?", (url.casefold(),)).fetchone()
            person_id = existing["person_id"] if existing else str(uuid4())
            connection.execute(
                """
                INSERT INTO people (
                    person_id, identity_key, company_id, name, title, location, linkedin_url,
                    verification_status, confidence, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    name = excluded.name, title = excluded.title,
                    location = excluded.location, linkedin_url = excluded.linkedin_url,
                    verification_status = excluded.verification_status,
                    confidence = excluded.confidence, updated_at = excluded.updated_at
                """,
                (
                    person_id, url.casefold(), company["company_id"], name, role,
                    ", ".join(str(location.get(key) or "").strip() for key in ("city", "state") if location.get(key)),
                    url, verification_status, confidence, now, now,
                ),
            )
            people += int(existing is None)
            for field, value in (("name", name), ("title", role), ("linkedin_url", url)):
                evidence_id = hashlib.sha256("\x1f".join((person_id, field, url, value)).encode()).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence (
                        evidence_id, entity_type, entity_id, field, source_url,
                        source_provider, observed_value, observed_at
                    ) VALUES (?, 'person', ?, ?, ?, 'tavily', ?, ?)
                    """,
                    (evidence_id, person_id, field, url, value, now),
                )
                evidence_added += cursor.rowcount
            excerpt = content[:500].strip()
            if excerpt:
                evidence_id = hashlib.sha256("\x1f".join((person_id, "search_excerpt", url, excerpt)).encode()).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence (
                        evidence_id, entity_type, entity_id, field, source_url,
                        source_provider, observed_value, observed_at
                    ) VALUES (?, 'person', ?, 'search_excerpt', ?, 'tavily', ?, ?)
                    """,
                    (evidence_id, person_id, url, excerpt, now),
                )
                evidence_added += cursor.rowcount
    return {"people": people, "evidence_added": evidence_added}


def _linkedin_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return ""
    if not parsed.path.casefold().startswith("/in/"):
        return ""
    return urlunsplit(("https", host, parsed.path.rstrip("/"), "", ""))


def _normalize(value: str) -> str:
    return " ".join(
        "".join(character for character in unicodedata.normalize("NFKD", value) if not unicodedata.combining(character))
        .casefold()
        .split()
    )


def _setting(name: str, default: str = "", env_file: Path | None = None) -> str:
    if name in os.environ:
        return os.environ[name]
    path = env_file or Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return default
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip("'\"")
    return default
