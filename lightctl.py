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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, TypedDict
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("lightsctl")

RIGHT_HOST = "http://10.27.27.110"
LEFT_HOST = "http://10.27.27.112"
DEFAULT_HOST = RIGHT_HOST

WALL_ORDER = ("far-left", "middle-left", "middle-right", "far-right")
CHANNEL_TOPOLOGY: dict[str, dict[str, Any]] = {
    "far-left": {
        "controller": "left",
        "host": LEFT_HOST,
        "segment": 0,
        "length": 43,
        "gpio": 2,
    },
    "middle-left": {
        "controller": "left",
        "host": LEFT_HOST,
        "segment": 1,
        "length": 50,
        "gpio": 16,
    },
    "middle-right": {
        "controller": "right",
        "host": RIGHT_HOST,
        "segment": 0,
        "length": 50,
        "gpio": 2,
    },
    "far-right": {
        "controller": "right",
        "host": RIGHT_HOST,
        "segment": 1,
        "length": 36,
        "gpio": 16,
    },
}
CHANNEL_LENGTHS = {name: int(spec["length"]) for name, spec in CHANNEL_TOPOLOGY.items()}
JSON_PATH = "/json"
STATE_PATH = "/json/state"
EFFECTS_PATH = (
    "/json/eff"  # individual effects list (main /json also returns "effects")
)
PALETTES_PATH = (
    "/json/pal"  # individual palettes list (main /json also returns "palettes")
)
NODES_PATH = "/json/nodes"
LIVE_PATH = "/json/live"  # optional; many builds return 501. Use E1.31/Art-Net/DDP for realtime per https://kno.wled.ge/interfaces/e1.31-dmx/
CONFIG_PATH = "/json/cfg"
FXDATA_PATH = "/json/fxdata"  # effect metadata (v0.14+)
NETWORKS_PATH = "/json/net"
PRESETS_PATH = "/json/presets"  # optional; not present on all versions
PRESETS_JSON_PATH = (
    "/presets.json"  # common way to get full preset list (may require no password)
)
SAFE_EFFECTS = {
    0: "Solid",
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

# Stable built-in WLED IDs for blink/strobe/flash/lightning/fireworks/sparkle
# families. Live catalogs receive an additional name-based safety filter.
BLOCKED_EFFECTS: set[int] = {1, 20, 21, 22, 23, 24, 25, 26, 31, 32, 42, 57}
FORBIDDEN_EFFECT_TERMS = (
    "blink",
    "strobe",
    "flash",
    "lightning",
    "fireworks",
    "sparkle",
)


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
    o1: bool
    o2: bool
    o3: bool
    on: bool
    frz: bool
    rev: bool
    rY: bool
    mi: bool
    mY: bool
    tp: bool
    sel: bool
    bri: int
    grp: int
    spc: int
    of: int
    cct: int
    m12: int
    si: int
    fxdef: bool
    set: int
    rpt: bool
    i: list[Any]


class WledPayload(TypedDict, total=False):
    on: bool
    bri: int
    seg: list[SegPayload]
    transition: int
    tt: int
    ps: int
    psave: int
    pdel: int
    pl: int
    playlist: dict[str, Any]
    nl: dict[str, Any]
    udpn: dict[str, Any]
    AudioReactive: dict[str, Any]
    v: bool
    rb: bool
    live: bool
    lor: int
    mainseg: int
    time: int
    tb: int
    ledmap: int
    rmcpal: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: int | float, *, name: str = "value") -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, not a boolean.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}.") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return int(numeric)


def clamp_int(
    value: int | float, minimum: int, maximum: int, *, name: str = "value"
) -> int:
    number = _coerce_int(value, name=name)
    return max(minimum, min(maximum, number))


def clamp_byte(value: int | float) -> int:
    return clamp_int(value, 0, 255)


def require_int_range(
    value: int | float, minimum: int, maximum: int, *, name: str
) -> int:
    number = _coerce_int(value, name=name)
    if not minimum <= number <= maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}, got {number}."
        )
    return number


def _transition_units(transition_ms: int | float) -> int:
    """Convert milliseconds to WLED 100 ms transition units.

    WLED accepts 0..65535 units. Payload builders use ``tt`` so the requested
    transition applies only to the current API call and does not overwrite the
    controller's configured default transition.
    """
    milliseconds = _coerce_int(transition_ms, name="transition_ms")
    if milliseconds < 0:
        raise ValueError("transition_ms cannot be negative.")
    return min(65535, round(milliseconds / 100))


def _apply_transition(payload: WledPayload, transition_ms: int | float) -> WledPayload:
    if transition_ms:
        payload["tt"] = _transition_units(transition_ms)
    return payload


def normalize_host(host: str) -> str:
    raw = str(host).strip()
    if not raw:
        raise ValueError("WLED host cannot be empty.")
    if "://" not in raw:
        raw = f"http://{raw}"
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported WLED URL scheme '{parts.scheme}'.")
    if not parts.netloc:
        raise ValueError(f"Invalid WLED host '{host}'.")
    if parts.query or parts.fragment:
        raise ValueError("WLED host must not contain a query string or fragment.")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def effect_name_is_safe(name: str) -> bool:
    normalized = " ".join(str(name).casefold().replace("_", " ").split())
    return not any(term in normalized for term in FORBIDDEN_EFFECT_TERMS)


def safe_effect_ids(effect_names: Sequence[str]) -> set[int]:
    """Return live effect IDs that pass the project seizure-safety filter."""
    return {
        index
        for index, name in enumerate(effect_names)
        if index not in BLOCKED_EFFECTS and effect_name_is_safe(name)
    }


def parse_fxdata_entry(raw: str) -> dict[str, Any]:
    """Parse one WLED ``/json/fxdata`` metadata string into a stable structure."""
    sections = str(raw).split(";", 4)
    sections.extend([""] * (5 - len(sections)))
    params_raw, colors_raw, palette_raw, flags_raw, defaults_raw = sections

    parameter_keys = ("sx", "ix", "c1", "c2", "c3", "o1", "o2", "o3")
    default_parameter_labels = {
        "sx": "Effect speed",
        "ix": "Effect intensity",
        "c1": "Custom 1",
        "c2": "Custom 2",
        "c3": "Custom 3",
        "o1": "Option 1",
        "o2": "Option 2",
        "o3": "Option 3",
    }
    parameters: list[dict[str, Any]] = []
    for index, label in enumerate(params_raw.split(",")):
        if index >= len(parameter_keys) or label == "":
            continue
        key = parameter_keys[index]
        resolved_label = default_parameter_labels[key] if label == "!" else label
        parameters.append(
            {
                "key": key,
                "label": resolved_label,
                "type": "checkbox" if key.startswith("o") else "slider",
                "range": (
                    [0, 31]
                    if key == "c3"
                    else ([False, True] if key.startswith("o") else [0, 255])
                ),
            }
        )

    default_color_labels = ("Fx", "Bg", "Cs")
    colors: list[dict[str, Any]] = []
    for index, label in enumerate(colors_raw.split(",")):
        if index >= 3 or label == "":
            continue
        colors.append(
            {
                "slot": index + 1,
                "label": default_color_labels[index] if label == "!" else label,
            }
        )

    defaults: dict[str, Any] = {}
    for assignment in defaults_raw.split(","):
        if "=" not in assignment:
            continue
        key, value = assignment.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        try:
            defaults[key] = int(value)
        except ValueError:
            defaults[key] = value

    flags = flags_raw.strip()
    return {
        "raw": str(raw),
        "parameters": parameters,
        "colors": colors,
        "uses_palette": bool(palette_raw.strip()),
        "flags": flags,
        "supports_1d": "1" in flags or ("2" not in flags and "3" not in flags),
        "requires_2d": "2" in flags and "1" not in flags,
        "audio_volume": "v" in flags,
        "audio_frequency": "f" in flags,
        "defaults": defaults,
    }


def _effect_mood_group(name: str, metadata: Mapping[str, Any]) -> str:
    text = str(name).casefold()
    if (
        metadata.get("audio_volume")
        or metadata.get("audio_frequency")
        or any(
            term in text
            for term in (
                "audio",
                "frequency",
                "grav",
                "juggles",
                "midnoise",
                "noisemeter",
                "pixelwave",
            )
        )
    ):
        return "audio-reactive"
    if any(term in text for term in ("fire", "flame", "candle", "lava")):
        return "fire-and-warmth"
    if any(
        term in text
        for term in ("ocean", "lake", "pacifica", "water", "wave", "ripple", "rain")
    ):
        return "water-and-waves"
    if any(
        term in text
        for term in (
            "chase",
            "runner",
            "running",
            "meteor",
            "scan",
            "sweep",
            "flow",
            "comet",
        )
    ):
        return "motion-and-chases"
    if any(
        term in text
        for term in ("rainbow", "colorloop", "colorwaves", "palette", "pride", "party")
    ):
        return "color-and-rainbow"
    if any(
        term in text
        for term in (
            "breathe",
            "fade",
            "drift",
            "aurora",
            "sine",
            "oscillate",
            "glitter",
        )
    ):
        return "ambient-and-slow"
    return "other"


