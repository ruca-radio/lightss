"""Tests for the wall/column composers (columns.py).

Written against the module contract in
docs/superpowers/specs/2026-08-01-wled-fleet-segments-design.md.

Each test drives the composers against a real fleet.LightFleet whose clients
are recording dry-run fakes, so the per-controller multi-segment payloads are
asserted exactly as they would be posted to each WLED controller.
"""

from __future__ import annotations

import unittest

import columns
import fleet
import lightctl


class RecordingClient(lightctl.LightClient):
    """dry_run LightClient that records every posted payload."""

    def __init__(self, host: str):
        super().__init__(host, dry_run=True)
        self.payloads: list[dict] = []

    def post_state(self, payload: dict) -> None:
        lightctl.validate_wled_payload(payload)
        self.payloads.append(payload)


def make_fleet() -> fleet.LightFleet:
    controllers = [
        fleet.ControllerConfig("right", "http://10.27.27.110", {0: "far-right", 1: "middle-right"}),
        fleet.ControllerConfig("left", "http://10.27.27.112", {0: "middle-left", 1: "far-left"}),
    ]
    clients = {
        "right": RecordingClient("http://10.27.27.110"),
        "left": RecordingClient("http://10.27.27.112"),
    }
    return fleet.LightFleet(clients, controllers)


def segs_by_id(payload: dict) -> dict[int, dict]:
    return {seg["id"]: seg for seg in payload["seg"]}


class WallSpanTests(unittest.TestCase):
    def test_same_effect_on_all_four_channels(self):
        fleet_ = make_fleet()
        result = columns.wall_span(fleet_, 9, pal=0)

        self.assertIsInstance(result, dict)
        for name in ("right", "left"):
            client = fleet_.clients[name]
            self.assertEqual(len(client.payloads), 1)
            segs = segs_by_id(client.payloads[0])
            self.assertEqual(set(segs), {0, 1})
            for seg in segs.values():
                self.assertEqual(seg["fx"], 9)
                self.assertEqual(seg["pal"], 0)

    def test_seg_opts_are_forwarded_to_every_segment(self):
        fleet_ = make_fleet()
        columns.wall_span(fleet_, 28, sx=200, ix=90)

        for name in ("right", "left"):
            segs = segs_by_id(fleet_.clients[name].payloads[0])
            for seg in segs.values():
                self.assertEqual(seg["sx"], 200)
                self.assertEqual(seg["ix"], 90)


class MirrorTests(unittest.TestCase):
    def test_left_controller_segments_mirrored_right_not(self):
        fleet_ = make_fleet()
        result = columns.mirror(fleet_, 9)

        self.assertIsInstance(result, dict)
        left_segs = segs_by_id(fleet_.clients["left"].payloads[0])
        right_segs = segs_by_id(fleet_.clients["right"].payloads[0])
        for seg in left_segs.values():
            self.assertEqual(seg["fx"], 9)
            self.assertTrue(seg["mi"])
        for seg in right_segs.values():
            self.assertEqual(seg["fx"], 9)
            self.assertFalse(seg.get("mi", False))


class ChaseTests(unittest.TestCase):
    def test_staggered_offsets_along_wall_order(self):
        fleet_ = make_fleet()
        result = columns.chase(fleet_, 28)

        self.assertIsInstance(result, dict)
        left_segs = segs_by_id(fleet_.clients["left"].payloads[0])
        right_segs = segs_by_id(fleet_.clients["right"].payloads[0])
        for seg in list(left_segs.values()) + list(right_segs.values()):
            self.assertEqual(seg["fx"], 28)
            self.assertIn("of", seg)

        # physical wall order: far-left, middle-left, middle-right, far-right
        offsets = [
            left_segs[1]["of"],   # left
            left_segs[0]["of"],   # middle-left
            right_segs[1]["of"],  # middle-right
            right_segs[0]["of"],  # right
        ]
        self.assertEqual(len(set(offsets)), 4)
        self.assertEqual(offsets, sorted(offsets))


class LeftVsRightTests(unittest.TestCase):
    def test_each_side_gets_its_own_effect_and_palette(self):
        fleet_ = make_fleet()
        result = columns.left_vs_right(fleet_, 9, 28, pal_left=1, pal_right=2)

        self.assertIsInstance(result, dict)
        left_segs = segs_by_id(fleet_.clients["left"].payloads[0])
        right_segs = segs_by_id(fleet_.clients["right"].payloads[0])
        for seg in left_segs.values():
            self.assertEqual(seg["fx"], 9)
            self.assertEqual(seg["pal"], 1)
        for seg in right_segs.values():
            self.assertEqual(seg["fx"], 28)
            self.assertEqual(seg["pal"], 2)


class SetChannelTests(unittest.TestCase):
    def test_channel_receives_single_segment_payload_with_id(self):
        fleet_ = make_fleet()
        result = columns.set_channel(fleet_, "middle-right", fx=28)

        self.assertIsInstance(result, dict)
        self.assertEqual(fleet_.clients["left"].payloads, [])
        right = fleet_.clients["right"]
        self.assertEqual(len(right.payloads), 1)
        self.assertEqual(right.payloads[0], {"seg": [{"id": 1, "fx": 28}], "udpn": {"nn": True}})

    def test_set_channel_color_options(self):
        fleet_ = make_fleet()
        # "middle-left" is unambiguous; bare "left"/"right" resolve to the
        # controller (controller names win over channel names in resolve()).
        columns.set_channel(fleet_, "middle-left", col=[[255, 0, 0, 0]])

        left = fleet_.clients["left"]
        self.assertEqual(len(left.payloads), 1)
        self.assertEqual(left.payloads[0], {"seg": [{"id": 0, "col": [[255, 0, 0, 0]]}], "udpn": {"nn": True}})

    def test_set_channel_accepts_hex_color_string(self):
        fleet_ = make_fleet()
        columns.set_channel(fleet_, "middle-left", col="FF8800")

        left = fleet_.clients["left"]
        self.assertEqual(left.payloads[0], {"seg": [{"id": 0, "col": [[255, 136, 0]]}], "udpn": {"nn": True}})

    def test_set_channel_wraps_flat_rgb_list_as_single_color(self):
        fleet_ = make_fleet()
        columns.set_channel(fleet_, "middle-left", col=[255, 0, 0])

        left = fleet_.clients["left"]
        self.assertEqual(left.payloads[0], {"seg": [{"id": 0, "col": [[255, 0, 0]]}], "udpn": {"nn": True}})


if __name__ == "__main__":
    unittest.main()
