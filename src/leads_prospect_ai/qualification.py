from __future__ import annotations

import json
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .db import connect, initialize
from .contacts import sanitize_email_evidence


def qualify_campaign(path: Path | str, campaign_id: str) -> int:
    initialize(path)
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        sanitize_email_evidence(connection)
        campaign = connection.execute(
            "SELECT config_json FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if campaign is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        config = json.loads(campaign["config_json"])
        negative_terms = tuple(_normalize(term) for term in config.get("negative_terms", ()))
        required_fields = tuple(config.get("required_fields", ()))
        rows = connection.execute(
            """
            SELECT c.company_id, c.name, c.category, c.address, c.phone, c.website,
                   c.review_rating,
                   EXISTS(SELECT 1 FROM evidence e WHERE e.entity_type = 'company' AND e.entity_id = c.company_id AND e.field = 'emails') AS has_email,
                   EXISTS(SELECT 1 FROM evidence e WHERE e.entity_type = 'company' AND e.entity_id = c.company_id AND e.field IN ('instagram','facebook','linkedin','tiktok','youtube','twitter','x')) AS has_social,
                   EXISTS(SELECT 1 FROM people p WHERE p.company_id = c.company_id) AS has_person,
                   EXISTS(SELECT 1 FROM people p WHERE p.company_id = c.company_id AND p.verification_status IN ('likely','verified')) AS has_verified_person
            FROM campaign_companies cc JOIN companies c USING (company_id)
            WHERE cc.campaign_id = ?
            """,
            (campaign_id,),
        ).fetchall()
        for row in rows:
            fields = {
                "phone": bool(row["phone"]),
                "website": bool(row["website"]),
                "emails": bool(row["has_email"]),
                "socials": bool(row["has_social"]),
                "decision_maker": bool(row["has_person"]),
            }
            score = (
                20 * fields["phone"]
                + 15 * fields["website"]
                + 20 * fields["emails"]
                + 10 * fields["socials"]
                + 10 * bool(row["review_rating"])
                + 15 * fields["decision_maker"]
                + 10 * bool(row["has_verified_person"])
            )
            confidence = min(
                1.0,
                0.5
                + 0.1 * fields["phone"]
                + 0.1 * fields["website"]
                + 0.1 * fields["emails"]
                + 0.1 * fields["socials"]
                + 0.1 * bool(row["has_verified_person"]),
            )
            searchable = _normalize(" ".join(str(row[key] or "") for key in ("name", "category", "address")))
            excluded = next((term for term in negative_terms if term and term in searchable), None)
            missing = next((field for field in required_fields if not fields[field]), None)
            reason = f"negative_term:{excluded}" if excluded else f"missing:{missing}" if missing else None
            connection.execute(
                """
                UPDATE campaign_companies
                SET fit_score = ?, confidence = ?, exclusion_reason = ?, updated_at = ?
                WHERE campaign_id = ? AND company_id = ?
                """,
                (score, confidence, reason, now, campaign_id, row["company_id"]),
            )
    return len(rows)


def _normalize(value: str) -> str:
    return " ".join(
        "".join(character for character in unicodedata.normalize("NFKD", value) if not unicodedata.combining(character))
        .casefold()
        .split()
    )
