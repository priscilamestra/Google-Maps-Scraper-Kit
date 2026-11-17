"""Pass explicit viewports to the installed Kit's Gosom client, without changing the vendor."""
import argparse
import csv
import importlib.util
import json
import os
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("latitude", type=float)
    parser.add_argument("longitude", type=float)
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--kit-script", type=Path, required=True)
    parser.add_argument("--radius", type=float, required=True)
    parser.add_argument("--zoom", type=int, default=15)
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--lang", default="pt")
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("official_maps_kit", args.kit_script)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    progress = {}
    if hasattr(kit, "req"):
        request = kit.req
        def tracked_request(method, path, body=None):
            response = request(method, path, body)
            if method == "POST" and path == "/api/v1/jobs":
                progress.update(engine_job_id=json.loads(response[1]).get("id"), stage="pending")
            elif method == "GET" and path.endswith("/download"):
                import io
                progress["raw_results"] = sum(1 for _ in csv.DictReader(io.StringIO(response[1].decode("utf-8", "replace"))))
            elif method == "GET" and path.startswith("/api/v1/jobs/"):
                progress["stage"] = json.loads(response[1]).get("Status")
            else:
                return response
            progress_path = args.out.with_suffix(".progress.json")
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = progress_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(progress), encoding="utf-8")
            os.replace(temporary, progress_path)
            return response
        kit.req = tracked_request
    body = {"name": args.query[:80], "keywords": [args.query, *args.keyword], "lat": str(args.latitude), "lon": str(args.longitude),
            "radius": int(round(args.radius)), "zoom": args.zoom, "depth": args.depth, "lang": args.lang,
            "fast_mode": False, "email": False, "max_time": 600}
    # Optional proxies stay in the local environment, never in campaign history or exports.
    if os.getenv("SCRAPER_PROXIES"):
        body["proxies"] = json.loads(os.environ["SCRAPER_PROXIES"])
    job_id, rows = kit.run_job(body)
    rows = kit.dedupe(rows)
    for row in rows:
        row["website"] = kit.clean_website(row.get("website", ""))
    # Keep the complete response on disk: saturation and cross-tile dedup need the raw count.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8-sig", dir=args.out.parent, delete=False) as stream:
            temporary = Path(stream.name)
            fields = list(dict.fromkeys(key for row in rows for key in row)) or kit.LEAD
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.out)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    print(f"engine job {job_id}: {len(rows)} raw places")


if __name__ == "__main__":
    main()
