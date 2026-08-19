#!/usr/bin/env python3
"""Bounded color intelligence for unique Lightss looks."""

from __future__ import annotations

import colorsys
import random
import re
from typing import Any, Iterable, Sequence

RGB_CAP = 210
SHADERS = {
    "red_rocks",
    "aurora_flow",
    "bass_bloom",
    "liquid_gradient",
    "center_wave",
    "vertical_scan",
    "ember_rise",
    "tide_pull",
    "comet_fall",
    "dusk_bloom",
    "magma_column",
    "twin_helix",
    "ribbon_drift",
}
COMPOSITION_MODES = {
    "unison",
    "independent",
    "pairs",
    "center_vs_outer",
    "left_vs_right",
    "alternating",
    "random_groups",
}

_NAMED = {
    "red": (210, 24, 18),
    "orange": (210, 92, 16),
    "amber": (210, 140, 28),
    "gold": (210, 168, 40),
    "yellow": (210, 190, 48),
    "lime": (120, 210, 40),
    "green": (28, 170, 64),
    "teal": (8, 168, 154),
    "cyan": (12, 180, 196),
    "aqua": (20, 190, 180),
    "blue": (24, 78, 196),
    "indigo": (48, 36, 168),
    "violet": (112, 36, 186),
    "purple": (132, 28, 168),
    "magenta": (196, 24, 140),
    "pink": (210, 72, 128),
    "wine": (128, 16, 40),
    "ember": (196, 48, 12),
    "coral": (210, 84, 64),
    "peach": (210, 140, 96),
    "sand": (196, 156, 104),
    "forest": (16, 92, 48),
    "ocean": (8, 72, 148),
    "ice": (140, 190, 210),
    "moon": (96, 120, 168),
    "noir": (28, 12, 36),
    "white": (210, 210, 210),
    "warm": (210, 148, 72),
    "cool": (48, 140, 210),
}

_HUE_HINTS = (
    (("fire", "ember", "flame", "lava", "magma", "sunset", "warm", "red", "rock", "amber", "fireplace"), 18.0),
    (("ocean", "tide", "water", "blue", "cool", "aqua", "ice", "rain"), 198.0),
    (("aurora", "forest", "green", "mint", "moss"), 148.0),
    (("neon", "party", "cyber", "magenta", "pink", "storm"), 308.0),
    (("dusk", "purple", "violet", "noir", "metal", "wine"), 278.0),
    (("sunrise", "gold", "honey", "peach"), 36.0),
    (("nocturne", "night", "moon", "indigo"), 228.0),
)

_SHADER_HINTS = (
    ("ember_rise", ("fire", "ember", "flame", "fireplace", "rise", "uplift")),
    ("tide_pull", ("ocean", "tide", "water", "rain", "fall", "waterfall")),
    ("comet_fall", ("comet", "meteor", "star", "shooting")),
    ("dusk_bloom", ("dusk", "bloom", "night", "nocturne")),
    ("magma_column", ("magma", "lava", "heat", "column")),
    ("twin_helix", ("helix", "twist", "spiral", "twin")),
    ("ribbon_drift", ("ribbon", "drift", "flow", "silk")),
    ("aurora_flow", ("aurora", "cool", "borealis")),
    ("red_rocks", ("rock", "canyon", "desert")),
    ("bass_bloom", ("bass", "beat", "pulse")),
    ("center_wave", ("center", "wave")),
    ("vertical_scan", ("scan", "search")),
)


def hsl_to_rgb(hue: float, saturation: float, lightness: float) -> tuple[int, int, int]:
    red, green, blue = colorsys.hls_to_rgb(hue % 360 / 360.0, lightness, saturation)
    return clamp_rgb((int(red * 255), int(green * 255), int(blue * 255)))


def clamp_rgb(rgb: Sequence[float], cap: int = RGB_CAP) -> tuple[int, int, int]:
    return tuple(max(0, min(cap, int(round(channel)))) for channel in list(rgb)[:3])  # type: ignore[return-value]


def parse_color(value: Any) -> tuple[int, int, int]:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _NAMED:
            return clamp_rgb(_NAMED[text])
        hex_text = text[1:] if text.startswith("#") else text
        if re.fullmatch(r"[0-9a-f]{6}", hex_text):
            return clamp_rgb((int(hex_text[0:2], 16), int(hex_text[2:4], 16), int(hex_text[4:6], 16)))
        match = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", text)
        if match:
            return clamp_rgb(tuple(int(part) for part in match.groups()))
        raise ValueError(f"unrecognized color {value!r}")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) < 3:
            raise ValueError(f"color needs at least 3 channels: {value!r}")
        return clamp_rgb(value)
    raise ValueError(f"unrecognized color {value!r}")


def coerce_colors(value: Any) -> list[tuple[int, int, int]] | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,;|]+", value) if part.strip()]
        return normalize_palette(parts) if parts else None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return normalize_palette(value)
    return None


