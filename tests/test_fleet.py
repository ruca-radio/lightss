"""Tests for the WLED fleet layer (fleet.py).

Written against the module contract in
docs/superpowers/specs/2026-08-01-wled-fleet-segments-design.md.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import pytest

import fleet
import lightctl


class RecordingClient(lightctl.LightClient):
    """dry_run LightClient that records every posted payload."""

    def __init__(self, host: str = "http://10.27.27.110", state: dict | None = None):
        super().__init__(host, dry_run=True)
        self.payloads: list[dict] = []
        self._state = state or {"on": True, "bri": 128}
        self.effects_calls = 0

    def post_state(self, payload: dict) -> None:
        lightctl.validate_wled_payload(payload)
        self.payloads.append(payload)

    def get_state(self) -> dict:
        return dict(self._state)

    def get_device_snapshot(self) -> dict:
        return {"marker": self.host, "state": dict(self._state)}

    def get_effects(self) -> list[str]:
        self.effects_calls += 1
        return ["Solid", "Blink", "Breathe"]


class FailingClient(RecordingClient):
    def post_state(self, payload: dict) -> None:
        raise RuntimeError("boom")

    def get_state(self) -> dict:
        raise RuntimeError("boom")

    def get_effects(self) -> list[str]:
        raise RuntimeError("boom")


def default_controllers() -> list[fleet.ControllerConfig]:
    return [
        fleet.ControllerConfig("left", "http://10.27.27.110",
                               {0: fleet.SegmentConfig("far-left", gpio=16, pixels=40),
                                1: fleet.SegmentConfig("middle-left", gpio=2, pixels=40)}),
        fleet.ControllerConfig("right", "http://10.27.27.112",
                               {0: fleet.SegmentConfig("far-right", gpio=2, pixels=40),
                                1: fleet.SegmentConfig("middle-right", gpio=16, pixels=40)}),
    ]


def make_fleet(left_client=None, right_client=None) -> fleet.LightFleet:
    clients = {
        "left": left_client or RecordingClient("http://10.27.27.110"),
        "right": right_client or RecordingClient("http://10.27.27.112"),
    }
    return fleet.LightFleet(clients, default_controllers())


class LoadControllersTests(unittest.TestCase):
    def setUp(self):
        # Make sure a developer/CI LIGHT_HOSTS does not leak into these tests.
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        os.environ.pop("LIGHT_HOSTS", None)
        self.addCleanup(patcher.stop)

    def test_builtin_default_is_the_two_known_controllers(self):
        controllers = fleet.load_controllers({})
        self.assertEqual([c.name for c in controllers], ["left", "right"])
        self.assertEqual(controllers[0].host, "http://10.27.27.110")
        self.assertEqual(controllers[1].host, "http://10.27.27.112")

    def test_default_segment_mapping_matches_wall_channels(self):
        controllers = {c.name: c for c in fleet.load_controllers({})}
        self.assertEqual(controllers["left"].segments, {
            0: fleet.SegmentConfig("far-left", gpio=16, pixels=40),
            1: fleet.SegmentConfig("middle-left", gpio=2, pixels=40),
        })
        self.assertEqual(controllers["right"].segments, {
            0: fleet.SegmentConfig("far-right", gpio=2, pixels=40),
            1: fleet.SegmentConfig("middle-right", gpio=16, pixels=40),
        })

    def test_config_controllers_win_and_parse_string_seg_keys(self):
        config = {
            "controllers": [
                {"name": "right", "host": "http://10.27.27.110",
                 "segments": {"0": "far-right", "1": "middle-right"}},
                {"name": "left", "host": "http://10.27.27.112",
                 "segments": {"0": "middle-left", "1": "far-left"}},
            ]
        }
        controllers = fleet.load_controllers(config)
        self.assertEqual([c.name for c in controllers], ["right", "left"])
        # JSON string keys must become int segment ids, string values become SegmentConfig
        self.assertEqual(controllers[0].segments,
                         {0: fleet.SegmentConfig("far-right"), 1: fleet.SegmentConfig("middle-right")})

    def test_config_file_controllers_win_over_env(self):
        config = {
            "controllers": [
                {"name": "solo", "host": "http://192.168.1.50", "segments": {"0": "solo"}},
            ]
        }
        with patch.dict(os.environ, {"LIGHT_HOSTS": "http://9.9.9.9"}):
            controllers = fleet.load_controllers(config)
        self.assertEqual([c.name for c in controllers], ["solo"])

    def test_light_hosts_env_fallback_names_lights_without_aliases(self):
        with patch.dict(os.environ, {"LIGHT_HOSTS": "http://1.1.1.1, http://2.2.2.2"}):
            controllers = fleet.load_controllers({})
        self.assertEqual([c.name for c in controllers], ["light-1", "light-2"])
        self.assertEqual(controllers[0].host, "http://1.1.1.1")
        self.assertEqual(controllers[1].host, "http://2.2.2.2")
        # env fallback provides no channel aliases
        self.assertEqual(controllers[0].segments, {})

    def test_none_config_reads_lightctl_config_file(self):
        config = {
            "controllers": [
                {"name": "from-file", "host": "http://5.5.5.5", "segments": {}},
            ]
        }
        with patch.object(lightctl, "load_config", return_value=config):
            controllers = fleet.load_controllers()
        self.assertEqual([c.name for c in controllers], ["from-file"])

    def test_malformed_controller_entries_are_skipped_with_warning(self):
        config = {
            "controllers": [
                "not-a-dict",
                {"name": "no-host"},
                {"host": "http://1.2.3.4"},
                {"name": "bad-seg", "host": "http://1.2.3.4", "segments": {"x": "ch"}},
                {"name": "good", "host": "http://1.2.3.5", "segments": {"0": "ch"}},
            ]
        }
        with patch.object(fleet, "_warn") as warn:
            controllers = fleet.load_controllers(config)
        self.assertEqual([c.name for c in controllers], ["good"])
        self.assertEqual(warn.call_count, 4)

    def test_all_malformed_entries_fall_back_to_builtin_default(self):
        config = {"controllers": [{"name": "no-host"}]}
        with patch.object(fleet, "_warn"):
            controllers = fleet.load_controllers(config)
        self.assertEqual([c.name for c in controllers], ["left", "right"])

    def test_wall_order_constant(self):
        self.assertEqual(fleet.WALL_ORDER, ["far-left", "middle-left", "middle-right", "far-right"])
        self.assertEqual(fleet.DEFAULT_TARGET, "all")

    def test_default_host_alias_matches_first_default_controller(self):
        controllers = fleet.load_controllers({})
        self.assertEqual(lightctl.DEFAULT_HOST, controllers[0].host)


class FleetResolutionTests(unittest.TestCase):
    def setUp(self):
        self.fleet = make_fleet()

    def test_names_returns_controller_names(self):
        self.assertEqual(self.fleet.names(), ["left", "right"])

    def test_channels_map_to_controller_and_segment_id(self):
        self.assertEqual(
            self.fleet.channels(),
            {
                "far-left": ("left", 0),
                "middle-left": ("left", 1),
                "far-right": ("right", 0),
                "middle-right": ("right", 1),
            },
        )

    def test_resolve_all_targets_both_controllers_without_segment(self):
        resolved = self.fleet.resolve("all")
        self.assertEqual(sorted(resolved), [("left", None), ("right", None)])

    def test_resolve_controller_name(self):
        self.assertEqual(self.fleet.resolve("right"), [("right", None)])

    def test_resolve_channel_name(self):
        self.assertEqual(self.fleet.resolve("middle-left"), [("left", 1)])
        self.assertEqual(self.fleet.resolve("middle-right"), [("right", 1)])

    def test_resolve_all_is_case_insensitive(self):
        self.assertEqual(sorted(self.fleet.resolve("ALL")), [("left", None), ("right", None)])
        self.assertEqual(sorted(self.fleet.resolve(" All ")), [("left", None), ("right", None)])

    def test_resolve_whitespace_only_uses_default_target(self):
        self.assertEqual(sorted(self.fleet.resolve("   ")), [("left", None), ("right", None)])

    def test_unknown_target_raises_with_valid_targets(self):
        with self.assertRaises(ValueError) as ctx:
            self.fleet.resolve("kitchen")
        message = str(ctx.exception)
        self.assertIn("kitchen", message)
        self.assertIn("all", message)


class FleetDuplicateNameTests(unittest.TestCase):
    def test_duplicate_channel_names_warn_and_keep_first(self):
        controllers = [
            fleet.ControllerConfig("a", "http://1.1.1.1", {0: fleet.SegmentConfig("dup")}),
            fleet.ControllerConfig("b", "http://2.2.2.2", {0: fleet.SegmentConfig("dup")}),
        ]
        clients = {"a": RecordingClient("http://1.1.1.1"), "b": RecordingClient("http://2.2.2.2")}
        with patch.object(fleet, "_warn") as warn:
            fleet_ = fleet.LightFleet(clients, controllers)
        self.assertTrue(warn.called)
        self.assertEqual(fleet_.channels(), {"dup": ("a", 0)})

    def test_channel_named_like_a_controller_warns_and_is_ignored(self):
        controllers = [fleet.ControllerConfig("a", "http://1.1.1.1", {0: fleet.SegmentConfig("b")})]
        clients = {"a": RecordingClient("http://1.1.1.1"), "b": RecordingClient("http://2.2.2.2")}
        with patch.object(fleet, "_warn") as warn:
            fleet_ = fleet.LightFleet(clients, controllers)
        self.assertTrue(warn.called)
        self.assertEqual(fleet_.channels(), {})

    def test_duplicate_controller_names_warn(self):
        controllers = [
            fleet.ControllerConfig("a", "http://1.1.1.1", {0: fleet.SegmentConfig("x")}),
            fleet.ControllerConfig("a", "http://1.1.1.1", {1: fleet.SegmentConfig("y")}),
        ]
        clients = {"a": RecordingClient("http://1.1.1.1")}
        with patch.object(fleet, "_warn") as warn:
            fleet.LightFleet(clients, controllers)
        self.assertTrue(warn.called)


class FleetFanOutTests(unittest.TestCase):
    def test_post_state_all_reaches_every_controller(self):
        fleet_ = make_fleet()
        result = fleet_.post_state(lightctl.on_payload(True))
        self.assertEqual(set(result), {"right", "left"})
        for name in ("right", "left"):
            self.assertTrue(result[name]["ok"])
        self.assertEqual(fleet_.clients["right"].payloads, [{"on": True, "udpn": {"nn": True}}])
        self.assertEqual(fleet_.clients["left"].payloads, [{"on": True, "udpn": {"nn": True}}])

    def test_post_state_defaults_to_all(self):
        fleet_ = make_fleet()
        result = fleet_.post_state(lightctl.brightness_payload(100))
        self.assertEqual(set(result), {"right", "left"})

    def test_post_state_to_one_controller_only(self):
        fleet_ = make_fleet()
        result = fleet_.post_state(lightctl.on_payload(False), target="left")
        self.assertEqual(set(result), {"left"})
        self.assertEqual(fleet_.clients["left"].payloads, [{"on": False, "udpn": {"nn": True}}])
        self.assertEqual(fleet_.clients["right"].payloads, [])

    def test_partial_failure_is_reported_not_raised(self):
        fleet_ = make_fleet(left_client=FailingClient("http://10.27.27.110"))
        result = fleet_.post_state(lightctl.on_payload(True))
        self.assertTrue(result["right"]["ok"])
        self.assertFalse(result["left"]["ok"])
        self.assertIn("boom", result["left"]["error"])
        # the healthy controller still received the payload
        self.assertEqual(fleet_.clients["right"].payloads, [{"on": True, "udpn": {"nn": True}}])

    def test_channel_target_injects_segment_id(self):
        fleet_ = make_fleet()
        result = fleet_.post_state({"seg": [{"fx": 9, "sx": 180}]}, target="middle-left")
        self.assertEqual(set(result), {"left"})
        self.assertEqual(fleet_.clients["left"].payloads, [{"seg": [{"id": 1, "fx": 9, "sx": 180}], "udpn": {"nn": True}}])
        self.assertEqual(fleet_.clients["right"].payloads, [])

    def test_channel_target_injects_into_every_seg_entry(self):
        fleet_ = make_fleet()
        fleet_.post_state({"seg": [{"fx": 9}, {"col": [[1, 2, 3, 0]]}]}, target="middle-right")
        # channel target: every seg entry gets the channel's segment id
        self.assertEqual(fleet_.clients["right"].payloads,
                         [{"seg": [{"id": 1, "fx": 9}, {"id": 1, "col": [[1, 2, 3, 0]]}], "udpn": {"nn": True}}])
        self.assertEqual(fleet_.clients["left"].payloads, [])

    def test_channel_target_injects_right_center_seg_id(self):
        fleet_ = make_fleet()
        fleet_.post_state({"seg": [{"fx": 28}]}, target="middle-right")
        self.assertEqual(fleet_.clients["right"].payloads, [{"seg": [{"id": 1, "fx": 28}], "udpn": {"nn": True}}])

    def test_channel_target_without_seg_passes_payload_through(self):
        fleet_ = make_fleet()
        fleet_.post_state({"on": False}, target="middle-left")
        self.assertEqual(fleet_.clients["left"].payloads, [{"on": False, "udpn": {"nn": True}}])

    def test_post_state_does_not_mutate_the_callers_payload(self):
        fleet_ = make_fleet()
        payload = {"on": True}
        fleet_.post_state(payload, target="middle-left")
        self.assertEqual(payload, {"on": True})

    def test_get_state_all_returns_per_controller_states(self):
        fleet_ = make_fleet()
        result = fleet_.get_state()
        self.assertEqual(set(result), {"right", "left"})
        self.assertEqual(result["right"]["bri"], 128)

    def test_get_state_partial_failure_is_reported(self):
        fleet_ = make_fleet(left_client=FailingClient("http://10.27.27.110"))
        result = fleet_.get_state()
        self.assertEqual(result["right"]["bri"], 128)
        self.assertIn("error", str(result["left"]).lower())


class FleetSnapshotTests(unittest.TestCase):
    def test_snapshot_is_labeled_per_controller(self):
        fleet_ = make_fleet()
        snapshot = fleet_.get_fleet_snapshot()
        self.assertEqual(set(snapshot["devices"]), {"left", "right"})
        for name in ("left", "right"):
            self.assertIsInstance(snapshot["devices"][name], dict)

    def test_snapshot_includes_device_snapshot_data(self):
        fleet_ = make_fleet()
        snapshot = fleet_.get_fleet_snapshot()
        self.assertIn("http://10.27.27.112", json.dumps(snapshot["devices"]["right"]))
        self.assertIn("http://10.27.27.110", json.dumps(snapshot["devices"]["left"]))


class FleetEffectIdsTests(unittest.TestCase):
    def test_effect_ids_returns_live_index_set_and_caches(self):
        fleet_ = make_fleet()
        ids = fleet_.effect_ids("right")
        self.assertEqual(ids, {0, 1, 2})
        fleet_.effect_ids("right")
        self.assertEqual(fleet_.clients["right"].effects_calls, 1)

    def test_effect_ids_returns_none_on_failure(self):
        fleet_ = make_fleet(left_client=FailingClient("http://10.27.27.110"))
        self.assertIsNone(fleet_.effect_ids("left"))


class FromConfigTests(unittest.TestCase):
    def test_from_config_dry_run_builds_default_fleet(self):
        with patch.object(lightctl, "load_config", return_value={}):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LIGHT_HOSTS", None)
                fleet_ = fleet.LightFleet.from_config(dry_run=True)
        self.assertEqual(fleet_.names(), ["left", "right"])
        for client in fleet_.clients.values():
            self.assertTrue(client.dry_run)


def test_fleet_snapshot_labels_live_devices_with_topology():
    fleet_ = make_fleet()
    snapshot = fleet_.get_fleet_snapshot()
    assert snapshot["topology"]["installation"]["spacing_inches"] == 30
    assert snapshot["topology"]["controllers"][0]["segments"]["0"] == {
        "channel": "far-left", "gpio": 16, "pixels": 40
    }
    assert set(snapshot["devices"]) == {"left", "right"}


def test_builtin_topology_matches_verified_wall():
    installation, controllers = fleet.load_topology({})
    assert installation.wall_order == [
        "far-left", "middle-left", "middle-right", "far-right"
    ]
    assert installation.spacing_inches == 30
    assert installation.pixel_zero == "bottom"
    assert installation.column_length_m == 2.0
    assert installation.pixels_per_meter == 20
    assert installation.visible_leds_per_meter == 720
    assert installation.color_order == "BRG"
    by_host = {controller.host: controller for controller in controllers}
    assert by_host["http://10.27.27.110"].segments == {
        0: fleet.SegmentConfig("far-left", gpio=16, pixels=40),
        1: fleet.SegmentConfig("middle-left", gpio=2, pixels=40),
    }
    assert by_host["http://10.27.27.112"].segments == {
        0: fleet.SegmentConfig("far-right", gpio=2, pixels=40),
        1: fleet.SegmentConfig("middle-right", gpio=16, pixels=40),
    }


def test_legacy_string_segment_schema_remains_supported():
    config = {"controllers": [{
        "name": "solo", "host": "http://1.2.3.4", "segments": {"0": "bar"}
    }]}
    _installation, controllers = fleet.load_topology(config)
    assert controllers[0].segments[0] == fleet.SegmentConfig("bar")


def test_duplicate_wall_channel_is_rejected():
    config = {
        "installation": {"wall_order": ["same", "same"]},
        "controllers": [{
            "name": "solo", "host": "http://1.2.3.4",
            "segments": {"0": {"channel": "same", "pixels": 40}},
        }],
    }
    with pytest.raises(ValueError, match="duplicate wall channel"):
        fleet.load_topology(config)


if __name__ == "__main__":
    unittest.main()
