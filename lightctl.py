#!/usr/bin/env python3
"""Small controller for a WLED-compatible bedroom light."""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TypedDict

logger = logging.getLogger("lightctl")

DEFAULT_HOST = "http://10.27.27.110"
JSON_PATH = "/json"
STATE_PATH = "/json/state"
EFFECTS_PATH = "/json/eff"      # individual effects list (main /json also returns "effects")
PALETTES_PATH = "/json/pal"      # individual palettes list (main /json also returns "palettes")
NODES_PATH = "/json/nodes"
LIVE_PATH = "/json/live"       # optional; many builds return 501. Use E1.31/Art-Net/DDP for realtime per https://kno.wled.ge/interfaces/e1.31-dmx/
CONFIG_PATH = "/json/cfg"
FXDATA_PATH = "/json/fxdata"     # effect metadata (v0.14+)
NETWORKS_PATH = "/json/net"
PRESETS_PATH = "/json/presets"   # optional; not present on all versions
PRESETS_JSON_PATH = "/presets.json"  # common way to get full preset list (may require no password)
SAFE_EFFECTS = {
    2: "Breathe",
    8: "Colorloop",
    9: "Rainbow",
    12: "Fade",
    28: "Chase",
    30: "Chase Rainbow",
    33: "Rainbow Runner",
    37: "Chase 2",
    52: "Running Dual",
    54: "Chase 3",
    62: "Oscillate",
    63: "Pride 2015",
    64: "Juggle",
    67: "Colorwaves",
    74: "Lake",
    76: "Meteor Smooth",
    90: "Sinelon",
    92: "Sinelon Rainbow",
    98: "Pacifica",
    105: "Sine",
    108: "Flow",
    115: "Drift Rose",
    120: "Waving Cell",
    122: "Pixelwave",
    130: "Waterfall",
    162: "Drift",
    163: "Waverly",
    172: "Swirl",
    179: "Flow Stripe",
    183: "Wavesins",
}

# Effect ids that must never be sent, even when a device's live /json/eff list
# contains them (validate_effect hook; keep empty until a blocklist is needed).
BLOCKED_EFFECTS: set[int] = set()


# ---------------------------------------------------------------------------
# Typed payloads
# ---------------------------------------------------------------------------

class SegPayload(TypedDict, total=False):
    id: int
    start: int
    stop: int
    len: int
    col: list[list[int]]
    fx: int
    sx: int
    ix: int
    pal: int
    c1: int
    c2: int
    c3: int
    o1: int
    o2: int
    o3: int
    on: bool
    frz: bool
    rev: bool
    mi: bool
    bri: int
    grp: int
    spc: int
    of: int
    cct: int
    i: list
    fxdef: bool


class WledPayload(TypedDict, total=False):
    on: bool
    bri: int
    seg: list[SegPayload]
    transition: int
    ps: int
    pl: int
    psave: int
    n: str
    ib: bool
    sb: bool
    pdel: int
    playlist: dict
    np: bool
    nl: dict
    udpn: dict
    AudioReactive: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clamp_byte(value: int | float) -> int:
    return max(0, min(255, int(value)))


def _transition_units(transition_ms: int) -> int:
    """Convert milliseconds to WLED 'transition' units.

    Per the WLED JSON API the 'transition' value is 0-255 in units of 100ms,
    so 500ms becomes 5 and anything above 25500ms clamps to 255.
    """
    return max(0, min(255, round(transition_ms / 100)))


def normalize_host(host: str) -> str:
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def on_payload(enabled: bool, transition_ms: int = 0) -> WledPayload:
    payload: WledPayload = {"on": enabled}
    if transition_ms > 0:
        payload["transition"] = _transition_units(transition_ms)
    return payload


def brightness_payload(brightness: int, transition_ms: int = 0) -> WledPayload:
    payload: WledPayload = {"bri": clamp_byte(brightness)}
    if transition_ms > 0:
        payload["transition"] = _transition_units(transition_ms)
    return payload


def color_payload(
    red: int, green: int, blue: int, white: int = 0,
    red2: int | None = None, green2: int | None = None, blue2: int | None = None, white2: int | None = None,
    red3: int | None = None, green3: int | None = None, blue3: int | None = None, white3: int | None = None,
    transition_ms: int = 0,
    seg_id: int | None = None
) -> WledPayload:
    colors = [[clamp_byte(red), clamp_byte(green), clamp_byte(blue), clamp_byte(white)]]
    if red2 is not None or green2 is not None or blue2 is not None or white2 is not None:
        colors.append([
            clamp_byte(red2 or 0),
            clamp_byte(green2 or 0),
            clamp_byte(blue2 or 0),
            clamp_byte(white2 or 0)
        ])
        if red3 is not None or green3 is not None or blue3 is not None or white3 is not None:
            colors.append([
                clamp_byte(red3 or 0),
                clamp_byte(green3 or 0),
                clamp_byte(blue3 or 0),
                clamp_byte(white3 or 0)
            ])
    seg: SegPayload = {"col": colors}
    if seg_id is not None:
        seg["id"] = seg_id
    payload: WledPayload = {"seg": [seg]}
    if transition_ms > 0:
        payload["transition"] = _transition_units(transition_ms)
    return payload


def validate_effect(fx: int, allowed: set[int] | None = None) -> int:
    """Validate an effect id. With a live device id set (from /json/eff indices)
    accept anything it contains except BLOCKED_EFFECTS; otherwise fall back to
    the offline SAFE_EFFECTS allowlist. Returns the effect id on success."""
    fx = int(fx)
    if fx in BLOCKED_EFFECTS:
        raise ValueError(f"Effect {fx} is blocked.")
    if allowed is not None:
        if fx not in allowed:
            raise ValueError(f"Effect {fx} is not available on the target device.")
        return fx
    if fx not in SAFE_EFFECTS:
        allowed_str = ", ".join(f"{effect_id}={name}" for effect_id, name in SAFE_EFFECTS.items())
        raise ValueError(f"Effect {fx} is not allowed. Safe effects: {allowed_str}.")
    return fx


def effect_payload(
    effect: int,
    speed: int = 128,
    transition_ms: int = 0,
    intensity: int | None = None,
    palette: int | None = None,
    c1: int | None = None,
    c2: int | None = None,
    c3: int | None = None,
    o1: int | None = None,
    o2: int | None = None,
    o3: int | None = None,
    seg_id: int | None = None,
    fxdef: bool = False,
) -> WledPayload:
    effect = validate_effect(effect)
    seg: SegPayload = {"fx": effect, "sx": clamp_byte(speed)}
    if seg_id is not None:
        seg["id"] = seg_id
    if fxdef:
        # fxdef tells WLED (0.14+) to apply the effect's tuned fxdata defaults.
        seg["fxdef"] = True
    optional_fields = {
        "ix": intensity,
        "pal": palette,
        "c1": c1,
        "c2": c2,
        "c3": c3,
        "o1": o1,
        "o2": o2,
        "o3": o3,
    }
    for key, value in optional_fields.items():
        if value is None:
            continue
        if key in ("o1", "o2", "o3"):
            seg[key] = bool(value)  # type: ignore[literal-required]
        elif key == "c3":
            seg[key] = max(0, min(31, int(value)))  # WLED c3 range is 0-31
        else:
            seg[key] = clamp_byte(value)  # type: ignore[literal-required]
    payload: WledPayload = {"seg": [seg]}
    if transition_ms > 0:
        payload["transition"] = _transition_units(transition_ms)
    return payload


def palette_payload(
    palette: int,
    seg_id: int | None = None,
    transition_ms: int = 0,
) -> WledPayload:
    seg: SegPayload = {"pal": int(palette)}
    if seg_id is not None:
        seg["id"] = seg_id
    payload: WledPayload = {"seg": [seg]}
    if transition_ms > 0:
        payload["transition"] = _transition_units(transition_ms)
    return payload


