@echo off
cd /d "%~dp0"
start "" http://localhost:8765
python -m uvicorn app.main:app --port 8765