def build_effect_catalog(
    effect_names: Sequence[str], fxdata: Sequence[str] | None = None
) -> dict[str, Any]:
    """Build a safety-labelled, metadata-rich effect catalog for AI consumers."""
    metadata_rows = list(fxdata or ())
    effects: list[dict[str, Any]] = []
    groups: dict[str, list[int]] = {}
    for effect_id, name in enumerate(effect_names):
        metadata = (
            parse_fxdata_entry(metadata_rows[effect_id])
            if effect_id < len(metadata_rows)
            else {
                "raw": "",
                "parameters": [],
                "colors": [],
                "uses_palette": True,
                "flags": "1",
                "supports_1d": True,
                "requires_2d": False,
                "audio_volume": False,
                "audio_frequency": False,
                "defaults": {},
            }
        )
        safe = effect_id not in BLOCKED_EFFECTS and effect_name_is_safe(name)
        group = "forbidden" if not safe else _effect_mood_group(name, metadata)
        entry = {
            "id": effect_id,
            "name": str(name),
            "safe": safe,
            "group": group,
            "metadata": metadata,
        }
        effects.append(entry)
        groups.setdefault(group, []).append(effect_id)
    return {"effects": effects, "groups": groups}


def _color_slot(red: int, green: int, blue: int, white: int = 0) -> list[int]:
    return [clamp_byte(red), clamp_byte(green), clamp_byte(blue), clamp_byte(white)]


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def on_payload(
    enabled: bool, transition_ms: int = 0, seg_id: int | None = None
) -> WledPayload:
    if seg_id is None:
        payload: WledPayload = {"on": bool(enabled)}
    else:
        payload = {
            "seg": [
                {
                    "id": require_int_range(seg_id, 0, 255, name="seg_id"),
                    "on": bool(enabled),
                }
            ]
        }
    return _apply_transition(payload, transition_ms)


def brightness_payload(
    brightness: int,
    transition_ms: int = 0,
    seg_id: int | None = None,
) -> WledPayload:
    value = clamp_byte(brightness)
    if seg_id is None:
        payload: WledPayload = {"bri": value}
    else:
        payload = {
            "seg": [
                {"id": require_int_range(seg_id, 0, 255, name="seg_id"), "bri": value}
            ]
        }
    return _apply_transition(payload, transition_ms)


def color_payload(
    red: int,
    green: int,
    blue: int,
    white: int = 0,
    red2: int | None = None,
    green2: int | None = None,
    blue2: int | None = None,
    white2: int | None = None,
    red3: int | None = None,
    green3: int | None = None,
    blue3: int | None = None,
    white3: int | None = None,
    transition_ms: int = 0,
    seg_id: int | None = None,
) -> WledPayload:
    colors = [_color_slot(red, green, blue, white)]
    secondary_requested = any(
        value is not None for value in (red2, green2, blue2, white2)
    )
    tertiary_requested = any(
        value is not None for value in (red3, green3, blue3, white3)
    )
    if secondary_requested or tertiary_requested:
        colors.append(_color_slot(red2 or 0, green2 or 0, blue2 or 0, white2 or 0))
    if tertiary_requested:
        colors.append(_color_slot(red3 or 0, green3 or 0, blue3 or 0, white3 or 0))
    seg: SegPayload = {"col": colors}
    if seg_id is not None:
        seg["id"] = require_int_range(seg_id, 0, 255, name="seg_id")
    payload: WledPayload = {"seg": [seg]}
    return _apply_transition(payload, transition_ms)


def validate_effect(
    fx: int,
    allowed: set[int] | None = None,
    effect_names: Sequence[str] | None = None,
) -> int:
    """Validate an effect ID against a live safe set or the offline allowlist."""
    effect = require_int_range(fx, 0, 255, name="effect")
    if effect in BLOCKED_EFFECTS:
        raise ValueError(f"Effect {effect} is blocked.")
    if effect_names is not None:
        if effect >= len(effect_names):
            raise ValueError(f"Effect {effect} is not available on the target device.")
        if not effect_name_is_safe(effect_names[effect]):
            raise ValueError(
                f"Effect {effect} ({effect_names[effect]}) is blocked by the safety policy."
            )
    if allowed is not None:
        if effect not in allowed:
            raise ValueError(
                f"Effect {effect} is not in the validated safe effect set."
            )
        return effect
    if effect_names is not None:
        return effect
    if effect not in SAFE_EFFECTS:
        allowed_str = ", ".join(
            f"{effect_id}={name}" for effect_id, name in SAFE_EFFECTS.items()
        )
        raise ValueError(
            f"Effect {effect} is not allowed offline. Safe effects: {allowed_str}."
        )
    return effect


def effect_payload(
    effect: int,
    speed: int = 128,
    transition_ms: int = 0,
    intensity: int | None = None,
    palette: int | None = None,
    c1: int | None = None,
    c2: int | None = None,
    c3: int | None = None,
    o1: bool | int | None = None,
    o2: bool | int | None = None,
    o3: bool | int | None = None,
    seg_id: int | None = None,
    allowed_effects: set[int] | None = None,
    effect_names: Sequence[str] | None = None,
    palette_count: int | None = None,
) -> WledPayload:
    effect_id = validate_effect(
        effect, allowed=allowed_effects, effect_names=effect_names
    )
    seg: SegPayload = {"fx": effect_id, "sx": clamp_byte(speed)}
    if seg_id is not None:
        seg["id"] = require_int_range(seg_id, 0, 255, name="seg_id")
    if intensity is not None:
        seg["ix"] = clamp_byte(intensity)
    if palette is not None:
        palette_id = require_int_range(palette, 0, 65535, name="palette")
        if palette_count is not None and palette_id >= palette_count:
            raise ValueError(
                f"Palette {palette_id} is not available; target exposes {palette_count} palettes."
            )
        seg["pal"] = palette_id
    if c1 is not None:
        seg["c1"] = clamp_byte(c1)
    if c2 is not None:
        seg["c2"] = clamp_byte(c2)
    if c3 is not None:
        seg["c3"] = clamp_int(c3, 0, 31, name="c3")
    for key, value in (("o1", o1), ("o2", o2), ("o3", o3)):
        if value is not None:
            seg[key] = bool(value)  # type: ignore[literal-required]
    payload: WledPayload = {"seg": [seg]}
    return _apply_transition(payload, transition_ms)


def palette_payload(
    palette: int,
    seg_id: int | None = None,
    transition_ms: int = 0,
    palette_count: int | None = None,
) -> WledPayload:
    palette_id = require_int_range(palette, 0, 65535, name="palette")
    if palette_count is not None and palette_id >= palette_count:
        raise ValueError(
            f"Palette {palette_id} is not available; target exposes {palette_count} palettes."
        )
    seg: SegPayload = {"pal": palette_id}
    if seg_id is not None:
        seg["id"] = require_int_range(seg_id, 0, 255, name="seg_id")
    return _apply_transition({"seg": [seg]}, transition_ms)


def normalize_cct(cct: int) -> int:
    value = _coerce_int(cct, name="cct")
    if 0 <= value <= 255 or 1900 <= value <= 10091:
        return value
    raise ValueError("cct must be a relative value 0-255 or Kelvin 1900-10091.")


def cct_payload(
    cct: int,
    seg_id: int | None = None,
    transition_ms: int = 0,
) -> WledPayload:
    seg: SegPayload = {"cct": normalize_cct(cct)}
    if seg_id is not None:
        seg["id"] = require_int_range(seg_id, 0, 255, name="seg_id")
    return _apply_transition({"seg": [seg]}, transition_ms)


def segment_payload(segments: list[dict[str, Any]]) -> WledPayload:
    """Build a multi-segment payload from raw segment dictionaries."""
    if not segments:
        raise ValueError("segment_payload requires at least one segment.")
    payload: WledPayload = {"seg": [dict(segment) for segment in segments]}  # type: ignore[list-item]
    validate_wled_payload(payload)
    return payload


def _validate_byte_field(
    container: Mapping[str, Any], key: str, maximum: int = 255
) -> None:
    if key in container:
        require_int_range(container[key], 0, maximum, name=key)


def validate_wled_payload(
    payload: WledPayload | dict[str, Any],
) -> WledPayload | dict[str, Any]:
    """Validate project-level safety and WLED value ranges before sending JSON."""
    if not isinstance(payload, dict):
        raise ValueError("WLED payload must be a JSON object.")
    _validate_byte_field(payload, "bri")
    for key in ("transition", "tt"):
        if key in payload:
            require_int_range(payload[key], 0, 65535, name=key)
    if "lor" in payload:
        require_int_range(payload["lor"], 0, 2, name="lor")
    for key in ("psave", "pdel"):
        if key in payload:
            require_int_range(payload[key], 1, 250, name=key)
    for key in ("ps", "pl"):
        if key in payload:
            require_int_range(payload[key], -1, 250, name=key)

    segments = payload.get("seg")
    if segments is None:
        return payload
    if not isinstance(segments, list):
        raise ValueError("WLED payload field 'seg' must be a list.")
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("WLED segment payloads must be objects.")
        if "id" in segment:
            require_int_range(segment["id"], 0, 255, name="segment id")
        start = segment.get("start")
        stop = segment.get("stop")
        if start is not None:
            require_int_range(start, 0, 65535, name="segment start")
        if stop is not None:
            require_int_range(stop, 0, 65535, name="segment stop")
        if start is not None and stop is not None and int(stop) <= int(start):
            raise ValueError(
                f"Segment stop must exceed start; got start={start}, stop={stop}."
            )
        for key in ("sx", "ix", "c1", "c2", "bri", "grp", "spc", "of"):
            _validate_byte_field(segment, key)
        _validate_byte_field(segment, "c3", maximum=31)
        if "cct" in segment:
            normalize_cct(segment["cct"])
        if "pal" in segment:
            require_int_range(segment["pal"], 0, 65535, name="palette")
        if "fx" in segment:
            effect = require_int_range(segment["fx"], 0, 255, name="effect")
            if effect in BLOCKED_EFFECTS:
                raise ValueError(f"Effect {effect} is blocked.")
        if "col" in segment:
            colors = segment["col"]
            if not isinstance(colors, list) or not 1 <= len(colors) <= 3:
                raise ValueError(
                    "Segment 'col' must contain one to three color arrays."
                )
            for color in colors:
                if not isinstance(color, (list, tuple)) or len(color) not in (3, 4):
                    raise ValueError(
                        "Each WLED color must contain three or four channels."
                    )
                for channel in color:
                    require_int_range(channel, 0, 255, name="color channel")
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

