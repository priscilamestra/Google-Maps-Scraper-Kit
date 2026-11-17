from __future__ import annotations

import json
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from .db import connect, enqueue_website_job, initialize
from .maps import run_next_maps_job
from .models import JobState
from .qualification import qualify_campaign
from .website import run_next_website_job

TERMINAL_STATES = {"completed", "partial", "failed", "blocked", "cancelled"}


def run_worker_once(path: Path | str) -> dict[str, Any]:
    initialize(path)
    try:
        result = run_next_maps_job(path)
        if result:
            return {"source": "maps", **result}
        _advance_dependencies(path)
        result = run_next_website_job(path)
        if result:
            with closing(connect(path)) as connection:
                campaign_id = connection.execute("SELECT campaign_id FROM jobs WHERE job_id = ?", (result["job_id"],)).fetchone()[0]
            qualify_campaign(path, campaign_id)
            return {"source": "website", **result}
        return {"state": "idle"}
    except RuntimeError as error:
        return {"state": "error", "error": str(error)}


def run_worker_forever(path: Path | str, *, poll_seconds: float = 1.0, stop_event: threading.Event | None = None) -> None:
    stop = stop_event or threading.Event()
    while not stop.is_set():
        result = run_worker_once(path)
        if result.get("state") in {"idle", "error"}:
            stop.wait(poll_seconds)


def _advance_dependencies(path: Path | str) -> None:
    with closing(connect(path)) as connection:
        campaigns = connection.execute(
            """SELECT campaign_id, config_json FROM campaigns
               WHERE mode = 'companies' AND json_extract(config_json, '$.schema_version') = 2
                 AND approved_at IS NOT NULL AND cancelled_at IS NULL ORDER BY created_at"""
        ).fetchall()
        for campaign in campaigns:
            config = json.loads(campaign["config_json"])
            if not (config.get("collect_emails") or config.get("collect_socials")):
                continue
            jobs = connection.execute("SELECT source, state FROM jobs WHERE campaign_id = ?", (campaign["campaign_id"],)).fetchall()
            maps = [job for job in jobs if job["source"] == "maps"]
            if maps and all(job["state"] in TERMINAL_STATES for job in maps) and not any(job["source"] == "website" for job in jobs):
                enqueue_website_job(path, campaign["campaign_id"])
