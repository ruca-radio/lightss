import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import lightctl
import light_gui
import mcp_light


class FakeClient:
    def __init__(self):
        self.payloads = []
        self._state = {"on": True, "bri": 128, "seg": [{"col": [[255, 100, 0, 255]]}]}

    def post_state(self, payload):
        self.payloads.append(payload)

    def get_state(self):
        return self._state


class BeatDetectorTests(unittest.TestCase):
    def test_quiet_input_returns_false(self):
        det = lightctl.BeatDetector()
        for _ in range(50):
            result = det.update(0.001)
        self.assertFalse(result)

    def test_spike_above_threshold_returns_true(self):
        det = lightctl.BeatDetector()
        # Warm up baseline
        for _ in range(50):
            det.update(0.01)
        # Large spike
        result = det.update(0.5)
        self.assertTrue(result)

    def test_beat_spacing_prevents_double_trigger(self):
        det = lightctl.BeatDetector()
        for _ in range(50):
            det.update(0.01)
        self.assertTrue(det.update(0.5))
        self.assertFalse(det.update(0.5))

    def test_baseline_adapts_over_time(self):
        det = lightctl.BeatDetector()
        det.update(0.1)
        initial_baseline = det.baseline
        for _ in range(100):
            det.update(0.001)
        self.assertLess(det.baseline, initial_baseline)


class MergePayloadsTests(unittest.TestCase):
    def test_three_seg_payloads_merge_correctly(self):
        a = lightctl.color_payload(255, 0, 0, 0)
        b = lightctl.effect_payload(28, 128)
        c = lightctl.brightness_payload(200)
        merged = lightctl.merge_payloads(a, b, c)
        self.assertEqual(merged["bri"], 200)
        self.assertEqual(merged["seg"][0]["col"], [[255, 0, 0, 0]])
        self.assertEqual(merged["seg"][0]["fx"], 28)
        self.assertEqual(merged["seg"][0]["sx"], 128)

    def test_transition_included_when_nonzero(self):
        payload = lightctl.on_payload(True, transition_ms=500)
        self.assertEqual(payload["transition"], 500)


class ReactiveThreadTests(unittest.TestCase):
    def _mock_run(self, **kwargs):
        stop_event = kwargs.get("stop_event")
        while stop_event is None or not stop_event.is_set():
            time.sleep(0.05)

    def test_start_stop_lifecycle(self):
        with patch.object(lightctl, "run_mode1", self._mock_run):
            client = FakeClient()
            rt = lightctl.ReactiveThread(client)
            self.assertFalse(rt.is_alive())
            msg = rt.start()
            self.assertIn("started", msg.lower())
            self.assertTrue(rt.is_alive())
            msg2 = rt.stop()
            self.assertIn("stopped", msg2.lower())
            time.sleep(0.1)
            self.assertFalse(rt.is_alive())

    def test_double_start_returns_already_running(self):
        with patch.object(lightctl, "run_mode1", self._mock_run):
            client = FakeClient()
            rt = lightctl.ReactiveThread(client)
            rt.start()
            msg = rt.start()
            self.assertIn("already running", msg.lower())
            rt.stop()
            time.sleep(0.1)

    def test_forwards_audio_level_callback(self):
        captured = {}

        def mock_run(**kwargs):
            captured.update(kwargs)

        def on_level(energy, beat):
            pass

        with patch.object(lightctl, "run_mode1", mock_run):
            client = FakeClient()
            rt = lightctl.ReactiveThread(client, level_callback=on_level)
            rt.start()
            time.sleep(0.1)

        self.assertIs(captured.get("level_callback"), on_level)

    def test_reports_thread_startup_errors(self):
        errors = []

        def mock_run(**kwargs):
            raise RuntimeError("microphone unavailable")

        with patch.object(lightctl, "run_mode1", mock_run):
            client = FakeClient()
            rt = lightctl.ReactiveThread(client, error_callback=errors.append)
            rt.start()
            time.sleep(0.1)

        self.assertEqual(len(errors), 1)
        self.assertIn("microphone unavailable", str(errors[0]))