# Physical model: each channel is a bottom-fed vertical bar. Pixel 0 is at the
# bottom and indices increase toward the roofline. Lengths differ by channel.
LEDS_PER_COLUMN = max(CHANNEL_LENGTHS.values())  # legacy compatibility only
COLUMN_LENGTH_M = 2.0

_ZONE_POSITIONS = ("top", "middle", "bottom")
_ZONE_FRACTIONS = {"half": 2, "third": 3, "quarter": 4}
_LED_ORIENTATIONS = ("up", "down")
_BYTE_SEG_FIELDS = ("sx", "ix", "c1", "c2", "bri")


def channel_length(channel: str) -> int:
    key = str(channel).strip().lower()
    try:
        return CHANNEL_LENGTHS[key]
    except KeyError as exc:
        raise ValueError(
            f"Unknown channel '{channel}'. Valid channels: {', '.join(WALL_ORDER)}."
        ) from exc


def _resolve_column_length(length: int | None, channel: str | None) -> int:
    if channel is not None:
        actual = channel_length(channel)
        if length is not None and int(length) != actual:
            raise ValueError(
                f"Channel '{channel}' has {actual} LEDs, but length={length} was supplied."
            )
        return actual
    if length is None:
        raise ValueError(
            "Column length is required when no channel is supplied; the four wall columns have different lengths."
        )
    return require_int_range(length, 1, 65535, name="column length")


def zone_bounds(
    zone: str,
    length: int | None = None,
    orientation: str = "up",
    *,
    channel: str | None = None,
) -> tuple[int, int]:
    """Return LED ``[start, stop)`` bounds for a physical zone.

    Supply either ``channel`` (preferred) or an explicit ``length``. For the
    installed wall, ``orientation='up'`` is correct because data enters each
    strip at the bottom.
    """
    resolved_length = _resolve_column_length(length, channel)
    orientation = str(orientation).strip().lower()
    if orientation not in _LED_ORIENTATIONS:
        raise ValueError(
            f"Unknown orientation '{orientation}'. Use one of: {', '.join(_LED_ORIENTATIONS)}."
        )
    key = " ".join(str(zone).strip().lower().split())
    if key == "all":
        return (0, resolved_length)
    parts = key.split(" ")
    if (
        len(parts) != 2
        or parts[0] not in _ZONE_POSITIONS
        or parts[1] not in _ZONE_FRACTIONS
    ):
        raise ValueError(
            f"Unknown zone '{zone}'. Use 'all' or '<top|middle|bottom> <half|third|quarter>'."
        )
    position, fraction = parts
    divisor = _ZONE_FRACTIONS[fraction]
    if position == "bottom":
        p0, p1 = 0.0, 1.0 / divisor
    elif position == "top":
        p0, p1 = 1.0 - 1.0 / divisor, 1.0
    else:
        p0, p1 = 0.5 - 0.5 / divisor, 0.5 + 0.5 / divisor
    start = round(resolved_length * p0)
    stop = round(resolved_length * p1)
    if orientation == "down":
        start, stop = resolved_length - stop, resolved_length - start
    if start >= stop:
        raise ValueError(
            f"Zone '{zone}' of length {resolved_length} resolves to an empty range "
            f"(start={start}, stop={stop})."
        )
    return (start, stop)


def _normalize_color(color: object) -> list[int]:
    """Normalize a color to an RGB or RGBW integer list."""
    if isinstance(color, str):
        hex_color = color.strip().lstrip("#")
        if len(hex_color) not in (6, 8):
            raise ValueError(f"Hex color must be RRGGBB or RRGGBBWW, got '{color}'.")
        try:
            return [
                int(hex_color[index : index + 2], 16)
                for index in range(0, len(hex_color), 2)
            ]
        except ValueError:
            raise ValueError(f"Invalid hex color '{color}'.") from None
    if isinstance(color, (list, tuple)) and len(color) in (3, 4):
        return [clamp_byte(channel) for channel in color]
    raise ValueError(f"Color must be RRGGBB, RRGGBBWW, RGB, or RGBW; got {color!r}.")


def zone_payload(
    zones: list[dict[str, Any]],
    length: int | None = None,
    orientation: str = "up",
    *,
    channel: str | None = None,
) -> WledPayload:
    """Build a multi-segment payload that carves one physical column into zones."""
    if not zones:
        raise ValueError("zone_payload requires at least one zone.")
    resolved_length = _resolve_column_length(length, channel)
    segments: list[SegPayload] = []
    for zone in zones:
        if not isinstance(zone, dict):
            raise ValueError(f"Each zone must be a dict, got {zone!r}.")
        entry = dict(zone)
        if "zone" in entry:
            start, stop = zone_bounds(
                str(entry.pop("zone")),
                length=resolved_length,
                orientation=orientation,
            )
            entry.pop("start", None)
            entry.pop("stop", None)
        else:
            if "start" not in entry or "stop" not in entry:
                raise ValueError(
                    "Each zone needs either a 'zone' name or explicit 'start' and 'stop'."
                )
            start, stop = int(entry.pop("start")), int(entry.pop("stop"))
            if not (0 <= start < stop <= resolved_length):
                raise ValueError(
                    f"Zone bounds must satisfy 0 <= start < stop <= {resolved_length}, "
                    f"got start={start}, stop={stop}."
                )
        seg_id = entry.pop("id", None)
        if "col" in entry:
            col = entry["col"]
            if isinstance(col, (str, tuple)) or (
                isinstance(col, list) and col and isinstance(col[0], int)
            ):
                entry["col"] = [_normalize_color(col)]
            elif isinstance(col, list):
                entry["col"] = [_normalize_color(item) for item in col]
        for field_name in _BYTE_SEG_FIELDS:
            if field_name in entry and entry[field_name] is not None:
                entry[field_name] = clamp_byte(entry[field_name])
        if "c3" in entry and entry["c3"] is not None:
            entry["c3"] = clamp_int(entry["c3"], 0, 31, name="c3")
        if "cct" in entry and entry["cct"] is not None:
            entry["cct"] = normalize_cct(entry["cct"])
        seg: SegPayload = {"start": start, "stop": stop}
        if seg_id is not None:
            seg["id"] = require_int_range(seg_id, 0, 255, name="segment id")
        seg.update(entry)  # type: ignore[typeddict-item]
        segments.append(seg)
    payload: WledPayload = {"seg": segments}
    validate_wled_payload(payload)
    return payload


def _color_token(color: object) -> str:
    """Normalize a color to RRGGBB or RRGGBBWW for WLED's ``i`` array."""
    channels = _normalize_color(color)
    return "".join(f"{channel:02X}" for channel in channels)


def leds_payload(
    led_ranges: list[Any],
    seg_id: int | None = None,
    *,
    length: int | None = None,
    channel: str | None = None,
) -> WledPayload:
    """Build a per-LED payload using the WLED segment ``i`` grammar."""
    if not led_ranges:
        raise ValueError("leds_payload requires at least one LED color or range.")
    resolved_length = _resolve_column_length(length, channel)
    individual: list[Any] = []
    for item in led_ranges:
        is_range = (
            isinstance(item, (list, tuple))
            and len(item) == 3
            and isinstance(item[0], (int, float))
            and isinstance(item[1], (int, float))
            and isinstance(item[2], (str, list, tuple))
        )
        if is_range:
            start, stop = int(item[0]), int(item[1])
            if not (0 <= start < stop <= resolved_length):
                raise ValueError(
                    f"LED range must satisfy 0 <= start < stop <= {resolved_length}, "
                    f"got start={start}, stop={stop}."
                )
            individual.extend([start, stop, _color_token(item[2])])
        else:
            individual.append(_color_token(item))
    seg: SegPayload = {"i": individual}
    if seg_id is not None:
        seg["id"] = require_int_range(seg_id, 0, 255, name="segment id")
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

_CONFIG_ROOT = Path(
    os.environ.get("LIGHTSCTL_CONFIG_DIR", "~/.config/lightsctl")
).expanduser()
_LEGACY_CONFIG_ROOT = Path("~/.config/lightss").expanduser()
_SCENE_DIR = str(_CONFIG_ROOT)  # compatibility for callers importing this name
_SCENE_PATH = _CONFIG_ROOT / "scenes.json"
_SCHEDULE_PATH = _CONFIG_ROOT / "schedule.json"
_PERSISTENCE_LOCK = threading.RLock()


