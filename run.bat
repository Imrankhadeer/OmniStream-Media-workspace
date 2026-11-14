@echo off
title OmniStream Downloader
cd /d "%~dp0"

echo ==============================================
echo   OmniStream - Local Video ^& Stream Downloader
echo ==============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Please set up the environment first.
    pause
    exit /b 1
)

echo Starting local server on http://127.0.0.1:8000...
start "" http://127.0.0.1:8000

.\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
pause
