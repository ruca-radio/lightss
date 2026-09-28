#!/usr/bin/env python3
"""Browser GUI for the bedroom Wi-Fi LED controller."""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import copy
import dataclasses
import functools
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping
import uuid

import actions
import light_gui_html

try:
    import lightctl
except ImportError:  # compatibility with the reworked standalone filename
    import lightsctl as lightctl  # type: ignore[no-redef]
import mood_orchestrator
import music_recognizer
from light_gui_html import HTML_TEMPLATE

logger = logging.getLogger("light_gui")

DEFAULT_AI_MODEL = "gpt-5.4-nano"
MAX_JSON_BODY_BYTES = 8 * 1024 * 1024
MAX_VISION_BODY_BYTES = 10 * 1024 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 4 * 1024 * 1024
STATE_CACHE_TTL_S = 0.6
INFO_CACHE_TTL_S = 5.0
SSE_INTERVAL_S = 0.75
SYSTEM_PROMPT_OVERRIDE_MAX_CHARS = 20_000

_ai_execution_lock = threading.Lock()
_lighting_apply_lock = threading.RLock()

AI_ACTIONS = actions.ai_action_names()
CLIENT_ACTIONS = actions.CLIENT_ACTIONS

# Wall-wide composers + per-channel control; routed through columns.py (fleet mode only).
WALL_ACTIONS = {
    "wall_span",
    "wall_mirror",
    "wall_chase",
    "wall_versus",
    "set_channel",
    "atmosphere",
}

API_GET_PATHS = {
    "/",
    "/tv",
    "/wled-logo.png",
    "/api/now-playing",
    "/api/recognize",
    "/api/match-lights",
    "/api/state",
    "/api/suggestions",
    "/api/events",
    "/api/ai/jobs",
    "/api/settings",
    "/api/system-prompt",
    "/api/firetv",
    "/api/music-director",
    "/api/tv-trivia",
}

API_POST_PATHS = {
    "/api/action",
    "/api/ai",
    "/api/ai_vision",
    "/api/recognize",
    "/api/match-lights",
    "/api/mood/sample",
    "/api/mood/control",
    "/api/settings",
    "/api/settings/reload",
    "/api/system-prompt",
    "/api/controllers/verify",
    "/api/ai/test",
    "/api/firetv",
    "/api/music-director",
}

API_HEAD_PATHS = {
    "/",
    "/wled-logo.png",
    "/api/now-playing",
    "/api/recognize",
    "/api/match-lights",
    "/api/state",
    "/api/suggestions",
    "/api/mood/sample",
    "/api/mood/control",
    "/api/ai_vision",
}


def ai_action_reference() -> str:
    return (
        "Available AI actions:\n"
        "- on/off: direct power control.\n"
        "- brightness: global brightness 0-255.\n"
        "- color: primary RGBW channels red/green/blue/white 0-255. Optionally set secondary color "
        "(red2/green2/blue2/white2) and tertiary color (red3/green3/blue3/white3) for effects that use "
        "multiple color slots — check 'Safe effect parameter hints' in the device snapshot.\n"
        "- temperature: native WLED CCT in Kelvin 1900-10091.\n"
        "- effect: safe WLED effect id with speed 0-255, optional intensity 0-255, palette id 0-N "
        "(use palette name from 'All palettes' list in snapshot), and c1/c2/c3 0-255 (meanings per "
        "effect listed in 'Safe effect parameter hints'). Always include primary color; add secondary/"
        "tertiary colors when the hint shows 'colors 1+2' or 'colors 1+2+3'.\n"
        "- palette: WLED palette id 0-N for the active segment. Choose by name from the palette list.\n"
        "- scene/random/preset/playlist: named scene, random safe scene, WLED preset by id "
        "(choose from 'Saved WLED presets' in snapshot), or playlist id.\n"
        "- save_preset/delete_preset: save current state as a native WLED preset with preset_id and optional name, or delete a preset by preset_id.\n"
        "- nightlight: WLED nightlight on/off, duration minutes, mode, and target brightness.\n"
        "- udp_sync: WLED UDP send/receive toggles; preferred wall topology is right=sender, left=receiver.\n"
        "- native_audio_reactive: device AudioReactive usermod on/off when installed.\n"
        "- segment_options: active segment on/off, freeze, reverse, mirror, brightness, CCT, grouping, spacing, offset.\n"
        "- mode1_start/mode1_stop: desktop/browser microphone reactive beat mode.\n"
        "- fade_off/cycle_start/cycle_stop/sunrise_start/sunrise_stop: local timer automations.\n"
        "- save_scene/delete_scene: local custom scene management.\n"
        "- schedule_add/schedule_remove: local schedule management with time HH:MM and action on/off/scene.\n"
        "- music_detect/music_match: now-playing media lookup and metadata-matched lighting.\n"
        "- wall_span/wall_mirror/wall_chase: wall-wide composers across all four columns "
        "(effect + optional palette; mirror flips the left pair, chase staggers offsets).\n"
        "- wall_versus: left pair vs right pair with fx_left/fx_right and optional pal_left/pal_right.\n"
        "- set_channel: one channel only (channel: far-left, middle-left, middle-right, or far-right) "
        "with optional effect/palette/colors.\n"
        "- Every action accepts an optional target: all (default), a controller name, or a channel name.\n"
        "One-shot examples:\n"
        "- 'soft ocean for 20 minutes then off' -> scene ocean, nightlight on duration 20 target brightness 0.\n"
        "- 'make it pulse with the song' -> safe color/effect setup plus mode1_start.\n"
        "- 'use the device audio reactive mode' -> native_audio_reactive enabled true.\n"
        "- 'wake me up over 30 minutes' -> sunrise_start minutes 30.\n"
        "- 'sync the wall' -> configure the right controller to send and the left controller to receive.\n"
        "- 'ocean chase two-tone blue and teal' -> effect Chase with blue primary and teal secondary color.\n"
    )


def _parse_fxdata_hints(fxdata: list) -> dict[int, str]:
    """Extract color-slot and parameter hints for safe effects from WLED fxdata."""
    hints: dict[int, str] = {}
    for effect_id, name in lightctl.SAFE_EFFECTS.items():
        if effect_id >= len(fxdata):
            continue
        entry = str(fxdata[effect_id])
        # Format: name@sx,ix,c1,c2,c3;col0,col1,col2;pal;flags
        at_split = entry.split("@", 1)
        rest = at_split[1] if len(at_split) > 1 else ""
        parts = rest.split(";")
        params = [p.strip() for p in parts[0].split(",")] if parts else []
        col_labels = [c.strip() for c in parts[1].split(",")] if len(parts) > 1 else []

        sx_label = params[0] if len(params) > 0 and params[0] not in ("", "!") else None
        ix_label = params[1] if len(params) > 1 and params[1] not in ("", "!") else None
        c1_label = params[2] if len(params) > 2 and params[2] not in ("", "!") else None
        c2_label = params[3] if len(params) > 3 and params[3] not in ("", "!") else None
        c3_label = params[4] if len(params) > 4 and params[4] not in ("", "!") else None

        col2_used = len(col_labels) > 1 and col_labels[1]
        col3_used = len(col_labels) > 2 and col_labels[2]

        hint_parts = []
        if col2_used and col3_used:
            hint_parts.append("colors 1+2+3")
        elif col2_used:
            hint_parts.append("colors 1+2")
        for label, key in (
            (c1_label, "c1"),
            (c2_label, "c2"),
            (c3_label, "c3"),
            (sx_label, "sx"),
            (ix_label, "ix"),
        ):
            if label:
                hint_parts.append(f"{key}={label}")
        if hint_parts:
            hints[effect_id] = "; ".join(hint_parts)
    return hints


def catalog_text_for_prompt(effects: list, fxdata: list) -> str:
    """Full effect catalog grouped by mood (via atmospheres.py)."""
    import atmospheres  # lazy: atmospheres imports columns

    return atmospheres.catalog_text(list(effects), list(fxdata))


_NUMBER_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
}


def _segment_sort_key(seg_id: object) -> tuple[int, object]:
    text = str(seg_id)
    return (0, int(text)) if text.isdigit() else (1, text)


def _format_number(value: object) -> str:
    """Compact number rendering: 30.0 -> '30', 2.5 -> '2.5'."""
    try:
        return f"{float(value):g}"
    except TypeError, ValueError:
        return str(value)


def topology_text(topology: dict) -> str:
    """Deterministic natural-language header describing the physical installation."""
    installation = (
        topology.get("installation")
        if isinstance(topology.get("installation"), dict)
        else {}
    )
    wall_order = (
        installation.get("wall_order")
        if isinstance(installation.get("wall_order"), list)
        else []
    )
    count = _NUMBER_WORDS.get(len(wall_order), str(len(wall_order)))
    lines = [
        "Physical installation:",
        (
            f"{count} {installation.get('orientation', '?')} columns "
            f"(wall order: {', '.join(str(channel) for channel in wall_order)}), "
            f"spaced {_format_number(installation.get('spacing_inches', '?'))} inches apart; "
            f"each column is {_format_number(installation.get('column_length_m', '?'))} m at "
            f"{installation.get('pixels_per_meter', '?')} addressable pixels/m "
            f"({installation.get('visible_leds_per_meter', '?')} visible LEDs/m), "
            f"LED 0 at the {installation.get('pixel_zero', '?')}, "
            f"color order {installation.get('color_order', '?')}."
        ),
    ]
    controllers = (
        topology.get("controllers")
        if isinstance(topology.get("controllers"), list)
        else []
    )
    for controller in controllers:
        if not isinstance(controller, dict):
            continue
        segments = (
            controller.get("segments")
            if isinstance(controller.get("segments"), dict)
            else {}
        )
        segment_texts = []
        for seg_id, segment in sorted(
            segments.items(), key=lambda item: _segment_sort_key(item[0])
        ):
            if not isinstance(segment, dict):
                continue
            details = [f"channel {segment.get('channel', '?')}"]
            if segment.get("gpio") is not None:
                details.append(f"gpio {segment['gpio']}")
            if segment.get("pixels") is not None:
                details.append(f"{segment['pixels']} addressable pixels")
            segment_texts.append(f"segment {seg_id} = {', '.join(details)}")
        lines.append(
            f"Controller '{controller.get('name', '?')}' ({controller.get('host', '?')}): "
            + ("; ".join(segment_texts) if segment_texts else "no segments configured")
        )
    return "\n".join(lines)


def device_snapshot_text(snapshot: dict | None, include_catalog: bool = True) -> str:
    if not snapshot:
        return "Current WLED device snapshot: unavailable."
    topology = snapshot.get("topology") if isinstance(snapshot, dict) else None
    devices = snapshot.get("devices") if isinstance(snapshot, dict) else None
    if isinstance(topology, dict) and isinstance(devices, dict):
        # Fleet envelope ({"topology": ..., "devices": ...}) — topology header,
        # then each controller; include the (identical) effect catalog only once.
        parts = [topology_text(topology)]
        for name, sub in devices.items():
            if isinstance(sub, dict) and "error" in sub:
                parts.append(
                    f"=== Controller '{name}' ===\nSnapshot unavailable: {sub['error']}"
                )
            else:
                parts.append(
                    f"=== Controller '{name}' ===\n{device_snapshot_text(sub, include_catalog=False)}"
                )
        for sub in devices.values():
            effects = sub.get("effects") if isinstance(sub, dict) else None
            fxdata = sub.get("fxdata") if isinstance(sub, dict) else None
            if isinstance(effects, list) and effects:
                parts.append(
                    catalog_text_for_prompt(
                        effects, fxdata if isinstance(fxdata, list) else []
                    )
                )
                break
        return "\n\n".join(parts)
    if "state" not in snapshot and all(
        isinstance(value, dict) for value in snapshot.values()
    ):
        # Legacy fleet snapshot ({controller_name: snapshot}) — render each controller,
        # but include the (identical) effect catalog only once.
        parts = [
            f"=== Controller '{name}' ===\n{device_snapshot_text(sub, include_catalog=False)}"
            for name, sub in snapshot.items()
        ]
        for sub in snapshot.values():
            effects = sub.get("effects") if isinstance(sub, dict) else None
            fxdata = sub.get("fxdata") if isinstance(sub, dict) else None
            if isinstance(effects, list) and effects:
                parts.append(
                    catalog_text_for_prompt(
                        effects, fxdata if isinstance(fxdata, list) else []
                    )
                )
                break
        return "\n\n".join(parts)
    state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
    info = snapshot.get("info") if isinstance(snapshot.get("info"), dict) else {}
    config = snapshot.get("config") if isinstance(snapshot.get("config"), dict) else {}
    effects = (
        snapshot.get("effects") if isinstance(snapshot.get("effects"), list) else []
    )
    palettes = (
        snapshot.get("palettes") if isinstance(snapshot.get("palettes"), list) else []
    )
    fxdata = snapshot.get("fxdata") if isinstance(snapshot.get("fxdata"), list) else []
    presets_raw = (
        snapshot.get("presets") if isinstance(snapshot.get("presets"), dict) else {}
    )
    leds = info.get("leds", {}) if isinstance(info.get("leds"), dict) else {}
    light_cfg = config.get("light", {}) if isinstance(config.get("light"), dict) else {}
    transition_cfg = (
        light_cfg.get("tr", {}) if isinstance(light_cfg.get("tr"), dict) else {}
    )
    nightlight_cfg = (
        light_cfg.get("nl", {}) if isinstance(light_cfg.get("nl"), dict) else {}
    )
    usermods = config.get("um", {}) if isinstance(config.get("um"), dict) else {}
    audio_reactive_cfg = (
        usermods.get("AudioReactive", {})
        if isinstance(usermods.get("AudioReactive"), dict)
        else {}
    )
    sync_cfg = (
        config.get("if", {}).get("sync", {})
        if isinstance(config.get("if"), dict)
        else {}
    )
    live_cfg = (
        config.get("if", {}).get("live", {})
        if isinstance(config.get("if"), dict)
        else {}
    )

    lines = [
        "Current WLED device snapshot:",
        f"Device: {info.get('name', 'unknown')} WLED {info.get('ver', '?')} at {info.get('ip', '?')}",
        f"LEDs: count={leds.get('count', '?')}, rgbw={leds.get('rgbw', '?')}, cct={leds.get('cct', '?')}, maxseg={leds.get('maxseg', '?')}",
        f"State: power={'on' if state.get('on') else 'off'}, bri={state.get('bri', '?')}, transition={state.get('transition', '?')}, preset={state.get('ps', '?')}, playlist={state.get('pl', '?')}",
    ]
    for segment in state.get("seg", []):
        lines.append(
            "Segment "
            f"{segment.get('id', '?')}: start={segment.get('start', '?')}, "
            f"stop={segment.get('stop', '?')}, on={segment.get('on', '?')}, "
            f"bri={segment.get('bri', '?')}, fx={segment.get('fx', '?')}, "
            f"pal={segment.get('pal', '?')}, colors={segment.get('col', [])}, "
            f"reverse={segment.get('rev', '?')}, mirror={segment.get('mi', '?')}"
        )
    lines.extend(
        [
            f"Nightlight state: {state.get('nl', {})}",
            f"UDP sync state: {state.get('udpn', {})}",
            f"AudioReactive state: {state.get('AudioReactive', {})}; config: {audio_reactive_cfg}",
            f"Config defaults: transition={transition_cfg}, nightlight={nightlight_cfg}",
            f"Sync config: {sync_cfg}; live config: {live_cfg}",
            f"Effects available: {len(effects)} total — full catalog below (🚫 = forbidden).",
            "All palettes (use id number when setting palette):\n  "
            + "\n  ".join(f"{i}: {name}" for i, name in enumerate(palettes)),
        ]
    )
    if effects and include_catalog:
        lines.append(catalog_text_for_prompt(effects, fxdata))
    fx_hints = _parse_fxdata_hints(fxdata)
    if fx_hints:
        hint_lines = "\n  ".join(
            f"{eid} {lightctl.SAFE_EFFECTS[eid]}: {hint}"
            for eid, hint in fx_hints.items()
            if eid in lightctl.SAFE_EFFECTS
        )
        lines.append(
            f"Safe effect parameter hints (colors/c1/c2/c3/sx/ix meanings):\n  {hint_lines}"
        )
    if presets_raw:
        preset_entries = sorted(
            (
                (k, v)
                for k, v in presets_raw.items()
                if isinstance(v, dict) and v.get("n")
            ),
            key=lambda x: int(x[0]) if str(x[0]).isdigit() else 9999,
        )
        if preset_entries:
            lines.append(
                "Saved WLED presets: "
                + ", ".join(f"{pid}={p['n']}" for pid, p in preset_entries)
            )
    return "\n".join(lines)


