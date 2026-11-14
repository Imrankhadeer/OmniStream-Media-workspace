@echo off
title Build OmniStream.exe Launcher
cd /d "%~dp0"

echo [Build] Compiling Windows resource...
windres resource.rc -O coff -o resource.res
if errorlevel 1 (
    echo [ERROR] windres failed. Ensure MinGW is installed.
    pause
    exit /b 1
)

echo [Build] Compiling native OmniStream.exe launcher...
gcc -mwindows -O2 launcher.c resource.res -o ..\OmniStream.exe
if errorlevel 1 (
    echo [ERROR] gcc compilation failed.
    pause
    exit /b 1
)

del /q resource.res 2>nul
echo [OK] Successfully built ..\OmniStream.exe!
pause