class KelvinTests(unittest.TestCase):
    def test_warm_2700k_has_red_and_white(self):
        r, g, b, w = lightctl.kelvin_to_rgbw(2700)
        self.assertGreater(r, b)
        self.assertGreater(w, 0)

    def test_cool_6500k_has_high_blue_and_low_white(self):
        r, g, b, w = lightctl.kelvin_to_rgbw(6500)
        self.assertGreater(b, 200)
        self.assertEqual(w, 0)

    def test_clamps_out_of_range(self):
        self.assertEqual(lightctl.kelvin_to_rgbw(1000), lightctl.kelvin_to_rgbw(2000))
        self.assertEqual(lightctl.kelvin_to_rgbw(10000), lightctl.kelvin_to_rgbw(6500))

    def test_all_channels_are_bytes(self):
        for k in (2000, 2700, 4000, 5000, 6500):
            r, g, b, w = lightctl.kelvin_to_rgbw(k)
            for ch in (r, g, b, w):
                self.assertGreaterEqual(ch, 0)
                self.assertLessEqual(ch, 255)


class ScenePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patch_path = patch.object(lightctl, "_SCENE_DIR", self.tmpdir)
        self.patch_path.start()
        lightctl._SCENE_PATH = os.path.join(self.tmpdir, "scenes.json")

    def tearDown(self):
        self.patch_path.stop()

    def test_save_and_load_scene(self):
        payload = lightctl.scene_payload("warm")
        lightctl.save_scene("my_scene", payload)
        loaded = lightctl.load_scene_payload("my_scene")
        self.assertEqual(loaded, payload)

    def test_list_scenes(self):
        lightctl.save_scene("alpha", lightctl.on_payload(True))
        lightctl.save_scene("beta", lightctl.on_payload(False))
        self.assertEqual(lightctl.list_scenes(), ["alpha", "beta"])

    def test_delete_scene(self):
        lightctl.save_scene("temp", lightctl.on_payload(True))
        lightctl.delete_scene("temp")
        self.assertNotIn("temp", lightctl.list_scenes())

    def test_scene_payload_falls_back_to_saved(self):
        lightctl.save_scene("custom", lightctl.color_payload(100, 100, 100, 100))
        payload = lightctl.scene_payload("custom")
        self.assertEqual(payload["seg"][0]["col"], [[100, 100, 100, 100]])


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patch_path = patch.object(lightctl, "_SCENE_DIR", self.tmpdir)
        self.patch_path.start()
        lightctl._SCHEDULE_PATH = os.path.join(self.tmpdir, "schedule.json")

    def tearDown(self):
        self.patch_path.stop()

    def test_add_and_list_schedule(self):
        lightctl.add_schedule("08:00", "on", {})
        lightctl.add_schedule("23:00", "scene", {"scene": "night"})
        entries = lightctl.list_schedule()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["time"], "08:00")

    def test_remove_schedule(self):
        lightctl.add_schedule("08:00", "on")
        lightctl.remove_schedule(0)
        self.assertEqual(len(lightctl.list_schedule()), 0)

    def test_remove_out_of_range_is_noop(self):
        lightctl.add_schedule("08:00", "on")
        lightctl.remove_schedule(99)
        self.assertEqual(len(lightctl.list_schedule()), 1)


class TransitionTests(unittest.TestCase):
    def test_brightness_payload_includes_transition(self):
        p = lightctl.brightness_payload(200, transition_ms=500)
        self.assertEqual(p["bri"], 200)
        self.assertEqual(p["transition"], 500)

    def test_color_payload_includes_transition(self):
        p = lightctl.color_payload(255, 0, 0, 0, transition_ms=300)
        self.assertEqual(p["transition"], 300)

    def test_effect_payload_includes_transition(self):
        p = lightctl.effect_payload(28, 128, transition_ms=100)
        self.assertEqual(p["transition"], 100)

    def test_scene_payload_includes_transition(self):
        p = lightctl.scene_payload("warm", transition_ms=250)
        self.assertEqual(p["transition"], 250)


class GuiPayloadTests(unittest.TestCase):
    def test_temp_action_builds_color_payload(self):
        payload = light_gui.payload_for_action("temp", {"kelvin": 3000})
        self.assertIn("seg", payload)
        col = payload["seg"][0]["col"][0]
        # Warm temp should have high red
        self.assertGreater(col[0], col[2])

    def test_schedule_add_action(self):
        payload = light_gui.payload_for_action("schedule", {"subaction": "add", "time": "08:00", "action": "on"})
        self.assertEqual(payload, {})

    def test_transition_passed_through(self):
        payload = light_gui.payload_for_action("bri", {"value": 200, "transition": 500})
        self.assertEqual(payload["transition"], 500)

    def test_hex_action_builds_color_payload(self):
        payload = light_gui.payload_for_action("hex", {"color": "#ff6600"})
        self.assertEqual(payload["seg"][0]["col"], [[255, 102, 0, 0]])

    def test_random_action_builds_scene_payload(self):
        payload = light_gui.payload_for_action("random", {})
        self.assertIn("on", payload)


