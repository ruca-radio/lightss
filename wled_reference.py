"""Official WLED effect and palette reference (names, descriptions, intensity).

Data transcribed from the WLED wiki "List of effects and palettes":
    https://github.com/wled/WLED/wiki/List-of-effects-and-palettes

The wiki table is written for an older WLED release: **treat effect and palette
IDs as historical only**. Effect order (and therefore the numeric id) differs
between releases and build options, and palette order can shift between versions
too. This module is keyed by NAME and is meant to be merged with the live catalog
read from each controller (/json/eff, /json/pal) so the AI can reason about what
an effect does and what the intensity slider means without inventing ids.

Public API (pure, stdlib only, never raises on unknown names):

    effect_info(name)        -> {"name","description","intensity"} | None
    palette_info(name)       -> {"name","description"} | None
    annotate_effect(entry)   -> copy of entry with description/intensity when known
    annotate_palette(entry)  -> copy of entry with description when known
    search_effects(query, limit=8)   -> [{"name","description","intensity"}]
    search_palettes(query, limit=8)  -> [{"name","description"}]
    stats()                  -> {"effects": int, "palettes": int}
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

SOURCE_URL = "https://github.com/wled/WLED/wiki/List-of-effects-and-palettes"
SOURCE_NOTE = (
    "Names/descriptions transcribed from the WLED wiki list (older release). "
    "Never copy numeric ids from here; use the live controller catalog."
)

# (wiki_id, name, description, intensity slider effect or None)
EFFECTS: tuple[tuple[int, str, str, str | None], ...] = [(0, 'Solid', 'Solid primary color on all LEDs', None),
 (1, 'Blink', 'Blinks between primary and secondary color', 'Duty cycle of blinking'),
 (2, 'Breathe', 'Fades between primary and secondary color', None),
 (3, 'Wipe', 'Switches between primary and secondary, switching LEDs one by one, start to end', 'Smoothness'),
 (4, 'Wipe Random', 'Same as Wipe, but uses random colors', 'Smoothness'),
 (5, 'Random Colors', 'Applies a new random color to all LEDs', 'Duration of fading between colors'),
 (6,
  'Sweep',
  'Switches between primary and secondary, switching LEDs one by one, start to end to start',
  'Smoothness'),
 (7, 'Dynamic', 'Sets each LED to a random color', '0-127: Set single LED 128-255: Set all LEDs'),
 (8, 'Colorloop', 'Cycle all LEDs through the rainbow colors', '0-127: Pastel colors 128-255: Saturated colors'),
 (9, 'Rainbow', 'Displays rainbow colors along the whole strip', 'Number of rainbows'),
 (10, 'Scan', 'A single primary colored light wanders between start and end', 'Scan dot size'),
 (11, 'Dual Scan', 'Same as Scan but uses two lights starting at both ends', 'scan dot size'),
 (12, 'Fade', 'Fades smoothly between primary and secondary color', None),
 (13, 'Theater', 'Pattern of one lit and two unlit LEDs running', 'Gap in lights'),
 (14, 'Theater Rainbow', 'Same as Theater but uses colors of the rainbow', None),
 (15, 'Running', 'Sine Waves scrolling', 'Wavelength'),
 (16, 'Saw', 'Sawtooth Waves scrolling', 'Wavelength'),
 (17,
  'Twinkle',
  'Random LEDs light up in the primary color with secondary as background, turns all off',
  'Amount of LEDs turning on'),
 (18, 'Dissolve', 'Fills LEDs with primary in random order, then off again', 'Dissolve rate'),
 (19, 'Dissolve Rnd', 'Fills LEDs with random colors in random order, then off again', 'Dissolve rate'),
 (20, 'Sparkle', 'Single random LEDs light up in the primary color for a short time, secondary is background', None),
 (21, 'Sparkle Dark', 'All LEDs are lit in the primary color, single random LEDs turn off for a short time', None),
 (22, 'Sparkle+', 'All LEDs are lit in the primary color, multiple random LEDs turn off for a short time', None),
 (23,
  'Strobe',
  'All LEDs are lit in the secondary color, all LEDs flash in a single short burst in primary color',
  None),
 (24, 'Strobe Rainbow', 'Same as strobe, cycles through the rainbow', None),
 (25,
  'Strobe Mega',
  'All LEDs are lit in the secondary color, all LEDs flash in several short bursts in primary color',
  None),
 (26, 'Blink Rainbow', 'Same as blink, cycles through the rainbow', 'Blink Duty cycle'),
 (27, 'Android', 'Section of varying length running', 'Maximum section length'),
 (28, 'Chase', '2 LEDs in primary color running on secondary', 'Size of chaser'),
 (29, 'Chase Random', 'Like Chase but leaves trail of random color', 'Size of chaser'),
 (30, 'Chase Rainbow', 'Like 28 but leaves trail of rainbow', 'Size of chaser'),
 (31,
  'Chase Flash',
  '2 LEDs flash in secondary color while the rest is lit in primary. The flashing LEDs wander from start to end',
  None),
 (32,
  'Chase Flash Rnd',
  'Like Chase Flash, but the 2 LEDs flash in random colors and leaves a random color behind',
  None),
 (33,
  'Rainbow Runner',
  'Like Chase, but the 2 LEDs light up in rainbow colors and leave a primary color trail',
  'Size of chaser'),
 (34, 'Colorful', 'Shifting Red-Amber-Green-Blue pattern', '0-127: Pastel colors 128-255: Saturated colors'),
 (35, 'Traffic Light', 'Emulates a traffic light', None),
 (36, 'Sweep Random', 'Like Sweep, but uses random colors', 'Smoothness'),
 (37,
  'Running 2',
  'Pattern of n LEDs primary and n LEDs secondary moves along the strip',
  'Amount of LEDs lit/unlit'),
 (38, 'Aurora', 'Simulation of the Aurora Borealis', 'Number of waves'),
 (39, 'Stream', 'Flush bands random hues along the string', 'Width of each band (lower is wider)'),
 (40, 'Scanner', 'Dot moves between ends, leaving behing a fading trail', 'Fade rate'),
 (41, 'Lighthouse', 'Dot moves from start to end, leaving behing a fading trail', 'Fade rate'),
 (42, 'Fireworks', 'Random color blobs light up, then fade again', 'Amount of fireworks'),
 (43, 'Rain', 'Like Fireworks, but the blobs move', 'Amout of Rain'),
 (44, 'Merry Christmas', 'Running 2, but always uses red and green', 'Amount of LEDs lit/unlit'),
 (45, 'Fire Flicker', 'LEDs randomly flickering', 'Flickering intensity'),
 (46, 'Gradient', 'Moves a saturation gradient of the primary color along the strip', 'Gradient width'),
 (47, 'Loading', 'Moves a sawtooth pattern along the strip', 'Width'),
 (48, 'Police', 'A red and a blue dot running', 'Size of dots'),
 (49, 'Police All', 'Two areas, one red and one blue, sweeping', None),
 (50, 'Two Dots', 'Like Police, but with custom colors', 'Size of dots'),
 (51, 'Two Areas', 'Like Police All, but with custom colors', None),
 (52, 'Circus', 'Pattern of secondary, red and white', 'Width of pattern'),
 (53, 'Halloween', 'Running 2, but always uses orange and purple', 'Amount of LEDs lit/unlit'),
 (54, 'Tri Chase', 'Like Chase, but with 3 colors', 'Width of pattern'),
 (55, 'Tri Wipe', 'Like Wipe but turns LEDs off as "third color"', None),
 (56, 'Tri Fade', 'Fades the whole strip from primary color to secondary color to off', None),
 (57, 'Lightning', 'Short random white strobe similar to a lightning bolt', 'Amount of strobes'),
 (58, 'ICU', 'Two "eyes" running on opposite sides of the strip', None),
 (59, 'Multi Comet', 'Like Scanner, but creates multiple trails', 'Fade rate'),
 (60, 'Scanner Dual', 'Like Scanner, but with two dots running on opposite sides', 'Fade rate'),
 (61, 'Stream 2', 'Flush random hues along the string', None),
 (62,
  'Oscillate',
  'Areas of primary and secondary colors move between opposite ends, combining colors where they touch',
  None),
 (63, 'Pride 2015', 'Rainbow cycling with brightness variation', None),
 (64, 'Juggle', 'Eight colored dots running, leaving trails', 'Fade rate'),
 (65, 'Palette', 'Running color palette', None),
 (66, 'Fire 2012', 'Simulates flickering fire in red and yellow', 'Sparkling of fire'),
 (67, 'Colorwaves', 'Like Pride 2015, but uses palettes', None),
 (68, 'BPM', 'Pulses moving back and forth on palette', 'BPM setting'),
 (69, 'Fill Noise', 'Noise pattern', None),
 (70, 'Noise 1', 'Fast Noise shift pattern', None),
 (71, 'Noise 2', 'Fast Noise shift pattern', None),
 (72, 'Noise 3', 'Noise shift pattern', None),
 (73, 'Noise 4', 'Noise sparkle pattern', None),
 (74, 'Colortwinkles', 'LEDs light up randomly in random colors and fade off again', 'Twinkle rate'),
 (75, 'Lake', 'Calm palette waving', None),
 (76, 'Meteor', 'The primary color creates a trail of randomly decaying color', 'Fade rate'),
 (77, 'Meteor Smooth', 'Smoothly animated meteor', 'Fade rate'),
 (78,
  'Railway',
  'Shows primary and secondary color on alternating LEDs. All LEDs fade to their opposite color and back again',
  'Duty cycle of fade transition'),
 (79, 'Ripple', 'Effect resembling random water ripples', 'Rate of new ripples'),
 (80, 'Twinklefox', 'FastLED gentle twinkling with slow fade in/out', 'Density'),
 (81, 'Twinklecat', 'Twinkling with fast in / slow out', 'Density'),
 (82, 'Halloween Eyes', 'One Pair of blinking eyes at random intervals along strip', 'Fading rade of eyes'),
 (83, 'Solid Pattern', 'Speed sets number of LEDs on, intensity sets off', 'Amount of LEDs turned off'),
 (84, 'Solid Pattern Tri', 'Solid Pattern with three colors', 'Amount of LEDs of each color'),
 (85, 'Spots', 'Solid lights with even distance', 'Size of each spot'),
 (86, 'Spots Fade', 'Spots, getting bigger and smaller', 'Fading of each spot'),
 (87, 'Glitter', 'Rainbow with white sparkles', 'Amount of glitter'),
 (88, 'Candle', 'Flicker resembling a candle flame', 'Intensity of flickering'),
 (89, 'Fireworks Starburst', 'Exploding multicolor fireworks', 'Fragment count'),
 (90, 'Fireworks 1D', 'one dimension fireworks with flare', 'Firing side'),
 (91, 'Bouncing Balls', 'Bouncing ball effect', 'Number of balls'),
 (92, 'Sinelon', 'Fastled sinusoidal moving eye', 'Fade rate'),
 (93, 'Sinelon Dual', 'Sinelon from both directions', 'Fade rate'),
 (94, 'Sinelon Rainbow', 'Sinelon in rainbow colours', 'Fade rate'),
 (95, 'Popcorn', 'popping kernels', 'Number of kernels'),
 (96, 'Drip', 'Water dripping effect', 'Intensity of dripping'),
 (97, 'Plasma', 'Plasma lamp', 'Max brightness'),
 (98, 'Percent', 'Lights up a percentage of segment', 'Percentage'),
 (99, 'Ripple Rainbow', 'Like ripple, but with a dimly lit changing background', 'Rate of new ripples'),
 (100, 'Heartbeat', 'led strip pulsing rhythm similar to a heart beat', None),
 (101, 'Pacifica', 'Gentle, blue-green ocean waves (one color palette currently)', 'Intensity of waves'),
 (102, 'Candle Multi', "Like candle effect, but each LED has it's own flicker pattern", 'Intensity of flicker'),
 (103, 'Solid Glitter', 'Like Glitter, but with solid color background', 'Amount of glitter'),
 (104,
  'Sunrise',
  'Simulates a gradual sunrise or sunset. Speed sets: 0 - static sun, 1 - 60: sunrise time in minutes, 61 - 120: '
  'sunset time in minutes - 60, above: "breathing" rise and set',
  'Sun intensity'),
 (105, 'Phased', 'Sine waves (in sourcecode)', 'Wave brightness against background'),
 (106, 'TwinkleUp', 'Twinkle effect with fade-in', 'Amount of Twinkles'),
 (107, 'Noise Pal', "Peaceful noise that's slow and with gradually changing palettes", 'Wave intensity'),
 (108, 'Sine', 'Controllable sine waves', 'Sinewave frequency'),
 (109, 'Phased Noise', 'Noisy sine waves', 'Wave brightness against background'),
 (110, 'Flow', 'Blend of palette and spot effects', 'waveform frequency'),
 (111, 'Chunchun', 'Birds flying in a circle formation', 'Number of birds'),
 (112, 'Dancing Shadows', 'Moving spotlights', 'Number of spotlights'),
 (113, 'Washing machine', 'Alternating direction (in source)', 'Size of lit sections'),
 (114, 'Candy Cane', 'Running 2 in Red & White', 'Amount of LEDs lit/unlit'),
 (115, 'Blends', 'Blends random colors across palette', 'Blend speed'),
 (116, 'TV Simulator', 'TV light spill simulation', None),
 (117,
  'Dynamic Smooth',
  'Link Dynamic but with smooth palette blends',
  '0-127: Set single LED 128-255: Set all LEDs')]

# (wiki_id, name, description)
PALETTES: tuple[tuple[int, str, str], ...] = [(0,
  'Default',
  'The palette is automatically selected depending on the effect. For most effects, this is the primary color'),
 (1, 'Random Cycle', 'The palette changes to a random one every few seconds. Subject to change'),
 (2, 'Primary color', 'A palette consisting only of the primary color'),
 (3, 'Based on primary', 'Consists of the primary color, as well as a slightly desaturated version and black'),
 (4, 'Set colors', 'A palette which is a mixture of all segment colors'),
 (5, 'Based on set', 'Contains primary, secondary colors and a desaturated version'),
 (6, 'Party', 'Rainbow without green hues'),
 (7, 'Cloud', 'Gray-blueish colors'),
 (8, 'Lava', 'Dark red, yellow and bright white'),
 (9, 'Ocean', 'Blue, teal and white colors'),
 (10, 'Forest', 'Yellow and green hues'),
 (11, 'Rainbow', 'Every hue'),
 (12, 'Rainbow bands', 'Rainbow colors with black spots in-between'),
 (13, 'Sunset', 'Dark blue with purple, red and yellow hues'),
 (14, 'Rivendell', 'Desaturated greens'),
 (15, 'Breeze', 'Teal colors with varying brightness'),
 (16, 'Red & Blue', 'Red running on blue'),
 (17, 'Yellowout', 'Yellow, fading out'),
 (18, 'Analoguous', 'Red running on blue'),
 (19, 'Splash', 'Vibrant pink and magenta'),
 (20, 'Pastel', 'Different hues with very little saturation'),
 (21, 'Sunset 2', 'Yellow and white runnung on dim blue'),
 (22, 'Beech', 'Different shades of light blue'),
 (23, 'Vintage', 'Warm white running on very dim red'),
 (24, 'Departure', 'Greens and white fading out'),
 (25, 'Landscape', 'Blue, white and green gradient'),
 (26, 'Beach', 'Teal and yellow gradient fading out'),
 (27, 'Sherbet', 'Bright white, pink and mint colors'),
 (28, 'Hult', 'White, magenta and teal'),
 (29, 'Hult 64', 'Teal and yellow hues'),
 (30, 'Drywet', 'Blue and yellow gradient'),
 (31, 'Jul', 'Pastel green and red'),
 (32, 'Grintage', 'Yellow fading out'),
 (33, 'Rewhi', 'Bright orange on desaturated purple'),
 (34, 'Tertiary', 'Red, green and blue gradien'),
 (35, 'Fire', 'White, yellow and fading red gradient'),
 (36, 'Icefire', 'Same as Fire, but with blue colors'),
 (37, 'Cyane', 'Desaturated pastel colors'),
 (38, 'Light Pink', 'Desaturated purple hues'),
 (39, 'Autumn', 'Three white fields surrounded by yellow and dim red'),
 (40, 'Magenta', 'White with magenta and blue'),
 (41, 'Magred', 'Magenta and red hues'),
 (42, 'Yelmag', 'Magenta and red hues with a yellow'),
 (43, 'Yelblu', 'Blue with a little yellow'),
 (44, 'Orange & Teal', 'An Orange - Gray - Teal gradient'),
 (45, 'Tiamat', 'A bright meteor with blue, teal and magenta hues'),
 (46, 'April Night', 'Dark blue background with colorful snowflakes'),
 (47, 'Orangery', 'Orange and yellow tones'),
 (48, 'C9', 'Christmas lights palette. Red - amber - green - blue'),
 (49, 'Sakura', 'Pink and rose tones'),
 (50, 'Aurora', 'Greens on dark blue'),
 (51, 'Atlantica', 'Greens & Blues of the ocean'),
 (52, 'C9 2', 'C9 plus yellow'),
 (53, 'C9 New', 'C9, but brighter and with a less purple blue'),
 (54, 'Temperature', 'Temperature mapping'),
 (55, 'Aurora 2', 'Aurora with some pinks & blue')]

# Modern live names that replaced older wiki names (best-effort, same effect).
EFFECT_ALIASES: dict[str, str] = {'Scan Dual': 'Dual Scan', 'Running Dual': 'Running 2'}
PALETTE_ALIASES: dict[str, str] = {'* Color 1': 'Primary color', '* Color Gradient': 'Based on primary', '* Colors Only': 'Set colors', '* Colors 1&2': 'Based on set', 'Analogous': 'Analoguous'}


def _norm(value: object) -> str:
    # Keep "Sparkle+" distinct from "Sparkle": map the plus to a word first.
    text = str(value or "").replace("+", "plus")
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _build_index() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    effects: dict[str, dict[str, Any]] = {}
    for wiki_id, name, description, intensity in EFFECTS:
        effects[_norm(name)] = {
            "wiki_id": wiki_id,
            "name": name,
            "description": description,
            "intensity": intensity,
        }
    palettes: dict[str, dict[str, Any]] = {}
    for wiki_id, name, description in PALETTES:
        palettes[_norm(name)] = {
            "wiki_id": wiki_id,
            "name": name,
            "description": description,
        }
    return effects, palettes


_EFFECT_INDEX, _PALETTE_INDEX = _build_index()
_EFFECT_ALIAS_INDEX = {_norm(alias): _norm(canonical) for alias, canonical in EFFECT_ALIASES.items()}
_PALETTE_ALIAS_INDEX = {_norm(alias): _norm(canonical) for alias, canonical in PALETTE_ALIASES.items()}


def effect_info(name: object) -> dict[str, Any] | None:
    """Description + intensity meaning for a live WLED effect name, if known."""
    key = _norm(name)
    if not key:
        return None
    row = _EFFECT_INDEX.get(key)
    if row is None:
        row = _EFFECT_INDEX.get(_EFFECT_ALIAS_INDEX.get(key, ""))
    return dict(row) if row else None


def palette_info(name: object) -> dict[str, Any] | None:
    """Description for a live WLED palette name, if known."""
    key = _norm(name)
    if not key:
        return None
    row = _PALETTE_INDEX.get(key)
    if row is None:
        row = _PALETTE_INDEX.get(_PALETTE_ALIAS_INDEX.get(key, ""))
    return dict(row) if row else None


def annotate_effect(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of an effect entry with wiki description/intensity added."""
    result = dict(entry)
    info = effect_info(result.get("name"))
    if info:
        result.setdefault("description", info.get("description"))
        result.setdefault("intensity", info.get("intensity"))
    return result


