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

from leads_prospect_ai.db import (
    approve_campaign,
    claim_next_maps_job,
    connect,
    create_campaign,
    defer_or_fail_job,
    enqueue_tavily_job,
    enqueue_website_job,
    finish_job,
    list_companies,
    list_evidence,
    list_jobs,
    list_maps_results,
    list_people,
    list_source_health,
    list_campaign_results,
    pause_job,
    resume_job,
    upsert_maps_results,
)
from leads_prospect_ai.api import create_server
from leads_prospect_ai.contacts import clean_emails
from leads_prospect_ai.maps import run_next_maps_job
from leads_prospect_ai.models import Campaign, JobState
from leads_prospect_ai.normalization import normalize_maps_results
from leads_prospect_ai.qualification import qualify_campaign
from leads_prospect_ai.export import export_companies
from leads_prospect_ai.service import execute_company_campaign
from leads_prospect_ai.tavily import _setting, run_next_tavily_job
from leads_prospect_ai.website import enrich_websites, run_next_website_job, validate_public_url


VALID = {
    "name": "clinicas_campinas",
    "mode": "companies_and_people",
    "target_count": 10,
    "business_terms": ["clinica de estetica"],
    "location": {"city": "Campinas", "state": "SP", "country": "BR"},
    "decision_maker_roles": ["fundador"],
    "manual_approval_required": True,
}


