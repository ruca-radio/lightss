"""Tests for WLED presets & playlists as first-class citizens.

Covers:
- lightctl payload builders: save_preset_payload (psave/n/ib/sb),
  delete_preset_payload (pdel), playlist_create_payload (playlist object with
  seconds -> tenths conversion), next_preset_payload (np).
- lightctl preset listing helpers: list_presets, find_preset_id, preset_name,
  current_preset, and the snapshot's "current_preset" entry.
- mcp_light tools: list_presets, apply_preset (by id or name), save_preset,
  delete_preset, create_playlist, next_preset — schema registration and
  fleet-level application with recording fakes.
- ai_chat keyword routing for preset/playlist terms.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import ai_chat
import fleet
import lightctl
import mcp_light


# ---------------------------------------------------------------------------
# Fakes (patterns copied from test_lightctl.py / test_zones_shows.py)
# ---------------------------------------------------------------------------

class FakeModes:
    def start(self) -> str:
        return "started"

    def stop(self) -> str:
        return "stopped"


class RecordingClient(lightctl.LightClient):
    """dry_run LightClient that records every posted payload."""

    def __init__(self, host: str, presets: dict | None = None):
        super().__init__(host, dry_run=True)
        self.payloads: list[dict] = []
        self._presets = presets if presets is not None else {
            "1": {"n": "Cozy", "on": True, "bri": 120},
            "2": {"n": "Party", "on": True, "bri": 255},
            "3": {"n": "Evening rotation", "playlist": {"ps": [1, 2], "dur": [100, 100]}},
        }

    def post_state(self, payload: dict) -> None:
        lightctl.validate_wled_payload(payload)
        self.payloads.append(payload)

    def get_presets(self) -> dict:
        return dict(self._presets)


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


def posted(client: RecordingClient) -> list[dict]:
    """Recorded payloads minus the fleet-injected udpn.nn no-notify marker."""
    return [{key: value for key, value in payload.items() if key != "udpn"} for payload in client.payloads]


# ---------------------------------------------------------------------------
# lightctl payload builders
# ---------------------------------------------------------------------------

class SavePresetPayloadTests(unittest.TestCase):
    def test_save_preset_payload_shape(self):
        self.assertEqual(
            lightctl.save_preset_payload(5, name="Cozy"),
            {"psave": 5, "n": "Cozy", "ib": True, "sb": True},
        )

    def test_save_preset_payload_omits_name_and_flags_when_disabled(self):
        self.assertEqual(
            lightctl.save_preset_payload(5, include_brightness=False, include_bounds=False),
            {"psave": 5},
        )

    def test_save_preset_payload_validates_id_range(self):
        with self.assertRaises(ValueError):
            lightctl.save_preset_payload(0)
        with self.assertRaises(ValueError):
            lightctl.save_preset_payload(251)

    def test_delete_preset_payload_shape(self):
        self.assertEqual(lightctl.delete_preset_payload(7), {"pdel": 7})

    def test_delete_preset_payload_validates_id_range(self):
        with self.assertRaises(ValueError):
            lightctl.delete_preset_payload(0)


class PlaylistCreatePayloadTests(unittest.TestCase):
    def test_seconds_are_converted_to_tenths(self):
        self.assertEqual(
            lightctl.playlist_create_payload([26, 20, 18], [3.0, 2.0, 5.0], repeat=10, end=21),
            {
                "playlist": {
                    "ps": [26, 20, 18],
                    "dur": [30, 20, 50],
                    "transition": 0,
                    "repeat": 10,
                    "end": 21,
                }
            },
        )

    def test_scalar_duration_stays_scalar(self):
        payload = lightctl.playlist_create_payload([1, 2, 3], 2.5)
        self.assertEqual(payload["playlist"]["dur"], 25)
        self.assertEqual(payload["playlist"]["ps"], [1, 2, 3])

    def test_transition_seconds_become_tenths_and_clamp_to_255(self):
        payload = lightctl.playlist_create_payload([1, 2], 1.0, transition=0.7)
        self.assertEqual(payload["playlist"]["transition"], 7)
        payload = lightctl.playlist_create_payload([1, 2], 1.0, transition=99.0)
        self.assertEqual(payload["playlist"]["transition"], 255)

    def test_repeat_zero_and_no_end_are_omitted(self):
        payload = lightctl.playlist_create_payload([1, 2], 1.0)
        self.assertNotIn("repeat", payload["playlist"])
        self.assertNotIn("end", payload["playlist"])

    def test_empty_playlist_is_rejected(self):
        with self.assertRaises(ValueError):
            lightctl.playlist_create_payload([], 1.0)

    def test_duration_list_must_match_preset_count(self):
        with self.assertRaises(ValueError):
            lightctl.playlist_create_payload([1, 2, 3], [1.0, 2.0])

    def test_preset_ids_are_validated(self):
        with self.assertRaises(ValueError):
            lightctl.playlist_create_payload([1, 300], 1.0)
        with self.assertRaises(ValueError):
            lightctl.playlist_create_payload([1, 2], 1.0, end=0)

    def test_next_preset_payload(self):
        self.assertEqual(lightctl.next_preset_payload(), {"np": True})


# ---------------------------------------------------------------------------
# lightctl preset listing helpers
# ---------------------------------------------------------------------------

class PresetListingTests(unittest.TestCase):
    PRESETS = {
        "1": {"n": "Cozy", "on": True},
        "3": {"n": "Evening rotation", "playlist": {"ps": [1, 2], "dur": 100}},
        "2": {"n": "Party", "on": True},
        "junk": "not-a-dict",
    }

    def test_list_presets_returns_sorted_id_name_playlist_flag(self):
        self.assertEqual(
            lightctl.list_presets(self.PRESETS),
            [
                {"id": 1, "name": "Cozy", "is_playlist": False},
                {"id": 2, "name": "Party", "is_playlist": False},
                {"id": 3, "name": "Evening rotation", "is_playlist": True},
            ],
        )

    def test_list_presets_handles_empty_and_missing_names(self):
        self.assertEqual(lightctl.list_presets({}), [])
        self.assertEqual(
            lightctl.list_presets({"4": {"on": True}}),
            [{"id": 4, "name": "Preset 4", "is_playlist": False}],
        )

    def test_find_preset_id_matches_name_case_insensitively(self):
        self.assertEqual(lightctl.find_preset_id(self.PRESETS, "cozy"), 1)
        self.assertEqual(lightctl.find_preset_id(self.PRESETS, "  Party "), 2)

    def test_find_preset_id_matches_numeric_ids(self):
        self.assertEqual(lightctl.find_preset_id(self.PRESETS, 3), 3)
        self.assertEqual(lightctl.find_preset_id(self.PRESETS, "2"), 2)

    def test_find_preset_id_unknown_name_returns_none(self):
        self.assertIsNone(lightctl.find_preset_id(self.PRESETS, "nonexistent"))
        self.assertIsNone(lightctl.find_preset_id(self.PRESETS, 42))

    def test_preset_name_matches_state_ps(self):
        self.assertEqual(lightctl.preset_name(self.PRESETS, 2), "Party")
        self.assertIsNone(lightctl.preset_name(self.PRESETS, 99))
        self.assertIsNone(lightctl.preset_name(self.PRESETS, -1))

    def test_current_preset_pairs_id_and_name(self):
        self.assertEqual(
            lightctl.current_preset(self.PRESETS, {"ps": 3}),
            {"id": 3, "name": "Evening rotation"},
        )
        self.assertEqual(lightctl.current_preset(self.PRESETS, {"ps": -1}), {})
        self.assertEqual(lightctl.current_preset(self.PRESETS, {}), {})


class PresetClientTests(unittest.TestCase):
    def test_client_list_presets_uses_get_presets(self):
        client = RecordingClient("http://10.27.27.110")
        self.assertEqual(
            client.list_presets(),
            [
                {"id": 1, "name": "Cozy", "is_playlist": False},
                {"id": 2, "name": "Party", "is_playlist": False},
                {"id": 3, "name": "Evening rotation", "is_playlist": True},
            ],
        )

    def test_snapshot_reports_current_preset_name(self):
        class SnapshotClient(RecordingClient):
            def get_json(self) -> dict:
                return {"state": {"on": True, "bri": 77, "ps": 2}, "info": {"name": "WLED"}}

            def get_config(self) -> dict:
                return {}

            def get_fxdata(self) -> list:
                return []

            def get_networks(self) -> dict:
                return {}

        snapshot = SnapshotClient("http://10.27.27.110").get_device_snapshot()
        self.assertEqual(snapshot["current_preset"], {"id": 2, "name": "Party"})

    def test_snapshot_current_preset_empty_without_match(self):
        class SnapshotClient(RecordingClient):
            def get_json(self) -> dict:
                return {"state": {"on": True, "ps": -1}, "info": {}}

            def get_config(self) -> dict:
                return {}

            def get_fxdata(self) -> list:
                return []

            def get_networks(self) -> dict:
                return {}

        snapshot = SnapshotClient("http://10.27.27.110").get_device_snapshot()
        self.assertEqual(snapshot["current_preset"], {})


# ---------------------------------------------------------------------------
# mcp_light tools
# ---------------------------------------------------------------------------

class McpPresetToolSchemaTests(unittest.TestCase):
    def test_new_tools_are_exposed(self):
        tool_names = {tool["name"] for tool in mcp_light.build_tools()}
        for name in (
            "list_presets",
            "apply_preset",
            "save_preset",
            "delete_preset",
            "create_playlist",
            "next_preset",
        ):
            self.assertIn(name, tool_names)

    def test_tool_schemas_require_their_mandatory_args(self):
        tools = {tool["name"]: tool for tool in mcp_light.build_tools()}
        self.assertIn("id", tools["save_preset"]["inputSchema"].get("required", []))
        self.assertIn("name", tools["save_preset"]["inputSchema"].get("required", []))
        self.assertIn("id", tools["delete_preset"]["inputSchema"].get("required", []))
        self.assertIn("preset_ids", tools["create_playlist"]["inputSchema"].get("required", []))
        self.assertIn("durations", tools["create_playlist"]["inputSchema"].get("required", []))
        # apply_preset accepts either an id or a name; neither is forced by schema
        props = tools["apply_preset"]["inputSchema"]["properties"]
        self.assertIn("id", props)
        self.assertIn("name", props)
        # all new tools accept the fleet target argument
        for name in ("list_presets", "apply_preset", "save_preset", "delete_preset", "create_playlist", "next_preset"):
            self.assertIn("target", tools[name]["inputSchema"]["properties"])


class McpPresetToolHandlerTests(unittest.TestCase):
    def test_apply_preset_by_id_posts_ps_fleet_wide(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(fleet_, "apply_preset", {"id": 2}, FakeModes())
        self.assertIn("content", result)
        self.assertEqual(posted(fleet_.clients["left"]), [{"ps": 2}])
        self.assertEqual(posted(fleet_.clients["right"]), [{"ps": 2}])

    def test_apply_preset_by_name_resolves_and_posts(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(fleet_, "apply_preset", {"name": "cozy"}, FakeModes())
        text = result["content"][0]["text"].lower()
        self.assertIn("preset 1", text)
        self.assertEqual(posted(fleet_.clients["left"]), [{"ps": 1}])
        self.assertEqual(posted(fleet_.clients["right"]), [{"ps": 1}])

    def test_apply_preset_unknown_name_errors_without_posting(self):
        fleet_ = make_fleet()
        with self.assertRaises(ValueError) as ctx:
            mcp_light.call_tool(fleet_, "apply_preset", {"name": "nope"}, FakeModes())
        self.assertIn("nope", str(ctx.exception))
        self.assertEqual(posted(fleet_.clients["left"]), [])
        self.assertEqual(posted(fleet_.clients["right"]), [])

    def test_apply_preset_requires_id_or_name(self):
        with self.assertRaises(ValueError):
            mcp_light.call_tool(make_fleet(), "apply_preset", {}, FakeModes())

    def test_apply_preset_honors_target(self):
        fleet_ = make_fleet()
        mcp_light.call_tool(fleet_, "apply_preset", {"id": 2, "target": "left"}, FakeModes())
        self.assertEqual(posted(fleet_.clients["left"]), [{"ps": 2}])
        self.assertEqual(posted(fleet_.clients["right"]), [])

    def test_list_presets_reports_names_and_playlists(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(fleet_, "list_presets", {}, FakeModes())
        text = result["content"][0]["text"]
        self.assertIn("Cozy", text)
        self.assertIn("Party", text)
        self.assertIn("playlist", text.lower())

    def test_save_preset_posts_psave_with_flags(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(
            fleet_, "save_preset", {"id": 4, "name": "Reading"}, FakeModes()
        )
        self.assertEqual(
            posted(fleet_.clients["left"]),
            [{"psave": 4, "n": "Reading", "ib": True, "sb": True}],
        )
        self.assertEqual(posted(fleet_.clients["left"]), posted(fleet_.clients["right"]))
        self.assertIn("saved preset 4", result["content"][0]["text"].lower())

    def test_save_preset_can_skip_brightness_and_bounds(self):
        fleet_ = make_fleet()
        mcp_light.call_tool(
            fleet_,
            "save_preset",
            {"id": 4, "name": "Reading", "include_brightness": False, "include_bounds": False},
            FakeModes(),
        )
        self.assertEqual(posted(fleet_.clients["left"]), [{"psave": 4, "n": "Reading"}])

    def test_delete_preset_posts_pdel(self):
        fleet_ = make_fleet()
        mcp_light.call_tool(fleet_, "delete_preset", {"id": 3}, FakeModes())
        self.assertEqual(posted(fleet_.clients["left"]), [{"pdel": 3}])
        self.assertEqual(posted(fleet_.clients["right"]), [{"pdel": 3}])

    def test_create_playlist_posts_playlist_object(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(
            fleet_,
            "create_playlist",
            {"preset_ids": [1, 2], "durations": [3.0, 2.0], "transition": 0.5, "repeat": 10, "end": 1},
            FakeModes(),
        )
        expected = {
            "playlist": {
                "ps": [1, 2],
                "dur": [30, 20],
                "transition": 5,
                "repeat": 10,
                "end": 1,
            }
        }
        self.assertEqual(posted(fleet_.clients["left"]), [expected])
        self.assertEqual(posted(fleet_.clients["right"]), [expected])
        self.assertIn("playlist", result["content"][0]["text"].lower())

    def test_create_playlist_scalar_duration(self):
        fleet_ = make_fleet()
        mcp_light.call_tool(
            fleet_, "create_playlist", {"preset_ids": [1, 2], "durations": 2.0}, FakeModes()
        )
        self.assertEqual(
            posted(fleet_.clients["left"]),
            [{"playlist": {"ps": [1, 2], "dur": 20, "transition": 0}}],
        )

    def test_create_playlist_empty_is_rejected(self):
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(
                fleet_, "create_playlist", {"preset_ids": [], "durations": 1.0}, FakeModes()
            )

    def test_next_preset_posts_np(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(fleet_, "next_preset", {}, FakeModes())
        self.assertEqual(posted(fleet_.clients["left"]), [{"np": True}])
        self.assertEqual(posted(fleet_.clients["right"]), [{"np": True}])
        self.assertIn("next", result["content"][0]["text"].lower())

    def test_tools_work_against_a_single_client(self):
        client = RecordingClient("http://10.27.27.110")
        modes = MagicMock()
        result = mcp_light.call_tool(client, "apply_preset", {"name": "Party"}, modes)
        self.assertEqual(client.payloads, [{"ps": 2}])
        self.assertIn("preset 2", result["content"][0]["text"].lower())
        result = mcp_light.call_tool(client, "list_presets", {}, modes)
        self.assertIn("Party", result["content"][0]["text"])
        with self.assertRaises(ValueError):
            mcp_light.call_tool(client, "apply_preset", {"name": "missing"}, modes)


# ---------------------------------------------------------------------------
# ai_chat routing
# ---------------------------------------------------------------------------

class AiChatRoutingTests(unittest.TestCase):
    def _tool_names(self, prompt: str) -> set:
        return {tool["function"]["name"] for tool in ai_chat.chat_tools(prompt)}

    def test_core_surface_includes_preset_read_tools(self):
        names = self._tool_names("what is on the wall right now")
        self.assertIn("list_presets", names)
        self.assertIn("apply_preset", names)
        self.assertIn("next_preset", names)
        # write/playlist tools stay out of the default surface
        self.assertNotIn("save_preset", names)
        self.assertNotIn("create_playlist", names)

    def test_preset_keywords_unlock_write_tools(self):
        names = self._tool_names("save this look as a preset")
        self.assertIn("save_preset", names)
        self.assertIn("delete_preset", names)

    def test_playlist_keywords_unlock_create_playlist(self):
        names = self._tool_names("make a playlist that cycles my presets")
        self.assertIn("create_playlist", names)

    def test_system_prompt_documents_presets_and_playlists(self):
        prompt = ai_chat.TOOL_CHAT_SYSTEM_PROMPT.lower()
        self.assertIn("preset", prompt)
        self.assertIn("on-device", prompt)
        self.assertIn("playlist", prompt)


class SnapshotTextTests(unittest.TestCase):
    def test_device_snapshot_text_names_current_preset(self):
        import light_gui

        text = light_gui.device_snapshot_text({
            "state": {"on": True, "bri": 77, "ps": 2},
            "info": {"name": "WLED"},
            "presets": {"1": {"n": "Cozy"}, "2": {"n": "Party"}},
            "current_preset": {"id": 2, "name": "Party"},
        })
        self.assertIn("Current preset: Party (id 2)", text)

    def test_device_snapshot_text_omits_current_preset_when_unknown(self):
        import light_gui

        text = light_gui.device_snapshot_text({
            "state": {"on": True, "ps": -1},
            "info": {},
            "current_preset": {},
        })
        self.assertNotIn("Current preset:", text)


if __name__ == "__main__":
    unittest.main()
