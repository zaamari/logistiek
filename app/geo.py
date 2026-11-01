"""Adressen -> coördinaten (PDOK) en reistijden (OSRM), met terugval op een schatting."""
from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = DATA_DIR / "geocache.json"
PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
OSRM_URL = "https://router.project-osrm.org"
FALLBACK_KMH = 60
FALLBACK_DETOUR = 1.35

_lock = threading.Lock()


def _read_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(cache: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


PLACE_ALIASES = [
    (r"\bden haag\b", "'s-gravenhage"),
    (r"\bs-gravenhage\b", "'s-gravenhage"),
    (r"\ba/d\b", "aan den"),
    (r"\ba\.d\.\b", "aan den"),
]


def _normalize(address: str) -> str:
    text = address.lower()
    for pattern, replacement in PLACE_ALIASES:
        text = re.sub(pattern, replacement, text)
    return text.replace("''s", "'s")


def _matches(address: str, doc: dict) -> bool:
    """PDOK geeft altijd een 'beste gok'; controleer straat, postcode/plaats en huisnummer."""
    text = _normalize(address)
    postcode = re.search(r"\b(\d{4})\s?([a-z]{2})\b", text)
    if postcode:
        if (doc.get("postcode") or "").lower() != postcode.group(1) + postcode.group(2):
            return False
        text = text.replace(postcode.group(0), " ")
    else:
        places = {(doc.get(f) or "").lower() for f in ("woonplaatsnaam", "gemeentenaam")} - {""}
        city = text.rsplit(",", 1)[1].strip() if "," in text else ""
        if not any(re.search(rf"\b{re.escape(p)}\b", text) or (city and p.startswith(city)) for p in places):
            return False
    street = (doc.get("straatnaam") or "").lower()
    if not re.search(rf"(?<![\w-]){re.escape(street)}\b", text):
        return False
    number = re.search(r"\b(\d+)", text)
    return number is None or str(doc.get("huisnummer")) == number.group(1)


def _search(query: str, rows: int = 10) -> list[dict]:
    params = {"q": query, "rows": rows, "fq": "type:adres",
              "fl": "weergavenaam,centroide_ll,straatnaam,postcode,huisnummer,woonplaatsnaam,gemeentenaam"}
    return requests.get(PDOK_URL, params=params, timeout=10).json()["response"]["docs"]


def geocode(address: str) -> dict | None:
    """Geeft {lat, lon, label} terug, of None als het adres niet (zeker) gevonden is.

    Bij twijfel staat de beste gok in `guess`, zodat de gebruiker kan zien wat er bedoeld werd.
    """
    key = " ".join(address.lower().split())
    if not key:
        return None
    with _lock:
        cache = _read_cache()
    if key in cache:
        return cache[key]

    docs = _search(address)
    doc = next((d for d in docs if _matches(address, d)), None)
    parsed = re.match(r"\s*(.+?)\s+(\d+)[^,]*,\s*(?:\d{4}\s?[a-zA-Z]{2}\s*)?(.+?)\s*$", _normalize(address))
    if doc is None and parsed:
        # Gericht zoeken op straat + plaats; levert ook een bruikbare suggestie op.
        street, number, city = (g.replace('"', "") for g in parsed.groups())
        same_street = _search(f'straatnaam:"{street}" AND (woonplaatsnaam:"{city}" OR gemeentenaam:"{city}")'
                              f" AND huisnummer:[{max(0, int(number) - 20)} TO {int(number) + 20}]", rows=50)
        doc = next((d for d in same_street if _matches(address, d)), None)
        if doc is None and same_street:
            docs = sorted(same_street, key=lambda d: abs(d["huisnummer"] - int(number)))
    if doc is None:
        return {"guess": docs[0]["weergavenaam"]} if docs else None

    lon, lat = doc["centroide_ll"].removeprefix("POINT(").rstrip(")").split()
    result = {"lat": float(lat), "lon": float(lon), "label": doc["weergavenaam"]}
    with _lock:
        cache = _read_cache()
        cache[key] = result
        _write_cache(cache)
    return result


def _haversine_km(a: dict, b: dict) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def travel_matrix(points: list[dict]) -> tuple[list[list[int]], str]:
    """Reistijden in seconden tussen alle punten, plus de gebruikte bron."""
    coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
    try:
        data = requests.get(f"{OSRM_URL}/table/v1/driving/{coords}",
                            params={"annotations": "duration"}, timeout=20).json()
        if data.get("code") == "Ok":
            return [[int(round(d or 0)) for d in row] for row in data["durations"]], "osrm"
    except (requests.RequestException, ValueError):
        pass
    matrix = [[int(_haversine_km(a, b) * FALLBACK_DETOUR / FALLBACK_KMH * 3600) for b in points] for a in points]
    return matrix, "schatting"


def route_geometry(points: list[dict]) -> list[list[float]]:
    """Wegdek-lijn [[lat, lon], ...] voor een rit; rechte lijnen als de routedienst faalt."""
    straight = [[p["lat"], p["lon"]] for p in points]
    if len(points) < 2:
        return straight
    coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
    try:
        data = requests.get(f"{OSRM_URL}/route/v1/driving/{coords}",
                            params={"overview": "simplified", "geometries": "geojson"}, timeout=15).json()
        if data.get("code") == "Ok":
            return [[lat, lon] for lon, lat in data["routes"][0]["geometry"]["coordinates"]]
    except (requests.RequestException, ValueError):
        pass
    return straight
