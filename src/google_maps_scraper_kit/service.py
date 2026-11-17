from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .db import approve_campaign, create_campaign, enqueue_website_job, list_campaign_company_ids, list_jobs
from .export import export_companies
from .maps import run_next_maps_job
from .qualification import qualify_campaign
from .models import Campaign
from .website import run_next_website_job


def execute_company_campaign(
    db_path: Path | str, campaign: Campaign, output: Path | str, *,
    executor: Callable = run_next_maps_job, website_executor: Callable = run_next_website_job,
) -> dict[str, Any]:
    campaign_id, job_ids = create_campaign(db_path, campaign)
    approve_campaign(db_path, campaign_id)
    jobs = []
    while any(job["source"] == "maps" and job["state"] in {"queued", "running", "waiting"} for job in list_jobs(db_path, campaign_id)):
        result = executor(db_path)
        if result is None:
            raise RuntimeError("approved campaign has no runnable job")
        jobs.append(result)
    if campaign.collect_emails or campaign.collect_socials:
        enqueue_website_job(db_path, campaign_id)
        result = website_executor(db_path)
        if result is None:
            raise RuntimeError("approved campaign has no runnable website job")
        jobs.append(result)
    qualify_campaign(db_path, campaign_id)
    company_ids = list_campaign_company_ids(db_path, campaign_id)
    count = export_companies(db_path, output, company_ids, campaign_id=campaign_id)
    return {"campaign_id": campaign_id, "state": "partial" if any(job["state"] != "completed" for job in jobs) else "completed",
            "jobs": jobs, "companies": len(company_ids), "exported_rows": count, "output": str(output)}
