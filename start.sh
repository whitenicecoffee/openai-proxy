#!/bin/sh
# bps-proxy service launcher.
# Configure Codex separately with the platform-specific A_start.bat on Windows,
# or add openai_base_url to CODEX_HOME/config.toml on macOS/Linux.
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python -m bps_proxy "$@"
fi
exec python3 -m bps_proxy "$@"
