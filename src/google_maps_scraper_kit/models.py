from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping
import math
import re
from .geography import search_area, area_tiles


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


CAMPAIGN_MODES = {"companies"}
CAMPAIGN_LANGUAGES = {"pt", "en", "es"}


@dataclass(frozen=True)
class Campaign:
    name: str
    mode: str
    target_count: int
    business_terms: tuple[str, ...]
    location: Mapping[str, str]
    manual_approval_required: bool = True
    collect_emails: bool = False
    collect_socials: bool = False
    negative_terms: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    search_area: Mapping[str, float] | None = None
    neighborhoods: tuple[str, ...] = ()
    collection_limit: int | None = None
    language: str = "pt"
    depth: int = 5
    grid_size: int = 0
    deep_scan: bool = False
    max_queries: int = 100
    min_rating: float | None = None
    min_reviews: int | None = None
    no_website: bool = False
    verify_emails: bool = False
    exclude_campaign_ids: tuple[str, ...] = ()
    match_business_terms: bool = True
    category_terms: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Campaign":
        name = _required_text(raw, "name")
        mode = _required_text(raw, "mode")
        if mode not in CAMPAIGN_MODES:
            raise ValueError(f"mode must be one of: {', '.join(sorted(CAMPAIGN_MODES))}")

        target_count = raw.get("target_count")
        if isinstance(target_count, bool) or not isinstance(target_count, int) or target_count < 1:
            raise ValueError("target_count must be a positive integer")

        terms = tuple(dict.fromkeys(_text_list(raw.get("business_terms"), "business_terms")))
        if raw.get("decision_maker_roles"):
            raise ValueError("decision_maker_roles belongs to the separate intelligence project")
        if not terms:
            raise ValueError("business_terms is required for company campaigns")

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
        allowed_fields = {"phone", "website", "emails", "socials"}
        unknown_fields = set(required_fields) - allowed_fields
        if unknown_fields:
            raise ValueError(f"unsupported required_fields: {', '.join(sorted(unknown_fields))}")

        language = raw.get("language", "pt")
        if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}", language):
            raise ValueError("language must be a lowercase two-letter code")
        neighborhoods = tuple(dict.fromkeys(_text_list(raw.get("neighborhoods", []), "neighborhoods")))
        if len(neighborhoods) > 20:
            raise ValueError("use at most 20 explicit neighborhoods")
        collection_limit = raw.get("collection_limit", target_count)
        if isinstance(collection_limit, bool) or not isinstance(collection_limit, int) or not target_count <= collection_limit <= max(target_count, 5000):
            raise ValueError("collection_limit must be an integer at least target_count (maximum 5000 for additional collection)")
        if neighborhoods and collection_limit < len(terms) * (1 + len(neighborhoods)):
            raise ValueError("collection_limit must cover at least one result per term/location query")

        area = search_area(raw.get("search_area"))
        depth = _integer(raw, "depth", 5, 1, 40)
        grid_size = _integer(raw, "grid_size", 0, 0, 10)
        max_queries = _integer(raw, "max_queries", 100, 1, 100)
        if grid_size > 1 and area is None:
            raise ValueError("grid_size requires a confirmed search_area")
        min_rating = raw.get("min_rating")
        if min_rating is not None and (isinstance(min_rating, bool) or not isinstance(min_rating, (int, float)) or not math.isfinite(min_rating) or not 0 <= min_rating <= 5):
            raise ValueError("min_rating must be between 0 and 5")
        min_reviews = None if raw.get("min_reviews") is None else _integer(raw, "min_reviews", 0, 0, 1000000000)
        switches = {key: raw.get(key, key == "match_business_terms") for key in ("deep_scan", "no_website", "verify_emails", "match_business_terms")}
        if any(not isinstance(value, bool) for value in switches.values()):
            raise ValueError("deep_scan, no_website, verify_emails and match_business_terms must be boolean")
        if switches["deep_scan"] and area is None:
            raise ValueError("deep_scan requires a confirmed search_area")
        if switches["verify_emails"] and not collect_emails:
            raise ValueError("verify_emails requires collect_emails")
        if switches["no_website"] and "website" in required_fields:
            raise ValueError("no_website conflicts with required website")
        excluded = tuple(dict.fromkeys(_text_list(raw.get("exclude_campaign_ids", []), "exclude_campaign_ids")))
        if len(excluded) > 100:
            raise ValueError("exclude_campaign_ids supports at most 100 lists")

        return cls(
            name=name,
            mode=mode,
            target_count=target_count,
            business_terms=terms,
            location=normalized_location,
            manual_approval_required=manual,
            collect_emails=collect_emails,
            collect_socials=collect_socials,
            negative_terms=negative_terms,
            required_fields=required_fields,
            search_area=area,
            neighborhoods=neighborhoods,
            collection_limit=collection_limit,
            language=language,
            depth=depth, grid_size=grid_size, max_queries=max_queries,
            min_rating=min_rating, min_reviews=min_reviews, exclude_campaign_ids=excluded,
            category_terms=_text_list(raw.get("category_terms", []), "category_terms"),
            **switches,
        )

    def maps_queries(self) -> tuple[str, ...]:
        place = ", ".join(
            self.location[key]
            for key in ("city", "state", "country")
            if self.location.get(key)
        )
        places = (place, *(f"{neighborhood}, {place}" for neighborhood in self.neighborhoods))
        return tuple(f"{term} em {location}" for location in places for term in self.business_terms)

    def maps_plan(self) -> tuple[tuple[str, int], ...]:
        queries = self.maps_queries()
        if not queries:
            return ()
        base, remainder = divmod(self.collection_limit or self.target_count, len(queries))
        return tuple(
            (query, base + int(index < remainder))
            for index, query in enumerate(queries)
            if base + int(index < remainder) > 0
        )

    def maps_jobs(self) -> list[dict[str, Any]]:
        if not self.search_area or self.grid_size == 1 or (self.grid_size == 0 and (self.collection_limit or self.target_count) <= 20):
            jobs = [{"queries": [query], "limit": limit} for query, limit in self.maps_plan()]
        else:
            size = self.grid_size or (3 if (self.collection_limit or self.target_count) > 100 else 2)
            areas = [*([dict(self.search_area, zoom=15)] if size < 10 else []), *area_tiles(self.search_area, size)]
            jobs = [{"queries": [query], "limit": min(120, self.collection_limit or self.target_count),
                     "area": area, "tile_level": 0, "regional": True}
                    for area in areas for query in self.maps_queries()]
        if len(jobs) > self.max_queries:
            raise ValueError(f"search plan needs {len(jobs)} queries; max_queries={self.max_queries}")
        return jobs


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _integer(raw, key, default, lower, upper):
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ValueError(f"{key} must be an integer between {lower} and {upper}")
    return value


def _text_list(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)
