@echo off
title Swing Trader - Scheduler Setup
color 0E
echo.
echo  ============================================
echo   Swing Trader - Task Scheduler Setup
echo  ============================================
echo.
echo  This creates a daily 9:30 AM task.
echo  You must run this as Administrator.
echo.

REM Check admin rights
net session >NUL 2>&1
if errorlevel 1 (
    echo  ERROR: Not running as Administrator.
    echo  Right-click this file and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

set SCRIPT_PATH=%~dp0run_screener.bat
echo  Creating task: SwingTraderDailyScan
echo  Schedule: Daily at 09:30
echo  Script: %SCRIPT_PATH%
echo.

schtasks /delete /tn "SwingTraderDailyScan" /f >NUL 2>NUL
schtasks /create /tn "SwingTraderDailyScan" /tr "%SCRIPT_PATH%" /sc DAILY /st 09:30 /rl HIGHEST /f

if errorlevel 1 (
    echo  Failed to create task. Try running as Administrator.
    pause
    exit /b 1
)

echo.
echo  ============================================
echo   Task created: SwingTraderDailyScan
echo   Schedule: Every day at 09:30 AM
echo.
echo   Make sure your PC timezone is IST.
echo  ============================================
echo.
pause