@functools.lru_cache(maxsize=1)
def _render_html_cached() -> str:
    effect_options = "\n            ".join(
        f'<option value="{effect_id}">{name}</option>'
        for effect_id, name in lightctl.SAFE_EFFECTS.items()
    )
    effect_name_map = json.dumps({str(k): v for k, v in lightctl.SAFE_EFFECTS.items()})
    return (
        HTML_TEMPLATE.replace("__SAFE_EFFECT_OPTIONS__", effect_options)
        .replace("__BEAT_EFFECTS__", json.dumps(list(lightctl.SAFE_EFFECTS)))
        .replace("__EFFECT_NAME_MAP__", effect_name_map)
    )


def render_html() -> str:
    return _render_html_cached()


def safe_effect_prompt() -> str:
    return actions.safe_effect_prompt()


def _atmosphere_menu() -> str:
    import atmospheres  # lazy: atmospheres imports columns

    return atmospheres.atmosphere_menu_text()


def system_knowledge_prompt() -> str:
    """Structured-plan fallback prompt.

    Normal AI control uses ``ai_chat.TOOL_CHAT_SYSTEM_PROMPT`` and native tool
    calling. This prompt exists only for providers that cannot call tools.
    """

    return f"""You are the fallback lighting planner for a four-column WLED wall.
Return only the JSON object required by the supplied response schema.

The runtime topology and device snapshot are authoritative. Preserve settings the
user did not request. Use physical wall order far-left, middle-left, middle-right,
far-right; LED 0 is at the bottom of every column. Never change segment bounds,
GPIO assignments, or strip direction unless explicitly asked.

Use only action names listed by the schema. Prefer one action, but use multiple
ordered actions when the request genuinely needs them. Exact user numbers are
mandatory. Every effect, palette, and preset identifier must exist in the snapshot.
Never use effects marked forbidden or whose name includes blink, strobe, flash,
lightning, fireworks, or sparkle. Avoid unsupported 2D effects.

Available operations:\n{ai_action_reference()}

Available curated atmospheres:\n{_atmosphere_menu()}

The response field is a 100-250 character marquee. Confirmations must truthfully
state what the actions will do. If the snapshot cannot validate a requested ID,
do not guess; choose a validated safe alternative or return no unsafe operation.
"""


def parse_playerctl_metadata(output: str) -> dict[str, str]:
    lines = [line.strip() for line in output.splitlines()]
    while len(lines) < 5:
        lines.append("")
    return {
        "player": lines[0],
        "artist": lines[1],
        "title": lines[2],
        "album": lines[3],
        "status": lines[4],
    }


def parse_mpris_metadata_output(output: str) -> dict[str, str]:
    def extract_string(key: str) -> str:
        match = re.search(rf"'{re.escape(key)}': <'([^']*)'>", output)
        return match.group(1) if match else ""

    def extract_first_array_string(key: str) -> str:
        match = re.search(rf"'{re.escape(key)}': <\['([^']*)'", output)
        return match.group(1) if match else ""

    return {
        "player": "",
        "artist": extract_first_array_string("xesam:artist"),
        "title": extract_string("xesam:title"),
        "album": extract_string("xesam:album"),
        "status": "",
    }


def parse_mpris_status_output(output: str) -> str:
    match = re.search(r"<'([^']+)'", output)
    return match.group(1) if match else ""


def now_playing_text(now_playing: dict[str, str] | None) -> str:
    if not now_playing:
        return "No song detected."
    artist = now_playing.get("artist", "").strip()
    title = now_playing.get("title", "").strip()
    album = now_playing.get("album", "").strip()
    status = now_playing.get("status", "").strip()
    if not title and not artist:
        return "No song detected."
    name = f"{artist} - {title}" if artist and title else title or artist
    details = ", ".join(part for part in (album, status) if part)
    return f"{name} ({details})" if details else name


def get_now_playing_playerctl() -> dict[str, str] | None:
    if not shutil.which("playerctl"):
        return None
    try:
        metadata = subprocess.run(
            [
                "playerctl",
                "metadata",
                "--format",
                "{{playerName}}\n{{artist}}\n{{title}}\n{{album}}",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=2,
        ).stdout
        status = subprocess.run(
            ["playerctl", "status"],
            check=False,
            text=True,
            capture_output=True,
            timeout=2,
        ).stdout.strip()
    except subprocess.SubprocessError, OSError:
        return None
    parsed = parse_playerctl_metadata(metadata + "\n" + status)
    if not parsed.get("title") and not parsed.get("artist"):
        return None
    if parsed.get("status", "").strip().lower() != "playing":
        return None
    return parsed


def list_mpris_players() -> list[str]:
    if not shutil.which("gdbus"):
        return []
    try:
        output = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.ListNames",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=2,
        ).stdout
    except subprocess.SubprocessError, OSError:
        return []
    return sorted(set(re.findall(r"org\.mpris\.MediaPlayer2\.[A-Za-z0-9_.-]+", output)))


def get_now_playing_mpris() -> dict[str, str] | None:
    for player in list_mpris_players():
        try:
            metadata_output = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    player,
                    "--object-path",
                    "/org/mpris/MediaPlayer2",
                    "--method",
                    "org.freedesktop.DBus.Properties.Get",
                    "org.mpris.MediaPlayer2.Player",
                    "Metadata",
                ],
                check=True,
                text=True,
                capture_output=True,
                timeout=2,
            ).stdout
            status_output = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    player,
                    "--object-path",
                    "/org/mpris/MediaPlayer2",
                    "--method",
                    "org.freedesktop.DBus.Properties.Get",
                    "org.mpris.MediaPlayer2.Player",
                    "PlaybackStatus",
                ],
                check=False,
                text=True,
                capture_output=True,
                timeout=2,
            ).stdout
        except subprocess.SubprocessError, OSError:
            continue
        parsed = parse_mpris_metadata_output(metadata_output)
        parsed["player"] = player.rsplit(".", 1)[-1]
        parsed["status"] = parse_mpris_status_output(status_output)
        if not (parsed.get("title") or parsed.get("artist")):
            continue
        if parsed.get("status", "").strip().lower() != "playing":
            continue
        return parsed
    return None


def get_now_playing() -> dict[str, str] | None:
    """Return normalized playing metadata from the shared recognizer first.

    ``music_recognizer.now_playing_mpris`` is also used by Music Director, so this
    keeps the GUI and director from disagreeing about the active player. Legacy
    playerctl/gdbus readers remain as a compatibility fallback.
    """

    shared = getattr(music_recognizer, "now_playing_mpris", None)
    if callable(shared):
        try:
            value = shared()
            if isinstance(value, Mapping):
                result = {
                    "player": str(value.get("player") or value.get("source") or ""),
                    "artist": str(value.get("artist") or ""),
                    "title": str(value.get("title") or ""),
                    "album": str(value.get("album") or ""),
                    "genre": str(value.get("genre") or ""),
                    "status": str(
                        value.get("status") or value.get("playback_status") or "Playing"
                    ),
                }
                if (result["artist"] or result["title"]) and result[
                    "status"
                ].casefold() == "playing":
                    return result
        except Exception as exc:
            logger.debug("Shared MPRIS lookup failed: %s", exc)
    return get_now_playing_playerctl() or get_now_playing_mpris()


def get_now_playing_with_shazam_fallback(
    use_shazam: bool = False,
) -> dict[str, str] | None:
    """Return now-playing info, optionally falling back to microphone recognition."""
    result = get_now_playing()
    if result:
        return result
    if not use_shazam:
        return None
    if not music_recognizer.can_identify_song():
        logger.warning("Shazam fallback requested but music_recognizer is unavailable")
        return None
    if not music_recognizer.is_available():
        # server mic path needs sounddevice; bytes path does not
        logger.warning(
            "Shazam mic fallback requires sounddevice (browser mic capture works without)"
        )
        return None
    try:
        shazam_result = music_recognizer.recognize_sync()
        if shazam_result:
            return {
                "title": shazam_result.get("title", ""),
                "artist": shazam_result.get("artist", ""),
                "album": shazam_result.get("album", ""),
                "status": "Playing",
                "source": "shazam",
            }
    except Exception:
        logger.exception("Shazam microphone recognition failed")
    return None


def _decode_base64_blob(value: Any, *, max_bytes: int, label: str) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required.")
    encoded = value.strip()
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header.casefold():
            raise ValueError(f"{label} data URL must use base64 encoding.")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{label} is not valid base64.") from exc
    if not decoded:
        raise ValueError(f"{label} is empty.")
    if len(decoded) > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes} byte decoded limit.")
    return decoded


# Sentinel so callers can explicitly pass now_playing=None meaning "I already tried to identify via mic and got nothing"
_NO_SONG = object()


def match_lights_to_song(
    client: Any, now_playing: dict[str, str] | None | object = _NO_SONG
) -> dict[str, Any]:
    """Detect the active song and execute one serialized tool-calling request."""

    if now_playing is _NO_SONG:
        now_playing = get_now_playing_with_shazam_fallback(use_shazam=True)
    if not isinstance(now_playing, dict) or not (
        now_playing.get("title") or now_playing.get("artist")
    ):
        return {
            "ok": False,
            "message": "No music detected. Try playing a song or using the microphone listen button.",
            "response": "",
            "confirmations": [],
            "client_actions": [],
            "now_playing": None,
        }
    genre = str(now_playing.get("genre") or "").strip()
    prompt = (
        f"The song '{now_playing.get('title', 'Unknown')}' by {now_playing.get('artist', 'Unknown')}"
        + (f" (genre: {genre})" if genre else "")
        + " is currently playing. Create a safe, theatrical light show matching its mood and energy. "
        "Prefer hardware audio-reactive looks, and finish with a fun marquee line tied to the song."
    )
    if not _ai_execution_lock.acquire(blocking=False):
        return {
            "ok": False,
            "message": "Another AI lighting request is already running.",
            "response": "",
            "confirmations": [],
            "client_actions": [],
            "now_playing": now_playing,
        }
    try:
        import ai_chat

        result = ai_chat.run_chat(
            client,
            prompt,
            system_prompt=tool_system_prompt_in_use(),
            settings=ai_settings(),
            context_text=ai_context_text(client, now_playing) if client else None,
        )
        log = list(result.get("log") or [])
        text = str(result.get("text") or "Matched the lights to the song.")[:300]
        return {
            "ok": True,
            "message": f"AI matched the song ({len(log)} tool call(s)).",
            "response": text,
            "confirmations": log[:8] or [text[:120]],
            "client_actions": [],
            "events": result.get("events", []),
            "now_playing": now_playing,
        }
    except Exception as exc:
        logger.exception("Match lights failed")
        return {
            "ok": False,
            "message": str(exc),
            "response": "",
            "confirmations": [],
            "client_actions": [],
            "now_playing": now_playing,
        }
    finally:
        _ai_execution_lock.release()


def generate_mood_for_song(
    client: lightctl.LightClient,
    song: dict[str, str],
) -> lightctl.WledPayload:
    """Ask the AI to create a smooth WLED mood for a recognized song."""
    title = song.get("title", "Unknown")
    artist = song.get("artist", "Unknown")
    genre = song.get("genre", "")
    prompt = (
        f"The song '{title}' by {artist}"
        + (f" (genre: {genre})" if genre else "")
        + " is playing. Design one smooth, non-jarring WLED mood that matches its energy and style. "
        "Choose a safe effect, palette by name, primary and secondary colors, speed, intensity, and brightness. "
        "The transition must feel gentle — avoid sudden brightness jumps or strobe-like effects. "
        "Do not include mode1_start; beat reaction is handled separately."
    )
    snapshot = _device_snapshot(client)
    plan = call_openai_for_plan(prompt, song, snapshot)
    effect_names, palette_count = _catalog_from_snapshot(snapshot)
    actions = plan.get("actions", [])
    # Find the first action that actually produces a WLED payload.
    for action in actions:
        payload = payload_for_ai_action(
            action, effect_names=effect_names, palette_count=palette_count
        )
        if payload:
            return payload
    raise ValueError("AI did not return a usable WLED payload for mood generation")


# ---------------------------------------------------------------------------
# AI provider settings
# ---------------------------------------------------------------------------

DEFAULT_AI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_AI_PROVIDER = "openai"
DEFAULT_AI_KEY_ENV = "OPENAI_API_KEY"
# Providers that do not require an API key (no Authorization header is sent
# when the configured key env var is unset).
_KEYLESS_PROVIDERS = {"ollama"}


def ai_settings(config: dict | None = None) -> dict:
    """Resolve non-secret AI runtime settings.

    Precedence is config ``ai`` values, then environment variables, then defaults.
    API key values are never returned; only the environment variable name and a
    boolean indicating whether it is populated are exposed.
    """

    if config is None:
        config = lightctl.load_config()
    ai_cfg = config.get("ai") if isinstance(config.get("ai"), dict) else {}

    def _pick(key: str, env_names: tuple[str, ...], default: str) -> str:
        value = ai_cfg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        for env_name in env_names:
            env_value = os.environ.get(env_name, "").strip()
            if env_value:
                return env_value
        return default

    provider = _pick("provider", (), DEFAULT_AI_PROVIDER).casefold()
    base_url = _pick("base_url", ("OPENAI_BASE_URL",), DEFAULT_AI_BASE_URL).rstrip("/")
    model = _pick("model", ("LIGHT_AI_MODEL", "OPENAI_MODEL"), DEFAULT_AI_MODEL)
    vision_model = _pick(
        "vision_model", ("LIGHT_VISION_MODEL", "OPENAI_VISION_MODEL"), model
    )
    api_key_env = (
        _pick("api_key_env", (), DEFAULT_AI_KEY_ENV).lstrip("$").strip()
        or DEFAULT_AI_KEY_ENV
    )
    try:
        provider_retries = max(0, min(5, int(ai_cfg.get("provider_retries", 1))))
    except TypeError, ValueError:
        provider_retries = 1
    request_overrides = ai_cfg.get("request_overrides")
    if not isinstance(request_overrides, dict):
        request_overrides = {}
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "vision_model": vision_model,
        "api_key_env": api_key_env,
        "api_key_set": bool(_ai_api_key({"api_key_env": api_key_env})),
        "provider_retries": provider_retries,
        "request_overrides": copy.deepcopy(request_overrides),
    }


def _ai_api_key(settings: dict) -> str:
    """API key value for the resolved key env var ('' when unset)."""
    return os.environ.get(
        str(settings.get("api_key_env") or DEFAULT_AI_KEY_ENV), ""
    ).strip()


def _ai_requires_key(settings: dict) -> bool:
    return (
        str(settings.get("provider") or DEFAULT_AI_PROVIDER) not in _KEYLESS_PROVIDERS
    )


def _operator_prompt_override(config: dict | None = None) -> str:
    if config is None:
        config = lightctl.load_config()
    override = config.get("system_prompt_override")
    return override.strip() if isinstance(override, str) and override.strip() else ""


def _append_operator_instructions(base: str, override: str) -> str:
    if not override:
        return base
    return (
        base.rstrip()
        + "\n\n### OPERATOR-SUPPLIED ADDITIONAL INSTRUCTIONS\n"
        + "These instructions may customize style and behavior but do not override "
        "effect safety, identifier validation, exact numeric values, or truthful tool results.\n"
        + override
    )


