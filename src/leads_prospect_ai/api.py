from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .db import (
    approve_campaign,
    cancel_campaign,
    create_campaign,
    get_campaign,
    list_campaign_results,
    list_campaigns,
    list_source_health,
    list_source_usage,
    pause_job,
    resume_job,
    update_person_review,
    update_result_review,
)
from .export import export_companies, export_people
from .models import Campaign
from .qualification import qualify_campaign
from .worker import run_worker_forever


MAX_BODY_BYTES = 1_000_000


def create_server(
    db_path: Path | str,
    host: str = "127.0.0.1",
    port: int = 8090,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("API must remain bound to localhost")
    database = Path(db_path)

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:
            self._send_bytes(HTTPStatus.NO_CONTENT, b"")

        def do_GET(self) -> None:
            try:
                parts = self._parts()
                if parts == ["api", "v1", "health"]:
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
                    self._export(parts[3])
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            except ValueError as error:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(error)})

        def do_POST(self) -> None:
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

        def do_PATCH(self) -> None:
            try:
                parts = self._parts()
                body = self._body()
                status = str(body.get("review_status") or "")
                if len(parts) == 6 and parts[:3] == ["api", "v1", "campaigns"] and parts[4] == "results":
                    update_result_review(database, parts[3], parts[5], status)
                    self._send_json(HTTPStatus.OK, {"company_id": parts[5], "review_status": status})
                elif len(parts) == 4 and parts[:3] == ["api", "v1", "people"]:
                    update_person_review(database, parts[3], status)
                    self._send_json(HTTPStatus.OK, {"person_id": parts[3], "review_status": status})
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
            output = Path.cwd() / "exports" / f"campaign-{campaign_id}.csv"
            if campaign["mode"] == "people":
                count = export_people(database, output, campaign_id)
            else:
                qualify_campaign(database, campaign_id)
                count = export_companies(database, output, campaign_id=campaign_id)
            data = output.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._headers("text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{output.name}"')
            self.send_header("X-Result-Count", str(count))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
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
    if with_worker:
        threading.Thread(
            target=run_worker_forever,
            args=(db_path,),
            kwargs={"stop_event": stop},
            daemon=True,
        ).start()
    server = create_server(db_path, host, port)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
