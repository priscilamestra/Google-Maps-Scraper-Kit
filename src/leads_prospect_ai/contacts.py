from __future__ import annotations

import hashlib
import re
import sqlite3


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