def _migrate_legacy_file(path: Path) -> None:
    if path.exists():
        return
    legacy = _LEGACY_CONFIG_ROOT / path.name
    if not legacy.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(legacy.read_bytes())
        logger.info("Migrated legacy configuration %s -> %s", legacy, path)
    except OSError as exc:
        logger.warning("Could not migrate legacy configuration %s: %s", legacy, exc)


def _read_json_file(path: Path, expected_type: type, default: Any) -> Any:
    _migrate_legacy_file(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return copy.deepcopy(default)
    except json.JSONDecodeError as exc:
        logger.error("Ignoring invalid JSON in %s: %s", path, exc)
        return copy.deepcopy(default)
    except OSError as exc:
        logger.error("Could not read %s: %s", path, exc)
        return copy.deepcopy(default)
    if not isinstance(data, expected_type):
        logger.error(
            "Ignoring %s because its root must be %s.", path, expected_type.__name__
        )
        return copy.deepcopy(default)
    return data


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    with _PERSISTENCE_LOCK:
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, path)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _normalize_scene_name(name: str) -> str:
    key = " ".join(str(name).strip().lower().split())
    if not key:
        raise ValueError("Scene name cannot be empty.")
    if len(key) > 80:
        raise ValueError("Scene name cannot exceed 80 characters.")
    return key


def _load_scenes() -> dict[str, WledPayload]:
    raw = _read_json_file(_SCENE_PATH, dict, {})
    scenes: dict[str, WledPayload] = {}
    for name, payload in raw.items():
        if not isinstance(name, str) or not isinstance(payload, dict):
            logger.warning("Skipping malformed scene entry %r.", name)
            continue
        try:
            validate_wled_payload(payload)
        except ValueError as exc:
            logger.warning("Skipping invalid saved scene '%s': %s", name, exc)
            continue
        scenes[name] = payload  # type: ignore[assignment]
    return scenes


def _save_scenes(scenes: dict[str, WledPayload]) -> None:
    _atomic_write_json(_SCENE_PATH, scenes)


def save_scene(name: str, payload: WledPayload) -> None:
    key = _normalize_scene_name(name)
    validate_wled_payload(payload)
    scenes = _load_scenes()
    scenes[key] = copy.deepcopy(payload)
    _save_scenes(scenes)


def delete_scene(name: str) -> bool:
    scenes = _load_scenes()
    removed = scenes.pop(_normalize_scene_name(name), None) is not None
    if removed:
        _save_scenes(scenes)
    return removed


def list_scenes() -> list[str]:
    return sorted(_load_scenes().keys())


def load_scene_payload(name: str) -> WledPayload:
    scenes = _load_scenes()
    key = _normalize_scene_name(name)
    if key in scenes:
        return copy.deepcopy(scenes[key])
    raise ValueError(
        f"Unknown saved scene: {name}. Saved scenes: {', '.join(list_scenes()) or 'none'}."
    )


def scene_payload(name: str, transition_ms: int = 0) -> WledPayload:
    key = _normalize_scene_name(name)
    if key in _builtin_scenes:
        payload = copy.deepcopy(_builtin_scenes[key])
    else:
        payload = load_scene_payload(key)
    return _apply_transition(payload, transition_ms)


# ---------------------------------------------------------------------------
# Schedule persistence
# ---------------------------------------------------------------------------


def _load_schedule() -> list[dict[str, Any]]:
    raw = _read_json_file(_SCHEDULE_PATH, list, [])
    return [entry for entry in raw if isinstance(entry, dict)]


def _save_schedule(schedule: list[dict[str, Any]]) -> None:
    _atomic_write_json(_SCHEDULE_PATH, schedule)


def _normalize_schedule_time(time_str: str) -> str:
    parts = str(time_str).strip().split(":")
    if (
        len(parts) != 2
        or not all(part.isdigit() for part in parts)
        or not (0 <= int(parts[0]) <= 23)
        or not (0 <= int(parts[1]) <= 59)
    ):
        raise ValueError(f"Schedule time must be in HH:MM format, got '{time_str}'.")
    return f"{int(parts[0]):02d}:{int(parts[1]):02d}"


def add_schedule(
    time_str: str, action: str, data: dict[str, Any] | None = None
) -> None:
    normalized_time = _normalize_schedule_time(time_str)
    normalized_action = str(action).strip().lower()
    if normalized_action not in {"on", "off", "scene"}:
        raise ValueError("Schedule action must be one of: on, off, scene.")
    payload = dict(data or {})
    if normalized_action == "scene":
        if not payload.get("scene"):
            raise ValueError("A scheduled scene action requires data['scene'].")
        payload["scene"] = _normalize_scene_name(str(payload["scene"]))
    schedule = _load_schedule()
    schedule.append(
        {"time": normalized_time, "action": normalized_action, "data": payload}
    )
    schedule.sort(key=lambda entry: str(entry.get("time", "")))
    _save_schedule(schedule)


def remove_schedule(index: int) -> bool:
    schedule = _load_schedule()
    if not (0 <= index < len(schedule)):
        return False
    schedule.pop(index)
    _save_schedule(schedule)
    return True


def list_schedule() -> list[dict[str, Any]]:
    return copy.deepcopy(_load_schedule())


# ---------------------------------------------------------------------------
# Kelvin → RGBW
# ---------------------------------------------------------------------------


def kelvin_to_rgbw(kelvin: int) -> tuple[int, int, int, int]:
    """Approximate RGBW from Kelvin in a practical 1000-20000 K range."""
    kelvin = require_int_range(kelvin, 1000, 20000, name="kelvin")
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


def _positive_float(value: float, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}.") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a finite number greater than zero.")
    return number


def _animation_plan(
    duration_seconds: float, preferred_interval: float = 5.0
) -> tuple[int, float]:
    """Return a bounded step count and interval for controller-friendly fades."""
    duration_seconds = _positive_float(duration_seconds, name="duration_seconds")
    steps = max(1, min(255, math.ceil(duration_seconds / preferred_interval)))
    return steps, duration_seconds / steps


class FadeTimer:
    """Gradually reduce brightness over a duration, then turn the target off."""

    def __init__(
        self,
        client: LightClient,
        duration_minutes: float,
        start_brightness: int | None = None,
    ) -> None:
        self.client = client
        self.duration_minutes = _positive_float(
            duration_minutes, name="duration_minutes"
        )
        self.start_brightness = (
            None if start_brightness is None else clamp_byte(start_brightness)
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Fade timer is already running."
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="lightsctl-fade", daemon=True
        )
        self._thread.start()
        return f"Fade timer started ({self.duration_minutes:g} min)."

    def stop(self) -> str:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        return "Fade timer stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            start_bri = self.start_brightness
            if start_bri is None:
                state = self.client.get_state()
                start_bri = clamp_byte(state.get("bri", 128))
            duration_seconds = self.duration_minutes * 60.0
            steps, interval = _animation_plan(duration_seconds)
            started = time.monotonic()
            for step in range(steps + 1):
                if self._stop.is_set():
                    return
                progress = step / steps
                brightness = round(start_bri * (1.0 - progress))
                self.client.post_state(brightness_payload(brightness))
                if step < steps:
                    deadline = started + ((step + 1) * interval)
                    if self._stop.wait(max(0.0, deadline - time.monotonic())):
                        return
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
    return _apply_transition(payload, transition_ms)


PLAYLIST_MIN = 1
PLAYLIST_MAX = 250


def playlist_payload(playlist_id: int, transition_ms: int = 0) -> WledPayload:
    playlist_id = int(playlist_id)
    if not (PLAYLIST_MIN <= playlist_id <= PLAYLIST_MAX):
        raise ValueError(
            f"Playlist ID must be between {PLAYLIST_MIN} and {PLAYLIST_MAX}."
        )
    payload: WledPayload = {"pl": playlist_id}
    return _apply_transition(payload, transition_ms)


# ---------------------------------------------------------------------------
# Scene Cycle / Playlist
# ---------------------------------------------------------------------------


class CycleThread:
    """Auto-rotate through a validated list of scenes or presets."""

    def __init__(
        self,
        client: LightClient,
        items: list[str] | None = None,
        interval_seconds: float = 60.0,
        mode: str = "scene",
    ) -> None:
        self.client = client
        self.items = list(items) if items is not None else list(_builtin_scenes.keys())
        if not self.items:
            raise ValueError("Cycle requires at least one scene or preset.")
        self.interval_seconds = _positive_float(
            interval_seconds, name="interval_seconds"
        )
        if self.interval_seconds < 1.0:
            raise ValueError("Cycle interval must be at least 1 second.")
        if mode not in {"scene", "preset"}:
            raise ValueError("Cycle mode must be 'scene' or 'preset'.")
        self.mode = mode
        if self.mode == "preset":
            for item in self.items:
                require_int_range(int(item), PRESET_MIN, PRESET_MAX, name="preset id")
        else:
            available = set(_builtin_scenes) | set(list_scenes())
            unknown = [
                item
                for item in self.items
                if _normalize_scene_name(item) not in available
            ]
            if unknown:
                raise ValueError(f"Unknown cycle scenes: {', '.join(unknown)}.")
        self._index = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Cycle is already running."
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="lightsctl-cycle", daemon=True
        )
        self._thread.start()
        return f"Cycle started ({len(self.items)} items, {self.interval_seconds:g}s interval)."

    def stop(self) -> str:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        return "Cycle stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        logger.info("Scene cycle started.")
        while not self._stop.is_set():
            item = self.items[self._index % len(self.items)]
            try:
                if self.mode == "preset":
                    self.client.post_state(preset_payload(int(item)))
                else:
                    self.client.post_state(scene_payload(item))
                logger.info("Cycled to: %s", item)
            except Exception:
                logger.exception("Cycle step failed for %s", item)
            self._index += 1
            if self._stop.wait(self.interval_seconds):
                return


