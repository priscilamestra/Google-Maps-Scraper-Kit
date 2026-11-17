from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import re
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from typing import Any, Callable

from .db import (
    claim_next_maps_job,
    defer_or_fail_job,
    finish_job,
    record_source_success,
    renew_job_lease,
    upsert_maps_results,
    connect,
)
from .models import JobState, CAMPAIGN_LANGUAGES
from .normalization import normalize_maps_results
from .qualification import qualify_campaign, extraction_filter, _normalize
from .geography import area_tiles, coordinates, distance_m


def run_next_maps_job(
    db_path: Path | str,
    *,
    project_root: Path | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any] | None:
    job = claim_next_maps_job(db_path)
    if job is None:
        return None

    root = project_root or Path(__file__).resolve().parents[2]
    kit = root / "integrations" / "google-maps-scraper-kit"
    script = kit / "scripts" / "scrape.py"
    output = root / "data" / "maps-jobs" / f"{job['job_id']}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        command = _command(job, script, output)
        rows = _read_results(output) if job["recovered"] and output.exists() else []
        if not rows:
            if runner is None:
                _run_with_heartbeat(command, kit, db_path, job["job_id"], job["lease_owner"])
            else:
                runner(command, cwd=kit, check=True, text=True)
            rows = _read_results(output)
        raw_count = len(rows)
        rows = _regional_candidates(db_path, job, rows)
        count = upsert_maps_results(db_path, job["job_id"], rows)
        normalized = normalize_maps_results(db_path, job["job_id"])
        qualify_campaign(db_path, job["campaign_id"])
        warning = _expand_or_stop(db_path, job, raw_count)
        state = JobState.PARTIAL if warning else JobState.COMPLETED
        finish_job(db_path, job["job_id"], job["lease_owner"], state, warning)
        record_source_success(db_path, "maps")
        return {
            "job_id": job["job_id"],
            "state": state,
            "results": count,
            **normalized,
        }
    except Exception as error:
        message = str(error).strip()[:2000] or type(error).__name__
        outcome = defer_or_fail_job(db_path, job["job_id"], job["lease_owner"], message)
        raise RuntimeError(
            f"maps job {outcome['state']}: {job['job_id']}: {message}"
        ) from error


def _run_with_heartbeat(
    command: list[str],
    cwd: Path,
    db_path: Path | str,
    job_id: str,
    owner: str,
) -> None:
    process = subprocess.Popen(command, cwd=cwd, text=True)
    progress_file = Path(command[command.index("--out")+1]).with_suffix(".progress.json")
    def update_progress():
        try:
            progress = json.loads(progress_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with closing(connect(db_path)) as connection, connection:
            connection.execute("UPDATE jobs SET progress_json=? WHERE job_id=? AND lease_owner=? AND state='running'", (json.dumps(progress), job_id, owner))
    try:
        while True:
            try:
                return_code = process.wait(timeout=5)
                update_progress()
                break
            except subprocess.TimeoutExpired:
                renew_job_lease(db_path, job_id, owner)
                update_progress()
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)


def _command(job: dict[str, Any], script: Path, output: Path) -> list[str]:
    raw_query = json.loads(job["query_json"])
    queries = raw_query.get("queries", ()) if isinstance(raw_query, dict) else raw_query
    limit = raw_query.get("limit", job["target_count"]) if isinstance(raw_query, dict) else job["target_count"]
    config = json.loads(job["config_json"])
    if not isinstance(queries, list) or not queries or any(not isinstance(query, str) for query in queries):
        raise ValueError("maps job has no valid queries")

    location = config.get("location", {})
    city = ", ".join(
        str(location[key]).strip()
        for key in ("city", "state", "country")
        if location.get(key)
    )
    if not city:
        raise ValueError("maps job has no city")

    command = [sys.executable, str(script), queries[0]]
    for query in queries[1:]:
        command.extend(("--keyword", query))
    area = raw_query.get("area", config.get("search_area")) if isinstance(raw_query, dict) else config.get("search_area")
    if area:
        command[3:3] = [str(area["latitude"]), str(area["longitude"])]
        if "depth" in config:
            command[1] = str(Path(__file__).resolve().parents[2] / "scripts" / "scrape-area.py")
            command.extend(("--kit-script", str(script), "--radius", str(area["radius_m"]), "--zoom", str(area.get("zoom", 15))))
    else:
        command.extend(("--city", city))
    language = config.get("language", "pt")
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}", language):
        raise ValueError("maps job language must be a lowercase two-letter code")
    command.extend(("--depth", str(config.get("depth", 1)), "--lang", language))
    # Website enrichment is a separate bounded institutional job owned by the product.
    command.append("--no-email")
    command.extend(("--full", "--limit", str(limit), "--out", str(output)))
    return command