def cct_payload(
    cct: int,
    seg_id: int | None = None,
    transition_ms: int = 0,
) -> WledPayload:
    seg: SegPayload = {"cct": clamp_byte(cct)}
    if seg_id is not None:
        seg["id"] = seg_id
    payload: WledPayload = {"seg": [seg]}
    if transition_ms > 0:
        payload["transition"] = _transition_units(transition_ms)
    return payload


def segment_payload(segments: list[dict]) -> WledPayload:
    """Build a multi-segment payload from raw segment dicts, validated."""
    if not segments:
        raise ValueError("segment_payload requires at least one segment.")
    payload: WledPayload = {"seg": [dict(segment) for segment in segments]}
    validate_wled_payload(payload)
    return payload


def validate_wled_payload(payload: WledPayload | dict) -> WledPayload | dict:
    """Validate project-level safety constraints before sending WLED JSON."""
    segments = payload.get("seg") if isinstance(payload, dict) else None
    if segments is None:
        return payload
    if not isinstance(segments, list):
        raise ValueError("WLED payload field 'seg' must be a list.")
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("WLED segment payloads must be objects.")
        if segment.get("fx") is None:
            continue
        effect = int(segment["fx"])
        if effect < 0 or effect > 255:
            raise ValueError(f"Effect {effect} is out of range (0-255).")
        if effect in BLOCKED_EFFECTS:
            raise ValueError(f"Effect {effect} is blocked.")
    return payload


def reactive_beat_payload(
    color: tuple[int, int, int, int],
    brightness: int,
    effect: int,
    speed: int,
    transition_ms: int = 0,
) -> WledPayload:
    return merge_payloads(
        on_payload(True, transition_ms=transition_ms),
        brightness_payload(brightness, transition_ms=transition_ms),
        color_payload(*color, transition_ms=transition_ms),
        effect_payload(effect, speed, transition_ms=transition_ms),
    )


def _merge_segments(existing: list[SegPayload], incoming: list[SegPayload]) -> None:
    """Merge incoming seg entries per segment id: entries with an id merge into
    the existing entry with the same id (appended if absent); id-less entries
    merge into seg[0], preserving the historical single-segment behavior."""
    for segment in incoming:
        seg_id = segment.get("id")
        target: SegPayload | None = None
        if seg_id is None:
            if existing:
                target = existing[0]
        else:
            for candidate in existing:
                if candidate.get("id") == seg_id:
                    target = candidate
                    break
        if target is None:
            existing.append(segment)
        else:
            target.update(segment)


def merge_payloads(*payloads: WledPayload) -> WledPayload:
    merged: WledPayload = {}
    for payload in payloads:
        for key, value in payload.items():
            if key == "seg" and key in merged:
                _merge_segments(merged[key], value)  # type: ignore[arg-type]
            else:
                merged[key] = value  # type: ignore[literal-required]
    return merged


# ---------------------------------------------------------------------------
# Zones and per-LED geometry
# ---------------------------------------------------------------------------

# Physical model: each channel is one vertical bar, COLUMN_LENGTH_M meters tall,
# with LEDS_PER_COLUMN addressable WS2811 IC pixels (20 addressable WS2811 IC
# pixels/m; 40 per 2 m column; 720 visible COB LEDs/m; pixel 0 at the bottom).
LEDS_PER_COLUMN = 40
COLUMN_LENGTH_M = 2.0

_ZONE_POSITIONS = ("top", "middle", "bottom")
_ZONE_FRACTIONS = {"half": 2, "third": 3, "quarter": 4}
_LED_ORIENTATIONS = ("up", "down")
_BYTE_SEG_FIELDS = ("sx", "ix", "c1", "c2", "c3", "bri")


def zone_bounds(zone: str, length: int = LEDS_PER_COLUMN, orientation: str = "up") -> tuple[int, int]:
    """LED [start, stop) bounds for a named zone of a column.

    Zones are physical positions on the bar: "<top|middle|bottom> <half|third|quarter>"
    or "all". "bottom"/"top" are the first/last fraction of the bar; "middle" is the
    centered fraction ("middle quarter" = the central 25%). orientation="up" means
    LED 0 is at the bottom, so "top" maps to the high LED indices; orientation="down"
    flips the mapping.
    """
    length = int(length)
    if length <= 0:
        raise ValueError(f"Zone length must be positive, got {length}.")
    if orientation not in _LED_ORIENTATIONS:
        raise ValueError(f"Unknown orientation '{orientation}'. Use one of: {', '.join(_LED_ORIENTATIONS)}.")
    key = " ".join(str(zone).strip().lower().split())
    if key == "all":
        return (0, length)
    parts = key.split(" ")
    if len(parts) != 2 or parts[0] not in _ZONE_POSITIONS or parts[1] not in _ZONE_FRACTIONS:
        raise ValueError(f"Unknown zone '{zone}'. Use 'all' or '<top|middle|bottom> <half|third|quarter>'.")
    position, fraction = parts
    n = _ZONE_FRACTIONS[fraction]
    if position == "bottom":
        p0, p1 = 0.0, 1.0 / n
    elif position == "top":
        p0, p1 = 1.0 - 1.0 / n, 1.0
    else:  # middle: the centered fraction of the bar
        p0, p1 = 0.5 - 0.5 / n, 0.5 + 0.5 / n
    start = round(length * p0)
    stop = round(length * p1)
    if orientation == "down":
        start, stop = length - stop, length - start
    if start >= stop:
        # Per WLED, stop <= start deletes the segment; never emit empty bounds.
        raise ValueError(
            f"Zone '{zone}' of length {length} resolves to an empty range (start={start}, stop={stop})."
        )
    return (start, stop)


def _hex_to_rgb(color: object) -> list[int]:
    """Normalize a color to an [r, g, b] list. Accepts 'RRGGBB'/'#RRGGBB' hex
    strings or [r, g, b](, w) sequences."""
    if isinstance(color, str):
        hex_color = color.strip().lstrip("#")
        if len(hex_color) not in (6, 8):
            raise ValueError(f"Hex color must be RRGGBB or RRGGBBWW, got '{color}'.")
        try:
            return [int(hex_color[i:i + 2], 16) for i in (0, 2, 4)]
        except ValueError:
            raise ValueError(f"Invalid hex color '{color}'.") from None
    if isinstance(color, (list, tuple)) and 3 <= len(color) <= 4:
        try:
            return [clamp_byte(color[0]), clamp_byte(color[1]), clamp_byte(color[2])]
        except (TypeError, ValueError):
            pass
    raise ValueError(f"Color must be an RRGGBB hex string or [r, g, b] list, got {color!r}.")


def zone_payload(zones: list[dict], length: int = LEDS_PER_COLUMN, orientation: str = "up") -> WledPayload:
    """Build a multi-segment payload carving a column into zones.

    Each zone dict carries either {"zone": "<position> <fraction>"} or explicit
    {"start": n, "stop": m} bounds, an optional "id" (present = update that segment,
    absent = append a new one), plus any seg fields (fx/pal/col/sx/ix/rev/mi/on...)
    passed through; "col" may be an RRGGBB hex string. Segments always get explicit
    start/stop bounds.
    """
    if not zones:
        raise ValueError("zone_payload requires at least one zone.")
    segments: list[SegPayload] = []
    for zone in zones:
        if not isinstance(zone, dict):
            raise ValueError(f"Each zone must be a dict, got {zone!r}.")
        entry = dict(zone)
        if "zone" in entry:
            start, stop = zone_bounds(str(entry.pop("zone")), length=length, orientation=orientation)
            # Stray start/stop keys must not silently override the computed bounds
            # via seg.update(entry) below.
            entry.pop("start", None)
            entry.pop("stop", None)
        else:
            if "start" not in entry or "stop" not in entry:
                raise ValueError("Each zone needs either a 'zone' name or explicit 'start' and 'stop'.")
            start, stop = int(entry.pop("start")), int(entry.pop("stop"))
            if not (0 <= start < stop <= length):
                raise ValueError(
                    f"Zone bounds must satisfy 0 <= start < stop <= {length}, got start={start}, stop={stop}."
                )
        seg_id = entry.pop("id", None)
        col = entry.get("col")
        if isinstance(col, str):
            entry["col"] = [_hex_to_rgb(col)]
        for field in _BYTE_SEG_FIELDS:
            if field in entry and entry[field] is not None:
                entry[field] = clamp_byte(entry[field])
        seg: SegPayload = {"start": start, "stop": stop}
        if seg_id is not None:
            seg["id"] = int(seg_id)
        seg.update(entry)  # type: ignore[typeddict-item]
        segments.append(seg)
    payload: WledPayload = {"seg": segments}
    validate_wled_payload(payload)
    return payload


