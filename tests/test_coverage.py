import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch

from google_maps_scraper_kit.api import create_server
from google_maps_scraper_kit.db import (connect, create_campaign, approve_campaign, get_campaign, list_jobs,
    list_campaign_results, upsert_maps_results, claim_next_maps_job, finish_job)
from google_maps_scraper_kit.email_verification import check_domain, verify_campaign_emails
from google_maps_scraper_kit.export import export_companies
from google_maps_scraper_kit.geography import area_tiles, distance_m
from google_maps_scraper_kit.maps import run_next_maps_job, _command, _expand_or_stop
from google_maps_scraper_kit.models import Campaign
from google_maps_scraper_kit.normalization import normalize_maps_results
from google_maps_scraper_kit.qualification import qualify_campaign
from google_maps_scraper_kit.website import _PageParser, _emails

AREA = {"latitude": -22.306, "longitude": -42.493, "radius_m": 3000}
CONFIG = {"name": "Synthetic coverage", "mode": "companies", "target_count": 3,
          "business_terms": ["marketing"], "location": {"city": "Test City"},
          "search_area": AREA, "grid_size": 2, "collection_limit": 6}


def row(identity, **changes):
    return {"fingerprint": identity, "title": identity, "phone": "12345678", "emails": "",
            "website": "https://business.test", "category": "Marketing", "address": "Street 1",
            "review_rating": "4.8", "review_count": "125", "source_url": "https://www.google.com/maps/place/test",
            "raw_json": json.dumps({**AREA, "place_id": identity}), **changes}