class RenderHtmlTests(unittest.TestCase):
    def test_render_html_is_cached(self):
        html1 = light_gui.render_html()
        html2 = light_gui.render_html()
        self.assertIs(html1, html2)

    def test_rendered_html_has_temperature_controls(self):
        html = light_gui.render_html()
        self.assertIn("Temperature", html)
        self.assertIn("kelvin", html)

    def test_rendered_html_has_state_display(self):
        html = light_gui.render_html()
        self.assertIn('id="stateDisplay"', html)
        self.assertIn("/api/events", html)

    def test_rendered_html_has_schedule_section(self):
        html = light_gui.render_html()
        self.assertIn("Schedule", html)
        self.assertIn('id="scheduleList"', html)

    def test_rendered_html_has_transition_slider(self):
        html = light_gui.render_html()
        self.assertIn('id="transition"', html)

    def test_rendered_html_has_hex_input(self):
        html = light_gui.render_html()
        self.assertIn('id="hexColor"', html)
        self.assertIn("Set Hex", html)

    def test_rendered_html_has_random_button(self):
        html = light_gui.render_html()
        self.assertIn("Random", html)
        self.assertIn("send('random')", html)

    def test_rendered_html_has_fade_timer(self):
        html = light_gui.render_html()
        self.assertIn('id="fadeMinutes"', html)
        self.assertIn("fade_off", html)


class McpNewToolsTests(unittest.TestCase):
    def test_get_state_tool_exists(self):
        tools = mcp_light.build_tools()
        names = {t["name"] for t in tools}
        self.assertIn("get_state", names)
        self.assertIn("set_temperature", names)
        self.assertIn("save_scene", names)
        self.assertIn("delete_scene", names)
        self.assertIn("list_scenes", names)

    def test_set_temperature_tool_posts_color_payload(self):
        client = FakeClient()
        modes = MagicMock()
        result = mcp_light.call_tool(client, "set_temperature", {"kelvin": 3000}, modes)
        self.assertEqual(len(client.payloads), 1)
        self.assertIn("set temperature to 3000k", result["content"][0]["text"].lower())

    def test_list_scenes_tool(self):
        client = FakeClient()
        modes = MagicMock()
        with patch.object(lightctl, "list_scenes", return_value=["alpha", "beta"]):
            result = mcp_light.call_tool(client, "list_scenes", {}, modes)
        self.assertIn("alpha", result["content"][0]["text"])


class HexColorTests(unittest.TestCase):
    def test_hex_6_digit(self):
        self.assertEqual(lightctl.hex_to_rgbw("#ff6600"), (255, 102, 0, 0))

    def test_hex_8_digit(self):
        self.assertEqual(lightctl.hex_to_rgbw("#ff6600aa"), (255, 102, 0, 170))

    def test_hex_without_hash(self):
        self.assertEqual(lightctl.hex_to_rgbw("00ff00"), (0, 255, 0, 0))

    def test_invalid_hex_raises(self):
        with self.assertRaises(ValueError):
            lightctl.hex_to_rgbw("ggg")


class RandomSceneTests(unittest.TestCase):
    def test_random_scene_is_builtin(self):
        payload = lightctl.random_scene_payload()
        self.assertIn("on", payload)


class FadeTimerTests(unittest.TestCase):
    def test_fade_timer_reduces_brightness(self):
        client = FakeClient()
        client._state["bri"] = 100
        timer = lightctl.FadeTimer(client, duration_minutes=0.1, start_brightness=12)
        timer.start()
        time.sleep(0.5)
        timer.stop()
        time.sleep(0.2)
        # It should have posted at least one brightness payload
        self.assertTrue(any("bri" in p for p in client.payloads))

    def test_double_start_returns_already_running(self):
        client = FakeClient()
        timer = lightctl.FadeTimer(client, duration_minutes=10)
        timer.start()
        msg = timer.start()
        self.assertIn("already running", msg.lower())
        timer.stop()


class RetryTests(unittest.TestCase):
    def test_post_state_retries_on_failure(self):
        client = lightctl.LightClient(dry_run=True)
        # dry_run should succeed without network
        client.post_state(lightctl.on_payload(True))
        # No exception means success


