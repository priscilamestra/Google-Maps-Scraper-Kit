from __future__ import annotations

import hashlib
import http.client
import ssl
import threading
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
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote, parse_qsl, urlencode
from urllib.request import HTTPRedirectHandler, HTTPHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
from urllib.error import HTTPError

from .db import (
    claim_next_job,
    connect,
    defer_or_fail_job,
    finish_job,
    initialize,
    record_source_success,
    renew_job_lease,
)
from .contacts import clean_emails, normalize_phone, normalize_url, social_contact, SOCIAL_HOSTS, WEBSITE_SCAN_VERSION
from .models import JobState


USER_AGENT = "LeadExtractorAI/0.2"
MAX_PAGES = 4
MAX_BYTES = 1_000_000
TIMEOUT_SECONDS = 10
PAGE_HINTS = ("contato", "contact", "sobre", "about", "equipe", "team", "staff", "lideranca", "leadership", "quem-somos")
Fetcher = Callable[[str], tuple[str, str, bytes]]
Resolver = Callable[..., list[tuple[Any, ...]]]


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links = []
        self.json_ld = []
        self.text = []
        self.method = "website"
        self.has_javascript = False
        self.obfuscated_emails = []
        self._anchor = None
        self._anchor_text = []
        self._json = None
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("data-cfemail"):
            try:
                encoded = bytes.fromhex(values["data-cfemail"])
                self.obfuscated_emails.append(bytes(value ^ encoded[0] for value in encoded[1:]).decode("utf-8"))
            except (ValueError, UnicodeError, IndexError):
                pass
        if tag == "script" and values.get("type", "").casefold() != "application/ld+json":
            self.has_javascript = True
        if tag in {"script", "style"}:
            self._hidden += 1
        if tag == "a" and values.get("href"):
            self._anchor = values["href"]
            self._anchor_text = []
        if tag == "script" and values.get("type", "").casefold() == "application/ld+json":
            self._json = []

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor_text.append(data)
        if self._json is not None:
            self._json.append(data)
        if not self._hidden:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            self.links.append((self._anchor, " ".join(self._anchor_text).strip()))
            self._anchor = None
        if tag == "script" and self._json is not None:
            self.json_ld.append("".join(self._json))
            self._json = None
        if tag in {"script", "style"}:
            self._hidden = max(0, self._hidden - 1)


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


def enrich_websites(path: Path | str, *, fetcher=None, resolver=socket.getaddrinfo,
                    refresh=False, company_ids=None, collect_emails=True, collect_socials=True, renderer=None):
    initialize(path)
    fetch = fetcher or _fetch
    stats = {"domains": 0, "pages": 0, "evidence_added": 0, "skipped": 0, "failed": 0}
    ids = None if company_ids is None else tuple(company_ids)
    with closing(connect(path)) as connection:
        companies = [] if ids == () else connection.execute(
            "SELECT company_id,name,website FROM companies WHERE website IS NOT NULL AND website != ''"
            + (f" AND company_id IN ({','.join('?' for _ in ids)})" if ids is not None else "") + " ORDER BY name", ids or ()
        ).fetchall()
    for company in companies:
        domain = _domain(urlsplit(company["website"]).hostname or "")
        key = json.dumps([WEBSITE_SCAN_VERSION, _canonical_url(company["website"]), collect_emails, collect_socials])
        try:
            domain = validate_public_url(company["website"], resolver)
            with closing(connect(path)) as connection:
                cached = connection.execute("SELECT company_id FROM website_scans WHERE domain=? AND role_key=? AND status='completed'",
                                            (domain, key)).fetchone()
            if not refresh and cached:
                _reuse_contacts(path, cached[0], company["company_id"], collect_emails, collect_socials)
                stats["skipped"] += 1
                continue
            pages, errors = _crawl(company["website"], fetch, resolver, renderer=renderer,
                                   collect_emails=collect_emails, collect_socials=collect_socials,
                                   allow_render=fetcher is None or renderer is not None)
            if not pages:
                raise ValueError("; ".join(errors) or "No accessible pages permitted by robots.txt")
            added = _store(path, dict(company), pages, collect_emails, collect_socials)
            stats["domains"] += 1
            stats["pages"] += len(pages)
            stats["evidence_added"] += added
            stats["failed"] += bool(errors)
            _record_scan(path, domain, key, company["company_id"], "partial" if errors else "completed", len(pages), "; ".join(errors)[:1800] or None)
        except (OSError, UnicodeError, ValueError, RuntimeError) as error:
            stats["failed"] += 1
            _record_scan(path, domain, key, company["company_id"], "failed", 0, str(error)[:1800])
    return stats


