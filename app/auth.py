"""Toegang voor de online planner: alleen beheerders van Houtenhak.

Lokaal (start.bat) staat dit uit en kan iedereen op de pc plannen. Online zet deploy.sh
SUPABASE_PROJECTS (zie deploy/env.yaml); dan moet elk API-verzoek het inlogtoken van een
beheerder meesturen (`Authorization: Bearer ...`). De planner controleert dat niet zelf maar
vraagt het aan Supabase (rpc is_admin): die keurt het token goed of af, net als in het beheer.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time

import requests
from fastapi import HTTPException, Request

# {"https://<project>.supabase.co": "<publishable key>"}: de Supabase-projecten waarvan beheerders mogen plannen.
PROJECTS: dict[str, str] = json.loads(os.environ.get("SUPABASE_PROJECTS") or "{}")
ENABLED = bool(PROJECTS)
CACHE_SECONDS = 300

_cache: dict[str, float] = {}   # token -> tot wanneer het als beheerder geldt
_lock = threading.Lock()


def _claims(token: str) -> dict:
    """De inhoud van het token, ongecontroleerd: alleen om het project te kiezen. Supabase controleert het echt."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _project(claims: dict) -> str | None:
    """Het Supabase-project dat het token uitgaf (veld `iss`), als dat een toegestaan project is."""
    issuer = str(claims.get("iss") or "")
    return next((url for url in PROJECTS if issuer == f"{url}/auth/v1"), None)


def _is_admin(url: str, token: str) -> bool:
    try:
        response = requests.post(f"{url}/rest/v1/rpc/is_admin", json={}, timeout=10, headers={
            "apikey": PROJECTS[url],
            "Authorization": f"Bearer {token}",
        })
        return response.status_code == 200 and response.json() is True
    except (requests.RequestException, ValueError):
        return False


def require_admin(request: Request) -> None:
    """FastAPI-dependency: weigert het verzoek als het niet van een ingelogde beheerder komt."""
    if not ENABLED:
        return
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    claims = _claims(token) if token else {}
    url = _project(claims)
    if not url:
        raise HTTPException(401, "Log in als beheerder.")
    now = time.time()
    with _lock:
        for old in [t for t, until in _cache.items() if until < now]:
            del _cache[old]
        if _cache.get(token, 0) >= now:
            return
    if not _is_admin(url, token):
        raise HTTPException(403, "Dit account heeft geen beheerrechten.")
    # Onthouden scheelt een vraag aan Supabase per verzoek; nooit langer dan het token zelf geldig is.
    expires = claims.get("exp")
    until = now + CACHE_SECONDS
    if isinstance(expires, (int, float)):
        until = min(until, float(expires))
    with _lock:
        _cache[token] = until
