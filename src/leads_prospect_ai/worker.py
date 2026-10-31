from __future__ import annotations

import json
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from .db import connect, enqueue_tavily_job, enqueue_website_job, initialize
from .maps import run_next_maps_job
from .models import JobState
from .qualification import qualify_campaign
from .tavily import run_next_tavily_job
from .website import run_next_website_job


TERMINAL_STATES = {
    JobState.COMPLETED,
    JobState.PARTIAL,
    JobState.FAILED,
    JobState.BLOCKED,
    JobState.CANCELLED,
}


def run_worker_once(path: Path | str) -> dict[str, Any]:
    initialize(path)
    try:
        result = run_next_maps_job(path)
        if result:
            return {"source": "maps", **result}
        _advance_dependencies(path)
        result = run_next_website_job(path)
        if result:
            return {"source": "website", **result}
        _advance_dependencies(path)
        result = run_next_tavily_job(path)
        if result:
            _qualify_finished(path)
            return {"source": "tavily", **result}
        _qualify_finished(path)
        return {"state": "idle"}
    except RuntimeError as error:
        return {"state": "error", "error": str(error)}


def run_worker_forever(
    path: Path | str,
    *,
    poll_seconds: float = 1.0,
    stop_event: threading.Event | None = None,
) -> None:
    stop = stop_event or threading.Event()
    while not stop.is_set():
        result = run_worker_once(path)
        if result.get("state") in {"idle", "error"}:
            stop.wait(poll_seconds)


def _advance_dependencies(path: Path | str) -> None:
    with closing(connect(path)) as connection:
        campaigns = connection.execute(
            """
            SELECT campaign_id, config_json
            FROM campaigns
            WHERE mode = 'companies_and_people' AND approved_at IS NOT NULL AND cancelled_at IS NULL
            ORDER BY created_at
            """
        ).fetchall()
        jobs = {
            row["campaign_id"]: connection.execute(
                "SELECT source, state FROM jobs WHERE campaign_id = ?", (row["campaign_id"],)
            ).fetchall()
            for row in campaigns
        }
    for campaign in campaigns:
        campaign_jobs = jobs[campaign["campaign_id"]]
        maps = [job for job in campaign_jobs if job["source"] == "maps"]
        website = [job for job in campaign_jobs if job["source"] == "website"]
        tavily = [job for job in campaign_jobs if job["source"] == "tavily"]
        config = json.loads(campaign["config_json"])
        roles = tuple(config.get("decision_maker_roles", ()))
        if maps and all(job["state"] in TERMINAL_STATES for job in maps) and not website:
            enqueue_website_job(path, campaign["campaign_id"], roles)
        elif website and all(job["state"] in TERMINAL_STATES for job in website) and not tavily:
            enqueue_tavily_job(path, campaign["campaign_id"], roles)


def _qualify_finished(path: Path | str) -> None:
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT c.campaign_id, c.mode
            FROM campaigns c
            WHERE c.approved_at IS NOT NULL AND c.cancelled_at IS NULL
              AND EXISTS (SELECT 1 FROM jobs j WHERE j.campaign_id = c.campaign_id)
              AND NOT EXISTS (
                  SELECT 1 FROM jobs j WHERE j.campaign_id = c.campaign_id
                  AND j.state NOT IN ('completed','partial','failed','blocked','cancelled')
              )
            """
        ).fetchall()
    for row in rows:
        with closing(connect(path)) as connection:
            sources = {
                item["source"]
                for item in connection.execute(
                    "SELECT source FROM jobs WHERE campaign_id = ?", (row["campaign_id"],)
                )
            }
        expected = {"tavily"} if row["mode"] == "people" else {"maps"} | ({"website", "tavily"} if row["mode"] == "companies_and_people" else set())
        if sources == expected:
            if row["mode"] != "people":
                qualify_campaign(path, row["campaign_id"])
