@echo off
title Swing Trader - Scheduled Scan
cd /d "%~dp0backend"
echo.
echo  Running Swing Trader scheduled scan...
echo.
python run_scheduled_scan.py
if errorlevel 1 (
    echo.
    echo  Scan failed! Check the log file.
    echo.
    pause
    exit /b 1
)
echo.
echo  Scan complete! Open start.bat to view results.
echo.
timeout /t 5 /nobreak >NUL
