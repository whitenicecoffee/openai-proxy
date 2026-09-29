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


def _windows_registry_proxies() -> dict[str, str]:
    if os.name != "nt":
        return {}
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return {}
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except (ImportError, OSError, TypeError):
        return {}
    if not isinstance(server, str) or not server.strip():
        return {}
    values: dict[str, str] = {}
    for item in server.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            scheme, value = item.split("=", 1)
            scheme = scheme.strip().lower()
        else:
            scheme, value = "http", item
        value = value.strip()
        if scheme in {"http", "https", "all"} and value:
            values[scheme] = value if "://" in value else f"http://{value}"
    if "http" in values and "https" not in values:
        values["https"] = values["http"]
    if "https" in values and "http" not in values:
        values["http"] = values["https"]
    return values


def _effective_proxies() -> dict[str, str]:
    proxies = request.getproxies()
    if any(key.lower() in {"http", "https", "all"} for key in proxies):
        return proxies
    return _windows_registry_proxies()


def _proxy_summary() -> str:
    proxies = _effective_proxies()
    if not proxies:
        return "none"
    values = []
    for scheme, value in sorted(proxies.items()):
        # urllib exposes NO_PROXY/no_proxy as a pseudo-scheme named "no".
        # It is a bypass list, not an outbound proxy.
        if scheme.lower() in {"no", "no_proxy"}:
            continue
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
    return f"system first, direct/TUN fallback (urllib proxies={_proxy_summary()})"


def _direct_opener():
    return request.build_opener(request.ProxyHandler({}))


class _SystemOpener:
    def open(self, req, *, timeout: float):
        # Keep the default urllib entry point so callers and tests can observe
        # the same environment-proxy behavior as a normal urllib request.
        return request.urlopen(req, timeout=timeout)


class _ConfiguredSystemOpener:
    def __init__(self, proxies: dict[str, str]):
        self._opener = request.build_opener(request.ProxyHandler(proxies))

    def open(self, req, *, timeout: float):
        return self._opener.open(req, timeout=timeout)


def _system_opener():
    proxies = _effective_proxies()
    if any(key.lower() in {"http", "https", "all"} for key in proxies) and not any(
        key.lower() in {"http", "https", "all"} for key in request.getproxies()
    ):
        return _ConfiguredSystemOpener(proxies)
    return _SystemOpener()


def _proxy_opener(proxy: str):
    return request.build_opener(
        request.ProxyHandler({"http": proxy, "https": proxy})
    )


_GATEWAY_STATUSES = frozenset({502, 503, 504})


def _close_error(exc) -> None:
    try:
        exc.close()
    except Exception:
        pass


def _system_then_direct(req, *, timeout: float):
    """Prefer the configured system proxy, then let TUN routing recover it.

    A chained proxy can return a gateway error even though the host is reachable
    through the OS/TUN route. Retrying only those transient gateway failures on a
    direct opener keeps the system proxy as the first choice without forcing a
    bypass for normal traffic.
    """
    try:
        return _system_opener().open(req, timeout=timeout)
    except error.HTTPError as system_error:
        if system_error.code not in _GATEWAY_STATUSES:
            raise
        log.warning(
            "system proxy gateway failed; trying direct/TUN fallback status=%s",
            system_error.code,
        )
        _close_error(system_error)
        return _direct_opener().open(req, timeout=timeout)
    except (error.URLError, OSError) as system_error:
        log.warning(
            "system proxy route failed; trying direct/TUN fallback "
            "exception_type=%s reason_type=%s",
            type(system_error).__name__,
            type(getattr(system_error, "reason", None)).__name__,
        )
        return _direct_opener().open(req, timeout=timeout)


def open_url(req, *, timeout: float):
    selected = mode()
    if selected == "system":
        return _system_then_direct(req, timeout=timeout)
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
        if direct_error.code not in _GATEWAY_STATUSES:
            raise
        log.warning(
            "direct upstream gateway failed; trying system proxy status=%s",
            direct_error.code,
        )
        _close_error(direct_error)
        return _system_opener().open(req, timeout=timeout)
    except (error.URLError, OSError) as direct_error:
        log.warning(
            "direct upstream route failed; trying system proxy "
            "exception_type=%s reason_type=%s",
            type(direct_error).__name__,
            type(getattr(direct_error, "reason", None)).__name__,
        )
        return _system_opener().open(req, timeout=timeout)
