"""The chat's HTTP entry and the existing CLI share one persistent campaign."""
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from contextlib import redirect_stdout

from google_maps_scraper_kit.api import create_server
from google_maps_scraper_kit.cli import main


class ChatApiTest(unittest.TestCase):
    def test_http_creation_and_cli_approval_share_campaign_and_options(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "google-maps-scraper-kit.db"
            server = create_server(database, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                config = {
                    "name": "synthetic-chat-request", "mode": "companies",
                    "target_count": 3, "business_terms": ["agencia"],
                    "location": {"city": "Cidade de teste"},
                    "collect_emails": True, "collect_socials": True,
                    "manual_approval_required": True,
                }
                connection.request("POST", "/api/v1/campaigns", json.dumps(config),
                                   {"Content-Type": "application/json"})
                response = connection.getresponse()
                created = json.loads(response.read())
                self.assertEqual(response.status, 201)
                self.assertEqual(created["status"], "pending_approval")
                campaign_id = created["campaign_id"]
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["--db", str(database), "campaign", campaign_id]), 0)
                persisted = json.loads(output.getvalue())
                self.assertTrue(persisted["config"]["collect_emails"])
                self.assertTrue(persisted["config"]["collect_socials"])
                with redirect_stdout(io.StringIO()):
                    main(["--db", str(database), "approve-campaign", campaign_id])
                connection.request("GET", f"/api/v1/campaigns/{campaign_id}")
                response = connection.getresponse()
                observed = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(observed["status"], "queued")
                self.assertEqual(observed["campaign_id"], campaign_id)
                self.assertEqual(len(observed["jobs"]), 1)
                connection.request("GET", f"/api/v1/campaigns/{campaign_id}/results")
                response = connection.getresponse()
                self.assertEqual(json.loads(response.read()), {"results": []})
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