def annotate_palette(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a palette entry with the wiki description added."""
    result = dict(entry)
    info = palette_info(result.get("name"))
    if info:
        result.setdefault("description", info.get("description"))
    return result


def _matches(row: Mapping[str, Any], query: str, extra: Iterable[object] = ()) -> bool:
    haystack = " ".join(
        [str(row.get("name") or ""), str(row.get("description") or ""), str(row.get("intensity") or "")]
        + [str(value) for value in extra]
    ).casefold()
    return all(token in haystack for token in query.casefold().split())


def _limit(value: object, default: int = 8) -> int:
    try:
        return max(1, min(25, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def search_effects(query: str = "", limit: int = 8) -> list[dict[str, Any]]:
    """Search the reference effect table (empty query returns the first rows)."""
    count = _limit(limit)
    query = " ".join(str(query or "").split())
    if query.isdigit():
        rows = [row for row in _EFFECT_INDEX.values() if row.get("wiki_id") == int(query)]
    else:
        rows = [
            row for row in _EFFECT_INDEX.values()
            if not query or _matches(row, query)
        ]
    rows.sort(key=lambda row: row.get("wiki_id", 0))
    return [dict(row) for row in rows[:count]]


def search_palettes(query: str = "", limit: int = 8) -> list[dict[str, Any]]:
    """Search the reference palette table (empty query returns the first rows)."""
    count = _limit(limit)
    query = " ".join(str(query or "").split())
    if query.isdigit():
        rows = [row for row in _PALETTE_INDEX.values() if row.get("wiki_id") == int(query)]
    else:
        rows = [
            row for row in _PALETTE_INDEX.values()
            if not query or _matches(row, query)
        ]
    rows.sort(key=lambda row: row.get("wiki_id", 0))
    return [dict(row) for row in rows[:count]]


def stats() -> dict[str, int]:
    return {"effects": len(_EFFECT_INDEX), "palettes": len(_PALETTE_INDEX)}
