import unittest
from unittest.mock import MagicMock

import ai_chat
import effect_metadata
import light_gui
import lightctl
import mcp_light


class FakeClient:
    def __init__(self):
        self.payloads = []

    def post_state(self, payload):
        self.payloads.append(payload)


# Real-format fxdata samples (WLED 0.14+ FX.cpp).
FIRE_2012 = "Fire 2012@Cooling,Spark rate,,2D Blur,Boost;;!;1;pal=35,sx=64,ix=160,m12=1,c2=128"
AURORA = "Aurora@!,!;1,2,3;!;;sx=24,pal=50"
BLINK = "Blink@!,Duty cycle;!,!;!;01"
GEQ = "GEQ@!,!;;;v"


class ParseEntryTests(unittest.TestCase):
    def test_full_five_sections_with_name_prefix(self):
        meta = effect_metadata.parse_fxdata_entry(FIRE_2012)

        self.assertEqual(
            meta.sliders,
            {"sx": "Cooling", "ix": "Spark rate", "c1": None, "c2": "2D Blur", "c3": "Boost"},
        )
        self.assertEqual(meta.color_labels, ["Color 1", "Color 2", "Color 3"])
        self.assertTrue(meta.uses_palette)
        self.assertTrue(meta.one_d)
        self.assertFalse(meta.two_d)
        self.assertFalse(meta.audio_volume)
        self.assertFalse(meta.audio_freq)
        self.assertEqual(
            meta.defaults,
            {"pal": 35, "sx": 64, "ix": 160, "m12": 1, "c2": 128},
        )

    def test_bang_labels_fall_back_to_default_names(self):
        meta = effect_metadata.parse_fxdata_entry(AURORA)

        self.assertEqual(meta.sliders["sx"], "Speed")
        self.assertEqual(meta.sliders["ix"], "Intensity")
        self.assertIsNone(meta.sliders["c1"])
        self.assertIsNone(meta.sliders["c2"])
        self.assertIsNone(meta.sliders["c3"])
        self.assertEqual(meta.color_labels, ["1", "2", "3"])
        self.assertTrue(meta.uses_palette)
        self.assertFalse(meta.one_d)
        self.assertEqual(meta.defaults, {"sx": 24, "pal": 50})

    def test_missing_sections_use_defaults(self):
        meta = effect_metadata.parse_fxdata_entry("Solid")

        # No metadata at all: speed+intensity shown, 3 colors, palette enabled.
        self.assertEqual(meta.sliders["sx"], "Speed")
        self.assertEqual(meta.sliders["ix"], "Intensity")
        self.assertIsNone(meta.sliders["c1"])
        self.assertEqual(len(meta.color_labels), 3)
        self.assertTrue(meta.uses_palette)
        self.assertEqual(meta.defaults, {})

    def test_empty_entry_uses_defaults(self):
        for entry in ("", None, "@"):
            meta = effect_metadata.parse_fxdata_entry(entry)
            self.assertEqual(meta.sliders["sx"], "Speed")
            self.assertEqual(meta.sliders["ix"], "Intensity")
            self.assertTrue(meta.uses_palette)

    def test_partial_params_hide_remaining_sliders(self):
        meta = effect_metadata.parse_fxdata_entry("!;")  # sx default label only

        self.assertEqual(meta.sliders["sx"], "Speed")
        self.assertIsNone(meta.sliders["ix"])
        self.assertIsNone(meta.sliders["c1"])

    def test_empty_palette_section_hides_palette(self):
        meta = effect_metadata.parse_fxdata_entry("Fx@!;;;1")

        self.assertFalse(meta.uses_palette)
        self.assertTrue(meta.one_d)

    def test_missing_palette_section_enables_palette(self):
        meta = effect_metadata.parse_fxdata_entry("Fx@!")

        self.assertTrue(meta.uses_palette)

    def test_flags_volume_freq_2d_3d(self):
        self.assertTrue(effect_metadata.parse_fxdata_entry(GEQ).audio_volume)
        freq = effect_metadata.parse_fxdata_entry("F@!;;;f")
        self.assertTrue(freq.audio_freq)
        self.assertFalse(freq.audio_volume)
        both = effect_metadata.parse_fxdata_entry("F@!;;;vf")
        self.assertTrue(both.audio_volume)
        self.assertTrue(both.audio_freq)
        self.assertTrue(both.audio_reactive)
        two_d = effect_metadata.parse_fxdata_entry("F@!;;;2")
        self.assertTrue(two_d.two_d)
        three_d = effect_metadata.parse_fxdata_entry("F@!;;;3")
        self.assertTrue(three_d.three_d)
        # '0' (single-LED) flag is ignored, '1' still parsed from digit runs.
        blink = effect_metadata.parse_fxdata_entry(BLINK)
        self.assertTrue(blink.one_d)
        self.assertFalse(blink.audio_reactive)

    def test_defaults_skip_malformed_pairs_and_keep_strings(self):
        meta = effect_metadata.parse_fxdata_entry("F@!;;;;sx=24,bogus,pal=Party,ix=")

        self.assertEqual(meta.defaults["sx"], 24)
        self.assertNotIn("bogus", meta.defaults)
        self.assertEqual(meta.defaults["pal"], "Party")
        self.assertEqual(meta.defaults["ix"], "")

    def test_malformed_inputs_do_not_raise(self):
        for entry in (None, 123, ";;;", "@", "name@", ";", "!,,,"):
            meta = effect_metadata.parse_fxdata_entry(entry)
            self.assertIsInstance(meta.sliders, dict)
            self.assertIsInstance(meta.color_labels, list)


