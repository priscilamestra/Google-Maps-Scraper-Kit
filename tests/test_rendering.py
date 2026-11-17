import unittest
from unittest.mock import patch
from google_maps_scraper_kit.rendering import render, _render_document


class RenderingTest(unittest.TestCase):
    def test_real_local_javascript_and_private_requests_blocked(self):
        html='''<html><body><div id="app"></div><script>
          document.getElementById('app').innerHTML='<a href="mailto:js@empresa.test">Contato JS</a><a href="https://linkedin.com/company/empresa">LinkedIn</a>';
          fetch('http://127.0.0.1:8090/api/v1/campaigns').catch(()=>{});
          fetch('http://169.254.169.254/latest/meta-data/').catch(()=>{});
        </script></body></html>'''
        requests=[]
        result=_render_document("https://example.com",html,fetcher=lambda u:requests.append(u))
        self.assertIn('href="mailto:js@empresa.test"',result)
        self.assertEqual(requests,[])

    def test_parent_wrapper_and_hard_timeout(self):
        result=render("https://example.com",'<html><body><script>document.body.append("JS_OK")</script></body></html>')
        self.assertIn("JS_OK",result)
        with patch("google_maps_scraper_kit.rendering.MAX_RENDER_SECONDS",2):
            with self.assertRaisesRegex(RuntimeError,"exceeded"):
                render("https://example.com",'<html><script>while(true){}</script></html>')