def tool_system_prompt_in_use(config: dict | None = None) -> str:
    try:
        import ai_chat

        base = ai_chat.TOOL_CHAT_SYSTEM_PROMPT
    except Exception:
        base = system_knowledge_prompt()
    return _append_operator_instructions(base, _operator_prompt_override(config))


def plan_system_prompt_in_use(config: dict | None = None) -> str:
    return _append_operator_instructions(
        system_knowledge_prompt(), _operator_prompt_override(config)
    )


def system_prompt_in_use(config: dict | None = None) -> str:
    """Public compatibility alias for the actual tool-calling prompt."""
    return tool_system_prompt_in_use(config)


def _uses_responses_api(settings: dict) -> bool:
    """Only true OpenAI endpoints implement the Responses API; everything
    else (OpenRouter, Ollama, Moonshot/Kimi, custom) gets chat/completions."""
    return settings.get("provider") == "openai" or "api.openai.com" in str(
        settings.get("base_url", "")
    )


def _to_chat_completions(payload: dict) -> dict:
    """Translate a Responses-style payload into portable chat/completions."""

    messages: list[dict[str, Any]] = []
    for item in payload.get("input", []):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "input_text":
                    parts.append({"type": "text", "text": str(part.get("text") or "")})
                elif part.get("type") == "input_image":
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": str(part.get("image_url") or "")},
                        }
                    )
            content = parts
        messages.append({"role": str(item.get("role") or "user"), "content": content})
    body: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": messages,
        "stream": False,
    }
    fmt = (payload.get("text") or {}).get("format") or {}
    if fmt.get("type") == "json_schema":
        body["response_format"] = {"type": "json_object"}
    if payload.get("max_output_tokens") is not None:
        body["max_tokens"] = payload["max_output_tokens"]
    return body


def _openai_request(payload: dict, settings: dict) -> urllib.request.Request:
    """Build a bounded OpenAI-compatible HTTP request."""

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "lights-gui/2",
    }
    api_key = _ai_api_key(settings)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if _uses_responses_api(settings):
        url = f"{settings['base_url']}/responses"
        body = payload
    else:
        url = f"{settings['base_url']}/chat/completions"
        body = _to_chat_completions(payload)
        overrides = settings.get("request_overrides")
        if isinstance(overrides, dict):
            for key, value in overrides.items():
                if key not in {"model", "messages", "tools", "tool_choice", "stream"}:
                    body[key] = value
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return urllib.request.Request(url, data=encoded, headers=headers, method="POST")


def _provider_lock_context():
    try:
        import ai_chat

        return ai_chat.provider_lock
    except Exception:
        return contextlib.nullcontext()


