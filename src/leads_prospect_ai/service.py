from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .db import approve_campaign, create_campaign, enqueue_tavily_job, enqueue_website_job, list_campaign_company_ids
from .export import export_companies, export_people
from .maps import run_next_maps_job
from .models import Campaign, JobState
from .qualification import qualify_campaign
from .tavily import run_next_tavily_job
from .website import run_next_website_job


def execute_company_campaign(
    db_path: Path | str,
    campaign: Campaign,
    output: Path | str,
    *,
    executor: Callable[[Path | str], dict[str, Any] | None] = run_next_maps_job,
    website_executor: Callable[[Path | str], dict[str, Any] | None] = run_next_website_job,
    tavily_executor: Callable[[Path | str], dict[str, Any] | None] = run_next_tavily_job,
) -> dict[str, Any]:
    campaign_id, job_ids = create_campaign(db_path, campaign)
    approve_campaign(db_path, campaign_id)
    jobs = []
    for _job_id in job_ids:
        result = tavily_executor(db_path) if campaign.mode == "people" else executor(db_path)
        if result is None:
            raise RuntimeError("approved campaign has no runnable job")
        jobs.append(result)

    if campaign.mode == "companies_and_people":
        enqueue_website_job(db_path, campaign_id, campaign.decision_maker_roles)
        result = website_executor(db_path)
        if result is None:
            raise RuntimeError("approved campaign has no runnable website job")
        jobs.append(result)
        enqueue_tavily_job(db_path, campaign_id, campaign.decision_maker_roles)
        result = tavily_executor(db_path)
        if result is None:
            raise RuntimeError("approved campaign has no runnable Tavily job")
        jobs.append(result)

    company_ids = list_campaign_company_ids(db_path, campaign_id)
    if campaign.mode == "people":
        exported_rows = export_people(db_path, output, campaign_id)
    else:
        qualify_campaign(db_path, campaign_id)
        exported_rows = export_companies(db_path, output, company_ids, campaign_id=campaign_id)
    state = "partial" if any(job["state"] == JobState.PARTIAL for job in jobs) else "completed"
    return {
        "campaign_id": campaign_id,
        "state": state,
        "jobs": jobs,
        "companies": len(company_ids),
        "people": exported_rows if campaign.mode == "people" else sum(job.get("people", 0) for job in jobs),
        "exported_rows": exported_rows,
        "output": str(output),
    }
