"""Choose how the proxy reaches the remote BPS service."""

from __future__ import annotations

import logging
import os
from urllib import error, parse, request

MODES = frozenset({"auto", "system", "direct", "proxy"})
log = logging.getLogger("bps_proxy")


def mode() -> str:
    default = "system"
    value = os.environ.get("BPS_UPSTREAM_MODE", default).strip().lower()
    if value not in MODES:
        raise ValueError(
            f"BPS_UPSTREAM_MODE must be one of {', '.join(sorted(MODES))}"
        )
    return value


def _proxy_summary() -> str:
    proxies = request.getproxies()
    if not proxies:
        return "none"
    values = []
    for scheme, value in sorted(proxies.items()):
        try:
            parsed = parse.urlsplit(value)
            host = parsed.hostname or "?"
            port = f":{parsed.port}" if parsed.port else ""
            values.append(f"{scheme}={parsed.scheme or 'proxy'}://{host}{port}")
        except (TypeError, ValueError):
            values.append(f"{scheme}=invalid")
    return ",".join(values) or "none"


def description() -> str:
    selected = mode()
    if selected == "auto":
        return f"auto (direct/TUN first, system proxy fallback; system proxies={_proxy_summary()})"
    if selected == "direct":
        return "direct (system/TUN routing)"
    if selected == "proxy":
        proxy = os.environ.get("BPS_UPSTREAM_PROXY", "").strip()
        return "proxy (configured)" if proxy else "proxy (missing BPS_UPSTREAM_PROXY)"
    return f"system (urllib proxies={_proxy_summary()})"


def _direct_opener():
    return request.build_opener(request.ProxyHandler({}))


def _system_opener():
    return request.build_opener()


def _proxy_opener(proxy: str):
    return request.build_opener(
        request.ProxyHandler({"http": proxy, "https": proxy})
    )


def open_url(req, *, timeout: float):
    selected = mode()
    if selected == "system":
        return _system_opener().open(req, timeout=timeout)
    if selected == "direct":
        return _direct_opener().open(req, timeout=timeout)
    if selected == "proxy":
        proxy = os.environ.get("BPS_UPSTREAM_PROXY", "").strip()
        if not proxy:
            raise OSError(
                "BPS_UPSTREAM_MODE=proxy requires BPS_UPSTREAM_PROXY, "
                "for example http://127.0.0.1:7890"
            )
        return _proxy_opener(proxy).open(req, timeout=timeout)

    try:
        return _direct_opener().open(req, timeout=timeout)
    except error.HTTPError as direct_error:
        if direct_error.code not in {502, 503, 504}:
            raise
        log.warning(
            "direct upstream gateway failed; trying system proxy status=%s",
            direct_error.code,
        )
        try:
            direct_error.close()
        except Exception:
            pass
        return _system_opener().open(req, timeout=timeout)
    except (error.URLError, OSError) as direct_error:
        log.warning(
            "direct upstream route failed; trying system proxy "
            "exception_type=%s reason_type=%s",
            type(direct_error).__name__,
            type(getattr(direct_error, "reason", None)).__name__,
        )
        return _system_opener().open(req, timeout=timeout)
