import unittest

import ai_chat
import dynamic_scenes
import fleet
import light_gui
import mcp_light
import palette_lab


# Mirrors the stock WLED palette list order (array index == palette id).
STOCK_PALETTES = [
    "Default", "Random Cycle", "Color 1", "Colors 1&2", "Color Gradient", "Colors Only",
    "Party", "Cloud", "Lava", "Ocean", "Forest", "Rainbow", "Rainbow Colors", "Sunset",
    "Rivendell", "Breeze", "Red & Blue", "Yellowout", "Analogous", "Splash", "Pastel",
    "Sunset 2", "Beech", "Vintage", "Departure", "Landscape", "Beach", "Sherbet", "Hult",
    "Hult 64", "Drywet", "Jul", "Grintage", "Rewhi", "Tertiary", "Fire", "Icefire",
    "Cyane", "Light Pink", "Autumn", "Magenta", "Magred", "Yelmag", "Yelblu",
    "Orange & Teal", "Tiamat", "April Night", "Orangery", "C9", "Sakura", "Aurora",
    "Atlantica", "C9 2", "C9 New", "Temperature",
]


class FakePaletteClient:
    """Single-controller fake: records payloads, serves a fixed palette list."""

    def __init__(self, palettes):
        self._palettes = palettes
        self.payloads = []

    def get_palettes(self):
        return list(self._palettes)

    def post_state(self, payload):
        self.payloads.append(payload)


class RecordingFleet:
    """Topology fake (same shape as tests/test_dynamic_scenes.py), no network."""

    def __init__(self, palettes=None):
        self.installation, self.controllers = fleet.load_topology({})
        self.posts = []
        self._palettes = palettes

    def channels(self):
        return {
            segment.channel: (controller.name, seg_id)
            for controller in self.controllers
            for seg_id, segment in controller.segments.items()
        }

    def resolve(self, target):
        return [(target, None)]

    def names(self):
        return [controller.name for controller in self.controllers]

    def get_palettes(self):
        return list(self._palettes) if self._palettes is not None else []

    def post_state(self, payload, target="all"):
        self.posts.append((target, payload))
        return {target: {"ok": True}}


class PaletteKnowledgeTests(unittest.TestCase):
    def test_known_stable_ids_match_wled_docs(self):
        self.assertEqual(palette_lab.KNOWN_PALETTES["Fire"][0], 35)
        self.assertEqual(palette_lab.KNOWN_PALETTES["Icefire"][0], 36)
        self.assertEqual(palette_lab.KNOWN_PALETTES["Aurora"][0], 50)
        self.assertEqual(palette_lab.KNOWN_PALETTES["Temperature"][0], 54)

    def test_covers_the_classic_set(self):
        classics = {
            "Party", "Cloud", "Lava", "Ocean", "Forest", "Rainbow", "Rainbow Colors",
            "Sunset", "Fire", "Icefire", "Aurora", "Temperature",
        }
        self.assertTrue(classics.issubset(set(palette_lab.KNOWN_PALETTES)))
        for _name, (typical_id, description) in palette_lab.KNOWN_PALETTES.items():
            self.assertIsInstance(typical_id, int)
            self.assertTrue(description.strip(), "every curated palette needs a description")

    def test_dynamic_palette_semantics_cover_ids_zero_to_five(self):
        self.assertEqual(sorted(palette_lab.DYNAMIC_PALETTES), [0, 1, 2, 3, 4, 5])
        self.assertIn("Default", palette_lab.DYNAMIC_PALETTES[0])
        self.assertIn("Random Cycle", palette_lab.DYNAMIC_PALETTES[1])
        line = palette_lab.dynamic_palette_line()
        self.assertIn("0-5", line)
        self.assertIn("Color Gradient", line)

    def test_curated_ids_are_typical_only_and_resolve_live(self):
        # Curated table agrees with the stock list order where both exist.
        for name, (typical_id, _desc) in palette_lab.KNOWN_PALETTES.items():
            if name in STOCK_PALETTES:
                self.assertEqual(STOCK_PALETTES.index(name), typical_id, name)