class CampaignTest(unittest.TestCase):
    def test_builds_maps_query(self) -> None:
        campaign = Campaign.from_dict(VALID)
        self.assertEqual(campaign.maps_queries(), ("clinica de estetica em Campinas, SP, BR",))

    def test_rejects_people_search_without_roles(self) -> None:
        raw = {**VALID, "decision_maker_roles": []}
        with self.assertRaisesRegex(ValueError, "decision_maker_roles"):
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
            db_path = Path(directory) / "leads.db"
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
            db_path = root / "leads.db"
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
            db_path = root / "leads.db"
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
            db_path = Path(directory) / "leads.db"
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
            db_path = Path(directory) / "leads.db"
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
            db_path = Path(directory) / "leads.db"
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
            db_path = Path(directory) / "leads.db"
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
            db_path = Path(directory) / "leads.db"
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
    def test_reads_tavily_settings_from_local_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("TAVILY_API_KEY='secret-value'\n", encoding="utf-8")
            self.assertEqual(_setting("TAVILY_API_KEY", env_file=env_file), "secret-value")

    def test_executes_one_company_campaign_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "leads.db"
            output = root / "companies.csv"

            def executor(path):
                def fake_runner(command, **_kwargs):
                    self.assertIn("--socials", command)
                    self.assertNotIn("--no-email", command)
                    csv_path = Path(command[command.index("--out") + 1])
                    csv_path.parent.mkdir(parents=True, exist_ok=True)
                    csv_path.write_text(
                        "title,phone,website,address,link\n"
                        "Café A,111,https://a.example,Rua A,https://maps.example/a\n",
                        encoding="utf-8",
                    )

                return run_next_maps_job(path, project_root=root, runner=fake_runner)

            result = execute_company_campaign(
                db_path,
                Campaign.from_dict(
                    {
                        **VALID,
                        "mode": "companies",
                        "decision_maker_roles": [],
                        "collect_emails": True,
                        "collect_socials": True,
                    }
                ),
                output,
                executor=executor,
            )
            output_exists = output.exists()

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["companies"], 1)
        self.assertEqual(result["exported_rows"], 1)
        self.assertTrue(output_exists)

    def test_executes_people_campaign_with_public_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "people.csv"

            def tavily_executor(path):
                return run_next_tavily_job(
                    path,
                    searcher=lambda _query: {"results": [{
                        "title": "Ana Silva - Fundador | LinkedIn",
                        "url": "https://br.linkedin.com/in/ana-silva",
                        "content": "Fundador de empresa de tecnologia em Campinas",
                    }]},
                    monthly_limit=10,
                )

            result = execute_company_campaign(
                Path(directory) / "leads.db",
                Campaign.from_dict({**VALID, "mode": "people", "business_terms": []}),
                output,
                tavily_executor=tavily_executor,
            )
            with output.open(newline="", encoding="utf-8-sig") as file:
                exported = list(csv.DictReader(file))

        self.assertEqual(result["people"], 1)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(exported[0]["decision_maker_name"], "Ana Silva")
        self.assertEqual(exported[0]["decision_maker_verification"], "likely")

    def test_executes_hybrid_campaign_through_website_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "leads.db"

            def maps_executor(path):
                def fake_runner(command, **_kwargs):
                    csv_path = Path(command[command.index("--out") + 1])
                    csv_path.parent.mkdir(parents=True, exist_ok=True)
                    csv_path.write_text(
                        "title,website,link\nEmpresa A,https://a.example,https://maps.example/a\n",
                        encoding="utf-8",
                    )

                return run_next_maps_job(path, project_root=root, runner=fake_runner)

            def website_executor(path):
                def fake_enricher(_path, roles, *, company_ids):
                    self.assertEqual(roles, ["fundador"])
                    self.assertEqual(len(company_ids), 1)
                    return {"domains": 1, "pages": 2, "people": 1, "evidence_added": 3, "skipped": 0, "failed": 0}

                return run_next_website_job(path, enricher=fake_enricher)

            def tavily_executor(path):
                return run_next_tavily_job(path, searcher=lambda _query: {"results": []}, monthly_limit=10)

            result = execute_company_campaign(
                db_path,
                Campaign.from_dict(VALID),
                root / "leads.csv",
                executor=maps_executor,
                website_executor=website_executor,
                tavily_executor=tavily_executor,
            )
            jobs = list_jobs(db_path)
            with (root / "leads.csv").open(newline="", encoding="utf-8-sig") as file:
                exported = list(csv.DictReader(file))

        self.assertEqual(result["state"], "completed")
        self.assertEqual([job["source"] for job in jobs], ["maps", "website", "tavily"])
        self.assertEqual([job["state"] for job in jobs], ["completed", "completed", "completed"])
        self.assertEqual(len(exported), 1)

    def test_tavily_adds_only_matching_linkedin_people_and_stops_at_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "leads.db"
            campaign_id, jobs = create_campaign(db_path, Campaign.from_dict(VALID))
            upsert_maps_results(
                db_path,
                jobs[0],
                [{
                    "fingerprint": "company-a",
                    "title": "Empresa A",
                    "phone": "",
                    "emails": "",
                    "website": "https://empresa.example",
                    "category": "Tecnologia",
                    "address": "Campinas",
                    "review_rating": "",
                    "review_count": "",
                    "source_url": "https://maps.example/a",
                    "raw_json": "{}",
                }],
            )
            normalize_maps_results(db_path, jobs[0])
            approve_campaign(db_path, campaign_id)
            enqueue_tavily_job(db_path, campaign_id, ("fundador",))

            def search(_query):
                return {"results": [
                    {"title": "Ana Silva - Fundador - Empresa A | LinkedIn", "url": "https://br.linkedin.com/in/ana-silva?trk=x", "content": "Fundador na Empresa A em Campinas"},
                    {"title": "Pessoa Errada - Fundador | LinkedIn", "url": "https://linkedin.com/in/errada", "content": "Outra empresa"},
                ]}

            result = run_next_tavily_job(db_path, searcher=search, monthly_limit=1)
            people = list_people(db_path)
            with closing(connect(db_path)) as connection:
                evidence = connection.execute(
                    "SELECT source_provider FROM evidence WHERE entity_type = 'person' AND entity_id = ?",
                    (people[0]["person_id"],),
                ).fetchall()

        self.assertEqual(result["queries"], 1)
        self.assertEqual(result["people"], 1)
        self.assertEqual(people[0]["name"], "Ana Silva")
        self.assertEqual(people[0]["linkedin_url"], "https://br.linkedin.com/in/ana-silva")
        self.assertEqual(people[0]["verification_status"], "likely")
        self.assertEqual(people[0]["confidence"], 0.8)
        self.assertTrue(any(item["source_provider"] == "tavily" for item in evidence))


