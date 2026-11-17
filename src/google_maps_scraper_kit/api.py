from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from urllib.request import urlopen
from urllib.error import URLError

from .db import (
    approve_campaign,
    cancel_campaign,
    create_campaign,
    delete_campaign,
    freeze_completed_campaigns,
    get_campaign,
    list_campaign_results,
    list_campaigns,
    list_source_health,
    list_source_usage,
    pause_job,
    resume_job,
    update_result_review,
)
from .export import export_companies, export_xlsx
from .models import Campaign
from .qualification import qualify_campaign
from .worker import run_worker_forever
from .geocoding import locate


MAX_BODY_BYTES = 1_000_000


def create_server(
    db_path: Path | str,
    host: str = "127.0.0.1",
    port: int = 8090,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("API must remain bound to localhost")
    database = Path(db_path)
    freeze_completed_campaigns(database)

    class Handler(BaseHTTPRequestHandler):
        def handle(self) -> None:
            try:
                super().handle()
            except (ConnectionError, TimeoutError):
                # Reloading or closing the page cancels outstanding HTTP responses.
                self.close_connection = True

        def _local_request(self):
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            expected = f"http://{host}"
            if urlsplit(expected).hostname not in {"localhost", "127.0.0.1", "::1"} or (origin and origin != expected):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Use a aplicação na mesma origem local da API"})
                return False
            return True

        def do_OPTIONS(self) -> None:
            self._send_bytes(HTTPStatus.NO_CONTENT, b"")

        def do_GET(self) -> None:
            if not self._local_request():
                return
            try:
                parts = self._parts()
                assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8")}
                asset = assets.get(urlsplit(self.path).path)
                if asset:
                    self._send_bytes(HTTPStatus.OK, (Path(__file__).parent / "static" / asset[0]).read_bytes(), asset[1])
                elif parts == ["api", "v1", "runtime"]:
                    try:
                        with urlopen("http://127.0.0.1:8081/api/v1/jobs", timeout=2) as response:
                            available = response.status == 200
                    except (OSError, URLError):
                        available = False
                    self._send_json(HTTPStatus.OK, {"maps_available": available, "worker_enabled": getattr(self.server, "worker_enabled", False)})
                elif parts == ["api", "v1", "geocode"]:
                    query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
                    self._send_json(HTTPStatus.OK, locate(database, query))
                elif parts == ["api", "v1", "health"]:
                    self._send_json(HTTPStatus.OK, {"status": "ok"})
                elif parts == ["api", "v1", "campaigns"]:
                    self._send_json(HTTPStatus.OK, {"campaigns": list_campaigns(database)})
                elif parts == ["api", "v1", "usage"]:
                    self._send_json(HTTPStatus.OK, {"usage": list_source_usage(database)})
                elif parts == ["api", "v1", "sources", "health"]:
                    self._send_json(HTTPStatus.OK, {"sources": list_source_health(database)})
                elif len(parts) == 4 and parts[:3] == ["api", "v1", "campaigns"]:
                    self._send_json(HTTPStatus.OK, get_campaign(database, parts[3]))
                elif len(parts) == 5 and parts[:3] == ["api", "v1", "campaigns"] and parts[4] == "results":
                    self._send_json(HTTPStatus.OK, {"results": list_campaign_results(database, parts[3])})
                elif len(parts) == 5 and parts[:3] == ["api", "v1", "campaigns"] and parts[4] == "export":
                    export_format = parse_qs(urlsplit(self.path).query).get("format", ["csv"])[0]
                    if export_format == "json":
                        self._export_json(parts[3])
                    elif export_format == "xlsx":
                        self._export_xlsx(parts[3])
                    elif export_format == "csv":
                        self._export(parts[3])
                    else:
                        raise ValueError("format must be csv, xlsx or json")
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            except ValueError as error:
                status = HTTPStatus.BAD_REQUEST if str(error).startswith("format must") else HTTPStatus.NOT_FOUND
                self._send_json(status, {"error": str(error)})
            except (OSError, URLError) as error:
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": f"Serviço indisponível: {error}"})

        def do_POST(self) -> None:
            if not self._local_request():
                return
            try:
                parts = self._parts()
                if parts == ["api", "v1", "campaigns"]:
                    campaign = Campaign.from_dict(self._body())
                    campaign_id, job_ids = create_campaign(database, campaign)
                    self._send_json(
                        HTTPStatus.CREATED,
                        {"campaign_id": campaign_id, "job_ids": job_ids, "status": "pending_approval" if campaign.manual_approval_required else "queued"},
                    )
                elif len(parts) == 5 and parts[:3] == ["api", "v1", "campaigns"] and parts[4] == "approve":
                    approve_campaign(database, parts[3])
                    self._send_json(HTTPStatus.OK, get_campaign(database, parts[3]))
                elif len(parts) == 5 and parts[:3] == ["api", "v1", "campaigns"] and parts[4] == "cancel":
                    cancel_campaign(database, parts[3])
                    self._send_json(HTTPStatus.OK, get_campaign(database, parts[3]))
                elif len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"] and parts[4] == "pause":
                    pause_job(database, parts[3])
                    self._send_json(HTTPStatus.OK, {"job_id": parts[3], "status": "paused"})
                elif len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"] and parts[4] == "resume":
                    resume_job(database, parts[3])
                    self._send_json(HTTPStatus.OK, {"job_id": parts[3], "status": "queued"})
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            except (json.JSONDecodeError, ValueError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def do_DELETE(self) -> None:
            if not self._local_request():
                return
            try:
                parts = self._parts()
                if len(parts) == 4 and parts[:3] == ["api", "v1", "campaigns"]:
                    delete_campaign(database, parts[3])
                    exports = database.resolve().parent / "exports"
                    for suffix in ("csv", "xlsx", "json"):
                        (exports / f"campaign-{parts[3]}.{suffix}").unlink(missing_ok=True)
                    self._send_bytes(HTTPStatus.NO_CONTENT, b"")
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            except RuntimeError as error:
                self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
            except ValueError as error:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(error)})

        def do_PATCH(self) -> None:
            if not self._local_request():
                return
            try:
                parts = self._parts()
                body = self._body()
                status = str(body.get("review_status") or "")
                if len(parts) == 6 and parts[:3] == ["api", "v1", "campaigns"] and parts[4] == "results":
                    update_result_review(database, parts[3], parts[5], status)
                    self._send_json(HTTPStatus.OK, {"company_id": parts[5], "review_status": status})
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            except (json.JSONDecodeError, ValueError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def _parts(self) -> list[str]:
            return [part for part in urlsplit(self.path).path.split("/") if part]

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY_BYTES:
                raise ValueError("request body must contain at most 1 MB of JSON")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("request body must be a JSON object")
            return value

        def _export(self, campaign_id: str) -> None:
            campaign = get_campaign(database, campaign_id)
            output = database.resolve().parent / "exports" / f"campaign-{campaign_id}.csv"
            if not campaign["read_only"]:
                qualify_campaign(database, campaign_id)
            count = export_companies(database, output, self._export_company_ids(), campaign_id)
            data = output.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._headers("text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{output.name}"')
            self.send_header("X-Result-Count", str(count))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _export_json(self, campaign_id: str) -> None:
            campaign = get_campaign(database, campaign_id)
            if not campaign["read_only"]:
                qualify_campaign(database, campaign_id)
            allowed = self._export_company_ids()
            results = list_campaign_results(database, campaign_id)
            if allowed is not None:
                results = [item for item in results if item["company_id"] in allowed]
            data = json.dumps({"campaign": get_campaign(database, campaign_id), "results": results}, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self._headers("application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="campaign-{campaign_id}.json"')
            self.send_header("X-Result-Count", str(len(results)))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _export_xlsx(self, campaign_id: str) -> None:
            campaign = get_campaign(database, campaign_id)
            output = database.resolve().parent / "exports" / f"campaign-{campaign_id}.xlsx"
            if not campaign["read_only"]:
                qualify_campaign(database, campaign_id)
            count = export_xlsx(database, output, self._export_company_ids(), campaign_id)
            data = output.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._headers("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{output.name}"')
            self.send_header("X-Result-Count", str(count))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _export_company_ids(self) -> set[str] | None:
            query = parse_qs(urlsplit(self.path).query)
            values = query.get("company_id", [])
            if not values:
                return None
            ids = {value for value in values if value}
            if not ids or len(ids) > 5000:
                raise ValueError("company_id must contain between 1 and 5000 values")
            return ids

        def _send_json(self, status: HTTPStatus, payload: object) -> None:
            self._send_bytes(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _send_bytes(self, status: HTTPStatus, data: bytes, content_type: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            self._headers(content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _headers(self, content_type: str) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
            self.send_header("Cache-Control", "no-store")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve_api(
    db_path: Path | str,
    host: str = "127.0.0.1",
    port: int = 8090,
    *,
    with_worker: bool = True,
) -> None:
    stop = threading.Event()
    server = create_server(db_path, host, port)
    server.worker_enabled = with_worker
    print(f"Google Maps Scraper Kit ativo em http://{host}:{port}", flush=True)
    print(f"Worker: {'ativo' if with_worker else 'desativado'} | Para desligar: Ctrl+C", flush=True)
    if with_worker:
        threading.Thread(
            target=run_worker_forever,
            args=(db_path,),
            kwargs={"stop_event": stop},
            daemon=True,
        ).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Google Maps Scraper Kit encerrado.", flush=True)
    finally:
        stop.set()
        server.server_close()
