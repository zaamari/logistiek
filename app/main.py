"""Webserver: serveert de frontend en de API.

Lokaal (start.bat) is alles open. Online (Cloud Run, zie deploy.sh) mogen alleen beheerders van
Houtenhak de planner gebruiken (zie auth.py), en alleen vanaf de adressen in ALLOWED_ORIGINS.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from . import excel_io, planner
from .auth import require_admin
from .geo import DATA_DIR

STATIC_DIR = Path(__file__).resolve().parent / "static"
SETTINGS_FILE = DATA_DIR / "settings.json"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

DEFAULT_SETTINGS = {
    "start_address": "",
    "loods_address": "",
    "first_arrival": "08:00",
    "capacity": 6,
    "load_minutes": 30,
    "unload_first": 7,
    "unload_extra": 3,
    "time_limit": 5,
}

app = FastAPI(title="Leverplanner", dependencies=[Depends(require_admin)])

# Websites die de planner vanuit de browser mogen aanroepen, gescheiden door komma's (leeg = geen),
# plus eventueel een patroon voor wisselende adressen (zoals de previews van Netlify).
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
ALLOWED_ORIGIN_REGEX = os.environ.get("ALLOWED_ORIGIN_REGEX") or None
if ALLOWED_ORIGINS or ALLOWED_ORIGIN_REGEX:
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_origin_regex=ALLOWED_ORIGIN_REGEX,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"], max_age=3600)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/settings")
def get_settings():
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULT_SETTINGS, **saved}


@app.put("/api/settings")
def put_settings(settings: dict):
    merged = {**DEFAULT_SETTINGS, **{k: v for k, v in settings.items() if k in DEFAULT_SETTINGS}}
    DATA_DIR.mkdir(exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


@app.post("/api/plan")
def make_plan(body: dict):
    try:
        return planner.plan(body.get("orders", []), {**get_settings(), **body.get("settings", {})})
    except planner.PlanError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(422, str(e))


@app.post("/api/import")
async def import_excel(file: UploadFile):
    try:
        return {"orders": excel_io.read_orders(await file.read())}
    except Exception as e:  # kapotte of onbekende bestanden
        raise HTTPException(400, f"Kon Excel niet lezen: {e}")


@app.get("/api/template")
def template():
    return Response(excel_io.template(), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="bestellingen-sjabloon.xlsx"'})


@app.post("/api/export")
def export(result: dict):
    return Response(excel_io.export_plan(result), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="planning.xlsx"'})
