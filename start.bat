@echo off
chcp 65001 >nul
title MISSAV M3U8 GUI Downloader

echo ========================================
echo   MISSAV M3U8 GUI Downloader - Starting...
echo ========================================
echo.

cd /d "%~dp0"

echo [Check] Activating conda py38 environment...
call conda activate py38
if errorlevel 1 (
    echo [Error] Failed to activate conda py38 environment.
    pause
    exit /b 1
)

python --version

echo [Check] Checking dependencies...
pip show requests >nul 2>&1
if errorlevel 1 (
    echo [Install] Installing dependencies...
    pip install -r requirements.txt
)

echo.
echo [Start] Launching GUI...
echo.

python app.py

pause
