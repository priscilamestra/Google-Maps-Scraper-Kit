"""Bounded local JS rendering; every HTTP request uses the protected Python fetcher."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from urllib.parse import urlsplit

MAX_REQUESTS = 20
MAX_RENDER_SECONDS = 20


def render(url: str, html: str) -> str:
    from .website import MAX_BYTES, validate_public_url
    validate_public_url(url)
    if len(html.encode("utf-8")) > MAX_BYTES:
        raise ValueError("render input exceeds size limit")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen([sys.executable, "-m", "google_maps_scraper_kit.rendering"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **options)
    try:
        output, error = process.communicate(json.dumps({"url": url, "html": html}), timeout=MAX_RENDER_SECONDS)
    except subprocess.TimeoutExpired:
        # Kill only this owned process tree, including its browser/driver children.
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        process.communicate()
        raise RuntimeError("local JavaScript rendering exceeded 20 seconds") from None
    if process.returncode:
        raise RuntimeError("local JavaScript rendering failed: " + error.strip()[-350:])
    result = json.loads(output)
    if not isinstance(result, str) or len(result.encode("utf-8")) > MAX_BYTES:
        raise ValueError("render output exceeds size limit")
    return result


def _render_document(url, html, *, fetcher=None):
    from playwright.sync_api import sync_playwright
    from .website import MAX_BYTES, _domain, _fetch, _robots, validate_public_url, USER_AGENT
    validate_public_url(url)
    fetch = fetcher or _fetch
    domain = _domain(urlsplit(url).hostname)
    count = 0
    rules = None
    with sync_playwright() as playwright:
        channel = os.getenv("GOOGLE_MAPS_SCRAPER_KIT_BROWSER_CHANNEL", "msedge" if os.name == "nt" else "")
        browser = playwright.chromium.launch(headless=True, channel=channel or None,
                                            args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
        context = browser.new_context(service_workers="block", accept_downloads=False, user_agent=USER_AGENT)
        context.route_web_socket("**/*", lambda socket: socket.close())
        context.add_init_script("window.RTCPeerConnection = undefined; window.webkitRTCPeerConnection = undefined;")

        def route_request(route):
            nonlocal count, rules
            request = route.request
            count += 1
            parsed = urlsplit(request.url)
            if (count > MAX_REQUESTS or request.method != "GET" or parsed.scheme not in {"http", "https"}
                    or _domain(parsed.hostname or "") != domain
                    or request.resource_type in {"image", "media", "font", "stylesheet"}):
                route.abort()
                return
            try:
                if count == 1 and request.url.rstrip("/") == url.rstrip("/"):
                    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
                    return
                if rules is None:
                    rules = _robots(url, fetch)
                if not rules.can_fetch(USER_AGENT, request.url):
                    route.abort()
                    return
                final_url, content_type, body = fetch(request.url)
                if _domain(urlsplit(final_url).hostname or "") != domain or not rules.can_fetch(USER_AGENT, final_url):
                    route.abort()
                    return
                route.fulfill(status=200, content_type=content_type, body=body)
            except (OSError, ValueError, RuntimeError):
                route.abort()

        context.route("**/*", route_request)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=12000)
        page.wait_for_timeout(1200)
        content = page.content()
        if len(content.encode("utf-8")) > MAX_BYTES:
            raise ValueError("render output exceeds size limit")
        context.close()
        browser.close()
        return content


if __name__ == "__main__":
    request = json.load(sys.stdin)
    print(json.dumps(_render_document(request["url"], request["html"]), ensure_ascii=False))
