import unittest

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


if __name__ == "__main__":
    unittest.main()
