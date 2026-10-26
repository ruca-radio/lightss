"""WLED effect/palette reference (wiki descriptions merged with live ids)."""

from __future__ import annotations

import json

import pytest

import mcp_light
import music_chapters
import show_library
import wled_reference


# ---------------------------------------------------------------------------
# Reference table
# ---------------------------------------------------------------------------

def test_reference_table_is_complete_and_clean():
    stats = wled_reference.stats()
    assert stats == {"effects": 118, "palettes": 56}
    for _wiki_id, name, description, intensity in wled_reference.EFFECTS:
        assert name and description
        assert "\\" not in description and "<br" not in description and "![" not in description
        if intensity is not None:
            assert intensity and "\\" not in intensity
    for _wiki_id, name, description in wled_reference.PALETTES:
        assert name and description
        assert "\\" not in description and "<br" not in description


def test_effect_lookup_includes_intensity_meaning():
    blink = wled_reference.effect_info("Blink")
    assert blink["description"].startswith("Blinks between primary and secondary")
    assert blink["intensity"] == "Duty cycle of blinking"
    assert wled_reference.effect_info("Sunrise")["intensity"] == "Sun intensity"
    assert wled_reference.effect_info("Chase")["intensity"] == "Size of chaser"


def test_effect_lookup_handles_plus_and_renames():
    assert "multiple random LEDs" in wled_reference.effect_info("Sparkle+")["description"]
    assert "Single random LEDs" in wled_reference.effect_info("Sparkle")["description"]
    # WLED 0.14 renames still resolve to the older wiki entry by name shape.
    assert wled_reference.effect_info("Scan Dual")["name"] == "Dual Scan"
    assert wled_reference.effect_info("Running Dual")["name"] == "Running 2"


def test_palette_lookup_and_aliases():
    assert wled_reference.palette_info("Ocean")["description"] == "Blue, teal and white colors"
    assert wled_reference.palette_info("Analogous")["name"] == "Analoguous"
    assert wled_reference.palette_info("* Color 1")["name"] == "Primary color"
    assert wled_reference.palette_info("Nope") is None


def test_unknown_effect_is_none_and_annotation_does_not_mutate():
    assert wled_reference.effect_info("Swirl") is None  # newer effect, no wiki entry
    source = {"id": 7, "name": "Nope", "audio": "v"}
    assert wled_reference.annotate_effect(source) == source
    assert "description" not in source
    annotated = wled_reference.annotate_effect({"id": 2, "name": "Fireworks"})
    assert annotated["description"] == "Random color blobs light up, then fade again"
    assert annotated["intensity"] == "Amount of fireworks"


def test_search_supports_tokens_ids_and_limits():
    fire = wled_reference.search_effects("fire", 3)
    assert [row["name"] for row in fire] == ["Fireworks", "Rain", "Fire Flicker"]
    assert wled_reference.search_effects("chase flash")[0]["name"] == "Chase Flash"
    id_rows = wled_reference.search_effects("28")
    assert [row["name"] for row in id_rows] == ["Chase"]  # numeric query = exact wiki id
    assert len(wled_reference.search_effects("", 5)) == 5
    assert len(wled_reference.search_effects("fire", 999)) <= 25
    assert wled_reference.search_palettes("ocean")[0]["name"] == "Ocean"


# ---------------------------------------------------------------------------
# Chapter candidate enrichment
# ---------------------------------------------------------------------------

def test_show_library_candidates_carry_descriptions():
    index = {
        "effects": [
            {"id": 28, "name": "Chase", "audio": "v", "energy": "high", "motion": "chase"},
            {"id": 999, "name": "Swirl", "audio": "v", "energy": "high", "motion": "flow"},
        ],
        "palettes": [
            {"id": 9, "name": "Ocean", "temperature": "cold"},
            {"id": 37, "name": "Cyane", "temperature": "cold"},
        ],
    }
    result = show_library.candidates(index, "x", {"level_avg": 0.6}, {}, limit=8)
    chase = next(effect for effect in result["effects"] if effect["name"] == "Chase")
    assert chase["description"].startswith("2 LEDs in primary color")
    assert chase["intensity"] == "Size of chaser"
    swirl = next(effect for effect in result["effects"] if effect["name"] == "Swirl")
    assert "description" not in swirl  # unknown effects keep their plain shape
    assert result["palettes"][0]["description"] == "Blue, teal and white colors"


def test_chapter_prompt_tells_the_model_to_use_descriptions():
    assert "description and intensity" in music_chapters.SYSTEM


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------

class _FakeClient:
    host = "http://fake-controller"

    def get_effects(self):
        return ["Solid", "Chase", "Swirl", "Fireworks"]

    def get_palettes(self):
        return ["Default", "Ocean", "Cyane", "Analogous"]


class _FakeFleet:
    def __init__(self):
        self.clients = {"left": _FakeClient()}

    def channels(self):
        return {"far-left": ("left", 0)}

    def resolve(self, target="all"):
        return [("left", 0)]


def _tool_payload(fleet, arguments):
    result = mcp_light.call_tool(fleet, "wled_reference", arguments, None)
    assert result["content"][0]["type"] == "text"
    return json.loads(result["content"][0]["text"])


def test_tool_is_registered():
    tool = next(tool for tool in mcp_light.build_tools() if tool["name"] == "wled_reference")
    assert tool["inputSchema"]["required"] == ["kind"]
    assert tool["inputSchema"]["properties"]["kind"]["enum"] == ["effect", "palette"]


def test_tool_returns_live_ids_with_descriptions():
    payload = _tool_payload(_FakeFleet(), {"kind": "effect"})
    assert payload["kind"] == "effect"
    assert payload["matched"] == 4
    item = next(row for row in payload["items"] if row["name"] == "Chase")
    assert item == {
        "id": 1,
        "name": "Chase",
        "description": "2 LEDs in primary color running on secondary",
        "intensity": "Size of chaser",
    }
    unknown = next(row for row in payload["items"] if row["name"] == "Swirl")
    assert unknown["description"] is None and unknown["intensity"] is None


def test_tool_filters_and_limits():
    payload = _tool_payload(_FakeFleet(), {"kind": "effect", "query": "fire", "limit": 1})
    assert payload["matched"] == 1
    assert payload["items"][0]["name"] == "Fireworks"
    palettes = _tool_payload(_FakeFleet(), {"kind": "palette", "query": "ocean"})
    assert palettes["items"][0] == {
        "id": 1, "name": "Ocean", "description": "Blue, teal and white colors",
    }
    analogous = _tool_payload(_FakeFleet(), {"kind": "palette", "query": "analogous"})
    assert analogous["items"][0]["description"] == "Red running on blue"


def test_tool_works_with_a_single_client():
    payload = _tool_payload(_FakeClient(), {"kind": "palette", "limit": 2})
    assert [row["name"] for row in payload["items"]] == ["Default", "Ocean"]


@pytest.mark.parametrize("arguments", [
    {"kind": "songs"},
    {},
    {"kind": "effect", "limit": 0},
    {"kind": "effect", "limit": 21},
    {"kind": "effect", "limit": True},
    {"kind": "effect", "query": "x" * 121},
])
def test_tool_rejects_bad_arguments(arguments):
    with pytest.raises(ValueError):
        mcp_light.call_tool(_FakeFleet(), "wled_reference", arguments, None)
