from __future__ import annotations

import csv
import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from google_maps_scraper_kit.db import (
    approve_campaign,
    claim_next_maps_job,
    connect,
    create_campaign,
    defer_or_fail_job,
    enqueue_website_job,
    finish_job,
    list_companies,
    list_evidence,
    list_jobs,
    list_maps_results,
    list_source_health,
    list_campaign_results,
    pause_job,
    resume_job,
    upsert_maps_results,
)
from google_maps_scraper_kit.api import create_server
from google_maps_scraper_kit.contacts import clean_emails
from google_maps_scraper_kit.maps import run_next_maps_job
from google_maps_scraper_kit.models import Campaign, JobState
from google_maps_scraper_kit.normalization import normalize_maps_results
from google_maps_scraper_kit.qualification import qualify_campaign
from google_maps_scraper_kit.export import export_companies
from google_maps_scraper_kit.service import execute_company_campaign
from google_maps_scraper_kit.website import enrich_websites, run_next_website_job, validate_public_url


VALID = {
    "match_business_terms": False,  # These fixtures exercise storage/queue, not topic matching.
    "name": "clinicas_campinas",
    "mode": "companies",
    "target_count": 10,
    "business_terms": ["clinica de estetica"],
    "location": {"city": "Campinas", "state": "SP", "country": "BR"},
    "manual_approval_required": True,
}


class CampaignTest(unittest.TestCase):
    def test_builds_maps_query(self) -> None:
        campaign = Campaign.from_dict(VALID)
        self.assertEqual(campaign.maps_queries(), ("clinica de estetica em Campinas, SP, BR",))

    def test_rejects_people_search_without_roles(self) -> None:
        raw = {**VALID, "mode": "people"}
        with self.assertRaisesRegex(ValueError, "mode"):
            Campaign.from_dict(raw)

    def test_splits_target_between_search_terms(self) -> None:
        campaign = Campaign.from_dict({**VALID, "target_count": 5, "business_terms": ["a", "b", "c"]})
        self.assertEqual([limit for _query, limit in campaign.maps_plan()], [2, 2, 1])

    def test_removes_placeholder_and_duplicate_emails(self) -> None:
        self.assertEqual(
            clean_emails("Contato@Empresa.com, seuemail@exemplo.com contato@empresa.com"),
            "contato@empresa.com",
        )