def _provider_json(
    request: urllib.request.Request,
    *,
    timeout: float,
    retries: int = 1,
    max_bytes: int = MAX_PROVIDER_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Execute an AI provider request with serialization, retries, and size caps."""

    retryable = {408, 425, 429, 500, 502, 503, 504}
    last_error: BaseException | None = None
    with _provider_lock_context():
        for attempt in range(max(0, retries) + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > max_bytes:
                        raise ValueError("AI provider response is too large.")
                    raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise ValueError("AI provider response is too large.")
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("AI provider returned a non-object JSON response.")
                return data
            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read(1000).decode("utf-8", errors="replace")
                finally:
                    exc.close()
                last_error = ValueError(f"AI provider HTTP {exc.code}: {detail}")
                if exc.code not in retryable or attempt >= retries:
                    raise last_error from exc
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                last_error = exc
                if attempt >= retries:
                    raise ValueError(f"AI provider request failed: {exc}") from exc
            time.sleep(min(1.5, 0.2 * (2**attempt)))
    raise ValueError(f"AI provider request failed: {last_error}")


def build_openai_request(
    prompt: str,
    model: str | None = None,
    now_playing: dict[str, str] | None = None,
    device_snapshot: dict | None = None,
) -> dict:
    if model is None:
        model = ai_settings()["model"]
    parts: list[str] = []
    if now_playing:
        parts.append(f"Background audio now playing: {now_playing_text(now_playing)}")
    if device_snapshot:
        parts.append(device_snapshot_text(device_snapshot))
    parts.append(f"User request: {prompt}")
    return {
        "model": model,
        "input": [
            {"role": "system", "content": plan_system_prompt_in_use()},
            {"role": "user", "content": "\n\n".join(parts)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "light_actions",
                "schema": _legacy_plan_schema(),
            }
        },
        "max_output_tokens": 1800,
    }


def _legacy_plan_schema() -> dict[str, Any]:
    """Portable fallback schema; action-specific validation happens locally."""

    action_properties: dict[str, Any] = {
        "action": {"type": "string", "enum": AI_ACTIONS},
        "brightness": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "red": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "green": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "blue": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "white": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "red2": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "green2": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "blue2": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "white2": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "red3": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "green3": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "blue3": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "white3": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "kelvin": {"type": ["integer", "null"], "minimum": 1900, "maximum": 10091},
        "effect": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "speed": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "intensity": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "palette": {"type": ["integer", "null"], "minimum": 0, "maximum": 65535},
        "c1": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "c2": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "c3": {"type": ["integer", "null"], "minimum": 0, "maximum": 31},
        "scene": {"type": ["string", "null"]},
        "transition": {"type": ["integer", "null"], "minimum": 0, "maximum": 6_553_500},
        "preset_id": {"type": ["integer", "null"], "minimum": 1, "maximum": 250},
        "playlist_id": {"type": ["integer", "null"], "minimum": 1, "maximum": 250},
        "name": {"type": ["string", "null"]},
        "minutes": {"type": ["number", "null"], "minimum": 0.1, "maximum": 1440},
        "interval": {"type": ["number", "null"], "minimum": 1, "maximum": 86400},
        "enabled": {"type": ["boolean", "null"]},
        "send": {"type": ["boolean", "null"]},
        "receive": {"type": ["boolean", "null"]},
        "mode": {"type": ["integer", "null"], "minimum": 0, "maximum": 3},
        "target_brightness": {
            "type": ["integer", "null"],
            "minimum": 0,
            "maximum": 255,
        },
        "schedule_time": {"type": ["string", "null"]},
        "schedule_action": {
            "type": ["string", "null"],
            "enum": ["on", "off", "scene", None],
        },
        "schedule_index": {"type": ["integer", "null"], "minimum": 0},
        "segment_on": {"type": ["boolean", "null"]},
        "freeze": {"type": ["boolean", "null"]},
        "reverse": {"type": ["boolean", "null"]},
        "mirror": {"type": ["boolean", "null"]},
        "grouping": {"type": ["integer", "null"], "minimum": 1, "maximum": 255},
        "spacing": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "offset": {"type": ["integer", "null"], "minimum": 0, "maximum": 65535},
        "target": {"type": ["string", "null"]},
        "channel": {"type": ["string", "null"]},
        "fx_left": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "fx_right": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
        "pal_left": {"type": ["integer", "null"], "minimum": 0, "maximum": 65535},
        "pal_right": {"type": ["integer", "null"], "minimum": 0, "maximum": 65535},
        "atmosphere": {"type": ["string", "null"]},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "response": {"type": "string"},
            "confirmations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string"},
            },
            "actions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": action_properties,
                    "required": list(action_properties),
                },
            },
        },
        "required": ["response", "confirmations", "actions"],
    }


def extract_response_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str) and response["output_text"].strip():
        return response["output_text"]
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if (
                isinstance(content, dict)
                and isinstance(content.get("text"), str)
                and content["text"].strip()
            ):
                return content["text"]
    for choice in response.get("choices", []):
        if not isinstance(choice, dict):
            continue
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            )
            if text.strip():
                return text
    raise ValueError("AI response did not include text output.")


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI response did not contain a JSON object.")
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("AI response root must be an object.")
    return data


def _require_action_field(action: Mapping[str, Any], name: str) -> Any:
    value = action.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"AI action {action.get('action')!r} requires {name}.")
    return value


def _validate_ai_action_shape(action: dict[str, Any]) -> dict[str, Any]:
    kind = str(action.get("action") or "").strip()
    if kind not in AI_ACTIONS:
        raise ValueError(f"Unknown AI action: {kind or '<missing>'}.")
    requirements = {
        "brightness": ("brightness",),
        "temperature": ("kelvin",),
        "effect": ("effect",),
        "scene": ("scene",),
        "preset": ("preset_id",),
        "playlist": ("playlist_id",),
        "palette": ("palette",),
        "save_preset": ("preset_id",),
        "delete_preset": ("preset_id",),
        "save_scene": ("scene",),
        "delete_scene": ("scene",),
        "schedule_remove": ("schedule_index",),
        "set_channel": ("channel",),
        "atmosphere": ("atmosphere",),
    }
    for field in requirements.get(kind, ()):
        _require_action_field(action, field)
    if kind == "color" and not any(
        action.get(k) is not None for k in ("red", "green", "blue", "white")
    ):
        raise ValueError("AI color action requires at least one RGBW channel.")
    if kind == "schedule_add":
        _require_action_field(action, "schedule_time")
        _require_action_field(action, "schedule_action")
        if action.get("schedule_action") == "scene":
            _require_action_field(action, "scene")
    if kind == "nightlight" and not any(
        action.get(k) is not None
        for k in ("enabled", "minutes", "mode", "target_brightness")
    ):
        raise ValueError("AI nightlight action requires at least one setting.")
    if (
        kind == "udp_sync"
        and action.get("send") is None
        and action.get("receive") is None
    ):
        raise ValueError("AI udp_sync action requires send and/or receive.")
    if kind == "native_audio_reactive" and action.get("enabled") is None:
        raise ValueError("AI native_audio_reactive action requires enabled.")
    return action


def parse_ai_plan(text: str) -> dict[str, Any]:
    data = _extract_json_object(text)
    actions_value = data.get("actions")
    if actions_value is None and isinstance(data.get("action"), dict):
        actions_value = [data["action"]]
    if not isinstance(actions_value, list) or not actions_value:
        raise ValueError("AI response did not include actions.")
    if len(actions_value) > 8:
        raise ValueError("AI response included too many actions (maximum 8).")
    validated_actions: list[dict[str, Any]] = []
    for index, raw_action in enumerate(actions_value):
        if not isinstance(raw_action, dict):
            raise ValueError(f"AI action {index + 1} must be an object.")
        action = {str(k): v for k, v in raw_action.items() if v is not None}
        validated_actions.append(_validate_ai_action_shape(action))

    response = " ".join(
        str(data.get("response") or "I applied a safe lighting change.").split()
    )[:300]
    confirmations_raw = data.get("confirmations")
    confirmations = []
    if isinstance(confirmations_raw, list):
        confirmations = [
            " ".join(str(item).split())[:240]
            for item in confirmations_raw
            if str(item).strip()
        ][:8]

    client_actions: list[Any] = []
    for action in validated_actions:
        kind = action["action"]
        if kind == "mode1_start":
            client_actions.append("startAudioReactive")
        elif kind == "mode1_stop":
            client_actions.append("stopAudioReactive")
        elif kind == "fade_off":
            client_actions.append(
                {"action": "fadeOff", "minutes": float(action.get("minutes", 30))}
            )
        elif kind == "cycle_start":
            client_actions.append(
                {"action": "startCycle", "interval": float(action.get("interval", 60))}
            )
        elif kind == "cycle_stop":
            client_actions.append({"action": "stopCycle"})
        elif kind == "sunrise_start":
            client_actions.append(
                {
                    "action": "startSunrise",
                    "minutes": float(action.get("minutes", 30)),
                    "brightness": int(action.get("target_brightness", 255)),
                }
            )
        elif kind == "sunrise_stop":
            client_actions.append({"action": "stopSunrise"})
        elif kind == "music_detect":
            client_actions.append({"action": "detectSong"})
        elif kind == "music_match":
            client_actions.append({"action": "matchLightsFromNowPlaying"})
    return {
        "response": response,
        "confirmations": confirmations,
        "client_actions": client_actions,
        "actions": validated_actions,
    }


def call_openai_for_plan(
    prompt: str,
    now_playing: dict[str, str] | None = None,
    device_snapshot: dict | None = None,
) -> dict[str, Any]:
    settings = ai_settings()
    if _ai_requires_key(settings) and not _ai_api_key(settings):
        raise ValueError(
            f"{settings['api_key_env']} is not set in the GUI server environment."
        )
    request = _openai_request(
        build_openai_request(prompt, settings["model"], now_playing, device_snapshot),
        settings,
    )
    data = _provider_json(
        request, timeout=120, retries=int(settings.get("provider_retries", 1))
    )
    return parse_ai_plan(extract_response_text(data))


def build_openai_vision_analysis_request(
    image_base64: str,
    model: str,
    device_snapshot: dict | None = None,
) -> dict[str, Any]:
    prompt = (
        "You are the vision scout for a bedroom WLED lighting director. "
        "Look at the webcam snapshot and describe only what the main lighting planner needs: "
        "ambient brightness, visible LED color/spill, wall or room color cast, glare, darkness, "
        "and any obvious mismatch between the current lights and the room. "
        "Do not output JSON or WLED actions. Return a concise room observation for the main model.\n\n"
        f"{device_snapshot_text(device_snapshot)}"
    )
    return {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{image_base64}",
                    },
                ],
            }
        ],
        "text": {"format": {"type": "text"}},
    }


def call_openai_vision_for_plan(image_base64: str, client: Any) -> dict[str, Any]:
    settings = ai_settings()
    if _ai_requires_key(settings) and not _ai_api_key(settings):
        raise ValueError(
            f"{settings['api_key_env']} is not set in the GUI server environment."
        )
    _decode_base64_blob(image_base64, max_bytes=8 * 1024 * 1024, label="Vision image")

    device_snapshot = _device_snapshot(client)
    body = build_openai_vision_analysis_request(
        image_base64, settings["vision_model"], device_snapshot
    )
    data = _provider_json(
        _openai_request(body, settings),
        timeout=45,
        retries=int(settings.get("provider_retries", 1)),
    )
    observation = " ".join(extract_response_text(data).split())[:4000]
    if not observation:
        raise ValueError("Vision response did not include a room observation.")
    plan = call_openai_for_plan(
        "Analyze this room-camera observation and set safe, smooth bedroom lighting.\n\n"
        f"Room observation: {observation}",
        now_playing=None,
        device_snapshot=device_snapshot,
    )
    return apply_ai_plan(client, plan)


def _segment_options_payload(data: dict[str, Any]) -> lightctl.WledPayload:
    seg: dict[str, Any] = {}
    field_map = {
        "segment_on": "on",
        "freeze": "frz",
        "reverse": "rev",
        "mirror": "mi",
        "brightness": "bri",
        "palette": "pal",
        "c1": "c1",
        "c2": "c2",
        "c3": "c3",
        "grouping": "grp",
        "spacing": "spc",
        "offset": "of",
    }
    for source, dest in field_map.items():
        if data.get(source) is not None:
            value = data[source]
            seg[dest] = value if isinstance(value, bool) else int(value)
    if data.get("kelvin") is not None:
        normalizer = getattr(lightctl, "normalize_cct", None)
        seg["cct"] = (
            normalizer(int(data["kelvin"]))
            if callable(normalizer)
            else lightctl.clamp_byte((int(data["kelvin"]) - 2000) * 255 / 4500)
        )
    payload: lightctl.WledPayload = {"seg": [seg]} if seg else {}
    if payload:
        lightctl.validate_wled_payload(payload)
    return payload


# ---------------------------------------------------------------------------
# Fleet targeting helpers
# ---------------------------------------------------------------------------


def _is_fleet(client: Any) -> bool:
    """Duck-test for fleet.LightFleet (avoids importing fleet at module level)."""
    return hasattr(client, "resolve") and hasattr(client, "post_state")


def _post_state(
    client: Any, payload: lightctl.WledPayload, target: str = "all"
) -> None:
    """Post through a fleet target, or require ``all`` for a single device."""

    target_name = str(target or "all")
    if _is_fleet(client):
        client.post_state(payload, target=target_name)
        return
    if target_name.casefold() not in ("all", "default"):
        raise ValueError(f"Target {target_name!r} requires fleet mode.")
    client.post_state(payload)


def _unwrap_controller_state(value: Any) -> dict:
    """Normalize fleet entries that may be raw state or ``{'state': state}``."""

    if not isinstance(value, dict) or "error" in value:
        return {}
    nested = value.get("state")
    return nested if isinstance(nested, dict) else value


def primary_state(client: Any, state_map: dict | None = None) -> dict:
    """Raw WLED state of the primary (first) controller, for single-state consumers."""
    raw = state_map if state_map is not None else client.get_state()
    if not _is_fleet(client):
        return raw if isinstance(raw, dict) else {}
    for value in raw.values():
        unwrapped = _unwrap_controller_state(value)
        if unwrapped:
            return unwrapped
    return {}


def _device_snapshot(client: Any) -> dict | None:
    """Fleet snapshot when available, otherwise the single-client device snapshot."""
    if hasattr(client, "get_fleet_snapshot"):
        try:
            return client.get_fleet_snapshot()
        except Exception:
            logger.exception("Fleet snapshot failed")
            return None
    return client.get_device_snapshot()


def ai_context_text(client: Any, now_playing: dict | None = None) -> str:
    parts: list[str] = []
    if now_playing:
        parts.append(f"Background audio now playing: {now_playing_text(now_playing)}")
    snapshot = _device_snapshot(client)
    parts.append(device_snapshot_text(snapshot))
    return "\n\n".join(parts)


def _wall_seg_opts(action: dict[str, Any]) -> dict[str, Any]:
    """Shared validated options for wall composers and per-channel control."""

    seg_opts: dict[str, Any] = {}
    mapping = {
        "speed": ("sx", 0, 255),
        "intensity": ("ix", 0, 255),
        "brightness": ("bri", 0, 255),
        "c1": ("c1", 0, 255),
        "c2": ("c2", 0, 255),
        "c3": ("c3", 0, 31),
        "grouping": ("grp", 1, 255),
        "spacing": ("spc", 0, 255),
        "offset": ("of", 0, 65535),
    }
    for source, (dest, minimum, maximum) in mapping.items():
        if action.get(source) is not None:
            value = int(action[source])
            if not minimum <= value <= maximum:
                raise ValueError(f"{source} must be between {minimum} and {maximum}.")
            seg_opts[dest] = value
    for source, dest in (
        ("reverse", "rev"),
        ("mirror", "mi"),
        ("freeze", "frz"),
        ("segment_on", "on"),
    ):
        if action.get(source) is not None:
            seg_opts[dest] = bool(action[source])
    return seg_opts


def _wall_colors(action: dict[str, Any]) -> list[list[int]] | None:
    slots: list[list[int]] = []
    for suffix in ("", "2", "3"):
        keys = (f"red{suffix}", f"green{suffix}", f"blue{suffix}", f"white{suffix}")
        if not any(action.get(key) is not None for key in keys):
            continue
        slots.append([lightctl.clamp_byte(int(action.get(key) or 0)) for key in keys])
    return slots or None


def _wall_color(action: dict[str, Any]) -> list[int] | None:
    colors = _wall_colors(action)
    return colors[0] if colors else None


def _apply_wall_action(client: Any, action: dict[str, Any]) -> str:
    if not _is_fleet(client):
        raise ValueError(
            f"{action.get('action')} requires fleet mode (start the GUI without --host)."
        )
    import columns

    kind = str(action.get("action") or "")
    if kind == "atmosphere":
        import atmospheres

        name = str(action.get("atmosphere") or action.get("name") or "").strip()
        if not name:
            raise ValueError(
                f"atmosphere requires a name. Available: {', '.join(atmospheres.atmosphere_names())}."
            )
        atmospheres.apply_atmosphere(client, name)
        return f"Applied atmosphere {name}."

    seg_opts = _wall_seg_opts(action)
    colors = _wall_colors(action)
    if colors:
        seg_opts["col"] = colors

    def optional_int(*names: str) -> int | None:
        for name in names:
            if action.get(name) is not None:
                return int(action[name])
        return None

    fx = optional_int("fx", "effect")
    pal = optional_int("pal", "palette")
    default_fx = fx if fx is not None else 9
    if kind == "wall_span":
        columns.wall_span(client, default_fx, pal, **seg_opts)
    elif kind == "wall_mirror":
        columns.mirror(client, default_fx, pal, **seg_opts)
    elif kind == "wall_chase":
        columns.chase(client, default_fx, pal, **seg_opts)
    elif kind == "wall_versus":
        columns.left_vs_right(
            client,
            (
                optional_int("fx_left")
                if optional_int("fx_left") is not None
                else default_fx
            ),
            (
                optional_int("fx_right")
                if optional_int("fx_right") is not None
                else default_fx
            ),
            optional_int("pal_left") if optional_int("pal_left") is not None else pal,
            optional_int("pal_right") if optional_int("pal_right") is not None else pal,
            **seg_opts,
        )
    elif kind == "set_channel":
        channel = str(action.get("channel") or "").strip()
        if not channel:
            raise ValueError("set_channel requires a channel name.")
        if fx is not None:
            seg_opts["fx"] = fx
        if pal is not None:
            seg_opts["pal"] = pal
        columns.set_channel(client, channel, **seg_opts)
    else:
        raise ValueError(f"Unknown wall action: {kind}")
    return f"Applied {kind}."


def payload_for_ai_action(
    action: dict[str, Any],
    *,
    effect_names: list[str] | None = None,
    palette_count: int | None = None,
) -> lightctl.WledPayload:
    """Pure conversion from a validated fallback action to a WLED payload."""

    action = _validate_ai_action_shape(dict(action))
    kind = str(action["action"])

    def required_int(name: str) -> int:
        return int(_require_action_field(action, name))

    def optional_int(name: str) -> int | None:
        return None if action.get(name) is None else int(action[name])

    transition_ms = int(action.get("transition") or 0)
    if kind == "on":
        return lightctl.on_payload(True, transition_ms=transition_ms)
    if kind == "off":
        return lightctl.on_payload(False, transition_ms=transition_ms)
    if kind == "brightness":
        return lightctl.brightness_payload(
            required_int("brightness"), transition_ms=transition_ms
        )
    if kind == "color":
        return lightctl.color_payload(
            int(action.get("red") or 0),
            int(action.get("green") or 0),
            int(action.get("blue") or 0),
            int(action.get("white") or 0),
            red2=optional_int("red2"),
            green2=optional_int("green2"),
            blue2=optional_int("blue2"),
            white2=optional_int("white2"),
            red3=optional_int("red3"),
            green3=optional_int("green3"),
            blue3=optional_int("blue3"),
            white3=optional_int("white3"),
            transition_ms=transition_ms,
        )
    if kind == "temperature":
        return lightctl.cct_payload(required_int("kelvin"), transition_ms=transition_ms)
    if kind == "effect":
        payload = lightctl.effect_payload(
            required_int("effect"),
            int(action.get("speed", 128)),
            transition_ms=transition_ms,
            intensity=optional_int("intensity"),
            palette=optional_int("palette"),
            c1=optional_int("c1"),
            c2=optional_int("c2"),
            c3=optional_int("c3"),
            effect_names=effect_names,
            palette_count=palette_count,
        )
        if any(
            action.get(k) is not None
            for k in (
                "red",
                "green",
                "blue",
                "white",
                "red2",
                "green2",
                "blue2",
                "white2",
                "red3",
                "green3",
                "blue3",
                "white3",
            )
        ):
            payload = lightctl.merge_payloads(
                payload,
                lightctl.color_payload(
                    int(action.get("red") or 0),
                    int(action.get("green") or 0),
                    int(action.get("blue") or 0),
                    int(action.get("white") or 0),
                    red2=optional_int("red2"),
                    green2=optional_int("green2"),
                    blue2=optional_int("blue2"),
                    white2=optional_int("white2"),
                    red3=optional_int("red3"),
                    green3=optional_int("green3"),
                    blue3=optional_int("blue3"),
                    white3=optional_int("white3"),
                    transition_ms=transition_ms,
                ),
            )
        return payload
    if kind == "scene":
        return lightctl.scene_payload(
            str(_require_action_field(action, "scene")), transition_ms=transition_ms
        )
    if kind == "random":
        return lightctl.random_scene_payload(transition_ms=transition_ms)
    if kind == "preset":
        return lightctl.preset_payload(
            required_int("preset_id"), transition_ms=transition_ms
        )
    if kind == "save_preset":
        return {
            "psave": required_int("preset_id"),
            "n": str(action.get("name") or f"Preset {required_int('preset_id')}"),
        }
    if kind == "delete_preset":
        return {"pdel": required_int("preset_id")}
    if kind == "playlist":
        return lightctl.playlist_payload(
            required_int("playlist_id"), transition_ms=transition_ms
        )
    if kind == "palette":
        return lightctl.palette_payload(
            required_int("palette"),
            transition_ms=transition_ms,
            palette_count=palette_count,
        )
    if kind == "nightlight":
        nl: dict[str, Any] = {}
        if action.get("enabled") is not None:
            nl["on"] = bool(action["enabled"])
        if action.get("minutes") is not None:
            nl["dur"] = int(float(action["minutes"]))
        if action.get("mode") is not None:
            nl["mode"] = int(action["mode"])
        if action.get("target_brightness") is not None:
            nl["tbri"] = int(action["target_brightness"])
        return {"nl": nl}
    if kind == "udp_sync":
        udpn: dict[str, Any] = {}
        if action.get("send") is not None:
            udpn["send"] = bool(action["send"])
        if action.get("receive") is not None:
            udpn["recv"] = bool(action["receive"])
        return {"udpn": udpn}
    if kind == "native_audio_reactive":
        enabled = bool(action["enabled"])
        return {"AudioReactive": {"on": enabled, "enabled": enabled}}
    if kind == "segment_options":
        payload = _segment_options_payload(action)
        if not payload:
            raise ValueError("segment_options requires at least one segment setting.")
        return payload
    if kind in CLIENT_ACTIONS or kind in {
        "save_scene",
        "delete_scene",
        "schedule_add",
        "schedule_remove",
    }:
        return {}
    raise ValueError(f"AI action {kind!r} is not a direct WLED payload action.")


def _catalog_from_snapshot(
    snapshot: dict | None,
) -> tuple[list[str] | None, int | None]:
    if not isinstance(snapshot, dict):
        return None, None
    candidates: list[dict[str, Any]] = []
    devices = snapshot.get("devices")
    if isinstance(devices, dict):
        candidates.extend(
            value for value in devices.values() if isinstance(value, dict)
        )
    else:
        candidates.append(snapshot)
    for candidate in candidates:
        effects = candidate.get("effects")
        palettes = candidate.get("palettes")
        if isinstance(effects, list):
            return [str(item) for item in effects], (
                len(palettes) if isinstance(palettes, list) else None
            )
    return None, None


def apply_ai_actions(
    client: Any, actions_list: list[dict[str, Any]], target: str = "all"
) -> str:
    """Prevalidate an entire fallback plan, then execute it in order."""

    effect_names, palette_count = _catalog_from_snapshot(_device_snapshot(client))
    prepared: list[tuple[str, dict[str, Any], str, lightctl.WledPayload | None]] = []
    for raw in actions_list:
        action = _validate_ai_action_shape(dict(raw))
        kind = str(action["action"])
        action_target = str(action.get("target") or target or "all")
        payload: lightctl.WledPayload | None = None
        if kind in WALL_ACTIONS:
            for field in ("effect", "fx_left", "fx_right"):
                if action.get(field) is not None:
                    lightctl.validate_effect(
                        int(action[field]), effect_names=effect_names
                    )
            if palette_count is not None:
                for field in ("palette", "pal_left", "pal_right"):
                    if (
                        action.get(field) is not None
                        and not 0 <= int(action[field]) < palette_count
                    ):
                        raise ValueError(f"Palette {action[field]} is not available.")
        elif (
            kind
            not in {"save_scene", "delete_scene", "schedule_add", "schedule_remove"}
            and kind not in CLIENT_ACTIONS
        ):
            payload = payload_for_ai_action(
                action, effect_names=effect_names, palette_count=palette_count
            )
            lightctl.validate_wled_payload(payload)
        prepared.append((kind, action, action_target, payload))

    applied: list[str] = []
    with _lighting_apply_lock:
        for kind, action, action_target, payload in prepared:
            if kind in WALL_ACTIONS:
                _apply_wall_action(client, action)
                applied.append(kind)
            elif kind == "save_scene":
                snapshot_payload: lightctl.WledPayload = {}
                state = primary_state(client)
                for key in ("on", "bri", "seg", "transition"):
                    if key in state:
                        snapshot_payload[key] = copy.deepcopy(state[key])  # type: ignore[literal-required]
                lightctl.save_scene(str(action["scene"]), snapshot_payload)
                applied.append(kind)
            elif kind == "delete_scene":
                if not lightctl.delete_scene(str(action["scene"])):
                    raise ValueError(f"Saved scene {action['scene']!r} does not exist.")
                applied.append(kind)
            elif kind == "schedule_add":
                schedule_action = str(action["schedule_action"])
                schedule_data = (
                    {"scene": str(action["scene"])}
                    if schedule_action == "scene"
                    else {}
                )
                lightctl.add_schedule(
                    str(action["schedule_time"]), schedule_action, schedule_data
                )
                applied.append(kind)
            elif kind == "schedule_remove":
                if not lightctl.remove_schedule(int(action["schedule_index"])):
                    raise ValueError(
                        f"No schedule entry at index {action['schedule_index']}."
                    )
                applied.append(kind)
            elif kind in CLIENT_ACTIONS:
                applied.append(f"{kind} (client)")
            elif payload:
                _post_state(client, payload, target=action_target)
                applied.append(kind)
    return "AI applied: " + ", ".join(applied) + "."


def apply_ai_plan(
    client: lightctl.LightClient, plan: dict[str, Any], target: str = "all"
) -> dict[str, Any]:
    message = apply_ai_actions(client, plan["actions"], target=target)
    return {
        "message": message,
        "response": str(plan.get("response", "")),
        "confirmations": plan.get("confirmations", []),
        "client_actions": plan.get("client_actions", []),
    }


def payload_for_action(action: str, data: dict[str, Any]) -> lightctl.WledPayload:
    transition_ms = int(data.get("transition", 0))
    if action == "on":
        return lightctl.on_payload(True, transition_ms=transition_ms)
    if action == "off":
        return lightctl.on_payload(False, transition_ms=transition_ms)
    if action == "bri":
        return lightctl.brightness_payload(
            int(data.get("value", 200)), transition_ms=transition_ms
        )
    if action == "color":
        return lightctl.color_payload(
            int(data.get("red", 255)),
            int(data.get("green", 255)),
            int(data.get("blue", 255)),
            int(data.get("white", 0)),
            red2=data.get("red2"),
            green2=data.get("green2"),
            blue2=data.get("blue2"),
            white2=data.get("white2"),
            red3=data.get("red3"),
            green3=data.get("green3"),
            blue3=data.get("blue3"),
            white3=data.get("white3"),
            transition_ms=transition_ms,
        )
    if action == "rgbw_bri":
        return lightctl.merge_payloads(
            lightctl.brightness_payload(
                int(data.get("brightness", 200)), transition_ms=transition_ms
            ),
            lightctl.color_payload(
                int(data.get("red", 255)),
                int(data.get("green", 255)),
                int(data.get("blue", 255)),
                int(data.get("white", 0)),
                transition_ms=transition_ms,
            ),
        )
    if action == "beat":
        return lightctl.reactive_beat_payload(
            (
                int(data.get("red", 255)),
                int(data.get("green", 255)),
                int(data.get("blue", 255)),
                int(data.get("white", 0)),
            ),
            int(data.get("brightness", 200)),
            int(data.get("effect", 9)),
            int(data.get("speed", 160)),
            transition_ms=transition_ms,
        )
    if action == "fx":
        return lightctl.effect_payload(
            int(data.get("effect", 2)),
            speed=int(data.get("speed", 128)),
            intensity=data.get("intensity"),
            palette=data.get("palette"),
            c1=data.get("c1"),
            c2=data.get("c2"),
            c3=data.get("c3"),
            o1=data.get("o1"),
            o2=data.get("o2"),
            o3=data.get("o3"),
            transition_ms=transition_ms,
        )
    if action == "scene":
        return lightctl.scene_payload(
            str(data.get("name", "warm")), transition_ms=transition_ms
        )
    if action == "temp":
        # Prefer native cct if provided, fall back to RGBW approx
        if data.get("cct") is not None:
            return lightctl.cct_payload(int(data["cct"]), transition_ms=transition_ms)
        return lightctl.color_payload(
            *lightctl.kelvin_to_rgbw(int(data.get("kelvin", 4000))),
            transition_ms=transition_ms,
        )
    if action == "cct":
        return lightctl.cct_payload(
            int(data.get("cct", 127)), transition_ms=transition_ms
        )
    if action == "delete_scene":
        lightctl.delete_scene(str(data.get("name", "")))
        return {}
    if action == "schedule":
        subaction = str(data.get("subaction", ""))
        if subaction == "add":
            sched_data: dict = {}
            scene_name = data.get("scene_name")
            if scene_name:
                sched_data["scene"] = str(scene_name)
            lightctl.add_schedule(
                str(data.get("time", "00:00")),
                str(data.get("action", "on")),
                sched_data,
            )
        elif subaction == "remove":
            lightctl.remove_schedule(int(data.get("index", -1)))
        return {}
    if action == "hex":
        return lightctl.color_payload(
            *lightctl.hex_to_rgbw(str(data.get("color", "#ffffff"))),
            transition_ms=transition_ms,
        )
    if action == "random":
        return lightctl.random_scene_payload(transition_ms=transition_ms)
    if action == "preset":
        return lightctl.preset_payload(
            int(data.get("id", 1)), transition_ms=transition_ms
        )
    if action == "save_preset":
        preset_id = int(data.get("id", 1))
        name = str(data.get("name", f"Preset {preset_id}"))
        return {"psave": preset_id, "n": name}
    if action == "delete_preset":
        return {"pdel": int(data.get("id", 1))}
    if action == "playlist":
        return lightctl.playlist_payload(int(data.get("id", 1)))
    if action == "palette":
        return {"seg": [{"pal": int(data.get("id", 0))}]}
    if action == "nightlight":
        nl: dict[str, Any] = {}
        if data.get("enabled") is not None:
            nl["on"] = bool(data.get("enabled"))
        if data.get("minutes") is not None:
            nl["dur"] = int(data["minutes"])
        if data.get("mode") is not None:
            nl["mode"] = int(data["mode"])
        if data.get("target_brightness") is not None:
            nl["tbri"] = int(data["target_brightness"])
        return {"nl": nl}
    if action == "udp_sync":
        udpn: dict[str, Any] = {}
        if data.get("send") is not None:
            udpn["send"] = bool(data.get("send"))
        if data.get("receive") is not None:
            udpn["recv"] = bool(data.get("receive"))
        return {"udpn": udpn}
    if action == "native_audio_reactive":
        enabled = bool(data.get("enabled"))
        return {"AudioReactive": {"on": enabled, "enabled": enabled}}
    if action == "segment_options":
        return _segment_options_payload(data)
    if action == "restart":
        return lightctl.restart_payload()
    raise ValueError(f"Unknown action: {action}")


# ---------------------------------------------------------------------------
# Autonomous AI mode
# ---------------------------------------------------------------------------


class AutonomousMode:
    """Server-side loop: detects song changes, AI-generates a themed beatmatched show."""

    POLL_SEC = 20  # song detection poll interval
    QUIET_SEC = 60  # seconds without beats → ambient dim
    SHAZAM_INTERVAL = 90  # min seconds between mic-recognition attempts

    def __init__(self, client: lightctl.LightClient) -> None:
        self._client = client
        self._stop = threading.Event()
        self._main_thread: threading.Thread | None = None
        self._reactive = lightctl.ReactiveMode(client)
        self._beat_thread: lightctl.ReactiveThread | None = None
        self._current_song_key: str = ""
        self._current_song: dict | None = None
        self._last_beat_time: float = time.time()
        self._in_quiet: bool = False
        self._lock = threading.Lock()

    # ---- public interface -----------------------------------------------

    def start(self) -> str:
        if self._main_thread and self._main_thread.is_alive():
            return "Autonomous AI mode is already running."
        self._stop.clear()
        self._current_song_key = ""
        self._last_beat_time = time.time()
        self._in_quiet = False
        self._main_thread = threading.Thread(
            target=self._run, daemon=True, name="auto-main"
        )
        self._main_thread.start()
        self._start_beat()
        return "Autonomous AI mode started — listening for music..."

    def stop(self) -> str:
        self._stop.set()
        self._stop_beat()
        return "Autonomous AI mode stopped."

    def is_running(self) -> bool:
        return bool(self._main_thread and self._main_thread.is_alive())

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self.is_running(),
                "quiet": self._in_quiet,
                "song": self._current_song,
            }

    # ---- beat detection -------------------------------------------------

    def _beat_callback(self, energy: float, is_beat: bool) -> None:
        if is_beat:
            with self._lock:
                self._last_beat_time = time.time()
                self._in_quiet = False

    def _start_beat(self) -> None:
        self._beat_thread = lightctl.ReactiveThread(
            self._client,
            level_callback=self._beat_callback,
            reactive_mode=self._reactive,
        )
        self._beat_thread.start()

    def _stop_beat(self) -> None:
        if self._beat_thread and self._beat_thread.is_alive():
            self._beat_thread.stop()
            # give sounddevice a moment to release the device
            time.sleep(0.3)

    # ---- main loop ------------------------------------------------------

    def _run(self) -> None:
        last_shazam = 0.0
        while not self._stop.wait(self.POLL_SEC):
            with self._lock:
                secs_silent = time.time() - self._last_beat_time
                already_quiet = self._in_quiet
            if secs_silent > self.QUIET_SEC and not already_quiet:
                with self._lock:
                    self._in_quiet = True
                self._enter_quiet_mode()
                continue

            now_playing = get_now_playing()  # playerctl/MPRIS — instant, no mic
            if not now_playing and (time.time() - last_shazam) > self.SHAZAM_INTERVAL:
                now_playing = self._shazam_identify()
                last_shazam = time.time()
            if not now_playing:
                continue

            song_key = (
                f"{now_playing.get('title', '')}||{now_playing.get('artist', '')}"
            )
            with self._lock:
                changed = song_key != self._current_song_key
            if changed:
                with self._lock:
                    self._current_song_key = song_key
                    self._current_song = now_playing
                    self._in_quiet = False
                self._apply_song_show(now_playing)

    # ---- helpers --------------------------------------------------------

    def _shazam_identify(self) -> dict | None:
        if not music_recognizer.is_available():
            return None
        logger.info("Autonomous: Shazam identification attempt")
        self._stop_beat()
        try:
            result = music_recognizer.recognize_sync()
            if result:
                return {
                    "title": result.get("title", ""),
                    "artist": result.get("artist", ""),
                    "album": result.get("album", ""),
                    "genre": result.get("genre", ""),
                    "status": "Playing",
                    "source": "shazam",
                }
        except Exception:
            logger.exception("Autonomous: Shazam failed")
        finally:
            # Always restart the beat detector; use a delay to let ALSA/PortAudio
            # fully release the capture device before reopening.
            time.sleep(0.5)
            self._start_beat()
        return None

    def _enter_quiet_mode(self) -> None:
        logger.info("Autonomous: quiet/conversation detected — dimming to ambient")
        try:
            self._client.post_state(
                lightctl.color_payload(
                    *lightctl.kelvin_to_rgbw(2700), transition_ms=3000
                )
            )
            self._client.post_state(lightctl.brightness_payload(60, transition_ms=3000))
        except Exception:
            logger.exception("Autonomous: quiet mode transition failed")

    def _apply_song_show(self, now_playing: dict) -> None:
        title = now_playing.get("title", "Unknown")
        artist = now_playing.get("artist", "Unknown")
        genre = now_playing.get("genre", "")
        logger.info("Autonomous: new song '%s' by %s — generating show", title, artist)
        try:
            prompt = (
                f"New song now playing: '{title}' by {artist}"
                + (f" (genre: {genre})" if genre else "")
                + ". Beat-reactive mode is running continuously — do NOT include mode1_start. "
                "Design a complete, unique light show: choose an effect, palette by name, primary "
                "color, secondary color, effect speed, intensity, and brightness that match this "
                "song's specific mood, energy, and tempo. Both color slots will be used by beat "
                "detection for two-tone rhythmic pulsing. Be bold and creative — each song should "
                "feel distinctly different. Reference the actual energy and genre of this song. "
                "For the 'response' field (shown as scrolling marquee at top of UI), include fun music trivia, artist trivia, song facts, jokes or hype — vary it and make it entertaining to scroll."
            )
            snapshot = _device_snapshot(self._client)
            plan = call_openai_for_plan(prompt, now_playing, snapshot)
            plan["actions"] = [
                a
                for a in plan.get("actions", [])
                if a.get("action") not in ("mode1_start", "mode1_stop")
            ]
            # Update beat detection palette/effects from AI response
            colors = self._extract_colors(plan)
            effects = self._extract_effects(plan)
            if colors:
                self._reactive.palette = colors
            if effects:
                self._reactive.effects = tuple(effects)
            apply_ai_plan(self._client, plan)
            logger.info("Autonomous: show applied for '%s'", title)
        except Exception:
            logger.exception("Autonomous: failed to apply show for '%s'", title)

    @staticmethod
    def _extract_colors(plan: dict) -> list[tuple[int, int, int, int]]:
        colors: list[tuple[int, int, int, int]] = []
        for action in plan.get("actions", []):
            for rk, gk, bk, wk in (
                ("red", "green", "blue", "white"),
                ("red2", "green2", "blue2", "white2"),
                ("red3", "green3", "blue3", "white3"),
            ):
                vals = [action.get(k) for k in (rk, gk, bk, wk)]
                if any(v is not None for v in vals[:3]):
                    c: tuple[int, int, int, int] = tuple(int(v or 0) for v in vals)  # type: ignore[assignment]
                    if any(c):
                        colors.append(c)
        return colors

    @staticmethod
    def _extract_effects(plan: dict) -> list[int]:
        return [
            int(a["effect"])
            for a in plan.get("actions", [])
            if a.get("effect") is not None and 0 <= int(a["effect"]) <= 255
        ]


class BrowserFirstAutonomousMode:
    """Compatibility shim: browser Music Mode owns microphone-based autonomy."""

    def start(self) -> str:
        return "Use browser Music Mode; server-side microphone autonomous mode is disabled to avoid duplicate listeners."

    def stop(self) -> str:
        return "Server-side autonomous mode is not running."

    def is_running(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {
            "running": False,
            "quiet": False,
            "song": None,
            "disabled": True,
            "message": "Use browser Music Mode.",
        }


class AiJobManager:
    MAX_JOBS = 50

    def __init__(self, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ai-job"
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._closed = False

    def submit(self, fn: Callable[[], dict[str, Any]]) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("AI job manager is shut down.")
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "result": None,
                "error": "",
                "created_at": time.time(),
                "started_at": None,
                "finished_at": None,
                "future": None,
            }
            self._prune_jobs_locked()

        def _run() -> dict[str, Any]:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job["status"] == "cancelled":
                    return {"ok": False, "error": "Cancelled"}
                job["status"] = "running"
                job["started_at"] = time.time()
            try:
                result = fn()
            except Exception as exc:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is not None:
                        job["status"] = "error"
                        job["error"] = str(exc)
                        job["finished_at"] = time.time()
                        self._prune_jobs_locked()
                raise
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "complete"
                    job["result"] = result
                    job["finished_at"] = time.time()
                    self._prune_jobs_locked()
            return result

        future = self._executor.submit(_run)
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["future"] = future
        return job_id

    def _prune_jobs_locked(self) -> None:
        if len(self._jobs) <= self.MAX_JOBS:
            return
        finished = sorted(
            (
                j
                for j in self._jobs.values()
                if j["status"] in ("complete", "error", "cancelled")
            ),
            key=lambda j: j["created_at"],
        )
        for job in finished[: max(0, len(self._jobs) - self.MAX_JOBS)]:
            self._jobs.pop(job["id"], None)

    def cancel_pending(self) -> int:
        count = 0
        with self._lock:
            for job in self._jobs.values():
                future = job.get("future")
                if (
                    job["status"] == "queued"
                    and isinstance(future, Future)
                    and future.cancel()
                ):
                    job["status"] = "cancelled"
                    job["finished_at"] = time.time()
                    count += 1
        return count

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return {
                    "id": job_id,
                    "status": "missing",
                    "result": None,
                    "error": "Unknown AI job.",
                }
            return {k: copy.deepcopy(v) for k, v in job.items() if k != "future"}

    def wait(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            future = job.get("future") if job else None
        if isinstance(future, Future):
            with contextlib.suppress(Exception):
                future.result(timeout=timeout)
        return self.get(job_id)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [
                {k: copy.deepcopy(v) for k, v in job.items() if k != "future"}
                for job in self._jobs.values()
            ]
        return sorted(jobs, key=lambda job: job["created_at"], reverse=True)[:5]

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
        self.cancel_pending()
        self._executor.shutdown(wait=wait, cancel_futures=True)


# ---------------------------------------------------------------------------
# AI request serialization
# ---------------------------------------------------------------------------
_AI_MAX_PROMPT_LEN = 2000


class ScheduleExecutor:
    def __init__(self, client: Any) -> None:
        self.client = client
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="lights-schedule"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        logger.info("Schedule executor started.")
        while not self._stop.is_set():
            now = time.strftime("%H:%M")
            today = time.strftime("%Y-%m-%d")
            schedule = lightctl.list_schedule()
            modified = False
            for entry in schedule:
                if entry.get("time") != now or entry.get("last_run_date") == today:
                    continue
                try:
                    action = entry.get("action")
                    data = (
                        entry.get("data") if isinstance(entry.get("data"), dict) else {}
                    )
                    if action == "on":
                        payload = lightctl.on_payload(True)
                    elif action == "off":
                        payload = lightctl.on_payload(False)
                    elif action == "scene":
                        payload = lightctl.scene_payload(
                            str(data.get("scene") or "warm")
                        )
                    else:
                        logger.warning("Unsupported schedule action: %s", action)
                        continue
                    _post_state(self.client, payload, target="all")
                    entry["last_run_date"] = today
                    modified = True
                    logger.info(
                        "Executed schedule: %s -> %s", entry.get("time"), action
                    )
                except Exception:
                    logger.exception("Schedule execution failed")
            if modified:
                saver = getattr(lightctl, "_save_schedule", None)
                if callable(saver):
                    saver(schedule)
                else:
                    logger.warning(
                        "Schedule persistence helper unavailable; last-run marker not saved"
                    )
            self._stop.wait(30)


def _build_fleet_client(
    dry_run: bool = False,
) -> tuple[Any, lightctl.LightClient | None]:
    """Build a LightFleet (plus a dedicated primary info client) from the current config."""
    import fleet  # lazy: fleet imports lightctl

    client = fleet.LightFleet.from_config(dry_run=dry_run)
    controllers = fleet.load_controllers()
    info_client = (
        lightctl.LightClient(controllers[0].host, dry_run=dry_run)
        if controllers
        else None
    )
    return client, info_client


class GuiState:
    def __init__(
        self,
        client: lightctl.LightClient,
        target: str = "all",
        info_client: lightctl.LightClient | None = None,
        dry_run: bool = False,
    ) -> None:
        self.client = client
        self.target = target or "all"
        self.dry_run = dry_run
        self.is_fleet = _is_fleet(client)
        # /json/info is per-device; in fleet mode a dedicated client on the
        # primary controller feeds the SSE intel block.
        self._info_client = info_client or client
        self.mode1 = lightctl.ReactiveThread(client)
        self.autonomous = BrowserFirstAutonomousMode()
        self.schedule = ScheduleExecutor(client)
        self.mood_session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=music_recognizer.recognize_audio_bytes_sync,
            generate_fn=lambda song: generate_mood_for_song(client, song),
        )
        self._mood_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mood-sample"
        )
        self._mood_sample_lock = threading.Lock()
        self._mood_samples_inflight = 0
        self._mood_sample_limit = 1
        self.ai_jobs = AiJobManager(max_workers=1)
        self.schedule.start()
        self.fade_timer: lightctl.FadeTimer | None = None
        self.cached_state = None
        self.cached_info = None
        # Offline status is tracked per-endpoint: a hiccup fetching /json/info
        # must not make /json/state (and vice versa) serve stale cached data.
        self._offline: dict[str, bool] = {}
        self._offline_checked_at: dict[str, float] = {}
        self._cache_fetched_at: dict[str, float] = {}
        self.state_lock = threading.RLock()
        self.action_lock = threading.RLock()
        self._reload_lock = threading.Lock()
        self._closed = False

    @property
    def is_offline(self) -> bool:
        return self._offline.get("cached_state", False)

    def _fetch_throttled(
        self, cache_attr: str, fetch_fn: Callable[[], dict], ttl_s: float
    ) -> dict:
        now = time.monotonic()
        cached = getattr(self, cache_attr, None)
        if (
            cached is not None
            and now - self._cache_fetched_at.get(cache_attr, 0.0) < ttl_s
        ):
            return cached
        if (
            self._offline.get(cache_attr)
            and now - self._offline_checked_at.get(cache_attr, 0.0) < 5.0
        ):
            if cached is not None:
                return cached
            raise RuntimeError("Controller is offline (throttled)")
        with self.state_lock:
            now = time.monotonic()
            cached = getattr(self, cache_attr, None)
            if (
                cached is not None
                and now - self._cache_fetched_at.get(cache_attr, 0.0) < ttl_s
            ):
                return cached
            try:
                result = fetch_fn()
                if not isinstance(result, dict):
                    raise RuntimeError("Controller returned a non-object state.")
                self._offline[cache_attr] = False
                self._offline_checked_at[cache_attr] = now
                self._cache_fetched_at[cache_attr] = now
                setattr(self, cache_attr, result)
                return result
            except Exception:
                self._offline[cache_attr] = True
                self._offline_checked_at[cache_attr] = now
                if cached is not None:
                    return cached
                raise

    def invalidate_caches(self) -> None:
        with self.state_lock:
            self._cache_fetched_at.clear()

    def get_state_throttled(self) -> dict:
        return self._fetch_throttled(
            "cached_state", self.client.get_state, STATE_CACHE_TTL_S
        )

    def get_info_throttled(self) -> dict:
        info_client = getattr(self, "_info_client", None) or self.client
        return self._fetch_throttled(
            "cached_info", info_client.get_info, INFO_CACHE_TTL_S
        )

    def api_state(self) -> dict:
        """State for /api/state + SSE: raw WLED state, or {controller: state} in fleet mode."""
        raw = self.get_state_throttled()
        if not getattr(self, "is_fleet", False):
            return raw
        return {name: _unwrap_controller_state(value) for name, value in raw.items()}

    def primary_state(self, state_map: dict | None = None) -> dict:
        """Raw WLED state of the primary controller (single-state consumers)."""
        return primary_state(
            self.client, state_map if state_map is not None else self.api_state()
        )

    def target_names(self) -> list[str]:
        """Valid action targets for the UI selector (deduped, order-preserving)."""
        if not getattr(self, "is_fleet", False):
            return ["all"]
        names = ["all"] + list(self.client.names()) + list(self.client.channels())
        return list(dict.fromkeys(names))

    def channel_map(self) -> dict[str, list]:
        """Channel -> [controller_name, segment id] for the UI."""
        if not getattr(self, "is_fleet", False):
            return {}
        return {
            name: [controller, seg_id]
            for name, (controller, seg_id) in self.client.channels().items()
        }

    def start_mode1(self) -> str:
        return self.mode1.start()

    def stop_mode1(self) -> str:
        return self.mode1.stop()

    def start_fade(self, minutes: float, brightness: int | None = None) -> str:
        if self.fade_timer and self.fade_timer.is_alive():
            return "Fade timer is already running."
        self.fade_timer = lightctl.FadeTimer(
            self.client, minutes, start_brightness=brightness
        )
        return self.fade_timer.start()

    def start_cycle(self, interval: float, items: list[str] | None = None) -> str:
        if hasattr(self, "_cycle") and self._cycle and self._cycle.is_alive():
            return "Cycle is already running."
        self._cycle = lightctl.CycleThread(
            self.client, items=items, interval_seconds=interval
        )
        return self._cycle.start()

    def stop_cycle(self) -> str:
        if hasattr(self, "_cycle") and self._cycle:
            return self._cycle.stop()
        return "Cycle is not running."

    def start_sunrise(self, minutes: float, brightness: int = 255) -> str:
        if hasattr(self, "_sunrise") and self._sunrise and self._sunrise.is_alive():
            return "Sunrise is already running."
        self._sunrise = lightctl.SunriseSimulator(
            self.client, duration_minutes=minutes, max_brightness=brightness
        )
        return self._sunrise.start()

    def stop_sunrise(self) -> str:
        if hasattr(self, "_sunrise") and self._sunrise:
            return self._sunrise.stop()
        return "Sunrise is not running."

    def submit_mood_sample(self, audio_bytes: bytes) -> bool:
        with self._mood_sample_lock:
            if self._mood_samples_inflight >= self._mood_sample_limit:
                return False
            self._mood_samples_inflight += 1

        def _run_sample() -> None:
            try:
                self.mood_session.sample(audio_bytes)
            except Exception as exc:
                logger.exception("Mood sample processing failed: %s", exc)
            finally:
                with self._mood_sample_lock:
                    self._mood_samples_inflight = max(
                        0, self._mood_samples_inflight - 1
                    )

        self._mood_executor.submit(_run_sample)
        return True

    def reload_controllers(self) -> list[str]:
        """Build and validate a replacement fleet before swapping live workers."""

        if self._closed:
            raise RuntimeError("GUI state is shutting down.")
        with self._reload_lock:
            if not _ai_execution_lock.acquire(blocking=False):
                raise ValueError(
                    "Cannot reload controllers while an AI lighting request is running."
                )
            try:
                if self._mood_samples_inflight:
                    raise ValueError(
                        "Cannot reload controllers while a mood sample is processing."
                    )
                new_client, info_client = _build_fleet_client(
                    dry_run=getattr(self, "dry_run", False)
                )
                old_workers = (
                    self.mode1,
                    self.schedule,
                    self.mood_session,
                    self.fade_timer,
                    getattr(self, "_cycle", None),
                    getattr(self, "_sunrise", None),
                )
                try:
                    import music_director

                    director_was_running = bool(
                        music_director.director_status().get("running")
                    )
                    if director_was_running:
                        music_director.stop_director()
                except Exception:
                    director_was_running = False

                for worker in old_workers:
                    if worker is not None:
                        with contextlib.suppress(Exception):
                            worker.stop()
                self.ai_jobs.shutdown(wait=False)
                self._mood_executor.shutdown(wait=False, cancel_futures=True)

                new_mode1 = lightctl.ReactiveThread(new_client)
                new_schedule = ScheduleExecutor(new_client)
                new_mood = mood_orchestrator.MoodSession(
                    client=new_client,
                    recognize_fn=music_recognizer.recognize_audio_bytes_sync,
                    generate_fn=lambda song: generate_mood_for_song(new_client, song),
                )
                new_jobs = AiJobManager(max_workers=1)
                new_mood_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="mood-sample"
                )

                with self.state_lock:
                    self.client = new_client
                    self._info_client = info_client or new_client
                    self.is_fleet = _is_fleet(new_client)
                    self.mode1 = new_mode1
                    self.schedule = new_schedule
                    self.mood_session = new_mood
                    self.ai_jobs = new_jobs
                    self._mood_executor = new_mood_executor
                    self._mood_samples_inflight = 0
                    self.fade_timer = None
                    self._cycle = None
                    self._sunrise = None
                    self.cached_state = None
                    self.cached_info = None
                    self._offline.clear()
                    self._offline_checked_at.clear()
                    self._cache_fetched_at.clear()
                new_schedule.start()
                if director_was_running or music_director_config().get("enabled"):
                    with contextlib.suppress(Exception):
                        set_music_director(new_client, True)
                return (
                    list(new_client.names())
                    if hasattr(new_client, "names")
                    else [str(getattr(new_client, "host", "default"))]
                )
            finally:
                _ai_execution_lock.release()

    def shutdown(self) -> None:
        """Stop background workers and release executors without hanging shutdown."""
        if self._closed:
            return
        self._closed = True
        for stoppable in (
            getattr(self, "mode1", None),
            getattr(self, "schedule", None),
            getattr(self, "mood_session", None),
            getattr(self, "fade_timer", None),
            getattr(self, "_cycle", None),
            getattr(self, "_sunrise", None),
        ):
            if stoppable is not None:
                with contextlib.suppress(Exception):
                    stoppable.stop()
        with contextlib.suppress(Exception):
            self.ai_jobs.shutdown(wait=False)
        with contextlib.suppress(Exception):
            self._mood_executor.shutdown(wait=False, cancel_futures=True)
        with contextlib.suppress(Exception):
            import music_director

            music_director.stop_director()


def smart_suggestions(
    state_data: dict | None = None, now_playing: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Return context-aware one-tap lighting suggestions for the GUI."""
    hour = int(time.strftime("%H"))
    seg = {}
    if state_data and isinstance(state_data.get("seg"), list) and state_data["seg"]:
        seg = state_data["seg"][0] if isinstance(state_data["seg"][0], dict) else {}
    is_on = bool(state_data.get("on")) if state_data else False
    bri = int(state_data.get("bri") or 0) if state_data else 0
    suggestions: list[dict[str, Any]] = []

    if not is_on:
        suggestions.append(
            {
                "title": "Wake the room",
                "reason": "Lights are off — start warm with a gentle fade.",
                "action": "scene",
                "payload": {"name": "warm"},
            }
        )
    elif bri > 210 and hour >= 21:
        suggestions.append(
            {
                "title": "Wind down",
                "reason": "It is late and bright — switch to a warmer low scene.",
                "action": "scene",
                "payload": {"name": "night"},
            }
        )
    elif 6 <= hour < 11:
        suggestions.append(
            {
                "title": "Morning focus",
                "reason": "Bright daylight tones help the room feel awake.",
                "action": "temp",
                "payload": {"kelvin": 5000, "transition": 700},
            }
        )
    elif 17 <= hour < 22:
        suggestions.append(
            {
                "title": "Evening ocean",
                "reason": "A calm blue flow fits the end of the day.",
                "action": "scene",
                "payload": {"name": "ocean"},
            }
        )
    else:
        suggestions.append(
            {
                "title": "Balanced focus",
                "reason": "Clean, bright, neutral light for everyday use.",
                "action": "scene",
                "payload": {"name": "focus"},
            }
        )

    if now_playing and (now_playing.get("title") or now_playing.get("artist")):
        suggestions.append(
            {
                "title": "Match the music",
                "reason": now_playing_text(now_playing),
                "action": "music_match",
                "payload": {},
            }
        )
    else:
        suggestions.append(
            {
                "title": "Listen + match",
                "reason": "Identify the song, then let AI build a show.",
                "action": "music_match",
                "payload": {},
            }
        )

    current_fx = int(seg.get("fx", 0) or 0)
    if current_fx == 0:
        suggestions.append(
            {
                "title": "Add motion",
                "reason": "Current output looks static — add smooth Colorwaves.",
                "action": "fx",
                "payload": {"effect": 67, "speed": 120, "transition": 500},
            }
        )
    else:
        suggestions.append(
            {
                "title": "Soften motion",
                "reason": "Use a slower, safer flow with gentle transitions.",
                "action": "fx",
                "payload": {"effect": 108, "speed": 78, "transition": 800},
            }
        )

    suggestions.append(
        {
            "title": "Surprise me",
            "reason": "Choose a random safe built-in scene.",
            "action": "random",
            "payload": {"transition": 600},
        }
    )
    return suggestions[:4]


# ---------------------------------------------------------------------------
# Settings API helpers
# ---------------------------------------------------------------------------

SETTINGS_MERGE_KEYS = (
    "ai",
    "controllers",
    "installation",
    "audio_source",
    "mic_device",
    "system_prompt_override",
)


def current_settings(config: dict | None = None) -> dict:
    """Settings payload for GET /api/settings (never includes API key values)."""
    if config is None:
        config = lightctl.load_config()
    override = config.get("system_prompt_override")
    mic_device = config.get("mic_device")
    installation = config.get("installation")
    controllers = config.get("controllers")
    if not isinstance(controllers, list) or not controllers:
        # Config has no controllers section yet — show the effective fleet
        # (env/built-in defaults) instead of an empty list.
        import fleet as fleet_mod  # lazy: fleet imports lightctl

        controllers = [
            {
                "name": c.name,
                "host": c.host,
                "segments": {
                    str(seg_id): dataclasses.asdict(seg)
                    for seg_id, seg in c.segments.items()
                },
            }
            for c in fleet_mod.load_controllers(config)
        ]
    return {
        "ai": ai_settings(config),
        "system_prompt_override": (
            override if isinstance(override, str) and override.strip() else None
        ),
        "controllers": controllers,
        "installation": dict(installation) if isinstance(installation, dict) else None,
        "audio_source": str(config.get("audio_source") or "monitor"),
        "mic_device": str(mic_device) if mic_device not in (None, "") else None,
    }


def merge_settings_into_config(updates: dict) -> dict:
    """Merge a partial settings body into config.json (top-level keys only;
    the 'ai' object is merged key-by-key). Returns the merged config.

    Controller/installation updates are validated against fleet.load_topology
    before anything is written; a ValueError leaves the config file untouched."""
    config = lightctl.load_config()
    for key in SETTINGS_MERGE_KEYS:
        if key not in updates:
            continue
        value = updates[key]
        if (
            key == "ai"
            and isinstance(value, dict)
            and isinstance(config.get("ai"), dict)
        ):
            config["ai"].update(value)
        else:
            config[key] = value
    if "controllers" in updates or "installation" in updates:
        import fleet as fleet_mod  # lazy: fleet imports lightctl

        fleet_mod.load_topology(config)  # raises ValueError on invalid topology
    lightctl.save_config(config)
    return config


DEFAULT_FIRETV_HOST = "10.27.27.207"

_TV_FALLBACK_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'><title>lightss tv</title>"
    "<style>html,body{margin:0;height:100%;background:#000;color:#555;display:flex;"
    "align-items:center;justify-content:center;font-family:sans-serif}</style></head>"
    "<body><p>lightss tv ambient</p></body></html>"
)


