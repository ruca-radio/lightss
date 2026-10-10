#!/usr/bin/env python3
"""Bounded color intelligence for unique Lightss looks.

Provides:
- Named + explicit RGB/hex color parsing
- Prompt-derived palettes
- Color harmonies appropriate to mood/energy
- Shader selection
- Multi-column composition selection
- Bounded brightness/intensity
- Deterministic generation with optional seeds

This module intentionally generates visual intent rather than WLED effect IDs.
"""

from __future__ import annotations

import colorsys
import random
import re
from typing import Any, Iterable, Sequence


# ---------------------------------------------------------------------------
# Global bounds
# ---------------------------------------------------------------------------

RGB_CAP = 210

MIN_INTENSITY = 0.05
MAX_INTENSITY = 0.90

MIN_PALETTE_STOPS = 2
MAX_PALETTE_STOPS = 5


# ---------------------------------------------------------------------------
# Shaders / compositions
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Named colors
# ---------------------------------------------------------------------------

_NAMED = {
    # Reds / heat
    "red": (210, 24, 18),
    "scarlet": (210, 30, 20),
    "crimson": (184, 18, 44),
    "ruby": (170, 18, 48),
    "wine": (128, 16, 40),
    "burgundy": (104, 16, 36),
    "ember": (196, 48, 12),
    "flame": (210, 64, 12),

    # Orange / earth
    "orange": (210, 92, 16),
    "coral": (210, 84, 64),
    "peach": (210, 140, 96),
    "copper": (184, 92, 46),
    "rust": (166, 62, 28),
    "terracotta": (178, 82, 54),

    # Gold / yellow
    "amber": (210, 140, 28),
    "gold": (210, 168, 40),
    "yellow": (210, 190, 48),
    "honey": (210, 156, 52),
    "sand": (196, 156, 104),

    # Green
    "lime": (120, 210, 40),
    "green": (28, 170, 64),
    "forest": (16, 92, 48),
    "moss": (56, 104, 44),
    "mint": (84, 196, 136),
    "jade": (22, 156, 104),
    "emerald": (20, 146, 78),

    # Cyan / teal
    "teal": (8, 168, 154),
    "cyan": (12, 180, 196),
    "aqua": (20, 190, 180),
    "turquoise": (26, 176, 172),
    "ice": (140, 190, 210),

    # Blue
    "blue": (24, 78, 196),
    "azure": (32, 116, 210),
    "cobalt": (26, 62, 176),
    "navy": (18, 34, 92),
    "ocean": (8, 72, 148),

    # Purple
    "indigo": (48, 36, 168),
    "violet": (112, 36, 186),
    "purple": (132, 28, 168),
    "lavender": (148, 104, 196),
    "plum": (118, 38, 118),

    # Pink
    "magenta": (196, 24, 140),
    "pink": (210, 72, 128),
    "rose": (198, 56, 102),
    "fuchsia": (210, 26, 164),

    # Neutral / atmospheric
    "moon": (96, 120, 168),
    "silver": (156, 166, 178),
    "smoke": (76, 82, 94),
    "slate": (58, 72, 94),
    "charcoal": (32, 34, 42),
    "noir": (28, 12, 36),
    "black": (0, 0, 0),
    "white": (210, 210, 210),

    # Semantic aliases
    "warm": (210, 148, 72),
    "cool": (48, 140, 210),
}


# ---------------------------------------------------------------------------
# Prompt intelligence
# ---------------------------------------------------------------------------

