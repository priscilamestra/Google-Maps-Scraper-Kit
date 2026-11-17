"""Manual history UI acceptance, temporary SQLite and synthetic data, no scraping."""
import tempfile
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright
from google_maps_scraper_kit.api import create_server
from test_history import campaign, import_company, finish


def run():
    with tempfile.TemporaryDirectory() as directory:
        db=Path(directory)/"data"/"db"
        first,job=campaign(db,"Lista antiga — sintética")
        import_company(db,job,"Empresa antes","2222222222",-22.28);finish(db,first,job)
        second,job=campaign(db,"Lista nova — sintética")
        import_company(db,job,"Empresa depois","22999999999",-22.29);finish(db,second,job)
        server=create_server(db,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,channel="msedge")
                page=browser.new_page(viewport={"width":1440,"height":1000})
                errors=[];page.on("pageerror",lambda error:errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.wait_for_function("typeof L!=='undefined' && document.querySelector('.leaflet-tile-loaded')",timeout=45000)
                for cid,label,name,latitude in [(first,"Lista antiga — sintética","Empresa antes",-22.28),
                                                 (second,"Lista nova — sintética","Empresa depois",-22.29),
                                                 (first,"Lista antiga — sintética","Empresa antes",-22.28)]:
                    page.get_by_role("button",name=label,exact=False).click()
                    page.wait_for_function("id=>active===id && results.length===1",arg=cid)
                    assert name in page.locator("#results").inner_text()
                    assert page.evaluate("markers.getLayers()[0].getLatLng().lat")==latitude
                    with page.expect_download() as downloaded:
                        page.locator("#export").click()
                    assert downloaded.value.suggested_filename==f"campaign-{cid}.csv"
                page.reload()
                page.get_by_role("button",name="Lista antiga — sintética",exact=False).click()
                page.wait_for_function("id=>active===id && results.length===1",arg=first)
                assert "Empresa antes" in page.locator("#results").inner_text()
                assert not errors,errors
                browser.close()
                print("PASS: independent old/new lists, their own map markers and CSVs, reload, no worker or scraping.")
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=="__main__":
    run()
