import json
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from google_maps_scraper_kit.api import create_server
from google_maps_scraper_kit.db import (connect, create_campaign, approve_campaign, claim_next_maps_job,
    finish_job, list_campaign_results, get_campaign, list_jobs, upsert_maps_results, freeze_completed_campaigns)
from google_maps_scraper_kit.models import Campaign
from google_maps_scraper_kit.normalization import normalize_maps_results
from google_maps_scraper_kit.qualification import qualify_campaign
from google_maps_scraper_kit.export import export_companies
from google_maps_scraper_kit.website import _add_evidence

AREA={"latitude":-22.28,"longitude":-42.53,"radius_m":10000}


def campaign(db, name):
    config={"name":name,"mode":"companies","target_count":1,"business_terms":["agencia"],
            "match_business_terms":False,
            "location":{"city":"Nova Friburgo","state":"RJ"},"search_area":AREA}
    cid,jobs=create_campaign(db,Campaign.from_dict(config))
    approve_campaign(db,cid)
    claimed=claim_next_maps_job(db)
    assert claimed["campaign_id"]==cid
    return cid,claimed


def import_company(db,job,name,phone,lat):
    raw={"latitude":lat,"longitude":-42.53,"place_id":"stable-company","link":"https://www.google.com/maps/place/stable"}
    upsert_maps_results(db,job["job_id"],[{"fingerprint":"stable-company","title":name,"phone":phone,
        "emails":"","website":"https://empresa.test","category":"Agencia","address":"Nova Friburgo",
        "review_rating":"","review_count":"","source_url":raw["link"],"raw_json":json.dumps(raw)}])
    normalize_maps_results(db,job["job_id"])


def finish(db,cid,job):
    qualify_campaign(db,cid)
    finish_job(db,job["job_id"],job["lease_owner"],"completed")


class HistoryTest(unittest.TestCase):
    def test_exhausted_interrupted_list_is_saved_before_next_campaign_changes_company(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"db";first,job=campaign(db,"Interrompida sem novas tentativas")
            import_company(db,job,"Empresa na lista interrompida","2222222222",-22.28)
            with closing(connect(db)) as c,c:
                c.execute("UPDATE jobs SET attempts=3,lease_expires_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",(job["job_id"],))
            second,job2=campaign(db,"Próxima lista")
            import_company(db,job2,"Empresa mudou depois","22999999999",-22.29)
            finish(db,second,job2)
            self.assertEqual(get_campaign(db,first)["status"],"partial")
            self.assertEqual(list_campaign_results(db,first)[0]["name"],"Empresa na lista interrompida")
            self.assertEqual(list_campaign_results(db,second)[0]["name"],"Empresa mudou depois")

    def test_lists_keep_their_own_observations_contacts_coordinates_and_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"data"/"db"
            first,job=campaign(db,"Lista de outubro")
            import_company(db,job,"Empresa antiga","2222222222",-22.28)
            company_id=list_campaign_results(db,first)[0]["company_id"]
            with closing(connect(db)) as c,c:
                c.execute("UPDATE campaigns SET created_at='2026-10-01T12:00:00+00:00' WHERE campaign_id=?",(first,))
                _add_evidence(c,"company",company_id,"emails","https://empresa.test","antigo@empresa.test","2026-10-01")
            finish(db,first,job)
            original=list_campaign_results(db,first)
            output=Path(directory)/"first.csv";export_companies(db,output,campaign_id=first)
            before=output.read_bytes()
            second,job2=campaign(db,"Lista de novembro")
            import_company(db,job2,"Empresa atualizada","22999999999",-22.29)
            with closing(connect(db)) as c,c:
                _add_evidence(c,"company",company_id,"emails","https://empresa.test","novo@empresa.test","2026-11-02")
            finish(db,second,job2)
            qualify_campaign(db,first)
            self.assertEqual(list_campaign_results(db,first),original)
            self.assertEqual(list_campaign_results(db,second)[0]["name"],"Empresa atualizada")
            self.assertEqual(list_campaign_results(db,first)[0]["latitude"],-22.28)
            self.assertNotIn("novo@empresa.test",list_campaign_results(db,first)[0]["emails"])
            export_companies(db,output,campaign_id=first)
            self.assertEqual(output.read_bytes(),before)
            self.assertTrue(get_campaign(db,first)["results_frozen_at"])
            self.assertEqual(len(list_jobs(db)),2)
            with closing(connect(db)) as c:
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_http_restart_and_deleted_csv_regenerate_without_new_scrape(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"data"/"db"
            cid,job=campaign(db,"Lista persistente")
            import_company(db,job,"Empresa","2222222222",-22.28);finish(db,cid,job)
            collected=[]
            for index in range(2):
                server=create_server(db,port=0)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    with urlopen(f"http://127.0.0.1:{server.server_port}/api/v1/campaigns/{cid}/results") as response:
                        self.assertEqual(len(json.load(response)["results"]),1)
                    with urlopen(f"http://127.0.0.1:{server.server_port}/api/v1/campaigns/{cid}/export") as response:
                        collected.append(response.read())
                    artifact=db.parent/"exports"/f"campaign-{cid}.csv"
                    self.assertTrue(artifact.is_file())
                    if index==0:artifact.unlink()
                finally:
                    server.shutdown();server.server_close();thread.join(timeout=5)
            self.assertEqual(collected[0],collected[1])
            self.assertEqual(len(list_jobs(db)),1)
            self.assertEqual(list_jobs(db)[0]["attempts"],1)

    def test_interrupted_extraction_resumes_without_duplicate_company(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"db";cid,job=campaign(db,"Interrompida")
            import_company(db,job,"Parcial","2222222222",-22.28)
            with closing(connect(db)) as c,c:
                c.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",(job["job_id"],))
            freeze_completed_campaigns(db)
            self.assertIsNone(get_campaign(db,cid)["results_frozen_at"])
            self.assertEqual(len(list_campaign_results(db,cid)),1)
            recovered=claim_next_maps_job(db)
            self.assertTrue(recovered["recovered"])
            import_company(db,recovered,"Parcial","2222222222",-22.28)
            finish(db,cid,recovered)
            self.assertEqual(len(list_campaign_results(db,cid)),1)
            self.assertEqual(list_jobs(db)[0]["attempts"],2)

    def test_atomic_csv_failure_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"db";cid,job=campaign(db,"Lista")
            import_company(db,job,"Empresa","2222222222",-22.28);finish(db,cid,job)
            output=Path(directory)/"list.csv";export_companies(db,output,campaign_id=cid)
            before=output.read_bytes()
            with patch("google_maps_scraper_kit.export.os.replace",side_effect=OSError("simulated write failure")):
                with self.assertRaises(OSError):
                    export_companies(db,output,campaign_id=cid)
            self.assertEqual(output.read_bytes(),before)
            self.assertEqual(list(Path(directory).glob("*.tmp")),[])