def run_next_website_job(path, *, enricher=enrich_websites):
    job = claim_next_job(path, "website")
    if job is None:
        return None
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(30):
            try:
                renew_job_lease(path, job["job_id"], job["lease_owner"])
            except RuntimeError:
                return
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        query = json.loads(job["query_json"])
        result = enricher(path, company_ids=query.get("company_ids", ()),
                          collect_emails=query.get("collect_emails", False), collect_socials=query.get("collect_socials", False))
        if json.loads(job["config_json"]).get("verify_emails"):
            from .email_verification import verify_campaign_emails
            result["email_checks_unknown"] = verify_campaign_emails(path, job["campaign_id"])
            result["failed"] += bool(result["email_checks_unknown"])
        from .qualification import qualify_campaign
        qualify_campaign(path, job["campaign_id"])
        # A blocked website should not re-crawl every successful page or block the campaign.
        state = JobState.PARTIAL if result["failed"] else JobState.COMPLETED
        error = f"{result['failed']} website domain(s) inaccessible or incomplete" if result["failed"] else None
        finish_job(path, job["job_id"], job["lease_owner"], state, error)
        record_source_success(path, "website")
        return {"job_id": job["job_id"], "state": state, **result}
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        defer_or_fail_job(path, job["job_id"], job["lease_owner"], str(error))
        raise RuntimeError(str(error)) from error
    finally:
        stop.set()
        thread.join(timeout=1)


def _absolute(base, href):
    try:
        return urljoin(base, href)
    except ValueError:
        return ""


def _crawl(start_url, fetcher, resolver, *, renderer=None, collect_emails=True, collect_socials=True, allow_render=False):
    domain = validate_public_url(start_url, resolver)
    start = _canonical_url(start_url)
    robots = _robots(start, fetcher)
    queue, visited, pages, errors = [start], set(), [], []
    while queue and len(visited) < MAX_PAGES:
        url = queue.pop(0)
        if url in visited or not robots.can_fetch(USER_AGENT, url):
            continue
        visited.add(url)
        try:
            final_url, content_type, body = fetcher(url)
            if validate_public_url(final_url, resolver) != domain or not robots.can_fetch(USER_AGENT, final_url):
                raise ValueError("website redirect outside domain or robots permission")
            if not content_type.casefold().startswith("text/html"):
                continue
            parser = _PageParser()
            parser.feed(body.decode(_charset(content_type), errors="replace"))
            needs_contacts = (collect_emails and not _emails(parser)) or (collect_socials and not any(_contact(_absolute(final_url, href))[0] in SOCIAL_HOSTS.values() for href,_ in parser.links))
            if allow_render and needs_contacts and parser.has_javascript and (len(visited) == 1 or len(" ".join(parser.text)) < 500):
                if renderer is None:
                    from .rendering import render
                    renderer = render
                try:
                    rendered = renderer(final_url, body.decode(_charset(content_type), errors="replace"))
                    rendered_parser = _PageParser()
                    rendered_parser.feed(rendered)
                    rendered_parser.method = "website_playwright"
                    parser = rendered_parser
                except (OSError, RuntimeError, ValueError) as error:
                    errors.append(f"{url}: rendering: {str(error)[:200]}")
            pages.append((final_url, parser))
            for href, text in parser.links:
                if len(queue) + len(visited) >= MAX_PAGES:
                    break
                candidate = _canonical_url(_absolute(final_url, href))
                parsed = urlsplit(candidate)
                if parsed.scheme in {"http", "https"} and _domain(parsed.hostname or "") == domain:
                    if any(word in _normalize(f"{parsed.path} {text}") for word in PAGE_HINTS) and candidate not in visited and candidate not in queue:
                        queue.append(candidate)
        except (OSError, UnicodeError, ValueError, RuntimeError) as error:
            errors.append(f"{url}: {str(error)[:200]}")
    return pages, errors


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
    except HTTPError as error:
        if error.code in {401, 403}:
            rules.parse(["User-agent: *", "Disallow: /"])
            return rules
        if error.code != 404:
            raise ValueError("robots.txt unavailable; website scan deferred") from error
    rules.parse([])
    return rules


def _emails(parser):
    text = " ".join(parser.text)
    text = re.sub(r"\s*(?:\[at\]|\(at\)|\[arroba\]|\(arroba\))\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(?:\[dot\]|\(dot\)|\[ponto\]|\(ponto\))\s*", ".", text, flags=re.IGNORECASE)
    values = re.findall(r"[\w.+%\-]+@[\w.\-]+\.[A-Za-z]{2,}", text)
    values.extend(unquote(urlsplit(href).path) for href,_ in parser.links if href.lower().startswith("mailto:"))
    values.extend(parser.obfuscated_emails)
    return clean_emails(" ".join(values))


