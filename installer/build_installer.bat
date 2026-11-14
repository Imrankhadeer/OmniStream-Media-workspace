@echo off
title Build OmniStream Installer
cd /d "%~dp0"

echo [Build] Compiling native OmniStream_Setup.exe wizard...
windres resource.rc -O coff -o resource.res
if errorlevel 1 (
    echo [ERROR] windres failed.
    pause
    exit /b 1
)

gcc -mwindows -O2 installer.c resource.res -o ..\OmniStream_Setup.exe -lole32 -luuid -lshell32
if errorlevel 1 (
    echo [ERROR] gcc compilation of OmniStream_Setup.exe failed.
    del /q resource.res 2>nul
    pause
    exit /b 1
)
del /q resource.res 2>nul
echo [OK] Built ..\OmniStream_Setup.exe successfully!

where iscc >nul 2>nul
if not errorlevel 1 (
    echo [Build] Building Inno Setup package...
    iscc OmniStream_Setup.iss
)

echo.
echo All installer targets are ready!
pause
