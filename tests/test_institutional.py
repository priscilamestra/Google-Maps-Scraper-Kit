import csv
import json
import socket
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from google_maps_scraper_kit.db import (connect, create_campaign, approve_campaign, list_campaign_results,
    upsert_maps_results, claim_next_maps_job, list_jobs, get_campaign, list_evidence, finish_job)
from google_maps_scraper_kit.models import Campaign
from google_maps_scraper_kit.normalization import normalize_maps_results
from google_maps_scraper_kit.qualification import qualify_campaign
from google_maps_scraper_kit.website import enrich_websites, run_next_website_job, _connect_public, _crawl, _contact, _add_evidence
from google_maps_scraper_kit.export import export_companies, DEPRECATED_FIELDS
from google_maps_scraper_kit.worker import run_worker_once

CONFIG = {"name":"Synthetic institutional", "mode":"companies", "target_count":5,
          "match_business_terms":False,
          "business_terms":["agencia"], "location":{"city":"Cidade de teste"},
          "collect_emails":True, "collect_socials":True}


def resolver(*args, **kwargs):
    return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))]


def add_company(db, job, fingerprint="a", title="Empresa", website="https://empresa.test"):
    row={"fingerprint":fingerprint,"title":title,"website":website,"phone":"","emails":"",
         "category":"Marketing","address":"","review_rating":"","review_count":"",
         "source_url":"https://www.google.com/maps/place/company", "raw_json":"{}"}
    upsert_maps_results(db,job,[row])
    normalize_maps_results(db,job)


def site_fetcher(url):
    if url.endswith("/robots.txt"):
        return url,"text/plain",b"User-agent: *\nAllow: /"
    if url.endswith("/contato"):
        raise OSError("page unavailable")
    html = '''<p>contato@empresa.test</p><p>Telefone: (22) 2222-3333</p>
      <a href="/contato">Contato</a><a href="/sobre">Sobre</a>
      <a href="https://www.linkedin.com/in/private-person">Pessoa fora do escopo</a>
      <script type="application/ld+json">[{"@type":"Organization","name":"Empresa",
      "sameAs":["https://www.linkedin.com/company/empresa/?utm_source=site",
      "https://www.instagram.com/empresa/","https://www.youtube.com/@empresa"],
      "telephone":"+55 (22) 99999-8888"},
      {"@type":"Person","name":"Pessoa histórica","jobTitle":"CEO","sameAs":"https://linkedin.com/in/person"}]</script>'''
    return url,"text/html; charset=utf-8",html.encode()


