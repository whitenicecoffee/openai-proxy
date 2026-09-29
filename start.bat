@echo off
rem bps-proxy service launcher.
rem First-time Windows setup: run A_start.bat to configure Codex.
rem To remove that configuration later: run A_close.bat.
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -m bps_proxy %*
) else (
    python -m bps_proxy %*
)