# ---------------------------------------------------------------------------
# Sunrise Simulator
# ---------------------------------------------------------------------------


class SunriseSimulator:
    """Gradually increase brightness and shift from warm to daylight RGBW."""

    def __init__(
        self,
        client: LightClient,
        duration_minutes: float = 30.0,
        start_kelvin: int = 2000,
        end_kelvin: int = 5000,
        max_brightness: int = 255,
    ) -> None:
        self.client = client
        self.duration_minutes = _positive_float(
            duration_minutes, name="duration_minutes"
        )
        self.start_kelvin = require_int_range(
            start_kelvin, 1000, 20000, name="start_kelvin"
        )
        self.end_kelvin = require_int_range(end_kelvin, 1000, 20000, name="end_kelvin")
        self.max_brightness = clamp_byte(max_brightness)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Sunrise simulation is already running."
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="lightsctl-sunrise", daemon=True
        )
        self._thread.start()
        return f"Sunrise started ({self.duration_minutes:g} min)."

    def stop(self) -> str:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        return "Sunrise stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        logger.info("Sunrise simulation started.")
        try:
            duration_seconds = self.duration_minutes * 60.0
            steps, interval = _animation_plan(duration_seconds)
            started = time.monotonic()
            for step in range(steps + 1):
                if self._stop.is_set():
                    return
                progress = step / steps
                brightness = round(self.max_brightness * progress)
                kelvin = round(
                    self.start_kelvin
                    + ((self.end_kelvin - self.start_kelvin) * progress)
                )
                payload = merge_payloads(
                    on_payload(True),
                    brightness_payload(brightness),
                    color_payload(*kelvin_to_rgbw(kelvin)),
                )
                self.client.post_state(payload)
                if step < steps:
                    deadline = started + ((step + 1) * interval)
                    if self._stop.wait(max(0.0, deadline - time.monotonic())):
                        return
        except Exception:
            logger.exception("Sunrise simulation error")


# ---------------------------------------------------------------------------
# Configuration file
# ---------------------------------------------------------------------------

_CONFIG_PATH = _CONFIG_ROOT / "config.json"


def load_config() -> dict[str, Any]:
    return _read_json_file(_CONFIG_PATH, dict, {})


def save_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")
    _atomic_write_json(_CONFIG_PATH, config)


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
                if "usb" in str(dev.get("name", "")).casefold():
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

_SENSITIVE_KEY_TERMS = (
    "password",
    "passwd",
    "passphrase",
    "psk",
    "token",
    "secret",
    "authorization",
)


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(term in key_text.casefold() for term in _SENSITIVE_KEY_TERMS):
                redacted[key_text] = "<redacted>"
            else:
                redacted[key_text] = _redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


@dataclass(slots=True)
class LightClient:
    host: str = DEFAULT_HOST
    timeout: float = 2.5
    dry_run: bool = False
    retries: int = 3
    min_request_interval: float = 0.1
    _last_req_time: float = field(default=0.0, init=False, repr=False)
    _http_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.host = normalize_host(self.host)
        self.timeout = _positive_float(self.timeout, name="timeout")
        self.retries = require_int_range(self.retries, 1, 10, name="retries")
        self.min_request_interval = max(0.0, float(self.min_request_interval))

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

    @staticmethod
    def _decode_json_bytes(body: bytes, url: str) -> dict[str, Any] | list[Any]:
        if not body.strip():
            raise RuntimeError(f"Empty response from {url}.")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid JSON from {url}: {exc}") from exc
        if not isinstance(data, (dict, list)):
            raise RuntimeError(
                f"Unexpected JSON root from {url}: {type(data).__name__}."
            )
        return data

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_req_time
        delay = self.min_request_interval - elapsed
        if delay > 0:
            time.sleep(delay)

    def _request_bytes(self, request: urllib.request.Request) -> bytes:
        retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
        last_exc: BaseException | None = None
        with self._http_lock:
            for attempt in range(self.retries):
                self._wait_for_rate_limit()
                try:
                    with urllib.request.urlopen(
                        request, timeout=self.timeout
                    ) as response:
                        body = response.read()
                    self._last_req_time = time.monotonic()
                    return body
                except urllib.error.HTTPError as exc:
                    try:
                        detail = exc.read(512).decode("utf-8", errors="replace").strip()
                    finally:
                        exc.close()
                    last_exc = exc
                    if exc.code not in retryable_statuses:
                        suffix = f": {detail}" if detail else ""
                        raise RuntimeError(
                            f"WLED HTTP {exc.code} from {request.full_url}{suffix}"
                        ) from exc
                except (urllib.error.URLError, OSError) as exc:
                    last_exc = exc
                if attempt < self.retries - 1:
                    time.sleep(0.1 * (2**attempt))
        raise RuntimeError(
            f"Could not reach light controller at {request.full_url}: {last_exc}"
        ) from last_exc

    def _get_json_url(self, url: str) -> dict[str, Any] | list[Any]:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "lightsctl/2"},
            method="GET",
        )
        return self._decode_json_bytes(self._request_bytes(request), url)

    def get_json(self) -> dict[str, Any]:
        data = self._get_json_url(self.json_url)
        if not isinstance(data, dict):
            raise RuntimeError(f"Expected an object from {self.json_url}.")
        return data

    def get_info(self) -> dict[str, Any]:
        data = self._get_json_url(self.info_url)
        if not isinstance(data, dict):
            raise RuntimeError(f"Expected an object from {self.info_url}.")
        return data

    def get_effects(self) -> list[str]:
        data = self._get_json_url(self.effects_url)
        if not isinstance(data, list):
            raise RuntimeError(f"Expected an array from {self.effects_url}.")
        return [str(item) for item in data]

    def get_palettes(self) -> list[str]:
        data = self._get_json_url(self.palettes_url)
        if not isinstance(data, list):
            raise RuntimeError(f"Expected an array from {self.palettes_url}.")
        return [str(item) for item in data]

    def get_nodes(self) -> dict[str, Any]:
        data = self._get_json_url(self.nodes_url)
        return data if isinstance(data, dict) else {}

    def get_live(self) -> dict[str, Any]:
        data = self._get_json_url(self.live_url)
        return data if isinstance(data, dict) else {}

    def get_config(self) -> dict[str, Any]:
        data = self._get_json_url(self.config_url)
        if not isinstance(data, dict):
            raise RuntimeError(f"Expected an object from {self.config_url}.")
        return data

    def get_fxdata(self) -> list[str]:
        data = self._get_json_url(self.fxdata_url)
        if not isinstance(data, list):
            raise RuntimeError(f"Expected an array from {self.fxdata_url}.")
        return [str(item) for item in data]

    def get_networks(self) -> dict[str, Any] | list[Any]:
        return self._get_json_url(self.networks_url)

    def get_presets(self) -> dict[str, Any]:
        """Read presets, trying both known WLED endpoints."""
        errors: list[str] = []
        for url in (self.presets_url, self.presets_json_url):
            try:
                data = self._get_json_url(url)
                if isinstance(data, dict):
                    return data
                errors.append(f"{url}: non-object response")
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        logger.debug("Could not read WLED presets: %s", "; ".join(errors))
        return {}

    def _snapshot_part(
        self,
        name: str,
        loader: Callable[[], dict[str, Any] | list[Any]],
    ) -> dict[str, Any] | list[Any]:
        try:
            return loader()
        except Exception as exc:
            logger.warning("Could not read WLED %s snapshot: %s", name, exc)
            return {"error": str(exc)}

    def get_device_snapshot(self) -> dict[str, Any]:
        combined = self._snapshot_part("combined", self.get_json)
        if not isinstance(combined, dict):
            combined = {}
        effects = combined.get("effects")
        if not isinstance(effects, list):
            effects = self._snapshot_part("effects", self.get_effects)
        palettes = combined.get("palettes")
        if not isinstance(palettes, list):
            palettes = self._snapshot_part("palettes", self.get_palettes)
        effect_names = (
            [str(item) for item in effects] if isinstance(effects, list) else []
        )
        fxdata = self._snapshot_part("fxdata", self.get_fxdata)
        fxdata_rows = [str(item) for item in fxdata] if isinstance(fxdata, list) else []
        catalog = build_effect_catalog(effect_names, fxdata_rows)
        safe_ids = safe_effect_ids(effect_names)
        return {
            "host": self.host,
            "state": combined.get("state")
            or self._snapshot_part("state", self.get_state),
            "info": combined.get("info") or self._snapshot_part("info", self.get_info),
            "effects": effect_names if effect_names else effects,
            "safe_effects": [
                {"id": index, "name": name}
                for index, name in enumerate(effect_names)
                if index in safe_ids
            ],
            "effect_catalog": catalog["effects"],
            "effect_groups": catalog["groups"],
            "safe_effect_parameter_hints": {
                str(entry["id"]): entry["metadata"]
                for entry in catalog["effects"]
                if entry["safe"]
            },
            "palettes": palettes,
            "config": _redact_sensitive(self._snapshot_part("config", self.get_config)),
            "fxdata": fxdata,
            "networks": _redact_sensitive(
                self._snapshot_part("networks", self.get_networks)
            ),
            "presets": self._snapshot_part("presets", self.get_presets),
            "wall_topology": {
                "order": list(WALL_ORDER),
                "channels": copy.deepcopy(CHANNEL_TOPOLOGY),
                "orientation": "bottom-to-top",
            },
        }

    def get_state(self) -> dict[str, Any]:
        data = self._get_json_url(self.state_url)
        if not isinstance(data, dict):
            raise RuntimeError(f"Expected an object from {self.state_url}.")
        return data

    def post_state(self, payload: WledPayload) -> dict[str, Any] | None:
        validate_wled_payload(payload)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self.dry_run:
            logger.info("dry-run [%s]: %s", self.host, body.decode("utf-8"))
            return None
        request = urllib.request.Request(
            self.state_url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "lightsctl/2",
            },
            method="POST",
        )
        response_body = self._request_bytes(request)
        if not response_body.strip():
            return None
        data = self._decode_json_bytes(response_body, self.state_url)
        return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Audio-reactive mode
