"""Explicit extraction filters only; legacy module name retained for import compatibility."""
from __future__ import annotations

import json
import unicodedata
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from .db import connect, initialize, select_campaign_results, list_campaign_results
from .contacts import sanitize_email_evidence
from .geography import distance_m


def qualify_campaign(path: Path | str, campaign_id: str) -> int:
    initialize(path)
    now = datetime.now(timezone.utc).isoformat()
    rows = list_campaign_results(path, campaign_id)
    with closing(connect(path)) as connection, connection:
        campaign = connection.execute("SELECT config_json FROM campaigns WHERE campaign_id = ?", (campaign_id,)).fetchone()
        if campaign is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        config = json.loads(campaign[0])
        if config.get("schema_version") != 2:
            return 0  # Preserve historical commercial scores without executing them.
        sanitize_email_evidence(connection)
        excluded = set()
        for previous in config.get("exclude_campaign_ids", []):
            excluded.update(row[0] for row in connection.execute("SELECT company_id FROM campaign_companies WHERE campaign_id=?", (previous,)))
        for row in rows:
            fields = {"phone": bool(row["phone"]), "website": bool(row["website"]), "emails": bool(row["emails"]), "socials": any(row.get(field) for field in ("instagram","facebook","linkedin","tiktok","youtube","twitter","x","whatsapp"))}
            text = _normalize(" ".join(str(row[key] or "") for key in ("name", "category", "address")))
            negative = next((term for term in config.get("negative_terms", []) if _normalize(term) in text), None)
            missing = next((field for field in config.get("required_fields", []) if not fields.get(field)), None)
            reason = f"negative_term:{negative}" if negative else f"missing:{missing}" if missing else None
            reason = reason or extraction_filter(row, config)
            if row["company_id"] in excluded:
                reason = "previous_list"
            area = config.get("search_area")
            if area:
                distance = distance_m(row["latitude"], row["longitude"], area)
                if distance is None:
                    reason = reason or "location_unverified"
                elif distance > area["radius_m"]:
                    reason = reason or "outside_radius"
            connection.execute("UPDATE campaign_companies SET fit_score=0, confidence=0, exclusion_reason=?, updated_at=? WHERE campaign_id=? AND company_id=?",
                               (reason, now, campaign_id, row["company_id"]))
        select_campaign_results(connection, campaign_id, config["target_count"])
    return len(rows)


def _normalize(text):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)).split())


def extraction_filter(row, config):
    if config.get("match_business_terms") and not matches_topic(row, config):
        return "search_mismatch"
    if config.get("no_website") and row.get("website"):
        return "has_website"
    for field, setting in (("review_rating", "min_rating"), ("review_count", "min_reviews")):
        threshold = config.get(setting)
        if threshold is not None:
            value = str(row.get(field) or "").strip()
            try:
                number = float(value.replace(",", ".")) if field == "review_rating" else int(re.sub(r"[.,\s]", "", value))
            except ValueError:
                return f"unverified:{field}"
            if number < threshold:
                return setting
    return None


def matches_topic(row, config):
    """Literal name/category evidence, never an invented semantic or commercial score."""
    ignored = {"de", "da", "do", "das", "dos", "e", "a", "o", "em", "para", "na", "no", "com", "the", "of", "in", "and", "for"}
    generic = {"agencia", "agencias", "empresa", "empresas", "loja", "lojas", "servico", "servicos", "consultoria", "curso", "cursos", "escola", "escolas", "digital", "online", "agency", "company", "store", "services"}
    observed = set(re.findall(r"[a-z0-9]+", _normalize(f'{row.get("name") or row.get("title") or ""} {row.get("category") or ""}')))
    for term in config.get("category_terms") or config.get("business_terms", []):
        words = set(re.findall(r"[a-z0-9]+", _normalize(term)))-ignored
        topics = words-generic or words
        if any(word in observed or (len(word) >= 4 and any(token.startswith(word) for token in observed)) for word in topics):
            return True
    return False
