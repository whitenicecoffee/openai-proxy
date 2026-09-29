"""One-click Codex proxy configuration for Windows."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROXY_BASE_URL = "http://127.0.0.1:8787/v1"
OPENAI_BASE_URL_RE = re.compile(
    r"(?m)^[ \t]*openai_base_url[ \t]*=.*(?:\r?\n|$)"
)


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


def main() -> int:
    path = config_path()
    try:
        current = read_config(path)
        body = OPENAI_BASE_URL_RE.sub("", current)
        updated = f'openai_base_url = "{PROXY_BASE_URL}"\n'
        if body:
            updated += body.lstrip("\r\n")
        write_config(path, updated)
    except (OSError, UnicodeError) as error:
        print(f"[ERROR] 无法写入 Codex 配置：{error}", file=sys.stderr)
        return 1

    print(f"[OK] 已完成代理配置：{path}")
    print(f"[OK] openai_base_url = {PROXY_BASE_URL}")
    print("请重启 Codex 并新建对话使配置生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
