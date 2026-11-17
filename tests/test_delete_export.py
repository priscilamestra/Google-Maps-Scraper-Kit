from __future__ import annotations

import csv
import io
import json
import tempfile
import threading
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from google_maps_scraper_kit.api import create_server
from google_maps_scraper_kit.db import connect, create_campaign, delete_campaign, get_campaign, upsert_maps_results
from google_maps_scraper_kit.models import Campaign
from google_maps_scraper_kit.normalization import normalize_maps_results


CONFIG = {
    "name": "Export test",
    "mode": "companies",
    "target_count": 10,
    "business_terms": ["marketing"],
    "location": {"city": "Nova Friburgo", "state": "RJ"},
    "match_business_terms": False,
}


def result(name: str) -> dict:
    return {
        "fingerprint": name,
        "title": name,
        "phone": "552212345678",
        "emails": f"contato@{name.casefold()}.test",
        "website": f"https://{name.casefold()}.test",
        "category": "Marketing",
        "address": "Rua Teste",
        "review_rating": "4.8",
        "review_count": "10",
        "source_url": f"https://maps.example/{name}",
        "raw_json": json.dumps({"place_id": name, "latitude": -22.2, "longitude": -42.5}),
    }


class DeleteAndExportTest(unittest.TestCase):
    def _campaign(self, db: Path, name: str, companies: list[str]):
        campaign_id, jobs = create_campaign(db, Campaign.from_dict({**CONFIG, "name": name}))
        upsert_maps_results(db, jobs[0], [result(company) for company in companies])
        normalize_maps_results(db, jobs[0])
        return campaign_id

    def test_delete_removes_only_unshared_campaign_data(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "google-maps-scraper-kit.db"
            first = self._campaign(db, "First", ["Shared", "OnlyFirst"])
            second = self._campaign(db, "Second", ["Shared", "OnlySecond"])

            delete_campaign(db, first)

            with self.assertRaisesRegex(ValueError, "campaign not found"):
                get_campaign(db, first)
            self.assertEqual(get_campaign(db, second)["name"], "Second")
            with closing(connect(db)) as connection:
                names = {row[0] for row in connection.execute("SELECT name FROM companies")}
                self.assertEqual(names, {"Shared", "OnlySecond"})
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs WHERE campaign_id=?", (first,)).fetchone()[0], 0)

    def test_delete_endpoint_rejects_running_job_then_returns_no_content(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "google-maps-scraper-kit.db"
            campaign_id = self._campaign(db, "Delete", ["A"])
            with closing(connect(db)) as connection, connection:
                connection.execute("UPDATE jobs SET state='running' WHERE campaign_id=?", (campaign_id,))
            server = create_server(db, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/api/v1/campaigns/{campaign_id}"
            try:
                with self.assertRaises(HTTPError) as raised:
                    urlopen(Request(url, method="DELETE"))
                self.assertEqual(raised.exception.code, 409)
                with closing(connect(db)) as connection, connection:
                    connection.execute("UPDATE jobs SET state='cancelled' WHERE campaign_id=?", (campaign_id,))
                with urlopen(Request(url, method="DELETE")) as response:
                    self.assertEqual(response.status, 204)
                with self.assertRaises(HTTPError) as missing:
                    urlopen(Request(url, method="DELETE"))
                self.assertEqual(missing.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_csv_json_and_xlsx_export_only_requested_company_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "google-maps-scraper-kit.db"
            campaign_id = self._campaign(db, "Exports", ["Included", "Excluded"])
            with closing(connect(db)) as connection:
                company_id = connection.execute("SELECT company_id FROM companies WHERE name='Included'").fetchone()[0]
            server = create_server(db, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/v1/campaigns/{campaign_id}/export"
            try:
                for export_format in ("csv", "json", "xlsx"):
                    query = urlencode({"format": export_format, "company_id": company_id})
                    with urlopen(f"{base}?{query}") as response:
                        data = response.read()
                        self.assertEqual(response.headers["X-Result-Count"], "1")
                    if export_format == "csv":
                        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
                        self.assertEqual([row["name"] for row in rows], ["Included"])
                    elif export_format == "json":
                        self.assertEqual([row["name"] for row in json.loads(data)["results"]], ["Included"])
                    else:
                        with zipfile.ZipFile(io.BytesIO(data)) as book:
                            sheet = book.read("xl/worksheets/sheet1.xml").decode()
                        self.assertIn("Included", sheet)
                        self.assertNotIn("Excluded", sheet)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
