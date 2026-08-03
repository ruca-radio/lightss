import json
import threading
import tomllib
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import actions
import light_gui
import mcp_light


class FakeClient:
    def __init__(self):
        self.payloads = []

    def post_state(self, payload):
        self.payloads.append(payload)

    def get_state(self):
        return {"on": True, "bri": 128, "seg": [{"col": [[255, 0, 0, 0]]}]}

    def get_device_snapshot(self):
        return {
            "state": self.get_state(),
            "info": {"name": "WLED-Gledopto", "ver": "0.15.1"},
            "effects": ["Solid", "Rainbow"],
            "palettes": ["Default"],
            "config": {},
        }


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

    def test_gui_action_builds_full_effect_payload(self):
        payload = light_gui.payload_for_action(
            "fx",
            {
                "effect": 28,
                "speed": 170,
                "intensity": 190,
                "palette": 6,
                "c1": 10,
                "c2": 20,
                "c3": 30,
                "transition": 500,
            },
        )

        self.assertEqual(
            payload,
            {
                "seg": [{"fx": 28, "sx": 170, "ix": 190, "pal": 6, "c1": 10, "c2": 20, "c3": 30}],
                "transition": 5,
            },
        )

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

    def test_mcp_effect_tool_accepts_full_catalog_range(self):
        effect_tool = next(tool for tool in mcp_light.build_tools() if tool["name"] == "set_effect")

        schema = effect_tool["inputSchema"]["properties"]["effect"]
        self.assertEqual(schema["minimum"], 0)
        self.assertEqual(schema["maximum"], 255)
        self.assertNotIn("enum", schema)

    def test_ai_action_enum_comes_from_action_registry(self):
        request = light_gui.build_openai_request("control everything")
        action_enum = request["text"]["format"]["schema"]["properties"]["actions"]["items"]["properties"]["action"]["enum"]

        self.assertEqual(action_enum, actions.ai_action_names())

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

    def test_gui_ai_prompt_uses_async_jobs(self):
        html = light_gui.render_html()

        self.assertIn("async: true", html)
        self.assertIn("async function waitForAiJob", html)
        self.assertIn("/api/ai/jobs/", html)

    def test_gui_mood_status_renders_diagnostics(self):
        html = light_gui.render_html()

        self.assertIn("mood.last_error", html)
        self.assertIn("mood.last_cache_hit", html)
        self.assertIn("mood.next_recognition_in", html)

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
        self.assertEqual(action_props["effect"]["minimum"], 0)
        self.assertEqual(action_props["effect"]["maximum"], 255)
        self.assertIn("catalog", action_props["effect"]["description"])
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

        self.assertEqual(request["model"], light_gui.DEFAULT_AI_MODEL)

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
                "save_preset",
                "delete_preset",
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
                "music_match",
                "wall_span",
                "wall_mirror",
                "wall_chase",
                "wall_versus",
                "set_channel",
                "atmosphere",
            },
        )
        properties = request["text"]["format"]["schema"]["properties"]["actions"]["items"]["properties"]
        for field in ("preset_id", "minutes", "interval", "schedule_time", "schedule_action", "schedule_index"):
            self.assertIn(field, properties)

    def test_ai_job_manager_tracks_completion_result(self):
        manager = light_gui.AiJobManager(max_workers=1)
        try:
            job_id = manager.submit(lambda: {"ok": True, "message": "done"})
            result = manager.wait(job_id, timeout=1)

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["result"]["message"], "done")
            self.assertEqual(manager.get(job_id)["status"], "complete")
        finally:
            manager.shutdown()

    def test_ai_job_manager_tracks_errors(self):
        manager = light_gui.AiJobManager(max_workers=1)
        try:
            def fail():
                raise RuntimeError("model down")

            job_id = manager.submit(fail)
            result = manager.wait(job_id, timeout=1)

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error"], "model down")
        finally:
            manager.shutdown()

    def test_ai_system_prompt_contains_one_shot_action_context(self):
        request = light_gui.build_openai_request("make a wake up schedule")
        system = request["input"][0]["content"]

        self.assertIn("Available AI actions", system)
        self.assertIn("schedule_add", system)
        self.assertIn("music_detect", system)
        self.assertIn("music_match", system)
        self.assertNotIn("music_listen", system)
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

    def test_get_now_playing_playerctl_ignores_paused_player(self):
        """A paused player (e.g. a browser tab with stale media metadata) must not be
        reported as 'now playing' — this previously caused persistent misidentification
        of whatever song a paused/backgrounded tab last loaded (e.g. FE!N by Travis Scott)."""
        with patch.object(light_gui.shutil, "which", return_value="/usr/bin/playerctl"), \
             patch.object(light_gui.subprocess, "run") as mock_run:
            mock_run.side_effect = [
                type("R", (), {"stdout": "chromium\nTravis Scott\nFE!N\nUTOPIA"})(),
                type("R", (), {"stdout": "Paused"})(),
            ]
            result = light_gui.get_now_playing_playerctl()

        self.assertIsNone(result)

    def test_get_now_playing_mpris_ignores_paused_player(self):
        with patch.object(light_gui, "list_mpris_players", return_value=["org.mpris.MediaPlayer2.chromium.instance1"]), \
             patch.object(light_gui.subprocess, "run") as mock_run:
            mock_run.side_effect = [
                type("R", (), {"stdout": "({'xesam:artist': <['Travis Scott']>, 'xesam:title': <'FE!N'>, 'xesam:album': <'UTOPIA'>},)"})(),
                type("R", (), {"stdout": "(<'Paused'>,)"})(),
            ]
            result = light_gui.get_now_playing_mpris()

        self.assertIsNone(result)

    def test_now_playing_text(self):
        text = light_gui.now_playing_text({"artist": "M83", "title": "Midnight City", "album": "Hurry Up", "status": "Playing"})

        self.assertEqual(text, "M83 - Midnight City (Hurry Up, Playing)")

    def test_gui_rendered_html_has_now_playing_controls(self):
        html = light_gui.render_html()

        self.assertIn('id="nowPlaying"', html)
        self.assertIn("refreshNowPlaying()", html)

    def test_music_mode_starts_mood_session_before_browser_recorder(self):
        html = light_gui.render_html()

        running_idx = html.index("musicModeRunning = true;")
        session_idx = html.index("await setMoodSessionRunning(true);")
        recorder_idx = html.index("await startMoodRecorder();")

        # Server-side MoodSession.sample() drops uploaded chunks unless the
        # session was started first, so the session must precede the recorder.
        self.assertLess(running_idx, session_idx)
        self.assertLess(session_idx, recorder_idx)
        self.assertIn("setMoodSessionRunning(false);", html)

    def test_music_mode_installs_recognition_timer_before_initial_cycle(self):
        html = light_gui.render_html()
        start = html.index("async function startMusicMode")
        end = html.index("function stopMusicMode")
        body = html[start:end]

        timer_idx = body.index("musicRecognitionTimer = setInterval")
        initial_cycle_idx = body.index("await runMusicRecognitionCycle(true);")

        self.assertLess(timer_idx, initial_cycle_idx)

    def test_music_recognition_requests_have_timeouts(self):
        html = light_gui.render_html()

        self.assertIn("async function fetchJsonWithTimeout", html)
        self.assertIn("const controller = new AbortController();", html)
        self.assertIn("fetchJsonWithTimeout('/api/now-playing'", html)
        self.assertIn("fetchJsonWithTimeout('/api/recognize'", html)
        self.assertIn("fetchJsonWithTimeout('/api/match-lights'", html)

    def test_music_mode_uses_browser_shazam_when_metadata_is_missing(self):
        html = light_gui.render_html()
        cycle_start = html.index("async function runMusicRecognitionCycle")
        cycle_end = html.index("async function startAudioReactive")
        cycle_body = html[cycle_start:cycle_end]

        self.assertIn("await matchLightsFromNowPlaying();", cycle_body)
        self.assertIn("await recognizeSongOnce();", cycle_body)
        self.assertIn("await matchLightsFromRecognizedSong(recognizedSong);", cycle_body)

    def test_browser_shazam_prefers_webcam_microphone(self):
        html = light_gui.render_html()
        helper_start = html.index("async function getPreferredMicStream")
        helper_end = html.index("async function ensureMicSession")
        helper_body = html[helper_start:helper_end]
        session_start = html.index("async function ensureMicSession")
        session_end = html.index("async function captureMicWav")
        session_body = html[session_start:session_end]

        self.assertIn("navigator.mediaDevices.enumerateDevices()", helper_body)
        self.assertIn("/c920|webcam/i", helper_body)
        self.assertIn("deviceId: { exact: preferred.deviceId }", helper_body)
        self.assertIn("micStream = await getPreferredMicStream();", session_body)

    def test_auto_match_button_uses_browser_shazam_fallback(self):
        html = light_gui.render_html()
        auto_start = html.index("async function autoMatchSong")
        auto_end = html.index("async function startMoodRecorder")
        auto_body = html[auto_start:auto_end]

        self.assertIn("await runMusicRecognitionCycle(true);", auto_body)
        self.assertNotIn("await matchLightsFromNowPlaying();", auto_body)

    def test_music_mode_starts_mood_session_for_browser_samples(self):
        # MoodSession.start() spawns no server-side recognition loop — it only
        # arms MoodSession.sample() so the browser-uploaded chunks are actually
        # processed instead of silently discarded.
        html = light_gui.render_html()
        start_mode = html.index("async function startMusicMode")
        stop_mode = html.index("function stopMusicMode")
        mode_body = html[start_mode:stop_mode]

        self.assertIn("setMoodSessionRunning(true)", mode_body)

    def test_browser_beat_posts_are_coalesced(self):
        html = light_gui.render_html()

        self.assertIn("let beatRequestInFlight = false;", html)
        self.assertIn("function sendBeatUpdate(values)", html)
        self.assertIn("if (beatRequestInFlight) return;", html)
        self.assertIn("sendBeatUpdate({red: color[0]", html)

    def test_explicit_platform_and_ai_control_take_ownership_from_music_mode(self):
        html = light_gui.render_html()
        send_start = html.index("async function send(action")
        send_end = html.index("async function sendBeatUpdate", send_start)
        send_body = html[send_start:send_end]
        ai_start = html.index("async function askAI()")
        ai_end = html.index("async function waitForAiJob", ai_start)
        ai_body = html[ai_start:ai_end]

        self.assertLess(send_body.index("stopMusicMode();"), send_body.index("postAction(action, values)"))
        self.assertLess(ai_body.index("stopMusicMode();"), ai_body.index("fetchJsonWithTimeout('/api/ai'"))

    def test_vision_analysis_request_uses_responses_image_input(self):
        request = light_gui.build_openai_vision_analysis_request(
            "abc123",
            "gpt-vision-test",
            {"state": {"on": True, "bri": 80}, "info": {"name": "Bedroom WLED"}},
        )

        self.assertEqual(request["model"], "gpt-vision-test")
        self.assertIn("text", request)
        content = request["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_text")
        self.assertEqual(content[1], {"type": "input_image", "image_url": "data:image/jpeg;base64,abc123"})
        self.assertIn("Current WLED device snapshot:", content[0]["text"])

    def test_api_route_sets_include_vision_endpoint(self):
        self.assertIn("/api/ai_vision", light_gui.API_POST_PATHS)
        self.assertIn("/api/ai_vision", light_gui.API_HEAD_PATHS)

    def test_mood_sample_submission_is_bounded(self):
        started = threading.Event()
        release = threading.Event()

        class SlowMoodSession:
            def sample(self, audio_bytes):
                started.set()
                release.wait(timeout=1)

        state = light_gui.GuiState.__new__(light_gui.GuiState)
        state.mood_session = SlowMoodSession()
        state._mood_sample_lock = threading.Lock()
        state._mood_samples_inflight = 0
        state._mood_sample_limit = 1
        state._mood_executor = ThreadPoolExecutor(max_workers=1)
        try:
            self.assertTrue(state.submit_mood_sample(b"one"))
            self.assertTrue(started.wait(timeout=1))
            self.assertFalse(state.submit_mood_sample(b"two"))
        finally:
            release.set()
            state._mood_executor.shutdown(wait=True)

    def test_gui_state_uses_browser_first_disabled_autonomous_mode(self):
        state = light_gui.GuiState(FakeClient())
        try:
            status = state.autonomous.status()

            self.assertFalse(status["running"])
            self.assertTrue(status["disabled"])
            self.assertIn("browser Music Mode", state.autonomous.start())
        finally:
            state.schedule.stop()
            state._mood_executor.shutdown(wait=True)

    def test_pyproject_exposes_console_scripts(self):
        with open("pyproject.toml", "rb") as f:
            project = tomllib.load(f)["project"]

        self.assertEqual(project["scripts"]["lightss"], "light_gui:main")
        self.assertEqual(project["scripts"]["light-gui"], "light_gui:main")
        self.assertEqual(project["scripts"]["lightctl"], "lightctl:main")
        self.assertEqual(project["scripts"]["mcp-light"], "mcp_light:main")

    def test_vision_room_observation_is_sent_to_main_planner(self):
        client = FakeClient()
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "output": [
                            {
                                "content": [
                                    {
                                        "text": (
                                            "The room is dim with warm wall reflections and the current LEDs "
                                            "look too saturated."
                                        )
                                    }
                                ]
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_planner(prompt, now_playing, device_snapshot):
            captured["prompt"] = prompt
            captured["now_playing"] = now_playing
            captured["device_snapshot"] = device_snapshot
            return {
                "response": "I softened the room lighting.",
                "confirmations": ["Applied softer room-aware lighting."],
                "client_actions": [],
                "actions": [{"action": "brightness", "brightness": 120}],
            }

        with patch.dict(light_gui.os.environ, {"OPENAI_API_KEY": "test-key", "LIGHT_AI_MODEL": "gpt-main-test"}):
            with patch.object(light_gui.urllib.request, "urlopen", return_value=FakeResponse()):
                with patch.object(light_gui, "call_openai_for_plan", side_effect=fake_planner):
                    result = light_gui.call_openai_vision_for_plan("abc123", client)

        self.assertEqual(result["response"], "I softened the room lighting.")
        self.assertIn("Room camera observation:", captured["prompt"])
        self.assertIn("warm wall reflections", captured["prompt"])
        self.assertIsNone(captured["now_playing"])
        self.assertEqual(captured["device_snapshot"]["info"]["name"], "WLED-Gledopto")

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
            '{"response":"I will match the music and fade later.","confirmations":["Matching song.","Starting fade."],"actions":[{"action":"music_match"},{"action":"fade_off","minutes":15},{"action":"cycle_start","interval":45},{"action":"sunrise_stop"}]}'
        )

        self.assertEqual(
            parsed["client_actions"],
            [
                {"action": "matchLightsFromNowPlaying"},
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
