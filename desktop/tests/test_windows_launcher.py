"""Launcher contracts plus executable PowerShell readiness regressions.

These tests never install packages, launch uvicorn, or change Windows processes.
PowerShell probes run with isolated test doubles. Full cmd.exe / Windows boot
still needs a target-machine smoke test.
"""
import base64
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = (ROOT / "run_windows.bat").read_text(encoding="utf-8")
COMMANDS = re.findall(r'^powershell .*? -Command "([^"\r\n]+)"', LAUNCHER, re.MULTILINE)
READY = next(command for command in COMMANDS if "$deadline" in command)
PORT_CHECK = next(command for command in COMMANDS if "TcpClient" in command)


def test_existing_venv_always_gets_a_requirements_refresh():
    creation = LAUNCHER.index('if not exist "%WORKER_PYTHON%" (')
    creation_end = LAUNCHER.index("\n)", creation) + 2
    install = LAUNCHER.index('"%WORKER_PYTHON%" -m pip install -q -r "%WORKER_DIR%\\requirements.txt"')
    assert creation_end < install < LAUNCHER.index('start "MerchFlow Engine')
    assert 'if errorlevel 1 goto :dependencies_failed' in LAUNCHER[install:]
    # An upgrade-pip failure must not skip requirements installation either.
    before_install = LAUNCHER[creation_end:install]
    assert "still attempting requirements installation" in before_install
    assert not re.search(r"^\s*(?:pip|uvicorn)\s", LAUNCHER, re.MULTILINE)
    assert "activate.bat" not in LAUNCHER
    assert "rembg[cpu]" in (ROOT / "desktop/requirements.txt").read_text()


def test_powershell_date_is_an_expression_not_a_get_date_parameter():
    assert "if ((Get-Date) -gt $deadline)" in READY
    assert "Get-Date -gt" not in LAUNCHER
    assert "/api/health" in READY
    assert "$health.ok -and $health.connected -and $health.unified" in READY
    assert READY.index("$env:ENGINE_EXIT_FILE") < READY.index("Invoke-RestMethod")


def test_crash_output_is_logged_exit_status_is_preserved_and_console_stays_open():
    assert '"%ComSpec%" /d /s /k ""%~f0" --engine"' in LAUNCHER
    assert " /min " not in LAUNCHER
    engine = LAUNCHER[LAUNCHER.index("\n:run_engine\n"):LAUNCHER.index("REM ------------------------------------------------------ failure messages")]
    command = '"%WORKER_PYTHON%" -u -m uvicorn worker.app:app --host 127.0.0.1 --port 8000 --log-level info >> "%ENGINE_LOG%" 2>&1'
    assert command + '\nset "ENGINE_EXIT_CODE=%ERRORLEVEL%"' in engine
    assert '> "%ENGINE_EXIT_FILE%" echo %ENGINE_EXIT_CODE%' in engine
    assert 'type "%ENGINE_LOG%"' in engine
    assert 'endlocal & exit /b %ENGINE_EXIT_CODE%' in engine
    failure = LAUNCHER[LAUNCHER.index("\n:boot_failed\n"):]
    assert 'type "%ENGINE_LOG%"' in failure and "pause" in failure
    assert 'endlocal & exit /b 1' in failure
    assert 'if errorlevel 1 goto :engine_not_ready' in LAUNCHER
    # Only uvicorn owns the long-lived log handle during readiness polling.
    ready_line = next(line for line in LAUNCHER.splitlines() if "$deadline" in line)
    assert '>> "%ENGINE_LOG%"' not in ready_line


def test_paths_status_cleanup_and_all_failure_targets_are_valid():
    assert 'cd /d "%~dp0"' in LAUNCHER
    assert "DISABLEDELAYEDEXPANSION" in LAUNCHER
    assert 'if exist "%ENGINE_EXIT_FILE%" del /q "%ENGINE_EXIT_FILE%"' in LAUNCHER
    assert LAUNCHER.index('del /q "%ENGINE_EXIT_FILE%"') < LAUNCHER.index('start "MerchFlow Engine')
    labels = {label.lower() for label in re.findall(r"^:([\w-]+)", LAUNCHER, re.MULTILINE)}
    targets = {target.lower() for target in re.findall(r"\bgoto\s+:([\w-]+)", LAUNCHER, re.IGNORECASE)}
    assert targets <= labels
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert "desktop/engine_boot.log" in ignored
    assert "desktop/engine_boot.exit" in ignored


@pytest.fixture
def powershell():
    executable = os.environ.get("MERCHFLOW_TEST_POWERSHELL") or shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        pytest.skip("PowerShell not installed; launcher contract tests still run")
    return executable


def run_ps(executable, script, tmp_path, prelude=""):
    env = os.environ.copy()
    env["ENGINE_EXIT_FILE"] = str(tmp_path / "engine boot.exit")
    env["MERCHFLOW_TEST_SCRIPT"] = script
    # Evaluate the exact script extracted from the batch file, not a rewritten
    # approximation. Strict errors expose the old Get-Date -gt binding bug.
    code = "$ErrorActionPreference = 'Stop'; " + (prelude + "; " if prelude else "") + "& ([scriptblock]::Create($env:MERCHFLOW_TEST_SCRIPT))"
    return subprocess.run([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code], env=env, capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize("command", COMMANDS)
def test_all_embedded_powershell_commands_parse(powershell, tmp_path, command):
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    parser = (
        "$tokens=$null; $parseErrors=$null; "
        f"$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')); "
        "[void][System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors); "
        "if ($parseErrors.Count) { $parseErrors | ForEach-Object { Write-Error $_.Message }; exit 1 }; exit 0"
    )
    result = run_ps(powershell, parser, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_powershell_ready_health_succeeds_and_date_comparison_executes(powershell, tmp_path):
    mock = "function Invoke-RestMethod { [pscustomobject]@{ok=$true; connected=$true; unified=$true} }"
    result = run_ps(powershell, READY, tmp_path, mock)
    assert result.returncode == 0, result.stdout + result.stderr


def test_powershell_old_date_binding_is_a_real_regression(powershell, tmp_path):
    old = READY.replace("if ((Get-Date) -gt $deadline)", "if (Get-Date -gt $deadline)")
    mock = "function Invoke-RestMethod { [pscustomobject]@{ok=$true; connected=$true; unified=$true} }"
    result = run_ps(powershell, old, tmp_path, mock)
    assert result.returncode != 0
    assert "gt" in result.stderr


def test_powershell_crashed_child_aborts_before_any_health_request(powershell, tmp_path):
    (tmp_path / "engine boot.exit").write_text("1\n")
    result = run_ps(powershell, READY, tmp_path, "function Invoke-RestMethod { throw 'Health must not be requested after a crash' }")
    assert result.returncode == 1
    assert "Health must not" not in result.stderr


def test_powershell_expired_deadline_returns_failure(powershell, tmp_path):
    expired = READY.replace(".AddSeconds(45)", ".AddSeconds(-1)")
    result = run_ps(powershell, expired, tmp_path, "function Invoke-RestMethod { throw 'Health must not be requested after the deadline' }")
    assert result.returncode == 1
    assert "Health must not" not in result.stderr


def test_powershell_unrelated_service_is_not_accepted_as_the_worker(powershell, tmp_path):
    mock = "function Invoke-RestMethod { Set-Content -LiteralPath $env:ENGINE_EXIT_FILE -Value 1; [pscustomobject]@{ok=$true; connected=$false; unified=$false} }"
    result = run_ps(powershell, READY, tmp_path, mock)
    assert result.returncode == 1, result.stdout + result.stderr
