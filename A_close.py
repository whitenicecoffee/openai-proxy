"""One-click removal of the Codex proxy configuration for Windows."""

from __future__ import annotations

import base64
import os
import re
import sys
import tempfile
from pathlib import Path

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
    r"(?m)^[ \t]*openai_base_url[ \t]*=[ \t]*(?:.*?)(?:\r?\n|$)"
)
MANAGED_BASE_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed openai_base_url previous=([A-Za-z0-9_-]+|absent)[ \t]*(?:\r?\n)"
    r"^[ \t]*openai_base_url[ \t]*=.*(?:\r?\n|$)"
)
MODEL_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*model_provider[ \t]*=[ \t]*(?P<value>.*?)(?:\r?\n|$)"
)
MANAGED_MODEL_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed model_provider previous=([A-Za-z0-9_-]+|absent)[ \t]*(?:\r?\n)"
    r"^[ \t]*model_provider[ \t]*=.*(?:\r?\n|$)"
)
PROXY_MODEL_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*model_provider[ \t]*=[ \t]*[\"']openai-proxy[\"'][ \t]*(?:#.*)?(?:\r?\n|$)"
)
MANAGED_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed provider previous=([A-Za-z0-9_-]+|absent)[ \t]*(?:\r?\n)"
)


def config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def _section_span(body: str, header: re.Pattern[str]) -> tuple[int, int] | None:
    match = header.search(body)
    if match is None:
        return None
    next_section = SECTION_HEADER_RE.search(body, match.end())
    end = next_section.start() if next_section else len(body)
    return match.start(), end


def _remove_section(body: str, header: re.Pattern[str]) -> tuple[str, str | None]:
    span = _section_span(body, header)
    if span is None:
        return body, None
    start, end = span
    return body[:start] + body[end:], body[start:end]


def _remove_all_sections(body: str, header: re.Pattern[str]) -> str:
    while header.search(body):
        body, _ = _remove_section(body, header)
    return body


def _b64decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")


def _restore_provider(body: str) -> str:
    managed = MANAGED_PROVIDER_RE.search(body)
    if managed:
        provider = PROVIDER_RE.search(body, managed.end())
        if provider is not None:
            next_section = SECTION_HEADER_RE.search(body, provider.end())
            end = next_section.start() if next_section else len(body)
            updated = body[:managed.start()] + body[end:]
        else:
            updated = body[:managed.start()] + body[managed.end():]
        previous = managed.group(1)
        if previous != "absent":
            restored = _b64decode(previous).rstrip("\r\n")
            updated = updated.rstrip("\r\n") + "\n\n" + restored
        return updated

    # Also clean a manually copied block from the old release.
    body, _ = _remove_section(body, PROVIDER_RE)
    return body


def _restore_line(
    body: str,
    managed: re.Pattern[str],
    current: re.Pattern[str],
    *,
    remove_unmanaged: bool = True,
) -> str:
    match = managed.search(body)
    if match is None:
        return current.sub("", body) if remove_unmanaged else body

    previous = match.group(1)
    updated = body[:match.start()] + body[match.end():]
    if previous != "absent":
        restored = _b64decode(previous).rstrip("\r\n")
        updated = restored + "\n" + updated.lstrip("\r\n")
    return updated


def _write_config(path: Path, content: str) -> None:
    newline = "\r\n" if os.name == "nt" else "\n"
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.strip("\n")
    if normalized:
        normalized += "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(normalized.replace("\n", newline))
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def main() -> int:
    path = config_path()
    if not path.exists():
        print(f"[OK] 未找到 Codex 配置，无需撤销：{path}")
        return 0

    try:
        current = path.read_text(encoding="utf-8-sig")
        updated = current.replace("\r\n", "\n").replace("\r", "\n")
        updated = _restore_provider(updated)
        updated = _remove_all_sections(updated, LEGACY_OPENAI_PROVIDER_RE)
        updated = _restore_line(
            updated,
            MANAGED_MODEL_PROVIDER_RE,
            MODEL_PROVIDER_RE,
            remove_unmanaged=False,
        )
        updated = _restore_line(updated, MANAGED_BASE_RE, BASE_LINE_RE)
        # A manually copied openai-proxy setting should also be removed.
        updated = PROXY_MODEL_PROVIDER_RE.sub("", updated)
        updated = updated.strip("\n")

        if updated != current.replace("\r\n", "\n").replace("\r", "\n").strip("\n"):
            _write_config(path, updated)
            print(f"[OK] 已撤销代理配置：{path}")
        else:
            print(f"[OK] 配置中没有代理配置，无需撤销：{path}")
    except (OSError, UnicodeError, ValueError) as error:
        print(f"[ERROR] 无法更新 Codex 配置：{error}", file=sys.stderr)
        return 1

    print("如需恢复官方通道，请重启 Codex 并新建对话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