class ParseCatalogTests(unittest.TestCase):
    def test_filters_reserved_and_placeholder_names(self):
        names = ["Solid", "RSVD", "Blink", "-", ""]
        fxdata = ["", "", BLINK, "", ""]

        catalog = effect_metadata.parse_fxdata(names, fxdata)

        self.assertEqual(sorted(catalog), [0, 2])
        self.assertEqual(catalog[0].name, "Solid")
        self.assertEqual(catalog[2].name, "Blink")
        self.assertEqual(catalog[2].id, 2)
        self.assertEqual(catalog[2].sliders["ix"], "Duty cycle")

    def test_case_insensitive_rsvd_filtering(self):
        catalog = effect_metadata.parse_fxdata(["rsvd", "Rsvd", "Solid"], [])

        self.assertEqual(list(catalog), [2])

    def test_shorter_fxdata_list_uses_defaults(self):
        catalog = effect_metadata.parse_fxdata(["Solid", "Blink"], [])

        self.assertEqual(catalog[1].sliders["sx"], "Speed")
        self.assertTrue(catalog[1].uses_palette)

    def test_name_recovered_from_prefix_when_names_missing(self):
        catalog = effect_metadata.parse_fxdata([], [FIRE_2012])

        self.assertEqual(catalog[0].name, "Fire 2012")
        self.assertEqual(catalog[0].sliders["sx"], "Cooling")

    def test_none_name_entries_skipped(self):
        catalog = effect_metadata.parse_fxdata([None, "Solid"], ["", ""])

        self.assertEqual(list(catalog), [1])


class RenderTests(unittest.TestCase):
    def test_compact_line_includes_labels_flags_palette_defaults(self):
        meta = effect_metadata.parse_fxdata([], [FIRE_2012])[0]
        line = effect_metadata.render_effect_line(meta)

        self.assertIn("0 Fire 2012", line)
        self.assertIn("[1D]", line)
        self.assertIn("sx=Cooling", line)
        self.assertIn("ix=Spark rate", line)
        self.assertIn("c2=2D Blur", line)
        self.assertNotIn("c1", line)  # hidden slider omitted
        self.assertIn("pal=yes", line)
        self.assertIn("defaults: pal=35, sx=64, ix=160", line)

    def test_compact_line_marks_audio_and_hidden_palette(self):
        meta = effect_metadata.parse_fxdata([], ["GEQ@!,!;;;vf"])[0]
        line = effect_metadata.render_effect_line(meta)

        self.assertIn("[vol]", line)
        self.assertIn("[freq]", line)
        self.assertIn("pal=no", line)

    def test_compact_line_omits_default_sections(self):
        meta = effect_metadata.parse_fxdata(["Solid"], [""])[0]
        line = effect_metadata.render_effect_line(meta)

        self.assertEqual(line, "0 Solid")

    def test_catalog_text_skips_uninformative_effects(self):
        names = ["Solid", "Fire 2012", "RSVD"]
        fxdata = ["", FIRE_2012, ""]

        text = effect_metadata.render_catalog_text(names, fxdata)

        self.assertIn("Fire 2012", text)
        self.assertNotIn("RSVD", text)
        lines = [l for l in text.splitlines() if l.strip().startswith(("0 ", "1 ", "2 "))]
        self.assertEqual(len(lines), 1)  # Solid has nothing beyond defaults

    def test_catalog_text_empty_when_nothing_informative(self):
        self.assertEqual(effect_metadata.render_catalog_text(["Solid"], [""]), "")
        self.assertEqual(effect_metadata.render_catalog_text([], []), "")


