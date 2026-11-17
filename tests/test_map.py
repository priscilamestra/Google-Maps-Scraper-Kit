import csv
import http.client
import json
import math
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch, MagicMock

from google_maps_scraper_kit.api import create_server
from google_maps_scraper_kit.db import connect, create_campaign, initialize, list_campaign_results, upsert_maps_results, get_campaign
from google_maps_scraper_kit.export import export_companies
from google_maps_scraper_kit.geography import coordinates, distance_m, search_area
from google_maps_scraper_kit.geocoding import locate
from google_maps_scraper_kit.maps import _command, _read_results
from google_maps_scraper_kit.models import Campaign
from google_maps_scraper_kit.normalization import normalize_maps_results
from google_maps_scraper_kit.qualification import qualify_campaign

AREA = {"latitude": 0, "longitude": 0, "radius_m": 1000}
CONFIG = {"name": "synthetic map test", "mode": "companies", "target_count": 3,
          "match_business_terms": False,
          "business_terms": ["agencia"], "location": {"city": "Cidade de teste"}, "search_area": AREA}


class GeographyTest(unittest.TestCase):
    def test_validation_and_distance(self):
        self.assertEqual(coordinates({"latitude": "0", "longitude": "0"}), (0, 0))
        for value in ("NaN", "Infinity", None, "", True, 91):
            self.assertEqual(coordinates({"latitude": value, "longitude": 0}), (None, None))
        self.assertIsNone(distance_m(None, None, AREA))
        self.assertEqual(distance_m(0, 0, AREA), 0)
        self.assertAlmostEqual(distance_m(0, 1, AREA), 111195.08, places=1)
        for field in AREA:
            for value in (True, "1", float("nan"), float("inf"), None):
                with self.assertRaises(ValueError):
                    search_area({**AREA, field: value})
        with self.assertRaises(ValueError):
            search_area({**AREA, "radius_m": 10001})
        edge = math.degrees(1000 / 6371008.8)
        self.assertAlmostEqual(distance_m(edge, 0, AREA), 1000, places=8)

    def seed(self, root):
        database = root / "map.db"
        campaign_id, jobs = create_campaign(database, Campaign.from_dict(CONFIG))
        source = root / "source.csv"
        with source.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["title", "place_id", "latitude", "longitude", "link"])
            writer.writeheader()
            for title, place_id, lat in (("Inside", "synthetic1", "0"), ("Outside", "synthetic2", "1"), ("Unknown", "synthetic3", "")):
                writer.writerow({"title": title, "place_id": place_id, "latitude": lat, "longitude": "0", "link": "https://www.google.com/maps?cid="+place_id})
        upsert_maps_results(database, jobs[0], _read_results(source))
        normalize_maps_results(database, jobs[0])
        return database, campaign_id

    def test_radius_exclusions_persist_and_export_real_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, campaign_id = self.seed(root)
            qualify_campaign(database, campaign_id)
            results = {row["name"]: row for row in list_campaign_results(database, campaign_id)}
            self.assertIsNone(results["Inside"]["exclusion_reason"])
            self.assertTrue(results["Inside"]["within_radius"])
            self.assertEqual(results["Outside"]["exclusion_reason"], "outside_radius")
            self.assertFalse(results["Outside"]["within_radius"])
            self.assertEqual(results["Unknown"]["exclusion_reason"], "location_unverified")
            self.assertIsNone(results["Unknown"]["latitude"])
            self.assertEqual(get_campaign(database, campaign_id)["result_count"], 1)
            output = root / "export.csv"
            self.assertEqual(export_companies(database, output, campaign_id=campaign_id), 3)
            with output.open(encoding="utf-8-sig", newline="") as file:
                rows = {row["name"]: row for row in csv.DictReader(file)}
            self.assertEqual(rows["Inside"]["latitude"], "0.0")
            self.assertEqual(rows["Unknown"]["latitude"], "")
            self.assertEqual(rows["Inside"]["place_id"], "synthetic1")

    def test_old_database_backfill_is_repeatable_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database, campaign_id = self.seed(Path(directory))
            with closing(connect(database)) as connection, connection:
                for column in ("latitude", "longitude", "maps_url", "place_id"):
                    connection.execute(f"ALTER TABLE companies DROP COLUMN {column}")
            initialize(database)
            initialize(database)
            results = {row["name"]: row for row in list_campaign_results(database, campaign_id)}
            self.assertEqual(len(results), 3)
            self.assertEqual(results["Inside"]["latitude"], 0)
            self.assertEqual(results["Outside"]["place_id"], "synthetic2")

    def test_adapter_passes_explicit_center_and_portuguese(self):
        job = {"query_json": json.dumps({"queries": ["a", "b"], "limit": 3}), "target_count": 3, "config_json": json.dumps(CONFIG)}
        command = _command(job, Path("scrape.py"), Path("out.csv"))
        self.assertEqual(command[2:5], ["a", "0", "0"])
        self.assertNotIn("--city", command)
        self.assertEqual(command[command.index("--lang")+1], "pt")
        self.assertIn("--keyword", command)

    def test_geocoding_cache_does_not_repeat_network(self):
        with tempfile.TemporaryDirectory() as directory:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = b'[{"lat":"0","lon":"0","display_name":"Synthetic place"}]'
            with patch("google_maps_scraper_kit.geocoding.urlopen", return_value=response) as network:
                first = locate(Path(directory)/"test.db", "Synthetic place")
                self.assertEqual(first, locate(Path(directory)/"test.db", "SYNTHETIC PLACE"))
                self.assertEqual(network.call_count, 1)

    def test_http_static_geodata_and_cross_origin_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            database, campaign_id = self.seed(Path(directory))
            server = create_server(database, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                for path, content in (("/", b"Google Maps Scraper"), ("/app.js", b"setRadius"), ("/app.css", b"#map")):
                    connection.request("GET", path)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertIn(content, response.read())
                connection.request("GET", f"/api/v1/campaigns/{campaign_id}/results")
                response = connection.getresponse()
                rows = json.loads(response.read())["results"]
                self.assertEqual(len(rows), 3)
                self.assertIn("latitude", rows[0])
                connection.request("POST", "/api/v1/campaigns", json.dumps(CONFIG), {"Origin": "https://external.example", "Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
