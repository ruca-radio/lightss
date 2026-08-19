"""Tests for ai_chat.py — tool-calling chat loop."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import ai_chat
import fleet
import lightctl


class RecordingClient(lightctl.LightClient):
    def __init__(self, host: str):
        super().__init__(host, dry_run=True)
        self.payloads: list[dict] = []

    def post_state(self, payload, transition_ms: int = 0):
        self.payloads.append(payload)
        return None


def make_fleet() -> fleet.LightFleet:
    controllers = fleet.load_controllers({})
    clients = {c.name: RecordingClient(c.host) for c in controllers}
    return fleet.LightFleet(clients, controllers)


SETTINGS = {"base_url": "https://example.test/v1", "model": "test-model", "api_key_env": "UNSET_KEY"}


def tool_call_round(call_id: str, name: str, arguments: str) -> dict:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }],
            }
        }]
    }


def text_round(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class ChatToolsTests(unittest.TestCase):
    def test_function_format_from_mcp_surface(self):
        tools = ai_chat.chat_tools()
        self.assertGreater(len(tools), 20)
        names = {t["function"]["name"] for t in tools}
        for expected in ("light_on", "set_effect", "set_color", "wall_mode",
                         "atmosphere", "dynamic_scene", "design_look", "strips", "list_segments", "list_controllers"):
            self.assertIn(expected, names)
        for tool in tools:
            self.assertEqual(tool["type"], "function")
            self.assertEqual(tool["function"]["parameters"]["type"], "object")

    def test_prompt_scopes_common_requests_to_core_tools(self):
        tools = ai_chat.chat_tools("Report the current light status")
        names = {tool["function"]["name"] for tool in tools}

        self.assertIn("get_state", names)
        self.assertIn("set_color", names)
        self.assertIn("design_look", names)
        self.assertNotIn("set_leds", names)
        self.assertNotIn("tv_open_url", names)
        self.assertLess(len(tools), len(ai_chat.chat_tools()))

    def test_prompt_adds_specialized_tools_when_requested(self):
        tools = ai_chat.chat_tools("Create a zoned per-LED show and cast it to the TV")
        names = {tool["function"]["name"] for tool in tools}

        self.assertIn("set_zone", names)
        self.assertIn("set_leds", names)
        self.assertIn("start_show", names)
        self.assertIn("tv_open_url", names)

    def test_prompt_adds_save_delete_tools_for_natural_phrasing(self):
        names = {tool["function"]["name"] for tool in ai_chat.chat_tools("save this as a scene")}
        self.assertIn("save_scene", names)

        names = {tool["function"]["name"] for tool in ai_chat.chat_tools("delete the ocean scene")}
        self.assertIn("delete_scene", names)

    def test_common_tool_schema_names_all_four_strip_targets(self):
        tools = {tool["function"]["name"]: tool["function"] for tool in ai_chat.chat_tools("set each strip")}
        for name in ("set_color", "set_effect", "set_brightness"):
            target = tools[name]["parameters"]["properties"]["target"]
            for channel in ("far-left", "middle-left", "middle-right", "far-right"):
                self.assertIn(channel, target["description"])
        self.assertIn("channel target", tools["set_brightness"]["description"])


class FakeHttpResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class ChatRoundHttpTests(unittest.TestCase):
    def test_non_json_200_body_raises_tool_chat_error_without_retry(self):
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(1)
            return FakeHttpResponse(b"<html>502 Bad Gateway via proxy</html>")

        with patch.object(ai_chat.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(ai_chat.ToolChatError) as ctx:
                ai_chat._chat_round("https://example.test/v1/chat/completions", {}, {}, 1.0)
        self.assertIn("invalid JSON", str(ctx.exception))
        self.assertEqual(len(calls), 1, "invalid JSON must not be retried")

    def test_url_error_is_retried_once(self):
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(1)
            if len(calls) == 1:
                raise ai_chat.urllib.error.URLError("connection reset")
            return FakeHttpResponse(b'{"choices": []}')

        with patch.object(ai_chat.urllib.request, "urlopen", fake_urlopen):
            data = ai_chat._chat_round("https://example.test/v1/chat/completions", {}, {}, 1.0)
        self.assertEqual(data, {"choices": []})
        self.assertEqual(len(calls), 2, "one retry on URLError")

    def test_url_error_twice_raises_tool_chat_error(self):
        def fake_urlopen(request, timeout=0):
            raise ai_chat.urllib.error.URLError("connection reset")

        with patch.object(ai_chat.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(ai_chat.ToolChatError) as ctx:
                ai_chat._chat_round("https://example.test/v1/chat/completions", {}, {}, 1.0)
        self.assertIn("unreachable", str(ctx.exception))


class FakeReactiveModes:
    instances: list = []

    def __init__(self, client):
        self.client = client
        self.started = 0
        self.stopped = 0
        FakeReactiveModes.instances.append(self)

    def start(self):
        self.started += 1
        return "Audio-reactive mode started."

    def stop(self):
        self.stopped += 1
        return "Audio-reactive mode stopped."


class RunChatSettingsAndModesTests(unittest.TestCase):
    def setUp(self):
        ai_chat._modes = None
        FakeReactiveModes.instances = []

    def tearDown(self):
        ai_chat._modes = None

    def test_missing_base_url_raises_tool_chat_error(self):
        with self.assertRaises(ai_chat.ToolChatError):
            ai_chat.run_chat(make_fleet(), "hi", settings={"model": "m"})

    def test_missing_model_raises_tool_chat_error(self):
        with self.assertRaises(ai_chat.ToolChatError):
            ai_chat.run_chat(make_fleet(), "hi", settings={"base_url": "https://x.test"})

    def test_audio_reactive_tool_gets_a_real_modes_handle(self):
        rounds = [
            tool_call_round("c1", "start_audio_reactive", "{}"),
            tool_call_round("c2", "stop_audio_reactive", "{}"),
            text_round("Beat mode on and off."),
        ]
        with (
            patch.object(ai_chat, "_chat_round", side_effect=rounds) as mock_round,
            patch.object(ai_chat.lightctl, "ReactiveThread", FakeReactiveModes),
        ):
            result = ai_chat.run_chat(make_fleet(), "match the music", settings=SETTINGS)

        self.assertEqual(result["text"], "Beat mode on and off.")
        self.assertEqual(len(FakeReactiveModes.instances), 1, "modes handle must be shared")
        modes = FakeReactiveModes.instances[0]
        self.assertEqual(modes.started, 1)
        self.assertEqual(modes.stopped, 1)
        tool_messages = [
            m
            for call in mock_round.call_args_list
            for m in call.args[2]["messages"]
            if m.get("role") == "tool"
        ]
        self.assertTrue(any("Audio-reactive mode started." == m["content"] for m in tool_messages))
        self.assertFalse(any("error" in m["content"] for m in tool_messages))


class RunChatTests(unittest.TestCase):
    def test_single_round_no_tools(self):
        with patch.object(ai_chat, "_chat_round", return_value=text_round("All good!")):
            result = ai_chat.run_chat(make_fleet(), "hi", settings=SETTINGS)
        self.assertEqual(result["text"], "All good!")
        self.assertEqual(result["log"], [])
        self.assertEqual(result["rounds"], 1)

    def test_run_chat_sends_prompt_scoped_tools(self):
        with patch.object(ai_chat, "_chat_round", return_value=text_round("All good!")) as chat_round:
            ai_chat.run_chat(make_fleet(), "Report current status", settings=SETTINGS)

        body = chat_round.call_args.args[2]
        names = {tool["function"]["name"] for tool in body["tools"]}
        self.assertIn("get_state", names)
        self.assertNotIn("set_leds", names)

    def test_tool_call_executes_against_fleet(self):
        rounds = [
            tool_call_round("c1", "set_color", '{"red": 0, "green": 0, "blue": 255, "target": "far-left"}'),
            text_round("Far left is now blue."),
        ]
        fleet_ = make_fleet()
        with patch.object(ai_chat, "_chat_round", side_effect=rounds):
            result = ai_chat.run_chat(fleet_, "far left blue", settings=SETTINGS)
        self.assertEqual(result["text"], "Far left is now blue.")
        self.assertEqual(len(result["log"]), 1)
        self.assertIn("set_color", result["log"][0])
        # far-left -> left controller, segment 0
        self.assertEqual(len(fleet_.clients["left"].payloads), 1)
        seg = fleet_.clients["left"].payloads[0]["seg"][0]
        self.assertEqual(seg["id"], 0)
        self.assertEqual(seg["col"][0][:3], [0, 0, 255])
        self.assertEqual(fleet_.clients["right"].payloads, [])

    def test_channel_targeted_brightness_only_hits_center_segment(self):
        rounds = [
            tool_call_round("c1", "set_brightness", '{"brightness": 77, "target": "middle-left"}'),
            text_round("Middle left dimmed."),
        ]
        fleet_ = make_fleet()
        with patch.object(ai_chat, "_chat_round", side_effect=rounds):
            ai_chat.run_chat(fleet_, "dim only the middle left strip", settings=SETTINGS)

        self.assertEqual(len(fleet_.clients["left"].payloads), 1)
        left_seg = fleet_.clients["left"].payloads[0]["seg"][0]
        self.assertEqual(left_seg["id"], 1)
        self.assertEqual(left_seg["bri"], 77)
        self.assertEqual(fleet_.clients["right"].payloads, [])

    def test_channel_targeted_color_and_brightness_combo_can_hit_middle_right(self):
        rounds = [
            tool_call_round("c1", "set_color", '{"red": 255, "green": 40, "blue": 0, "target": "middle-right"}'),
            tool_call_round("c2", "set_brightness", '{"brightness": 180, "target": "middle-right"}'),
            text_round("Middle right is orange and bright."),
        ]
        fleet_ = make_fleet()
        with patch.object(ai_chat, "_chat_round", side_effect=rounds):
            ai_chat.run_chat(fleet_, "make only middle right orange at 180 brightness", settings=SETTINGS)

        self.assertEqual(fleet_.clients["left"].payloads, [])
        self.assertEqual(len(fleet_.clients["right"].payloads), 2)
        self.assertEqual(fleet_.clients["right"].payloads[0]["seg"][0]["id"], 1)
        self.assertEqual(fleet_.clients["right"].payloads[0]["seg"][0]["col"][0][:3], [255, 40, 0])
        self.assertEqual(fleet_.clients["right"].payloads[1]["seg"][0]["id"], 1)
        self.assertEqual(fleet_.clients["right"].payloads[1]["seg"][0]["bri"], 180)

    def test_multiple_tool_calls_then_answer(self):
        rounds = [
            tool_call_round("c1", "atmosphere", '{"name": "ocean"}'),
            tool_call_round("c2", "set_brightness", '{"brightness": 200}'),
            text_round("Ocean at 200."),
        ]
        fleet_ = make_fleet()
        with patch.object(ai_chat, "_chat_round", side_effect=rounds):
            result = ai_chat.run_chat(fleet_, "ocean but brighter", settings=SETTINGS)
        self.assertEqual(len(result["log"]), 2)
        self.assertEqual(result["rounds"], 3)
        self.assertTrue(fleet_.clients["left"].payloads)
        self.assertTrue(fleet_.clients["right"].payloads)

    def test_tool_error_is_fed_back_not_raised(self):
        rounds = [
            tool_call_round("c1", "atmosphere", '{"name": "mordor"}'),
            text_round("That atmosphere does not exist."),
        ]
        with patch.object(ai_chat, "_chat_round", side_effect=rounds) as mock_round:
            result = ai_chat.run_chat(make_fleet(), "mordor", settings=SETTINGS)
        self.assertEqual(result["rounds"], 2)
        # the error was passed back to the model as a tool message
        second_body_messages = mock_round.call_args_list[1][0][2]["messages"]
        tool_messages = [m for m in second_body_messages if m.get("role") == "tool"]
        self.assertTrue(any("error" in m["content"] for m in tool_messages))

    def test_bad_arguments_json_treated_as_empty(self):
        rounds = [
            tool_call_round("c1", "light_off", "{not json"),
            text_round("Lights off."),
        ]
        fleet_ = make_fleet()
        with patch.object(ai_chat, "_chat_round", side_effect=rounds):
            ai_chat.run_chat(fleet_, "off", settings=SETTINGS)
        for client in fleet_.clients.values():
            self.assertEqual(client.payloads[-1]["on"], False)

    def test_round_limit_returns_gracefully(self):
        endless = [tool_call_round("c1", "get_state", "{}")] * 10
        with patch.object(ai_chat, "_chat_round", side_effect=endless):
            result = ai_chat.run_chat(make_fleet(), "loop", settings=SETTINGS, max_rounds=3)
        self.assertEqual(result["rounds"], 3)
        self.assertEqual(len(result["log"]), 3)


def test_static_tool_prompt_has_no_invented_geometry():
    prompt = ai_chat.TOOL_CHAT_SYSTEM_PROMPT
    assert "50 individually addressable" not in prompt
    assert "LEDs 0-24" not in prompt
    assert "L-shaped" not in prompt
    assert "device snapshot" in prompt.lower()
    assert "far-left, middle-left, middle-right, far-right" in prompt
    assert "set_brightness" in prompt and "individual strip brightness" in prompt


if __name__ == "__main__":
    unittest.main()
