import unittest
from unittest.mock import patch

import light_gui
import mcp_light


class FakeClient:
    def __init__(self):
        self.payloads = []

    def post_state(self, payload):
        self.payloads.append(payload)


class SurfaceTests(unittest.TestCase):
    def test_gui_action_builds_color_payload(self):
        payload = light_gui.payload_for_action(
            "color",
            {"red": 255, "green": 100, "blue": 0, "white": 255},
        )

        self.assertEqual(payload, {"seg": [{"col": [[255, 100, 0, 255]]}]})

    def test_gui_action_builds_combined_audio_reactive_payload(self):
        payload = light_gui.payload_for_action(
            "rgbw_bri",
            {"red": 0, "green": 50, "blue": 255, "white": 0, "brightness": 212},
        )

        self.assertEqual(payload, {"bri": 212, "seg": [{"col": [[0, 50, 255, 0]]}]})

    def test_gui_action_builds_beat_effect_payload(self):
        payload = light_gui.payload_for_action(
            "beat",
            {
                "red": 0,
                "green": 50,
                "blue": 255,
                "white": 0,
                "brightness": 212,
                "effect": 9,
                "speed": 190,
            },
        )

        self.assertEqual(payload, {"on": True, "bri": 212, "seg": [{"col": [[0, 50, 255, 0]], "fx": 9, "sx": 190}]})

    def test_gui_action_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            light_gui.payload_for_action("nope", {})

    def test_mcp_exposes_expected_tools(self):
        tool_names = {tool["name"] for tool in mcp_light.build_tools()}

        self.assertIn("light_on", tool_names)
        self.assertIn("light_off", tool_names)
        self.assertIn("set_brightness", tool_names)
        self.assertIn("set_color", tool_names)
        self.assertIn("set_effect", tool_names)

    def test_mcp_effect_tool_only_exposes_safe_effect_ids(self):
        effect_tool = next(tool for tool in mcp_light.build_tools() if tool["name"] == "set_effect")

        enum = effect_tool["inputSchema"]["properties"]["effect"]["enum"]
        self.assertIn(28, enum)
        self.assertIn(30, enum)
        self.assertNotIn(1, enum)
        self.assertNotIn(23, enum)

    def test_gui_rendered_effects_include_chase_but_not_strobe(self):
        html = light_gui.render_html()

        self.assertIn("Chase Rainbow", html)
        self.assertIn("Rainbow Runner", html)
        self.assertNotIn("Strobe", html)

    def test_gui_rendered_html_has_vu_meter(self):
        html = light_gui.render_html()

        self.assertIn('id="vuMeter"', html)
        self.assertIn('id="vuFill"', html)
        self.assertIn('id="beatLamp"', html)

    def test_gui_rendered_html_has_ai_prompt(self):
        html = light_gui.render_html()

        self.assertIn('id="aiInput"', html)
        self.assertIn('id="aiReply"', html)
        self.assertIn("askAI()", html)

    def test_ai_request_uses_structured_actions(self):
        request = light_gui.build_openai_request(
            "make it rainbow",
            "gpt-test",
            {"title": "Midnight City", "artist": "M83", "status": "Playing"},
            {
                "state": {"on": True, "bri": 88, "seg": [{"fx": 9, "sx": 180, "pal": 6}]},
                "info": {"name": "WLED-Gledopto", "ver": "0.15.1", "leds": {"count": 1, "rgbw": True}},
                "effects": ["Solid", "Rainbow"],
                "palettes": ["Default", "Party"],
                "config": {"light": {"tr": {"dur": 7}}, "um": {"AudioReactive": {"enabled": False}}},
            },
        )

        self.assertEqual(request["model"], "gpt-test")
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertIn("response", request["text"]["format"]["schema"]["properties"])
        action_props = request["text"]["format"]["schema"]["properties"]["actions"]["items"]["properties"]
        self.assertIn("mode1_start", action_props["action"]["enum"])
        self.assertIn("mode1_stop", action_props["action"]["enum"])
        self.assertIn("confirmations", request["text"]["format"]["schema"]["properties"])
        self.assertIn(28, action_props["effect"]["enum"])
        self.assertIn("RGBW color channels", request["input"][0]["content"])
        self.assertIn("Background audio now playing: M83 - Midnight City (Playing)", request["input"][1]["content"])
        self.assertIn("Current WLED device snapshot:", request["input"][1]["content"])
        self.assertIn("WLED-Gledopto", request["input"][1]["content"])
        self.assertIn("AudioReactive", request["input"][1]["content"])

    def test_device_snapshot_prompt_sanitizes_network_config(self):
        text = light_gui.device_snapshot_text(
            {
                "state": {"on": True, "bri": 1},
                "info": {"name": "WLED-Gledopto", "ip": "10.27.27.110"},
                "effects": ["Solid"],
                "palettes": ["Default"],
                "config": {"nw": {"ins": [{"ssid": "SecretWifi"}]}, "um": {"AudioReactive": {"enabled": True}}},
            }
        )

        self.assertIn("WLED-Gledopto", text)
        self.assertIn("AudioReactive", text)
        self.assertNotIn("SecretWifi", text)

    def test_ai_request_defaults_to_gpt_5_2(self):
        request = light_gui.build_openai_request("make it warm", now_playing=None)

        self.assertEqual(request["model"], "gpt-5.2")

    def test_ai_request_exposes_full_desktop_action_surface(self):
        request = light_gui.build_openai_request("control everything")
        action_enum = set(
            request["text"]["format"]["schema"]["properties"]["actions"]["items"]["properties"]["action"]["enum"]
        )

        self.assertEqual(
            action_enum,
            {
                "on",
                "off",
                "brightness",
                "color",
                "effect",
                "scene",
                "temperature",
                "random",
                "preset",
                "playlist",
                "palette",
                "nightlight",
                "udp_sync",
                "native_audio_reactive",
                "segment_options",
                "mode1_start",
                "mode1_stop",
                "fade_off",
                "cycle_start",
                "cycle_stop",
                "sunrise_start",
                "sunrise_stop",
                "save_scene",
                "delete_scene",
                "schedule_add",
                "schedule_remove",
                "music_detect",
                "music_listen",
                "music_match",
            },
        )
        properties = request["text"]["format"]["schema"]["properties"]["actions"]["items"]["properties"]
        for field in ("preset_id", "minutes", "interval", "schedule_time", "schedule_action", "schedule_index"):
            self.assertIn(field, properties)

    def test_ai_system_prompt_contains_one_shot_action_context(self):
        request = light_gui.build_openai_request("make a wake up schedule")
        system = request["input"][0]["content"]

        self.assertIn("Available AI actions", system)
        self.assertIn("schedule_add", system)
        self.assertIn("music_listen", system)
        self.assertIn("One-shot examples", system)

    def test_parse_playerctl_metadata(self):
        metadata = light_gui.parse_playerctl_metadata("spotify\nM83\nMidnight City\nHurry Up\nPlaying\n")

        self.assertEqual(metadata["player"], "spotify")
        self.assertEqual(metadata["artist"], "M83")
        self.assertEqual(metadata["title"], "Midnight City")
        self.assertEqual(metadata["status"], "Playing")

    def test_parse_mpris_metadata(self):
        metadata = light_gui.parse_mpris_metadata_output(
            "({'xesam:artist': <['M83']>, 'xesam:title': <'Midnight City'>, 'xesam:album': <'Hurry Up'>},)"
        )

        self.assertEqual(metadata["artist"], "M83")
        self.assertEqual(metadata["title"], "Midnight City")
        self.assertEqual(metadata["album"], "Hurry Up")

    def test_now_playing_text(self):
        text = light_gui.now_playing_text({"artist": "M83", "title": "Midnight City", "album": "Hurry Up", "status": "Playing"})

        self.assertEqual(text, "M83 - Midnight City (Hurry Up, Playing)")

    def test_gui_rendered_html_has_now_playing_controls(self):
        html = light_gui.render_html()

        self.assertIn('id="nowPlaying"', html)
        self.assertIn("refreshNowPlaying()", html)

    def test_parse_ai_actions_keeps_response_text(self):
        parsed = light_gui.parse_ai_plan(
            '{"response":"I will run smooth beat synced effects.","actions":[{"action":"effect","brightness":null,"red":null,"green":null,"blue":null,"white":null,"effect":30,"speed":140,"scene":null}]}'
        )

        self.assertEqual(parsed["response"], "I will run smooth beat synced effects.")
        self.assertEqual(parsed["actions"][0]["effect"], 30)

    def test_ai_payload_rejects_unsafe_effect(self):
        with self.assertRaises(ValueError):
            light_gui.payload_for_ai_action({"action": "effect", "effect": 1, "speed": 200})

    def test_ai_payload_accepts_chase_effect(self):
        self.assertEqual(
            light_gui.payload_for_ai_action({"action": "effect", "effect": 28, "speed": 170}),
            {"seg": [{"fx": 28, "sx": 170}]},
        )

    def test_ai_plan_can_request_browser_mode1_start(self):
        parsed = light_gui.parse_ai_plan(
            '{"response":"I will start beat mode.","confirmations":["Started beat mode."],"actions":[{"action":"mode1_start","brightness":null,"red":null,"green":null,"blue":null,"white":null,"effect":null,"speed":null,"scene":null}]}'
        )

        self.assertEqual(parsed["client_actions"], ["startAudioReactive"])
        self.assertEqual(parsed["confirmations"], ["Started beat mode."])

    def test_ai_plan_can_request_music_and_timer_client_actions(self):
        parsed = light_gui.parse_ai_plan(
            '{"response":"I will listen and fade later.","confirmations":["Listening for song.","Starting fade."],"actions":[{"action":"music_listen"},{"action":"fade_off","minutes":15},{"action":"cycle_start","interval":45},{"action":"sunrise_stop"}]}'
        )

        self.assertEqual(
            parsed["client_actions"],
            [
                {"action": "listenForSong"},
                {"action": "fadeOff", "minutes": 15.0},
                {"action": "startCycle", "interval": 45.0},
                {"action": "stopSunrise"},
            ],
        )

    def test_apply_ai_plan_returns_confirmations_and_client_actions(self):
        client = FakeClient()
        result = light_gui.apply_ai_plan(
            client,
            {
                "response": "I set a deep blue chase.",
                "confirmations": ["Set RGBW color to 0, 0, 255, 0.", "Set effect to Chase."],
                "client_actions": ["startAudioReactive"],
                "actions": [
                    {"action": "color", "red": 0, "green": 0, "blue": 255, "white": 0},
                    {"action": "effect", "effect": 28, "speed": 150},
                ],
            },
        )

        self.assertEqual(len(client.payloads), 2)
        self.assertEqual(result["response"], "I set a deep blue chase.")
        self.assertEqual(result["client_actions"], ["startAudioReactive"])
        self.assertIn("Set effect to Chase.", result["confirmations"])

    def test_ai_plan_can_apply_random_preset_and_scene_management(self):
        client = FakeClient()
        with patch.object(light_gui.lightctl, "save_scene") as save_scene, patch.object(light_gui.lightctl, "delete_scene") as delete_scene:
            result = light_gui.apply_ai_plan(
                client,
                {
                    "response": "I updated scenes.",
                    "confirmations": [],
                    "client_actions": [],
                    "actions": [
                        {"action": "random"},
                        {"action": "preset", "preset_id": 3},
                        {"action": "save_scene", "scene": "movie"},
                        {"action": "delete_scene", "scene": "old"},
                    ],
                },
            )

        self.assertEqual(len(client.payloads), 2)
        save_scene.assert_called_once()
        delete_scene.assert_called_once_with("old")
        self.assertIn("random", result["message"])
        self.assertIn("preset", result["message"])

    def test_mcp_tool_call_posts_payload(self):
        client = FakeClient()

        class FakeModes:
            def start(self):
                return "started"

            def stop(self):
                return "stopped"

        result = mcp_light.call_tool(
            client,
            "set_color",
            {"red": 0, "green": 0, "blue": 255, "white": 0},
            FakeModes(),
        )

        self.assertEqual(client.payloads, [{"seg": [{"col": [[0, 0, 255, 0]]}]}])
        self.assertEqual(result["content"][0]["text"], "Set color to RGBW(0, 0, 255, 0).")


if __name__ == "__main__":
    unittest.main()
