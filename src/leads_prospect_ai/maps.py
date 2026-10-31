from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .db import (
    claim_next_maps_job,
    defer_or_fail_job,
    finish_job,
    record_source_success,
    renew_job_lease,
    upsert_maps_results,
)
from .models import JobState
from .normalization import normalize_maps_results


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
        count = upsert_maps_results(db_path, job["job_id"], rows)
        normalized = normalize_maps_results(db_path, job["job_id"])
        finish_job(db_path, job["job_id"], job["lease_owner"], JobState.COMPLETED)
        record_source_success(db_path, "maps")
        return {
            "job_id": job["job_id"],
            "state": JobState.COMPLETED,
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
    while True:
        try:
            return_code = process.wait(timeout=30)
            break
        except subprocess.TimeoutExpired:
            renew_job_lease(db_path, job_id, owner)
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
    command.extend(("--city", city, "--depth", "1"))
    if not config.get("collect_emails", False):
        command.append("--no-email")
    if config.get("collect_socials", False):
        command.append("--socials")
    command.extend(("--full", "--limit", str(limit), "--out", str(output)))
    return command


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
