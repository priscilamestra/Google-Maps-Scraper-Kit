"""Opt-in live acceptance: uv run --with playwright python tests/live_web.py --execute.

Creates one bounded real campaign through the web UI. Resume with --campaign-id ID.
Reports and screenshots remain under ignored data/. Never run by unittest discover.
"""
import argparse
import csv
import io
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8090"


def api(path, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(BASE + "/api/v1" + path, data=data, method=method,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--execute", action="store_true")
    choice.add_argument("--campaign-id")
    args = parser.parse_args()
    runtime = api("/runtime")
    assert runtime["worker_enabled"] and runtime["maps_available"], runtime
    output = Path("data/live-validation")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE)
        page.wait_for_function("typeof L !== 'undefined' && document.querySelector('.leaflet-tile-loaded')", timeout=45000)
        campaign_id = args.campaign_id
        if args.execute:
            page.locator("#terms").fill("agência de marketing")
            page.locator("#location").fill("Nova Friburgo, RJ")
            page.locator("#location").dispatch_event("input")
            page.get_by_role("button", name="Localizar", exact=True).click()
            page.wait_for_function("center !== null", timeout=25000)
            page.locator("#count").fill("5")
            page.locator("#collection-limit").fill("10")
            page.locator("#radius").fill("10000")
            page.locator("#radius").dispatch_event("input")
            page.locator("#neighborhoods").fill("")
            page.locator("#language").select_option("pt")
            page.locator("#emails").check()
            page.locator("#socials").check()
            page.locator("#search").click()
            page.wait_for_function("active !== null && document.querySelector('#notice').textContent.startsWith('Busca iniciada')", timeout=15000)
            campaign_id = page.evaluate("active")
            (output / "latest.json").write_text(json.dumps({"campaign_id": campaign_id}), encoding="utf-8")
            print("CREATED", campaign_id, flush=True)
        campaign = api(f"/campaigns/{campaign_id}")
        assert campaign["approved_at"], campaign
        previous = None
        deadline = time.monotonic() + 900
        while True:
            campaign = api(f"/campaigns/{campaign_id}")
            state = (campaign["status"], [(job["state"], job["attempts"]) for job in campaign["jobs"]])
            if state != previous:
                print("PROGRESS", state, flush=True)
                previous = state
            if campaign["status"] not in {"queued", "running", "waiting"}:
                break
            assert time.monotonic() < deadline, f"Timed out; resume campaign {campaign_id}"
            page.wait_for_timeout(5000)
        results = api(f"/campaigns/{campaign_id}/results")["results"]
        selected = [row for row in results if row["selected"] and row["review_status"] != "rejected"]
        assert campaign["status"] in {"completed", "partial"}, campaign
        assert any(job["source"] == "website" for job in campaign["jobs"]), campaign
        assert selected and len(selected) <= campaign["target_count"], campaign
        assert all(row["within_radius"] and row["maps_url"] for row in selected), selected
        assert len({row["company_id"] for row in results}) == len(results)
        page.reload()
        page.get_by_role("button", name=campaign["name"], exact=False).first.click()
        page.wait_for_function("rows => results.length === rows", arg=len(results))
        assert page.evaluate("markers.getLayers().length") == len(selected)
        page.locator("#results tr").first.wait_for()
        page.screenshot(path=str(output / f"{campaign_id}-panel.png"), full_page=True)
        page.locator("#results .company-focus").first.click()
        page.locator(".leaflet-popup-content").wait_for()
        page.screenshot(path=str(output / f"{campaign_id}-map.png"))
        with urlopen(BASE + f"/api/v1/campaigns/{campaign_id}/export", timeout=20) as response:
            exported = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
        assert len(exported) == len(results)  # One institutional company per row.
        assert {row["company_id"] for row in exported} == {row["company_id"] for row in results}
        first = selected[0]
        original = first["review_status"]
        for status in ("approved", "rejected", original):
            api(f"/campaigns/{campaign_id}/results/{first['company_id']}", "PATCH", {"review_status": status})
            persisted = api(f"/campaigns/{campaign_id}/results")["results"]
            assert next(row for row in persisted if row["company_id"] == first["company_id"])["review_status"] == status
        assert not errors, errors
        job_starts = [datetime.fromisoformat(job["started_at"]) for job in campaign["jobs"] if job["started_at"]]
        job_ends = [datetime.fromisoformat(job["finished_at"]) for job in campaign["jobs"] if job["finished_at"]]
        report = {"campaign_id": campaign_id, "status": campaign["status"],
                  "validation_seconds": round(time.monotonic()-started, 1),
                  "collection_seconds": round((max(job_ends)-min(job_starts)).total_seconds(), 1), "raw_unique": len(results),
                  "selected": len(selected), "shortfall": campaign["shortfall"], "csv_rows": len(exported),
                  "with_phone": sum(bool(row["phone"]) for row in selected),
                  "with_website": sum(bool(row["website"]) for row in selected),
                  "with_email": sum(bool(row["emails"]) for row in selected),
                  "with_social": sum(any(row[key] for key in ("instagram", "facebook", "linkedin")) for row in selected),
                  "checks": ["real campaign", "web rendering", "worker", "radius", "deduplication", "real markers", "popup", "CSV", "review persistence", "no JS errors"]}
        (output / f"{campaign_id}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PASS", json.dumps(report), flush=True)
        browser.close()


if __name__ == "__main__":
    run()
