@echo off
title Build OmniStream Inno Setup Installer
cd /d "%~dp0"

where iscc >nul 2>nul
if errorlevel 1 (
    echo [NOTICE] Inno Setup compiler (iscc.exe) not found in PATH.
    echo Please install Inno Setup 6 (https://jrsoftware.org/isdl.php)
    echo or add "C:\Program Files (x86)\Inno Setup 6" to your system PATH.
    pause
    exit /b 1
)

echo [Build] Building OmniStream Windows Installer...
iscc OmniStream_Setup.iss
if errorlevel 1 (
    echo [ERROR] Inno Setup build failed.
    pause
    exit /b 1
)

echo [OK] Installer successfully built in ..\dist\
pause