class CoverageTest(unittest.TestCase):
    def test_geographic_grid_covers_circle_and_crosses_dateline_safely(self):
        for area in (AREA, {**AREA, "latitude": 0, "longitude": 179.99}):
            tiles = area_tiles(area, 3)
            self.assertEqual(len(tiles), 9)
            self.assertTrue(all(-180 <= tile["longitude"] <= 180 for tile in tiles))
            for north in range(-3000, 3001, 500):
                for east in range(-3000, 3001, 500):
                    if north*north+east*east > 3000**2:
                        continue
                    import math
                    lat = area["latitude"]+north/111195
                    lon = (area["longitude"]+east/(111195*math.cos(math.radians(area["latitude"])))+180)%360-180
                    self.assertTrue(any(distance_m(lat, lon, tile) <= tile["radius_m"] for tile in tiles))

    def test_plan_options_and_explicit_bounded_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"db"
            campaign = Campaign.from_dict({**CONFIG, "depth": 8, "deep_scan": True})
            cid, jobs = create_campaign(db, campaign)
            self.assertEqual(len(jobs), 5)
            with closing(connect(db)) as connection:
                job = dict(connection.execute("SELECT j.*, c.config_json,c.target_count FROM jobs j JOIN campaigns c USING(campaign_id) LIMIT 1").fetchone())
            command = _command(job, Path("kit.py"), Path("out.csv"))
            self.assertTrue(command[1].endswith("scrape-area.py"))
            self.assertEqual(command[command.index("--depth")+1], "8")
            self.assertEqual(command[command.index("--radius")+1], "3000.0")
            self.assertEqual(get_campaign(db, cid)["progress"], {"finished": 0, "total": 5})
            for changes in ({"depth": 0}, {"depth": True}, {"grid_size": 11}, {"max_queries": 4},
                            {"min_rating": float("nan")}, {"min_reviews": -1}, {"verify_emails": True},
                            {"no_website": True, "required_fields": ["website"]}):
                with self.assertRaises(ValueError):
                    create_campaign(db, Campaign.from_dict({**CONFIG, **changes}))

    def test_cross_tile_overlap_does_not_consume_budget_and_outside_does_not_starve_it(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"db"
            cid, jobs = create_campaign(db, Campaign.from_dict({**CONFIG, "collection_limit": 3}))
            approve_campaign(db, cid)
            batches = [[row("A"), row("outside", raw_json=json.dumps({"latitude": 0, "longitude": 0}))],
                       [row("A"), row("B"), row("C"), row("D")]]
            def runner(command, **kwargs):
                destination = Path(command[command.index("--out")+1])
                with destination.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=["title", "phone", "website", "latitude", "longitude", "place_id", "category"])
                    writer.writeheader()
                    for item in batches.pop(0):
                        raw = json.loads(item["raw_json"])
                        writer.writerow({"title": item["title"], "phone": item["phone"], "website": item["website"], "latitude": raw["latitude"], "longitude": raw["longitude"], "place_id": item["fingerprint"], "category": "Marketing"})
            root = Path(directory)
            self.assertEqual(run_next_maps_job(db, project_root=root, runner=runner)["state"], "completed")
            run_next_maps_job(db, project_root=root, runner=runner)
            self.assertEqual(len(list_campaign_results(db, cid)), 3)
            self.assertTrue(all(job["state"] == "completed" for job in list_jobs(db, cid)))
            self.assertEqual(get_campaign(db, cid)["result_count"], 3)

    def test_saturated_tile_adds_durable_children_and_respects_query_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"db"
            cid, jobs = create_campaign(db, Campaign.from_dict({**CONFIG, "deep_scan": True, "max_queries": 9}))
            approve_campaign(db, cid)
            job = claim_next_maps_job(db)
            self.assertIsNone(_expand_or_stop(db, job, 120))
            self.assertEqual(len(list_jobs(db, cid)), 9)
            self.assertIn("max_queries", _expand_or_stop(db, job, 120))
            finish_job(db, job["job_id"], job["lease_owner"], "completed")
            next_job = claim_next_maps_job(db)
            self.assertNotEqual(next_job["job_id"], job["job_id"])
            self.assertEqual(len(list_jobs(db, cid)), 9)

    def test_rating_reviews_absence_and_previous_list_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"db"
            old, old_jobs = create_campaign(db, Campaign.from_dict({**CONFIG, "grid_size": 1}))
            upsert_maps_results(db, old_jobs[0], [row("previous")]); normalize_maps_results(db, old_jobs[0])
            cid, jobs = create_campaign(db, Campaign.from_dict({**CONFIG, "grid_size": 1, "exclude_campaign_ids": [old], "min_rating": 4.5, "min_reviews": 100, "no_website": True}))
            upsert_maps_results(db, jobs[0], [row("previous", website=""), row("low_rating", website="", review_rating="4,2"), row("low_reviews", website="", review_count="99"), row("has_site"), row("keep", website="", review_count="1.234")])
            normalize_maps_results(db, jobs[0]); qualify_campaign(db, cid)
            reasons = {r["name"]: r["exclusion_reason"] for r in list_campaign_results(db, cid)}
            self.assertEqual(reasons, {"previous": "previous_list", "low_rating": "min_rating", "low_reviews": "min_reviews", "has_site": "has_website", "keep": None})

    def test_email_dns_states_cache_and_historical_observations(self):
        def dns_response(payload):
            return lambda url: (url, "application/json", json.dumps(payload).encode())
        self.assertEqual(check_domain("test.invalid", dns_response({"Status": 3}))["status"], "invalid")
        self.assertEqual(check_domain("test.invalid", dns_response({"Status": 2}))["status"], "unknown")
        self.assertEqual(check_domain("test.invalid", dns_response({"Status": 0, "Answer": [{"type": 15, "data": "0 ."}]}))["status"], "invalid")
        self.assertEqual(check_domain("test.invalid", dns_response({"Status": 0, "Answer": [{"type": 15, "data": "10 aspmx.l.google.com."}]}))["mail_host"], "google_workspace")
        def implicit(url):
            return url, "application/json", json.dumps({"Status": 0, "Answer": [{"type": 1, "data": "93.184.216.34"}]}).encode()
        self.assertEqual(check_domain("test.invalid", implicit)["status"], "implicit_mx")
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"db"
            cid, jobs = create_campaign(db, Campaign.from_dict({**CONFIG, "grid_size": 1, "collect_emails": True, "verify_emails": True}))
            upsert_maps_results(db, jobs[0], [row("A", emails="one@dead.test, two@live.test, three@live.test, four@unknown.test")]); normalize_maps_results(db, jobs[0])
            calls = []
            def checker(domain):
                calls.append(domain)
                return {"status": {"dead.test": "invalid", "live.test": "valid", "unknown.test": "unknown"}[domain], "mail_host": "other"}
            self.assertEqual(verify_campaign_emails(db, cid, checker), 1)
            result = list_campaign_results(db, cid)[0]
            self.assertNotIn("one@dead.test", result["emails"])
            self.assertIn("one@dead.test", result["published_emails"])
            self.assertIn("four@unknown.test", result["emails"])
            self.assertEqual(calls.count("live.test"), 1)
            verify_campaign_emails(db, cid, checker)
            self.assertEqual(calls.count("live.test"), 1)
            self.assertEqual(calls.count("unknown.test"), 2)
            export_companies(db, Path(directory)/"out.csv", campaign_id=cid)
            self.assertIn("email_checks_json", (Path(directory)/"out.csv").read_text(encoding="utf-8-sig"))

    def test_public_email_obfuscation_is_decoded_without_javascript(self):
        parser = _PageParser()
        value = "contato@empresa.test"
        encoded = bytes([42, *(ord(c)^42 for c in value)]).hex()
        parser.feed(f'<p>suporte [at] empresa [dot] test</p><a data-cfemail="{encoded}"></a>')
        self.assertEqual(set(_emails(parser).split(", ")), {value, "suporte@empresa.test"})

    def test_search_relevance_rejects_noise_and_accepts_explicit_alternatives(self):
        from google_maps_scraper_kit.qualification import extraction_filter
        config = {"business_terms": ["agência de marketing digital"], "match_business_terms": True}
        self.assertEqual(extraction_filter({"name": "Loja Digital", "category": "Supermercado"}, config), "search_mismatch")
        self.assertIsNone(extraction_filter({"name": "Empresa A", "category": "Consultoria em Marketing"}, config))
        self.assertIsNone(extraction_filter({"name": "Agência B", "category": "Agência de publicidade"}, {**config, "category_terms": ["marketing", "publicidade"]}))
        self.assertEqual(extraction_filter({"name": "Agência de viagens", "category": "Turismo"}, config), "search_mismatch")

    def test_public_social_redirects_and_maps_social_website_are_preserved_without_visit(self):
        from google_maps_scraper_kit.contacts import social_contact
        self.assertEqual(social_contact("https://www.google.com/url?q=https%3A%2F%2Fwa.me%2F552212345678&sa=D"), ("whatsapp", "https://wa.me/552212345678"))
        self.assertEqual(social_contact("https://www.google.com/url?q=javascript%3Aalert(1)"), (None, ""))
        self.assertEqual(social_contact("https://www.google.com/url?q=https%3A%2F%2Flinkedin.com%2Fin%2Fperson"), (None, ""))
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"db"
            cid, jobs = create_campaign(db, Campaign.from_dict({**CONFIG, "grid_size": 1, "collect_socials": True}))
            upsert_maps_results(db, jobs[0], [row("A", website="https://www.instagram.com/business/")]); normalize_maps_results(db, jobs[0])
            result = list_campaign_results(db, cid)[0]
            self.assertEqual(result["instagram"], "https://www.instagram.com/business")
            self.assertTrue(any(e["field"] == "instagram" and e["source_provider"] == "google_maps" for e in result["evidence"]))

    def test_json_download_uses_saved_results_and_has_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"db"
            cid, jobs = create_campaign(db, Campaign.from_dict({**CONFIG, "grid_size": 1}))
            upsert_maps_results(db, jobs[0], [row("A", raw_json=json.dumps({**AREA, "categories": ["Marketing"], "open_hours": {"Monday": "9-18"}}))]); normalize_maps_results(db, jobs[0])
            server = create_server(db, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/v1/campaigns/{cid}/export?format=json") as response:
                    payload = json.load(response)
                    self.assertIn("attachment", response.headers["Content-Disposition"])
                self.assertEqual(payload["results"][0]["maps_details"]["categories"], ["Marketing"])
                self.assertEqual(len(list_jobs(db, cid)), len(jobs))
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_viewport_wrapper_reuses_installed_kit_and_writes_all_raw_rows_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory)/"kit.py"
            fake.write_text('''LEAD=["title"]
import json
def req(method,path,body=None):
    if method=="POST": return 201,json.dumps({"id":"engine-id"}).encode()
    if path.endswith("download"): return 200,b"title\\nA\\nB\\n"
    return 200,json.dumps({"Status":"ok"}).encode()
def run_job(body):
    assert body["depth"]==7 and body["radius"]==650 and body["zoom"]==17
    assert body["lat"]=="-22.0" and body["email"] is False
    req("POST","/api/v1/jobs",body)
    req("GET","/api/v1/jobs/engine-id")
    req("GET","/api/v1/jobs/engine-id/download")
    return "engine-id", [{"title":"A","website":"example"},{"title":"B","website":"example"}]
def dedupe(rows): return rows
def clean_website(url): return "https://"+url
''', encoding="utf-8")
            output = Path(directory)/"out.csv"
            script = Path(__file__).resolve().parents[1]/"scripts"/"scrape-area.py"
            subprocess.run([sys.executable, str(script), "marketing", "-22", "-42", "--kit-script", str(fake), "--radius", "650", "--zoom", "17", "--depth", "7", "--limit", "1", "--out", str(output)], check=True, capture_output=True)
            with output.open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["website"], "https://example")
            self.assertEqual(json.loads(output.with_suffix(".progress.json").read_text()), {"engine_job_id":"engine-id", "stage":"ok", "raw_results":2})
            self.assertFalse(list(Path(directory).glob("*.tmp")))
