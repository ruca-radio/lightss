#!/usr/bin/env python3
"""Effect catalog intelligence + curated atmospheres for the WLED fleet.

Two jobs:

1. Classify the live WLED effect catalog into mood groups with metadata flags,
   so the AI/director and UIs can reason about effects by name/capability
   instead of guessing numeric IDs.
2. ATMOSPHERES — named, curated, multi-part looks composed via columns.py
   wall modes. Each is one call: apply_atmosphere(fleet, "fireplace").

Designed for WLED 16.x and compatible with live /json/eff + /json/fxdata data.

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

# Only effects that are explicitly rapid-flash / strobe style are forbidden.
#
# IMPORTANT:
# - Blink is intentionally allowed.
# - Blink Rainbow is intentionally allowed.
# - Sparkle / PS Sparkler are intentionally allowed.
# - Fireworks / PS Fireworks are intentionally allowed.
#
# The director can therefore use these effects intentionally instead of
# discarding whole families based on broad substring matches.
UNSAFE_FX_SUBSTRINGS = (
    "strobe",
    "chase flash",
    "lightning",
)

# Exact names can catch effects we specifically don't want while avoiding
# overly broad substring matching.
UNSAFE_FX_NAMES = {
    "Strobe",
    "Strobe Rainbow",
    "Strobe Mega",
    "Chase Flash",
    "Chase Flash Rnd",
    "Lightning",
}


# Mood groups: label -> name substrings (matched case-insensitively).
#
# First matching group becomes the primary catalog grouping, but an effect can
# carry multiple vibes internally.
VIBE_GROUPS: dict[str, tuple[str, ...]] = {
    "Fire & heat": (
        "fire",
        "candle",
        "volcano",
        "lava",
        "magma",
        "sun radiation",
        "sunrise",
    ),

    "Water & rain": (
        "rain",
        "lake",
        "ripple",
        "drip",
        "puddle",
        "waterfall",
        "pacifica",
        "octopus",
        "soap",
    ),

    "Calm & ambient": (
        "breathe",
        "fade",
        "gradient",
        "colorloop",
        "blend",
        "drift",
        "slow transition",
        "shimmer",
        "color clouds",
        "cloud",
        "plasma",
        "colorwaves",
        "aurora",
    ),

    "Sky & cosmic": (
        "aurora",
        "polar",
        "galaxy",
        "black hole",
        "meteor",
        "comet",
        "spaceships",
        "ghost rider",
        "starburst",
        "pride",
    ),

    "Blink & pulse": (
        "blink",
        "pulser",
        "bpm",
        "heartbeat",
    ),

    "Party & motion": (
        "chase",
        "theater",
        "running",
        "scan",
        "scanner",
        "android",
        "traffic",
        "popcorn",
        "bouncing",
        "juggle",
        "sinelon",
        "dots",
        "spots",
        "saw",
        "sweep",
        "wipe",
        "dynamic",
        "random",
        "noise",
        "twinkle",
        "fairy",
        "sparkle",
        "fireworks",
        "dancing shadows",
        "ballpit",
        "pinball",
        "spray",
    ),

    "Audio reactive": (
        "geq",
        "freq",
        "sonic",
        "gravimeter",
        "gravcenter",
        "noisemeter",
        "midnoise",
        "rocktaves",
        "waverly",
        "waterfall",
        "puddlepeak",
        "ripple peak",
        "dj light",
        "vu",
    ),

    "Retro & geek": (
        "pacman",
        "tetrix",
        "game of life",
        "matrix",
        "dna",
        "scrolling text",
        "pixel",
        "tv simulator",
        "pinball",
        "lissajous",
        "tartan",
        "image",
    ),

    "Particle system": (
        "ps ",
    ),

    "Abstract & 2D": (
        "swirl",
        "julia",
        "rotozoomer",
        "plasmoid",
        "blob",
        "metaballs",
        "wavesins",
        "distortion",
        "frizzles",
        "oscillate",
        "waving",
        "sindots",
        "fuzzy noise",
        "attractor",
        "vortex",
        "impact",
        "box",
    ),
}


_FLAG_LABELS = {
    "1": "1D",
    "v": "audio:volume",
    "f": "audio:freq",
    "2": "2D",
}


def _fxdata_flags(entry: str) -> set[str]:
    """Parse flags from a /json/fxdata entry.

    WLED fxdata format:

        <params>;<colors>;<palette>;<flags>;<defaults>

    Useful flags include:
      1 = 1D-capable (also exempts a simultaneous 2 flag from matrix-only)
      2 = 2D-capable; requires a matrix only when 1 is absent
      v = audio volume reactive
      f = audio frequency reactive
    """
    parts = str(entry).split(";")
    flags: set[str] = set()

    if len(parts) > 3:
        for char in parts[3].strip():
            label = _FLAG_LABELS.get(char)
            if label:
                flags.add(label)

    return flags


def classify_effects(
    effects: list[str],
    fxdata: list[str] | None = None,
) -> dict[int, dict]:
    """Classify a live WLED effect list.

    Returns:

        {
            effect_id: {
                "name": ...,
                "unsafe": bool,
                "audio": bool,
                "2d": bool,
                "particle": bool,
                "blink": bool,
                "vibes": [...],
            }
        }

    RSVD/placeholder entries remain unavailable because WLED uses those for
    effects unsupported by a particular firmware/device build.
    """
    fxdata = fxdata or []
    classified: dict[int, dict] = {}

    for effect_id, name in enumerate(effects):
        name = str(name)
        lowered = name.lower()

        flags = (
            _fxdata_flags(fxdata[effect_id])
            if effect_id < len(fxdata)
            else set()
        )

        vibes = [
            group
            for group, keywords in VIBE_GROUPS.items()
            if any(keyword in lowered for keyword in keywords)
        ]

        placeholder = lowered in ("rsvd", "-", "")

        explicitly_unsafe = (
            name in UNSAFE_FX_NAMES
            or any(token in lowered for token in UNSAFE_FX_SUBSTRINGS)
        )

        classified[effect_id] = {
            "name": name,
            "unsafe": placeholder or explicitly_unsafe,
            "audio": (
                "audio:volume" in flags
                or "audio:freq" in flags
            ),
            "2d": "2D" in flags and "1D" not in flags,
            "particle": lowered.startswith("ps "),
            "blink": "blink" in lowered,
            "vibes": vibes,
        }

    return classified


def catalog_text(
    effects: list[str],
    fxdata: list[str] | None = None,
) -> str:
    """Compact, prompt-ready effect catalog grouped by mood.

    Markers:
      ♪    audio reactive
      [2D] matrix / 2D effect
      [PS] particle-system effect
      ◉    blink/pulse effect
      🚫    blocked
    """
    classified = classify_effects(effects, fxdata)

    def marker(info: dict) -> str:
        marks: list[str] = []

        if info["audio"]:
            marks.append("♪")

        if info["2d"]:
            marks.append("[2D]")

        if info["particle"]:
            marks.append("[PS]")

        if info["blink"]:
            marks.append("◉")

        if info["unsafe"]:
            marks.append("🚫")

        return "".join(marks)

    grouped: dict[str, list[str]] = {}
    other: list[str] = []

    for effect_id, info in classified.items():
        entry = f"{effect_id}={info['name']}{marker(info)}"

        if info["vibes"]:
            grouped.setdefault(info["vibes"][0], []).append(entry)
        else:
            other.append(entry)

    lines = [
        (
            "Effect catalog "
            "(id=name; ♪ audio, [2D] matrix, [PS] particles, "
            "◉ blink/pulse, 🚫 blocked):"
        )
    ]

    for group, entries in grouped.items():
        lines.append(f"{group}: {', '.join(entries)}")

    if other:
        lines.append(f"Misc: {', '.join(other)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Curated atmospheres
# ---------------------------------------------------------------------------

# WLED 16.x effect IDs used below:
#
#   1   Blink
#   2   Breathe
#   26  Blink Rainbow
#   38  Aurora
#   43  Rain
#   66  Fire 2012
#   88  Candle
#   101 Pacifica
#   104 Sunrise
#   110 Flow
#   133 Gravcenter / build dependent — existing installation verified
#   136 VU Meter
#   139 GEQ
#   153 Matrix
#   159 DJ Light
#   161 Shimmer
#   175 Swirl
#
# WLED 16 additions:
#   187 PS Volcano
#   188 PS Fire
#   189 PS Fireworks
#   190 PS Vortex
#   191 PS Fuzzy Noise
#   192 PS Ballpit
#   193 PS Box
#   194 PS Attractor
#   195 PS Impact
#   196 PS Waterfall
#   197 PS Spray
#   198 PS GEQ 2D
#   199 PS GEQ Nova
#   200 PS Ghost Rider
#   201 PS Blobs
#   202 PS DripDrop
#   203 PS Pinball
#   204 PS Dancing Shadows
#   205 PS Fireworks 1D
#   206 PS Sparkler
#   207 PS Hourglass
#   208 PS Spray 1D
#   209 PS 1D Balance
#   210 PS Chase
#   211 PS Starburst
#   212 PS GEQ 1D
#   213 PS Fire 1D
#   214 PS Sonic Stream
#   215 PS Sonic Boom
#   216 PS Springy
#   217 PS Galaxy
#   218 Color Clouds
#   219 Slow Transition
#
# IDs should still ultimately come from the live /json/eff catalog whenever
# possible because custom/user_fx builds can differ.


ATMOSPHERES: dict[str, dict] = {
    # ------------------------------------------------------------------
    # Warm / natural
    # ------------------------------------------------------------------

    "fireplace": {
        "description": "Roaring fire across all four columns",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 66,
                    "pal": 35,
                    "sx": 64,
                    "ix": 160,
                    "c2": 128,
                },
            )
        ],
    },

    "particle_fire": {
        "description": "WLED 16 particle-system fire",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 188,
                    "pal": 35,
                    "sx": 120,
                    "ix": 190,
                },
            )
        ],
    },

    "fireline": {
        "description": "Particle fire optimized for vertical strips",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 213,
                    "pal": 35,
                    "sx": 110,
                    "ix": 180,
                },
            )
        ],
    },

    "candlelit": {
        "description": "Warm flickering candlelight",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 88,
                    "sx": 96,
                    "ix": 224,
                    "col": [
                        [255, 140, 40],
                        [0, 0, 0],
                        [0, 0, 0],
                    ],
                },
            )
        ],
    },

    "volcano": {
        "description": "Particle volcano eruption",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 187,
                    "pal": 35,
                    "sx": 110,
                    "ix": 170,
                },
            )
        ],
    },

    "sunrise": {
        "description": "Slow warm simulated sunrise",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 104,
                    "pal": 13,
                    "sx": 20,
                    "ix": 160,
                },
            )
        ],
    },

    # ------------------------------------------------------------------
    # Water / atmospheric
    # ------------------------------------------------------------------

    "ocean": {
        "description": "Deep calm ocean waves",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 101,
                    "pal": 51,
                },
            )
        ],
    },

    "rainstorm": {
        "description": "Rain on the left, dripping particles on the right",
        "steps": [
            (
                "left_vs_right",
                {
                    "fx_left": 43,
                    "fx_right": 202,
                    "pal_left": 9,
                    "pal_right": 9,
                },
            )
        ],
    },

    "waterfall": {
        "description": "Particle waterfall across the LED wall",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 196,
                    "pal": 9,
                    "sx": 110,
                    "ix": 170,
                },
            )
        ],
    },

    "aurora": {
        "description": "Slow polar lights",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 38,
                    "pal": 50,
                    "sx": 24,
                },
            )
        ],
    },

    "clouds": {
        "description": "Soft evolving WLED 16 color clouds",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 218,
                    "pal": 50,
                    "sx": 32,
                    "ix": 48,
                },
            )
        ],
    },

    "shimmer": {
        "description": "Slow shimmering highlights drifting across the wall",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 161,
                    "pal": 13,
                    "sx": 72,
                    "ix": 96,
                },
            )
        ],
    },

    # ------------------------------------------------------------------
    # Blink / pulse
    # ------------------------------------------------------------------

    "blink": {
        "description": "Classic two-color blink across the wall",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 1,
                    "sx": 48,
                    "ix": 160,
                    "col": [
                        [255, 160, 40],
                        [16, 0, 0],
                        [0, 0, 0],
                    ],
                },
            )
        ],
    },

    "blink_rainbow": {
        "description": "Rainbow-cycling blink",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 26,
                    "sx": 40,
                    "ix": 160,
                },
            )
        ],
    },

    "alternating_blink": {
        "description": "Left/right contrasting blink pattern",
        "steps": [
            (
                "left_vs_right",
                {
                    "fx_left": 1,
                    "fx_right": 26,
                },
            )
        ],
    },

    "soft_pulse": {
        "description": "Smooth breathing pulse rather than hard blinking",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 2,
                    "sx": 48,
                    "ix": 160,
                },
            )
        ],
    },

    # ------------------------------------------------------------------
    # Party / particles
    # ------------------------------------------------------------------

    "fireworks": {
        "description": "Particle fireworks across the wall",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 189,
                    "pal": 11,
                    "sx": 100,
                    "ix": 150,
                },
            )
        ],
    },

    "fireworks_1d": {
        "description": "Particle fireworks optimized for vertical strips",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 205,
                    "pal": 11,
                    "sx": 110,
                    "ix": 160,
                },
            )
        ],
    },

    "sparkler": {
        "description": "Moving particle sparkler",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 206,
                    "pal": 11,
                    "sx": 80,
                    "ix": 170,
                },
            )
        ],
    },

    "starburst": {
        "description": "Exploding particle starbursts",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 211,
                    "pal": 11,
                    "sx": 90,
                    "ix": 170,
                },
            )
        ],
    },

    "pinball": {
        "description": "Bouncing particle pinballs",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 203,
                    "pal": 11,
                    "sx": 120,
                    "ix": 160,
                },
            )
        ],
    },

    "dancing_shadows": {
        "description": "Ghostlike particle shadows racing across the wall",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 204,
                    "pal": 40,
                    "sx": 100,
                    "ix": 150,
                },
            )
        ],
    },

    "springy": {
        "description": "Elastic particle motion connected by virtual springs",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 216,
                    "pal": 11,
                    "sx": 100,
                    "ix": 150,
                },
            )
        ],
    },

    # ------------------------------------------------------------------
    # Neon / cyber / abstract
    # ------------------------------------------------------------------

    "sunset": {
        "description": "Warm sunset gradient flowing along the wall",
        "steps": [
            (
                "chase",
                {
                    "fx": 110,
                    "pal": 13,
                },
            )
        ],
    },

    "forest": {
        "description": "Lush forest canopy waves",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 67,
                    "pal": 10,
                },
            )
        ],
    },

    "lava": {
        "description": "Bubbling lava lamp",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 133,
                    "pal": 8,
                    "m12": 1,
                },
            )
        ],
    },

    "vaporwave": {
        "description": "Neon magenta/cyan retro flow, mirrored",
        "steps": [
            (
                "mirror",
                {
                    "fx": 110,
                    "pal": 40,
                },
            )
        ],
    },

    "cyberpunk": {
        "description": "Swirling neon vs electric flow split down the middle",
        "steps": [
            (
                "left_vs_right",
                {
                    "fx_left": 175,
                    "fx_right": 110,
                    "pal_left": 37,
                    "pal_right": 40,
                },
            )
        ],
    },

    "vortex": {
        "description": "Particle vortex",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 190,
                    "pal": 40,
                    "sx": 100,
                    "ix": 160,
                },
            )
        ],
    },

    "black_hole": {
        "description": "Particles orbiting a central attractor",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 194,
                    "pal": 59,
                    "sx": 110,
                    "ix": 160,
                },
            )
        ],
    },

    "galaxy": {
        "description": "Particle-system rotating galaxy starfield",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 217,
                    "pal": 59,
                    "sx": 80,
                    "c1": 1,
                    "c3": 4,
                },
            )
        ],
    },

    "ghost_rider": {
        "description": "Spiraling particle trails",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 200,
                    "pal": 40,
                    "sx": 120,
                    "ix": 160,
                },
            )
        ],
    },

    "candy": {
        "description": "Sweet candy-shop colors",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 67,
                    "pal": 57,
                },
            )
        ],
    },

    "matrix": {
        "description": "Falling green code rain",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 153,
                    "pal": 10,
                },
            )
        ],
    },

    # ------------------------------------------------------------------
    # Audio reactive
    # ------------------------------------------------------------------

    "club": {
        "description": "Beat-driven VU columns",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 136,
                    "ix": 128,
                    "m12": 2,
                },
            )
        ],
    },

    "equalizer": {
        "description": "Frequency-band equalizer across the wall",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 139,
                    "pal": 11,
                    "c1": 255,
                    "c2": 64,
                },
            )
        ],
    },

    "particle_equalizer": {
        "description": "WLED 16 particle-system 1D equalizer",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 212,
                    "pal": 11,
                    "sx": 110,
                    "ix": 175,
                },
            )
        ],
    },

    "sonic_stream": {
        "description": "Audio-reactive particle stream",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 214,
                    "pal": 40,
                    "sx": 110,
                    "ix": 180,
                },
            )
        ],
    },

    "sonic_boom": {
        "description": "Beat-triggered particle explosions",
        "steps": [
            (
                "wall_span",
                {
                    "fx": 215,
                    "pal": 11,
                    "sx": 110,
                    "ix": 190,
                },
            )
        ],
    },

    "dj": {
        "description": "DJ light vs VU meter split across the wall",
        "steps": [
            (
                "left_vs_right",
                {
                    "fx_left": 159,
                    "fx_right": 136,
                    "m12": 2,
                },
            )
        ],
    },
}


def atmosphere_names() -> list[str]:
    return sorted(ATMOSPHERES)


def atmosphere_menu_text() -> str:
    """Prompt/UI-ready list of available atmospheres."""
    return "\n".join(
        f"- {name}: {ATMOSPHERES[name]['description']}"
        for name in atmosphere_names()
    )


def apply_atmosphere(fleet: LightFleet, name: str) -> dict:
    """Apply a named atmosphere across the fleet via columns wall modes."""
    key = str(name).strip().lower()

    if key not in ATMOSPHERES:
        raise ValueError(
            f"Unknown atmosphere {name!r}. "
            f"Available: {', '.join(atmosphere_names())}."
        )

    results: dict = {}

    for func_name, kwargs in ATMOSPHERES[key]["steps"]:
        results.update(
            getattr(columns, func_name)(
                fleet,
                **kwargs,
            )
        )

    return results
