"""Tests for the Youtopia bridge and daemon (youtopia_bridge.py, youtopia_daemon.py)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from unittest.mock import MagicMock, patch

import fleet
import light_gui
import lightctl
import youtopia_bridge
import youtopia_daemon
from tests.test_fleet import RecordingClient, make_fleet
from youtopia_bridge import TargetedFleet


class TargetedFleetTests(unittest.TestCase):
    """TargetedFleet must quack like a fleet for light_gui's duck-typed helpers."""

    def test_is_fleet_recognizes_targeted_fleet(self):
        targeted = TargetedFleet(make_fleet(), "left")
        self.assertTrue(light_gui._is_fleet(targeted))
        self.assertFalse(light_gui._is_fleet(RecordingClient()))

    def test_resolve_passthrough(self):
        targeted = TargetedFleet(make_fleet(), "left")
        self.assertEqual(targeted.resolve("left"), [("left", None)])

    def test_post_state_defaults_to_bound_target(self):
        left = RecordingClient("http://10.27.27.112")
        right = RecordingClient("http://10.27.27.110")
        targeted = TargetedFleet(make_fleet(left_client=left, right_client=right), "left")

        light_gui._post_state(targeted, {"on": True})

        self.assertEqual(len(left.payloads), 1)
        self.assertEqual(len(right.payloads), 0)

    def test_post_state_honors_per_action_target(self):
        left = RecordingClient("http://10.27.27.112")
        right = RecordingClient("http://10.27.27.110")
        targeted = TargetedFleet(make_fleet(left_client=left, right_client=right), "left")

        light_gui._post_state(targeted, {"on": False}, target="right")

        self.assertEqual(len(left.payloads), 0)
        self.assertEqual(len(right.payloads), 1)

    def test_primary_state_unwraps_fleet_shaped_state(self):
        left = RecordingClient("http://10.27.27.112", state={"on": True, "bri": 200})
        targeted = TargetedFleet(make_fleet(left_client=left), "left")

        self.assertEqual(light_gui.primary_state(targeted), {"on": True, "bri": 200})

    def test_save_scene_reads_unwrapped_primary_state(self):
        left = RecordingClient("http://10.27.27.112", state={"on": True, "bri": 200})
        targeted = TargetedFleet(make_fleet(left_client=left), "left")
        saved: dict = {}
        with patch.object(lightctl, "save_scene", new=lambda name, payload: saved.update(name=name, payload=payload)):
            light_gui.apply_ai_actions(targeted, [{"action": "save_scene", "scene": "test"}])

        self.assertEqual(saved["name"], "test")
        self.assertEqual(saved["payload"].get("bri"), 200)
        self.assertTrue(saved["payload"].get("on"))


class EnsureClientTests(unittest.TestCase):
    def test_target_change_keeps_host_override_client(self):
        daemon = youtopia_daemon.YoutopiaDaemon()
        daemon.handle_host({"host": "http://10.27.27.110"})
        client = daemon.client

        with patch.object(fleet.LightFleet, "from_config") as from_config:
            daemon.handle_target({"target": "left"})

        from_config.assert_not_called()
        self.assertIs(daemon.client, client)
        self.assertEqual(daemon.host, "http://10.27.27.110")
        self.assertEqual(daemon.target, "left")

    def test_fleet_built_only_when_no_client(self):
        test_fleet = make_fleet()
        daemon = youtopia_daemon.YoutopiaDaemon()
        with patch.object(fleet.LightFleet, "from_config", return_value=test_fleet) as from_config:
            daemon.handle_target({"target": "all"})
            daemon.handle_target({"target": "all"})

        from_config.assert_called_once()
        self.assertIs(daemon.client, test_fleet)

    def test_fleet_construction_failure_keeps_state_and_is_retried(self):
        test_fleet = make_fleet()
        daemon = youtopia_daemon.YoutopiaDaemon()
        with patch.object(fleet.LightFleet, "from_config", side_effect=[KeyError("controllers"), test_fleet]):
            with self.assertRaises(KeyError):
                daemon._ensure_client()
            # State was not clobbered, so the next call retries the fleet build.
            self.assertIsNone(daemon.client)
            self.assertIsNone(daemon.host)
            self.assertIs(daemon._ensure_client(), test_fleet)


