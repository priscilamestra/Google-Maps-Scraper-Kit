from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .db import (
    approve_campaign,
    create_campaign,
    cancel_campaign,
    get_campaign,
    initialize,
    list_campaign_results,
    list_campaigns,
    list_jobs,
    list_source_health,
    list_source_usage,
    pause_job,
    resume_job,
)
from .api import serve_api
from .export import export_companies
from .maps import run_next_maps_job
from .models import Campaign
from .service import execute_company_campaign
from .website import enrich_websites, run_next_website_job
from .worker import run_worker_forever, run_worker_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="google-maps-scraper-kit")
    parser.add_argument("--db", type=Path, default=Path(os.getenv("GOOGLE_MAPS_SCRAPER_KIT_DB_PATH", "data/google-maps-scraper-kit.db")))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="initialize the SQLite database")
    create = commands.add_parser("create-campaign", help="validate and persist a campaign")
    create.add_argument("config", type=Path)
    execute = commands.add_parser("execute-campaign", help="approve and execute one company campaign now")
    execute.add_argument("config", type=Path)
    execute.add_argument("--output", type=Path, default=Path("exports/companies.csv"))
    approve = commands.add_parser("approve-campaign", help="approve a campaign for execution")
    approve.add_argument("campaign_id")
    commands.add_parser("jobs", help="list queued and processed jobs")
    commands.add_parser("campaigns", help="list campaigns and their current status")
    campaign = commands.add_parser("campaign", help="show one campaign")
    campaign.add_argument("campaign_id")
    results = commands.add_parser("results", help="list one campaign's results")
    results.add_argument("campaign_id")
    cancel = commands.add_parser("cancel-campaign", help="cancel pending work for a campaign")
    cancel.add_argument("campaign_id")
    pause = commands.add_parser("pause-job", help="pause a queued or waiting job")
    pause.add_argument("job_id")
    resume = commands.add_parser("resume-job", help="resume a manually paused job")
    resume.add_argument("job_id")
    commands.add_parser("source-health", help="show circuit breaker state")
    commands.add_parser("source-usage", help="show source credit usage")
    commands.add_parser("run-maps", help="run the next queued Google Maps job")
    commands.add_parser("run-websites", help="run the next queued website job")
    export = commands.add_parser("export-companies", help="export normalized companies to CSV")
    export.add_argument("output", nargs="?", type=Path, default=Path("exports/companies.csv"))
    websites = commands.add_parser("enrich-websites", help="enrich saved companies from their public websites")
    websites.add_argument("--refresh", action="store_true", help="scan domains already completed")
    worker = commands.add_parser("worker", help="process approved campaigns in the background")
    worker.add_argument("--once", action="store_true", help="process at most one ready step")
    api = commands.add_parser("serve-api", help="serve the local product API and worker")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8090)
    api.add_argument("--no-worker", action="store_true", help="preview the UI and history without executing queued work")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            initialize(args.db)
            print(f"database initialized: {args.db}")
        elif args.command == "create-campaign":
            raw = json.loads(args.config.read_text(encoding="utf-8"))
            campaign_id, job_ids = create_campaign(args.db, Campaign.from_dict(raw))
            print(json.dumps({"campaign_id": campaign_id, "job_ids": job_ids}))
        elif args.command == "execute-campaign":
            raw = json.loads(args.config.read_text(encoding="utf-8"))
            print(json.dumps(execute_company_campaign(args.db, Campaign.from_dict(raw), args.output)))
        elif args.command == "approve-campaign":
            approve_campaign(args.db, args.campaign_id)
            print(json.dumps({"campaign_id": args.campaign_id, "approved": True}))
        elif args.command == "jobs":
            print(json.dumps(list_jobs(args.db), indent=2))
        elif args.command == "campaigns":
            print(json.dumps(list_campaigns(args.db), indent=2, ensure_ascii=False))
        elif args.command == "campaign":
            print(json.dumps(get_campaign(args.db, args.campaign_id), indent=2, ensure_ascii=False))
        elif args.command == "results":
            print(json.dumps(list_campaign_results(args.db, args.campaign_id), indent=2, ensure_ascii=False))
        elif args.command == "cancel-campaign":
            cancel_campaign(args.db, args.campaign_id)
            print(json.dumps({"campaign_id": args.campaign_id, "status": "cancelled"}))
        elif args.command == "pause-job":
            pause_job(args.db, args.job_id)
            print(json.dumps({"job_id": args.job_id, "state": "waiting", "paused": True}))
        elif args.command == "resume-job":
            resume_job(args.db, args.job_id)
            print(json.dumps({"job_id": args.job_id, "state": "queued", "paused": False}))
        elif args.command == "source-health":
            print(json.dumps(list_source_health(args.db), indent=2))
        elif args.command == "source-usage":
            print(json.dumps(list_source_usage(args.db), indent=2))
        elif args.command == "run-maps":
            print(json.dumps(run_next_maps_job(args.db) or {"state": "idle"}))
        elif args.command == "run-websites":
            print(json.dumps(run_next_website_job(args.db) or {"state": "idle"}))
        elif args.command == "export-companies":
            print(json.dumps({"output": str(args.output), "companies": export_companies(args.db, args.output)}))
        elif args.command == "enrich-websites":
            print(json.dumps(enrich_websites(args.db, refresh=args.refresh)))
        elif args.command == "worker":
            if args.once:
                print(json.dumps(run_worker_once(args.db)))
            else:
                run_worker_forever(args.db)
        elif args.command == "serve-api":
            serve_api(args.db, args.host, args.port, with_worker=not args.no_worker)
    except (OSError, RuntimeError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    return 0
