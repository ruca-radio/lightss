"""Tests for the live effect-catalog policy (mcp_light).

The offline SAFE_EFFECTS allowlist is the fallback; once a live catalog is
seeded, every real id in it must be usable by the AI tools. Effect-family
prohibitions (strobe/blink/flash/fireworks/sparkle) were removed by operator
decision, so the only ids excluded from a live catalog are WLED's
reserved/placeholder entries.
"""

from __future__ import annotations

import unittest

import fleet
import lightctl
import mcp_light

# ids: 0=Solid, 1=Strobe, 2=Breathe, 3=GEQ (♪), 4=RSVD (placeholder, unavailable)
CATALOG = ["Solid", "Strobe", "Breathe", "GEQ", "RSVD"]
ALLOWED = {0, 1, 2, 3}


class PolicyRecordingClient(lightctl.LightClient):
    def __init__(self, host, state=None, info=None):
        super().__init__(host, dry_run=True)
        self.payloads: list[dict] = []
        self._state = state or {}
        self._info = info or {}

    def post_state(self, payload):
        lightctl.validate_wled_payload(payload)
        self.payloads.append(payload)

    def get_state(self):
        return dict(self._state)

    def get_info(self):
        return dict(self._info)


class FakeModes:
    def start(self):
        return "started"

    def stop(self):
        return "stopped"


def make_fleet() -> fleet.LightFleet:
    controllers = [
        fleet.ControllerConfig("left", "http://10.27.27.112", {0: "far-left", 1: "middle-left"}),
        fleet.ControllerConfig("right", "http://10.27.27.110", {0: "far-right", 1: "middle-right"}),
    ]
    clients = {
        "left": PolicyRecordingClient("http://10.27.27.112"),
        "right": PolicyRecordingClient("http://10.27.27.110"),
    }
    return fleet.LightFleet(clients, controllers)


def posted(fleet_: fleet.LightFleet) -> list[dict]:
    return [payload for client in fleet_.clients.values() for payload in client.payloads]


