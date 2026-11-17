from __future__ import annotations

import hashlib
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote

WEBSITE_SCAN_VERSION = "institutional-v3"

SOCIAL_HOSTS = {"instagram.com":"instagram", "facebook.com":"facebook", "linkedin.com":"linkedin",
                "youtube.com":"youtube", "tiktok.com":"tiktok", "twitter.com":"twitter", "x.com":"x",
                "wa.me":"whatsapp", "api.whatsapp.com":"whatsapp", "whatsapp.com":"whatsapp"}


def normalize_phone(value):
    value = unquote(str(value or "")).strip()
    digits = re.sub(r"\D", "", value)
    return ("+" if value.startswith("+") else "") + digits if 7 <= len(digits) <= 15 else ""


def normalize_url(value):
    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    if port not in {None,80,443}:
        return ""
    query = urlencode([(k,v) for k,v in parse_qsl(parsed.query) if not k.lower().startswith("utm_") and k.lower() not in {"fbclid","gclid"}])
    netloc = parsed.hostname.lower()
    if port and port != (443 if parsed.scheme == "https" else 80):
        netloc += f":{port}"
    return urlunsplit((parsed.scheme.lower(),netloc,parsed.path.rstrip("/") or "/",query,""))


def social_contact(value, _redirect_depth=0):
    if _redirect_depth > 3:
        return None, ""
    value = normalize_url(value)
    if not value:
        return None, ""
    parsed=urlsplit(value); host=parsed.hostname.removeprefix("www.")
    if (host == "google.com" and parsed.path == "/url") or (host in {"l.facebook.com", "lm.facebook.com"} and parsed.path == "/l.php"):
        parameters = dict(parse_qsl(parsed.query))
        destination = normalize_url(parameters.get("q") or parameters.get("url") or parameters.get("u") or "")
        if destination and urlsplit(destination).hostname != parsed.hostname:
            # Decode a published destination only; never follow the redirect or fetch a profile.
            return social_contact(destination, _redirect_depth+1)
    for domain,field in SOCIAL_HOSTS.items():
        if host == domain or host.endswith("."+domain):
            if field == "linkedin" and not parsed.path.casefold().startswith(("/company/","/school/")):
                return None,""
            if any(token in parsed.path.casefold() for token in ("/sharer","/share","/reel/","/p/","/watch")):
                return None,""
            return field,value
    return None,""


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PLACEHOLDER_DOMAINS = {
    "example.com",
    "example.org",
    "exemplo.com",
    "suaescola.com.br",
    "seudominio.com",
}
PLACEHOLDER_LOCALS = {"email", "exemplo", "seuemail", "seu-email", "teste", "test"}


def clean_emails(value: str) -> str:
    found: list[str] = []
    for raw in re.split(r"[,;\s]+", value.strip()):
        email = raw.removeprefix("mailto:").split("?", 1)[0].strip().casefold()
        if not EMAIL_PATTERN.fullmatch(email):
            continue
        local, domain = email.rsplit("@", 1)
        if local in PLACEHOLDER_LOCALS or domain in PLACEHOLDER_DOMAINS:
            continue
        if email not in found:
            found.append(email)
    return ", ".join(found)


def sanitize_email_evidence(connection: sqlite3.Connection) -> int:
    changed = 0
    rows = connection.execute(
        """
        SELECT evidence_id, entity_type, entity_id, field, source_url,
               source_provider, observed_value, observed_at
        FROM evidence WHERE field = 'emails'
        """
    ).fetchall()
    for row in rows:
        cleaned = clean_emails(row["observed_value"])
        if cleaned == row["observed_value"]:
            continue
        connection.execute("DELETE FROM evidence WHERE evidence_id = ?", (row["evidence_id"],))
        if cleaned:
            evidence_id = hashlib.sha256(
                "\x1f".join((row["entity_id"], row["field"], row["source_url"], cleaned)).encode()
            ).hexdigest()
            connection.execute(
                """
                INSERT OR IGNORE INTO evidence (
                    evidence_id, entity_type, entity_id, field, source_url,
                    source_provider, observed_value, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id, row["entity_type"], row["entity_id"], row["field"],
                    row["source_url"], row["source_provider"], cleaned, row["observed_at"],
                ),
            )
        changed += 1
    return changed