# ---------------------------------------------------------------------------


class ReactiveMode:
    """Low-latency microphone mode that avoids effect changes on every beat.

    The old implementation rotated the effect for every detected beat, producing
    excessive HTTP traffic and visually chaotic output. This implementation uses
    a stable Solid effect, smooths amplitude, and changes color only periodically.
    """

    def __init__(
        self,
        client: LightClient,
        palette: Sequence[tuple[int, int, int, int]] | None = None,
        min_interval: float = 0.12,
        min_brightness: int = 24,
        max_brightness: int = 255,
        color_every_beats: int = 4,
    ) -> None:
        self.client = client
        self.palette = tuple(
            palette
            or (
                (255, 0, 0, 0),
                (255, 90, 0, 0),
                (255, 0, 180, 0),
                (0, 80, 255, 0),
                (0, 255, 120, 0),
                (255, 120, 0, 120),
            )
        )
        if not self.palette:
            raise ValueError("Reactive palette cannot be empty.")
        self.min_interval = max(0.1, _positive_float(min_interval, name="min_interval"))
        self.min_brightness = clamp_byte(min_brightness)
        self.max_brightness = clamp_byte(max_brightness)
        if self.max_brightness < self.min_brightness:
            raise ValueError("max_brightness cannot be lower than min_brightness.")
        self.color_every_beats = require_int_range(
            color_every_beats, 1, 64, name="color_every_beats"
        )
        self.color_index = 0
        self.beat_count = 0
        self.last_sent_at = 0.0
        self.smoothed_energy = 0.0
        self.last_brightness: int | None = None
        self._initialized = False

    def handle_level(self, energy: float, beat: bool = False) -> None:
        now = time.monotonic()
        energy = max(0.0, min(1.0, float(energy)))
        attack = 0.55 if energy > self.smoothed_energy else 0.18
        self.smoothed_energy += (energy - self.smoothed_energy) * attack
        color_changed = False
        if beat:
            self.beat_count += 1
            if self.beat_count % self.color_every_beats == 0:
                self.color_index = (self.color_index + 1) % len(self.palette)
                color_changed = True
        brightness = round(
            self.min_brightness
            + ((self.max_brightness - self.min_brightness) * self.smoothed_energy)
        )
        brightness_changed = (
            self.last_brightness is None or abs(brightness - self.last_brightness) >= 2
        )
        refresh_due = now - self.last_sent_at >= 0.75
        if not color_changed and not brightness_changed and not refresh_due:
            return
        if now - self.last_sent_at < self.min_interval and not color_changed:
            return
        payloads: list[WledPayload] = [
            on_payload(True),
            brightness_payload(brightness),
        ]
        if not self._initialized:
            payloads.append(effect_payload(0, speed=128))
            payloads.append(color_payload(*self.palette[self.color_index]))
            self._initialized = True
        elif color_changed:
            payloads.append(color_payload(*self.palette[self.color_index]))
        self.client.post_state(merge_payloads(*payloads))
        self.last_brightness = brightness
        self.last_sent_at = now

    def handle_beat(self, energy: float) -> None:
        """Backward-compatible beat-only entry point."""
        self.handle_level(energy, beat=True)