class ProductApiTest(unittest.TestCase):
    def test_creates_and_lists_campaigns_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "leads.db"
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
            db_path = Path(directory) / "leads.db"
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

        self.assertGreater(result["fit_score"], 0)
        self.assertGreaterEqual(result["confidence"], 0.7)
        self.assertIsNone(result["exclusion_reason"])


class WebsiteTest(unittest.TestCase):
    def test_rejects_private_network_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "private"):
            validate_public_url("http://127.0.0.1/admin")

    def test_empty_campaign_scope_does_not_scan_other_companies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "leads.db"
            create_campaign(db_path, Campaign.from_dict(VALID))
            result = enrich_websites(db_path, company_ids=[])

        self.assertEqual(result, {"domains": 0, "pages": 0, "people": 0, "evidence_added": 0, "skipped": 0, "failed": 0})

    def test_enriches_public_site_once_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "leads.db"
            campaign_id, jobs = create_campaign(db_path, Campaign.from_dict(VALID))
            upsert_maps_results(
                db_path,
                jobs[0],
                [{
                    "fingerprint": "company-a",
                    "title": "Empresa A",
                    "phone": "",
                    "emails": "",
                    "website": "https://empresa.example",
                    "category": "Tecnologia",
                    "address": "Campinas",
                    "review_rating": "",
                    "review_count": "",
                    "source_url": "https://maps.example/a",
                    "raw_json": "{}",
                }],
            )
            normalize_maps_results(db_path, jobs[0])
            pages = {
                "https://empresa.example/robots.txt": ("https://empresa.example/robots.txt", "text/plain; charset=utf-8", b"User-agent: *\nAllow: /"),
                "https://empresa.example/": (
                    "https://empresa.example/",
                    "text/html; charset=utf-8",
                    b'<a href="/equipe">Equipe</a><a href="mailto:contato@empresa.example">Contato</a>',
                ),
                "https://empresa.example/equipe": (
                    "https://empresa.example/equipe",
                    "text/html; charset=utf-8",
                    b'<script type="application/ld+json">invalid</script><script type="application/ld+json">{"@type":"Person","name":"Ana Silva","jobTitle":"Fundador","sameAs":"https://www.linkedin.com/in/ana-silva"}</script><section itemscope itemtype="https://schema.org/Person"><strong itemprop="name">Bia Souza</strong><span itemprop="jobTitle">Diretora</span><a itemprop="sameAs" href="https://www.linkedin.com/in/bia-souza">Perfil</a></section>',
                ),
            }

            def fetch(url):
                return pages[url]

            def resolve(*_args, **_kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

            first = enrich_websites(db_path, ["fundador", "diretor"], fetcher=fetch, resolver=resolve)
            second = enrich_websites(db_path, ["fundador", "diretor"], fetcher=fetch, resolver=resolve)
            people = list_people(db_path)
            evidence = list_evidence(db_path, list_companies(db_path)[0]["company_id"])
            output = Path(directory) / "leads.csv"
            export_companies(db_path, output)
            with output.open(newline="", encoding="utf-8-sig") as file:
                exported = list(csv.DictReader(file))

        self.assertEqual(first["pages"], 2)
        self.assertEqual(first["people"], 2)
        self.assertGreater(first["evidence_added"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(people[0]["name"], "Ana Silva")
        self.assertTrue(any(item["observed_value"] == "contato@empresa.example" for item in evidence))
        self.assertEqual({row["decision_maker_name"] for row in exported}, {"Ana Silva", "Bia Souza"})

    def test_defers_website_job_when_every_domain_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "leads.db"
            campaign_id, _jobs = create_campaign(db_path, Campaign.from_dict(VALID))
            approve_campaign(db_path, campaign_id)
            enqueue_website_job(db_path, campaign_id, ("fundador",))

            def failing(_path, _roles, *, company_ids):
                return {"domains": 0, "pages": 0, "people": 0, "evidence_added": 0, "skipped": 0, "failed": 1}

            with self.assertRaisesRegex(RuntimeError, "all website domains failed"):
                run_next_website_job(db_path, enricher=failing)
            website_job = next(job for job in list_jobs(db_path) if job["source"] == "website")

        self.assertEqual(website_job["state"], "waiting")
        self.assertIsNotNone(website_job["next_run_at"])


if __name__ == "__main__":
    unittest.main()
