@echo off
setlocal
rem One-click Windows launcher: configure Codex, then start bps-proxy.
rem A_close.bat removes the proxy configuration.

cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

if not defined BPS_UPSTREAM_MODE set "BPS_UPSTREAM_MODE=auto"
"%PYTHON%" "%~dp0start.py" %*
set "exit_code=%errorlevel%"

:done
endlocal & exit /b %exit_code%