class BeatDetector:
    def __init__(
        self,
        threshold: float = 1.55,
        floor: float = 0.015,
        minimum_spacing: float = 0.16,
    ) -> None:
        self.threshold = _positive_float(threshold, name="threshold")
        self.floor = max(0.0, float(floor))
        self.minimum_spacing = _positive_float(minimum_spacing, name="minimum_spacing")
        self.baseline = 0.03
        self.last_beat_at = 0.0

    def update(self, rms: float) -> bool:
        value = max(0.0, float(rms))
        previous_baseline = max(self.baseline, 1e-6)
        now = time.monotonic()
        is_beat = (
            value >= self.floor
            and value > previous_baseline * self.threshold
            and now - self.last_beat_at >= self.minimum_spacing
        )
        # Track the ambient level after comparing against the previous baseline;
        # incorporating the current spike first suppresses real beats.
        weight = 0.04 if value > previous_baseline else 0.10
        self.baseline = (previous_baseline * (1.0 - weight)) + (value * weight)
        if is_beat:
            self.last_beat_at = now
        return is_beat


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
            name="lightsctl-reactive",
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
        raise RuntimeError(
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
            consecutive_errors = 0
            while stop_event is None or not stop_event.is_set():
                try:
                    samples, overflowed = stream.read(blocksize)
                    if overflowed:
                        logger.debug("Audio input overflowed; processing latest block.")
                    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
                    energy = min(1.0, rms * 12.0)
                    beat = detector.update(rms)
                    if level_callback is not None:
                        level_callback(energy, beat)
                    mode.handle_level(energy, beat=beat)
                    consecutive_errors = 0
                except Exception:
                    consecutive_errors += 1
                    logger.exception("Error in audio processing loop")
                    if consecutive_errors >= 10:
                        raise RuntimeError("Audio processing failed repeatedly.")
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
        return require_int_range(samplerate, 8000, 384000, name="samplerate")
    device_info = sounddevice_module.query_devices(device=device, kind="input")
    return require_int_range(
        int(device_info.get("default_samplerate", 44100)),
        8000,
        384000,
        name="samplerate",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _arg_int_range(minimum: int, maximum: int, label: str) -> Callable[[str], int]:
    def parser(value: str) -> int:
        try:
            return require_int_range(int(value), minimum, maximum, name=label)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return parser


_arg_byte = _arg_int_range(0, 255, "value")
_arg_segment = _arg_int_range(0, 255, "segment")
_arg_c3 = _arg_int_range(0, 31, "c3")
_arg_preset = _arg_int_range(PRESET_MIN, PRESET_MAX, "preset id")
_arg_transition_ms = _arg_int_range(0, 6_553_500, "transition milliseconds")
_arg_kelvin = _arg_int_range(2000, 6500, "kelvin")
_arg_cct = _arg_int_range(0, 10091, "cct")


def parse_rgbw(values: Iterable[str]) -> tuple[int, int, int, int]:
    try:
        parsed = [
            require_int_range(int(value), 0, 255, name="color channel")
            for value in values
        ]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if len(parsed) not in (3, 4):
        raise argparse.ArgumentTypeError("color needs R G B or R G B W")
    if len(parsed) == 3:
        parsed.append(0)
    return (parsed[0], parsed[1], parsed[2], parsed[3])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control the four-column bedroom/office WLED wall."
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Force single-controller mode with this host; otherwise use fleet.py configuration.",
    )
    parser.add_argument(
        "--target",
        default="all",
        help="Fleet target: all, left, right, or a channel name (default: all).",
    )
    parser.add_argument(
        "--segment",
        type=_arg_segment,
        default=None,
        help="WLED segment id within a controller target.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log JSON instead of sending state changes.",
    )
    parser.add_argument(
        "--transition",
        type=_arg_transition_ms,
        default=0,
        help="One-shot transition in milliseconds (0-6553500); emitted as WLED 'tt'.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("on", help="Turn the target on")
    subparsers.add_parser("off", help="Turn the target off")

    bri = subparsers.add_parser("bri", help="Set brightness, 0-255")
    bri.add_argument("value", type=_arg_byte)

    color = subparsers.add_parser("color", help="Set RGB or RGBW color")
    color.add_argument("values", nargs="+", help="R G B or R G B W")

    fx = subparsers.add_parser("fx", help="Set an offline-validated safe effect")
    fx.add_argument("effect", type=int, choices=tuple(SAFE_EFFECTS))
    fx.add_argument("--speed", "-s", type=_arg_byte, default=128)
    fx.add_argument("--intensity", "-i", type=_arg_byte)
    fx.add_argument("--palette", "-p", type=_arg_int_range(0, 65535, "palette"))
    fx.add_argument("--c1", type=_arg_byte)
    fx.add_argument("--c2", type=_arg_byte)
    fx.add_argument("--c3", type=_arg_c3)
    fx.add_argument("--o1", action=argparse.BooleanOptionalAction, default=None)
    fx.add_argument("--o2", action=argparse.BooleanOptionalAction, default=None)
    fx.add_argument("--o3", action=argparse.BooleanOptionalAction, default=None)

    scene = subparsers.add_parser("scene", help="Apply a named built-in or saved scene")
    scene.add_argument("name")

    mode1 = subparsers.add_parser(
        "mode1", help="Microphone-driven amplitude and beat mode"
    )
    mode1.add_argument("--device", help="sounddevice input device name or index")
    mode1.add_argument(
        "--samplerate", type=_arg_int_range(8000, 384000, "samplerate"), default=None
    )

    temp = subparsers.add_parser(
        "temp", help="Approximate RGBW color temperature, 2000-6500 K"
    )
    temp.add_argument("kelvin", type=_arg_kelvin)

    cct = subparsers.add_parser(
        "cct", help="Set native WLED CCT: 0-255 or 1900-10091 K"
    )
    cct.add_argument("value", type=_arg_cct)

    save_scene_parser = subparsers.add_parser(
        "save-scene", help="Save current state as a named scene"
    )
    save_scene_parser.add_argument("name")

    delete_scene_parser = subparsers.add_parser(
        "delete-scene", help="Delete a saved scene"
    )
    delete_scene_parser.add_argument("name")

    schedule_parser = subparsers.add_parser(
        "schedule", help="Manage persisted schedule entries"
    )
    schedule_sub = schedule_parser.add_subparsers(dest="schedule_action", required=True)
    sched_add = schedule_sub.add_parser("add", help="Add a schedule entry")
    sched_add.add_argument("time", help="Time in HH:MM format")
    sched_add.add_argument("action", choices=("on", "off", "scene"))
    sched_add.add_argument("--scene-name", help="Required when action=scene")
    schedule_sub.add_parser("list", help="List schedule entries")
    sched_remove = schedule_sub.add_parser(
        "remove", help="Remove a schedule entry by index"
    )
    sched_remove.add_argument("index", type=int)

    hex_cmd = subparsers.add_parser("hex", help="Set #RRGGBB or #RRGGBBWW color")
    hex_cmd.add_argument("color")

    subparsers.add_parser("random", help="Apply a random built-in scene")

    fade = subparsers.add_parser("fade-off", help="Gradually fade to off")
    fade.add_argument("minutes", type=float)
    fade.add_argument(
        "--brightness", type=_arg_byte, help="Starting brightness; defaults to current"
    )

    preset = subparsers.add_parser("preset", help="Load a WLED preset")
    preset.add_argument("id", type=_arg_preset)

    cycle = subparsers.add_parser("cycle", help="Auto-rotate through scenes or presets")
    cycle.add_argument(
        "--items", nargs="+", help="Items to cycle; defaults to built-in scenes"
    )
    cycle.add_argument(
        "--interval", type=float, default=60, help="Seconds between changes"
    )
    cycle.add_argument("--mode", choices=("scene", "preset"), default="scene")

    sunrise = subparsers.add_parser("sunrise", help="Gradual wake-up simulation")
    sunrise.add_argument("--minutes", type=float, default=30)
    sunrise.add_argument(
        "--start-kelvin", type=_arg_int_range(1000, 20000, "start kelvin"), default=2000
    )
    sunrise.add_argument(
        "--end-kelvin", type=_arg_int_range(1000, 20000, "end kelvin"), default=5000
    )
    sunrise.add_argument("--brightness", type=_arg_byte, default=255)

    subparsers.add_parser("info", help="Read controller information")
    subparsers.add_parser("snapshot", help="Print an AI-ready device snapshot as JSON")
    subparsers.add_parser("effects", help="Print effect IDs and names")
    subparsers.add_parser("palettes", help="Print palette IDs and names")
    subparsers.add_parser("presets", help="Print saved WLED presets")
    subparsers.add_parser("restart", help="Reboot the target controller")
    subparsers.add_parser("segments", help="List fleet controllers and wall channels")

    wall = subparsers.add_parser(
        "wall", help="Wall-wide composers across all four columns"
    )
    wall.add_argument("mode", choices=("span", "mirror", "chase", "versus"))
    wall.add_argument("--fx", type=int, choices=tuple(SAFE_EFFECTS), default=9)
    wall.add_argument("--pal", type=_arg_int_range(0, 65535, "palette"), default=None)
    wall.add_argument("--fx-left", type=int, choices=tuple(SAFE_EFFECTS), default=None)
    wall.add_argument("--fx-right", type=int, choices=tuple(SAFE_EFFECTS), default=None)
    wall.add_argument(
        "--pal-left", type=_arg_int_range(0, 65535, "left palette"), default=None
    )
    wall.add_argument(
        "--pal-right", type=_arg_int_range(0, 65535, "right palette"), default=None
    )

    atmosphere = subparsers.add_parser(
        "atmosphere", help="Apply a named curated atmosphere"
    )
    atmosphere.add_argument("name", help="Atmosphere name or 'list'")

    return parser


class _FleetRouter:
    """Duck-typed LightClient shim that routes through a LightFleet target."""

    def __init__(self, fleet: object, target: str = "all") -> None:
        self.fleet = fleet
        self.target = target

    def post_state(self, payload: WledPayload) -> Any:
        validate_wled_payload(payload)
        return self.fleet.post_state(payload, target=self.target)  # type: ignore[attr-defined]

    def get_state(self) -> dict[str, Any]:
        states = self.fleet.get_state(target=self.target)  # type: ignore[attr-defined]
        if not isinstance(states, dict):
            return {}
        for entry in states.values():
            if isinstance(entry, dict):
                state = entry.get("state", entry)
                if isinstance(state, dict):
                    return state
        return {}


def _with_seg_id(payload: WledPayload, seg_id: int | None) -> WledPayload:
    """Scope a payload to one segment without leaking global on/brightness changes."""
    if seg_id is None:
        return copy.deepcopy(payload)
    segment_id = require_int_range(seg_id, 0, 255, name="segment id")
    scoped = copy.deepcopy(payload)
    segments = scoped.get("seg")
    if segments is None:
        segments = [{}]
        scoped["seg"] = segments
    if len(segments) != 1:
        raise ValueError(
            "--segment cannot be applied to a scene containing multiple segment entries."
        )
    segment = segments[0]
    segment["id"] = segment_id
    for key in ("on", "bri"):
        if key in scoped:
            segment[key] = scoped.pop(key)  # type: ignore[literal-required]
    return scoped


def _log_wled_info(info: Mapping[str, Any]) -> None:
    logger.info("Name: %s", info.get("name", "?"))
    logger.info("Version: %s", info.get("ver", "?"))
    leds = info.get("leds", {})
    logger.info("LEDs: %d", leds.get("count", 0) if isinstance(leds, dict) else 0)
    logger.info("Uptime: %ds", info.get("uptime", 0))
    logger.info("Free heap: %d", info.get("freeheap", 0))
    logger.info("IP: %s", info.get("ip", "?"))


def _selected_controllers(fleet_mod: Any, fleet_obj: Any, target: str) -> list[Any]:
    controllers = list(fleet_mod.load_controllers())
    normalized = str(target).strip().lower()
    if normalized == "all":
        return controllers
    by_name = {str(controller.name).lower(): controller for controller in controllers}
    if normalized in by_name:
        return [by_name[normalized]]
    channels = fleet_obj.channels()
    if normalized in channels:
        controller_name, _segment_id = channels[normalized]
        controller = by_name.get(str(controller_name).lower())
        if controller is not None:
            return [controller]
    raise ValueError(
        f"Unknown target '{target}'. Use all, a controller name, or one of: {', '.join(WALL_ORDER)}."
    )


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, default=str))


