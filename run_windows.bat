@echo off
setlocal ENABLEEXTENSIONS
title MerchFlow Pro - 1-Click Starter (unified :8000)
cd /d %~dp0

echo ============================================================
echo   MerchFlow Pro - unified engine + UI on 127.0.0.1:8000
echo ============================================================
echo.

REM ------------------------------------------------------------ [1/4] Python
echo [1/4] Checking Python 3.11+ ...
where python >nul 2>nul
if errorlevel 1 (
  echo   ERROR: Python not found. Install Python 3.11+ from python.org
  echo   and enable "Add python.exe to PATH".
  pause
  exit /b 1
)

REM ------------------------------------------------- [2/4] Worker virtualenv
echo [2/4] Preparing engine environment (desktop\.venv_worker) ...
if not exist desktop\.venv_worker (
  python -m venv desktop\.venv_worker
)
call desktop\.venv_worker\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -q -r desktop\requirements.txt
if errorlevel 1 (
  echo   ERROR: dependency install failed. pyvips needs the libvips runtime:
  echo     winget install --id libvips.libvips
  pause
  exit /b 1
)

REM ----------------------------------------------------- [3/4] Engine :8000
REM One process serves BOTH the pre-built static UI (out/) and /api/*
REM on the same port. No Node.js, no npm, no dev server.
echo [3/4] Starting unified MerchFlow engine on http://127.0.0.1:8000 ...
start "MerchFlow Engine (uvicorn :8000)" /min cmd /c "cd /d %~dp0desktop && call .venv_worker\Scripts\activate.bat && uvicorn worker.app:app --host 127.0.0.1 --port 8000 --log-level warning"

REM Confirm port 8000 accepts a real TCP connection before opening the window
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline = (Get-Date).AddSeconds(45); while (-not (Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -WarningAction SilentlyContinue -InformationLevel Quiet)) { if (Get-Date -gt $deadline) { exit 1 }; Start-Sleep -Milliseconds 400 }; exit 0"
if errorlevel 1 (
  echo   ERROR: engine did not come online on 127.0.0.1:8000 within 45 seconds.
  echo          Check antivirus/port conflicts, then rerun.
  pause
  exit /b 1
)
echo   Engine online.

REM ----------------------------------------- [4/4] Standalone desktop window
echo [4/4] Opening MerchFlow app window ...
set "UI_URL=http://127.0.0.1:8000"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --app=%UI_URL% --window-size=1440,920
  goto :done
)
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
  start "" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" --app=%UI_URL% --window-size=1440,920
  goto :done
)
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
  start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --app=%UI_URL% --window-size=1440,920
  goto :done
)
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
  start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --app=%UI_URL% --window-size=1440,920
  goto :done
)
start "" %UI_URL%

:done
echo.
echo   MerchFlow Pro is UP - one port, zero Node at runtime:
echo     App + API  %UI_URL%          (served by Python/FastAPI)
echo     Database   %USERPROFILE%\MerchFlow\merchflow.db  (SQLite, offline)
if defined MERCHFLOW_OUT (echo     Output     %MERCHFLOW_OUT%) else (echo     Output     %~dp0output)
echo.
echo   Leave the engine window running while you work.
endlocal