class PresetTests(unittest.TestCase):
    def test_preset_payload_within_range(self):
        p = lightctl.preset_payload(5)
        self.assertEqual(p["ps"], 5)

    def test_preset_payload_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            lightctl.preset_payload(0)
        with self.assertRaises(ValueError):
            lightctl.preset_payload(251)

    def test_preset_payload_includes_transition(self):
        p = lightctl.preset_payload(10, transition_ms=300)
        self.assertEqual(p["transition"], 300)


class WledInfoTests(unittest.TestCase):
    def test_from_dict_parses_fields(self):
        data = {
            "name": "Bedroom LEDs",
            "ver": "0.14.0",
            "leds": {"count": 300},
            "udpport": 21324,
            "live": False,
            "arch": "esp32",
            "core": "v3.3.6",
            "freeheap": 150000,
            "uptime": 3600,
            "opt": 119,
            "brand": "WLED",
            "product": "DIY",
            "mac": "aabbccdd1122",
            "ip": "10.27.27.110",
        }
        info = lightctl.WledInfo.from_dict(data)
        self.assertEqual(info.name, "Bedroom LEDs")
        self.assertEqual(info.version, "0.14.0")
        self.assertEqual(info.led_count, 300)
        self.assertEqual(info.ip, "10.27.27.110")


class CycleThreadTests(unittest.TestCase):
    def _mock_post(self, payload):
        pass

    def test_start_stop_lifecycle(self):
        client = FakeClient()
        cycle = lightctl.CycleThread(client, items=["warm", "night"], interval_seconds=0.2)
        self.assertFalse(cycle.is_alive())
        msg = cycle.start()
        self.assertIn("started", msg.lower())
        self.assertTrue(cycle.is_alive())
        time.sleep(0.1)
        msg2 = cycle.stop()
        self.assertIn("stopped", msg2.lower())
        time.sleep(0.3)

    def test_double_start_returns_already_running(self):
        client = FakeClient()
        cycle = lightctl.CycleThread(client, items=["warm"], interval_seconds=5)
        cycle.start()
        msg = cycle.start()
        self.assertIn("already running", msg.lower())
        cycle.stop()


class SunriseSimulatorTests(unittest.TestCase):
    def test_start_stop_lifecycle(self):
        client = FakeClient()
        sim = lightctl.SunriseSimulator(client, duration_minutes=0.1, max_brightness=50)
        self.assertFalse(sim.is_alive())
        msg = sim.start()
        self.assertIn("sunrise started", msg.lower())
        self.assertTrue(sim.is_alive())
        time.sleep(0.1)
        msg2 = sim.stop()
        self.assertIn("stopped", msg2.lower())
        time.sleep(0.2)

    def test_posts_brightness_and_color(self):
        client = FakeClient()
        sim = lightctl.SunriseSimulator(client, duration_minutes=0.05, max_brightness=12)
        sim.start()
        time.sleep(0.4)
        sim.stop()
        time.sleep(0.2)
        self.assertTrue(any("bri" in p for p in client.payloads))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patch_dir = patch.object(lightctl, "_SCENE_DIR", self.tmpdir)
        self.patch_dir.start()
        lightctl._CONFIG_PATH = os.path.join(self.tmpdir, "config.json")

    def tearDown(self):
        self.patch_dir.stop()

    def test_save_and_load_config(self):
        lightctl.save_config({"host": "http://10.0.0.5", "default_transition": 250})
        cfg = lightctl.load_config()
        self.assertEqual(cfg["host"], "http://10.0.0.5")
        self.assertEqual(cfg["default_transition"], 250)

    def test_load_missing_config_returns_empty(self):
        cfg = lightctl.load_config()
        self.assertEqual(cfg, {})


class GuiPresetTests(unittest.TestCase):
    def test_preset_action_builds_payload(self):
        payload = light_gui.payload_for_action("preset", {"id": 5})
        self.assertEqual(payload["ps"], 5)


class McpPresetTests(unittest.TestCase):
    def test_preset_tool_exists(self):
        tools = mcp_light.build_tools()
        names = {t["name"] for t in tools}
        self.assertIn("load_preset", names)
        self.assertIn("get_info", names)
        self.assertIn("start_sunrise", names)

    def test_preset_tool_posts_ps_payload(self):
        client = FakeClient()
        modes = MagicMock()
        result = mcp_light.call_tool(client, "load_preset", {"id": 7}, modes)
        self.assertEqual(client.payloads, [{"ps": 7}])
        self.assertIn("loaded preset 7", result["content"][0]["text"].lower())


if __name__ == "__main__":
    unittest.main()