def _single_target_required(
    args: argparse.Namespace, fleet_mod: Any, fleet_obj: Any, operation: str
) -> None:
    if fleet_obj is None:
        return
    selected = _selected_controllers(fleet_mod, fleet_obj, args.target)
    if len(selected) != 1:
        raise ValueError(
            f"{operation} requires a single controller or channel target, not 'all'."
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    transition_ms = args.transition
    seg_id = args.segment

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    fleet_mod: Any = None
    fleet_obj: Any = None
    try:
        segment_capable_commands = {
            "on",
            "off",
            "bri",
            "color",
            "fx",
            "scene",
            "temp",
            "cct",
            "hex",
            "random",
        }
        if seg_id is not None and args.command not in segment_capable_commands:
            raise ValueError(
                f"--segment is not supported by the '{args.command}' command."
            )
        if args.host is not None:
            if args.target != "all":
                raise ValueError(
                    "--target is only valid in fleet mode; omit --host or leave --target=all."
                )
            client: LightClient | _FleetRouter = LightClient(
                args.host, dry_run=args.dry_run
            )
        else:
            import fleet as fleet_mod  # lazy: fleet.py imports lightsctl/lightctl

            fleet_obj = fleet_mod.LightFleet.from_config(dry_run=args.dry_run)
            channels = fleet_obj.channels()
            if seg_id is not None and args.target in channels:
                raise ValueError(
                    "Do not combine --segment with a channel target; the channel already identifies its segment."
                )
            client = _FleetRouter(fleet_obj, target=args.target)

        if args.command == "on":
            client.post_state(
                on_payload(True, transition_ms=transition_ms, seg_id=seg_id)
            )
        elif args.command == "off":
            client.post_state(
                on_payload(False, transition_ms=transition_ms, seg_id=seg_id)
            )
        elif args.command == "bri":
            client.post_state(
                brightness_payload(
                    args.value, transition_ms=transition_ms, seg_id=seg_id
                )
            )
        elif args.command == "color":
            client.post_state(
                color_payload(
                    *parse_rgbw(args.values), transition_ms=transition_ms, seg_id=seg_id
                )
            )
        elif args.command == "fx":
            client.post_state(
                effect_payload(
                    args.effect,
                    args.speed,
                    transition_ms=transition_ms,
                    intensity=args.intensity,
                    palette=args.palette,
                    c1=args.c1,
                    c2=args.c2,
                    c3=args.c3,
                    o1=args.o1,
                    o2=args.o2,
                    o3=args.o3,
                    seg_id=seg_id,
                )
            )
        elif args.command == "scene":
            client.post_state(
                _with_seg_id(
                    scene_payload(args.name, transition_ms=transition_ms), seg_id
                )
            )
        elif args.command == "mode1":
            run_mode1(client, device=args.device, samplerate=args.samplerate)
        elif args.command == "temp":
            client.post_state(
                color_payload(
                    *kelvin_to_rgbw(args.kelvin),
                    transition_ms=transition_ms,
                    seg_id=seg_id,
                )
            )
        elif args.command == "cct":
            if not (0 <= args.value <= 255 or 1900 <= args.value <= 10091):
                raise ValueError("cct must be 0-255 or 1900-10091 K.")
            client.post_state(
                cct_payload(args.value, transition_ms=transition_ms, seg_id=seg_id)
            )
        elif args.command == "save-scene":
            _single_target_required(args, fleet_mod, fleet_obj, "save-scene")
            state = client.get_state()
            payload: WledPayload = {}
            for key in ("on", "bri", "seg"):
                if key in state:
                    payload[key] = copy.deepcopy(state[key])  # type: ignore[literal-required]
            save_scene(args.name, payload)
            logger.info("Saved scene '%s'.", args.name)
        elif args.command == "delete-scene":
            if delete_scene(args.name):
                logger.info("Deleted scene '%s'.", args.name)
            else:
                raise ValueError(f"Saved scene '{args.name}' does not exist.")
        elif args.command == "schedule":
            if args.schedule_action == "add":
                data: dict[str, Any] = {}
                if args.action == "scene":
                    if not args.scene_name:
                        raise ValueError("--scene-name is required when action=scene")
                    data["scene"] = args.scene_name
                add_schedule(args.time, args.action, data)
                logger.info(
                    "Added schedule for %s.", _normalize_schedule_time(args.time)
                )
            elif args.schedule_action == "list":
                entries = list_schedule()
                if not entries:
                    logger.info("No scheduled entries.")
                else:
                    for index, entry in enumerate(entries):
                        logger.info(
                            "%d: %s -> %s %s",
                            index,
                            entry.get("time", "?"),
                            entry.get("action", "?"),
                            entry.get("data", ""),
                        )
            elif args.schedule_action == "remove":
                if remove_schedule(args.index):
                    logger.info("Removed schedule entry %d.", args.index)
                else:
                    raise ValueError(f"No schedule entry at index {args.index}.")
        elif args.command == "hex":
            client.post_state(
                color_payload(
                    *hex_to_rgbw(args.color), transition_ms=transition_ms, seg_id=seg_id
                )
            )
        elif args.command == "random":
            client.post_state(
                _with_seg_id(random_scene_payload(transition_ms=transition_ms), seg_id)
            )
        elif args.command == "fade-off":
            timer = FadeTimer(client, args.minutes, start_brightness=args.brightness)
            timer.start()
            logger.info(
                "Fading to off over %g minutes. Press Ctrl+C to stop.", args.minutes
            )
            while timer.is_alive():
                time.sleep(0.25)
        elif args.command == "preset":
            if seg_id is not None:
                raise ValueError(
                    "WLED presets are controller-wide and cannot be scoped with --segment."
                )
            client.post_state(preset_payload(args.id, transition_ms=transition_ms))
        elif args.command == "cycle":
            cycler = CycleThread(
                client, items=args.items, interval_seconds=args.interval, mode=args.mode
            )
            cycler.start()
            logger.info("Cycling %s. Press Ctrl+C to stop.", args.mode)
            while cycler.is_alive():
                time.sleep(0.25)
        elif args.command == "sunrise":
            sim = SunriseSimulator(
                client,
                duration_minutes=args.minutes,
                start_kelvin=args.start_kelvin,
                end_kelvin=args.end_kelvin,
                max_brightness=args.brightness,
            )
            sim.start()
            logger.info(
                "Sunrise simulation for %g minutes. Press Ctrl+C to stop.", args.minutes
            )
            while sim.is_alive():
                time.sleep(0.25)
        elif args.command == "info":
            if fleet_obj is not None:
                for controller in _selected_controllers(
                    fleet_mod, fleet_obj, args.target
                ):
                    logger.info("[%s]", controller.name)
                    _log_wled_info(LightClient(controller.host).get_info())
            else:
                _log_wled_info(client.get_info())  # type: ignore[attr-defined]
        elif args.command == "snapshot":
            if fleet_obj is not None:
                snapshots: dict[str, Any] = {}
                for controller in _selected_controllers(
                    fleet_mod, fleet_obj, args.target
                ):
                    snapshots[str(controller.name)] = LightClient(
                        controller.host
                    ).get_device_snapshot()
                _print_json(
                    {
                        "wall_order": list(WALL_ORDER),
                        "channels": fleet_obj.channels(),
                        "controllers": snapshots,
                    }
                )
            else:
                _print_json(client.get_device_snapshot())  # type: ignore[attr-defined]
        elif args.command == "effects":
            result: dict[str, Any] = {}
            if fleet_obj is not None:
                controllers = _selected_controllers(fleet_mod, fleet_obj, args.target)
            else:
                controllers = [
                    type("Controller", (), {"name": "controller", "host": args.host})()
                ]
            for controller in controllers:
                effects = LightClient(controller.host).get_effects()
                safe_ids = safe_effect_ids(effects)
                result[str(controller.name)] = [
                    {"id": index, "name": name, "safe": index in safe_ids}
                    for index, name in enumerate(effects)
                ]
            _print_json(result)
        elif args.command == "palettes":
            result = {}
            if fleet_obj is not None:
                controllers = _selected_controllers(fleet_mod, fleet_obj, args.target)
            else:
                controllers = [
                    type("Controller", (), {"name": "controller", "host": args.host})()
                ]
            for controller in controllers:
                result[str(controller.name)] = [
                    {"id": index, "name": name}
                    for index, name in enumerate(
                        LightClient(controller.host).get_palettes()
                    )
                ]
            _print_json(result)
        elif args.command == "presets":
            result = {}
            if fleet_obj is not None:
                controllers = _selected_controllers(fleet_mod, fleet_obj, args.target)
            else:
                controllers = [
                    type("Controller", (), {"name": "controller", "host": args.host})()
                ]
            for controller in controllers:
                result[str(controller.name)] = LightClient(
                    controller.host
                ).get_presets()
            _print_json(result)
        elif args.command == "restart":
            if seg_id is not None:
                raise ValueError(
                    "Restart is controller-wide and cannot be scoped with --segment."
                )
            try:
                client.post_state(restart_payload())
            except RuntimeError as exc:
                logger.debug("Controller disconnected during reboot: %s", exc)
            logger.info("Restart command sent.")
        elif args.command == "segments":
            if fleet_obj is None:
                _print_json(
                    {"wall_order": list(WALL_ORDER), "channels": CHANNEL_TOPOLOGY}
                )
            else:
                channels = fleet_obj.channels()
                _print_json(
                    {
                        "controllers": list(fleet_obj.names()),
                        "wall_order": list(WALL_ORDER),
                        "channels": channels,
                    }
                )
        elif args.command == "wall":
            if fleet_obj is None:
                raise ValueError("'wall' requires fleet mode (omit --host).")
            import columns

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
                    pal_right=(
                        args.pal_right if args.pal_right is not None else args.pal
                    ),
                )
            logger.info("wall %s: %s", args.mode, json.dumps(result, default=str))
        elif args.command == "atmosphere":
            if fleet_obj is None:
                raise ValueError("'atmosphere' requires fleet mode (omit --host).")
            import atmospheres

            if args.name == "list":
                logger.info("atmospheres:\n%s", atmospheres.atmosphere_menu_text())
            else:
                result = atmospheres.apply_atmosphere(fleet_obj, args.name)
                logger.info(
                    "atmosphere %s: %s", args.name, json.dumps(result, default=str)
                )
    except KeyboardInterrupt:
        logger.info("Stopped.")
        return 130
    except (RuntimeError, ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
