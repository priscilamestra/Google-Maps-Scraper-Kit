"""Manual location lookup, cached and serialized per the public Nominatim policy."""
import json
import os
import threading
import time
from contextlib import closing
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .db import connect
from .geography import coordinates

_lock = threading.Lock()
_last_request = 0.0


def locate(database, query):
    global _last_request
    query = query.strip()
    if not 3 <= len(query) <= 200:
        raise ValueError("Informe uma localidade de 3 a 200 caracteres")
    with _lock, closing(connect(database)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS geocode_cache (query TEXT PRIMARY KEY, result_json TEXT NOT NULL)")
        cached = connection.execute("SELECT result_json FROM geocode_cache WHERE query = ?", (query.casefold(),)).fetchone()
        if cached:
            return json.loads(cached[0])
        time.sleep(max(0, 1.1 - (time.monotonic() - _last_request)))
        _last_request = time.monotonic()
        endpoint = os.getenv("GOOGLE_MAPS_SCRAPER_KIT_GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
        request = Request(endpoint + "?" + urlencode({"q": query, "format": "jsonv2", "limit": 1}),
                          headers={"User-Agent": "LeadsProspectAI/0.1 (local campaign location selector)", "Accept": "application/json"})
        with urlopen(request, timeout=12) as response:
            hits = json.loads(response.read(100000))
        if not hits:
            raise ValueError("Local não encontrado. Especifique cidade/estado ou selecione no mapa.")
        lat, lon = coordinates({"latitude": hits[0].get("lat"), "longitude": hits[0].get("lon")})
        if lat is None:
            raise ValueError("O serviço não retornou coordenadas válidas")
        result = {"latitude": lat, "longitude": lon, "label": hits[0].get("display_name", query)}
        connection.execute("INSERT OR REPLACE INTO geocode_cache VALUES (?, ?)", (query.casefold(), json.dumps(result)))
        return result