class PaletteResolutionTests(unittest.TestCase):
    def test_exact_match_returns_array_index(self):
        self.assertEqual(palette_lab.resolve_palette_id("Fire", STOCK_PALETTES), 35)
        self.assertEqual(palette_lab.resolve_palette_id("Default", STOCK_PALETTES), 0)

    def test_case_insensitive_match(self):
        self.assertEqual(palette_lab.resolve_palette_id("icefire", STOCK_PALETTES), 36)
        self.assertEqual(palette_lab.resolve_palette_id("RAINBOW", STOCK_PALETTES), 11)

    def test_normalized_match_tolerates_punctuation_and_spacing(self):
        self.assertEqual(palette_lab.resolve_palette_id("colors 1 & 2", STOCK_PALETTES), 3)
        self.assertEqual(palette_lab.resolve_palette_id("sunset  2", STOCK_PALETTES), 21)
        self.assertEqual(palette_lab.resolve_palette_id("Orange and Teal", STOCK_PALETTES), 44)

    def test_prefix_markers_are_ignored(self):
        names = ["Default", "~Fire", "*Sunset"]
        self.assertEqual(palette_lab.resolve_palette_id("Fire", names), 1)
        self.assertEqual(palette_lab.resolve_palette_id("~Fire", names), 1)
        self.assertEqual(palette_lab.resolve_palette_id("Sunset", names), 2)

    def test_containment_fallback_picks_shortest_candidate(self):
        self.assertEqual(palette_lab.resolve_palette_id("fire", ["Default", "Icefire"]), 1)
        # Exact match still beats containment.
        self.assertEqual(
            palette_lab.resolve_palette_id("Sunset", ["Default", "Sunset 2", "Sunset"]), 2
        )

    def test_missing_name_returns_none(self):
        self.assertIsNone(palette_lab.resolve_palette_id("Volcano", STOCK_PALETTES))
        self.assertIsNone(palette_lab.resolve_palette_id("Fire", []))
        self.assertIsNone(palette_lab.resolve_palette_id("", STOCK_PALETTES))

    def test_close_matches_help_with_typos(self):
        matches = palette_lab.close_matches("Fiire", STOCK_PALETTES)
        self.assertIn("Fire", matches)
        self.assertIn("Sunset", palette_lab.close_matches("sunse", STOCK_PALETTES))

    def test_resolve_any_returns_first_live_candidate(self):
        self.assertEqual(palette_lab.resolve_any(["Nope", "Fire", "Sunset"], STOCK_PALETTES), 35)
        self.assertIsNone(palette_lab.resolve_any(["Nope", "Also nope"], STOCK_PALETTES))


class MoodSuggestionTests(unittest.TestCase):
    def test_warm_cozy_suggests_fire_like_palettes(self):
        suggestions = palette_lab.suggest_palettes("warm cozy evening", STOCK_PALETTES)
        ids = [pid for pid, _name in suggestions]
        self.assertIn(35, ids)  # Fire resolved live
        self.assertEqual(suggestions[0], (35, "Fire"))

    def test_ocean_mood_resolves_live_ids(self):
        suggestions = palette_lab.suggest_palettes("calm ocean water", STOCK_PALETTES)
        self.assertIn((9, "Ocean"), suggestions)

    def test_suggestions_degrade_gracefully_when_names_absent(self):
        self.assertEqual(palette_lab.suggest_palettes("warm cozy", ["Default", "Party"]), [])
        self.assertEqual(palette_lab.suggest_palettes("warm cozy", []), [])

    def test_unknown_mood_suggests_nothing(self):
        self.assertEqual(palette_lab.suggest_palettes("zqxwv jibberish", STOCK_PALETTES), [])


class PromptContextTests(unittest.TestCase):
    def test_prompt_lines_annotate_curated_palettes_with_live_ids(self):
        lines = palette_lab.prompt_lines(STOCK_PALETTES)
        text = "\n".join(lines)
        self.assertIn("35 Fire", text)
        self.assertIn("0-5", text)

    def test_prompt_lines_empty_without_live_list(self):
        self.assertEqual(palette_lab.prompt_lines([]), [])

    def test_snapshot_text_includes_palette_notes(self):
        snapshot = {
            "state": {"on": True, "bri": 100, "seg": []},
            "info": {"name": "WLED-test", "ver": "0.15.0"},
            "palettes": STOCK_PALETTES,
            "effects": [],
        }
        text = light_gui.device_snapshot_text(snapshot, include_catalog=False)
        self.assertIn("35 Fire", text)
        self.assertIn("Palette ids 0-5 are dynamic", text)

    def test_ai_chat_prompt_explains_dynamic_palette_ids(self):
        self.assertIn("0-5 are dynamic", ai_chat.TOOL_CHAT_SYSTEM_PROMPT)


