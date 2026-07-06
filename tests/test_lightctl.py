import unittest
from unittest.mock import patch

import lightctl


class FakeClient:
    def __init__(self):
        self.payloads = []

    def post_state(self, payload):
        self.payloads.append(payload)


class LightCtlTests(unittest.TestCase):
    def test_normalizes_controller_host(self):
        client = lightctl.LightClient("10.27.27.110")
        self.assertEqual(client.state_url, "http://10.27.27.110/json/state")

    def test_exposes_wled_json_endpoint_urls(self):
        client = lightctl.LightClient("10.27.27.110")

        self.assertEqual(client.json_url, "http://10.27.27.110/json")
        self.assertEqual(client.info_url, "http://10.27.27.110/json/info")
        self.assertEqual(client.effects_url, "http://10.27.27.110/json/eff")
        self.assertEqual(client.palettes_url, "http://10.27.27.110/json/pal")
        self.assertEqual(client.nodes_url, "http://10.27.27.110/json/nodes")
        self.assertEqual(client.live_url, "http://10.27.27.110/json/live")
        self.assertEqual(client.config_url, "http://10.27.27.110/json/cfg")
        self.assertEqual(client.fxdata_url, "http://10.27.27.110/json/fxdata")
        self.assertEqual(client.networks_url, "http://10.27.27.110/json/net")

    def test_device_snapshot_combines_current_wled_data(self):
        class SnapshotClient(lightctl.LightClient):
            def get_json(self):
                return {
                    "state": {"on": True, "bri": 77},
                    "info": {"name": "WLED-Gledopto"},
                    "effects": ["Solid", "Rainbow"],
                    "palettes": ["Default", "Party"],
                }

            def get_config(self):
                return {"light": {"nl": {"dur": 60}}}

            def get_fxdata(self):
                return ["", "speed"]

            def get_networks(self):
                return {"networks": []}

        snapshot = SnapshotClient("10.27.27.110").get_device_snapshot()

        self.assertEqual(snapshot["state"]["bri"], 77)
        self.assertEqual(snapshot["info"]["name"], "WLED-Gledopto")
        self.assertEqual(snapshot["effects"], ["Solid", "Rainbow"])
        self.assertEqual(snapshot["config"]["light"]["nl"]["dur"], 60)

    def test_color_payload_uses_rgbw_segment_array(self):
        self.assertEqual(
            lightctl.color_payload(255, 100, 0, 255),
            {"seg": [{"col": [[255, 100, 0, 255]]}]},
        )

    def test_values_are_clamped_to_wled_byte_range(self):
        self.assertEqual(lightctl.brightness_payload(300), {"bri": 255})
        self.assertEqual(lightctl.color_payload(-1, 12, 999, 1), {"seg": [{"col": [[0, 12, 255, 1]]}]})

    def test_reactive_controller_sends_color_on_beat(self):
        fake = FakeClient()
        controller = lightctl.ReactiveMode(
            fake,
            palette=[(255, 0, 0, 0), (0, 0, 255, 0)],
            min_interval=0,
        )

        controller.handle_beat(0.8)
        controller.handle_beat(0.9)

        self.assertEqual(
            fake.payloads,
            [
                {"on": True, "bri": 204, "seg": [{"col": [[255, 0, 0, 0]], "fx": 2, "sx": 168}]},
                {"on": True, "bri": 229, "seg": [{"col": [[0, 0, 255, 0]], "fx": 8, "sx": 179}]},
            ],
        )

    def test_reactive_beat_payload_combines_color_brightness_and_effect(self):
        self.assertEqual(
            lightctl.reactive_beat_payload((0, 50, 255, 0), 212, 9, 190),
            {"on": True, "bri": 212, "seg": [{"col": [[0, 50, 255, 0]], "fx": 9, "sx": 190}]},
        )

    def test_scene_payload_combines_power_brightness_and_color(self):
        self.assertEqual(
            lightctl.scene_payload("night"),
            {"on": True, "bri": 25, "seg": [{"col": [[255, 70, 0, 0]]}]},
        )

    def test_strobe_like_effects_are_rejected(self):
        with self.assertRaises(ValueError):
            lightctl.effect_payload(1)

        with self.assertRaises(ValueError):
            lightctl.effect_payload(23)

        with self.assertRaises(ValueError):
            lightctl.effect_payload(31)

    def test_chase_and_rainbow_effects_are_allowed(self):
        self.assertEqual(lightctl.effect_payload(28, 170), {"seg": [{"fx": 28, "sx": 170}]})
        self.assertEqual(lightctl.effect_payload(30, 180), {"seg": [{"fx": 30, "sx": 180}]})

    def test_resolves_default_input_samplerate_from_sounddevice(self):
        class FakeSoundDevice:
            @staticmethod
            def query_devices(device=None, kind=None):
                return {"default_samplerate": 48000.0}

        self.assertEqual(lightctl.resolve_input_samplerate(FakeSoundDevice, None, None), 48000)

    def test_explicit_samplerate_wins(self):
        class FakeSoundDevice:
            @staticmethod
            def query_devices(device=None, kind=None):
                raise AssertionError("should not query when explicit sample rate is provided")

        self.assertEqual(lightctl.resolve_input_samplerate(FakeSoundDevice, None, 22050), 22050)


class RequestRetryTests(unittest.TestCase):
    """WLED's ESP-based HTTP server can reset the connection mid-response
    (e.g. while rebooting after a restart command). Regression coverage for
    a bug where that raw socket error bypassed retry entirely and wasn't
    wrapped into the RuntimeError that CLI/GUI/MCP restart handlers rely on
    to distinguish "device unreachable" from other failures."""

    def setUp(self):
        self.client = lightctl.LightClient("10.27.27.110", timeout=0.01)
        self.sleep_patch = patch("lightctl.time.sleep")
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()

    def test_connection_reset_mid_response_is_retried_then_wrapped(self):
        with patch("lightctl.urllib.request.urlopen", side_effect=ConnectionResetError("reset by peer")) as mock_urlopen:
            with self.assertRaises(RuntimeError):
                self.client.get_state()
        self.assertEqual(mock_urlopen.call_count, 3)

    def test_exhausted_url_error_retries_raise_runtime_error(self):
        with patch(
            "lightctl.urllib.request.urlopen",
            side_effect=lightctl.urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.get_state()

    def test_success_after_transient_reset_returns_response(self):
        import io

        good_response = io.BytesIO(b'{"on": true}')
        with patch(
            "lightctl.urllib.request.urlopen",
            side_effect=[ConnectionResetError("reset by peer"), good_response],
        ):
            with self.client._request_with_retry(
                lightctl.urllib.request.Request(self.client.state_url)
            ) as response:
                self.assertEqual(response.read(), b'{"on": true}')


if __name__ == "__main__":
    unittest.main()
