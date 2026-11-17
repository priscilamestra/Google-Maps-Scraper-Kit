import json
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from google_maps_scraper_kit.models import Campaign
from google_maps_scraper_kit.db import create_campaign, list_jobs, connect, upsert_maps_results, list_campaign_results, get_campaign, update_result_review, initialize
from google_maps_scraper_kit.normalization import normalize_maps_results
from google_maps_scraper_kit.qualification import qualify_campaign
from google_maps_scraper_kit.maps import _command

CONFIG = {"name": "Synthetic planning", "mode": "companies", "target_count": 3,
          "business_terms": ["Agencia", "Publicidade"], "location": {"city": "Cidade de teste"}}


class PlanningTest(unittest.TestCase):
    def test_languages_persist_and_reach_engine_without_neighborhoods(self):
        self.assertEqual(Campaign.from_dict(CONFIG).language, "pt")
        for language in ("pt", "en", "es", "fr", "de"):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                database = Path(directory)/"test.db"
                campaign = Campaign.from_dict({**CONFIG, "language": language})
                campaign_id, jobs = create_campaign(database, campaign)
                saved = get_campaign(database, campaign_id)["config"]
                self.assertEqual(saved["language"], language)
                self.assertEqual(saved["neighborhoods"], [])
                self.assertEqual(len(jobs), len(CONFIG["business_terms"]))
                command = _command({"config_json": json.dumps(saved), "query_json": json.dumps({"queries": [campaign.maps_queries()[0]], "limit": 3}), "target_count": 3}, Path("scrape.py"), Path("out.csv"))
                self.assertEqual(command[command.index("--lang")+1], language)
        for language in ("PT", "pt-BR", "p", "123", None, 1, True, []):
            with self.assertRaisesRegex(ValueError, "language"):
                Campaign.from_dict({**CONFIG, "language": language})

    def test_city_and_explicit_neighborhoods_share_one_campaign_and_budget(self):
        campaign = Campaign.from_dict({**CONFIG, "collection_limit": 12, "neighborhoods": ["Bairro A", "Bairro B", "Bairro A"]})
        self.assertEqual(len(campaign.maps_queries()), 6)
        self.assertEqual(campaign.maps_queries()[2], "Agencia em Bairro A, Cidade de teste")
        self.assertEqual(sum(limit for _, limit in campaign.maps_plan()), 12)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)/"test.db"
            campaign_id, job_ids = create_campaign(database, campaign)
            self.assertEqual(len(job_ids), 6)
            self.assertEqual({job["campaign_id"] for job in list_jobs(database)}, {campaign_id})
            saved = get_campaign(database, campaign_id)
            self.assertEqual(saved["config"]["collection_limit"], 12)

    def test_rejects_invalid_budgets_and_keeps_old_default(self):
        self.assertEqual(sum(limit for _, limit in Campaign.from_dict(CONFIG).maps_plan()), 3)
        for value in (True, 0, 2, 5001, "12"):
            with self.assertRaises(ValueError):
                Campaign.from_dict({**CONFIG, "collection_limit": value})
        with self.assertRaises(ValueError):
            Campaign.from_dict({**CONFIG, "neighborhoods": ["Bairro A", "Bairro B"]})

    def test_selects_best_until_target_preserves_extras_and_replaces_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)/"test.db"
            campaign_id, jobs = create_campaign(database, Campaign.from_dict({**CONFIG, "target_count": 1, "collection_limit": 4}))
            rows=[]
            for name, phone, website in (("A", "111", "https://a.example"), ("B", "222", ""), ("C", "", "")):
                rows.append({"fingerprint": name,"title": name,"phone": phone,"emails": "","website": website,
                             "category": "Agencia","address": "","review_rating": "","review_count": "",
                             "source_url": "","raw_json": "{}"})
            for job in jobs:
                upsert_maps_results(database, job, rows)
                normalize_maps_results(database, job)
            qualify_campaign(database, campaign_id)
            results = list_campaign_results(database, campaign_id)
            self.assertEqual(len(results), 3)
            self.assertEqual([x["name"] for x in results if x["selected"]], ["A"])
            self.assertEqual(get_campaign(database, campaign_id)["result_count"], 1)
            self.assertTrue(all(x["selection_reason"]=="beyond_target" for x in results if not x["selected"]))
            update_result_review(database, campaign_id, results[0]["company_id"], "rejected")
            results = list_campaign_results(database, campaign_id)
            self.assertEqual([x["name"] for x in results if x["selected"]], ["B"])
            with closing(connect(database)) as connection, connection:
                connection.execute("ALTER TABLE campaign_companies DROP COLUMN selected")
                connection.execute("ALTER TABLE campaign_companies DROP COLUMN selection_reason")
            initialize(database)
            qualify_campaign(database, campaign_id)
            self.assertEqual(len(list_campaign_results(database, campaign_id)), 3)
