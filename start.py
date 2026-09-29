"""One-click Codex proxy configuration for Windows."""

from __future__ import annotations

import base64
import os
import re
import sys
import tempfile
from pathlib import Path

PROXY_BASE_URL = "http://127.0.0.1:8787/v1"
OPENAI_BASE_URL_RE = re.compile(
    r"(?m)^[ \t]*openai_base_url[ \t]*=[ \t]*(?P<value>.*?)(?:\r?\n|$)"
)
SECTION_HEADER_RE = re.compile(
    r"(?m)^[ \t]*\[[^]\r\n]+\][ \t]*(?:#.*)?(?:\r?\n|$)"
)
OPENAI_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*\[model_providers\.openai\][ \t]*(?:#.*)?(?:\r?\n|$)"
)
SUPPORTS_WEBSOCKETS_RE = re.compile(
    r"(?m)^[ \t]*supports_websockets[ \t]*=[ \t]*(true|false)(?:[ \t]*#.*)?(?:\r?\n|$)"
)
MANAGED_SUPPORTS_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed supports_websockets previous=(absent|true|false)[ \t]*\n"
    r"^[ \t]*supports_websockets[ \t]*=[ \t]*false[ \t]*(?:#.*)?(?:\r?\n|$)"
)
MANAGED_MARKER = "# bps-proxy: managed supports_websockets previous={previous}"
MANAGED_BASE_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed openai_base_url previous=([A-Za-z0-9_-]+|absent)[ \t]*\n"
    r"^[ \t]*openai_base_url[ \t]*=.*(?:\r?\n|$)"
)
MANAGED_BASE_MARKER = "# bps-proxy: managed openai_base_url previous={previous}"
MODEL_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*model_provider[ \t]*=[ \t]*(?P<value>.*?)(?:\r?\n|$)"
)
MANAGED_MODEL_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed model_provider previous=([A-Za-z0-9_-]+|absent)[ \t]*\n"
    r"^[ \t]*model_provider[ \t]*=.*(?:\r?\n|$)"
)
MANAGED_MODEL_PROVIDER_MARKER = "# bps-proxy: managed model_provider previous={previous}"


def config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def read_config(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig")


def write_config(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    newline = "\r\n" if os.name == "nt" else "\n"
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(normalized.replace("\n", newline))
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def _provider_transport_config(body: str) -> str:
    """Force HTTP/SSE while remembering the user's previous provider value."""
    provider = OPENAI_PROVIDER_RE.search(body)
    if provider is None:
        suffix = body.rstrip("\n")
        if suffix:
            suffix += "\n"
        return (
            suffix
            + "[model_providers.openai]\n"
            + MANAGED_MARKER.format(previous="absent")
            + "\n"
            + "supports_websockets = false\n"
        )

    next_section = SECTION_HEADER_RE.search(body, provider.end())
    section_end = next_section.start() if next_section else len(body)
    section = body[provider.end():section_end]

    managed = MANAGED_SUPPORTS_RE.search(section)
    if managed:
        previous = managed.group(1)
        section = section[:managed.start()] + section[managed.end():]
    else:
        existing = SUPPORTS_WEBSOCKETS_RE.search(section)
        previous = existing.group(1) if existing else "absent"
        if existing:
            section = section[:existing.start()] + section[existing.end():]

    managed_lines = (
        MANAGED_MARKER.format(previous=previous)
        + "\n"
        + "supports_websockets = false\n"
    )
    return body[:provider.end()] + managed_lines + section + body[section_end:]


def configure() -> Path:
    path = config_path()
    current = read_config(path)
    managed_base = MANAGED_BASE_RE.search(current)
    if managed_base:
        previous_base = managed_base.group(1)
    else:
        existing_base = OPENAI_BASE_URL_RE.search(current)
        if existing_base:
            encoded = base64.urlsafe_b64encode(
                existing_base.group(0).rstrip("\r\n").encode("utf-8")
            ).decode("ascii").rstrip("=")
            previous_base = encoded
        else:
            previous_base = "absent"

    managed_provider = MANAGED_MODEL_PROVIDER_RE.search(current)
    if managed_provider:
        previous_provider = managed_provider.group(1)
    else:
        existing_provider = MODEL_PROVIDER_RE.search(current)
        if existing_provider:
            encoded = base64.urlsafe_b64encode(
                existing_provider.group(0).rstrip("\r\n").encode("utf-8")
            ).decode("ascii").rstrip("=")
            previous_provider = encoded
        else:
            previous_provider = "absent"

    body = MANAGED_BASE_RE.sub("", current)
    body = OPENAI_BASE_URL_RE.sub("", body)
    body = MANAGED_MODEL_PROVIDER_RE.sub("", body)
    body = MODEL_PROVIDER_RE.sub("", body)
    body = _provider_transport_config(body)
    updated = (
        MANAGED_MODEL_PROVIDER_MARKER.format(previous=previous_provider)
        + "\n"
        + 'model_provider = "openai"\n'
        + MANAGED_BASE_MARKER.format(previous=previous_base)
        + "\n"
        + f'openai_base_url = "{PROXY_BASE_URL}"\n'
        + body.lstrip("\r\n")
    )
    write_config(path, updated)
    return path


def main() -> int:
    try:
        path = configure()
    except (OSError, UnicodeError) as error:
        print(f"[ERROR] 无法写入 Codex 配置：{error}", file=sys.stderr)
        return 1

    print(f"[OK] 已完成代理配置：{path}")
    print(f"[OK] openai_base_url = {PROXY_BASE_URL}")
    print("[OK] model_provider = openai")
    print("[OK] Codex WebSocket 已关闭，使用 HTTP/SSE。")
    print("正在启动 bps-proxy；按 Ctrl+C 停止。")
    from bps_proxy.__main__ import main as proxy_main
    proxy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