def _organizations(value):
    if isinstance(value, list):
        for item in value:
            yield from _organizations(item)
    elif isinstance(value, dict):
        kind = value.get("@type", [])
        kinds = [kind] if isinstance(kind, str) else kind if isinstance(kind, list) else []
        if any(k in {"Organization", "LocalBusiness", "Corporation", "EducationalOrganization", "School"} for k in kinds):
            yield value
        for item in value.values():
            if isinstance(item, (dict, list)):
                yield from _organizations(item)


_phone = normalize_phone


def _store(path, company, pages, collect_emails=True, collect_socials=True):
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    with closing(connect(path)) as connection, connection:
        for source_url, parser in pages:
            contacts = []
            if collect_emails and _emails(parser):
                contacts.extend(("emails", email.strip()) for email in _emails(parser).split(","))
            for href,_ in parser.links:
                field, value = _contact(_absolute(source_url, href).strip())
                if field == "phone" or (collect_emails and field == "emails") or (collect_socials and field in SOCIAL_HOSTS.values()):
                    contacts.append((field, value))
            for phone_text in re.findall(r"(?:telefone|tel|fone|celular|whatsapp)\s*:?\s*(\+?[\d(][\d() .-]{7,}\d)", " ".join(parser.text), re.IGNORECASE):
                if _phone(phone_text):
                    contacts.append(("phone", _phone(phone_text)))
            for raw in parser.json_ld:
                try:
                    organizations = list(_organizations(json.loads(raw)))
                except (ValueError, TypeError):
                    continue
                for organization in organizations:
                    if collect_emails and organization.get("email"):
                        contacts.extend(("emails", email.strip()) for email in clean_emails(str(organization["email"])).split(",") if email)
                    phone = _phone(organization.get("telephone"))
                    if phone:
                        contacts.append(("phone", phone))
                    socials = organization.get("sameAs", [])
                    for url in socials if isinstance(socials, list) else [socials]:
                        field,value = _contact(str(url))
                        if collect_socials and field in SOCIAL_HOSTS.values():
                            contacts.append((field,value))
            for field,value in set(contacts):
                if value:
                    added += _add_evidence(connection, "company", company["company_id"], field, source_url, value, now, parser.method)
    return added


def _reuse_contacts(path, source_id, target_id, emails, socials):
    if source_id == target_id:
        return
    with closing(connect(path)) as connection, connection:
        rows = connection.execute("SELECT field,source_url,observed_value,source_provider,observed_at FROM evidence WHERE entity_type='company' AND entity_id=?", (source_id,)).fetchall()
        for row in rows:
            if row["field"] == "phone" or (emails and row["field"] == "emails") or (socials and row["field"] in SOCIAL_HOSTS.values()):
                _add_evidence(connection, "company", target_id, row["field"], row["source_url"], row["observed_value"], row["observed_at"], row["source_provider"])


def _contact(url: str) -> tuple[str | None, str]:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None, ""
    if parsed.scheme == "mailto":
        email = clean_emails(unquote(parsed.path))
        return ("emails", email) if email else (None, "")
    if parsed.scheme == "tel":
        return "phone", _phone(parsed.path)
    return social_contact(url)


def _add_evidence(connection: Any, entity_type: str, entity_id: str, field: str, source_url: str, value: str, now: str, provider="website") -> int:
    evidence_id = hashlib.sha256("\x1f".join((entity_id, field, source_url, value)).encode()).hexdigest()
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO evidence (
            evidence_id, entity_type, entity_id, field, source_url,
            source_provider, observed_value, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (evidence_id, entity_type, entity_id, field, source_url, provider, value, now),
    )
    return cursor.rowcount


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


def _connect_public(host, port, timeout):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("website connection resolves to a private or non-public address")
    # Connect to the checked IP, not the hostname again (DNS rebinding protection).
    return socket.create_connection((addresses[0][4][0], port), timeout)


class _PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _connect_public(self.host, self.port, self.timeout)


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        self.sock = _connect_public(self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
        except BaseException:
            self.sock.close()
            raise


class _PublicHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=ssl.create_default_context())


def _fetch(url: str) -> tuple[str, str, bytes]:
    validate_public_url(url)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"})
    with build_opener(ProxyHandler({}), _PublicHTTPHandler(), _PublicHTTPSHandler(), _SafeRedirectHandler()).open(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("website response exceeds the size limit")
        return response.geturl(), response.headers.get("Content-Type", "application/octet-stream"), body


def _charset(content_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    return match.group(1).strip('"\'') if match else "utf-8"


def _canonical_url(url: str) -> str:
    return normalize_url(url)


def _domain(host: str) -> str:
    value = host.casefold().rstrip(".")
    return value[4:] if value.startswith("www.") else value


def _normalize(value: str) -> str:
    return " ".join(
        "".join(character for character in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(character)).split()
    )