def tv_ambient_html() -> str:
    """Full-screen ambient page for the FireTV browser (GET /tv)."""
    return getattr(light_gui_html, "TV_AMBIENT_HTML", _TV_FALLBACK_HTML)


def firetv_config(config: dict | None = None) -> dict:
    """The 'firetv' section of config.json ({} when absent)."""
    if config is None:
        config = lightctl.load_config()
    section = config.get("firetv")
    return dict(section) if isinstance(section, dict) else {}


def set_firetv_enabled(enabled: bool) -> dict:
    """Persist the FireTV on/off switch into config.json, keeping any existing host."""
    config = lightctl.load_config()
    firetv_cfg = firetv_config(config)
    firetv_cfg["enabled"] = bool(enabled)
    firetv_cfg.setdefault("host", DEFAULT_FIRETV_HOST)
    config["firetv"] = firetv_cfg
    lightctl.save_config(config)
    return firetv_cfg


def firetv_status_payload() -> dict:
    """GET /api/firetv payload: enabled flag plus live TV status. Never raises."""
    enabled = bool(firetv_config().get("enabled", False))
    try:
        import firetv  # lazy: firetv shells out to adb

        enabled = bool(firetv.is_enabled())
        status = firetv.status()
    except Exception as exc:
        status = {
            "enabled": enabled,
            "connected": False,
            "awake": False,
            "foreground_app": None,
            "error": str(exc),
        }
    return {"ok": True, "enabled": enabled, "status": status}


