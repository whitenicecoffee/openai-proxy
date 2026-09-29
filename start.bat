@echo off
setlocal
rem One-click Windows launcher: configure Codex, then start bps-proxy.
rem A_start.bat only reapplies the configuration without starting the proxy.
rem A_close.bat removes the proxy configuration.

cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo [1/2] Applying Codex proxy configuration...
"%PYTHON%" "%~dp0A_start.py"
if errorlevel 1 (
    echo [ERROR] Codex configuration failed. The proxy was not started.
    set "exit_code=1"
    goto :done
)

echo [2/2] Starting bps-proxy...
if not defined BPS_UPSTREAM_MODE set "BPS_UPSTREAM_MODE=direct"
"%PYTHON%" -m bps_proxy %*
set "exit_code=%errorlevel%"

:done
endlocal & exit /b %exit_code%