_HUE_HINTS = (
    (
        (
            "fire",
            "ember",
            "flame",
            "lava",
            "magma",
            "sunset",
            "warm",
            "red",
            "rock",
            "amber",
            "fireplace",
            "volcano",
            "copper",
        ),
        18.0,
    ),
    (
        (
            "ocean",
            "tide",
            "water",
            "blue",
            "cool",
            "aqua",
            "ice",
            "rain",
            "waterfall",
            "pacifica",
        ),
        198.0,
    ),
    (
        (
            "aurora",
            "forest",
            "green",
            "mint",
            "moss",
            "jungle",
            "nature",
        ),
        148.0,
    ),
    (
        (
            "neon",
            "party",
            "cyber",
            "cyberpunk",
            "magenta",
            "pink",
            "club",
            "vaporwave",
        ),
        308.0,
    ),
    (
        (
            "dusk",
            "purple",
            "violet",
            "noir",
            "metal",
            "wine",
            "plum",
        ),
        278.0,
    ),
    (
        (
            "sunrise",
            "gold",
            "honey",
            "peach",
            "morning",
        ),
        36.0,
    ),
    (
        (
            "nocturne",
            "night",
            "moon",
            "indigo",
            "midnight",
            "space",
            "galaxy",
        ),
        228.0,
    ),
)


_SHADER_HINTS = (
    (
        "ember_rise",
        ("fire", "ember", "flame", "fireplace", "rise", "uplift"),
    ),
    (
        "tide_pull",
        ("ocean", "tide", "water", "rain", "waterfall"),
    ),
    (
        "comet_fall",
        ("comet", "meteor", "star", "shooting", "galaxy"),
    ),
    (
        "dusk_bloom",
        ("dusk", "bloom", "night", "nocturne", "moon"),
    ),
    (
        "magma_column",
        ("magma", "lava", "heat", "volcano", "column"),
    ),
    (
        "twin_helix",
        ("helix", "twist", "spiral", "twin", "dna"),
    ),
    (
        "ribbon_drift",
        ("ribbon", "drift", "flow", "silk", "wave"),
    ),
    (
        "aurora_flow",
        ("aurora", "borealis", "polar"),
    ),
    (
        "red_rocks",
        ("rock", "canyon", "desert", "mesa"),
    ),
    (
        "bass_bloom",
        ("bass", "beat", "pulse", "music", "club", "audio"),
    ),
    (
        "center_wave",
        ("center", "wave", "ripple"),
    ),
    (
        "vertical_scan",
        ("scan", "scanner", "search", "matrix"),
    ),
)


# ---------------------------------------------------------------------------
# Core color utilities
# ---------------------------------------------------------------------------

def clamp_rgb(
    rgb: Sequence[float],
    cap: int = RGB_CAP,
) -> tuple[int, int, int]:
    """Clamp an RGB triple to the configured safe output ceiling."""
    channels = list(rgb)[:3]

    if len(channels) < 3:
        raise ValueError(f"RGB value needs 3 channels: {rgb!r}")

    return tuple(
        max(0, min(cap, int(round(channel))))
        for channel in channels
    )  # type: ignore[return-value]


def hsl_to_rgb(
    hue: float,
    saturation: float,
    lightness: float,
) -> tuple[int, int, int]:
    """Convert HSL-like values into bounded RGB."""
    saturation = max(0.0, min(1.0, float(saturation)))
    lightness = max(0.0, min(1.0, float(lightness)))

    red, green, blue = colorsys.hls_to_rgb(
        hue % 360 / 360.0,
        lightness,
        saturation,
    )

    return clamp_rgb(
        (
            red * 255,
            green * 255,
            blue * 255,
        )
    )


def parse_color(value: Any) -> tuple[int, int, int]:
    """Parse a color from a name, hex string, rgb(), or sequence."""
    if isinstance(value, str):
        text = value.strip().lower()

        if text in _NAMED:
            return clamp_rgb(_NAMED[text])

        hex_text = text[1:] if text.startswith("#") else text

        if re.fullmatch(r"[0-9a-f]{6}", hex_text):
            return clamp_rgb(
                (
                    int(hex_text[0:2], 16),
                    int(hex_text[2:4], 16),
                    int(hex_text[4:6], 16),
                )
            )

        if re.fullmatch(r"[0-9a-f]{3}", hex_text):
            return clamp_rgb(
                tuple(
                    int(channel * 2, 16)
                    for channel in hex_text
                )
            )

        match = re.fullmatch(
            r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
            text,
        )

        if match:
            return clamp_rgb(
                tuple(
                    int(part)
                    for part in match.groups()
                )
            )

        raise ValueError(f"unrecognized color {value!r}")

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) < 3:
            raise ValueError(
                f"color needs at least 3 channels: {value!r}"
            )

        return clamp_rgb(value)

    raise ValueError(f"unrecognized color {value!r}")