class QueueTest(unittest.TestCase):
    def test_persists_campaign_and_queued_maps_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "google-maps-scraper-kit.db"
            campaign_id, job_ids = create_campaign(db_path, Campaign.from_dict(VALID))
            jobs = list_jobs(db_path)

        self.assertTrue(campaign_id)
        self.assertEqual(len(job_ids), 1)
        self.assertEqual(jobs[0]["source"], "maps")
        self.assertEqual(jobs[0]["state"], "queued")
        self.assertEqual(jobs[0]["attempts"], 0)

    def test_runs_and_imports_maps_job_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "google-maps-scraper-kit.db"
            campaign_id, job_ids = create_campaign(db_path, Campaign.from_dict(VALID))
            approve_campaign(db_path, campaign_id)

            def fake_runner(command, **_kwargs):
                self.assertIn("--full", command)
                self.assertIn("--no-email", command)
                self.assertEqual(command[command.index("--city") + 1], "Campinas, SP, BR")
                self.assertEqual(command[command.index("--limit") + 1], "10")
                output = Path(command[command.index("--out") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    "title,phone,website,address,review_rating,review_count,link\n"
                    "Clínica A,111,https://a.example,Rua A,4.8,10,https://maps.example/a\n"
                    "Clínica A,111,https://a.example,Rua A,4.8,10,https://maps.example/a\n",
                    encoding="utf-8",
                )

            result = run_next_maps_job(db_path, project_root=root, runner=fake_runner)
            imported = list_maps_results(db_path, job_ids[0])
            jobs = list_jobs(db_path)

        self.assertEqual(result["results"], 1)
        self.assertEqual(result["companies"], 1)
        self.assertGreater(result["evidence_added"], 0)
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["title"], "Clínica A")
        self.assertEqual(jobs[0]["state"], "completed")
        self.assertEqual(jobs[0]["attempts"], 1)

    def test_defers_failed_maps_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "google-maps-scraper-kit.db"
            campaign_id, _ = create_campaign(db_path, Campaign.from_dict(VALID))
            approve_campaign(db_path, campaign_id)

            def failing_runner(_command, **_kwargs):
                raise OSError("engine unavailable")

            with self.assertRaisesRegex(RuntimeError, "engine unavailable"):
                run_next_maps_job(db_path, project_root=root, runner=failing_runner)
            jobs = list_jobs(db_path)

        self.assertEqual(jobs[0]["state"], "waiting")
        self.assertEqual(jobs[0]["attempts"], 1)
        self.assertIsNotNone(jobs[0]["next_run_at"])
        self.assertEqual(jobs[0]["error"], "engine unavailable")

    def test_reclaims_expired_lease_and_rejects_old_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "google-maps-scraper-kit.db"
            campaign_id, _ = create_campaign(db_path, Campaign.from_dict(VALID))
            approve_campaign(db_path, campaign_id)
            first = claim_next_maps_job(db_path)
            with closing(connect(db_path)) as connection, connection:
                connection.execute(
                    "UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE job_id = ?",
                    (first["job_id"],),
                )
            second = claim_next_maps_job(db_path)
            with self.assertRaisesRegex(RuntimeError, "lease lost"):
                finish_job(db_path, first["job_id"], first["lease_owner"], JobState.COMPLETED)
            finish_job(db_path, second["job_id"], second["lease_owner"], JobState.COMPLETED)
            jobs = list_jobs(db_path)

        self.assertTrue(second["recovered"])
        self.assertNotEqual(first["lease_owner"], second["lease_owner"])
        self.assertEqual(jobs[0]["attempts"], 2)
        self.assertEqual(jobs[0]["state"], "completed")

    def test_deduplicates_company_and_evidence_across_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "google-maps-scraper-kit.db"
            _, first_jobs = create_campaign(db_path, Campaign.from_dict(VALID))
            _, second_jobs = create_campaign(db_path, Campaign.from_dict({**VALID, "name": "second"}))
            row = {
                "fingerprint": "same-place",
                "title": "Clínica A",
                "phone": "111",
                "emails": "contato@a.example",
                "website": "https://a.example",
                "category": "Clínica",
                "address": "Rua A",
                "review_rating": "4.8",
                "review_count": "10",
                "source_url": "https://maps.example/a",
                "raw_json": '{"instagram": "https://instagram.com/a"}',
            }
            upsert_maps_results(db_path, first_jobs[0], [row])
            upsert_maps_results(db_path, second_jobs[0], [row])
            first_result = normalize_maps_results(db_path, first_jobs[0])
            second_result = normalize_maps_results(db_path, second_jobs[0])
            companies = list_companies(db_path)
            evidence = list_evidence(db_path, companies[0]["company_id"])
            output = Path(directory) / "companies.csv"
            exported = export_companies(db_path, output)
            with output.open(newline="", encoding="utf-8-sig") as file:
                exported_rows = list(csv.DictReader(file))

        self.assertEqual(first_result["companies"], 1)
        self.assertGreater(first_result["evidence_added"], 0)
        self.assertEqual(second_result, {"companies": 1, "evidence_added": 0})
        self.assertEqual(len(companies), 1)
        self.assertTrue(any(item["field"] == "maps_url" for item in evidence))
        self.assertEqual(exported, 1)
        self.assertIn("https://maps.example/a", exported_rows[0]["source_urls"])
        self.assertEqual(exported_rows[0]["emails"], "contato@a.example")
        self.assertEqual(exported_rows[0]["instagram"], "https://instagram.com/a")

    def test_stops_reclaiming_after_three_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "google-maps-scraper-kit.db"
            campaign_id, _ = create_campaign(db_path, Campaign.from_dict(VALID))
            approve_campaign(db_path, campaign_id)
            claimed = claim_next_maps_job(db_path)
            with closing(connect(db_path)) as connection, connection:
                connection.execute(
                    """
                    UPDATE jobs SET attempts = 3, lease_expires_at = '2000-01-01T00:00:00+00:00'
                    WHERE job_id = ?
                    """,
                    (claimed["job_id"],),
                )
            self.assertIsNone(claim_next_maps_job(db_path))
            jobs = list_jobs(db_path)

        self.assertEqual(jobs[0]["state"], "failed")
        self.assertIn("maximum attempts", jobs[0]["error"])

    def test_requires_approval_and_pauses_queued_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "google-maps-scraper-kit.db"
            campaign_id, job_ids = create_campaign(db_path, Campaign.from_dict(VALID))
            self.assertIsNone(claim_next_maps_job(db_path))
            approve_campaign(db_path, campaign_id)
            pause_job(db_path, job_ids[0])
            self.assertIsNone(claim_next_maps_job(db_path))
            resume_job(db_path, job_ids[0])
            claimed = claim_next_maps_job(db_path)

        self.assertEqual(claimed["job_id"], job_ids[0])

    def test_backoff_then_opens_circuit_after_three_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "google-maps-scraper-kit.db"
            campaign_id, _ = create_campaign(db_path, Campaign.from_dict(VALID))
            approve_campaign(db_path, campaign_id)
            outcomes = []
            for attempt in range(3):
                claimed = claim_next_maps_job(db_path)
                outcomes.append(
                    defer_or_fail_job(db_path, claimed["job_id"], claimed["lease_owner"], "temporary")
                )
                if attempt < 2:
                    with closing(connect(db_path)) as connection, connection:
                        connection.execute(
                            "UPDATE jobs SET next_run_at = '2000-01-01T00:00:00+00:00' WHERE job_id = ?",
                            (claimed["job_id"],),
                        )
            health = list_source_health(db_path)
            jobs = list_jobs(db_path)

        self.assertEqual([item["state"] for item in outcomes], ["waiting", "waiting", "failed"])
        self.assertEqual(jobs[0]["state"], "failed")
        self.assertEqual(health[0]["consecutive_failures"], 3)
        self.assertIsNotNone(health[0]["open_until"])


