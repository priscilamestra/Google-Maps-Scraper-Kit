from __future__ import annotations

import csv
import json
from contextlib import closing
from pathlib import Path
from typing import Iterable

from .db import connect, initialize


FIELDS = (
    "campaign_id",
    "campaign",
    "company_id",
    "name",
    "category",
    "city",
    "state",
    "address",
    "phone",
    "whatsapp",
    "website",
    "review_rating",
    "review_count",
    "emails",
    "instagram",
    "facebook",
    "linkedin",
    "source_urls",
    "decision_maker_name",
    "decision_maker_title",
    "decision_maker_linkedin_url",
    "decision_maker_verification",
    "decision_maker_confidence",
    "fit_score",
    "confidence",
    "review_status",
    "exclusion_reason",
    "collected_at",
)


def export_companies(
    db_path: Path | str,
    output: Path | str,
    company_ids: Iterable[str] | None = None,
    campaign_id: str | None = None,
) -> int:
    initialize(db_path)
    selected_ids = None if company_ids is None else tuple(company_ids)
    with closing(connect(db_path)) as connection:
        if selected_ids == ():
            rows = []
        else:
            where = ""
            parameters: tuple[str, ...] = ()
            campaign_join = ""
            campaign_columns = "'' AS campaign_id, '' AS campaign, '' AS city, '' AS state, 0 AS fit_score, 0.5 AS confidence, 'pending' AS review_status, NULL AS exclusion_reason"
            if campaign_id:
                campaign_join = "JOIN campaign_companies AS cc ON cc.company_id = c.company_id AND cc.campaign_id = ? JOIN campaigns AS ca ON ca.campaign_id = cc.campaign_id"
                parameters = (campaign_id,)
                campaign_columns = "ca.campaign_id, ca.name AS campaign, json_extract(ca.config_json, '$.location.city') AS city, json_extract(ca.config_json, '$.location.state') AS state, cc.fit_score, cc.confidence, cc.review_status, cc.exclusion_reason"
            if selected_ids is not None:
                where = f"WHERE c.company_id IN ({','.join('?' for _ in selected_ids)})"
                parameters += selected_ids
            rows = connection.execute(
                f"""
            SELECT {campaign_columns}, c.company_id, c.name, c.category, c.address,
                   c.phone, '' AS whatsapp, c.website,
                   c.review_rating, c.review_count,
                   COALESCE(group_concat(DISTINCT CASE WHEN e.field = 'emails' THEN e.observed_value END), '') AS emails,
                   COALESCE(group_concat(DISTINCT CASE WHEN e.field = 'instagram' THEN e.observed_value END), '') AS instagram,
                   COALESCE(group_concat(DISTINCT CASE WHEN e.field = 'facebook' THEN e.observed_value END), '') AS facebook,
                   COALESCE(group_concat(DISTINCT CASE WHEN e.field = 'linkedin' THEN e.observed_value END), '') AS linkedin,
                   COALESCE(group_concat(DISTINCT NULLIF(e.source_url, '')), '') AS source_urls,
                   p.name AS decision_maker_name,
                   p.title AS decision_maker_title,
                   p.linkedin_url AS decision_maker_linkedin_url,
                   p.verification_status AS decision_maker_verification,
                   p.confidence AS decision_maker_confidence,
                   c.updated_at AS collected_at
            FROM companies AS c
            {campaign_join}
            LEFT JOIN evidence AS e
              ON e.entity_type = 'company' AND e.entity_id = c.company_id
            LEFT JOIN people AS p ON p.company_id = c.company_id
            {where}
            GROUP BY c.company_id, p.person_id
            ORDER BY c.name, p.name
                """,
                parameters,
            ).fetchall()

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
    return len(rows)


def export_people(path: Path | str, output: Path | str, campaign_id: str) -> int:
    initialize(path)
    with closing(connect(path)) as connection:
        campaign = connection.execute(
            "SELECT name, config_json FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if campaign is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        location = json.loads(campaign["config_json"]).get("location", {})
        people = connection.execute(
            """
            SELECT name, title, linkedin_url, verification_status, confidence,
                   review_status, created_at
            FROM campaign_people WHERE campaign_id = ?
            ORDER BY confidence DESC, name
            """,
            (campaign_id,),
        ).fetchall()
    rows = []
    for person in people:
        row = dict.fromkeys(FIELDS, "")
        row.update(
            {
                "campaign_id": campaign_id,
                "campaign": campaign["name"],
                "city": location.get("city", ""),
                "state": location.get("state", ""),
                "source_urls": person["linkedin_url"],
                "decision_maker_name": person["name"],
                "decision_maker_title": person["title"],
                "decision_maker_linkedin_url": person["linkedin_url"],
                "decision_maker_verification": person["verification_status"],
                "decision_maker_confidence": person["confidence"],
                "confidence": person["confidence"],
                "review_status": person["review_status"],
                "collected_at": person["created_at"],
            }
        )
        rows.append(row)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
