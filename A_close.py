"""One-click removal of the Codex proxy configuration for Windows."""

from __future__ import annotations

import base64
import os
import re
import sys
import tempfile
from pathlib import Path

OPENAI_BASE_URL_RE = re.compile(
    r"(?m)^[ \t]*openai_base_url[ \t]*=[ \t]*(?P<value>.*?)(?:\r?\n|$)"
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
SECTION_HEADER_RE = re.compile(
    r"(?m)^[ \t]*\[[^]\r\n]+\][ \t]*(?:#.*)?(?:\r?\n|$)"
)
OPENAI_PROVIDER_RE = re.compile(
    r"(?m)^[ \t]*\[model_providers\.openai\][ \t]*(?:#.*)?(?:\r?\n|$)"
)
MANAGED_SUPPORTS_RE = re.compile(
    r"(?m)^[ \t]*# bps-proxy: managed supports_websockets previous=(absent|true|false)[ \t]*(?:\r?\n)"
    r"^[ \t]*supports_websockets[ \t]*=[ \t]*false[ \t]*(?:#.*)?(?:\r?\n|$)"
)


def config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def _restore_managed_transport(body: str) -> str:
    def restore(match: re.Match[str]) -> str:
        previous = match.group(1)
        return "" if previous == "absent" else f"supports_websockets = {previous}\n"

    return MANAGED_SUPPORTS_RE.sub(restore, body)


def _remove_empty_openai_provider(body: str) -> str:
    provider = OPENAI_PROVIDER_RE.search(body)
    if provider is None:
        return body
    next_section = SECTION_HEADER_RE.search(body, provider.end())
    section_end = next_section.start() if next_section else len(body)
    if not body[provider.end():section_end].strip():
        return body[:provider.start()] + body[section_end:]
    return body


def main() -> int:
    path = config_path()
    if not path.exists():
        print(f"[OK] 未找到 Codex 配置，无需撤销：{path}")
        return 0

    try:
        current = path.read_text(encoding="utf-8-sig")
        normalized = current.replace("\r\n", "\n").replace("\r", "\n")
        managed_base = MANAGED_BASE_RE.search(normalized)
        if managed_base:
            previous_base = managed_base.group(1)
            without_managed = (
                normalized[:managed_base.start()] + normalized[managed_base.end():]
            )
            updated = OPENAI_BASE_URL_RE.sub("", without_managed)
            if previous_base != "absent":
                padding = "=" * (-len(previous_base) % 4)
                previous_line = base64.urlsafe_b64decode(
                    previous_base + padding
                ).decode("utf-8")
                updated = previous_line.rstrip("\r\n") + "\n" + updated.lstrip("\r\n")
        else:
            updated = OPENAI_BASE_URL_RE.sub("", normalized)

        managed_provider = MANAGED_MODEL_PROVIDER_RE.search(updated)
        if managed_provider:
            previous_provider = managed_provider.group(1)
            without_managed = (
                updated[:managed_provider.start()] + updated[managed_provider.end():]
            )
            updated = MODEL_PROVIDER_RE.sub("", without_managed)
            if previous_provider != "absent":
                padding = "=" * (-len(previous_provider) % 4)
                previous_line = base64.urlsafe_b64decode(
                    previous_provider + padding
                ).decode("utf-8")
                updated = previous_line.rstrip("\r\n") + "\n" + updated.lstrip("\r\n")

        updated = _restore_managed_transport(updated)
        updated = _remove_empty_openai_provider(updated)
        if updated != normalized:
            newline = "\r\n" if os.name == "nt" else "\n"
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="", dir=path.parent, delete=False
            ) as temporary:
                temporary.write(updated.replace("\n", newline))
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
            print(f"[OK] 已撤销代理配置：{path}")
        else:
            print(f"[OK] 配置中没有代理配置，无需撤销：{path}")
    except (OSError, UnicodeError) as error:
        print(f"[ERROR] 无法更新 Codex 配置：{error}", file=sys.stderr)
        return 1

    print("如需恢复官方通道，请重启 Codex 并新建对话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