class InstitutionalTest(unittest.TestCase):
    def test_worker_finishes_inaccessible_site_as_partial_without_commercial_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"db";cid,jobs=create_campaign(db,Campaign.from_dict(CONFIG))
            add_company(db,jobs[0]);approve_campaign(db,cid)
            claimed=claim_next_maps_job(db)
            finish_job(db,claimed["job_id"],claimed["lease_owner"],"completed")
            def fail(url):
                raise OSError("website unavailable")
            def website_job(path):
                return run_next_website_job(path,enricher=lambda p,**kw:enrich_websites(p,fetcher=fail,resolver=resolver,**kw))
            with patch("google_maps_scraper_kit.worker.run_next_website_job",side_effect=website_job):
                result=run_worker_once(db)
            self.assertEqual(result["state"],"partial")
            self.assertEqual([job["source"] for job in list_jobs(db)],["maps","website"])
            self.assertEqual(get_campaign(db,cid)["status"],"partial")
            self.assertTrue(all(job["state"] not in {"queued","running","waiting"} for job in list_jobs(db)))

    def test_rendered_contacts_reach_persistent_evidence_and_csv(self):
        from google_maps_scraper_kit.rendering import render
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"db";cid,jobs=create_campaign(db,Campaign.from_dict(CONFIG))
            add_company(db,jobs[0],website="https://example.com")
            def fetch(url):
                if url.endswith("robots.txt"):return url,"text/plain",b""
                return url,"text/html",b'''<html><body><script>document.body.innerHTML='<a href="mailto:js@empresa.test">Email</a><a href="https://linkedin.com/company/empresa">Rede</a>'</script></body></html>'''
            result=enrich_websites(db,fetcher=fetch,resolver=resolver,renderer=render)
            self.assertEqual(result["failed"],0)
            row=list_campaign_results(db,cid)[0]
            self.assertEqual(row["emails"],"js@empresa.test")
            self.assertEqual(row["linkedin"],"https://linkedin.com/company/empresa")
            self.assertTrue(any(e["source_provider"]=="website_playwright" for e in row["evidence"]))
            output=Path(d)/"out.csv";export_companies(db,output,campaign_id=cid)
            self.assertIn("js@empresa.test",output.read_text(encoding="utf-8-sig"))

    def test_refuses_commercial_scope_and_never_claims_legacy_jobs(self):
        for change in ({"mode":"people"},{"mode":"companies_and_people"},{"decision_maker_roles":["CEO"]},
                       {"required_fields":["decision_maker"]}):
            with self.assertRaises(ValueError):
                Campaign.from_dict({**CONFIG,**change})
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"db"
            cid,jobs=create_campaign(db,Campaign.from_dict(CONFIG))
            with closing(connect(db)) as c, c:
                config=json.loads(c.execute("select config_json from campaigns").fetchone()[0])
                config.pop("schema_version")
                c.execute("update campaigns set config_json=?, approved_at='old'",(json.dumps(config),))
            self.assertTrue(get_campaign(db,cid)["read_only"])
            self.assertIsNone(claim_next_maps_job(db))
            self.assertEqual(run_worker_once(db),{"state":"idle"})
            with self.assertRaisesRegex(ValueError,"Legacy"):
                approve_campaign(db,cid)
            self.assertEqual(len(list_jobs(db)),1)

    def test_institutional_contacts_partial_pages_no_people_and_unique_csv(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"db";cid,jobs=create_campaign(db,Campaign.from_dict(CONFIG))
            add_company(db,jobs[0])
            stats=enrich_websites(db,fetcher=site_fetcher,resolver=resolver)
            self.assertEqual(stats["domains"],1)
            self.assertEqual(stats["failed"],1)
            self.assertEqual(stats["pages"],2)
            with closing(connect(db)) as c, c:
                self.assertEqual(c.execute("select count(*) from people").fetchone()[0],0)
                self.assertEqual(c.execute("select count(*) from evidence where entity_type='person'").fetchone()[0],0)
            qualify_campaign(db,cid)
            row=list_campaign_results(db,cid)[0]
            self.assertEqual(row["emails"],"contato@empresa.test")
            self.assertIn("2222223333",row["phones"])
            self.assertIn("+5522999998888",row["phones"])
            self.assertEqual(row["linkedin"],"https://www.linkedin.com/company/empresa")
            self.assertEqual(row["website_scan_status"],"partial")
            self.assertIn("page unavailable",row["website_scan_error"])
            self.assertIn("youtube.com/@empresa",row["youtube"])
            self.assertNotIn("decision_makers",row)
            output=Path(d)/"export.csv"
            self.assertEqual(export_companies(db,output,campaign_id=cid),1)
            with output.open(encoding="utf-8-sig",newline="") as file:
                rows=list(csv.DictReader(file))
            self.assertEqual(len(rows),1)
            self.assertTrue(all(rows[0][key]=="" for key in DEPRECATED_FIELDS))
            self.assertIn("contato@empresa.test",rows[0]["emails"])
            self.assertTrue(json.loads(rows[0]["evidence_json"]))
            with closing(connect(db)) as c, c:
                _add_evidence(c,"company",row["company_id"],"linkedin","https://empresa.test","https://linkedin.com/in/legacy-person","old")
            export_companies(db,output,campaign_id=cid)
            self.assertNotIn("legacy-person",output.read_text(encoding="utf-8-sig"))

    def test_optin_cache_reuses_exact_site_without_losing_new_company_contacts(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"db";cid,jobs=create_campaign(db,Campaign.from_dict(CONFIG))
            add_company(db,jobs[0])
            def fetch(url):
                return url,"text/plain" if url.endswith("robots.txt") else "text/html",b'<a href="mailto:contato@empresa.test">Email</a><a href="https://instagram.com/empresa">Social</a>'
            first=enrich_websites(db,fetcher=fetch,resolver=resolver,collect_emails=False,collect_socials=True)
            self.assertEqual(list_campaign_results(db,cid)[0]["emails"],"")
            second=enrich_websites(db,fetcher=fetch,resolver=resolver)
            self.assertEqual(second["domains"],1)
            add_company(db,jobs[0],fingerprint="b",title="Outra unidade")
            third=enrich_websites(db,fetcher=lambda u:self.fail("cache should avoid requests"),resolver=resolver)
            self.assertEqual(third["skipped"],2)
            self.assertTrue(all(row["emails"]=="contato@empresa.test" for row in list_campaign_results(db,cid)))

    def test_attempt_limit_and_private_connection_pin(self):
        requested=[]
        def fetch(url):
            requested.append(url)
            if url.endswith("robots.txt"):return url,"text/plain",b""
            if url.endswith(".test/"):return url,"text/html",b"".join(f'<a href="/contato{i}">Contato</a>'.encode() for i in range(20))
            raise OSError("inaccessible")
        pages,errors=_crawl("https://empresa.test",fetch,resolver)
        self.assertEqual(len(requested),5)  # robots + at most four page attempts
        self.assertEqual(len(pages),1)
        self.assertEqual(len(errors),3)
        with patch("socket.getaddrinfo",return_value=resolver()),patch("socket.create_connection") as connection:
            _connect_public("empresa.test",443,10)
            connection.assert_called_once_with(("93.184.216.34",443),10)
        private=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))]
        with patch("socket.getaddrinfo",return_value=private),patch("socket.create_connection") as connection:
            with self.assertRaisesRegex(ValueError,"private"):
                _connect_public("empresa.test",443,10)
            connection.assert_not_called()
        self.assertEqual(_contact("https://linkedin.com/in/person"),(None,""))
        self.assertEqual(_contact("https://linkedin.com.evil.test/company/a"),(None,""))