def coerce_colors(
    value: Any,
) -> list[tuple[int, int, int]] | None:
    """Coerce user color input into a normalized palette."""
    if value is None or value == "" or value == []:
        return None

    if isinstance(value, str):
        text = value.strip()

        # A single rgb(...) value contains commas, so parse it before trying
        # to interpret commas as palette delimiters.
        try:
            return normalize_palette([parse_color(text)])
        except ValueError:
            pass

        # Semicolon/pipe are unambiguous palette delimiters.
        if ";" in text or "|" in text:
            parts = [
                part.strip()
                for part in re.split(r"[;|]+", text)
                if part.strip()
            ]
            return normalize_palette(parts) if parts else None

        # Comma-separated named/hex colors:
        #   "red, blue, #ff8800"
        parts = [
            part.strip()
            for part in text.split(",")
            if part.strip()
        ]

        return normalize_palette(parts) if parts else None

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return normalize_palette(value)

    return None


def normalize_palette(
    colors: Iterable[Any] | None,
    min_stops: int = MIN_PALETTE_STOPS,
    max_stops: int = MAX_PALETTE_STOPS,
) -> list[tuple[int, int, int]]:
    """Parse, deduplicate, clamp, and pad a palette."""
    min_stops = max(1, int(min_stops))
    max_stops = max(min_stops, int(max_stops))

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
        stops = [
            clamp_rgb((28, 64, 140)),
            clamp_rgb((160, 56, 196)),
        ]

    while len(stops) < min_stops:
        source = stops[-1]

        # Produce a visually related second stop instead of simply making
        # every fallback darker.
        target = (
            (12, 12, 18)
            if len(stops) % 2
            else (RGB_CAP, RGB_CAP, RGB_CAP)
        )

        stops.append(
            mix(
                source,
                target,
                0.35,
            )
        )

    return stops[:max_stops]


def mix(
    left: Sequence[float],
    right: Sequence[float],
    amount: float,
) -> tuple[int, int, int]:
    """Linear RGB interpolation."""
    amount = max(
        0.0,
        min(1.0, float(amount)),
    )

    return clamp_rgb(
        tuple(
            left[index]
            + (right[index] - left[index]) * amount
            for index in range(3)
        )
    )


def sample_palette(
    palette: Sequence[Sequence[int]],
    amount: float,
) -> tuple[int, int, int]:
    """Sample a color at position 0..1 across a palette."""
    stops = normalize_palette(
        palette,
        min_stops=1,
    )

    if len(stops) == 1:
        return stops[0]

    amount = max(
        0.0,
        min(1.0, float(amount)),
    )

    scaled = amount * (len(stops) - 1)
    index = min(
        len(stops) - 2,
        int(scaled),
    )

    return mix(
        stops[index],
        stops[index + 1],
        scaled - index,
    )


# ---------------------------------------------------------------------------
# Palette generation
# ---------------------------------------------------------------------------

def _hue_for(
    text: str,
    rng: random.Random,
) -> float:
    """Resolve a semantic base hue from prompt text."""
    for words, value in _HUE_HINTS:
        if any(
            re.search(
                rf"\b{re.escape(word)}\b",
                text,
            )
            for word in words
        ):
            hue = value
            break
    else:
        hue = rng.uniform(
            0.0,
            360.0,
        )

    return (
        hue
        + rng.uniform(-14.0, 14.0)
    ) % 360.0


def _palette_harmony(
    text: str,
    rng: random.Random,
) -> str:
    """Choose an appropriate color relationship."""
    if any(
        word in text
        for word in (
            "calm",
            "soft",
            "ambient",
            "dream",
            "sunset",
            "sunrise",
            "ocean",
            "forest",
            "fire",
        )
    ):
        return "analogous"

    if any(
        word in text
        for word in (
            "cyber",
            "neon",
            "vaporwave",
            "contrast",
            "duel",
        )
    ):
        return "complementary"

    if any(
        word in text
        for word in (
            "party",
            "rainbow",
            "festival",
            "colorful",
        )
    ):
        return "triadic"

    return rng.choice(
        (
            "analogous",
            "analogous",
            "complementary",
            "triadic",
        )
    )


