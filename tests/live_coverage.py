"""Explicit real smoke: uv run python tests/live_coverage.py --run. No paid providers."""
import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8090/api/v1"


def api(path, method="GET", payload=None):
    request = Request(BASE+path, method=method, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path, default=Path("data/live-validation/coverage-report.json"))
    args = parser.parse_args()
    prior = api("/campaigns")["campaigns"]
    if any(item["status"] in {"running", "queued", "waiting"} for item in prior):
        raise RuntimeError("Another campaign is active")
    before = {item["campaign_id"]: fingerprint(api(f'/campaigns/{item["campaign_id"]}/results')) for item in prior}
    config = {"name": "Validação regional — marketing Nova Friburgo", "mode": "companies", "target_count": 60,
              "collection_limit": 120, "business_terms": ["agência de marketing"],
              "location": {"city": "Nova Friburgo", "state": "RJ"}, "language": "pt",
              "search_area": {"latitude": -22.3061661, "longitude": -42.4934183, "radius_m": 3000},
              "depth": 5, "grid_size": 2, "max_queries": 5, "deep_scan": False,
              "collect_emails": True, "collect_socials": True, "verify_emails": True, "manual_approval_required": True}
    config.update(match_business_terms=True, category_terms=["marketing", "publicidade", "publicitária"])
    if args.config:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    created = api("/campaigns", "POST", config)
    campaign_id = created["campaign_id"]
    report_path = args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"campaign_id": campaign_id, "started_at": datetime.now(timezone.utc).isoformat(), "config": config}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"campaign_id": campaign_id, "planned_maps_queries": len(created["job_ids"])}), flush=True)
    started = time.monotonic()
    api(f"/campaigns/{campaign_id}/approve", "POST", {})
    last = None
    while time.monotonic()-started < 900:
        campaign = api(f"/campaigns/{campaign_id}")
        status = [(job["source"], job["state"]) for job in campaign["jobs"]]
        if status != last:
            print(json.dumps({"elapsed_s": round(time.monotonic()-started), "jobs": status, "selected": campaign["result_count"]}), flush=True)
            last = status
        if campaign["status"] in {"completed", "partial", "failed", "cancelled"}:
            break
        time.sleep(3)
    else:
        api(f"/campaigns/{campaign_id}/cancel", "POST", {})
        raise RuntimeError("Smoke timed out; pending work cancelled; preserved partial results")
    results = api(f"/campaigns/{campaign_id}/results")["results"]
    assert len({item["company_id"] for item in results}) == len(results)
    assert all(item["within_radius"] for item in results)
    if config.get("match_business_terms"):
        from google_maps_scraper_kit.qualification import matches_topic
        assert all(matches_topic(item, config) for item in results if item["selected"])
    assert all(fingerprint(api(f"/campaigns/{cid}/results")) == digest for cid, digest in before.items())
    with urlopen(BASE+f"/campaigns/{campaign_id}/export", timeout=30) as response:
        csv_data = response.read()
        assert response.headers["Content-Type"].startswith("text/csv")
    with urlopen(BASE+f"/campaigns/{campaign_id}/export?format=json", timeout=30) as response:
        assert json.load(response)["results"] == results
    with sqlite3.connect("data/google-maps-scraper-kit.db") as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok" and not connection.execute("PRAGMA foreign_key_check").fetchall()
    report.update(state=campaign["status"], duration_s=round(time.monotonic()-started, 1),
                  companies=len(results), selected=campaign["result_count"], shortfall=campaign["shortfall"],
                  with_emails=sum(bool(item["emails"]) for item in results),
                  with_socials=sum(any(item.get(field) for field in ("instagram","facebook","linkedin","youtube","tiktok","x","twitter")) for item in results),
                  email_checks=sum(len(item.get("email_checks", [])) for item in results),
                  website_statuses={item["company_id"]: item.get("website_scan_status") for item in results},
                  jobs=campaign["jobs"], old_lists_preserved=len(before), integrity=integrity,
                  csv_sha256=hashlib.sha256(csv_data).hexdigest(), paid_api_credits=0)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"config", "jobs", "website_statuses"}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
