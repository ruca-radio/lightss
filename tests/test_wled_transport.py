"""Exercise the real sync/async bridge while replacing only the device boundary."""

import asyncio
import gc
import io
import threading
import unittest
from unittest.mock import patch

import lightctl


class DeviceBoundary:
    def __init__(self, host, request_timeout):
        self.host = host
        self.timeout = request_timeout
        self.calls = []
        self.closed = False
        self.cancelled = threading.Event()
        self.value = {"bri": 123, "seg": [{"id": 7, "c3": 29}], "AudioReactive": {"on": True}}
        self.error = None

    async def request(self, uri, method="GET"):
        self.calls.append((uri, method))
        if self.error:
            raise self.error
        return self.value

    async def close(self):
        self.closed = True


class WledTransportTests(unittest.TestCase):
    def adapter(self, **options):
        from wled_transport import WledHttpTransport
        devices = []

        def factory(host, request_timeout):
            device = DeviceBoundary(host, request_timeout)
            devices.append(device)
            return device

        adapter = WledHttpTransport("http://wled.local", options.get("timeout", 0.1), client_factory=options.get("factory", factory))
        self.addCleanup(adapter.close)
        return adapter, devices

    def test_returns_untouched_device_fields(self):
        adapter, devices = self.adapter()
        result = adapter.get_json("/json/state")
        self.assertEqual(result, devices[0].value)
        self.assertEqual(devices[0].calls, [("/json/state", "GET")])

    def test_reuses_one_session_for_catalog_reads(self):
        adapter, devices = self.adapter()
        adapter.get_json("/json/state")
        devices[0].value = ["Solid", "Rainbow"]
        self.assertEqual(adapter.get_json("/json/eff"), ["Solid", "Rainbow"])
        self.assertEqual(len(devices), 1)

    def test_maps_connection_failures_without_outer_retries(self):
        adapter, devices = self.adapter()
        adapter.get_json("/json/state")
        devices[0].error = OSError("disconnected")
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            adapter.get_json("/json/state")
        self.assertEqual(len(devices[0].calls), 2)

    def test_rejects_non_object_non_array_json(self):
        adapter, devices = self.adapter()
        adapter.get_json("/json/state")
        devices[0].value = True
        with self.assertRaisesRegex(RuntimeError, "Unexpected JSON root"):
            adapter.get_json("/json/state")

    def test_close_releases_session_and_worker(self):
        adapter, devices = self.adapter()
        adapter.get_json("/json/state")
        worker = adapter._thread
        adapter.close()
        self.assertTrue(devices[0].closed)
        self.assertFalse(worker.is_alive())
        adapter.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            adapter.get_json("/json/state")

    def test_close_releases_owned_session_when_client_close_fails(self):
        adapter, devices = self.adapter()
        adapter.get_json("/json/state")

        class SessionBoundary:
            closed = False

            async def close(self):
                self.closed = True

        device = devices[0]
        device.session = SessionBoundary()
        device._close_session = True

        async def failed_close():
            raise RuntimeError("socket close failed")

        device.close = failed_close
        adapter.close()
        self.assertTrue(device.session.closed)
        self.assertFalse(adapter._thread.is_alive())

    def test_close_releases_owned_session_when_client_close_stalls(self):
        adapter, devices = self.adapter()
        adapter.get_json("/json/state")

        class SessionBoundary:
            closed = False

            async def close(self):
                self.closed = True

        device = devices[0]
        device.session = SessionBoundary()
        device._close_session = True

        async def stalled_close():
            await asyncio.Event().wait()

        device.close = stalled_close
        adapter.close()
        self.assertTrue(device.session.closed)
        self.assertFalse(adapter._thread.is_alive())

    def test_timeout_cancels_underlying_request(self):
        devices = []

        class SlowBoundary(DeviceBoundary):
            async def request(self, uri, method="GET"):
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled.set()

        def factory(host, request_timeout):
            device = SlowBoundary(host, request_timeout)
            devices.append(device)
            return device

        adapter, _ = self.adapter(timeout=0.01, factory=factory)
        with self.assertRaisesRegex(RuntimeError, "[Tt]imeout|timed out"):
            adapter.get_json("/json/state")
        self.assertTrue(devices[0].cancelled.wait(1))

    def test_cancelled_timeout_keeps_session_reusable(self):
        adapter, devices = self.adapter()
        adapter.get_json("/json/state")
        request = devices[0].request

        async def slow(uri, method="GET"):
            await asyncio.Event().wait()

        devices[0].request = slow
        with self.assertRaisesRegex(RuntimeError, "[Tt]imeout"):
            adapter.get_json("/json/state")
        devices[0].request = request
        self.assertEqual(adapter.get_json("/json/state")["bri"], 123)

    def test_invalid_json_mapping_preserves_metadata_retry_signal(self):
        from wled import WLEDInvalidResponseError
        adapter, devices = self.adapter()
        adapter.get_json("/json/state")
        devices[0].error = WLEDInvalidResponseError("truncated")
        with self.assertRaisesRegex(RuntimeError, "Invalid JSON"):
            adapter.get_json("/json/fxdata")

    def test_client_collection_releases_transport(self):
        client = lightctl.LightClient("wled.local", use_python_wled=True, min_request_interval=0)
        with patch("wled.WLED", DeviceBoundary):
            client.get_state()
        worker = client._wled_transport._thread
        del client
        gc.collect()
        self.assertFalse(worker.is_alive())

    def test_fresh_observer_snapshot_serves_raw_state(self):
        from unittest.mock import Mock
        client = lightctl.LightClient("wled.local", use_python_wled=True)
        self.addCleanup(client.close)
        observer = Mock()
        observer.cached_json.return_value = {"state": {"seg": [{"id": 13}], "extra": "raw"}}
        client._wled_observer = observer
        self.assertEqual(client.get_state(), {"seg": [{"id": 13}], "extra": "raw"})
        self.assertIsNone(client._wled_transport)

    def test_post_clears_observer_snapshot_even_on_failure(self):
        from unittest.mock import Mock
        client = lightctl.LightClient("wled.local", use_python_wled=True, retries=1)
        self.addCleanup(client.close)
        observer = Mock()
        client._wled_observer = observer
        with patch("urllib.request.urlopen", side_effect=OSError("lost ack")):
            with self.assertRaisesRegex(RuntimeError, "lost ack"):
                client.post_state({"bri": 17})
        self.assertEqual(observer.invalidate.call_count, 2)

    def test_dry_run_never_starts_observer(self):
        client = lightctl.LightClient("wled.local", use_python_wled=True, dry_run=True)
        self.addCleanup(client.close)
        client.start_observing()
        self.assertIsNone(client._wled_observer)

    def test_closed_client_cannot_create_new_session(self):
        client = lightctl.LightClient("wled.local", use_python_wled=True)
        client.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            client.get_state()

    def test_read_waiting_for_close_cannot_create_new_transport(self):
        from wled_transport import WledHttpTransport
        client = lightctl.LightClient("wled.local", use_python_wled=True, min_request_interval=0)
        self.addCleanup(client.close)
        reached = threading.Event()
        proceed = threading.Event()
        errors = []

        def supports(host):
            reached.set()
            proceed.wait(1)
            return True

        def read():
            try:
                client.get_state()
            except RuntimeError as exc:
                errors.append(str(exc))

        with patch.object(WledHttpTransport, "supports_host", side_effect=supports), patch("wled.WLED", DeviceBoundary):
            worker = threading.Thread(target=read)
            worker.start()
            self.assertTrue(reached.wait(1))
            client.close()
            proceed.set()
            worker.join(1)
        self.assertEqual(errors, ["Light client is closed"])
        self.assertIsNone(client._wled_transport)

    def test_post_waiting_for_close_cannot_send_command(self):
        client = lightctl.LightClient("wled.local", use_python_wled=True)
        self.addCleanup(client.close)
        reached = threading.Event()
        proceed = threading.Event()
        errors = []

        def validate(payload):
            reached.set()
            proceed.wait(1)
            return payload

        def post():
            try:
                client.post_state({"bri": 17})
            except RuntimeError as exc:
                errors.append(str(exc))

        with patch.object(lightctl, "validate_wled_payload", side_effect=validate), patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"success":true}')) as network:
            worker = threading.Thread(target=post)
            worker.start()
            self.assertTrue(reached.wait(1))
            client.close()
            proceed.set()
            worker.join(1)
        self.assertEqual(errors, ["Light client is closed"])
        network.assert_not_called()

    def test_single_host_cli_closes_client_after_read_failure(self):
        from unittest.mock import Mock
        client = Mock()
        client.get_info.side_effect = RuntimeError("unreachable")
        with patch.object(lightctl, "LightClient", return_value=client) as factory:
            self.assertEqual(lightctl.main(["--host", "wled.local", "info"]), 1)
        self.assertTrue(factory.call_args.kwargs.get("use_python_wled"))
        client.close.assert_called_once()

    def test_fleet_cli_closes_clients_after_command(self):
        from unittest.mock import Mock
        import fleet
        lights = Mock()
        lights.channels.return_value = {}
        with patch.object(fleet.LightFleet, "from_config", return_value=lights):
            self.assertEqual(lightctl.main(["on"]), 0)
        lights.close.assert_called_once()

    def test_only_supports_plain_http_hosts_at_port_80(self):
        from wled_transport import WledHttpTransport
        for host in ("http://wled.local", "http://10.0.0.5:80"):
            self.assertTrue(WledHttpTransport.supports_host(host), host)
        for host in ("https://wled.local", "http://wled.local:8080", "http://wled.local/proxy", "http://user:pass@wled.local", "http://[::1]"):
            self.assertFalse(WledHttpTransport.supports_host(host), host)

    def test_opt_in_read_uses_upstream_json_adapter(self):
        from wled_transport import WledHttpTransport
        state = {"bri": 42, "seg": [{"id": 9}], "custom": True}
        client = lightctl.LightClient("wled.local", use_python_wled=True, min_request_interval=0)
        self.addCleanup(client.close)
        with patch.object(WledHttpTransport, "get_json", return_value=state) as read:
            self.assertEqual(client.get_state(), state)
        read.assert_called_once_with("/json/state")

    def test_custom_port_preserves_urllib_compatibility(self):
        client = lightctl.LightClient("http://wled.local:8080", use_python_wled=True)
        self.addCleanup(client.close)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"bri":17}')):
            self.assertEqual(client.get_state(), {"bri": 17})

    def test_dry_run_posts_never_start_upstream_transport(self):
        client = lightctl.LightClient("wled.local", use_python_wled=True, dry_run=True)
        self.addCleanup(client.close)
        with patch("urllib.request.urlopen") as network:
            self.assertIsNone(client.post_state({"bri": 17}))
        network.assert_not_called()
        self.assertIsNone(client._wled_transport)

    def test_writes_keep_validated_raw_urllib_payload(self):
        client = lightctl.LightClient("wled.local", use_python_wled=True)
        self.addCleanup(client.close)
        payload = {"seg": [{"id": 7, "c3": 31}], "tt": 2}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"success":true}')) as network:
            self.assertEqual(client.post_state(payload), {"success": True})
        self.assertEqual(network.call_args.args[0].data, b'{"seg":[{"id":7,"c3":31}],"tt":2}')
        self.assertIsNone(client._wled_transport)


if __name__ == "__main__":
    unittest.main()
