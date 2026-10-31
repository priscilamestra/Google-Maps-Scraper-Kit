from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


CAMPAIGN_MODES = {"companies", "people", "companies_and_people"}


@dataclass(frozen=True)
class Campaign:
    name: str
    mode: str
    target_count: int
    business_terms: tuple[str, ...]
    location: Mapping[str, str]
    decision_maker_roles: tuple[str, ...] = ()
    manual_approval_required: bool = True
    collect_emails: bool = False
    collect_socials: bool = False
    negative_terms: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Campaign":
        name = _required_text(raw, "name")
        mode = _required_text(raw, "mode")
        if mode not in CAMPAIGN_MODES:
            raise ValueError(f"mode must be one of: {', '.join(sorted(CAMPAIGN_MODES))}")

        target_count = raw.get("target_count")
        if isinstance(target_count, bool) or not isinstance(target_count, int) or target_count < 1:
            raise ValueError("target_count must be a positive integer")

        terms = _text_list(raw.get("business_terms"), "business_terms")
        roles = _text_list(raw.get("decision_maker_roles", []), "decision_maker_roles")
        if mode != "people" and not terms:
            raise ValueError("business_terms is required for company campaigns")
        if mode != "companies" and not roles:
            raise ValueError("decision_maker_roles is required when searching for people")

        location = raw.get("location")
        if not isinstance(location, Mapping) or not _required_text(location, "city"):
            raise ValueError("location.city is required")
        normalized_location = {
            key: value.strip()
            for key, value in location.items()
            if isinstance(key, str) and isinstance(value, str) and value.strip()
        }

        manual = raw.get("manual_approval_required", True)
        if not isinstance(manual, bool):
            raise ValueError("manual_approval_required must be boolean")
        collect_emails = raw.get("collect_emails", False)
        collect_socials = raw.get("collect_socials", False)
        if not isinstance(collect_emails, bool) or not isinstance(collect_socials, bool):
            raise ValueError("collect_emails and collect_socials must be boolean")
        negative_terms = _text_list(raw.get("negative_terms", []), "negative_terms")
        required_fields = _text_list(raw.get("required_fields", []), "required_fields")
        allowed_fields = {"phone", "website", "emails", "socials", "decision_maker"}
        unknown_fields = set(required_fields) - allowed_fields
        if unknown_fields:
            raise ValueError(f"unsupported required_fields: {', '.join(sorted(unknown_fields))}")

        return cls(
            name=name,
            mode=mode,
            target_count=target_count,
            business_terms=terms,
            location=normalized_location,
            decision_maker_roles=roles,
            manual_approval_required=manual,
            collect_emails=collect_emails,
            collect_socials=collect_socials,
            negative_terms=negative_terms,
            required_fields=required_fields,
        )

    def maps_queries(self) -> tuple[str, ...]:
        if self.mode == "people":
            return ()
        place = ", ".join(
            self.location[key]
            for key in ("city", "state", "country")
            if self.location.get(key)
        )
        return tuple(f"{term} em {place}" for term in self.business_terms)

    def maps_plan(self) -> tuple[tuple[str, int], ...]:
        queries = self.maps_queries()
        if not queries:
            return ()
        base, remainder = divmod(self.target_count, len(queries))
        return tuple(
            (query, base + int(index < remainder))
            for index, query in enumerate(queries)
            if base + int(index < remainder) > 0
        )


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _text_list(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)
