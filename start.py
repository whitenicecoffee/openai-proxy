"""One-click Codex proxy configuration for Windows."""

from __future__ import annotations

import base64
import os
import re
import sys
import tempfile
from pathlib import Path

PROXY_BASE_URL = "http://127.0.0.1:8787/v1"
PROXY_PROVIDER = "openai-proxy"

SECTION_HEADER_RE = re.compile(
    r"(?m)^[ \t]*\[[^]\r\n]+\][ \t]*(?:#.*)?(?:\r?\n|$)"
)
PROVIDER_RE = re.compile(
    rf"(?m)^[ \t]*\[model_providers\.{re.escape(PROXY_PROVIDER)}\][ \t]*(?:#.*)?(?:\r?\n|$)"
)
LEGACY_OPENAI_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*\[model_providers\.openai\][ \t]*(?:#.*)?(?:#.*)?(?:\r?\n|$)"
)
BASE_LINE_RE = re.compile(
    r"(?m)^[ \t]*openai_base_url[ \t]*=[ \t]*(?P<value>.*?)(?:\r?\n|$)"
)
MANAGED_BASE_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed openai_base_url previous=([A-Za-z0-9_-]+|absent)[ \t]*(?:\r?\n)"
    r"^[ \t]*openai_base_url[ \t]*=.*(?:\r?\n|$)"
)
MANAGED_BASE_MARKER = "# bps-proxy: managed openai_base_url previous={previous}"

MODEL_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*model_provider[ \t]*=[ \t]*(?P<value>.*?)(?:\r?\n|$)"
)
MANAGED_MODEL_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed model_provider previous=([A-Za-z0-9_-]+|absent)[ \t]*(?:\r?\n)"
    r"^[ \t]*model_provider[ \t]*=.*(?:\r?\n|$)"
)
MANAGED_MODEL_PROVIDER_MARKER = "# bps-proxy: managed model_provider previous={previous}"

MANAGED_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed provider previous=([A-Za-z0-9_-]+|absent)[ \t]*(?:\r?\n)"
)
MANAGED_PROVIDER_MARKER = "# bps-proxy: managed provider previous={previous}"


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


def _b64encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _b64decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")


def _section_span(body: str, header: re.Pattern[str]) -> tuple[int, int] | None:
    match = header.search(body)
    if match is None:
        return None
    next_section = SECTION_HEADER_RE.search(body, match.end())
    end = next_section.start() if next_section else len(body)
    return match.start(), end


def _remove_section(
    body: str, header: re.Pattern[str]
) -> tuple[str, str | None]:
    span = _section_span(body, header)
    if span is None:
        return body, None
    start, end = span
    return body[:start] + body[end:], body[start:end]


def _remove_all_sections(body: str, header: re.Pattern[str]) -> str:
    while header.search(body):
        body, _ = _remove_section(body, header)
    return body


def _provider_block(body: str) -> tuple[str, str]:
    """Remove our provider block and return (body, previous block token)."""
    managed = MANAGED_PROVIDER_RE.search(body)
    if managed:
        provider = PROVIDER_RE.search(body, managed.end())
        if provider is not None:
            next_section = SECTION_HEADER_RE.search(body, provider.end())
            end = next_section.start() if next_section else len(body)
            return (
                body[:managed.start()] + body[end:],
                managed.group(1),
            )
        return body[:managed.start()] + body[managed.end():], "absent"

    body, block = _remove_section(body, PROVIDER_RE)
    return body, _b64encode(block) if block is not None else "absent"


def _previous_line(body: str, managed: re.Pattern[str], current: re.Pattern[str]) -> str:
    match = managed.search(body)
    if match:
        return match.group(1)
    existing = current.search(body)
    if existing:
        return _b64encode(existing.group(0).rstrip("\r\n"))
    return "absent"


def configure() -> Path:
    path = config_path()
    current = read_config(path).replace("\r\n", "\n").replace("\r", "\n")

    previous_base = _previous_line(current, MANAGED_BASE_RE, BASE_LINE_RE)
    previous_provider = _previous_line(
        current, MANAGED_MODEL_PROVIDER_RE, MODEL_PROVIDER_RE
    )

    body = MANAGED_BASE_RE.sub("", current)
    body = BASE_LINE_RE.sub("", body)
    body = MANAGED_MODEL_PROVIDER_RE.sub("", body)
    body = MODEL_PROVIDER_RE.sub("", body)

    # The earlier release incorrectly created [model_providers.openai].
    # That table overrides a reserved built-in ID and must be removed so an
    # upgraded configuration can start successfully.
    body = _remove_all_sections(body, LEGACY_OPENAI_PROVIDER_RE)

    body, previous_proxy_provider = _provider_block(body)

    managed_provider_block = (
        MANAGED_PROVIDER_MARKER.format(previous=previous_proxy_provider)
        + "\n"
        + f"[model_providers.{PROXY_PROVIDER}]\n"
        + 'name = "OpenAI Proxy"\n'
        + f'base_url = "{PROXY_BASE_URL}"\n'
        + 'wire_api = "responses"\n'
        + "requires_openai_auth = true\n"
        + "supports_websockets = false\n"
    )

    body = body.strip("\n")
    if body:
        body += "\n\n"
    updated = (
        MANAGED_MODEL_PROVIDER_MARKER.format(previous=previous_provider)
        + "\n"
        + f'model_provider = "{PROXY_PROVIDER}"\n'
        + MANAGED_BASE_MARKER.format(previous=previous_base)
        + "\n"
        + body
        + managed_provider_block
    )
    write_config(path, updated)
    return path


def main() -> int:
    try:
        path = configure()
    except (OSError, UnicodeError, ValueError) as error:
        print(f"[ERROR] 无法写入 Codex 配置：{error}", file=sys.stderr)
        return 1

    print(f"[OK] 已完成代理配置：{path}")
    print(f"[OK] model_provider = {PROXY_PROVIDER}")
    print(f"[OK] base_url = {PROXY_BASE_URL}")
    print("[OK] Codex WebSocket 已关闭，使用 HTTP/SSE。")
    print("正在启动 bps-proxy；按 Ctrl+C 停止。")
    from bps_proxy.__main__ import main as proxy_main

    proxy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
