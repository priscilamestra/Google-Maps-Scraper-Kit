"""Manual UI smoke: uv run --with playwright python tests/browser_smoke.py.

Uses a temporary SQLite, no worker and synthetic companies; never starts scraping.
"""
import json
import os
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright
from google_maps_scraper_kit.api import create_server
from google_maps_scraper_kit.db import create_campaign, upsert_maps_results, list_campaign_results, get_campaign
from google_maps_scraper_kit.models import Campaign
from google_maps_scraper_kit.normalization import normalize_maps_results
from google_maps_scraper_kit.qualification import qualify_campaign


def run():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "test.db"
        area = {"latitude": -22.2887, "longitude": -42.5341, "radius_m": 3000}
        config = {"name": "Campanha sintética", "mode": "companies", "target_count": 2,
                  "business_terms": ["Empresa de teste"], "location": {"city": "Nova Friburgo, RJ"}, "search_area": area}
        campaign_id, jobs = create_campaign(database, Campaign.from_dict(config))
        row = {"fingerprint": "synthetic", "title": "Empresa sintética", "phone": "", "emails": "", "website": "",
               "category": "Teste", "address": "Dados de teste", "review_rating": "", "review_count": "", "source_url": "",
               "raw_json": json.dumps({"latitude": area["latitude"], "longitude": area["longitude"], "place_id": "synthetic"})}
        upsert_maps_results(database, jobs[0], [row])
        normalize_maps_results(database, jobs[0])
        qualify_campaign(database, campaign_id)
        server = create_server(database, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, channel="msedge")
                page = browser.new_page(viewport={"width": 1440, "height": 950})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.wait_for_function("typeof L !== 'undefined' && document.querySelector('.leaflet-tile-loaded')", timeout=30000)
                assert page.evaluate("document.querySelector('#location').compareDocumentPosition(document.querySelector('#neighborhoods')) & Node.DOCUMENT_POSITION_FOLLOWING")
                assert page.evaluate("document.querySelector('#neighborhoods').compareDocumentPosition(document.querySelector('#radius')) & Node.DOCUMENT_POSITION_FOLLOWING")
                assert not page.locator("#neighborhoods").get_attribute("required")
                page.wait_for_function("document.querySelector('#runtime').textContent.includes('Scraper')")
                page.get_by_role("button", name="Abrir Campanha sintética", exact=True).click()
                page.locator("#results tr").first.wait_for()
                assert "Empresa sintética" in page.locator("#results").inner_text()
                assert page.evaluate("markers.getLayers().length") == 1
                page.locator("#filter").fill("nome ausente nesta campanha")
                assert page.locator("#results tr").count() == 0
                page.locator("#filter").fill("")
                assert page.locator("#results tr").count() == 1
                page.locator("#email-only").check()
                assert page.locator("#results tr").count() == 0
                page.locator("#email-only").uncheck()
                assert page.locator("#results tr").count() == 1
                assert page.evaluate("document.querySelector('#results-panel').getBoundingClientRect().top > document.querySelector('#new-search').getBoundingClientRect().bottom")
                assert page.locator("#results td").nth(0).bounding_box()["width"] >= 180
                capture_folder = Path(os.environ.get("TEMP", directory))
                page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
                page.screenshot(path=str(capture_folder / "leads-panel-review.png"), full_page=True)
                page.locator("#manual-review summary").click()
                page.get_by_label("Selecionar resultados visíveis").check()
                page.get_by_role("button", name="Aprovar selecionados").click()
                page.wait_for_function("document.querySelector('#notice').textContent === 'Revisão salva.'")
                assert list_campaign_results(database, campaign_id)[0]["review_status"] == "approved"
                page.locator("#results .company-focus").click()
                assert "Empresa sintética" in page.locator(".leaflet-popup-content").inner_text()
                popup_box=page.locator(".leaflet-popup-content").bounding_box()
                assert popup_box and popup_box["x"] >= 310 and popup_box["y"] >= 0
                assert popup_box["x"]+popup_box["width"] <= 1440
                assert page.locator("#map-leads").is_visible()
                assert page.get_by_role("link", name="Rotas", exact=True).first.get_attribute("href").startswith("https://www.google.com/maps/dir/?")
                page.wait_for_timeout(300)
                page.screenshot(path=str(capture_folder / "leads-map-review.png"))
                page.get_by_role("button", name="Painel", exact=True).click()
                page.locator("#map-dock .leaflet-container").wait_for()
                page.get_by_role("button", name="Mapa", exact=True).click()
                page.locator("#map-controls-slot").wait_for()
                page.locator("#radius").fill("1500")
                page.locator("#radius").dispatch_event("input")
                assert page.evaluate("circle.getRadius()") == 1500
                page.locator("#terms").fill("Busca sintética pela interface")
                page.get_by_text("Opções avançadas", exact=True).click()
                page.get_by_text("Cobertura da busca", exact=True).click()
                page.locator("#collection-limit").fill("4")
                page.locator("#neighborhoods").fill("Bairro A, Bairro B")
                page.locator("#emails").check()
                page.locator("#depth").fill("8")
                page.locator("#grid-size").select_option("1")
                page.locator("#max-queries").fill("20")
                page.get_by_text("Filtros adicionais e e-mails", exact=True).click()
                page.locator("#verify-emails").check()
                assert page.locator("#save").count() == 0
                page.get_by_role("button", name="Iniciar busca", exact=True).click()
                page.wait_for_function("document.querySelector('#notice').textContent.includes('Worker desligado')")
                saved_config = {"name": "Busca sintética pela interface", "mode": "companies", "target_count": 2,
                                "business_terms": ["Busca sintética pela interface"], "location": {"city": "Nova Friburgo, RJ"},
                                "search_area": {**area, "radius_m": 1500}, "collect_emails": True,
                                "neighborhoods": ["Bairro A", "Bairro B"], "depth": 8, "grid_size": 1,
                                "verify_emails": True, "max_queries": 20, "collection_limit": 4}
                saved_id, _ = create_campaign(database, Campaign.from_dict(saved_config))
                page.evaluate("id=>load(id,true)", saved_id)
                saved = get_campaign(database, saved_id)
                assert saved["status"] == "pending_approval"
                assert saved["config"]["search_area"]["radius_m"] == 1500
                assert saved["config"]["collect_emails"] is True
                assert saved["config"]["depth"] == 8
                assert saved["config"]["grid_size"] == 1
                assert saved["config"]["verify_emails"] is True
                assert saved["config"]["max_queries"] == 20
                assert saved["config"]["collection_limit"] == 4
                assert saved["config"]["neighborhoods"] == ["Bairro A", "Bairro B"]
                assert len(saved["jobs"]) == 3
                page.get_by_role("button", name="Painel", exact=True).click()
                page.get_by_role("button", name="Iniciar pedido salvo").click()
                page.wait_for_function("document.querySelector('#notice').textContent.includes('Worker desligado')")
                assert get_campaign(database, saved_id)["status"] == "pending_approval"
                assert page.get_by_text("Detalhes da execução", exact=True).count() == 0
                page.once("dialog", lambda dialog: dialog.accept())
                page.get_by_role("button", name="Cancelar campanha", exact=True).click()
                page.wait_for_function("document.querySelector('#campaign-status').textContent.startsWith('Cancelada')")
                assert all(job["state"] == "cancelled" for job in get_campaign(database, saved_id)["jobs"])
                for language in ("pt", "en", "es"):
                    language_config = {**config, "name": "Idioma "+language, "business_terms": ["Idioma "+language],
                                       "language": language, "neighborhoods": []}
                    language_id, _ = create_campaign(database, Campaign.from_dict(language_config))
                    page.evaluate("refresh()")
                    page.get_by_role("button", name="Abrir Idioma "+language, exact=True).click()
                    page.wait_for_function("id => active === id", arg=language_id)
                    saved_language=get_campaign(database, language_id)
                    assert saved_language["config"]["language"] == language
                    assert saved_language["config"]["neighborhoods"] == []
                    page.reload()
                    page.get_by_role("button", name="Abrir Idioma "+language, exact=True).click()
                    page.wait_for_function("id => active === id", arg=language_id)
                    assert page.locator("#language").input_value() == language
                    assert page.locator("#neighborhoods").input_value() == ""
                    page.get_by_role("button", name="Mapa", exact=True).click()
                    assert page.locator("#language").is_visible()
                    assert page.locator("#language").input_value() == language
                    page.get_by_role("button", name="Painel", exact=True).click()
                print("PASS: pt/en/es submitted and restored after reload; neighborhood optional in both layouts.")
                page.get_by_role("button", name="Mapa", exact=True).click()
                screenshot = Path(os.environ.get("TEMP", directory)) / "google-maps-scraper-kit-map-smoke.png"
                page.screenshot(path=str(screenshot))
                assert not errors, errors
                print(f"PASS: real OSM tiles, marker, popup, radius, review, saved campaign, execution guard. Screenshot: {screenshot}")
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(200)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.get_by_role("button", name="Painel", exact=True).click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("#new-search").is_visible()
                assert page.locator("#history-card").is_visible()
                assert page.locator("#results-panel").is_visible()
                print("PASS: mobile layout fits viewport; zero browser errors.")
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    run()