class PolicyTests(unittest.TestCase):
    def setUp(self):
        mcp_light._fx_allowed.clear()

    def tearDown(self):
        mcp_light._fx_allowed.clear()

    def seed_both(self, catalog=CATALOG):
        mcp_light.seed_effect_catalog("left", catalog)
        mcp_light.seed_effect_catalog("right", catalog)

    # -- seeding / lookup ----------------------------------------------------

    def test_seed_excludes_placeholder_names_only(self):
        """Strobe stays selectable; RSVD is not a real effect."""
        mcp_light.seed_effect_catalog("left", CATALOG)
        self.assertEqual(mcp_light._fx_allowed["left"], ALLOWED)
        self.assertNotIn(4, mcp_light._fx_allowed["left"])

    def test_allowed_effects_intersect_across_controllers(self):
        mcp_light.seed_effect_catalog("left", CATALOG)
        mcp_light.seed_effect_catalog("right", ["Solid", "Breathe"])  # ids 0, 1 — no GEQ
        fleet_ = make_fleet()
        # left {0,1,2,3} ∩ right {0,1} = {0,1}: only ids both controllers can run
        self.assertEqual(mcp_light.allowed_effects_for(fleet_, "all"), {0, 1})
        # a channel target only consults its own controller
        self.assertEqual(mcp_light.allowed_effects_for(fleet_, "far-left"), ALLOWED)

    def test_allowed_effects_none_when_unseeded(self):
        self.assertIsNone(mcp_light.allowed_effects_for(make_fleet(), "all"))

    def test_unseeded_controller_does_not_shrink_the_intersection(self):
        """Only controllers with a known catalog may constrain the result.

        Substituting the offline SAFE_EFFECTS list for an unseeded controller
        collapsed the intersection to ~30 ids, so one missing snapshot made
        every legitimate live effect look forbidden.
        """
        mcp_light.seed_effect_catalog("right", CATALOG)
        fleet_ = make_fleet()
        # "left" is unknown, so it must not constrain "right"'s catalog.
        self.assertEqual(mcp_light.allowed_effects_for(fleet_, "all"), ALLOWED)

    def test_device_snapshot_seeds_the_policy_for_the_gui_paths(self):
        """light_gui._device_snapshot must keep the live catalog available.

        A refactor dropped this call, so the GUI process never seeded anything
        and every effect check silently used the offline list — which rejected
        live effects the model had just been shown (e.g. 159 DJ Light).
        """
        import light_gui

        class SnapshotClient:
            def get_fleet_snapshot(self):
                return {
                    "devices": {
                        "left": {"effects": CATALOG, "fxdata": []},
                        "right": {"effects": CATALOG, "fxdata": []},
                    }
                }

        light_gui._device_snapshot(SnapshotClient())
        self.assertEqual(mcp_light._fx_allowed.get("left"), ALLOWED)
        self.assertEqual(mcp_light._fx_allowed.get("right"), ALLOWED)
        self.assertEqual(mcp_light.allowed_effects_for(make_fleet(), "all"), ALLOWED)

    def test_single_client_policy_keyed_by_host(self):
        client = PolicyRecordingClient("http://10.27.27.99")
        mcp_light.seed_effect_catalog(client.host, CATALOG)
        self.assertEqual(mcp_light.allowed_effects_for(client, "all"), ALLOWED)

    # -- set_effect ----------------------------------------------------------

    def test_seeded_catalog_unlocks_non_safe_effect_ids(self):
        self.seed_both()
        fleet_ = make_fleet()
        result = mcp_light.call_tool(fleet_, "set_effect", {"effect": 3}, FakeModes())
        self.assertIn("Set effect 3", result["content"][0]["text"])
        payloads = posted(fleet_)
        self.assertTrue(payloads)
        self.assertTrue(all(p["seg"][0]["fx"] == 3 for p in payloads))

    def test_seeded_catalog_rejects_ids_absent_from_it(self):
        self.seed_both()
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(fleet_, "set_effect", {"effect": 99}, FakeModes())
        self.assertEqual(posted(fleet_), [])

    def test_previously_prohibited_families_are_selectable(self):
        """Strobe/Blink/Fireworks are ordinary effects now."""
        self.seed_both(["Solid", "Strobe", "Blink", "Fireworks 1D", "Sparkle+", "RSVD"])
        fleet_ = make_fleet()
        for fx in (1, 2, 3, 4):
            result = mcp_light.call_tool(fleet_, "set_effect", {"effect": fx}, FakeModes())
            self.assertIn(f"Set effect {fx}", result["content"][0]["text"])
        self.assertEqual(len(posted(fleet_)), 8)  # two controllers

    def test_unseeded_set_effect_keeps_offline_allowlist(self):
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(fleet_, "set_effect", {"effect": 3}, FakeModes())
        mcp_light.call_tool(fleet_, "set_effect", {"effect": 9}, FakeModes())  # SAFE id works

    # -- wall_mode / strips / set_zone ---------------------------------------

    def test_wall_mode_rejects_ids_absent_from_a_seeded_catalog(self):
        self.seed_both()
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(fleet_, "wall_mode", {"mode": "span", "fx": 99}, FakeModes())
        self.assertEqual(posted(fleet_), [])

    def test_wall_mode_accepts_catalog_safe_non_safe_id_when_seeded(self):
        self.seed_both()
        fleet_ = make_fleet()
        result = mcp_light.call_tool(fleet_, "wall_mode", {"mode": "span", "fx": 3}, FakeModes())
        self.assertIn("span", result["content"][0]["text"])
        self.assertTrue(posted(fleet_))

    def test_wall_mode_unseeded_rejects_unsafe_effect(self):
        fleet_ = make_fleet()
        # Unknown capability is not permission to bypass effect safety.
        with self.assertRaises(ValueError):
            mcp_light.call_tool(fleet_, "wall_mode", {"mode": "span", "fx": 1}, FakeModes())
        self.assertFalse(posted(fleet_))

    def test_strips_rejects_ids_absent_from_a_seeded_catalog(self):
        self.seed_both()
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(fleet_, "strips", {"channels": ["far-left"], "fx": 99}, FakeModes())

    def test_strips_assignments_reject_ids_absent_from_a_seeded_catalog(self):
        self.seed_both()
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(
                fleet_, "strips", {"assignments": [{"channel": "far-left", "fx": 99}]}, FakeModes()
            )

    def test_set_zone_rejects_ids_absent_from_a_seeded_catalog(self):
        self.seed_both()
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(
                fleet_, "set_zone", {"channel": "far-left", "zone": "top half", "fx": 99}, FakeModes()
            )

    def test_effect_payload_allowed_param_threads_through(self):
        payload = lightctl.effect_payload(3, allowed=ALLOWED)
        self.assertEqual(payload["seg"][0]["fx"], 3)
        with self.assertRaises(ValueError):
            lightctl.effect_payload(99, allowed=ALLOWED)

    def test_reactive_mode_honors_allowed_effects(self):
        client = PolicyRecordingClient("http://x")
        mode = lightctl.ReactiveMode(client)
        mode.effects = (3,)
        mode.allowed_effects = ALLOWED
        mode.handle_beat(0.9)
        self.assertEqual(client.payloads[0]["seg"][0]["fx"], 3)


if __name__ == "__main__":
    unittest.main()
