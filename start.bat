@echo off
rem bps-proxy service launcher.
rem First-time Windows setup: run A_start.bat to configure Codex.
rem To remove that configuration later: run A_close.bat.
rem With TUN enabled, direct sockets are routed by the system/TUN policy.
if not defined BPS_UPSTREAM_MODE set "BPS_UPSTREAM_MODE=direct"
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -m bps_proxy %*
) else (
    python -m bps_proxy %*
)
