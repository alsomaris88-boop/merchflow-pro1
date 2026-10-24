@echo off
setlocal ENABLEEXTENSIONS DISABLEDELAYEDEXPANSION
title MerchFlow Pro - 1-Click Starter (unified :8000)
cd /d "%~dp0"

set "WORKER_DIR=%~dp0desktop"
set "WORKER_PYTHON=%WORKER_DIR%\.venv_worker\Scripts\python.exe"
set "ENGINE_LOG=%WORKER_DIR%\engine_boot.log"
set "ENGINE_EXIT_FILE=%WORKER_DIR%\engine_boot.exit"

REM The visible engine console re-enters here, without reinstalling dependencies.
if /i "%~1"=="--engine" goto :run_engine

> "%ENGINE_LOG%" echo MerchFlow engine boot - %date% %time%
if errorlevel 1 (
  echo ERROR: Cannot write "%ENGINE_LOG%". Check folder permissions.
  pause
  exit /b 1
)

echo ============================================================
echo   MerchFlow Pro - unified engine + UI on 127.0.0.1:8000
echo ============================================================
echo   Boot log: "%ENGINE_LOG%"
echo.

REM ------------------------------------------------------------ [1/4] Python
echo [1/4] Checking Python 3.11+ ...
where python >nul 2>nul
if errorlevel 1 goto :python_missing
where powershell >nul 2>nul
if errorlevel 1 goto :powershell_missing

REM ------------------------------------------------- [2/4] Worker virtualenv
echo [2/4] Preparing engine environment (desktop\.venv_worker) ...
if not exist "%WORKER_PYTHON%" (
  python -m venv "%WORKER_DIR%\.venv_worker" >> "%ENGINE_LOG%" 2>&1
  if errorlevel 1 goto :venv_failed
)
"%WORKER_PYTHON%" -c "import sys; print(sys.executable); print(sys.version); sys.exit(0 if sys.version_info >= (3, 11) else 1)" >> "%ENGINE_LOG%" 2>&1
if errorlevel 1 goto :venv_failed

REM Always refresh requirements, including when the venv already exists.
REM Use the exact venv interpreter; never resolve a different pip from PATH.
echo   Updating worker dependencies (this may take a few minutes) ...
"%WORKER_PYTHON%" -m pip install --upgrade pip >> "%ENGINE_LOG%" 2>&1
if errorlevel 1 echo   WARNING: pip upgrade failed; still attempting requirements installation.
"%WORKER_PYTHON%" -m pip install -q -r "%WORKER_DIR%\requirements.txt" >> "%ENGINE_LOG%" 2>&1
if errorlevel 1 goto :dependencies_failed

REM ----------------------------------------------------- [3/4] Engine :8000
REM Never mistake an old engine or unrelated listener for this new process.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$client = New-Object System.Net.Sockets.TcpClient; try { $client.Connect('127.0.0.1', 8000); exit 1 } catch { exit 0 } finally { $client.Dispose() }" >> "%ENGINE_LOG%" 2>&1
if errorlevel 1 goto :port_unavailable

REM Discard stale exit status so a previous crash cannot fail this boot.
if exist "%ENGINE_EXIT_FILE%" del /q "%ENGINE_EXIT_FILE%" >nul 2>nul
if exist "%ENGINE_EXIT_FILE%" goto :status_reset_failed

echo [3/4] Starting unified MerchFlow engine on http://127.0.0.1:8000 ...
REM /k keeps the engine console visible after a crash; /s handles quoted paths.
REM Do not minimize it or use an unqualified uvicorn executable from PATH.
start "MerchFlow Engine (uvicorn :8000)" "%ComSpec%" /d /s /k ""%~f0" --engine"
if errorlevel 1 goto :launch_failed

