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
    r"(?m)^[ \t]*\[model_providers\.openai\][ \t]*(?:#.*)?(?:\r?\n|$)"
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

NO_PROXY_MARKER_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed NO_PROXY previous=([A-Za-z0-9_-]+|absent)[ \t]*(?:\r?\n|$)"
)
MANAGED_NO_PROXY_MARKER = "# bps-proxy: managed NO_PROXY previous={previous}"
LOCAL_PROXY_BYPASS = ("127.0.0.1", "localhost", "::1")


def _append_local_bypass(value: str) -> str:
    current = value.strip()
    if current == "*":
        return current
    existing = {item.strip().lower() for item in current.split(",") if item.strip()}
    missing = [host for host in LOCAL_PROXY_BYPASS if host.lower() not in existing]
    if not missing:
        return current
    suffix = ",".join(missing)
    return f"{current},{suffix}" if current else suffix


def _read_user_no_proxy() -> str:
    current = os.environ.get("NO_PROXY", "")
    if current:
        return current
    if os.name != "nt":
        return current
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "NO_PROXY")
            return str(value or "")
    except (ImportError, OSError, TypeError):
        return current


def _broadcast_environment_change() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None
        )
    except (AttributeError, OSError):
        pass


def _write_user_no_proxy(value: str) -> bool:
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            winreg.SetValueEx(key, "NO_PROXY", 0, winreg.REG_SZ, value)
        _broadcast_environment_change()
        return True
    except (ImportError, OSError):
        return False


def ensure_local_proxy_bypass(previous: str | None = None) -> tuple[str, bool]:
    current = _read_user_no_proxy()
    if previous is None:
        previous = _b64encode(current) if current else "absent"
    desired = _append_local_bypass(current)
    persisted = _write_user_no_proxy(desired)
    return previous, persisted




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


def _previous_base(body: str) -> str:
    managed = MANAGED_BASE_RE.search(body)
    if managed:
        return managed.group(1)
    existing = BASE_LINE_RE.search(body)
    if existing is None:
        return "absent"
    value = existing.group("value").strip().strip("\"'")
    if value == PROXY_BASE_URL:
        return "absent"
    return _b64encode(existing.group(0).rstrip("\r\n"))


def configure() -> Path:
    path = config_path()
    current = read_config(path).replace("\r\n", "\n").replace("\r", "\n")

    previous_base = _previous_base(current)
    previous_provider = _previous_line(
        current, MANAGED_MODEL_PROVIDER_RE, MODEL_PROVIDER_RE
    )
    no_proxy_marker = NO_PROXY_MARKER_RE.search(current)
    previous_no_proxy = no_proxy_marker.group(1) if no_proxy_marker else None
    previous_no_proxy, no_proxy_persisted = ensure_local_proxy_bypass(previous_no_proxy)

    body = MANAGED_BASE_RE.sub("", current)
    body = NO_PROXY_MARKER_RE.sub("", body)
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
        + MANAGED_NO_PROXY_MARKER.format(previous=previous_no_proxy)
        + "\n"
        + (
            MANAGED_BASE_MARKER.format(previous=previous_base) + "\n"
            if previous_base != "absent"
            else ""
        )
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
    print("[OK] Codex 本机地址绕过系统代理：127.0.0.1, localhost, ::1")
    if os.name == "nt":
        if not no_proxy_persisted:
            print("[WARN] 无法写入 Windows 用户 NO_PROXY；请在启动 Codex 前手动设置 NO_PROXY=127.0.0.1,localhost,::1。")
        else:
            print("[提示] 上游请求仍按 BPS_UPSTREAM_MODE 走系统代理；请在新终端启动 Codex 以读取 NO_PROXY。")
    print("正在启动 bps-proxy；按 Ctrl+C 停止。")
    from bps_proxy.__main__ import main as proxy_main

    proxy_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
