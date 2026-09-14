from __future__ import annotations

import color_lab


def _stop_hue(rgb: tuple[int, int, int]) -> float:
    import colorsys

    hue, _light, _sat = colorsys.rgb_to_hls(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
    return (hue * 360.0) % 360.0


def _circular_spread(hues: list[float]) -> float:
    import math

    if len(hues) < 2:
        return 0.0
    angles = [math.radians(hue) for hue in hues]
    x = sum(math.cos(angle) for angle in angles) / len(angles)
    y = sum(math.sin(angle) for angle in angles) / len(angles)
    mean_length = min(1.0, math.hypot(x, y))
    return math.degrees(math.acos(mean_length)) * 2.0


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
    assert color_lab.choose_shader(shader="twin_helix") == "twin_helix"
    assert color_lab.choose_shader(shader="not-real") == "liquid_gradient"


def test_hintless_palettes_sweep_the_hue_wheel_instead_of_staying_amber():
    hues = [_stop_hue(color_lab.palette_from_prompt("moody drift", seed=seed)[0]) for seed in range(24)]
    assert _circular_spread(hues) > 90.0
    # Hint words still anchor the color family (ocean -> ~198 degrees +- jitter).
    for seed in range(6):
        ocean_hue = _stop_hue(color_lab.palette_from_prompt("cold ocean tide", seed=seed)[0])
        assert 160.0 <= ocean_hue <= 240.0


def test_shader_fallback_rotates_per_seed_instead_of_always_liquid_gradient():
    picks = {color_lab.choose_shader(mood="moody lounge", seed=seed) for seed in range(20)}
    assert len(picks) >= 6
    assert all(pick in color_lab.SHADERS for pick in picks)
    assert color_lab.choose_shader(mood="moody lounge", seed=4) == color_lab.choose_shader(mood="moody lounge", seed=4)


def test_composition_fallback_rotates_per_seed_instead_of_always_unison():
    picks = {color_lab.choose_composition(mood="ambient drift", seed=seed) for seed in range(20)}
    assert len(picks) >= 4
    assert all(pick in color_lab.COMPOSITION_MODES for pick in picks)
    assert color_lab.choose_composition(mood="ambient drift", seed=4) == color_lab.choose_composition(mood="ambient drift", seed=4)
    assert color_lab.choose_composition(composition_mode="pairs", seed=1) == "pairs"
    assert color_lab.choose_composition(mood="split duel", seed=1) == "left_vs_right"


def test_build_look_without_hints_variates_shader_composition_and_palette():
    looks = [color_lab.build_look(mood="moody lounge", seed=seed) for seed in range(12)]
    shaders = {look["shader"] for look in looks}
    compositions = {look["composition_mode"] for look in looks}
    assert len(shaders) >= 4
    assert len(compositions) >= 3
    assert all(look["shader"] in color_lab.SHADERS for look in looks)
    assert all(look["composition_mode"] in color_lab.COMPOSITION_MODES for look in looks)


def test_build_look_recipe_is_runnable_and_bounded():
    look = color_lab.build_look(mood="neon storm", energy="bright", motion="drift", seed="storm-1")
    assert look["shader"] in color_lab.SHADERS
    assert 2 <= len(look["colors"]) <= 5
    assert look["composition_mode"] in color_lab.COMPOSITION_MODES
    assert 0.05 <= look["intensity"] <= 0.82
    assert look["seed"] == "storm-1"