def _regional_candidates(path, job, rows):
    """The global budget counts unique candidates inside the requested radius, not tile overlaps."""
    query, config = json.loads(job["query_json"]), json.loads(job["config_json"])
    if not isinstance(query, dict) or not query.get("regional"):
        if isinstance(query, dict) and "depth" in config:
            return [row for row in rows if not extraction_filter(row, config)][:query["limit"]]
        return rows
    with closing(connect(path)) as connection:
        existing = {row[0] for row in connection.execute("SELECT DISTINCT m.fingerprint FROM maps_results m JOIN jobs j USING(job_id) WHERE j.campaign_id=? AND j.job_id!=?", (job["campaign_id"], job["job_id"]))}
        excluded = set()
        for previous in config.get("exclude_campaign_ids", []):
            excluded.update(row[0] for row in connection.execute("SELECT identity_key FROM companies JOIN campaign_companies USING(company_id) WHERE campaign_id=?", (previous,)))
    remaining = max(0, config["collection_limit"]-len(existing))
    accepted = []
    seen = set(existing)
    for row in rows:
        raw = json.loads(row["raw_json"])
        lat, lon = coordinates(raw)
        distance = distance_m(lat, lon, config["search_area"])
        if distance is None or distance > config["search_area"]["radius_m"]:
            continue
        text = _normalize(" ".join(str(row.get(key) or "") for key in ("title", "category", "address")))
        if (row["fingerprint"] in excluded or extraction_filter(row, config)
                or any(_normalize(term) in text for term in config.get("negative_terms", []))
                or ("phone" in config.get("required_fields", []) and not row.get("phone"))
                or ("website" in config.get("required_fields", []) and not row.get("website"))):
            continue
        if row["fingerprint"] not in seen:
            if remaining == 0:
                continue
            remaining -= 1
            seen.add(row["fingerprint"])
        accepted.append(row)
    return accepted


def _expand_or_stop(path, job, raw_count):
    config, query = json.loads(job["config_json"]), json.loads(job["query_json"])
    if not isinstance(query, dict) or not query.get("regional"):
        return
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT cancelled_at FROM campaigns WHERE campaign_id=?", (job["campaign_id"],)).fetchone()[0]:
            return
        count = connection.execute("SELECT COUNT(*) FROM campaign_companies WHERE campaign_id=?", (job["campaign_id"],)).fetchone()[0]
        if count >= config["collection_limit"]:
            connection.execute("UPDATE jobs SET state='completed', error='collection_limit reached; region not requested', finished_at=?, updated_at=? WHERE campaign_id=? AND source='maps' AND state IN ('queued','waiting')", (now, now, job["campaign_id"]))
            return
        if config.get("deep_scan") and raw_count >= 100 and query.get("tile_level", 0) >= 2:
            return "deep_scan reached its two subdivision levels; region may remain saturated"
        if config.get("deep_scan") and raw_count >= 100 and query.get("tile_level", 0) < 2:
            used = connection.execute("SELECT COUNT(*) FROM jobs WHERE campaign_id=? AND source='maps'", (job["campaign_id"],)).fetchone()[0]
            if used+4 > config["max_queries"]:
                return "max_queries reached; saturated region not subdivided"
            for area in area_tiles(query["area"], 2):
                child = {**query, "area": area, "tile_level": query.get("tile_level", 0)+1}
                connection.execute("INSERT INTO jobs(job_id,campaign_id,source,query_json,state,created_at,updated_at) VALUES(?,?,'maps',?,'queued',?,?)", (str(uuid4()), job["campaign_id"], json.dumps(child), now, now))


def _read_results(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    results: list[dict[str, str]] = []
    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        identity = next(
            (
                (row.get(key) or "").strip()
                for key in ("place_id", "data_id", "cid", "link")
                if (row.get(key) or "").strip()
            ),
            "\x1f".join((title, row.get("address", ""), row.get("phone", ""), row.get("website", ""))).casefold(),
        )
        results.append(
            {
                "fingerprint": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "title": title,
                "phone": (row.get("phone") or "").strip(),
                "emails": (row.get("emails") or "").strip(),
                "website": (row.get("website") or "").strip(),
                "category": (row.get("category") or "").strip(),
                "address": (row.get("address") or "").strip(),
                "review_rating": (row.get("review_rating") or "").strip(),
                "review_count": (row.get("review_count") or "").strip(),
                "source_url": (row.get("link") or row.get("maps_url") or "").strip(),
                "raw_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
        )
    return results
