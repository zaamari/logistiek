"""Webserver: serveert de frontend en de API."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from . import excel_io, planner
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

app = FastAPI(title="Leverplanner")


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
