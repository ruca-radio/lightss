#!/usr/bin/env python3
"""Curated WLED palette intelligence.

WLED palettes are indexed color sources referenced by the ``pal`` field: ids
0-5 are DYNAMIC color-slot modes, ids 6+ are fixed gradients, and v16+ devices
add 800+ cpt-city palettes plus custom palettes numbered downward from 200.
``GET /json/pal`` returns the live ordered name list where ARRAY INDEX == id —
always resolve names against that list; never hardcode counts.

This is a different concept from color_lab's hex ramps (paint inputs for the
DDP realtime renderer): palette_lab reasons about WLED palette ids.
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable, Sequence

# Dynamic palette ids 0-5 (stable across WLED builds): color-slot modes, not
# gradients. Exposed as data so the AI layer can quote exact semantics.
DYNAMIC_PALETTES = {
    0: ("Default", "Auto per effect, usually derived from the primary color slot."),
    1: ("Random Cycle", "Random palette that changes every few seconds."),
    2: ("Color 1", "Primary color slot only."),
    3: ("Colors 1&2", "Primary and secondary color slots."),
    4: ("Color Gradient", "Smooth blend of all three segment color slots."),
    5: ("Colors Only", "Steps through the three segment color slots."),
}

# Curated built-in gradients: name -> (typical id, mood/character note).
# Ids follow the stock WLED palette order and are TYPICAL ONLY — verify against
# the live /json/pal list, whose array index is the real id on any given build.
KNOWN_PALETTES = {
    "Party": (6, "Multi-hue festive gradient, busy and bright."),
    "Cloud": (7, "Desaturated blues and whites, calm sky haze."),
    "Lava": (8, "Deep reds and oranges, molten warmth."),
    "Ocean": (9, "Blues and sea-greens, cool and watery."),
    "Forest": (10, "Greens with warm accents, deep woodland."),
    "Rainbow": (11, "Full saturated hue wheel."),
    "Rainbow Colors": (12, "Rainbow hues in discrete color steps."),
    "Sunset": (13, "Warm red-orange-purple dusk fade."),
    "Rivendell": (14, "Muted greens and teals, soft elven calm."),
    "Breeze": (15, "Teal-to-blue coastal freshness."),
    "Red & Blue": (16, "Bold red-to-blue contrast."),
    "Yellowout": (17, "Soft yellows fading toward white."),
    "Analogous": (18, "Neighboring warm hues, smooth and harmonious."),
    "Splash": (19, "Bright aqua and pink pop."),
    "Pastel": (20, "Soft desaturated candy tones."),
    "Sunset 2": (21, "Alternate warm dusk ramp."),
    "Vintage": (23, "Faded sepia-warm retro tones."),
    "Landscape": (25, "Greens to sky blues, horizon-like."),
    "Beach": (26, "Sand, aqua, and sun tones."),
    "Sherbet": (27, "Sweet orange-pink sorbet hues."),
    "Hult": (28, "Smooth violet-magenta gradient."),
    "Drywet": (30, "Blue-green split, land meets water."),
    "Fire": (35, "Roaring flame reds and yellows."),
    "Icefire": (36, "Cold blue-white flame inversion."),
    "Cyane": (37, "Deep cyan-blue, cool and electric."),
    "Autumn": (39, "Reds, oranges, and browns; fall foliage."),
    "Magenta": (40, "Saturated magenta-purple ramp."),
    "Orange & Teal": (44, "Cinematic warm/cool contrast."),
    "Tiamat": (45, "Fiery reds with cool edges, intense."),
    "April Night": (46, "Dark blues with warm sparks, moody night."),
    "Orangery": (47, "Bright citrus oranges."),
    "C9": (48, "Classic multicolor Christmas lights."),
    "Sakura": (49, "Soft pinks and whites, cherry blossom."),
    "Aurora": (50, "Green-teal-violet northern lights."),
    "Atlantica": (51, "Deep ocean blues and greens."),
    "Temperature": (54, "Candle-warm to cool-white color-temperature ramp."),
}

# Mood keywords -> preferred curated palette names, in priority order. Only
# palettes that resolve against the live list are suggested.
MOOD_PALETTES = (
    (("cozy", "warm", "hygge", "ember", "fireplace"), ("Fire", "Sunset", "Lava")),
    (("fire", "flame", "burn", "inferno"), ("Fire", "Lava", "Tiamat")),
    (("calm", "relax", "sleep", "soft", "gentle", "dreamy"), ("Cloud", "Pastel", "Rivendell")),
    (("ocean", "sea", "water", "beach", "coastal", "tide"), ("Ocean", "Atlantica", "Breeze")),
    (("forest", "nature", "woodland", "moss"), ("Forest", "Landscape", "Rivendell")),
    (("party", "dance", "celebration", "festive", "disco"), ("Party", "Rainbow", "Splash")),
    (("rainbow", "pride", "colorful", "colourful"), ("Rainbow", "Rainbow Colors")),
    (("sunset", "dusk", "evening", "golden hour"), ("Sunset", "Sunset 2", "Orangery")),
    (("aurora", "northern lights", "cosmic", "space"), ("Aurora", "Cyane")),
    (("ice", "cold", "winter", "frozen", "frost"), ("Icefire", "Cyane", "Breeze")),
    (("romantic", "love", "pink", "blossom"), ("Sakura", "Magenta", "Pastel")),
    (("christmas", "holiday", "xmas"), ("C9", "Sherbet")),
    (("autumn", "fall", "harvest"), ("Autumn", "Sunset", "Vintage")),
    (("retro", "vintage", "sepia"), ("Vintage", "Sunset 2")),
    (("candy", "sweet", "kawaii"), ("Sherbet", "Pastel")),
    (("cinematic", "movie", "noir"), ("Orange & Teal", "April Night")),
    (("night", "dark", "moody", "midnight"), ("April Night", "Cyane")),
)


def _strip_marker(name: str) -> str:
    """Drop WLED's palette-name prefix markers (~ / *) and surrounding space."""
    return name.lstrip("*~").strip()