class ServiceTest(unittest.TestCase):
    def test_executes_company_and_institutional_website_on_demand(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root/"google-maps-scraper-kit.db"
            def executor(path):
                def runner(command, **kwargs):
                    self.assertIn("--no-email", command)
                    self.assertNotIn("--socials", command)
                    Path(command[command.index("--out")+1]).write_text("title,phone,website,link\nEmpresa A,22222222,https://empresa.test,https://maps.example/a\n", encoding="utf-8")
                return run_next_maps_job(path, project_root=root, runner=runner)
            def websites(path):
                return run_next_website_job(path, enricher=lambda p,**kw: {"domains":1,"pages":1,"evidence_added":0,"skipped":0,"failed":0})
            result = execute_company_campaign(database, Campaign.from_dict({**VALID,"collect_socials":True}), root/"out.csv", executor=executor, website_executor=websites)
            self.assertEqual(result["state"], "completed")
            self.assertEqual([j["source"] for j in list_jobs(database)], ["maps","website"])
            self.assertEqual(result["exported_rows"],1)


class ProductApiTest(unittest.TestCase):
    def test_creates_and_lists_campaigns_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "google-maps-scraper-kit.db"
            server = create_server(db_path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                body = json.dumps({**VALID, "mode": "companies", "decision_maker_roles": [], "manual_approval_required": False})
                connection.request("POST", "/api/v1/campaigns", body, {"Content-Type": "application/json"})
                created = connection.getresponse()
                payload = json.loads(created.read())
                self.assertEqual(created.status, 201)
                self.assertEqual(payload["status"], "queued")
                connection.request("POST", f"/api/v1/jobs/{payload['job_ids'][0]}/pause", "{}", {"Content-Type": "application/json"})
                paused = connection.getresponse()
                self.assertEqual(paused.status, 200)
                paused.read()
                connection.request("POST", f"/api/v1/jobs/{payload['job_ids'][0]}/resume", "{}", {"Content-Type": "application/json"})
                resumed = connection.getresponse()
                self.assertEqual(resumed.status, 200)
                resumed.read()
                connection.request("GET", "/api/v1/campaigns")
                listed = connection.getresponse()
                campaigns = json.loads(listed.read())["campaigns"]
                self.assertEqual(listed.status, 200)
                self.assertEqual(campaigns[0]["campaign_id"], payload["campaign_id"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_qualifies_campaign_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "google-maps-scraper-kit.db"
            campaign_id, jobs = create_campaign(db_path, Campaign.from_dict({**VALID, "required_fields": ["phone"]}))
            upsert_maps_results(db_path, jobs[0], [{
                "fingerprint": "company-a", "title": "Empresa A", "phone": "111",
                "emails": "", "website": "https://empresa.example", "category": "Marketing",
                "address": "Campinas", "review_rating": "4.8", "review_count": "10",
                "source_url": "https://maps.example/a", "raw_json": "{}",
            }])
            normalize_maps_results(db_path, jobs[0])
            qualify_campaign(db_path, campaign_id)
            result = list_campaign_results(db_path, campaign_id)[0]

        self.assertNotIn("fit_score", result)
        self.assertNotIn("confidence", result)
        self.assertIsNone(result["exclusion_reason"])


if __name__ == "__main__":
    unittest.main()
