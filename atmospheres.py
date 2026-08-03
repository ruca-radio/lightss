#!/usr/bin/env python3
"""Effect catalog intelligence + curated atmospheres for the WLED fleet.

Two jobs:

1. Classify the live WLED effect catalog (220 effects on 16.0.1) into mood
   groups with metadata flags, so the AI/director and UIs can reason about
   what a look will feel like instead of guessing numeric ids.
2. ATMOSPHERES — named, curated, multi-part looks composed via columns.py
   wall modes. Each is one call: apply_atmosphere(fleet, "fireplace").

Everything is stdlib-only and duck-typed against fleet.LightFleet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import columns

if TYPE_CHECKING:
    from fleet import LightFleet

# ---------------------------------------------------------------------------
# Effect classification
# ---------------------------------------------------------------------------

# Name substrings that mark an effect as forbidden (strobe/seizure-risk).
UNSAFE_FX_SUBSTRINGS = (
    "strobe", "blink", "flash", "lightning", "fireworks", "sparkl",
)

# Mood groups: label -> name substrings (matched case-insensitively).
VIBE_GROUPS: dict[str, tuple[str, ...]] = {
    "Fire & heat": ("fire", "candle", "volcano", "sun radiation"),
    "Water & rain": ("rain", "lake", "ripple", "drip", "puddle", "waterfall",
                     "pacifica", "octopus", "soap"),
    "Calm & ambient": ("breathe", "fade", "gradient", "colorloop", "blend",
                       "drift", "slow", "shimmer", "clouds", "plasma",
                       "colorwaves", "halloween"),
    "Sky & cosmic": ("aurora", "sunrise", "polar", "galaxy", "black hole",
                     "meteor", "comet", "spaceships", "ghost rider",
                     "starburst", "pride"),
    "Party & motion": ("chase", "theater", "running", "scan", "android",
                       "traffic", "popcorn", "bouncing", "juggle", "sinelon",
                       "dots", "spots", "saw", "sweep", "wipe", "dynamic",
                       "random", "noise", "twinkle", "fairy"),
    "Retro & geek": ("pacman", "tetrix", "game of life", "matrix", "dna",
                     "scrolling text", "pixel", "tv simulator", "pinball",
                     "lissajous", "tartan", "image"),
    "Abstract & 2D": ("swirl", "julia", "rotozoomer", "plasmoid", "blob",
                      "metaballs", "wavesins", "distortion", "frizzles",
                      "oscillate", "waving", "geq", "freq", "sonic",
                      "gravimeter", "gravcenter", "noisemeter", "midnoise",
                      "ps ", "springy", "rocktaves", "akemi", "sindots"),
}

_FLAG_LABELS = {"v": "audio:volume", "f": "audio:freq", "2": "2D"}


def _fxdata_flags(entry: str) -> set[str]:
    """Parse the flags section of a /json/fxdata entry.

    Format: <params>;<colors>;<palette>;<flags>;<defaults>
    Flags: '0' single-LED, '1' 1D-optimized, '2' requires 2D,
    'v' audio volume-reactive, 'f' audio frequency-reactive.
    """
    parts = str(entry).split(";")
    flags = set()
    if len(parts) > 3:
        for char in parts[3].strip():
            if char in _FLAG_LABELS:
                flags.add(_FLAG_LABELS[char])
    return flags


def classify_effects(effects: list[str], fxdata: list[str] | None = None) -> dict[int, dict]:
    """Classify a live effect list.

    Returns {effect_id: {"name", "unsafe", "audio", "2d", "vibes": [...]}}.
    RSVD/placeholder entries are marked unsafe so nothing picks them.
    """
    fxdata = fxdata or []
    classified: dict[int, dict] = {}
    for effect_id, name in enumerate(effects):
        lowered = str(name).lower()
        flags = _fxdata_flags(fxdata[effect_id]) if effect_id < len(fxdata) else set()
        vibes = [
            group
            for group, keywords in VIBE_GROUPS.items()
            if any(keyword in lowered for keyword in keywords)
        ]
        classified[effect_id] = {
            "name": str(name),
            "unsafe": (
                any(token in lowered for token in UNSAFE_FX_SUBSTRINGS)
                or lowered in ("rsvd", "-", "")
            ),
            "audio": "audio:volume" in flags or "audio:freq" in flags,
            "2d": "2D" in flags,
            "vibes": vibes,
        }
    return classified


def catalog_text(effects: list[str], fxdata: list[str] | None = None) -> str:
    """Compact, prompt-ready effect catalog grouped by mood.

    Markers: ♪ = audio-reactive, [2D] = matrix-style, 🚫 = forbidden.
    """
    classified = classify_effects(effects, fxdata)

    def marker(info: dict) -> str:
        marks = ""
        if info["audio"]:
            marks += "♪"
        if info["2d"]:
            marks += "[2D]"
        if info["unsafe"]:
            marks += "🚫"
        return marks

    grouped: dict[str, list[str]] = {}
    other: list[str] = []
    for effect_id, info in classified.items():
        entry = f"{effect_id}={info['name']}{marker(info)}"
        if info["vibes"]:
            grouped.setdefault(info["vibes"][0], []).append(entry)
        else:
            other.append(entry)

    lines = ["Effect catalog (id=name; ♪ audio-reactive, [2D] matrix-style, 🚫 forbidden):"]
    for group, entries in grouped.items():
        lines.append(f"{group}: {', '.join(entries)}")
    if other:
        lines.append(f"Misc: {', '.join(other)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Curated atmospheres
# ---------------------------------------------------------------------------

# Each atmosphere is a list of steps: (columns function name, kwargs).
# Effect/palette ids are verified against WLED 16.0.1 (220 fx, 72 pal).
ATMOSPHERES: dict[str, dict] = {
    "fireplace": {
        "description": "Roaring fire across all four columns",
        "steps": [("wall_span", {"fx": 66, "pal": 35, "sx": 64, "ix": 160, "c2": 128})],
    },
    "candlelit": {
        "description": "Warm flickering candlelight",
        "steps": [("wall_span", {"fx": 88, "sx": 96, "ix": 224,
                                 "col": [[255, 140, 40], [0, 0, 0], [0, 0, 0]]})],
    },
    "ocean": {
        "description": "Deep calm ocean waves",
        "steps": [("wall_span", {"fx": 101, "pal": 51})],
    },
    "rainstorm": {
        "description": "Rain on the left, dripping on the right (no thunder)",
        "steps": [("left_vs_right", {"fx_left": 43, "fx_right": 96,
                                     "pal_left": 9, "pal_right": 9})],
    },
    "aurora": {
        "description": "Slow polar lights",
        "steps": [("wall_span", {"fx": 38, "pal": 50, "sx": 24})],
    },
    "sunset": {
        "description": "Warm sunset gradient flowing along the wall",
        "steps": [("chase", {"fx": 110, "pal": 13})],
    },
    "forest": {
        "description": "Lush forest canopy waves",
        "steps": [("wall_span", {"fx": 67, "pal": 10})],
    },
    "lava": {
        "description": "Bubbling lava lamp",
        "steps": [("wall_span", {"fx": 133, "pal": 8, "m12": 1})],
    },
    "vaporwave": {
        "description": "Neon magenta/cyan retro grid, mirrored",
        "steps": [("mirror", {"fx": 110, "pal": 40})],
    },
    "cyberpunk": {
        "description": "Swirling neon vs electric flow, split down the middle",
        "steps": [("left_vs_right", {"fx_left": 175, "fx_right": 110,
                                     "pal_left": 37, "pal_right": 40})],
    },
    "galaxy": {
        "description": "Starfield with drifting galaxy",
        "steps": [("wall_span", {"fx": 217, "pal": 59, "sx": 80, "c1": 1, "c3": 4})],
    },
    "candy": {
        "description": "Sweet candy-shop colors",
        "steps": [("wall_span", {"fx": 67, "pal": 57})],
    },
    "matrix": {
        "description": "Falling green code rain",
        "steps": [("wall_span", {"fx": 153, "pal": 10})],
    },
    "club": {
        "description": "Beat-driven VU columns (uses the controllers' mics)",
        "steps": [("wall_span", {"fx": 136, "ix": 128, "m12": 2})],
    },
    "equalizer": {
        "description": "Frequency-band equalizer across the wall",
        "steps": [("wall_span", {"fx": 139, "pal": 11, "c1": 255, "c2": 64})],
    },
    "dj": {
        "description": "DJ light vs VU meter, split — full audio-reactive chaos",
        "steps": [("left_vs_right", {"fx_left": 159, "fx_right": 136, "m12": 2})],
    },
}


def atmosphere_names() -> list[str]:
    return sorted(ATMOSPHERES)


def atmosphere_menu_text() -> str:
    """Prompt/UI-ready list of available atmospheres."""
    return "\n".join(
        f"- {name}: {ATMOSPHERES[name]['description']}" for name in atmosphere_names()
    )


def apply_atmosphere(fleet: LightFleet, name: str) -> dict:
    """Apply a named atmosphere across the fleet via columns wall modes."""
    key = str(name).strip().lower()
    if key not in ATMOSPHERES:
        raise ValueError(
            f"Unknown atmosphere {name!r}. Available: {', '.join(atmosphere_names())}."
        )
    results: dict = {}
    for func_name, kwargs in ATMOSPHERES[key]["steps"]:
        results.update(getattr(columns, func_name)(fleet, **kwargs))
    return results
