"""Coordinates from the source and circular filters; no inferred business locations."""
import math
from urllib.parse import urlsplit


def search_area(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("search_area must be an object")
    result = {}
    for key, lower, upper in (("latitude", -90, 90), ("longitude", -180, 180), ("radius_m", 1, 10000)):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not lower <= number <= upper:
            raise ValueError(f"search_area.{key} must be between {lower} and {upper}")
        result[key] = float(number)
    return result


def coordinates(raw):
    try:
        lat, lon = raw.get("latitude"), raw.get("longitude")
        if isinstance(lat, bool) or isinstance(lon, bool):
            return None, None
        lat, lon = float(lat), float(lon)
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError):
        pass
    return None, None


def distance_m(lat, lon, area):
    if lat is None or lon is None:
        return None
    a, b = math.radians(lat), math.radians(area["latitude"])
    dlat = a - b
    dlon = math.radians(lon - area["longitude"])
    h = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def maps_url(raw):
    value = raw.get("link") or raw.get("maps_url") or ""
    if isinstance(value, str):
        try:
            parsed = urlsplit(value)
            if parsed.scheme == "https" and parsed.hostname in {"google.com", "www.google.com", "maps.google.com", "maps.app.goo.gl"}:
                return value
        except ValueError:
            pass
    return None


def area_tiles(area, size):
    """Overlapping viewports covering the requested circle; no invented neighbourhood names."""
    latitude, longitude, radius = area["latitude"], area["longitude"], area["radius_m"]
    cosine = max(0.01, math.cos(math.radians(latitude)))
    step = 2 * radius / size
    tiles = []
    for row in range(size):
        for column in range(size):
            north = -radius + (row + 0.5) * step
            east = -radius + (column + 0.5) * step
            if math.hypot(max(0, abs(north)-step/2), max(0, abs(east)-step/2)) > radius:
                continue
            lat = max(-89.99, min(89.99, latitude + north / 111195))
            lon = (longitude + east / (111195*cosine) + 180) % 360 - 180
            zoom = round(math.log2(156543.03 * cosine * 1000 / max(1, step)))
            tiles.append({"latitude": lat, "longitude": lon,
                          "radius_m": max(50, math.ceil(step * 0.8)), "zoom": max(11, min(19, zoom))})
    return tiles