class RunLoopTests(unittest.TestCase):
    def run_daemon(self, daemon: youtopia_daemon.YoutopiaDaemon, lines: str) -> str:
        out = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(lines)), contextlib.redirect_stdout(out):
            daemon.run()
        return out.getvalue()

    def test_run_ignores_non_dict_json_lines(self):
        daemon = youtopia_daemon.YoutopiaDaemon()
        lines = '[1, 2, 3]\n"a string"\n42\nnull\n{"type": "quit"}\n'
        with self.assertLogs("youtopia_daemon", level="WARNING"):
            self.run_daemon(daemon, lines)  # must not raise

    def test_run_survives_command_failure_and_retries(self):
        test_fleet = make_fleet()
        daemon = youtopia_daemon.YoutopiaDaemon()
        lines = '{"type": "target", "target": "all"}\n{"type": "target", "target": "all"}\n{"type": "quit"}\n'
        with patch.object(fleet.LightFleet, "from_config", side_effect=[KeyError("controllers"), test_fleet]):
            out = self.run_daemon(daemon, lines)

        emitted = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(emitted[0]["ok"], False)
        self.assertIn("controllers", emitted[0]["error"])
        self.assertIs(daemon.client, test_fleet)


class VUReactiveModeTests(unittest.TestCase):
    def test_handle_frame_skips_bad_bin_values(self):
        mode = youtopia_daemon.VUReactiveMode(RecordingClient())
        mode.handle_frame([None, "abc", {}, 255, 0])  # must not raise
        # Only the two numeric bins count toward the energy.
        self.assertAlmostEqual(mode._last_energy, 0.5)

    def test_handle_frame_all_bad_values_is_a_noop(self):
        mode = youtopia_daemon.VUReactiveMode(RecordingClient())
        mode.handle_frame([None, {}])  # must not raise
        self.assertEqual(mode._last_energy, 0.0)


class PauseTests(unittest.TestCase):
    def test_stop_pauses_audio_until_host_resumes(self):
        daemon = youtopia_daemon.YoutopiaDaemon()
        daemon.reactive = MagicMock()

        daemon.handle_stop({})
        self.assertTrue(daemon._paused)
        daemon.reactive.reset.assert_called_once()

        daemon.handle_audio({"data": [10, 20]})
        daemon.reactive.handle_frame.assert_not_called()

        daemon.handle_host({"host": "http://10.27.27.110"})
        self.assertFalse(daemon._paused)

        daemon.reactive = MagicMock()
        daemon.handle_audio({"data": [10, 20]})
        daemon.reactive.handle_frame.assert_called_once_with([10, 20])

    def test_song_resumes_paused_audio(self):
        daemon = youtopia_daemon.YoutopiaDaemon()
        daemon._paused = True
        with (
            patch.object(fleet.LightFleet, "from_config", return_value=make_fleet()),
            patch.object(light_gui, "call_openai_for_plan", return_value={"actions": [], "response": ""}),
            patch.object(light_gui, "apply_ai_plan", return_value={"message": "ok", "response": ""}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            daemon.handle_song({"song": {"title": "Song", "artist": "Artist"}})

        self.assertFalse(daemon._paused)


class PromptSharingTests(unittest.TestCase):
    def test_daemon_uses_bridge_prompt_builder(self):
        self.assertIs(youtopia_daemon.build_song_prompt, youtopia_bridge.build_prompt)

    def test_empty_title_and_artist_coerced_to_unknown(self):
        prompt = youtopia_daemon.build_song_prompt(
            {"title": "", "artist": None, "videoType": 1, "likeStatus": 2}
        )
        self.assertIn("Title: Unknown", prompt)
        self.assertIn("Artist: Unknown", prompt)
        self.assertIn("Video type: Music Video", prompt)
        self.assertIn("User like status: Liked", prompt)


if __name__ == "__main__":
    unittest.main()
