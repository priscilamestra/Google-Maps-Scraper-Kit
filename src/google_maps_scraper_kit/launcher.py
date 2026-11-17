from pathlib import Path
import sys

from google_maps_scraper_kit.cli import main


def run() -> int:
    project_root = Path(__file__).resolve().parents[2]

    sys.argv = [
        "gmaps",
        "--db",
        str(project_root / "data" / "google-maps-scraper-kit.db"),
        "serve-api",
        "--host",
        "127.0.0.1",
        "--port",
        "8090",
    ]

    return main()