REM Cold imports (libvips/rembg/ONNX) can take time. Poll for up to 45 seconds.
REM Require HTTP 200 from our worker, not just a listener or a Google account.
REM No app browser/WebView can open before the readiness command succeeds.
REM The exit marker aborts the wait immediately when the engine process exits.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline = (Get-Date).AddSeconds(45); while ($true) { if (Test-Path -LiteralPath $env:ENGINE_EXIT_FILE) { exit 1 }; if ((Get-Date) -gt $deadline) { exit 1 }; try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1 -ErrorAction Stop; if ([int]$response.StatusCode -eq 200) { $health = $response.Content | ConvertFrom-Json; if ($health.ok -and $health.unified) { if (Test-Path -LiteralPath $env:ENGINE_EXIT_FILE) { exit 1 }; if ((Get-Date) -gt $deadline) { exit 1 }; exit 0 } } } catch { }; Start-Sleep -Milliseconds 400 }"
if errorlevel 1 goto :engine_not_ready
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
start "" "%UI_URL%"

:done
echo.
echo   MerchFlow Pro is UP - one port, zero Node at runtime:
echo     App + API  %UI_URL%          (served by Python/FastAPI)
echo     Database   "%USERPROFILE%\MerchFlow\merchflow.db" (SQLite, offline)
if defined MERCHFLOW_OUT (echo     Output     "%MERCHFLOW_OUT%") else (echo     Output     "%~dp0output")
echo     Boot log   "%ENGINE_LOG%"
echo.
echo   Leave the engine window running while you work.
endlocal & exit /b 0

REM ------------------------------------------------------ engine console
:run_engine
cd /d "%WORKER_DIR%" >> "%ENGINE_LOG%" 2>&1
if errorlevel 1 (
  set "ENGINE_EXIT_CODE=1"
  goto :engine_exited
)
echo   Engine output is being recorded in "%ENGINE_LOG%".
echo   If the engine exits, its complete log will be displayed here.
echo   Leave this console open while using MerchFlow Pro.
"%WORKER_PYTHON%" -u -m uvicorn worker.app:app --host 127.0.0.1 --port 8000 --log-level info >> "%ENGINE_LOG%" 2>&1
set "ENGINE_EXIT_CODE=%ERRORLEVEL%"

:engine_exited
>> "%ENGINE_LOG%" echo Engine exited with code %ENGINE_EXIT_CODE%.
> "%ENGINE_EXIT_FILE%" echo %ENGINE_EXIT_CODE%
echo.
echo   Engine exited with code %ENGINE_EXIT_CODE%. See the boot log below.
type "%ENGINE_LOG%"
echo.
echo   Full boot log: "%ENGINE_LOG%"
endlocal & exit /b %ENGINE_EXIT_CODE%

REM ------------------------------------------------------ failure messages
:python_missing
set "BOOT_ERROR=Python not found. Install Python 3.11+ and enable Add python.exe to PATH."
goto :boot_failed

:powershell_missing
set "BOOT_ERROR=Windows PowerShell was not found. Restore powershell.exe on PATH."
goto :boot_failed

:venv_failed
set "BOOT_ERROR=Worker venv setup failed. The venv must use a working Python 3.11+ interpreter."
goto :boot_failed

:dependencies_failed
set "BOOT_ERROR=Worker dependency installation failed. See the pip error below; the engine was not started."
goto :boot_failed

:port_unavailable
set "BOOT_ERROR=Port 8000 is already in use, or the port check failed. Stop the existing engine and rerun."
goto :boot_failed

:status_reset_failed
set "BOOT_ERROR=Cannot reset the previous engine exit status. Check desktop folder permissions."
goto :boot_failed

:launch_failed
set "BOOT_ERROR=Could not open the engine console. See the boot log below."
goto :boot_failed

:engine_not_ready
set "BOOT_ERROR=Engine exited or did not become healthy within 45 seconds. See the startup error below."
goto :boot_failed

:boot_failed
>> "%ENGINE_LOG%" echo ERROR: %BOOT_ERROR%
echo.
echo   ERROR: %BOOT_ERROR%
echo.
echo --------------------- engine_boot.log ---------------------
if exist "%ENGINE_LOG%" type "%ENGINE_LOG%"
echo -----------------------------------------------------------
echo   Full boot log: "%ENGINE_LOG%"
pause
endlocal & exit /b 1