class DynamicScenesPaletteTests(unittest.TestCase):
    def test_fallback_ids_unchanged_without_live_list(self):
        self.assertEqual(dynamic_scenes._palette_id("sunset"), 35)
        self.assertEqual(dynamic_scenes._palette_id("purple"), 10)
        self.assertEqual(dynamic_scenes._palette_id("rainbow"), 11)
        self.assertEqual(dynamic_scenes._palette_id("default"), 0)
        self.assertEqual(dynamic_scenes._palette_id("warm", []), dynamic_scenes.PALETTES["warm"])

    def test_live_list_overrides_fallback_ids(self):
        names = ["Default", "Fire", "Sunset", "Rainbow", "Lava", "Ocean", "Forest", "Magenta"]
        self.assertEqual(dynamic_scenes._palette_id("sunset", names), 2)
        self.assertEqual(dynamic_scenes._palette_id("warm", names), 4)  # warm -> Lava, resolved live
        self.assertEqual(dynamic_scenes._palette_id("rainbow", names), 3)

    def test_compose_effect_uses_fallback_palettes_without_live_list(self):
        f = RecordingFleet()
        payloads = dynamic_scenes.compose_dynamic_scene(f, engine="effect", strategy="quiet_gradient")
        for payload in payloads.values():
            for seg in payload["seg"]:
                self.assertEqual(seg["pal"], 35)

    def test_compose_effect_resolves_against_live_list(self):
        names = ["Default", "Fire", "Sunset", "Rainbow", "Lava", "Ocean", "Forest", "Magenta"]
        f = RecordingFleet(palettes=names)
        payloads = dynamic_scenes.compose_dynamic_scene(f, engine="effect", strategy="quiet_gradient")
        for payload in payloads.values():
            for seg in payload["seg"]:
                self.assertEqual(seg["pal"], 2)  # live index of "Sunset"
        chase = dynamic_scenes.compose_dynamic_scene(f, engine="effect", strategy="left_to_right")
        for payload in chase.values():
            for seg in payload["seg"]:
                self.assertEqual(seg["pal"], 3)  # live index of "Rainbow"


class SetPaletteToolTests(unittest.TestCase):
    def test_tool_is_registered_with_name_and_id_schema(self):
        tools = {tool["name"]: tool for tool in mcp_light.build_tools()}
        self.assertIn("set_palette", tools)
        props = tools["set_palette"]["inputSchema"]["properties"]
        self.assertIn("name", props)
        self.assertIn("palette", props)

    def test_tool_is_part_of_the_core_chat_surface(self):
        names = {tool["function"]["name"] for tool in ai_chat.chat_tools("make it cozy")}
        self.assertIn("set_palette", names)

    def test_set_palette_by_name_resolves_live(self):
        client = FakePaletteClient(STOCK_PALETTES)
        result = mcp_light.call_tool(client, "set_palette", {"name": "fire"}, None)
        self.assertEqual(client.payloads, [{"seg": [{"pal": 35}]}])
        self.assertIn("Fire", result["content"][0]["text"])

    def test_set_palette_by_id(self):
        client = FakePaletteClient(STOCK_PALETTES)
        mcp_light.call_tool(client, "set_palette", {"palette": 13}, None)
        self.assertEqual(client.payloads, [{"seg": [{"pal": 13}]}])

    def test_set_palette_unknown_name_lists_close_matches(self):
        client = FakePaletteClient(STOCK_PALETTES)
        with self.assertRaises(ValueError) as ctx:
            mcp_light.call_tool(client, "set_palette", {"name": "Fiire"}, None)
        self.assertIn("Fire", str(ctx.exception))
        self.assertEqual(client.payloads, [])

    def test_set_palette_requires_name_or_id(self):
        client = FakePaletteClient(STOCK_PALETTES)
        with self.assertRaises(ValueError):
            mcp_light.call_tool(client, "set_palette", {}, None)

    def test_set_palette_id_out_of_range_is_helpful(self):
        client = FakePaletteClient(["Default", "Party"])
        with self.assertRaises(ValueError) as ctx:
            mcp_light.call_tool(client, "set_palette", {"palette": 9}, None)
        self.assertIn("2 palettes", str(ctx.exception))

    def test_set_palette_name_without_live_list_errors_politely(self):
        class NoPaletteClient:
            def __init__(self):
                self.payloads = []

            def post_state(self, payload):
                self.payloads.append(payload)

        client = NoPaletteClient()
        with self.assertRaises(ValueError) as ctx:
            mcp_light.call_tool(client, "set_palette", {"name": "Fire"}, None)
        self.assertIn("palette id", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