def _harmony_offsets(
    harmony: str,
    count: int,
    rng: random.Random,
) -> list[float]:
    """Generate hue offsets for a palette harmony."""
    if harmony == "complementary":
        candidates = [
            0.0,
            rng.uniform(12, 30),
            180.0,
            180.0 + rng.uniform(12, 30),
            -rng.uniform(12, 24),
        ]

    elif harmony == "triadic":
        candidates = [
            0.0,
            120.0,
            240.0,
            rng.uniform(12, 28),
            120.0 + rng.uniform(12, 28),
        ]

    else:
        step = rng.uniform(
            14.0,
            28.0,
        )

        # Keep the semantic anchor as stop 0. The old left-to-right ordering
        # placed the first stop up to two steps away from e.g. an ocean hue.
        candidates = [0.0]
        for distance in range(1, count):
            candidates.append((1 if distance % 2 else -1) * ((distance + 1) // 2) * step)

    return candidates[:count]


def palette_from_prompt(
    mood: str = "",
    energy: str = "",
    seed: int | str | None = None,
) -> list[tuple[int, int, int]]:
    """Generate a bounded palette from semantic intent."""
    rng = random.Random(seed)

    text = (
        f"{mood} {energy}"
        .strip()
        .lower()
    )

    hue = _hue_for(
        text,
        rng,
    )

    saturation = (
        0.64
        + rng.uniform(-0.07, 0.12)
    )

    if any(
        word in text
        for word in (
            "calm",
            "soft",
            "sleep",
            "quiet",
            "dream",
            "ambient",
            "pastel",
        )
    ):
        saturation *= 0.74

    if any(
        word in text
        for word in (
            "party",
            "neon",
            "bright",
            "storm",
            "club",
            "electric",
        )
    ):
        saturation = min(
            0.96,
            saturation + 0.18,
        )

    count = rng.choice(
        (3, 4, 4, 5)
    )

    harmony = _palette_harmony(
        text,
        rng,
    )

    offsets = _harmony_offsets(
        harmony,
        count,
        rng,
    )

    palette: list[tuple[int, int, int]] = []

    for index, offset in enumerate(offsets):
        stop_hue = (
            hue
            + offset
            + rng.uniform(-5.0, 5.0)
        ) % 360.0

        stop_sat = min(
            0.97,
            max(
                0.24,
                saturation
                + rng.uniform(-0.10, 0.10),
            ),
        )

        # Maintain enough luminance separation for motion shaders to show
        # structure without allowing full-white blowout.
        position = (
            index / max(1, count - 1)
        )

        lightness = (
            0.16
            + position * 0.34
            + rng.uniform(-0.035, 0.04)
        )

        if any(
            word in text
            for word in (
                "night",
                "dark",
                "noir",
                "moody",
                "dusk",
            )
        ):
            lightness *= 0.72

        if any(
            word in text
            for word in (
                "bright",
                "party",
                "daylight",
            )
        ):
            lightness += 0.06

        lightness = min(
            0.60,
            max(0.07, lightness),
        )

        palette.append(
            hsl_to_rgb(
                stop_hue,
                stop_sat,
                lightness,
            )
        )

    return normalize_palette(
        palette,
        min_stops=3,
        max_stops=MAX_PALETTE_STOPS,
    )


# ---------------------------------------------------------------------------
# Output conversion
# ---------------------------------------------------------------------------

def to_rgbw(
    palette: Sequence[Sequence[int]],
) -> list[list[int]]:
    """Convert an RGB palette to WLED-style RGBW entries.

    White remains zero because color extraction onto a physical white channel
    should depend on the actual strip type/configuration.
    """
    return [
        [
            int(red),
            int(green),
            int(blue),
            0,
        ]
        for red, green, blue in normalize_palette(palette)
    ]


# ---------------------------------------------------------------------------
# Look selection
# ---------------------------------------------------------------------------

def choose_shader(
    mood: str = "",
    motion: str = "",
    energy: str = "",
    shader: str | None = None,
    seed: int | str | None = None,
) -> str:
    requested = (
        (shader or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )

    if requested in SHADERS:
        return requested

    if requested and requested != "auto":
        return "liquid_gradient"

    text = (
        f"{mood} {motion} {energy}"
        .lower()
    )

    for name, words in _SHADER_HINTS:
        if any(
            re.search(
                rf"\b{re.escape(word)}\b",
                text,
            )
            for word in words
        ):
            return name

    rng = random.Random(
        f"shader:{seed}"
        if seed is not None
        else None
    )

    return rng.choice(
        sorted(SHADERS)
    )


def choose_composition(
    mood: str = "",
    motion: str = "",
    energy: str = "",
    composition_mode: str | None = None,
    seed: int | str | None = None,
) -> str:
    requested = (
        (composition_mode or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )

    if requested in COMPOSITION_MODES:
        return requested

    text = (
        f"{mood} {motion} {energy}"
        .lower()
    )

    if any(
        word in text
        for word in (
            "versus",
            "duel",
            "split",
            "contrast",
        )
    ):
        return "left_vs_right"

    if any(
        word in text
        for word in (
            "pair",
            "pairs",
            "couple",
            "paired",
        )
    ):
        return "pairs"

    if any(
        word in text
        for word in (
            "outer",
            "center",
            "centre",
        )
    ):
        return "center_vs_outer"

    if any(
        word in text
        for word in (
            "alternate",
            "alternating",
            "stripe",
            "striped",
        )
    ):
        return "alternating"

    if any(
        word in text
        for word in (
            "independent",
            "each",
            "unique",
            "different",
        )
    ):
        return "independent"

    if any(
        word in text
        for word in (
            "together",
            "uniform",
            "same",
            "unison",
            "single",
        )
    ):
        return "unison"

    rng = random.Random(
        f"composition:{seed}"
        if seed is not None
        else None
    )

    # Weighted toward visually structured arrangements instead of fully
    # arbitrary behavior.
    return rng.choice(
        (
            "unison",
            "unison",
            "pairs",
            "center_vs_outer",
            "left_vs_right",
            "alternating",
            "independent",
            "random_groups",
        )
    )


def choose_intensity(
    energy: str = "",
    intensity: float | None = None,
) -> float:
    """Convert semantic energy into normalized visual intensity."""
    if intensity is not None:
        return min(
            MAX_INTENSITY,
            max(
                MIN_INTENSITY,
                float(intensity),
            ),
        )

    text = energy.lower()

    if any(
        word in text
        for word in (
            "max",
            "intense",
            "wild",
            "club",
            "rave",
        )
    ):
        return 0.82

    if any(
        word in text
        for word in (
            "party",
            "bright",
            "high",
            "energetic",
        )
    ):
        return 0.74

    if any(
        word in text
        for word in (
            "medium",
            "normal",
            "moderate",
        )
    ):
        return 0.58

    if any(
        word in text
        for word in (
            "calm",
            "soft",
            "low",
            "sleep",
            "quiet",
            "ambient",
        )
    ):
        return 0.38

    if any(
        word in text
        for word in (
            "dim",
            "nightlight",
            "very low",
        )
    ):
        return 0.22

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
    """Build a complete semantic lighting look."""
    explicit = coerce_colors(colors)

    palette = (
        explicit
        if explicit
        else palette_from_prompt(
            mood,
            energy,
            seed,
        )
    )

    return {
        "mood": mood,
        "energy": energy,
        "motion": motion,
        "shader": choose_shader(
            mood,
            motion,
            energy,
            shader,
            seed=seed,
        ),
        "colors": palette,
        "composition_mode": choose_composition(
            mood,
            motion,
            energy,
            composition_mode,
            seed=seed,
        ),
        "intensity": choose_intensity(
            energy,
            intensity,
        ),
        "seed": seed,
    }
