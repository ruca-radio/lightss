from __future__ import annotations

import color_lab


def test_parse_hex_rgb_named_and_rgbw():
    assert color_lab.parse_color("#ff6600") == (color_lab.RGB_CAP, 102, 0)
    assert color_lab.parse_color([10, 20, 30, 40]) == (10, 20, 30)
    assert color_lab.parse_color("teal")[1] > color_lab.parse_color("teal")[0]


def test_normalize_palette_clamps_and_keeps_two_to_five_stops():
    palette = color_lab.normalize_palette(["#ffffff", [400, -3, 9], "not-a-color", "#112233", "#445566", "#778899"])
    assert 2 <= len(palette) <= 5
    assert all(0 <= channel <= color_lab.RGB_CAP for rgb in palette for channel in rgb)


def test_prompt_palettes_are_unique_per_seed_and_mood():
    ember_a = color_lab.palette_from_prompt("ember dusk", seed=7)
    ember_b = color_lab.palette_from_prompt("ember dusk", seed=7)
    ember_c = color_lab.palette_from_prompt("ember dusk", seed=8)
    ocean = color_lab.palette_from_prompt("cold ocean tide", seed=7)
    assert ember_a == ember_b
    assert ember_a != ember_c
    assert ember_a != ocean
    assert all(sum(stop) > 0 for stop in ember_a)


def test_choose_shader_maps_motion_and_falls_back_to_auto():
    assert color_lab.choose_shader(mood="fireplace", motion="rise") == "ember_rise"
    assert color_lab.choose_shader(mood="ocean", motion="fall") == "tide_pull"
    assert color_lab.choose_shader(mood="sunrise gold") != "ember_rise"
    assert color_lab.choose_shader(shader="twin_helix") == "twin_helix"
    assert color_lab.choose_shader(shader="not-real") == "liquid_gradient"


def test_build_look_recipe_is_runnable_and_bounded():
    look = color_lab.build_look(mood="neon storm", energy="bright", motion="drift", seed="storm-1")
    assert look["shader"] in color_lab.SHADERS
    assert 2 <= len(look["colors"]) <= 5
    assert look["composition_mode"] in color_lab.COMPOSITION_MODES
    assert 0.05 <= look["intensity"] <= 0.82
    assert look["seed"] == "storm-1"
