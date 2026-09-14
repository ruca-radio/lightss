"""Tests for the AI's deep WLED knowledge upgrade:

- raw wled_read/wled_write tools (mcp_light),
- recent-effect memory (lightctl.record_fx_use) surfaced in the AI context,
- full-catalog fxdata hints in the device snapshot,
- music_director per-mood look variants + rotation,
- system-prompt variety rule + core tool registration.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

import ai_chat
import fleet
import lightctl
import light_gui
import mcp_light
import music_director
import shows

# ids: 0=Solid, 1=Strobe (🚫), 2=Breathe, 3=GEQ (♪), 4=RSVD (🚫 placeholder)
CATALOG = ["Solid", "Strobe", "Breathe", "GEQ", "RSVD"]


class RecordingClient(lightctl.LightClient):
    def __init__(self, host, state=None):
        super().__init__(host, dry_run=True)
        self.payloads: list[dict] = []
        self._state = state or {}

    def post_state(self, payload):
        lightctl.validate_wled_payload(payload)
        self.payloads.append(payload)

    def get_state(self):
        return dict(self._state)

    def get_effects(self):
        return list(CATALOG)

    def get_palettes(self):
        return ["Default", "Party"]


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
        "left": RecordingClient("http://10.27.27.112"),
        "right": RecordingClient("http://10.27.27.110"),
    }
    return fleet.LightFleet(clients, controllers)


def posted(fleet_: fleet.LightFleet) -> list[dict]:
    return [payload for client in fleet_.clients.values() for payload in client.payloads]


class WledWriteTests(unittest.TestCase):
    def setUp(self):
        mcp_light._fx_allowed.clear()
        lightctl._fx_history.clear()

    def tearDown(self):
        mcp_light._fx_allowed.clear()
        lightctl._fx_history.clear()

    def test_posts_raw_payload_verbatim(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(
            fleet_, "wled_write",
            {"payload": {"nl": {"on": True, "dur": 20}, "bri": 42}},
            FakeModes(),
        )
        self.assertIn("Posted raw WLED state", result["content"][0]["text"])
        payloads = posted(fleet_)
        self.assertTrue(payloads)
        self.assertTrue(all(p["nl"] == {"on": True, "dur": 20} and p["bri"] == 42 for p in payloads))

    def test_seg_dict_normalized_to_list(self):
        fleet_ = make_fleet()
        mcp_light.call_tool(
            fleet_, "wled_write", {"payload": {"seg": {"id": 0, "fx": 9}}}, FakeModes()
        )
        payloads = posted(fleet_)
        self.assertTrue(all(isinstance(p["seg"], list) for p in payloads))
        self.assertTrue(all(p["seg"][0]["fx"] == 9 for p in payloads))

    def test_requires_nonempty_object_payload(self):
        fleet_ = make_fleet()
        for bad in (None, {}, ["not", "a", "dict"], "nope"):
            with self.assertRaises(ValueError):
                mcp_light.call_tool(fleet_, "wled_write", {"payload": bad}, FakeModes())
        self.assertEqual(posted(fleet_), [])

    def test_oversized_payload_rejected(self):
        fleet_ = make_fleet()
        big = {"seg": [{"id": 0, "n": "x" * 9000}]}
        with self.assertRaises(ValueError):
            mcp_light.call_tool(fleet_, "wled_write", {"payload": big}, FakeModes())
        self.assertEqual(posted(fleet_), [])

    def test_forbidden_effect_rejected_when_seeded(self):
        mcp_light.seed_effect_catalog("left", CATALOG)
        mcp_light.seed_effect_catalog("right", CATALOG)
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(
                fleet_, "wled_write", {"payload": {"seg": [{"fx": 1}]}}, FakeModes()  # Strobe
            )
        self.assertEqual(posted(fleet_), [])

    def test_effect_use_is_recorded(self):
        fleet_ = make_fleet()
        mcp_light.call_tool(
            fleet_, "wled_write", {"payload": {"seg": [{"fx": 9}, {"fx": 28}]}}, FakeModes()
        )
        self.assertEqual(lightctl.recent_fx_ids()[:2], [28, 9])  # newest first


class WledReadTests(unittest.TestCase):
    def test_reads_section_per_controller(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(fleet_, "wled_read", {"section": "effects"}, FakeModes())
        data = json.loads(result["content"][0]["text"])
        self.assertEqual(set(data), {"left", "right"})
        self.assertEqual(data["left"], CATALOG)

    def test_single_client_read(self):
        client = RecordingClient("http://x", state={"on": True, "bri": 7})
        result = mcp_light.call_tool(client, "wled_read", {}, FakeModes())
        self.assertEqual(json.loads(result["content"][0]["text"]), {"on": True, "bri": 7})

    def test_unknown_section_rejected(self):
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(fleet_, "wled_read", {"section": "defrag"}, FakeModes())

    def test_controller_error_is_reported_not_raised(self):
        fleet_ = make_fleet()
        fleet_.clients["left"].get_palettes = lambda: (_ for _ in ()).throw(OSError("down"))
        result = mcp_light.call_tool(fleet_, "wled_read", {"section": "palettes"}, FakeModes())
        data = json.loads(result["content"][0]["text"])
        self.assertIn("error", data["left"])
        self.assertEqual(data["right"], ["Default", "Party"])


class FxHistoryTests(unittest.TestCase):
    def setUp(self):
        lightctl._fx_history.clear()

    def tearDown(self):
        lightctl._fx_history.clear()

    def test_newest_first_deduped(self):
        for fx in (9, 28, 9, 63):
            lightctl.record_fx_use(fx, source="test")
        self.assertEqual(lightctl.recent_fx_ids(), [63, 9, 28])

    def test_limit(self):
        for fx in range(20):
            lightctl.record_fx_use(fx)
        self.assertEqual(len(lightctl.recent_fx_ids(limit=5)), 5)

    def test_bad_ids_ignored(self):
        lightctl.record_fx_use(None)
        lightctl.record_fx_use("not-a-number")
        self.assertEqual(lightctl.recent_fx_ids(), [])
        self.assertEqual(lightctl.recent_fx_text(), "")

    def test_text_with_names(self):
        lightctl.record_fx_use(9)
        text = lightctl.recent_fx_text(names=["Solid"] * 9 + ["Rainbow"])
        self.assertIn("9=Rainbow", text)

    def test_tool_paths_record_fx(self):
        mcp_light._fx_allowed.clear()
        lightctl._fx_history.clear()
        fleet_ = make_fleet()
        mcp_light.call_tool(fleet_, "set_effect", {"effect": 9}, FakeModes())
        mcp_light.call_tool(fleet_, "wall_mode", {"mode": "span", "fx": 28}, FakeModes())
        self.assertEqual(lightctl.recent_fx_ids()[:2], [28, 9])
        mcp_light._fx_allowed.clear()

    def test_show_look_records_fx(self):
        lightctl._fx_history.clear()
        fleet_ = make_fleet()
        shows.apply_look(fleet_, {"wall_mode": "span", "fx": 63})
        shows.apply_look(fleet_, {"payload": {"seg": [{"fx": 9}]}})
        self.assertEqual(lightctl.recent_fx_ids()[:2], [9, 63])


class SnapshotHintTests(unittest.TestCase):
    FXDATA = [
        "Solid@;;0;0",                        # 0: no params
        "Strobe@Speed;Color;0;0",             # 1: 🚫 by name
        "Breathe@Speed;Color;0;0",            # 2: sx only -> no hint parts? sx=Speed is a hint
        "GEQ@Speed,Intensity,Gain,,;Fc,St,Bg;0;v",  # 3: colors 1+2+3 + c1=Gain
        "RSVD@;;;;",                          # 4: 🚫 placeholder
    ]

    def test_live_catalog_hints_cover_all_allowed_effects(self):
        hints = light_gui._parse_fxdata_hints(self.FXDATA, CATALOG)
        self.assertIn(3, hints)          # GEQ — not in SAFE_EFFECTS, but live-allowed
        self.assertIn("colors 1+2+3", hints[3])
        self.assertIn("c1=Gain", hints[3])
        self.assertNotIn(1, hints)       # Strobe is 🚫 — no hint
        self.assertNotIn(4, hints)       # RSVD is 🚫 — no hint

    def test_legacy_fallback_without_effects_list(self):
        hints = light_gui._parse_fxdata_hints(self.FXDATA)
        self.assertNotIn(3, hints)       # GEQ is outside the offline allowlist
        self.assertIn(2, hints)          # Breathe is in SAFE_EFFECTS

    def test_snapshot_text_uses_live_names_and_marks_allowed(self):
        snapshot = {
            "state": {"on": True, "bri": 100, "seg": []},
            "info": {"name": "test", "ver": "0.16", "leds": {}},
            "effects": list(CATALOG),
            "palettes": ["Default"],
            "fxdata": list(self.FXDATA),
        }
        text = light_gui.device_snapshot_text(snapshot)
        self.assertIn("Effect parameter hints", text)
        self.assertIn("3 GEQ:", text)

    def test_ai_context_includes_recent_effects(self):
        lightctl._fx_history.clear()
        snapshot = {
            "state": {"on": True, "seg": []},
            "info": {"name": "test", "leds": {}},
            "effects": list(CATALOG),
            "palettes": [],
        }
        client = mock.Mock()
        with mock.patch.object(light_gui, "_device_snapshot", return_value=snapshot):
            without_recent = light_gui.ai_context_text(client)
            self.assertNotIn("Recently used effects", without_recent)
            lightctl.record_fx_use(3)
            with_recent = light_gui.ai_context_text(client)
        self.assertIn("Recently used effects", with_recent)
        self.assertIn("3=GEQ", with_recent)
        lightctl._fx_history.clear()


class DirectorVariantTests(unittest.TestCase):
    AUDIO_FX_IDS = {68, 132, 135, 136, 137, 139, 143, 144, 145, 155, 156, 157,
                    158, 159, 175, 185}

    def test_every_variant_passes_shows_validation(self):
        for index, variants in music_director.MOOD_LOOK_VARIANTS.items():
            self.assertLess(index, len(music_director.MOOD_LOOKS))
            for look in variants:
                steps = shows.validate_show({"steps": [{"look": look, "duration_s": 1}]})
                self.assertEqual(len(steps), 1, f"rule {index}: {look}")

    def test_every_variant_uses_audio_reactive_fx(self):
        for index, variants in music_director.MOOD_LOOK_VARIANTS.items():
            for look in variants:
                for key in ("fx", "fx_left", "fx_right"):
                    if key in look:
                        self.assertIn(look[key], self.AUDIO_FX_IDS,
                                      f"rule {index}:{key}={look[key]} not ♪")

    def test_match_rule_returns_index_and_fallback(self):
        index, keyword = music_director._match_rule("some techno banger")
        self.assertEqual(index, 0)
        self.assertEqual(keyword, "techno")
        index, keyword = music_director._match_rule("qzx nothing matches")
        self.assertEqual(index, len(music_director.MOOD_LOOKS) - 1)
        self.assertEqual(keyword, "default")

    def test_rotation_canonical_first_then_cycles_variants(self):
        director = music_director.MusicDirector(mock.Mock(), poll_s=60)
        rule = 0
        candidates = [music_director.MOOD_LOOKS[rule][1],
                      *music_director.MOOD_LOOK_VARIANTS[rule]]
        seen = [director._look_for_rule(rule) for _ in range(len(candidates) + 1)]
        self.assertIs(seen[0], candidates[0])   # canonical first
        self.assertIs(seen[1], candidates[1])
        self.assertIs(seen[2], candidates[2])
        self.assertIs(seen[3], candidates[0])   # wraps around

    def test_track_changes_rotate_looks(self):
        fleet_ = make_fleet()
        director = music_director.MusicDirector(fleet_, poll_s=60)
        director._handle_track({"artist": "A", "title": "techno one"})
        director._handle_track({"artist": "B", "title": "techno two"})
        fx_sequence = [
            seg["fx"]
            for payload in posted(fleet_) if "seg" in payload
            for seg in payload["seg"] if isinstance(seg, dict) and "fx" in seg
        ]
        # two different tracks of the same mood -> two different looks
        first_look_fx = music_director.MOOD_LOOKS[0][1]["fx"]
        variant_fx = music_director.MOOD_LOOK_VARIANTS[0][0]["fx"]
        self.assertIn(first_look_fx, fx_sequence)
        self.assertIn(variant_fx, fx_sequence)


class PromptKnowledgeTests(unittest.TestCase):
    def test_raw_tools_are_core(self):
        self.assertIn("wled_read", ai_chat._CORE_TOOL_NAMES)
        self.assertIn("wled_write", ai_chat._CORE_TOOL_NAMES)
        names = {tool["function"]["name"] for tool in ai_chat.chat_tools("set an effect")}
        self.assertIn("wled_read", names)
        self.assertIn("wled_write", names)

    def test_system_prompt_teaches_raw_access_and_variety(self):
        prompt = ai_chat.TOOL_CHAT_SYSTEM_PROMPT
        self.assertIn("wled_read", prompt)
        self.assertIn("wled_write", prompt)
        self.assertIn("Recently used", prompt)
        self.assertIn("VARIETY", prompt)
        self.assertIn("psave", prompt)  # raw API cheat-sheet
        self.assertIn("c1/c2/c3", prompt)


if __name__ == "__main__":
    unittest.main()