class EffectPayloadFxdefTests(unittest.TestCase):
    def test_effect_payload_fxdef_sets_segment_flag(self):
        self.assertEqual(
            lightctl.effect_payload(28, fxdef=True),
            {"seg": [{"fx": 28, "sx": 128, "fxdef": True}]},
        )

    def test_effect_payload_without_fxdef_unchanged(self):
        self.assertEqual(
            lightctl.effect_payload(28, 170),
            {"seg": [{"fx": 28, "sx": 170}]},
        )


class McpSetEffectTests(unittest.TestCase):
    def test_set_effect_schema_offers_fxdef(self):
        tool = next(t for t in mcp_light.build_tools() if t["name"] == "set_effect")

        self.assertIn("fxdef", tool["inputSchema"]["properties"])
        self.assertNotIn("fxdef", tool["inputSchema"].get("required", []))
        self.assertIn("metadata", tool["description"].lower())

    def test_set_effect_tool_sends_fxdef(self):
        client = FakeClient()
        result = mcp_light.call_tool(
            client, "set_effect", {"effect": 28, "fxdef": True}, MagicMock()
        )

        self.assertEqual(client.payloads[0]["seg"][0]["fxdef"], True)
        self.assertIn("effect 28", result["content"][0]["text"].lower())

    def test_set_effect_tool_without_fxdef_sends_no_flag(self):
        client = FakeClient()
        mcp_light.call_tool(client, "set_effect", {"effect": 28}, MagicMock())

        self.assertNotIn("fxdef", client.payloads[0]["seg"][0])


class PromptIntegrationTests(unittest.TestCase):
    def test_system_prompt_explains_fxdata(self):
        prompt = ai_chat.TOOL_CHAT_SYSTEM_PROMPT

        self.assertIn("fxdata", prompt)
        self.assertIn("fxdef", prompt)
        self.assertIn("audio-reactive", prompt)

    def test_chat_tools_carry_fxdef_schema(self):
        tool = next(
            t for t in ai_chat.chat_tools("set an effect")
            if t["function"]["name"] == "set_effect"
        )

        self.assertIn("fxdef", tool["function"]["parameters"]["properties"])

    def test_snapshot_catalog_includes_metadata_lines(self):
        snapshot = {
            "state": {"on": True, "bri": 100, "seg": []},
            "info": {"name": "WLED", "ver": "0.14.0", "leds": {}},
            "effects": ["Solid", "Fire 2012"],
            "fxdata": ["", FIRE_2012],
            "palettes": ["Default"],
        }

        text = light_gui.device_snapshot_text(snapshot)

        self.assertIn("Fire 2012", text)
        self.assertIn("sx=Cooling", text)
        self.assertIn("defaults:", text)

    def test_snapshot_text_survives_malformed_fxdata(self):
        snapshot = {
            "state": {"on": True, "seg": []},
            "info": {},
            "effects": ["Solid"],
            "fxdata": [None],
            "palettes": [],
        }

        text = light_gui.device_snapshot_text(snapshot)

        self.assertIn("Solid", text)


if __name__ == "__main__":
    unittest.main()
