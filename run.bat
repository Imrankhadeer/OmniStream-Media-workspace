@echo off
title OmniStream
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [OmniStream] Initializing virtual environment...
    python -m venv .venv
    .\.venv\Scripts\pip install -r requirements.txt
)

REM Launch via the native executable if present, or run tray_app.py
if exist "OmniStream.exe" (
    start "" "OmniStream.exe"
) else (
    start "" .\.venv\Scripts\python.exe tray_app.py
)
exit