def _color_token(color) -> str:
    """Normalize a color to an RRGGBB hex string for WLED 'i' arrays."""
    if isinstance(color, str):
        return "".join(f"{c:02X}" for c in _hex_to_rgb(color))
    return "".join(f"{clamp_byte(c):02X}" for c in list(color)[:3])


def leds_payload(led_ranges: list, seg_id: int | None = None) -> WledPayload:
    """Build a per-LED payload emitting the WLED seg 'i' array.

    led_ranges is either a flat list of colors applied from LED 0 upward
    (["RRGGBB", ...] or [r, g, b] entries) or a list of [start, stop, color]
    ranges (stop exclusive, bounded by LEDS_PER_COLUMN). Colors are emitted
    as RRGGBB hex strings per the WLED JSON API ('i' grammar). Setting
    individual LEDs freezes the running effect on the segment.
    """
    if not led_ranges:
        raise ValueError("leds_payload requires at least one LED color or range.")
    individual: list = []
    for item in led_ranges:
        if isinstance(item, (list, tuple)) and len(item) == 3 and isinstance(item[2], (str, list, tuple)) and isinstance(item[0], (int, float)) and isinstance(item[1], (int, float)):
            start, stop = int(item[0]), int(item[1])
            if not (0 <= start < stop <= LEDS_PER_COLUMN):
                raise ValueError(
                    f"LED range must satisfy 0 <= start < stop <= {LEDS_PER_COLUMN}, got start={start}, stop={stop}."
                )
            individual.extend([start, stop, _color_token(item[2])])
        else:
            individual.append(_color_token(item))
    seg: SegPayload = {"i": individual}
    if seg_id is not None:
        seg["id"] = int(seg_id)
    payload: WledPayload = {"seg": [seg]}
    validate_wled_payload(payload)
    return payload


# ---------------------------------------------------------------------------
# Built-in scenes
# ---------------------------------------------------------------------------

_builtin_scenes: dict[str, WledPayload] = {
    "warm": merge_payloads(
        on_payload(True), brightness_payload(180), color_payload(255, 150, 60, 120)
    ),
    "night": merge_payloads(
        on_payload(True), brightness_payload(25), color_payload(255, 70, 0, 0)
    ),
    "focus": merge_payloads(
        on_payload(True), brightness_payload(230), color_payload(255, 255, 220, 180)
    ),
    "ocean": merge_payloads(
        on_payload(True), brightness_payload(190), color_payload(0, 70, 255, 0)
    ),
    "party": merge_payloads(
        on_payload(True),
        brightness_payload(230),
        effect_payload(9, 180),
        color_payload(255, 0, 180, 0),
    ),
}


# ---------------------------------------------------------------------------
# Scene persistence
# ---------------------------------------------------------------------------

_SCENE_DIR = os.path.expanduser("~/.config/lightss")
_SCENE_PATH = os.path.join(_SCENE_DIR, "scenes.json")


