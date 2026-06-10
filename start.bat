@echo off
title Swing Trader - NSE Stock Screener
color 0A
echo.
echo  ============================================
echo   Swing Trader - NSE Stock Screener
echo  ============================================
echo.

REM -- Check Python --
echo [1/7] Checking Python...
where python >NUL 2>NUL
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  ERROR: Python is not installed.
    echo  Download from https://www.python.org/downloads/
    echo  Make sure to check Add Python to PATH during install.
    echo.
    pause
    exit /b 1
)
echo        Python found.

REM -- Check Node.js --
echo [2/7] Checking Node.js...
where node >NUL 2>NUL
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  ERROR: Node.js is not installed.
    echo  Download from https://nodejs.org/
    echo.
    pause
    exit /b 1
)
echo        Node.js found.

REM -- Install Python packages --
echo [3/7] Installing Python packages...
cd /d "%~dp0backend"
pip install -r requirements.txt --quiet --disable-pip-version-check
echo        Done.

REM -- Install frontend packages --
echo [4/7] Installing frontend packages...
cd /d "%~dp0frontend"
if not exist node_modules (
    call npm install --silent
)
echo        Done.

REM -- Kill old processes --
echo [5/7] Cleaning up old processes...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000.*LISTENING"') do taskkill /F /PID %%a >NUL 2>NUL
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5174.*LISTENING"') do taskkill /F /PID %%a >NUL 2>NUL
timeout /t 2 /nobreak >NUL

REM -- Start Backend --
echo [6/7] Starting backend...
cd /d "%~dp0backend"
start "SwingTrader-Backend" cmd /k "title Swing Trader Backend && python run.py"
timeout /t 5 /nobreak >NUL
echo        Backend started.

REM -- Start Frontend --
echo [7/7] Starting frontend...
cd /d "%~dp0frontend"
start "SwingTrader-Frontend" cmd /k "title Swing Trader Frontend && npm run dev"
timeout /t 4 /nobreak >NUL
echo        Frontend started.

echo.
echo  ============================================
echo   Swing Trader is running!
echo   Open http://localhost:5174
echo  ============================================
echo.
start http://localhost:5174
pause
