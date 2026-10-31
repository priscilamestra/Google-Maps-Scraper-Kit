from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
from uuid import uuid4

from .db import (
    claim_next_job,
    connect,
    defer_or_fail_job,
    finish_job,
    initialize,
    record_source_success,
)
from .contacts import clean_emails
from .models import JobState


USER_AGENT = "LeadsProspectAI/0.1"
MAX_PAGES = 4
MAX_BYTES = 1_000_000
TIMEOUT_SECONDS = 10
PAGE_HINTS = ("contato", "contact", "sobre", "about", "equipe", "team", "staff", "lideranca", "leadership", "quem-somos")
SOCIAL_HOSTS = {
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "tiktok.com": "tiktok",
    "twitter.com": "twitter",
    "x.com": "x",
    "youtube.com": "youtube",
}
Fetcher = Callable[[str], tuple[str, str, bytes]]
Resolver = Callable[..., list[tuple[Any, ...]]]


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.json_ld: list[str] = []
        self._anchor: str | None = None
        self._anchor_text: list[str] = []
        self._json: list[str] | None = None
        self._depth = 0
        self._person: tuple[int, dict[str, Any]] | None = None
        self._property: tuple[str, str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += 1
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self._anchor = values["href"]
            self._anchor_text = []
        if tag == "script" and values.get("type", "").casefold() == "application/ld+json":
            self._json = []
        if "itemscope" in values and str(values.get("itemtype", "")).rstrip("/").endswith("/Person"):
            self._person = (self._depth, {})
        if self._person:
            prop = values.get("itemprop")
            if prop in {"name", "jobTitle"}:
                self._property = (tag, prop, [])
            elif prop == "sameAs" and values.get("href"):
                self._person[1].setdefault("sameAs", []).append(values["href"])

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor_text.append(data)
        if self._json is not None:
            self._json.append(data)
        if self._property is not None:
            self._property[2].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None:
            self.links.append((self._anchor, " ".join(self._anchor_text).strip()))
            self._anchor = None
        if tag == "script" and self._json is not None:
            self.json_ld.append("".join(self._json))
            self._json = None
        if self._property is not None and tag == self._property[0]:
            self._person[1][self._property[1]] = " ".join(self._property[2]).strip()
            self._property = None
        if self._person is not None and self._depth == self._person[0]:
            self.json_ld.append(json.dumps({"@type": "Person", **self._person[1]}))
            self._person = None
        self._depth -= 1


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_public_url(url: str, resolver: Resolver = socket.getaddrinfo) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("website URL must be public HTTP(S) without credentials")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("website URL uses a disallowed port")
    try:
        addresses = {item[4][0] for item in resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError as error:
        raise ValueError(f"website hostname could not be resolved: {parsed.hostname}") from error
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("website URL resolves to a private or non-public address")
    return _domain(parsed.hostname)


def enrich_websites(
    path: Path | str,
    roles: Iterable[str] = (),
    *,
    fetcher: Fetcher | None = None,
    resolver: Resolver = socket.getaddrinfo,
    refresh: bool = False,
    company_ids: Iterable[str] | None = None,
) -> dict[str, int]:
    initialize(path)
    fetch = fetcher or _fetch
    normalized_roles = tuple(sorted({_normalize(role) for role in roles if role.strip()}))
    role_key = "\x1f".join(normalized_roles)
    stats = {"domains": 0, "pages": 0, "people": 0, "evidence_added": 0, "skipped": 0, "failed": 0}

    selected_ids = None if company_ids is None else tuple(company_ids)
    with closing(connect(path)) as connection:
        if selected_ids == ():
            companies = []
        else:
            query = "SELECT company_id, name, website FROM companies WHERE website IS NOT NULL AND website != ''"
            parameters: tuple[str, ...] = ()
            if selected_ids is not None:
                query += f" AND company_id IN ({','.join('?' for _ in selected_ids)})"
                parameters = selected_ids
            companies = connection.execute(f"{query} ORDER BY name", parameters).fetchall()

    seen_domains: set[str] = set()
    for company in companies:
        domain = _domain(urlsplit(company["website"]).hostname or company["website"])
        try:
            domain = validate_public_url(company["website"], resolver)
            if domain in seen_domains or (not refresh and _scan_completed(path, domain, role_key)):
                stats["skipped"] += 1
                continue
            seen_domains.add(domain)
            pages = _crawl(company["website"], fetch, resolver)
            result = _store(path, dict(company), domain, role_key, pages, normalized_roles)
            stats["domains"] += 1
            stats["pages"] += len(pages)
            stats["people"] += result["people"]
            stats["evidence_added"] += result["evidence_added"]
            _record_scan(path, domain, role_key, company["company_id"], "completed", len(pages), None)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            stats["failed"] += 1
            _record_scan(path, domain, role_key, company["company_id"], "failed", 0, str(error))
    return stats


def run_next_website_job(
    path: Path | str,
    *,
    enricher: Callable[..., dict[str, int]] = enrich_websites,
) -> dict[str, Any] | None:
    job = claim_next_job(path, "website")
    if job is None:
        return None
    query = json.loads(job["query_json"])
    try:
        result = enricher(
            path,
            query.get("roles", ()),
            company_ids=query.get("company_ids", ()),
        )
        if result["failed"] and not result["domains"] and not result["skipped"]:
            raise RuntimeError("all website domains failed")
        state = JobState.PARTIAL if result["failed"] else JobState.COMPLETED
        error = f"{result['failed']} website domain(s) failed" if result["failed"] else None
        finish_job(path, job["job_id"], job["lease_owner"], state, error)
        record_source_success(path, "website")
        return {"job_id": job["job_id"], "state": state, **result}
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        defer_or_fail_job(path, job["job_id"], job["lease_owner"], str(error))
        raise RuntimeError(str(error)) from error


def _crawl(start_url: str, fetcher: Fetcher, resolver: Resolver) -> list[tuple[str, _PageParser]]:
    domain = validate_public_url(start_url, resolver)
    start = _canonical_url(start_url)
    robots = _robots(start, fetcher)
    queue = [start]
    visited: set[str] = set()
    pages: list[tuple[str, _PageParser]] = []

    while queue and len(pages) < MAX_PAGES:
        url = queue.pop(0)
        if url in visited or not robots.can_fetch(USER_AGENT, url):
            continue
        visited.add(url)
        final_url, content_type, body = fetcher(url)
        if validate_public_url(final_url, resolver) != domain:
            raise ValueError("website redirected outside its domain")
        if not content_type.casefold().startswith("text/html"):
            continue
        parser = _PageParser()
        parser.feed(body.decode(_charset(content_type), errors="replace"))
        pages.append((final_url, parser))
        for href, text in parser.links:
            candidate = _canonical_url(urljoin(final_url, href))
            parsed = urlsplit(candidate)
            if parsed.scheme in {"http", "https"} and parsed.hostname and _domain(parsed.hostname) == domain:
                hint = _normalize(f"{parsed.path} {text}")
                if any(word in hint for word in PAGE_HINTS) and candidate not in visited and candidate not in queue:
                    queue.append(candidate)
    return pages


def _robots(start_url: str, fetcher: Fetcher) -> RobotFileParser:
    parsed = urlsplit(start_url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    rules = RobotFileParser()
    rules.set_url(robots_url)
    try:
        _final, content_type, body = fetcher(robots_url)
        if content_type.casefold().startswith("text/plain"):
            rules.parse(body.decode(_charset(content_type), errors="replace").splitlines())
            return rules
    except (OSError, UnicodeError, ValueError):
        pass
    rules.parse([])
    return rules


def _store(
    path: Path | str,
    company: dict[str, Any],
    domain: str,
    role_key: str,
    pages: list[tuple[str, _PageParser]],
    roles: tuple[str, ...],
) -> dict[str, int]:
    now = datetime.now(timezone.utc).isoformat()
    people_added = 0
    evidence_added = 0
    with closing(connect(path)) as connection, connection:
        for source_url, parser in pages:
            for href, _text in parser.links:
                absolute = urljoin(source_url, href).strip()
                field, value = _contact(absolute)
                if field:
                    evidence_added += _add_evidence(connection, "company", company["company_id"], field, source_url, value, now)
            for raw in parser.json_ld:
                if not raw.strip():
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for person in _persons(data):
                    name = str(person.get("name") or "").strip()
                    title = str(person.get("jobTitle") or "").strip()
                    if not name or (roles and not _matches_role(title, roles)):
                        continue
                    linkedin = _linkedin(person.get("sameAs"))
                    identity = _normalize(linkedin) if linkedin else _normalize(f"{name}\x1f{company['company_id']}\x1f{title}")
                    existing = connection.execute("SELECT person_id FROM people WHERE identity_key = ?", (identity,)).fetchone()
                    person_id = existing["person_id"] if existing else str(uuid4())
                    connection.execute(
                        """
                        INSERT INTO people (
                            person_id, identity_key, company_id, name, title, linkedin_url,
                            verification_status, confidence, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, 'verified', 1.0, ?, ?)
                        ON CONFLICT(identity_key) DO UPDATE SET
                            name = excluded.name, title = excluded.title,
                            linkedin_url = excluded.linkedin_url,
                            verification_status = excluded.verification_status,
                            confidence = excluded.confidence, updated_at = excluded.updated_at
                        """,
                        (person_id, identity, company["company_id"], name, title or None, linkedin or None, now, now),
                    )
                    people_added += int(existing is None)
                    for field, value in (("name", name), ("title", title), ("linkedin_url", linkedin)):
                        if value:
                            evidence_added += _add_evidence(connection, "person", person_id, field, source_url, value, now)
    return {"people": people_added, "evidence_added": evidence_added}


def _persons(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _persons(item)
    elif isinstance(value, dict):
        kind = value.get("@type")
        if kind == "Person" or isinstance(kind, list) and "Person" in kind:
            yield value
        for item in value.values():
            yield from _persons(item)


def _matches_role(title: str, roles: tuple[str, ...]) -> bool:
    normalized = _normalize(title)
    return any(role in normalized for role in roles)


def _linkedin(value: Any) -> str:
    values = value if isinstance(value, list) else [value]
    return next((str(item).strip() for item in values if item and "linkedin.com/in/" in str(item).casefold()), "")


def _contact(url: str) -> tuple[str | None, str]:
    parsed = urlsplit(url)
    if parsed.scheme == "mailto":
        email = clean_emails(parsed.path)
        return ("emails", email) if email else (None, "")
    if parsed.scheme == "tel":
        return "phone", parsed.path
    host = _domain(parsed.hostname or "")
    for social_host, field in SOCIAL_HOSTS.items():
        if host == social_host or host.endswith(f".{social_host}"):
            return field, _canonical_url(url)
    return None, ""


def _add_evidence(connection: Any, entity_type: str, entity_id: str, field: str, source_url: str, value: str, now: str) -> int:
    evidence_id = hashlib.sha256("\x1f".join((entity_id, field, source_url, value)).encode()).hexdigest()
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO evidence (
            evidence_id, entity_type, entity_id, field, source_url,
            source_provider, observed_value, observed_at
        ) VALUES (?, ?, ?, ?, ?, 'website', ?, ?)
        """,
        (evidence_id, entity_type, entity_id, field, source_url, value, now),
    )
    return cursor.rowcount


def _scan_completed(path: Path | str, domain: str, role_key: str) -> bool:
    with closing(connect(path)) as connection:
        return connection.execute(
            "SELECT 1 FROM website_scans WHERE domain = ? AND role_key = ? AND status = 'completed'",
            (domain, role_key),
        ).fetchone() is not None


def _record_scan(path: Path | str, domain: str, role_key: str, company_id: str, status: str, pages: int, error: str | None) -> None:
    with closing(connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO website_scans (domain, role_key, company_id, status, pages, error, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain, role_key) DO UPDATE SET
                company_id = excluded.company_id, status = excluded.status,
                pages = excluded.pages, error = excluded.error, scanned_at = excluded.scanned_at
            """,
            (domain, role_key, company_id, status, pages, error, datetime.now(timezone.utc).isoformat()),
        )


def _fetch(url: str) -> tuple[str, str, bytes]:
    validate_public_url(url)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"})
    with build_opener(_SafeRedirectHandler()).open(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("website response exceeds the size limit")
        return response.geturl(), response.headers.get_content_type(), body


def _charset(content_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    return match.group(1).strip('"\'') if match else "utf-8"


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return url.strip()
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, "", ""))


def _domain(host: str) -> str:
    value = host.casefold().rstrip(".")
    return value[4:] if value.startswith("www.") else value


def _normalize(value: str) -> str:
    return " ".join(
        "".join(character for character in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(character)).split()
    )