def _load_scenes() -> dict[str, WledPayload]:
    try:
        with open(_SCENE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_scenes(scenes: dict[str, WledPayload]) -> None:
    os.makedirs(_SCENE_DIR, exist_ok=True)
    with open(_SCENE_PATH, "w", encoding="utf-8") as f:
        json.dump(scenes, f, indent=2)


def save_scene(name: str, payload: WledPayload) -> None:
    scenes = _load_scenes()
    scenes[name.strip().lower()] = payload
    _save_scenes(scenes)


def delete_scene(name: str) -> None:
    scenes = _load_scenes()
    scenes.pop(name.strip().lower(), None)
    _save_scenes(scenes)


def list_scenes() -> list[str]:
    return sorted(_load_scenes().keys())


def load_scene_payload(name: str) -> WledPayload:
    scenes = _load_scenes()
    key = name.strip().lower()
    if key in scenes:
        return scenes[key]
    raise ValueError(f"Unknown saved scene: {name}. Saved scenes: {', '.join(list_scenes()) or 'none'}.")


def scene_payload(name: str, transition_ms: int = 0) -> WledPayload:
    key = name.strip().lower()
    if key in _builtin_scenes:
        # Deep copy: callers may stamp seg ids via _with_seg_id(), which mutates
        # nested seg dicts; never hand out the shared module-global scene.
        payload = copy.deepcopy(_builtin_scenes[key])
    else:
        payload = load_scene_payload(name)
    if transition_ms > 0:
        payload["transition"] = _transition_units(transition_ms)
    return payload


# ---------------------------------------------------------------------------
# Schedule persistence
# ---------------------------------------------------------------------------

_SCHEDULE_PATH = os.path.join(_SCENE_DIR, "schedule.json")


def _load_schedule() -> list[dict]:
    try:
        with open(_SCHEDULE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_schedule(schedule: list[dict]) -> None:
    os.makedirs(_SCENE_DIR, exist_ok=True)
    with open(_SCHEDULE_PATH, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2)


def add_schedule(time_str: str, action: str, data: dict | None = None) -> None:
    parts = time_str.split(":")
    if (
        len(parts) != 2
        or not all(part.isdigit() for part in parts)
        or not (0 <= int(parts[0]) <= 23)
        or not (0 <= int(parts[1]) <= 59)
    ):
        raise ValueError(f"Schedule time must be in HH:MM format, got '{time_str}'.")
    schedule = _load_schedule()
    schedule.append({"time": time_str, "action": action, "data": data or {}})
    _save_schedule(schedule)


def remove_schedule(index: int) -> bool:
    schedule = _load_schedule()
    if not (0 <= index < len(schedule)):
        return False
    schedule.pop(index)
    _save_schedule(schedule)
    return True


def list_schedule() -> list[dict]:
    return _load_schedule()


# ---------------------------------------------------------------------------
# Kelvin → RGBW
# ---------------------------------------------------------------------------

def kelvin_to_rgbw(kelvin: int) -> tuple[int, int, int, int]:
    """Approximate RGBW from Kelvin (2000–6500)."""
    kelvin = max(2000, min(6500, kelvin))
    temp = kelvin / 100.0
    if temp <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(temp) - 161.1195681661
        if temp <= 19:
            b = 0.0
        else:
            b = 138.5177312231 * math.log(temp - 10) - 305.0447927307
    else:
        r = 329.698727446 * ((temp - 60) ** -0.1332047592)
        g = 288.1221695283 * ((temp - 60) ** -0.0755148492)
        b = 255.0
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    # White channel peaks around 4000K
    w = int(255 * (1.0 - abs(kelvin - 4000) / 2500.0))
    w = max(0, min(255, w))
    return (r, g, b, w)


def hex_to_rgbw(hex_color: str) -> tuple[int, int, int, int]:
    """Parse #RRGGBB or #RRGGBBWW into RGBW tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        return (
            int(hex_color[0:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
            0,
        )
    if len(hex_color) == 8:
        return (
            int(hex_color[0:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
            int(hex_color[6:8], 16),
        )
    raise ValueError("Hex color must be #RRGGBB or #RRGGBBWW")


def random_scene_payload(transition_ms: int = 0) -> WledPayload:
    """Return a random safe built-in scene."""
    import random
    names = list(_builtin_scenes.keys())
    name = random.choice(names)
    return scene_payload(name, transition_ms=transition_ms)


def restart_payload() -> WledPayload:
    """Return a payload that triggers a WLED controller reboot."""
    return {"rb": True}


class FadeTimer:
    """Gradually reduce brightness over a duration, then turn off."""

    def __init__(self, client: LightClient, duration_minutes: float, start_brightness: int | None = None) -> None:
        self.client = client
        self.duration_minutes = max(1, duration_minutes)
        self.start_brightness = start_brightness
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Fade timer is already running."
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return f"Fade timer started ({self.duration_minutes} min)."

    def stop(self) -> str:
        self._stop.set()
        return "Fade timer stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            start_bri = self.start_brightness
            if start_bri is None:
                state = self.client.get_state()
                start_bri = state.get("bri", 128)
            steps = int(self.duration_minutes * 6)  # update every 10s
            for i in range(steps + 1):
                if self._stop.is_set():
                    return
                bri = int(start_bri * (1 - i / steps))
                self.client.post_state(brightness_payload(bri))
                if i < steps:
                    time.sleep(10)
            self.client.post_state(on_payload(False))
        except Exception:
            logger.exception("Fade timer error")


# ---------------------------------------------------------------------------
# WLED Info
# ---------------------------------------------------------------------------

INFO_PATH = "/json/info"


@dataclass
class WledInfo:
    name: str
    version: str
    led_count: int
    udp_port: int
    live: bool
    arch: str
    core: str
    free_heap: int
    uptime: int
    opt: int
    brand: str
    product: str
    mac: str
    ip: str

    @classmethod
    def from_dict(cls, data: dict) -> "WledInfo":
        return cls(
            name=data.get("name", "Unknown"),
            version=data.get("ver", "?"),
            led_count=data.get("leds", {}).get("count", 0),
            udp_port=data.get("udpport", 0),
            live=data.get("live", False),
            arch=data.get("arch", "?"),
            core=data.get("core", "?"),
            free_heap=data.get("freeheap", 0),
            uptime=data.get("uptime", 0),
            opt=data.get("opt", 0),
            brand=data.get("brand", "WLED"),
            product=data.get("product", "?"),
            mac=data.get("mac", ""),
            ip=data.get("ip", ""),
        )


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

PRESET_MIN = 1
PRESET_MAX = 250


def preset_payload(preset_id: int, transition_ms: int = 0) -> WledPayload:
    preset_id = int(preset_id)
    if not (PRESET_MIN <= preset_id <= PRESET_MAX):
        raise ValueError(f"Preset ID must be between {PRESET_MIN} and {PRESET_MAX}.")
    payload: WledPayload = {"ps": preset_id}
    if transition_ms > 0:
        payload["transition"] = _transition_units(transition_ms)
    return payload


PLAYLIST_MIN = 1
PLAYLIST_MAX = 250


def playlist_payload(playlist_id: int, transition_ms: int = 0) -> WledPayload:
    playlist_id = int(playlist_id)
    if not (PLAYLIST_MIN <= playlist_id <= PLAYLIST_MAX):
        raise ValueError(f"Playlist ID must be between {PLAYLIST_MIN} and {PLAYLIST_MAX}.")
    payload: WledPayload = {"pl": playlist_id}
    if transition_ms > 0:
        payload["transition"] = _transition_units(transition_ms)
    return payload


def save_preset_payload(
    preset_id: int,
    name: str | None = None,
    include_brightness: bool = True,
    include_bounds: bool = True,
) -> WledPayload:
    """Save the current state into an on-device preset (psave).

    NOTE: preset saves are LittleFS writes and stall the device for seconds —
    call sparingly, never in a loop. Boot presets should include segment bounds.
    """
    preset_id = int(preset_id)
    if not (PRESET_MIN <= preset_id <= PRESET_MAX):
        raise ValueError(f"Preset ID must be between {PRESET_MIN} and {PRESET_MAX}.")
    payload: WledPayload = {"psave": preset_id}
    if name:
        payload["n"] = str(name)
    if include_brightness:
        payload["ib"] = True
    if include_bounds:
        payload["sb"] = True
    return payload


def delete_preset_payload(preset_id: int) -> WledPayload:
    """Delete an on-device preset (pdel). Like psave, this stalls the device."""
    preset_id = int(preset_id)
    if not (PRESET_MIN <= preset_id <= PRESET_MAX):
        raise ValueError(f"Preset ID must be between {PRESET_MIN} and {PRESET_MAX}.")
    return {"pdel": preset_id}


def _tenths(seconds: int | float) -> int:
    """Convert seconds to WLED playlist tenths-of-a-second units (min 1)."""
    return max(1, round(float(seconds) * 10))


def playlist_create_payload(
    preset_ids: Iterable[int],
    durations: int | float | Sequence[int | float],
    transition: int | float = 0,
    repeat: int = 0,
    end: int | None = None,
) -> WledPayload:
    """Create and start an on-device playlist (the 'playlist' state object).

    durations/transition are in SECONDS here (scalar or per-preset list) and
    are converted to the tenths-of-a-second units WLED expects; a scalar dur
    stays scalar (uniform value). repeat 0/omitted = loop indefinitely;
    end = preset applied after the last repeat.
    """
    ids = [int(preset_id) for preset_id in preset_ids]
    if not ids:
        raise ValueError("playlist_create_payload requires at least one preset id.")
    for preset_id in ids:
        if not (PRESET_MIN <= preset_id <= PRESET_MAX):
            raise ValueError(f"Preset ID must be between {PRESET_MIN} and {PRESET_MAX}.")
    if isinstance(durations, (int, float)):
        dur: int | list[int] = _tenths(durations)
    else:
        dur = [_tenths(item) for item in durations]
        if len(dur) != len(ids):
            raise ValueError(
                f"Playlist durations ({len(dur)}) must match preset count ({len(ids)})."
            )
    playlist: dict = {
        "ps": ids,
        "dur": dur,
        "transition": max(0, min(255, round(float(transition) * 10))),
    }
    if repeat:
        playlist["repeat"] = int(repeat)
    if end is not None:
        end = int(end)
        if not (PRESET_MIN <= end <= PRESET_MAX):
            raise ValueError(f"Preset ID must be between {PRESET_MIN} and {PRESET_MAX}.")
        playlist["end"] = end
    return {"playlist": playlist}


def next_preset_payload() -> WledPayload:
    """Skip to the next preset of the running playlist (np, WLED 0.15+)."""
    return {"np": True}


def list_presets(presets: dict) -> list[dict]:
    """Flatten a /presets.json dict into sorted [{id, name, is_playlist}]."""
    entries = []
    for key, value in (presets or {}).items():
        if not isinstance(value, dict):
            continue
        try:
            preset_id = int(key)
        except (TypeError, ValueError):
            continue
        entries.append({
            "id": preset_id,
            "name": str(value.get("n") or f"Preset {preset_id}"),
            "is_playlist": "playlist" in value,
        })
    return sorted(entries, key=lambda entry: entry["id"])


def find_preset_id(presets: dict, name_or_id: int | str) -> int | None:
    """Resolve a preset id or (case-insensitive) name to an id; None if unknown."""
    entries = list_presets(presets)
    if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.strip().isdigit()):
        preset_id = int(name_or_id)
        return preset_id if any(entry["id"] == preset_id for entry in entries) else None
    wanted = str(name_or_id).strip().lower()
    for entry in entries:
        if entry["name"].lower() == wanted:
            return entry["id"]
    return None


def preset_name(presets: dict, preset_id: int) -> str | None:
    """Name of a preset id, or None when the id is not a saved preset."""
    try:
        preset_id = int(preset_id)
    except (TypeError, ValueError):
        return None
    for entry in list_presets(presets):
        if entry["id"] == preset_id:
            return entry["name"]
    return None


def current_preset(presets: dict, state: dict) -> dict:
    """Match state.ps against the preset list -> {"id", "name"}; {} when none."""
    preset_id = state.get("ps") if isinstance(state, dict) else None
    if not isinstance(preset_id, int) or preset_id < 0:
        return {}
    name = preset_name(presets, preset_id)
    return {"id": preset_id, "name": name} if name else {}


# ---------------------------------------------------------------------------
# Scene Cycle / Playlist
# ---------------------------------------------------------------------------

class CycleThread:
    """Auto-rotate through a list of scenes or presets at a given interval."""

    def __init__(
        self,
        client: LightClient,
        items: list[str] | None = None,
        interval_seconds: float = 60.0,
        mode: str = "scene",
    ) -> None:
        self.client = client
        self.items = items or list(_builtin_scenes.keys())
        self.interval_seconds = max(5.0, interval_seconds)
        self.mode = mode  # "scene" or "preset"
        self._index = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Cycle is already running."
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return f"Cycle started ({len(self.items)} items, {self.interval_seconds}s interval)."

    def stop(self) -> str:
        self._stop.set()
        return "Cycle stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        logger.info("Scene cycle started.")
        while not self._stop.is_set():
            try:
                item = self.items[self._index % len(self.items)]
                if self.mode == "preset":
                    self.client.post_state(preset_payload(int(item)))
                else:
                    self.client.post_state(scene_payload(item))
                logger.info("Cycled to: %s", item)
            except Exception:
                logger.exception("Cycle step failed")
            self._index += 1
            # Sleep in small chunks so stop is responsive, honoring fractional intervals
            remaining = self.interval_seconds
            while remaining > 0 and not self._stop.is_set():
                time.sleep(min(1.0, remaining))
                remaining -= 1.0


# ---------------------------------------------------------------------------
# Sunrise Simulator
# ---------------------------------------------------------------------------

class SunriseSimulator:
    """Gradually increase brightness and shift color temperature from warm to daylight."""

    def __init__(
        self,
        client: LightClient,
        duration_minutes: float = 30.0,
        start_kelvin: int = 2000,
        end_kelvin: int = 5000,
        max_brightness: int = 255,
    ) -> None:
        self.client = client
        self.duration_minutes = max(1, duration_minutes)
        self.start_kelvin = start_kelvin
        self.end_kelvin = end_kelvin
        self.max_brightness = clamp_byte(max_brightness)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Sunrise simulation is already running."
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return f"Sunrise started ({self.duration_minutes} min)."

    def stop(self) -> str:
        self._stop.set()
        return "Sunrise stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        logger.info("Sunrise simulation started.")
        try:
            steps = int(self.duration_minutes * 6)  # every 10s
            for i in range(steps + 1):
                if self._stop.is_set():
                    return
                progress = i / steps
                bri = int(self.max_brightness * progress)
                kelvin = int(self.start_kelvin + (self.end_kelvin - self.start_kelvin) * progress)
                rgbw = kelvin_to_rgbw(kelvin)
                payload = merge_payloads(
                    on_payload(True),
                    brightness_payload(bri),
                    color_payload(*rgbw),
                )
                self.client.post_state(payload)
                if i < steps:
                    time.sleep(10)
        except Exception:
            logger.exception("Sunrise simulation error")


# ---------------------------------------------------------------------------
# Configuration file
# ---------------------------------------------------------------------------

_CONFIG_PATH = os.path.join(_SCENE_DIR, "config.json")


def load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(config: dict) -> None:
    os.makedirs(_SCENE_DIR, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def load_dotenv(path: str | None = None) -> int:
    """Load KEY=VALUE lines from a .env file into os.environ (setdefault only).

    Defaults to the .env next to this package. Real environment variables
    always win; comments and blank lines are skipped; optional quotes are
    stripped. Returns the number of variables applied.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    applied = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
                    applied += 1
    except FileNotFoundError:
        pass
    return applied


def get_mic_device() -> str | int | None:
    """Return the preferred microphone device for sounddevice.

    Priority: LIGHT_MIC_DEVICE env var → config mic_device key →
    auto-detect first USB (preferred) or any input device → None (sounddevice default).
    Validates that the chosen device actually exists and has input channels.
    """
    import sounddevice as sd  # type: ignore[import-untyped]

    def _is_valid_input(dev_id: str | int | None) -> bool:
        if dev_id is None:
            return False
        try:
            info = sd.query_devices(device=dev_id, kind="input")
            return bool(info.get("max_input_channels", 0) > 0)
        except Exception:
            return False

    env = os.environ.get("LIGHT_MIC_DEVICE", "").strip()
    if env:
        candidate = int(env) if env.isdigit() else env
        if _is_valid_input(candidate):
            return candidate

    cfg_val = load_config().get("mic_device", "")
    if cfg_val:
        candidate = int(cfg_val) if str(cfg_val).isdigit() else str(cfg_val)
        if _is_valid_input(candidate):
            return candidate

    # Auto-detect: prefer USB audio input, fall back to first available input device
    try:
        devices = sd.query_devices()
        usb_dev = None
        first_input = None
        for i, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) > 0:
                if first_input is None:
                    first_input = i
                if "USB" in dev.get("name", ""):
                    usb_dev = i
                    break
        if usb_dev is not None:
            return usb_dev
        if first_input is not None:
            return first_input
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

@dataclass
class LightClient:
    host: str = DEFAULT_HOST
    timeout: float = 2.5
    dry_run: bool = False
    _last_req_time: float = 0.0
    _http_lock: threading.RLock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.host = normalize_host(self.host)
        self._http_lock = threading.RLock()

    @property
    def json_url(self) -> str:
        return f"{self.host}{JSON_PATH}"

    @property
    def state_url(self) -> str:
        return f"{self.host}{STATE_PATH}"

    @property
    def info_url(self) -> str:
        return f"{self.host}{INFO_PATH}"

    @property
    def effects_url(self) -> str:
        return f"{self.host}{EFFECTS_PATH}"

    @property
    def palettes_url(self) -> str:
        return f"{self.host}{PALETTES_PATH}"

    @property
    def nodes_url(self) -> str:
        return f"{self.host}{NODES_PATH}"

    @property
    def live_url(self) -> str:
        return f"{self.host}{LIVE_PATH}"

    @property
    def config_url(self) -> str:
        return f"{self.host}{CONFIG_PATH}"

    @property
    def fxdata_url(self) -> str:
        return f"{self.host}{FXDATA_PATH}"

    @property
    def networks_url(self) -> str:
        return f"{self.host}{NETWORKS_PATH}"

    @property
    def presets_url(self) -> str:
        return f"{self.host}{PRESETS_PATH}"

    @property
    def presets_json_url(self) -> str:
        return f"{self.host}{PRESETS_JSON_PATH}"

    def _decode_response_json(self, response: urllib.request.addinfourl, url: str) -> dict | list:
        """Decode a response body as JSON, wrapping truncation/empty-body failures
        into RuntimeError so callers only handle one error type for controller issues."""
        try:
            return json.loads(response.read().decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from {url}: {exc}") from exc

    def _get_json_url(self, url: str) -> dict | list:
        request = urllib.request.Request(url, method="GET")
        with self._request_with_retry(request) as response:
            return self._decode_response_json(response, url)

    def get_json(self) -> dict:
        data = self._get_json_url(self.json_url)
        return data if isinstance(data, dict) else {}

    def get_info(self) -> dict:
        data = self._get_json_url(self.info_url)
        return data if isinstance(data, dict) else {}

    def get_effects(self) -> list[str]:
        try:
            data = self._get_json_url(self.effects_url)
            return [str(item) for item in data] if isinstance(data, list) else []
        except Exception:
            return []

    def get_palettes(self) -> list[str]:
        try:
            data = self._get_json_url(self.palettes_url)
            return [str(item) for item in data] if isinstance(data, list) else []
        except Exception:
            return []

    def get_nodes(self) -> dict:
        try:
            data = self._get_json_url(self.nodes_url)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def get_live(self) -> dict:
        """Live/realtime data. Returns 501 on many WLED builds (not implemented or disabled).
        Realtime LED data should use E1.31 (sACN), Art-Net or DDP instead (see https://kno.wled.ge/interfaces/e1.31-dmx/).
        """
        try:
            data = self._get_json_url(self.live_url)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def get_config(self) -> dict:
        data = self._get_json_url(self.config_url)
        return data if isinstance(data, dict) else {}

    def get_fxdata(self) -> list[str]:
        try:
            data = self._get_json_url(self.fxdata_url)
            return [str(item) for item in data] if isinstance(data, list) else []
        except Exception:
            return []

    def get_networks(self) -> dict:
        data = self._get_json_url(self.networks_url)
        return data if isinstance(data, dict) else {}

    def get_presets(self) -> dict:
        """List of presets.
        Tries /json/presets first (some builds), then falls back to /presets.json .
        Many devices return 501 for /json/presets; /presets.json may work if accessible.
        Use 'ps'/'PL' to load, 'psave'/'PS' to save.
        """
        for url in (self.presets_url, self.presets_json_url):
            try:
                data = self._get_json_url(url)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def list_presets(self) -> list[dict]:
        """Saved on-device presets as sorted [{id, name, is_playlist}]."""
        return list_presets(self.get_presets())

    def _snapshot_part(self, name: str, loader: Callable[[], dict | list]) -> dict | list:
        try:
            return loader()
        except Exception as exc:
            # Some endpoints (live, nodes, sometimes presets) return 501 on stock WLED
            # (see JSON API and HTTP API docs). Presets getter now falls back to /presets.json.
            if name in ("live", "presets", "nodes"):
                logger.debug("Could not read WLED %s snapshot: %s", name, exc)
            else:
                logger.warning("Could not read WLED %s snapshot: %s", name, exc)
            return {"error": str(exc)}

    def get_device_snapshot(self) -> dict:
        combined = self._snapshot_part("combined", self.get_json)
        if not isinstance(combined, dict):
            combined = {}
        presets = self._snapshot_part("presets", self.get_presets)
        state = combined.get("state") or self._snapshot_part("state", self.get_state)
        return {
            "state": state,
            "info": combined.get("info") or self._snapshot_part("info", self.get_info),
            "effects": combined.get("effects") or self._snapshot_part("effects", self.get_effects),
            "palettes": combined.get("palettes") or self._snapshot_part("palettes", self.get_palettes),
            "config": self._snapshot_part("config", self.get_config),
            "fxdata": self._snapshot_part("fxdata", self.get_fxdata),
            "networks": self._snapshot_part("networks", self.get_networks),
            "presets": presets,
            # current preset NAME is only recoverable by matching state.ps against presets
            "current_preset": current_preset(
                presets if isinstance(presets, dict) else {},
                state if isinstance(state, dict) else {},
            ),
            # live and nodes omitted (often return 501; realtime data uses E1.31/Art-Net per docs)
        }

    def _request_with_retry(
        self, request: urllib.request.Request, retries: int = 3
    ) -> urllib.request.addinfourl:
        last_exc: Exception | None = None
        _WLED_MIN_REQ_INTERVAL = 0.1
        with self._http_lock:
            elapsed = time.time() - self._last_req_time
            if elapsed < _WLED_MIN_REQ_INTERVAL:
                time.sleep(_WLED_MIN_REQ_INTERVAL - elapsed)
            for attempt in range(retries):
                try:
                    res = urllib.request.urlopen(request, timeout=self.timeout)
                    self._last_req_time = time.time()
                    return res
                except urllib.error.HTTPError as exc:
                    # Close the error response body so its socket fd is released
                    # (HTTPError is also a response object).
                    exc.close()
                    if exc.code in (501, 404, 405):
                        raise
                    last_exc = exc
                    if attempt < retries - 1:
                        time.sleep(0.1 * (2 ** attempt))
                except OSError as exc:
                    # Covers urllib.error.URLError (connect/DNS failures) as well as
                    # raw socket errors - ConnectionResetError, TimeoutError,
                    # http.client.RemoteDisconnected - that WLED's ESP-based HTTP
                    # server can raise mid-response after the connection is already
                    # open (e.g. rebooting after a restart command). urlopen() only
                    # wraps failures during connect/send into URLError, so these
                    # otherwise bypass retry entirely.
                    last_exc = exc
                    if attempt < retries - 1:
                        time.sleep(0.1 * (2 ** attempt))
            # Retries exhausted: wrap in RuntimeError so callers (CLI/GUI/MCP restart
            # handlers, main()'s top-level catch) can rely on one exception type for
            # "controller unreachable" instead of a raw socket/urllib exception.
        raise RuntimeError(f"Could not reach light controller at {request.full_url}: {last_exc}") from last_exc

    def get_state(self) -> dict:
        request = urllib.request.Request(self.state_url, method="GET")
        with self._request_with_retry(request) as response:
            return self._decode_response_json(response, self.state_url)

    def post_state(self, payload: WledPayload) -> None:
        validate_wled_payload(payload)
        body = json.dumps(payload).encode("utf-8")
        if self.dry_run:
            logger.info("dry-run: %s", body.decode("utf-8"))
            return

        with self._http_lock:
            request = urllib.request.Request(
                self.state_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self._request_with_retry(request) as response:
                response.read()


# ---------------------------------------------------------------------------
# Audio-reactive mode
# ---------------------------------------------------------------------------

class ReactiveMode:
    def __init__(
        self,
        client: LightClient,
        palette: Sequence[tuple[int, int, int, int]] | None = None,
        min_interval: float = 0.12,
    ) -> None:
        self.client = client
        self.palette = palette or (
            (255, 0, 0, 0),
            (255, 90, 0, 0),
            (255, 0, 180, 0),
            (0, 80, 255, 0),
            (0, 255, 120, 0),
            (255, 120, 0, 180),
        )
        self.min_interval = min_interval
        self.effects = tuple(SAFE_EFFECTS)
        self.color_index = 0
        self.last_sent_at = 0.0

    def handle_beat(self, energy: float) -> None:
        now = time.monotonic()
        if now - self.last_sent_at < self.min_interval:
            return

        color = self.palette[self.color_index % len(self.palette)]
        effect = self.effects[self.color_index % len(self.effects)]
        self.color_index += 1
        brightness = clamp_byte(max(80, min(255, energy * 255)))
        speed = clamp_byte(round(80 + (energy * 110)))
        payload = reactive_beat_payload(color, brightness, effect, speed)
        self.client.post_state(payload)
        self.last_sent_at = now


class BeatDetector:
    def __init__(self, threshold: float = 1.55, floor: float = 0.015) -> None:
        self.threshold = threshold
        self.floor = floor
        self.baseline = 0.03
        self.last_beat_at = 0.0

    def update(self, rms: float) -> bool:
        rms = max(0.0, float(rms))
        self.baseline = (self.baseline * 0.92) + (rms * 0.08)
        now = time.monotonic()
        is_loud_enough = rms >= self.floor
        is_spike = rms > self.baseline * self.threshold
        is_spaced = now - self.last_beat_at > 0.16
        if is_loud_enough and is_spike and is_spaced:
            self.last_beat_at = now
            return True
        return False


class ReactiveThread:
    """Thread-safe wrapper to start/stop audio-reactive mode."""

    def __init__(
        self,
        client: LightClient,
        device: str | int | None = None,
        samplerate: int | None = None,
        level_callback: Callable[[float, bool], None] | None = None,
        error_callback: Callable[[BaseException], None] | None = None,
        reactive_mode: ReactiveMode | None = None,
    ) -> None:
        self.client = client
        self.device = device
        self.samplerate = samplerate
        self.level_callback = level_callback
        self.error_callback = error_callback
        self.reactive_mode = reactive_mode
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Audio-reactive mode is already running."
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
        )
        self._thread.start()
        return "Audio-reactive mode started."

    def stop(self) -> str:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        return "Audio-reactive mode stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            run_mode1(
                client=self.client,
                device=self.device,
                samplerate=self.samplerate,
                stop_event=self._stop,
                level_callback=self.level_callback,
                reactive_mode=self.reactive_mode,
            )
        except BaseException as exc:
            if self.error_callback is not None:
                self.error_callback(exc)
            else:
                logger.exception("Audio-reactive mode failed")


def run_mode1(
    client: LightClient,
    device: str | int | None = None,
    samplerate: int | None = None,
    stop_event: threading.Event | None = None,
    level_callback: Callable[[float, bool], None] | None = None,
    reactive_mode: ReactiveMode | None = None,
) -> None:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise SystemExit(
            "Mode 1 needs audio dependencies. Install them with: "
            "python3 -m pip install -r requirements.txt"
        ) from exc

    detector = BeatDetector()
    mode = reactive_mode if reactive_mode is not None else ReactiveMode(client)
    blocksize = 1024

    logger.info("Mode 1 listening on the default microphone. Press Ctrl+C to stop.")
    if device is None:
        device = get_mic_device()
    if isinstance(device, str) and device.isdigit():
        device = int(device)
    samplerate = resolve_input_samplerate(sd, device, samplerate)

    try:
        with sd.InputStream(
            device=device, channels=1, samplerate=samplerate, blocksize=blocksize
        ) as stream:
            while stop_event is None or not stop_event.is_set():
                try:
                    samples, overflowed = stream.read(blocksize)
                    if overflowed:
                        continue
                    rms = float(np.sqrt(np.mean(np.square(samples))))
                    energy = min(1.0, rms * 12.0)
                    beat = detector.update(rms)
                    if level_callback is not None:
                        level_callback(energy, beat)
                    if beat:
                        mode.handle_beat(energy)
                except Exception:
                    logger.exception("Error in audio processing loop")
                    time.sleep(0.1)
    except Exception:
        logger.exception("Fatal error opening audio stream")
        raise


def resolve_input_samplerate(
    sounddevice_module: object,
    device: str | int | None,
    samplerate: int | None,
) -> int:
    if samplerate is not None:
        return int(samplerate)
    device_info = sounddevice_module.query_devices(device=device, kind="input")
    return int(device_info.get("default_samplerate", 44100))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_rgbw(values: Iterable[str]) -> tuple[int, int, int, int]:
    parsed = [int(value) for value in values]
    if len(parsed) not in (3, 4):
        raise argparse.ArgumentTypeError("color needs R G B or R G B W")
    if len(parsed) == 3:
        parsed.append(0)
    return (parsed[0], parsed[1], parsed[2], parsed[3])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control the bedroom Wi-Fi LED controller.")
    parser.add_argument(
        "--host",
        default=None,
        help="Force single-controller mode with this host (default: fleet mode over the configured controllers)",
    )
    parser.add_argument(
        "--target",
        default="all",
        help="Fleet target: 'all', a controller name, or a channel name (default: all)",
    )
    parser.add_argument(
        "--segment",
        type=int,
        default=None,
        help="WLED segment id to address within the target",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print JSON instead of sending it")
    parser.add_argument(
        "--transition", type=int, default=0, help="Transition time in milliseconds (0-65535)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("on", help="Turn lights on")
    subparsers.add_parser("off", help="Turn lights off")

    bri = subparsers.add_parser("bri", help="Set brightness, 0-255")
    bri.add_argument("value", type=int)

    color = subparsers.add_parser("color", help="Set RGBW color")
    color.add_argument("values", nargs="+", help="R G B or R G B W")

    fx = subparsers.add_parser("fx", help="Set a safe non-strobe built-in effect")
    fx.add_argument("effect", type=int, choices=tuple(SAFE_EFFECTS))
    fx.add_argument("--speed", "-s", type=int, default=128)

    scene = subparsers.add_parser("scene", help="Set a named scene")
    scene.add_argument("name")

    mode1 = subparsers.add_parser("mode1", help="Audio-reactive mode using the default microphone")
    mode1.add_argument("--device", help="Optional sounddevice input device name or index")
    mode1.add_argument("--samplerate", type=int, default=None)

    temp = subparsers.add_parser("temp", help="Set color temperature in Kelvin (2000-6500)")
    temp.add_argument("kelvin", type=int)

    save_scene = subparsers.add_parser("save-scene", help="Save current state as a named scene")
    save_scene.add_argument("name")

    delete_scene = subparsers.add_parser("delete-scene", help="Delete a saved scene")
    delete_scene.add_argument("name")

    schedule_parser = subparsers.add_parser("schedule", help="Manage scheduled lighting changes")
    schedule_sub = schedule_parser.add_subparsers(dest="schedule_action", required=True)

    sched_add = schedule_sub.add_parser("add", help="Add a schedule entry")
    sched_add.add_argument("time", help="Time in HH:MM format")
    sched_add.add_argument("action", choices=("on", "off", "scene"), help="Action to perform")
    sched_add.add_argument("--scene-name", help="Scene name (required if action=scene)")

    schedule_sub.add_parser("list", help="List schedule entries")
    sched_remove = schedule_sub.add_parser("remove", help="Remove a schedule entry by index")
    sched_remove.add_argument("index", type=int)

    hex_cmd = subparsers.add_parser("hex", help="Set color from hex #RRGGBB or #RRGGBBWW")
    hex_cmd.add_argument("color", help="Hex color string")

    subparsers.add_parser("random", help="Set a random built-in scene")

    fade = subparsers.add_parser("fade-off", help="Gradually fade to off over N minutes")
    fade.add_argument("minutes", type=float, help="Duration in minutes")
    fade.add_argument("--brightness", type=int, help="Starting brightness (defaults to current)")

    preset = subparsers.add_parser("preset", help="Load a WLED preset (1-250)")
    preset.add_argument("id", type=int, help="Preset ID")

    cycle = subparsers.add_parser("cycle", help="Auto-rotate through scenes")
    cycle.add_argument("--items", nargs="+", help="Scene names to cycle (default: built-in scenes)")
    cycle.add_argument("--interval", type=float, default=60, help="Seconds between changes")
    cycle.add_argument("--mode", choices=("scene", "preset"), default="scene", help="Cycle mode")

    sunrise = subparsers.add_parser("sunrise", help="Gradual wake-up light simulation")
    sunrise.add_argument("--minutes", type=float, default=30, help="Duration in minutes")
    sunrise.add_argument("--start-kelvin", type=int, default=2000, help="Starting color temp")
    sunrise.add_argument("--end-kelvin", type=int, default=5000, help="Ending color temp")
    sunrise.add_argument("--brightness", type=int, default=255, help="Max brightness at end")

    subparsers.add_parser("info", help="Read WLED controller info")

    subparsers.add_parser("restart", help="Reboot the WLED controller")

    subparsers.add_parser("segments", help="List fleet controllers, channels, and segments")

    wall = subparsers.add_parser("wall", help="Wall-wide effect composers across all four columns")
    wall.add_argument("mode", choices=("span", "mirror", "chase", "versus"))
    wall.add_argument("--fx", type=int, default=9, help="Effect id (versus default for both sides)")
    wall.add_argument("--pal", type=int, default=None, help="Palette id")
    wall.add_argument("--fx-left", type=int, default=None, help="Versus: effect id for the left pair")
    wall.add_argument("--fx-right", type=int, default=None, help="Versus: effect id for the right pair")
    wall.add_argument("--pal-left", type=int, default=None, help="Versus: palette for the left pair")
    wall.add_argument("--pal-right", type=int, default=None, help="Versus: palette for the right pair")

    atmosphere = subparsers.add_parser("atmosphere", help="Apply a named curated atmosphere")
    atmosphere.add_argument("name", help="Atmosphere name (see list)")

    return parser


class _FleetRouter:
    """Duck-typed LightClient shim: routes posts/reads through a LightFleet target."""

    def __init__(self, fleet: object, target: str = "all") -> None:
        self.fleet = fleet
        self.target = target

    def post_state(self, payload: WledPayload) -> None:
        validate_wled_payload(payload)
        self.fleet.post_state(payload, target=self.target)  # type: ignore[attr-defined]

    def get_state(self) -> dict:
        states = self.fleet.get_state(target=self.target)  # type: ignore[attr-defined]
        for entry in states.values():
            if isinstance(entry, dict):
                state = entry.get("state", entry)
                if isinstance(state, dict):
                    return state
        return {}


def _with_seg_id(payload: WledPayload, seg_id: int | None) -> WledPayload:
    """Stamp a segment id onto every id-less seg entry (for --segment)."""
    if seg_id is None:
        return payload
    segments = payload.get("seg")
    if segments:
        for segment in segments:
            segment.setdefault("id", seg_id)
    return payload


def _log_wled_info(info: dict) -> None:
    logger.info("Name: %s", info.get("name", "?"))
    logger.info("Version: %s", info.get("ver", "?"))
    logger.info("LEDs: %d", info.get("leds", {}).get("count", 0))
    logger.info("Uptime: %ds", info.get("uptime", 0))
    logger.info("Free heap: %d", info.get("freeheap", 0))
    logger.info("IP: %s", info.get("ip", "?"))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    transition_ms = getattr(args, "transition", 0)
    seg_id = getattr(args, "segment", None)

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    fleet_mod = None
    fleet_obj = None
    if args.host is not None:
        # --host forces single-controller mode (back-compat escape hatch)
        client: LightClient | _FleetRouter = LightClient(args.host, dry_run=args.dry_run)
    else:
        import fleet as fleet_mod  # lazy: fleet.py imports lightctl

        fleet_obj = fleet_mod.LightFleet.from_config(dry_run=args.dry_run)
        client = _FleetRouter(fleet_obj, target=args.target)

    try:
        if args.command == "on":
            client.post_state(on_payload(True, transition_ms=transition_ms))
        elif args.command == "off":
            client.post_state(on_payload(False, transition_ms=transition_ms))
        elif args.command == "bri":
            client.post_state(brightness_payload(args.value, transition_ms=transition_ms))
        elif args.command == "color":
            client.post_state(color_payload(*parse_rgbw(args.values), transition_ms=transition_ms, seg_id=seg_id))
        elif args.command == "fx":
            client.post_state(effect_payload(args.effect, args.speed, transition_ms=transition_ms, seg_id=seg_id))
        elif args.command == "scene":
            client.post_state(_with_seg_id(scene_payload(args.name, transition_ms=transition_ms), seg_id))
        elif args.command == "mode1":
            run_mode1(client, device=args.device, samplerate=args.samplerate)
        elif args.command == "temp":
            client.post_state(color_payload(*kelvin_to_rgbw(args.kelvin), transition_ms=transition_ms, seg_id=seg_id))
        elif args.command == "save-scene":
            state = client.get_state()
            payload: WledPayload = {}
            for key in ("on", "bri", "seg", "transition"):
                if key in state:
                    payload[key] = state[key]  # type: ignore[literal-required]
            save_scene(args.name, payload)
            logger.info("Saved scene '%s'.", args.name)
        elif args.command == "delete-scene":
            delete_scene(args.name)
            logger.info("Deleted scene '%s'.", args.name)
        elif args.command == "schedule":
            if args.schedule_action == "add":
                data: dict = {}
                if args.action == "scene":
                    if not args.scene_name:
                        raise ValueError("--scene-name is required when action=scene")
                    data["scene"] = args.scene_name
                add_schedule(args.time, args.action, data)
                logger.info("Added schedule for %s.", args.time)
            elif args.schedule_action == "list":
                entries = list_schedule()
                if not entries:
                    logger.info("No scheduled entries.")
                else:
                    for i, entry in enumerate(entries):
                        logger.info("%d: %s -> %s %s", i, entry["time"], entry["action"], entry.get("data", ""))
            elif args.schedule_action == "remove":
                if remove_schedule(args.index):
                    logger.info("Removed schedule entry %d.", args.index)
                else:
                    raise ValueError(f"No schedule entry at index {args.index}.")
        elif args.command == "hex":
            client.post_state(color_payload(*hex_to_rgbw(args.color), transition_ms=transition_ms, seg_id=seg_id))
        elif args.command == "random":
            client.post_state(_with_seg_id(random_scene_payload(transition_ms=transition_ms), seg_id))
        elif args.command == "fade-off":
            timer = FadeTimer(client, args.minutes, start_brightness=args.brightness)
            timer.start()
            logger.info("Fading to off over %.1f minutes. Press Ctrl+C to stop.", args.minutes)
            try:
                while timer.is_alive():
                    time.sleep(1)
            except KeyboardInterrupt:
                timer.stop()
                logger.info("Fade timer cancelled.")
                return 130
        elif args.command == "preset":
            client.post_state(preset_payload(args.id, transition_ms=transition_ms))
        elif args.command == "cycle":
            cycler = CycleThread(
                client,
                items=args.items,
                interval_seconds=args.interval,
                mode=args.mode,
            )
            cycler.start()
            logger.info("Cycling scenes. Press Ctrl+C to stop.")
            try:
                while cycler.is_alive():
                    time.sleep(1)
            except KeyboardInterrupt:
                cycler.stop()
                logger.info("Cycle stopped.")
                return 130
        elif args.command == "sunrise":
            sim = SunriseSimulator(
                client,
                duration_minutes=args.minutes,
                start_kelvin=args.start_kelvin,
                end_kelvin=args.end_kelvin,
                max_brightness=args.brightness,
            )
            sim.start()
            logger.info("Sunrise simulation for %.1f minutes. Press Ctrl+C to stop.", args.minutes)
            try:
                while sim.is_alive():
                    time.sleep(1)
            except KeyboardInterrupt:
                sim.stop()
                logger.info("Sunrise cancelled.")
                return 130
        elif args.command == "info":
            if fleet_obj is not None:
                for controller in fleet_mod.load_controllers():
                    if args.target not in ("all", controller.name):
                        continue
                    logger.info("[%s]", controller.name)
                    _log_wled_info(LightClient(controller.host, dry_run=args.dry_run).get_info())
            else:
                _log_wled_info(client.get_info())  # type: ignore[attr-defined]
        elif args.command == "restart":
            try:
                client.post_state(restart_payload())
            except RuntimeError:
                pass  # Device may reboot before completing the HTTP response
            logger.info("Restart command sent. Device will reconnect in a few seconds.")
        elif args.command == "segments":
            if fleet_obj is None:
                raise ValueError("'segments' requires fleet mode (omit --host).")
            for name in fleet_obj.names():
                logger.info("controller: %s", name)
            channels = fleet_obj.channels()
            ordered = [c for c in fleet_mod.WALL_ORDER if c in channels]
            ordered += sorted(c for c in channels if c not in fleet_mod.WALL_ORDER)
            for channel in ordered:
                controller_name, segment_id = channels[channel]
                logger.info("channel: %s -> %s segment %d", channel, controller_name, segment_id)
        elif args.command == "wall":
            if fleet_obj is None:
                raise ValueError("'wall' requires fleet mode (omit --host).")
            import columns  # lazy: columns.py builds on fleet + lightctl

            if args.mode == "span":
                result = columns.wall_span(fleet_obj, args.fx, pal=args.pal)
            elif args.mode == "mirror":
                result = columns.mirror(fleet_obj, args.fx, pal=args.pal)
            elif args.mode == "chase":
                result = columns.chase(fleet_obj, args.fx, pal=args.pal)
            else:
                result = columns.left_vs_right(
                    fleet_obj,
                    args.fx_left if args.fx_left is not None else args.fx,
                    args.fx_right if args.fx_right is not None else args.fx,
                    pal_left=args.pal_left if args.pal_left is not None else args.pal,
                    pal_right=args.pal_right if args.pal_right is not None else args.pal,
                )
            logger.info("wall %s: %s", args.mode, json.dumps(result, default=str))
        elif args.command == "atmosphere":
            if fleet_obj is None:
                raise ValueError("'atmosphere' requires fleet mode (omit --host).")
            import atmospheres  # lazy: atmospheres builds on columns

            if args.name == "list":
                logger.info("atmospheres:\n%s", atmospheres.atmosphere_menu_text())
            else:
                result = atmospheres.apply_atmosphere(fleet_obj, args.name)
                logger.info("atmosphere %s: %s", args.name, json.dumps(result, default=str))
    except KeyboardInterrupt:
        logger.info("Stopped.")
        return 130
    except (RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