def normalize_palette(colors: Iterable[Any] | None, min_stops: int = 2, max_stops: int = 5) -> list[tuple[int, int, int]]:
    stops: list[tuple[int, int, int]] = []
    for item in colors or ():
        try:
            parsed = parse_color(item)
        except (TypeError, ValueError):
            continue
        if parsed not in stops:
            stops.append(parsed)
        if len(stops) >= max_stops:
            break
    if not stops:
        stops = [clamp_rgb((28, 64, 140)), clamp_rgb((160, 56, 196))]
    while len(stops) < min_stops:
        stops.append(mix(stops[-1], (12, 12, 18), 0.35))
    return stops[:max_stops]


def mix(left: Sequence[float], right: Sequence[float], amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, float(amount)))
    return clamp_rgb(tuple(left[i] + (right[i] - left[i]) * amount for i in range(3)))


def sample_palette(palette: Sequence[Sequence[int]], amount: float) -> tuple[int, int, int]:
    stops = normalize_palette(palette)
    if len(stops) == 1:
        return stops[0]
    amount = max(0.0, min(1.0, float(amount)))
    scaled = amount * (len(stops) - 1)
    index = min(len(stops) - 2, int(scaled))
    return mix(stops[index], stops[index + 1], scaled - index)


def _hue_for(text: str, rng: random.Random) -> float:
    hue = 32.0
    for words, value in _HUE_HINTS:
        if any(word in text for word in words):
            hue = value
            break
    return (hue + rng.uniform(-18.0, 18.0)) % 360.0


def palette_from_prompt(mood: str = "", energy: str = "", seed: int | str | None = None) -> list[tuple[int, int, int]]:
    rng = random.Random(seed)
    text = f"{mood} {energy}".strip().lower()
    hue = _hue_for(text, rng)
    saturation = 0.62 + rng.uniform(-0.08, 0.14)
    if any(word in text for word in ("calm", "soft", "sleep", "quiet", "dream")):
        saturation *= 0.78
    if any(word in text for word in ("party", "neon", "bright", "storm")):
        saturation = min(0.94, saturation + 0.16)
    count = 3 + rng.randrange(0, 2)
    palette: list[tuple[int, int, int]] = []
    for index in range(count):
        direction = 1.0 if rng.random() > 0.25 else -1.0
        stop_hue = (hue + index * (16.0 + rng.uniform(8.0, 26.0)) * direction) % 360.0
        stop_sat = min(0.95, max(0.28, saturation + rng.uniform(-0.12, 0.12)))
        lightness = min(0.58, max(0.09, 0.16 + index * 0.16 + rng.uniform(-0.04, 0.05)))
        palette.append(hsl_to_rgb(stop_hue, stop_sat, lightness))
    return normalize_palette(palette)


def to_rgbw(palette: Sequence[Sequence[int]]) -> list[list[int]]:
    return [[int(red), int(green), int(blue), 0] for red, green, blue in normalize_palette(palette)]


def choose_shader(mood: str = "", motion: str = "", energy: str = "", shader: str | None = None) -> str:
    requested = (shader or "").strip().lower().replace("-", "_")
    if requested in SHADERS:
        return requested
    if requested and requested not in {"", "auto"}:
        return "liquid_gradient"
    text = f"{mood} {motion} {energy}".lower()
    for name, words in _SHADER_HINTS:
        if any(re.search(rf"\b{re.escape(word)}\b", text) for word in words):
            return name
    return "liquid_gradient"


def choose_composition(mood: str = "", motion: str = "", energy: str = "", composition_mode: str | None = None) -> str:
    requested = (composition_mode or "").strip().lower().replace("-", "_")
    if requested in COMPOSITION_MODES:
        return requested
    text = f"{mood} {motion} {energy}".lower()
    if any(word in text for word in ("versus", "duel", "split")):
        return "left_vs_right"
    if any(word in text for word in ("pair", "couple")):
        return "pairs"
    if any(word in text for word in ("outer", "center")):
        return "center_vs_outer"
    if any(word in text for word in ("alternate", "stripe")):
        return "alternating"
    if any(word in text for word in ("independent", "each", "unique")):
        return "independent"
    return "unison"


def choose_intensity(energy: str = "", intensity: float | None = None) -> float:
    if intensity is not None:
        return min(0.82, max(0.05, float(intensity)))
    text = energy.lower()
    if any(word in text for word in ("party", "bright", "high")):
        return 0.74
    if any(word in text for word in ("calm", "soft", "low", "sleep")):
        return 0.38
    return 0.58


def build_look(
    mood: str = "",
    energy: str = "",
    motion: str = "",
    colors: Iterable[Any] | None = None,
    shader: str | None = None,
    composition_mode: str | None = None,
    intensity: float | None = None,
    seed: int | str | None = None,
) -> dict[str, Any]:
    explicit = coerce_colors(colors)
    palette = explicit if explicit else palette_from_prompt(mood, energy, seed)
    return {
        "mood": mood,
        "energy": energy,
        "motion": motion,
        "shader": choose_shader(mood, motion, energy, shader),
        "colors": palette,
        "composition_mode": choose_composition(mood, motion, energy, composition_mode),
        "intensity": choose_intensity(energy, intensity),
        "seed": seed,
    }
