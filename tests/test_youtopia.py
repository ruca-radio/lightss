"""Tests for the Youtopia bridge (youtopia_bridge.py) and its light_gui integration."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import light_gui
import lightctl
from tests.test_fleet import RecordingClient, make_fleet
from youtopia_bridge import TargetedFleet, build_prompt


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


class PromptBuildingTests(unittest.TestCase):
    def test_empty_title_and_artist_coerced_to_unknown(self):
        prompt = build_prompt(
            {"title": "", "artist": None, "videoType": 1, "likeStatus": 2}
        )
        self.assertIn("Title: Unknown", prompt)
        self.assertIn("Artist: Unknown", prompt)
        self.assertIn("Video type: Music Video", prompt)
        self.assertIn("User like status: Liked", prompt)


if __name__ == "__main__":
    unittest.main()
