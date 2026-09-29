import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from bps_proxy import network


class _Opener:
    def __init__(self, result, calls, name):
        self.result = result
        self.calls = calls
        self.name = name

    def open(self, request, timeout=None):
        self.calls.append(self.name)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class NetworkRouteTest(unittest.TestCase):
    def test_system_gateway_falls_back_to_direct_tun(self):
        calls = []
        with patch.dict(os.environ, {"BPS_UPSTREAM_MODE": "system"}, clear=False), \
             patch.object(network, "_system_opener", return_value=_Opener(
                 HTTPError("https://upstream.invalid", 502, "bad gateway", {}, None), calls, "system"
             )), \
             patch.object(network, "_direct_opener", return_value=_Opener("ok", calls, "direct")):
            self.assertEqual(network.open_url(object(), timeout=1), "ok")
        self.assertEqual(calls, ["system", "direct"])

    def test_system_auth_error_is_not_retried_directly(self):
        calls = []
        error = HTTPError("https://upstream.invalid", 401, "unauthorized", {}, None)
        with patch.dict(os.environ, {"BPS_UPSTREAM_MODE": "system"}, clear=False), \
             patch.object(network, "_system_opener", return_value=_Opener(error, calls, "system")), \
             patch.object(network, "_direct_opener", return_value=_Opener("unexpected", calls, "direct")):
            with self.assertRaises(HTTPError):
                network.open_url(object(), timeout=1)
        self.assertEqual(calls, ["system"])

    def test_system_connection_failure_falls_back_to_direct_tun(self):
        calls = []
        with patch.dict(os.environ, {"BPS_UPSTREAM_MODE": "system"}, clear=False), \
             patch.object(network, "_system_opener", return_value=_Opener(
                 URLError("proxy refused"), calls, "system"
             )), \
             patch.object(network, "_direct_opener", return_value=_Opener("ok", calls, "direct")):
            self.assertEqual(network.open_url(object(), timeout=1), "ok")
        self.assertEqual(calls, ["system", "direct"])

    def test_system_description_states_route_order(self):
        with patch.dict(os.environ, {"BPS_UPSTREAM_MODE": "system"}, clear=False), \
             patch.object(network.request, "getproxies", return_value={"https": "http://127.0.0.1:7897"}):
            value = network.description()
        self.assertIn("system first", value)
        self.assertIn("direct/TUN fallback", value)
        self.assertIn("https=http://127.0.0.1:7897", value)


if __name__ == "__main__":
    unittest.main()