def _normalize(name: str) -> str:
    # Treat "&" as "and" so "Red & Blue" == "red and blue" after cleanup.
    return re.sub(r"[^a-z0-9]+", "", _strip_marker(name).lower().replace("&", "and"))


def resolve_palette_id(name: str, palette_names: Sequence[str] | None) -> int | None:
    """Resolve a palette name to its id (array index) in the live /json/pal list.

    Order: exact, case-insensitive, normalized (markers/punctuation stripped),
    then normalized containment (shortest candidate wins). None when unresolved.
    """
    if not name or not palette_names:
        return None
    names = [str(item) for item in palette_names]
    for index, candidate in enumerate(names):
        if candidate == name:
            return index
    lowered = name.lower()
    for index, candidate in enumerate(names):
        if candidate.lower() == lowered:
            return index
    target = _normalize(name)
    if not target:
        return None
    for index, candidate in enumerate(names):
        if _normalize(candidate) == target:
            return index
    containment = [
        (len(_normalize(candidate)), index)
        for index, candidate in enumerate(names)
        if _normalize(candidate) and (target in _normalize(candidate) or _normalize(candidate) in target)
    ]
    if containment:
        containment.sort()
        return containment[0][1]
    return None


def resolve_any(candidates: Iterable[str], palette_names: Sequence[str] | None) -> int | None:
    """First candidate name that resolves against the live list, else None."""
    for candidate in candidates:
        palette_id = resolve_palette_id(candidate, palette_names)
        if palette_id is not None:
            return palette_id
    return None


def close_matches(name: str, palette_names: Sequence[str] | None, limit: int = 5) -> list[str]:
    """Display names similar to ``name`` — for helpful 'did you mean' errors."""
    if not name or not palette_names:
        return []
    names = [str(item) for item in palette_names]
    stripped = {_strip_marker(candidate): candidate for candidate in names}
    close = difflib.get_close_matches(_strip_marker(name), list(stripped), n=limit, cutoff=0.6)
    target = _normalize(name)
    contains = [candidate for candidate in names if target and target in _normalize(candidate)]
    ordered: list[str] = []
    for candidate in [stripped[hit] for hit in close] + contains:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered[:limit]


def suggest_palettes(mood: str, palette_names: Sequence[str] | None, limit: int = 3) -> list[tuple[int, str]]:
    """Mood text -> [(live id, palette name)] for matching curated palettes.

    Only names present in the live /json/pal list are suggested; returns []
    when nothing matches or the list is unavailable.
    """
    if not mood or not palette_names:
        return []
    words = mood.lower()
    groups = [
        candidates
        for keywords, candidates in MOOD_PALETTES
        if any(keyword in words for keyword in keywords)
    ]
    suggestions: list[tuple[int, str]] = []
    # First suggestion from each matching mood group, then fill in priority
    # order, so "calm ocean" mixes calm and water palettes instead of
    # exhausting the limit on the first group.
    resolved = [
        [(pid, name) for name in candidates if (pid := resolve_palette_id(name, palette_names)) is not None]
        for candidates in groups
    ]
    ranked = [entry for pair in zip(*resolved) for entry in pair] if resolved else []
    ranked += [entry for group in resolved for entry in group]
    for entry in ranked:
        if entry not in suggestions:
            suggestions.append(entry)
        if len(suggestions) >= limit:
            break
    return suggestions[:limit]


def dynamic_palette_line() -> str:
    """One prompt-ready line explaining the dynamic ids 0-5."""
    return (
        "Palette ids 0-5 are dynamic color-slot modes, not gradients: "
        "0 Default (auto per effect, usually from the primary color), "
        "1 Random Cycle (changes every few seconds), 2 Color 1 (primary only), "
        "3 Colors 1&2, 4 Color Gradient (smooth blend of all 3 color slots), "
        "5 Colors Only (steps through the 3 color slots)."
    )


def curated_palette_lines(palette_names: Sequence[str] | None) -> list[str]:
    """'id Name — note' lines for curated palettes present in the live list."""
    entries = []
    for name, (_typical_id, description) in KNOWN_PALETTES.items():
        palette_id = resolve_palette_id(name, palette_names)
        if palette_id is not None:
            entries.append((palette_id, f"{palette_id} {name} — {description}"))
    return [line for _pid, line in sorted(entries)]


def prompt_lines(palette_names: Sequence[str] | None) -> list[str]:
    """AI-context lines: curated palette notes plus the dynamic-ids explainer."""
    if not palette_names:
        return []
    lines = []
    curated = curated_palette_lines(palette_names)
    if curated:
        lines.append("Curated palette notes (ids resolved live):\n  " + "\n  ".join(curated))
    lines.append(dynamic_palette_line())
    return lines
