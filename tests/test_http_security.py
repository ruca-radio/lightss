"""Request-origin hardening for the GUI server.

The GUI listens on the LAN and has no login, so a random web page must not be
able to drive the lights. These tests pin the two independent defences:

* same-origin (CSRF) enforcement for every mutating endpoint, including the
  ones that used to have no check at all, and for the "simple request" content
  types that skip a CORS preflight;
* Host header validation, which is what stops DNS rebinding from turning the
  attacker's own domain into a same-origin request.
"""

from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import light_gui


class _ServerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.state = MagicMock()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), light_gui.make_handler(self.state)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_address[1]
        self.host = f"127.0.0.1:{self.port}"

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict | None]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        sent = {"Host": self.host}
        sent.update(headers or {})
        payload = None if body is None else json.dumps(body).encode("utf-8")
        conn.request(method, path, body=payload, headers=sent)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        return response.status, parsed

    def evil_origin_post(
        self,
        path: str = "/api/action",
        *,
        content_type: str = "text/plain",
        body: dict | None = None,
    ) -> tuple[int, dict | None]:
        return self.request(
            "POST",
            path,
            body=body if body is not None else {"action": "off"},
            headers={"Origin": "http://evil.example", "Content-Type": content_type},
        )


class CrossOriginPostTest(_ServerFixture):
    """Browsers attach Origin to cross-origin POSTs; mismatch must be refused."""

    def test_simple_content_type_post_cannot_drive_the_lights(self) -> None:
        # text/plain avoids a CORS preflight, so the request really is sent.
        status, payload = self.evil_origin_post(content_type="text/plain")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "Cross-origin request denied.")
        self.state.client.post_state.assert_not_called()

    def test_endpoint_without_its_own_guard_is_covered(self) -> None:
        # /api/settings previously had no origin check at all.
        status, _ = self.request(
            "POST",
            "/api/settings",
            body={"anything": 1},
            headers={"Origin": "http://evil.example", "Content-Type": "application/json"},
        )
        self.assertEqual(status, 403)

    def test_null_origin_is_refused(self) -> None:
        status, _ = self.request(
            "POST",
            "/api/action",
            body={"action": "off"},
            headers={"Origin": "null", "Content-Type": "application/json"},
        )
        self.assertEqual(status, 403)

    def test_origin_matching_a_different_port_is_refused(self) -> None:
        status, _ = self.request(
            "POST",
            "/api/action",
            body={"action": "off"},
            headers={
                "Origin": f"http://127.0.0.1:{self.port + 1}",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 403)

    def test_same_origin_post_is_allowed(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/action",
            body={"action": "off"},
            headers={
                "Origin": f"http://{self.host}",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_post_without_origin_is_allowed_for_non_browser_clients(self) -> None:
        # The tray, desktop bridge and curl send no Origin; there is no ambient
        # browser credential for an attacker to ride on.
        status, payload = self.request(
            "POST",
            "/api/action",
            body={"action": "off"},
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_plain_form_post_is_refused_for_api_paths(self) -> None:
        # An HTML <form> cannot set application/json without a preflight.
        status, _ = self.request(
            "POST",
            "/api/action",
            body={"action": "off"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 400)


class HostHeaderTest(_ServerFixture):
    """DNS rebinding makes an attacker domain same-origin; Host must be ours."""

    def test_foreign_host_is_refused_on_post(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/action",
            body={"action": "off"},
            headers={
                "Host": f"evil.example:{self.port}",
                "Origin": f"http://evil.example:{self.port}",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "Untrusted Host header.")
        self.state.client.post_state.assert_not_called()

    def test_foreign_host_is_refused_on_get(self) -> None:
        status, _ = self.request("GET", "/api/state", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_foreign_host_is_refused_on_head(self) -> None:
        status, _ = self.request("HEAD", "/api/state", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_ip_literal_host_is_accepted(self) -> None:
        status, _ = self.request(
            "POST",
            "/api/action",
            body={"action": "off"},
            headers={
                "Host": f"10.27.27.50:{self.port}",
                "Origin": f"http://10.27.27.50:{self.port}",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 200)

    def test_localhost_host_is_accepted(self) -> None:
        status, _ = self.request(
            "GET", "/api/state", headers={"Host": f"localhost:{self.port}"}
        )
        self.assertNotEqual(status, 403)

    def test_allowed_hosts_env_admits_an_explicit_hostname(self) -> None:
        with patch.dict("os.environ", {"LIGHTSS_ALLOWED_HOSTS": "bedroom.local"}):
            self.assertTrue(light_gui.host_header_allowed("bedroom.local:8123"))
            self.assertFalse(light_gui.host_header_allowed("evil.example:8123"))

    def test_host_helper_handles_ports_and_ipv6(self) -> None:
        self.assertEqual(light_gui.host_header_name("10.0.0.5:8123"), "10.0.0.5")
        self.assertEqual(light_gui.host_header_name("[::1]:8123"), "::1")
        self.assertEqual(light_gui.host_header_name(""), "")
        self.assertTrue(light_gui.host_header_allowed("[::1]:8123"))
        self.assertFalse(light_gui.host_header_allowed(""))


class ForwardedHeaderTrustTest(_ServerFixture):
    """X-Forwarded-* is client-controlled unless a proxy is declared."""

    def _login_url(self, **headers: str) -> str:
        with patch.object(light_gui, "_audio_player_enabled", return_value=True):
            status, payload = self.request(
                "GET", "/api/player/apple/session", headers=headers
            )
        self.assertEqual(status, 200)
        return payload["login_url"]

    def test_forwarded_host_is_ignored_by_default(self) -> None:
        # public_base_url rewrites a loopback Host to the LAN IP so a phone can
        # scan the QR code; what matters is that the spoofed header is dropped.
        url = self._login_url(**{"X-Forwarded-Host": "evil.example"})
        self.assertNotIn("evil.example", url)
        self.assertIn(f":{self.port}/apple-login", url)

    def test_forwarded_host_is_honoured_when_a_proxy_is_declared(self) -> None:
        with patch.dict("os.environ", {"LIGHTSS_TRUST_PROXY": "1"}):
            url = self._login_url(**{"X-Forwarded-Host": "proxy.example"})
        self.assertIn("proxy.example", url)


if __name__ == "__main__":
    unittest.main()