def _lan_ip() -> str:
    """Best-effort LAN IP of this machine (for URLs the FireTV must reach)."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.27.27.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def run_firetv_action(data: dict) -> dict:
    """Execute a wake/sleep/open_url action via firetv.py. Raises ValueError when
    FireTV control is disabled in settings (surfaced as ok=False by the handler)."""
    import firetv  # lazy: firetv shells out to adb

    action = str(data.get("action", "")).strip().lower()
    if action == "wake":
        result = firetv.wake()
    elif action == "sleep":
        result = firetv.sleep()
    elif action == "open_url":
        url = str(data.get("url", "")).strip()
        if not url:
            raise ValueError("url is required for open_url.")
        parsed_url = urllib.parse.urlsplit(url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
            raise ValueError("open_url only accepts absolute http or https URLs.")
        if parsed_url.username or parsed_url.password:
            raise ValueError("open_url does not accept URLs containing credentials.")
        # A URL pointing at this machine's loopback is unreachable from the TV —
        # rewrite it to the LAN IP.
        for loopback in ("127.0.0.1", "localhost"):
            if f"://{loopback}" in url:
                url = url.replace(f"://{loopback}", f"://{_lan_ip()}", 1)
                break
        result = firetv.open_url(url)
    else:
        raise ValueError("action must be wake, sleep, or open_url.")
    if isinstance(result, dict):
        return {"ok": True, **result}
    return {
        "ok": True,
        "message": str(result) if result is not None else f"FireTV {action} sent.",
    }


def music_director_config(config: dict | None = None) -> dict:
    """The 'music_director' section of config.json ({} when absent)."""
    if config is None:
        config = lightctl.load_config()
    section = config.get("music_director")
    return dict(section) if isinstance(section, dict) else {}


def set_music_director_enabled(enabled: bool) -> dict:
    """Persist the Music Director on/off switch into config.json (merge only)."""
    config = lightctl.load_config()
    md_cfg = music_director_config(config)
    md_cfg["enabled"] = bool(enabled)
    config["music_director"] = md_cfg
    lightctl.save_config(config)
    return md_cfg


def music_director_status_payload() -> dict:
    """GET /api/music-director payload: live director status plus the enabled flag
    (enabled = running). Never raises; degrades gracefully when music_director.py
    is not installed yet."""
    try:
        import music_director  # lazy: module is built in parallel and may be missing

        status = music_director.director_status()
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Music Director unavailable: {exc}",
            "enabled": False,
        }
    enabled = bool(isinstance(status, dict) and status.get("running"))
    return {"ok": True, "status": status, "enabled": enabled}


def tv_trivia_payload() -> dict:
    """GET /api/tv-trivia payload: ticker trivia for the currently playing artist.
    Never raises; degrades gracefully when tv_trivia.py is not installed yet."""
    track = None
    try:
        import music_director  # lazy: module is built in parallel and may be missing

        status = music_director.director_status()
        if isinstance(status, dict):
            track = status.get("track")
    except Exception:
        track = None
    try:
        import tv_trivia  # lazy: module is built in parallel and may be missing

        return tv_trivia.trivia_payload(track, ai_settings())
    except Exception:
        return {"ok": True, "artist": None, "items": []}


def set_music_director(client: Any, enabled: bool) -> dict:
    import music_director

    cfg = music_director_config()
    if enabled:
        kwargs: dict[str, Any] = {"ai_settings": ai_settings()}
        if cfg.get("poll_s") is not None:
            kwargs["poll_s"] = float(cfg["poll_s"])
        if cfg.get("idle_atmosphere"):
            kwargs["idle_atmosphere"] = str(cfg["idle_atmosphere"])
        if cfg.get("ai_timeout") is not None:
            kwargs["ai_timeout"] = float(cfg["ai_timeout"])
        music_director.start_director(client, **kwargs)
    else:
        music_director.stop_director()
    set_music_director_enabled(enabled)
    status = music_director.director_status()
    return {"ok": True, "status": status, "enabled": bool(status.get("running"))}


def verify_controllers(timeout: float = 2.0) -> dict:
    """Probe all configured controllers concurrently without exposing config secrets."""

    import fleet

    controllers = list(fleet.load_controllers())

    def probe(controller: Any) -> tuple[str, dict[str, Any]]:
        started = time.monotonic()
        entry: dict[str, Any] = {
            "ok": False,
            "version": None,
            "effects": None,
            "leds": None,
            "latency_ms": None,
            "error": None,
        }
        try:
            info = lightctl.LightClient(
                controller.host, timeout=timeout, retries=1
            ).get_info()
            entry["ok"] = True
            entry["version"] = str(info.get("ver") or "") or None
            entry["effects"] = (
                int(info["fxcount"]) if info.get("fxcount") is not None else None
            )
            leds = info.get("leds") if isinstance(info.get("leds"), dict) else {}
            entry["leds"] = leds.get("count")
        except Exception as exc:
            entry["error"] = str(exc)
        entry["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        return controller.name, entry

    with ThreadPoolExecutor(max_workers=max(1, min(4, len(controllers)))) as executor:
        return dict(executor.map(probe, controllers))


def test_ai_connection(timeout: float = 10.0) -> dict:
    try:
        settings = ai_settings()
        if _ai_requires_key(settings) and not _ai_api_key(settings):
            return {
                "ok": False,
                "message": f"{settings['api_key_env']} is not set in the GUI server environment.",
            }
        payload = {
            "model": settings["model"],
            "input": [{"role": "user", "content": "Reply with exactly: pong"}],
            "max_output_tokens": 24,
        }
        data = _provider_json(
            _openai_request(payload, settings), timeout=timeout, retries=0
        )
        with contextlib.suppress(Exception):
            reply = extract_response_text(data).strip()[:120]
            return {
                "ok": True,
                "message": f"OK: {settings['provider']} ({settings['model']}) responded. Reply: {reply}",
            }
        return {
            "ok": True,
            "message": f"OK: {settings['provider']} ({settings['model']}) responded.",
        }
    except Exception as exc:
        return {"ok": False, "message": f"AI test failed: {exc}"}


def make_handler(state: GuiState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "lights-gui/2"

        def read_json_body(
            self, max_bytes: int = MAX_JSON_BODY_BYTES
        ) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length is required.")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("Invalid Content-Length.") from exc
            if length < 0 or length > max_bytes:
                raise OverflowError(f"Request body too large (max {max_bytes} bytes).")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid JSON body: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object.")
            return value

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/api/settings":
                self.respond_json({"ok": True, "settings": current_settings()})
                return
            if path == "/api/firetv":
                self.respond_json(firetv_status_payload())
                return
            if path == "/api/music-director":
                self.respond_json(music_director_status_payload())
                return
            if path == "/api/tv-trivia":
                self.respond_json(tv_trivia_payload())
                return
            if path == "/api/system-prompt":
                config = lightctl.load_config()
                override = config.get("system_prompt_override")
                self.respond_json(
                    {
                        "ok": True,
                        "default": tool_system_prompt_in_use({}),
                        "override": (
                            override
                            if isinstance(override, str) and override.strip()
                            else None
                        ),
                        "current": tool_system_prompt_in_use(config),
                    }
                )
                return
            if path == "/api/ai/jobs":
                self.respond_json({"ok": True, "jobs": state.ai_jobs.snapshot()})
                return
            if path.startswith("/api/ai/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                job = state.ai_jobs.get(job_id)
                self.respond_json(
                    {"ok": job["status"] != "missing", "job": job},
                    status=404 if job["status"] == "missing" else 200,
                )
                return
            if path == "/api/now-playing":
                now_playing = get_now_playing_with_shazam_fallback(use_shazam=False)
                self.respond_json(
                    {
                        "ok": bool(now_playing),
                        "now_playing": now_playing,
                        "text": now_playing_text(now_playing),
                    }
                )
                return
            if path == "/api/recognize":
                if not music_recognizer.can_identify_song():
                    self.respond_json(
                        {
                            "ok": False,
                            "error": "Music recognition unavailable (need shazamio + pydub)",
                        },
                        status=503,
                    )
                    return
                self.respond_json(
                    {
                        "ok": False,
                        "error": "Browser audio is required for song recognition.",
                    },
                    status=400,
                )
                return
            if path == "/api/match-lights":
                self.respond_json(
                    {
                        "ok": False,
                        "error": "Use POST /api/match-lights to change lighting.",
                    },
                    status=405,
                )
                return
            if path == "/api/state":
                try:
                    if hasattr(state, "api_state"):
                        st = state.api_state()
                    elif hasattr(state, "get_state_throttled"):
                        st = state.get_state_throttled()
                    else:
                        st = state.client.get_state()
                    self.respond_json(
                        {
                            "ok": True,
                            "state": st,
                            "fleet": bool(getattr(state, "is_fleet", False)),
                            "targets": (
                                state.target_names()
                                if hasattr(state, "target_names")
                                else ["all"]
                            ),
                            "channels": (
                                state.channel_map()
                                if hasattr(state, "channel_map")
                                else {}
                            ),
                        }
                    )
                except Exception as exc:
                    is_off = getattr(state, "is_offline", False)
                    if is_off:
                        self.respond_json(
                            {
                                "ok": False,
                                "error": "WLED controller is offline",
                                "offline": True,
                            },
                            status=503,
                        )
                    else:
                        logger.exception("Error reading state")
                        self.respond_json({"ok": False, "error": str(exc)}, status=500)
                return
            if path == "/api/suggestions":
                try:
                    if hasattr(state, "primary_state"):
                        st = state.primary_state()
                    elif hasattr(state, "get_state_throttled"):
                        st = state.get_state_throttled()
                    else:
                        st = state.client.get_state()
                except Exception:
                    st = getattr(state, "cached_state", None)
                self.respond_json(
                    {
                        "ok": True,
                        "suggestions": smart_suggestions(st, get_now_playing()),
                    }
                )
                return
            if path == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    tick = 0
                    while True:
                        auto_status = {}
                        if hasattr(state, "autonomous"):
                            auto_status = state.autonomous.status()

                        try:
                            if hasattr(state, "api_state"):
                                st = state.api_state()
                            elif hasattr(state, "get_state_throttled"):
                                st = state.get_state_throttled()
                            else:
                                st = state.client.get_state()
                            primary = (
                                state.primary_state(st)
                                if hasattr(state, "primary_state")
                                else st
                            )

                            intel = {}
                            # always try to include leds capabilities for visualizer accuracy
                            try:
                                if hasattr(state, "get_info_throttled"):
                                    info = state.get_info_throttled()
                                else:
                                    info = state.client.get_info()

                                leds = info.get("leds", {}) or {}
                                intel["leds"] = leds
                                if tick % 4 == 0:
                                    intel["name"] = info.get("name")
                                    intel["ver"] = info.get("ver")
                                    intel["wifi"] = info.get("wifi", {})
                                    intel["uptime"] = info.get("uptime")
                                    intel["freeheap"] = info.get("freeheap")
                                    intel["fps"] = leds.get("fps")
                                    intel["pwr"] = leds.get("pwr")
                                    intel["ps"] = primary.get("ps")
                                    intel["pl"] = primary.get("pl")
                                    seg0 = (primary.get("seg") or [{}])[0]
                                    intel["cct"] = seg0.get("cct")
                            except Exception:
                                pass

                            payload = json.dumps(
                                {
                                    "state": st,
                                    "fleet": bool(getattr(state, "is_fleet", False)),
                                    "targets": (
                                        state.target_names()
                                        if hasattr(state, "target_names")
                                        else ["all"]
                                    ),
                                    "channels": (
                                        state.channel_map()
                                        if hasattr(state, "channel_map")
                                        else {}
                                    ),
                                    "autonomous": auto_status,
                                    "mood": (
                                        state.mood_session.status()
                                        if hasattr(state, "mood_session")
                                        else {}
                                    ),
                                    "ai_jobs": (
                                        state.ai_jobs.snapshot()
                                        if hasattr(state, "ai_jobs")
                                        else []
                                    ),
                                    "intel": intel,
                                }
                            )
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        except Exception:
                            payload = json.dumps(
                                {
                                    "error": "device_unavailable",
                                    "autonomous": auto_status,
                                }
                            )
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))

                        try:
                            self.wfile.flush()
                        except BrokenPipeError, ConnectionResetError:
                            break
                        time.sleep(SSE_INTERVAL_S)
                        tick += 1
                except BrokenPipeError, ConnectionResetError:
                    pass
                return
            if path == "/wled-logo.png":
                try:
                    logo_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "wled-logo.png"
                    )
                    if os.path.exists(logo_path):
                        with open(logo_path, "rb") as f:
                            logo_data = f.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.send_header("Content-Length", str(len(logo_data)))
                        self.send_header("Cache-Control", "public, max-age=86400")
                        self.end_headers()
                        self.wfile.write(logo_data)
                    else:
                        self.send_error(404, "Logo file not found")
                except Exception as exc:
                    logger.exception("Error serving logo")
                    self.send_error(500, str(exc))
                return

            if path == "/tv":
                body = tv_ambient_html().encode("utf-8")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                except BrokenPipeError, ConnectionResetError, OSError:
                    pass
                return

            if path != "/":
                self.send_error(404)
                return
            body = render_html().encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
            except BrokenPipeError, ConnectionResetError, OSError:
                pass

        def do_HEAD(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path not in API_HEAD_PATHS:
                self.send_error(404)
                return
            if path == "/wled-logo.png":
                try:
                    logo_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "wled-logo.png"
                    )
                    if os.path.exists(logo_path):
                        logo_size = os.path.getsize(logo_path)
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.send_header("Content-Length", str(logo_size))
                        self.send_header("Cache-Control", "public, max-age=86400")
                        self.end_headers()
                    else:
                        self.send_error(404)
                except Exception:
                    self.send_error(500)
                return
            if path != "/":
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                except BrokenPipeError, ConnectionResetError, OSError:
                    pass
                return
            body = render_html().encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
            except BrokenPipeError, ConnectionResetError, OSError:
                pass

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/ai_vision":
                try:
                    req = self.read_json_body(MAX_VISION_BODY_BYTES)
                    image_base64 = req.get("image")
                    if not isinstance(image_base64, str) or not image_base64:
                        raise ValueError("Missing image data in request.")
                    self.respond_json(
                        call_openai_vision_for_plan(image_base64, client=state.client)
                    )
                except OverflowError as exc:
                    self.respond_json({"ok": False, "error": str(exc)}, status=413)
                except ValueError as exc:
                    self.respond_json({"ok": False, "error": str(exc)}, status=400)
                except Exception:
                    logger.exception("Vision request failed")
                    self.respond_json(
                        {"ok": False, "error": "Vision request failed."}, status=500
                    )
                return

            if path not in API_POST_PATHS:
                self.send_error(404)
                return
            try:
                data = self.read_json_body()
                if path == "/api/settings":
                    if not isinstance(data, dict):
                        raise ValueError("Settings body must be a JSON object.")
                    merge_settings_into_config(data)
                    self.respond_json({"ok": True})
                    return
                if path == "/api/firetv":
                    if not isinstance(data, dict):
                        raise ValueError("FireTV body must be a JSON object.")
                    if "enabled" in data:
                        firetv_cfg = set_firetv_enabled(bool(data.get("enabled")))
                        self.respond_json(
                            {"ok": True, "enabled": firetv_cfg["enabled"]}
                        )
                        return
                    self.respond_json(run_firetv_action(data))
                    return
                if path == "/api/music-director":
                    if not isinstance(data, dict):
                        raise ValueError("Music Director body must be a JSON object.")
                    try:
                        self.respond_json(
                            set_music_director(state.client, bool(data.get("enabled")))
                        )
                    except Exception as exc:
                        logger.exception("Music Director toggle failed")
                        self.respond_json(
                            {
                                "ok": False,
                                "error": f"Music Director unavailable: {exc}",
                            },
                            status=503,
                        )
                    return
                if path == "/api/settings/reload":
                    names = state.reload_controllers()
                    self.respond_json({"ok": True, "controllers": names})
                    return
                if path == "/api/system-prompt":
                    prompt = data.get("prompt")
                    config = lightctl.load_config()
                    if prompt is None or (
                        isinstance(prompt, str) and not prompt.strip()
                    ):
                        config.pop("system_prompt_override", None)
                        lightctl.save_config(config)
                        self.respond_json(
                            {
                                "ok": True,
                                "override": None,
                                "current": tool_system_prompt_in_use(config),
                            }
                        )
                        return
                    prompt_text = str(prompt)
                    if len(prompt_text) > SYSTEM_PROMPT_OVERRIDE_MAX_CHARS:
                        raise ValueError(
                            f"System prompt override too long (max {SYSTEM_PROMPT_OVERRIDE_MAX_CHARS} characters)."
                        )
                    config["system_prompt_override"] = prompt_text
                    lightctl.save_config(config)
                    self.respond_json(
                        {
                            "ok": True,
                            "override": prompt_text,
                            "current": tool_system_prompt_in_use(config),
                        }
                    )
                    return
                if path == "/api/controllers/verify":
                    self.respond_json({"ok": True, "results": verify_controllers()})
                    return
                if path == "/api/ai/test":
                    self.respond_json(test_ai_connection())
                    return
                if path == "/api/recognize":
                    # MPRIS first — instant and exact when a player on this
                    # machine is playing; mic/Shazam only as fallback.
                    mpris_np = get_now_playing()
                    if mpris_np and (mpris_np.get("title") or mpris_np.get("artist")):
                        mpris_np.setdefault("source", "mpris")
                        self.respond_json(
                            {
                                "ok": True,
                                "now_playing": mpris_np,
                                "text": now_playing_text(mpris_np),
                            }
                        )
                        return
                    # Client-provided browser mic audio for song ID
                    audio_b64 = data.get("audio") or data.get("audio_b64")
                    shazam_result = None
                    if audio_b64:
                        if not music_recognizer.can_identify_song():
                            self.respond_json(
                                {"ok": False, "error": "Music recognition unavailable"},
                                status=503,
                            )
                            return
                        import base64

                        try:
                            audio_bytes = _decode_base64_blob(
                                audio_b64, max_bytes=6 * 1024 * 1024, label="Audio"
                            )
                            shazam_result = music_recognizer.recognize_audio_bytes_sync(
                                audio_bytes
                            )
                        except Exception:
                            shazam_result = None
                    else:
                        self.respond_json(
                            {
                                "ok": False,
                                "error": "Browser audio is required for song recognition.",
                            },
                            status=400,
                        )
                        return
                    if shazam_result:
                        np = {
                            "title": shazam_result.get("title", ""),
                            "artist": shazam_result.get("artist", ""),
                            "album": shazam_result.get("album", ""),
                            "status": "Playing",
                            "source": "shazam",
                        }
                        self.respond_json(
                            {
                                "ok": True,
                                "now_playing": np,
                                "text": now_playing_text(np),
                            }
                        )
                    else:
                        msg = "No match found."
                        if not audio_b64:
                            # Server-side mic attempt
                            if not music_recognizer.is_available():
                                msg = music_recognizer.available_reason()
                            else:
                                msg = "No match found (microphone may be unavailable or silent)."
                        self.respond_json({"ok": False, "error": msg})
                    return
                if path == "/api/match-lights":
                    # Prefer the same now-playing metadata shown in the browser. Audio remains
                    # available for explicit legacy callers, but the main UI does not need it.
                    provided_now_playing = data.get("now_playing")
                    audio_b64 = data.get("audio") or data.get("audio_b64")
                    provided_np = None
                    used_client_mic = bool(audio_b64)
                    if isinstance(provided_now_playing, dict):
                        provided_np = provided_now_playing
                    elif used_client_mic and music_recognizer.can_identify_song():
                        import base64

                        try:
                            audio_bytes = _decode_base64_blob(
                                audio_b64, max_bytes=6 * 1024 * 1024, label="Audio"
                            )
                            sh = music_recognizer.recognize_audio_bytes_sync(
                                audio_bytes
                            )
                            if sh:
                                provided_np = {
                                    "title": sh.get("title", ""),
                                    "artist": sh.get("artist", ""),
                                    "album": sh.get("album", ""),
                                    "genre": sh.get("genre", ""),
                                    "status": "Playing",
                                    "source": "shazam",
                                }
                        except Exception:
                            pass
                    if isinstance(provided_now_playing, dict):
                        now_playing_for_match = provided_np
                    elif used_client_mic:
                        # Respect explicit mic capture result (even if None = no song found)
                        now_playing_for_match = provided_np
                    else:
                        now_playing_for_match = get_now_playing()
                    result = match_lights_to_song(
                        state.client, now_playing=now_playing_for_match
                    )
                    if result.get("ok") and hasattr(state, "invalidate_caches"):
                        state.invalidate_caches()
                    self.respond_json(
                        {
                            "ok": result["ok"],
                            "message": result.get("message", ""),
                            "response": result.get("response", ""),
                            "confirmations": result.get("confirmations", ""),
                            "now_playing": result.get("now_playing"),
                        }
                    )
                    return
                if path == "/api/mood/sample":
                    audio_b64 = data.get("audio_b64") or data.get("audio")
                    if not audio_b64:
                        self.respond_json(
                            {"ok": False, "error": "audio_b64 is required"}, status=400
                        )
                        return
                    import base64

                    try:
                        audio_bytes = _decode_base64_blob(
                            audio_b64, max_bytes=6 * 1024 * 1024, label="Audio"
                        )
                    except Exception as exc:
                        self.respond_json(
                            {"ok": False, "error": f"Invalid audio data: {exc}"},
                            status=400,
                        )
                        return

                    if not state.submit_mood_sample(audio_bytes):
                        self.respond_json(
                            {
                                "ok": False,
                                "error": "Mood sample processor is busy",
                                **state.mood_session.status(),
                            },
                            status=429,
                        )
                        return
                    self.respond_json({"ok": True, **state.mood_session.status()})
                    return

                if path == "/api/mood/control":
                    command = str(data.get("command", "")).strip().lower()
                    if command == "start":
                        state.mood_session.start()
                        message = "Mood session started."
                    elif command == "stop":
                        state.mood_session.stop()
                        message = "Mood session stopped."
                    elif command == "status":
                        message = "OK"
                    else:
                        self.respond_json(
                            {
                                "ok": False,
                                "error": "command must be start, stop, or status",
                            },
                            status=400,
                        )
                        return
                    self.respond_json(
                        {"ok": True, "message": message, **state.mood_session.status()}
                    )
                    return

                if path == "/api/ai":
                    prompt = str(data.get("prompt", "")).strip()
                    if not prompt:
                        raise ValueError("Prompt is required.")
                    if len(prompt) > _AI_MAX_PROMPT_LEN:
                        self.respond_json(
                            {"ok": False, "error": "Prompt too long (max 2000 chars)."},
                            status=400,
                        )
                        return
                    now_playing = data.get("now_playing")
                    if not isinstance(now_playing, dict):
                        now_playing = get_now_playing()

                    def _execute_ai_request() -> dict[str, Any]:
                        import ai_chat

                        if not _ai_execution_lock.acquire(blocking=False):
                            raise ValueError(
                                "Another AI lighting request is already running. Try again when it completes."
                            )
                        try:
                            settings = ai_settings()
                            context_text = ai_context_text(state.client, now_playing)
                            try:
                                result = ai_chat.run_chat(
                                    state.client,
                                    prompt,
                                    system_prompt=tool_system_prompt_in_use(),
                                    settings=settings,
                                    context_text=context_text,
                                )
                                confirmations = result.get("log") or [
                                    str(result.get("text") or "Done")[:120]
                                ]
                                if hasattr(state, "invalidate_caches"):
                                    state.invalidate_caches()
                                return {
                                    "ok": True,
                                    "message": f"AI chat complete ({len(result.get('log') or [])} tool call(s), {result.get('rounds', 0)} round(s)).",
                                    "response": str(result.get("text") or "Done.")[
                                        :300
                                    ],
                                    "confirmations": confirmations[:8],
                                    "client_actions": [],
                                    "events": result.get("events", []),
                                }
                            except ai_chat.ToolChatError as exc:
                                message = str(exc).casefold()
                                if not any(
                                    token in message
                                    for token in (
                                        "tool",
                                        "http 400",
                                        "http 404",
                                        "unsupported",
                                    )
                                ):
                                    raise
                                plan = call_openai_for_plan(
                                    prompt, now_playing, _device_snapshot(state.client)
                                )
                                applied = apply_ai_plan(
                                    state.client,
                                    plan,
                                    target=str(
                                        data.get("target")
                                        or getattr(state, "target", "all")
                                        or "all"
                                    ),
                                )
                                if hasattr(state, "invalidate_caches"):
                                    state.invalidate_caches()
                                return {"ok": True, **applied}
                        finally:
                            _ai_execution_lock.release()

                    if bool(data.get("async")):
                        job_id = state.ai_jobs.submit(_execute_ai_request)
                        self.respond_json(
                            {"ok": True, "job_id": job_id, "status": "queued"},
                            status=202,
                        )
                        return
                    result = _execute_ai_request()
                    if result.get("ok") and hasattr(state, "invalidate_caches"):
                        state.invalidate_caches()
                    self.respond_json(result)
                    return
                else:
                    action = str(data.get("action", ""))
                    if action == "mode1_start":
                        message = state.start_mode1()
                    elif action == "mode1_stop":
                        message = state.stop_mode1()
                    elif action == "fade_off":
                        minutes = float(data.get("minutes", 30))
                        start_brightness = data.get("brightness")
                        if start_brightness is not None:
                            start_brightness = int(start_brightness)
                        message = state.start_fade(minutes, brightness=start_brightness)
                    elif action == "save_scene":
                        st = (
                            state.primary_state()
                            if hasattr(state, "primary_state")
                            else state.client.get_state()
                        )
                        payload: lightctl.WledPayload = {}
                        for key in ("on", "bri", "seg", "transition"):
                            if key in st:
                                payload[key] = st[key]  # type: ignore[literal-required]
                        lightctl.save_scene(str(data.get("name", "custom")), payload)
                        message = f"Saved scene '{data.get('name', 'custom')}'."
                    elif action == "cycle_start":
                        message = state.start_cycle(float(data.get("interval", 60)))
                    elif action == "cycle_stop":
                        message = state.stop_cycle()
                    elif action == "sunrise_start":
                        message = state.start_sunrise(
                            float(data.get("minutes", 30)),
                            int(data.get("brightness", 255)),
                        )
                    elif action == "sunrise_stop":
                        message = state.stop_sunrise()
                    elif action == "autonomous_start":
                        message = state.autonomous.start()
                    elif action == "autonomous_stop":
                        message = state.autonomous.stop()
                    elif action == "restart":
                        try:
                            _post_state(
                                state.client,
                                lightctl.restart_payload(),
                                target=str(data.get("target") or "all"),
                            )
                        except RuntimeError, urllib.error.HTTPError:
                            pass  # Device may drop connection before responding
                        message = "Restart command sent. Device will reconnect in a few seconds."
                    elif action == "schedule":
                        subaction = str(data.get("subaction", ""))
                        if subaction == "add":
                            sched_data: dict[str, Any] = {}
                            scene_name = data.get("scene_name")
                            if scene_name:
                                sched_data["scene"] = str(scene_name)
                            lightctl.add_schedule(
                                str(data.get("time", "00:00")),
                                str(data.get("action", "on")),
                                sched_data,
                            )
                            message = "Schedule added."
                        elif subaction == "remove":
                            lightctl.remove_schedule(int(data.get("index", -1)))
                            message = "Schedule removed."
                        elif subaction == "list":
                            entries = lightctl.list_schedule()
                            self.respond_json(
                                {"ok": True, "entries": entries, "message": "OK"}
                            )
                            return
                        else:
                            message = "Unknown schedule subaction."
                    else:
                        if action in WALL_ACTIONS:
                            message = _apply_wall_action(
                                state.client, {"action": action, **data}
                            )
                        else:
                            target = str(
                                data.get("target")
                                or getattr(state, "target", "all")
                                or "all"
                            )
                            _post_state(
                                state.client,
                                payload_for_action(action, data),
                                target=target,
                            )
                            if hasattr(state, "invalidate_caches"):
                                state.invalidate_caches()
                            message = f"Sent {action}."
                if hasattr(state, "invalidate_caches"):
                    state.invalidate_caches()
                self.respond_json({"ok": True, "message": message})
            except OverflowError as exc:
                self.respond_json({"ok": False, "error": str(exc)}, status=413)
            except ValueError as exc:
                self.respond_json({"ok": False, "error": str(exc)}, status=400)
            except Exception:
                logger.exception("Server error")
                self.respond_json(
                    {"ok": False, "error": "Internal server error"}, status=500
                )

        def respond_json(self, payload: dict, status: int = 200) -> None:
            try:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
            except BrokenPipeError, ConnectionResetError, OSError:
                # Client disconnected (tab close, refresh during slow AI call, etc.).
                # No point logging or crashing the handler thread.
                pass

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


class LightsThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start the browser GUI for the bedroom LED controller."
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Force single-controller mode with this host (back-compat; default is fleet mode from config)",
    )
    parser.add_argument(
        "--target",
        default="all",
        help="Default target for actions: all, a controller name, or a channel name",
    )
    parser.add_argument(
        "--listen",
        default="0.0.0.0",
        help="Bind address (default 0.0.0.0 so the FireTV can reach /tv; use 127.0.0.1 for local-only)",
    )
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    loaded = lightctl.load_dotenv()
    if loaded:
        logging.getLogger("light_gui").info("loaded %d env var(s) from .env", loaded)

    info_client = None
    if args.host:
        client: Any = lightctl.LightClient(args.host, dry_run=args.dry_run)
    else:
        client, info_client = _build_fleet_client(dry_run=args.dry_run)
        logger.info(
            "Fleet mode: controllers=%s channels=%s",
            client.names(),
            list(client.channels()),
        )
    state = GuiState(
        client, target=args.target, info_client=info_client, dry_run=args.dry_run
    )
    server = LightsThreadingHTTPServer(
        (args.listen, args.port),
        make_handler(state),
    )
    if music_director_config().get("enabled"):
        try:
            import music_director  # lazy: module is built in parallel and may be missing

            logger.info("Auto-starting Music Director (enabled in config).")
            cfg = music_director_config()
            kwargs: dict[str, Any] = {"ai_settings": ai_settings()}
            if cfg.get("poll_s") is not None:
                kwargs["poll_s"] = float(cfg["poll_s"])
            if cfg.get("idle_atmosphere"):
                kwargs["idle_atmosphere"] = str(cfg["idle_atmosphere"])
            music_director.start_director(state.client, **kwargs)
        except Exception:
            logger.exception("Music Director auto-start failed")
    if args.listen not in ("127.0.0.1", "::1", "localhost"):
        logger.warning(
            "GUI is listening on the LAN without built-in authentication; restrict access with the host firewall or reverse proxy."
        )
    logger.info("GUI running at http://%s:%d/", args.listen, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped.")
    finally:
        with contextlib.suppress(Exception):
            state.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
