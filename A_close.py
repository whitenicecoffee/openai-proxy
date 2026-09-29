"""One-click removal of the Codex proxy configuration for Windows."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

OPENAI_BASE_URL_RE = re.compile(
    r"(?m)^[ \t]*openai_base_url[ \t]*=.*(?:\r?\n|$)"
)


def config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def main() -> int:
    path = config_path()
    if not path.exists():
        print(f"[OK] 未找到 Codex 配置，无需撤销：{path}")
        return 0

    try:
        current = path.read_text(encoding="utf-8-sig")
        updated = OPENAI_BASE_URL_RE.sub("", current)
        if updated != current:
            newline = "\r\n" if os.name == "nt" else "\n"
            normalized = updated.replace("\r\n", "\n").replace("\r", "\n")
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="", dir=path.parent, delete=False
            ) as temporary:
                temporary.write(normalized.replace("\n", newline))
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
            print(f"[OK] 已撤销代理配置：{path}")
        else:
            print(f"[OK] 配置中没有 openai_base_url，无需撤销：{path}")
    except (OSError, UnicodeError) as error:
        print(f"[ERROR] 无法更新 Codex 配置：{error}", file=sys.stderr)
        return 1

    print("如需恢复官方通道，请重启 Codex 并新建对话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
