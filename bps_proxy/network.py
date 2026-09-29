"""Choose how the proxy reaches the remote BPS service.

In TUN mode, sockets should be opened directly so the system TUN policy owns
the route. An explicit HTTP/SOCKS environment proxy can still be selected when
the machine does not use TUN.
"""

from __future__ import annotations

import os
from urllib import request

MODES = frozenset({"system", "direct", "proxy"})


def mode() -> str:
    default = "direct" if os.name == "nt" else "system"
    value = os.environ.get("BPS_UPSTREAM_MODE", default).strip().lower()
    if value not in MODES:
        raise ValueError(
            f"BPS_UPSTREAM_MODE must be one of {', '.join(sorted(MODES))}"
        )
    return value


def description() -> str:
    selected = mode()
    if selected == "direct":
        return "direct (system/TUN routing)"
    if selected == "proxy":
        proxy = os.environ.get("BPS_UPSTREAM_PROXY", "").strip()
        return "proxy (configured)" if proxy else "proxy (missing BPS_UPSTREAM_PROXY)"
    return "system (urllib environment proxies)"


def open_url(req, *, timeout: float):
    selected = mode()
    if selected == "system":
        return request.urlopen(req, timeout=timeout)
    if selected == "direct":
        opener = request.build_opener(request.ProxyHandler({}))
        return opener.open(req, timeout=timeout)

    proxy = os.environ.get("BPS_UPSTREAM_PROXY", "").strip()
    if not proxy:
        raise OSError(
            "BPS_UPSTREAM_MODE=proxy requires BPS_UPSTREAM_PROXY, "
            "for example http://127.0.0.1:7890"
        )
    opener = request.build_opener(
        request.ProxyHandler({"http": proxy, "https": proxy})
    )
    return opener.open(req, timeout=timeout)
