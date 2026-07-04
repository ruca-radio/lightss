#!/usr/bin/env python3
"""Browser GUI for the bedroom Wi-Fi LED controller."""

from __future__ import annotations

import argparse
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import lightctl
import mood_orchestrator
import music_recognizer

logger = logging.getLogger("light_gui")

DEFAULT_AI_MODEL = "gpt-5.2"

AI_ACTIONS = [
    "on",
    "off",
    "brightness",
    "color",
    "effect",
    "scene",
    "temperature",
    "random",
    "preset",
    "save_preset",
    "delete_preset",
    "playlist",
    "palette",
    "nightlight",
    "udp_sync",
    "native_audio_reactive",
    "segment_options",
    "mode1_start",
    "mode1_stop",
    "fade_off",
    "cycle_start",
    "cycle_stop",
    "sunrise_start",
    "sunrise_stop",
    "save_scene",
    "delete_scene",
    "schedule_add",
    "schedule_remove",
    "music_detect",
    "music_listen",
    "music_match",
]

CLIENT_ACTIONS = {
    "mode1_start",
    "mode1_stop",
    "fade_off",
    "cycle_start",
    "cycle_stop",
    "sunrise_start",
    "sunrise_stop",
    "music_detect",
    "music_listen",
    "music_match",
}


def ai_action_reference() -> str:
    return (
        "Available AI actions:\n"
        "- on/off: direct power control.\n"
        "- brightness: global brightness 0-255.\n"
        "- color: primary RGBW channels red/green/blue/white 0-255. Optionally set secondary color "
        "(red2/green2/blue2/white2) and tertiary color (red3/green3/blue3/white3) for effects that use "
        "multiple color slots — check 'Safe effect parameter hints' in the device snapshot.\n"
        "- temperature: Kelvin 2000-6500 converted to RGBW.\n"
        "- effect: safe WLED effect id with speed 0-255, optional intensity 0-255, palette id 0-N "
        "(use palette name from 'All palettes' list in snapshot), and c1/c2/c3 0-255 (meanings per "
        "effect listed in 'Safe effect parameter hints'). Always include primary color; add secondary/"
        "tertiary colors when the hint shows 'colors 1+2' or 'colors 1+2+3'.\n"
        "- palette: WLED palette id 0-N for the active segment. Choose by name from the palette list.\n"
        "- scene/random/preset/playlist: named scene, random safe scene, WLED preset by id "
        "(choose from 'Saved WLED presets' in snapshot), or playlist id.\n"
        "- save_preset/delete_preset: save current state as a native WLED preset with preset_id and optional name, or delete a preset by preset_id.\n"
        "- nightlight: WLED nightlight on/off, duration minutes, mode, and target brightness.\n"
        "- udp_sync: WLED UDP send/receive sync toggles.\n"
        "- native_audio_reactive: device AudioReactive usermod on/off when installed.\n"
        "- segment_options: active segment on/off, freeze, reverse, mirror, brightness, CCT, grouping, spacing, offset.\n"
        "- mode1_start/mode1_stop: desktop/browser microphone reactive beat mode.\n"
        "- fade_off/cycle_start/cycle_stop/sunrise_start/sunrise_stop: local timer automations.\n"
        "- save_scene/delete_scene: local custom scene management.\n"
        "- schedule_add/schedule_remove: local schedule management with time HH:MM and action on/off/scene.\n"
        "- music_detect/music_match: now-playing media lookup and metadata-matched lighting.\n"
        "One-shot examples:\n"
        "- 'soft ocean for 20 minutes then off' -> scene ocean, nightlight on duration 20 target brightness 0.\n"
        "- 'make it pulse with the song' -> safe color/effect setup plus mode1_start.\n"
        "- 'use the device audio reactive mode' -> native_audio_reactive enabled true.\n"
        "- 'wake me up over 30 minutes' -> sunrise_start minutes 30.\n"
        "- 'sync this WLED to the room group' -> udp_sync send true recv true.\n"
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
        for label, key in ((c1_label, "c1"), (c2_label, "c2"), (c3_label, "c3"),
                           (sx_label, "sx"), (ix_label, "ix")):
            if label:
                hint_parts.append(f"{key}={label}")
        if hint_parts:
            hints[effect_id] = "; ".join(hint_parts)
    return hints


def device_snapshot_text(snapshot: dict | None) -> str:
    if not snapshot:
        return "Current WLED device snapshot: unavailable."
    state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
    info = snapshot.get("info") if isinstance(snapshot.get("info"), dict) else {}
    config = snapshot.get("config") if isinstance(snapshot.get("config"), dict) else {}
    effects = snapshot.get("effects") if isinstance(snapshot.get("effects"), list) else []
    palettes = snapshot.get("palettes") if isinstance(snapshot.get("palettes"), list) else []
    fxdata = snapshot.get("fxdata") if isinstance(snapshot.get("fxdata"), list) else []
    presets_raw = snapshot.get("presets") if isinstance(snapshot.get("presets"), dict) else {}
    seg = state.get("seg", [{}])[0] if state.get("seg") else {}
    leds = info.get("leds", {}) if isinstance(info.get("leds"), dict) else {}
    light_cfg = config.get("light", {}) if isinstance(config.get("light"), dict) else {}
    transition_cfg = light_cfg.get("tr", {}) if isinstance(light_cfg.get("tr"), dict) else {}
    nightlight_cfg = light_cfg.get("nl", {}) if isinstance(light_cfg.get("nl"), dict) else {}
    usermods = config.get("um", {}) if isinstance(config.get("um"), dict) else {}
    audio_reactive_cfg = usermods.get("AudioReactive", {}) if isinstance(usermods.get("AudioReactive"), dict) else {}
    sync_cfg = config.get("if", {}).get("sync", {}) if isinstance(config.get("if"), dict) else {}
    live_cfg = config.get("if", {}).get("live", {}) if isinstance(config.get("if"), dict) else {}

    lines = [
        "Current WLED device snapshot:",
        f"Device: {info.get('name', 'unknown')} WLED {info.get('ver', '?')} at {info.get('ip', '?')}",
        f"LEDs: count={leds.get('count', '?')}, rgbw={leds.get('rgbw', '?')}, cct={leds.get('cct', '?')}, maxseg={leds.get('maxseg', '?')}",
        f"State: power={'on' if state.get('on') else 'off'}, bri={state.get('bri', '?')}, transition={state.get('transition', '?')}, preset={state.get('ps', '?')}, playlist={state.get('pl', '?')}",
        (
            "Active segment: "
            f"fx={seg.get('fx', '?')}, sx={seg.get('sx', '?')}, ix={seg.get('ix', '?')}, pal={seg.get('pal', '?')}, "
            f"cct={seg.get('cct', '?')}, colors={seg.get('col', [])}, on={seg.get('on', '?')}, "
            f"freeze={seg.get('frz', '?')}, reverse={seg.get('rev', '?')}, mirror={seg.get('mi', '?')}"
        ),
        f"Nightlight state: {state.get('nl', {})}",
        f"UDP sync state: {state.get('udpn', {})}",
        f"AudioReactive state: {state.get('AudioReactive', {})}; config: {audio_reactive_cfg}",
        f"Config defaults: transition={transition_cfg}, nightlight={nightlight_cfg}",
        f"Sync config: {sync_cfg}; live config: {live_cfg}",
        f"Effects available: {len(effects)} total; safe ids: {lightctl.SAFE_EFFECTS}",
        "All palettes (use id number when setting palette):\n  "
        + "\n  ".join(f"{i}: {name}" for i, name in enumerate(palettes)),
    ]
    fx_hints = _parse_fxdata_hints(fxdata)
    if fx_hints:
        hint_lines = "\n  ".join(
            f"{eid} {lightctl.SAFE_EFFECTS[eid]}: {hint}"
            for eid, hint in fx_hints.items()
            if eid in lightctl.SAFE_EFFECTS
        )
        lines.append(f"Safe effect parameter hints (colors/c1/c2/c3/sx/ix meanings):\n  {hint_lines}")
    if presets_raw:
        preset_entries = sorted(
            ((k, v) for k, v in presets_raw.items() if isinstance(v, dict) and v.get("n")),
            key=lambda x: int(x[0]) if str(x[0]).isdigit() else 9999,
        )
        if preset_entries:
            lines.append(
                "Saved WLED presets: "
                + ", ".join(f"{pid}={p['n']}" for pid, p in preset_entries)
            )
    return "\n".join(lines)

from light_gui_html import HTML_TEMPLATE


@functools.lru_cache(maxsize=1)
def _render_html_cached() -> str:
    effect_options = "\n            ".join(
        f'<option value="{effect_id}">{name}</option>' for effect_id, name in lightctl.SAFE_EFFECTS.items()
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
    return ", ".join(f"{effect_id}={name}" for effect_id, name in lightctl.SAFE_EFFECTS.items())


def system_knowledge_prompt() -> str:
    return '''### ROLE
You are the AI lighting director for a bedroom Wi-Fi LED controller. You convert
natural-language requests into validated WLED JSON state payloads. You are decisive,
safety-aware, and never override what the user explicitly asks for.

### CONTEXT — DEVICE & API
- Hardware: WLED-compatible controller. Control is performed by POSTing a validated
  JSON state payload to /json/state (full JSON API: https://kno.wled.ge/interfaces/json-api/).
- The live device also exposes /json, /json/info, /json/effects (or /eff),
  /json/palettes (or /pal). Realtime input via E1.31/Art-Net/DDP
  (https://kno.wled.ge/interfaces/e1.31-dmx/). Normal control = JSON state posts.
- A "device snapshot" may be provided with each request. Read it before acting:
  power, brightness, active segment (fx/sx/ix/pal/cct/colors), nightlight, UDP sync,
  AudioReactive state, all palettes by id, "Safe effect parameter hints" (color-slot +
  c1/c2/c3/sx/ix meanings per effect), and "Saved WLED presets".

### ACTION SCHEMA
Emit exactly one action object. The full WLED JSON API surface is available — power
(on/off or toggle 't'), global/segment brightness 0-255, RGBW color channels
(red/green/blue/white + red2/green2/blue2/white2 + red3/...), safe effects (fx),
speed (sx), intensity (ix), custom params (c1/c2/c3, o1/o2/o3), palettes (pal),
segment options (id/start/stop/len/grp/spc/of/sel/rev/mi/rY/mY/tp/on/frz/cct/m12/si/
fxdef/set/rpt), individual LEDs (i array), mainseg, cct (0-255 or Kelvin), one-shot
transition (tt), live/lor, nightlight (nl.on/dur/mode/tbri), udpn sync, playlists
(pl or full object), presets (ps/psave/pdel), ledmap, rmcpal, np, time, rb (reboot).

### Available AI actions
- on/off, brightness, color, temperature, effect, scene, random, preset
- save_preset, delete_preset, nightlight, udp_sync, native_audio_reactive, segment_options
- mode1_start, mode1_stop, fade_off, cycle_start, cycle_stop, sunrise_start, sunrise_stop
- save_scene, delete_scene, schedule_add, schedule_remove, music_detect, music_listen, music_match

One-shot examples (intent → action):
- "soft ocean for 20 minutes then off" → scene ocean + nightlight on, duration 20, target brightness 0.
- "make it pulse with the song" → safe color/effect setup + mode1_start.
- "use the device audio reactive mode" → native_audio_reactive enabled true.
- "wake me up over 30 minutes" → sunrise_start minutes 30.
- "sync this WLED to the room group" → udp_sync send true recv true.
- "ocean chase two-tone blue and teal" → effect Chase, blue primary, teal secondary.

### COLOR HANDLING
- Every LED color is RGBW: any value in [0,0,0,0]..[255,255,255,255]. Named colors →
  translate to RGBW. Hex (#ff6600) accepted. Use the white channel for soft pastel,
  warm, or room-light looks.
- Multi-slot effects: when the snapshot hint shows "colors 1+2" or "colors 1+2+3",
  ALWAYS set secondary (col1) and tertiary (col2) colors — effects look dramatically
  better with all slots filled.
- Palettes: 70+ named (Ocean, Forest, Party, Rainbow, Sunset...). Choose by id from
  the snapshot list, matching the name to the mood. Effects with palette support
  ignore color slots and use the palette instead.

### NOW-PLAYING MUSIC (when a Now-playing object is provided)
Use title, artist, album, genre, and playback status to infer mood, energy, palette,
speed, and effect style. Genre is the strongest signal:
- jazz/acoustic → chill warm colors, slow flow effects.
- EDM/pop → vibrant rainbows, fast chase.
- metal/dark ambient → deep reds/purples, slow pulse.
- reggae/funk → bright warm tones.
Mention the song in your response when it fits.

### CONSTRAINTS (hard rules — never violate)
1. SAFETY: Never use blink, strobe, flash, lightning, sparkle, fireworks, or any
   seizure-like effect. Motion requests → use chase / rainbow / flow effects only.
   Allowed effect ids: __SAFE_EFFECTS__.
2. EXPLICIT VALUES ARE INSTRUCTIONS, NOT SUGGESTIONS. When the user gives an exact
   number — "brightness 241", "RGBW 255 0 0 0", "5000K", "effect 28 speed 200" — use
   that exact value. Do not substitute, approximate, round, or override it with your
   own aesthetic judgment.
3. One action object per response. Every emitted color/effect/palette/preset id must
   exist in the snapshot or the allowed lists above. If unsure an id is valid, do not
   guess — omit it or pick a known-safe default.

### OUTPUT FORMAT (strict)
Respond ONLY with a single JSON object, no prose outside it:
{
  "action": { /* validated WLED state payload or higher-level action */ },
  "response": "<100-250 char marquee string>"
}

"response" field — this text scrolls right-to-left in the top UI box (above the Music
Mode card). Make it fun, varied, and scroll-friendly. It MAY describe the lighting
plan, but PREFER (or mix in) music trivia, artist facts, song stories, jokes, hype,
memes — anything engaging tied to the song/request/mood. Vary it; do not always give
straight lighting instructions. Keep it punchy (100-250 chars so it scrolls cleanly).
Mention the song/artist when it fits. Be creative and entertaining.

Always include a confirmation of the operation(s) inside or alongside the response text.

### SELF-CHECK (internal, before emitting — do not print)
- Did I honor every explicit numeric value the user gave? (highest priority)
- Is the effect on the allowed list and not a strobe/flash/seizure type?
- For a multi-slot effect, did I set secondary/tertiary colors when the hint said so?
- Is "response" 100-250 chars, varied, and not a boilerplate lighting recap?
If any check fails, fix it silently, then emit the final JSON.

### NOW EXECUTE
Translate the user's request (given in the next message, along with the device snapshot
and now-playing info when available) into one validated action + marquee response.
'''.replace("__SAFE_EFFECTS__", safe_effect_prompt())

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
            ["playerctl", "metadata", "--format", "{{playerName}}\n{{artist}}\n{{title}}\n{{album}}"],
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
    except (subprocess.SubprocessError, OSError):
        return None
    parsed = parse_playerctl_metadata(metadata + "\n" + status)
    if not parsed.get("title") and not parsed.get("artist"):
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
    except (subprocess.SubprocessError, OSError):
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
        except (subprocess.SubprocessError, OSError):
            continue
        parsed = parse_mpris_metadata_output(metadata_output)
        parsed["player"] = player.rsplit(".", 1)[-1]
        parsed["status"] = parse_mpris_status_output(status_output)
        if parsed.get("title") or parsed.get("artist"):
            return parsed
    return None


def get_now_playing() -> dict[str, str] | None:
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
        logger.warning("Shazam mic fallback requires sounddevice (browser mic capture works without)")
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


# Sentinel so callers can explicitly pass now_playing=None meaning "I already tried to identify via mic and got nothing"
_NO_SONG = object()

def match_lights_to_song(client: lightctl.LightClient, now_playing: dict[str, str] | None | object = _NO_SONG) -> dict[str, Any]:
    """Detect the currently playing song and ask the AI to create matching lights.

    If now_playing is provided (including explicit None from a failed browser mic capture),
    use it directly. Only auto-detect when the argument was not supplied.
    """
    if now_playing is _NO_SONG:
        now_playing = get_now_playing_with_shazam_fallback(use_shazam=True)
    if not now_playing:
        return {
            "ok": False,
            "message": "No music detected. Try playing a song or using the microphone listen button.",
            "response": "",
            "confirmations": "",
            "client_actions": [],
            "now_playing": None,
        }
    genre = now_playing.get("genre", "")
    prompt = (
        f"The song '{now_playing.get('title', 'Unknown')}' by "
        f"{now_playing.get('artist', 'Unknown')}"
    )
    if genre:
        prompt += f" (genre: {genre})"
    prompt += (
        " is currently playing. Create a light show that matches its mood, energy, and style. "
        "Choose colors, effects, and speed that feel right for this song. "
        "For the response field, make it fun and varied for the top scrolling marquee: include music trivia, artist trivia, song facts, jokes, or hype in addition to the lighting plan."
    )
    try:
        plan = call_openai_for_plan(prompt, now_playing, client.get_device_snapshot())
        result = apply_ai_plan(client, plan)
        result["ok"] = True
        result["now_playing"] = now_playing
        return result
    except Exception as exc:
        logger.exception("Match lights failed")
        return {
            "ok": False,
            "message": str(exc),
            "response": "",
            "confirmations": "",
            "client_actions": [],
            "now_playing": now_playing,
        }


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
    snapshot = client.get_device_snapshot()
    plan = call_openai_for_plan(prompt, song, snapshot)
    actions = plan.get("actions", [])
    # Find the first action that actually produces a WLED payload.
    for action in actions:
        payload = payload_for_ai_action(action)
        if payload:
            return payload
    raise ValueError("AI did not return a usable WLED payload for mood generation")


def build_openai_request(
    prompt: str,
    model: str | None = None,
    now_playing: dict[str, str] | None = None,
    device_snapshot: dict | None = None,
) -> dict:
    if model is None:
        model = os.environ.get("LIGHT_AI_MODEL", os.environ.get("OPENAI_MODEL", DEFAULT_AI_MODEL))
    parts = []
    if now_playing:
        parts.append(f"Background audio now playing: {now_playing_text(now_playing)}")
    if device_snapshot:
        parts.append(device_snapshot_text(device_snapshot))
    parts.append(f"User request: {prompt}")
    user_content = "\n\n".join(parts)
    return {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": system_knowledge_prompt(),
            },
            {"role": "user", "content": user_content},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "light_actions",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "response": {
                            "type": "string",
                            "description": "Fun, engaging text (100-300 chars) for the top scrolling marquee: lighting vibe + music/artist trivia, fun facts, jokes, hype, stories, or any entertaining content related to the song/request. Vary it and make it scroll-worthy.",
                        },
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
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "enum": AI_ACTIONS,
                                    },
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
                                    "kelvin": {"type": ["integer", "null"], "minimum": 2000, "maximum": 6500},
                                    "effect": {"type": ["integer", "null"], "enum": list(lightctl.SAFE_EFFECTS) + [None]},
                                    "speed": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
                                    "intensity": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
                                    "palette": {"type": ["integer", "null"], "minimum": 0, "maximum": 70},
                                    "c1": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
                                    "c2": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
                                    "c3": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
                                    "scene": {
                                        "type": ["string", "null"],
                                        "description": "Scene name (built-in or saved custom)",
                                    },
                                    "transition": {"type": ["integer", "null"], "minimum": 0, "maximum": 2000},
                                    "preset_id": {"type": ["integer", "null"], "minimum": 1, "maximum": 250},
                                    "playlist_id": {"type": ["integer", "null"], "minimum": 1, "maximum": 250},
                                    "name": {"type": ["string", "null"], "description": "Custom name for saved preset or scene"},
                                    "minutes": {"type": ["number", "null"], "minimum": 0.5, "maximum": 120},
                                    "interval": {"type": ["number", "null"], "minimum": 5, "maximum": 3600},
                                    "enabled": {"type": ["boolean", "null"]},
                                    "send": {"type": ["boolean", "null"]},
                                    "receive": {"type": ["boolean", "null"]},
                                    "mode": {"type": ["integer", "null"], "minimum": 0, "maximum": 3},
                                    "target_brightness": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
                                    "schedule_time": {"type": ["string", "null"], "description": "HH:MM schedule time"},
                                    "schedule_action": {"type": ["string", "null"], "enum": ["on", "off", "scene", None]},
                                    "schedule_index": {"type": ["integer", "null"], "minimum": 0},
                                    "segment_on": {"type": ["boolean", "null"]},
                                    "freeze": {"type": ["boolean", "null"]},
                                    "reverse": {"type": ["boolean", "null"]},
                                    "mirror": {"type": ["boolean", "null"]},
                                    "grouping": {"type": ["integer", "null"], "minimum": 1, "maximum": 255},
                                    "spacing": {"type": ["integer", "null"], "minimum": 0, "maximum": 255},
                                    "offset": {"type": ["integer", "null"], "minimum": 0, "maximum": 65535},
                                },
                                "required": [
                                    "action",
                                    "brightness",
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
                                    "kelvin",
                                    "effect",
                                    "speed",
                                    "intensity",
                                    "palette",
                                    "c1",
                                    "c2",
                                    "c3",
                                    "scene",
                                    "transition",
                                    "preset_id",
                                    "playlist_id",
                                    "name",
                                    "minutes",
                                    "interval",
                                    "enabled",
                                    "send",
                                    "receive",
                                    "mode",
                                    "target_brightness",
                                    "schedule_time",
                                    "schedule_action",
                                    "schedule_index",
                                    "segment_on",
                                    "freeze",
                                    "reverse",
                                    "mirror",
                                    "grouping",
                                    "spacing",
                                    "offset",
                                ],
                            },
                        }
                    },
                    "required": ["response", "confirmations", "actions"],
                },
            }
        },
    }


def extract_response_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for item in response.get("output", []):
        for content in item.get("content", []):
            if isinstance(content.get("text"), str):
                return content["text"]
    raise ValueError("OpenAI response did not include text output.")


def parse_ai_plan(text: str) -> dict[str, Any]:
    data = json.loads(text)
    actions = data.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("AI response did not include actions.")
    response = data.get("response")
    if not isinstance(response, str) or not response.strip():
        response = "I applied a safe lighting change."
    confirmations = data.get("confirmations")
    if not isinstance(confirmations, list):
        confirmations = []
    client_actions = []
    for action in actions:
        kind = action.get("action")
        if kind == "mode1_start":
            client_actions.append("startAudioReactive")
        elif kind == "mode1_stop":
            client_actions.append("stopAudioReactive")
        elif kind == "fade_off":
            client_actions.append({"action": "fadeOff", "minutes": float(action.get("minutes") or 30)})
        elif kind == "cycle_start":
            client_actions.append({"action": "startCycle", "interval": float(action.get("interval") or 60)})
        elif kind == "cycle_stop":
            client_actions.append({"action": "stopCycle"})
        elif kind == "sunrise_start":
            target_brightness = action.get("target_brightness")
            client_actions.append(
                {
                    "action": "startSunrise",
                    "minutes": float(action.get("minutes") or 30),
                    "brightness": int(target_brightness) if target_brightness is not None else 255,
                }
            )
        elif kind == "sunrise_stop":
            client_actions.append({"action": "stopSunrise"})
        elif kind == "music_detect":
            client_actions.append({"action": "detectSong"})
        elif kind == "music_listen":
            client_actions.append({"action": "listenForSong"})
        elif kind == "music_match":
            client_actions.append({"action": "matchLightsFromNowPlaying"})
    return {
        "response": response.strip(),
        "confirmations": [str(item) for item in confirmations if str(item).strip()],
        "client_actions": client_actions,
        "actions": actions,
    }


def call_openai_for_plan(
    prompt: str,
    now_playing: dict[str, str] | None = None,
    device_snapshot: dict | None = None,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set in the GUI server environment.")

    model = os.environ.get("LIGHT_AI_MODEL", os.environ.get("OPENAI_MODEL", "gpt-5.2"))
    body = json.dumps(build_openai_request(prompt, model, now_playing, device_snapshot)).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"OpenAI request failed: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"OpenAI request failed: {exc}") from exc
    return parse_ai_plan(extract_response_text(data))


def call_openai_vision_for_plan(image_base64: str, client: lightctl.LightClient) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set in the GUI server environment.")

    model = os.environ.get("LIGHT_AI_MODEL", os.environ.get("OPENAI_MODEL", "gpt-4o"))
    device_snapshot = client.get_device_snapshot()

    # System and user prompts for Vision
    system_prompt = system_knowledge_prompt()
    user_text_content = (
        "Here is a webcam snapshot of the bedroom environment.\n"
        "Analyze the real-world ambient brightness, current room layout, and colors.\n"
        "Map these real-world vibes into a validated WLED action to set a fitting, premium light atmosphere.\n"
        f"Current WLED device snapshot:\n{device_snapshot_text(device_snapshot)}"
    )

    # Build multi-modal body (using OpenAI structure with image_url)
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": user_text_content},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_base64}"
                }}
            ]}
        ],
        "response_format": {"type": "json_object"}
    }).encode("utf-8")

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"OpenAI request failed: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"OpenAI request failed: {exc}") from exc

    # Extract completed plan
    plan_text = data["choices"][0]["message"]["content"]
    parsed = parse_ai_plan(plan_text)
    
    # Apply actions to WLED client
    apply_ai_plan(client, parsed)
    return parsed


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
        seg["cct"] = lightctl.clamp_byte((int(data["kelvin"]) - 2000) * 255 / 4500)
    return {"seg": [seg]} if seg else {}


def payload_for_ai_action(action: dict[str, Any]) -> lightctl.WledPayload:
    def int_or_default(name: str, default: int) -> int:
        value = action.get(name)
        return default if value is None else int(value)

    def optional_int(name: str) -> int | None:
        value = action.get(name)
        return None if value is None else int(value)

    transition_ms = int_or_default("transition", 0)
    kind = str(action.get("action", ""))
    if kind == "on":
        return lightctl.on_payload(True, transition_ms=transition_ms)
    if kind == "off":
        return lightctl.on_payload(False, transition_ms=transition_ms)
    if kind == "brightness":
        return lightctl.brightness_payload(int_or_default("brightness", 180), transition_ms=transition_ms)
    if kind == "color":
        payload = lightctl.color_payload(
            int_or_default("red", 255),
            int_or_default("green", 255),
            int_or_default("blue", 255),
            int_or_default("white", 0),
            transition_ms=transition_ms,
        )
        seg = payload.setdefault("seg", [{}])[0]
        col = seg.setdefault("col", [[]])
        while len(col) < 3:
            col.append([])
        if any(action.get(k) is not None for k in ("red2", "green2", "blue2", "white2")):
            col[1] = [int_or_default("red2", 0), int_or_default("green2", 0),
                      int_or_default("blue2", 0), int_or_default("white2", 0)]
        if any(action.get(k) is not None for k in ("red3", "green3", "blue3", "white3")):
            col[2] = [int_or_default("red3", 0), int_or_default("green3", 0),
                      int_or_default("blue3", 0), int_or_default("white3", 0)]
        return payload
    if kind == "temperature":
        return lightctl.color_payload(
            *lightctl.kelvin_to_rgbw(int_or_default("kelvin", 4000)),
            transition_ms=transition_ms,
        )
    if kind == "effect":
        payload = lightctl.effect_payload(int_or_default("effect", 9), int_or_default("speed", 140), transition_ms=transition_ms)
        seg = payload.setdefault("seg", [{}])[0]
        for source, dest in (("intensity", "ix"), ("palette", "pal"), ("c1", "c1"), ("c2", "c2"), ("c3", "c3")):
            value = optional_int(source)
            if value is not None:
                seg[dest] = lightctl.clamp_byte(value)
        # Color slots: primary, secondary, tertiary
        col = seg.get("col", [])
        while len(col) < 3:
            col.append([])
        if any(action.get(k) is not None for k in ("red", "green", "blue", "white")):
            col[0] = [lightctl.clamp_byte(int_or_default("red", 255)),
                      lightctl.clamp_byte(int_or_default("green", 255)),
                      lightctl.clamp_byte(int_or_default("blue", 255)),
                      lightctl.clamp_byte(int_or_default("white", 0))]
        if any(action.get(k) is not None for k in ("red2", "green2", "blue2", "white2")):
            col[1] = [lightctl.clamp_byte(int_or_default("red2", 0)),
                      lightctl.clamp_byte(int_or_default("green2", 0)),
                      lightctl.clamp_byte(int_or_default("blue2", 0)),
                      lightctl.clamp_byte(int_or_default("white2", 0))]
        if any(action.get(k) is not None for k in ("red3", "green3", "blue3", "white3")):
            col[2] = [lightctl.clamp_byte(int_or_default("red3", 0)),
                      lightctl.clamp_byte(int_or_default("green3", 0)),
                      lightctl.clamp_byte(int_or_default("blue3", 0)),
                      lightctl.clamp_byte(int_or_default("white3", 0))]
        if any(c for c in col):
            seg["col"] = col
        return payload
    if kind == "scene":
        return lightctl.scene_payload(str(action.get("scene") or "warm"), transition_ms=transition_ms)
    if kind == "random":
        return lightctl.random_scene_payload(transition_ms=transition_ms)
    if kind == "preset":
        return lightctl.preset_payload(int_or_default("preset_id", 1), transition_ms=transition_ms)
    if kind == "save_preset":
        preset_id = int_or_default("preset_id", 1)
        name = str(action.get("name") or f"Preset {preset_id}")
        return {"psave": preset_id, "n": name}
    if kind == "delete_preset":
        return {"pdel": int_or_default("preset_id", 1)}
    if kind == "playlist":
        return lightctl.playlist_payload(int_or_default("playlist_id", 1), transition_ms=transition_ms)
    if kind == "palette":
        return {"seg": [{"pal": int_or_default("palette", 0)}]}
    if kind == "nightlight":
        return {
            "nl": {
                "on": bool(action.get("enabled")),
                "dur": int_or_default("minutes", 60),
                "mode": int_or_default("mode", 1),
                "tbri": int_or_default("target_brightness", 0),
            }
        }
    if kind == "udp_sync":
        return {"udpn": {"send": bool(action.get("send")), "recv": bool(action.get("receive"))}}
    if kind == "native_audio_reactive":
        enabled = bool(action.get("enabled"))
        return {"AudioReactive": {"on": enabled, "enabled": enabled}}
    if kind == "segment_options":
        return _segment_options_payload(action)
    if kind == "save_scene":
        return {}
    if kind == "delete_scene":
        lightctl.delete_scene(str(action.get("scene") or ""))
        return {}
    if kind == "schedule_add":
        time_str = str(action.get("schedule_time") or "00:00")
        schedule_action = str(action.get("schedule_action") or "on")
        data: dict[str, Any] = {}
        if schedule_action == "scene":
            data["scene"] = str(action.get("scene") or "warm")
        lightctl.add_schedule(time_str, schedule_action, data)
        return {}
    if kind == "schedule_remove":
        lightctl.remove_schedule(int_or_default("schedule_index", 0))
        return {}
    if kind in CLIENT_ACTIONS:
        return {}
    raise ValueError(f"Unknown AI action: {kind}")


def apply_ai_actions(client: lightctl.LightClient, actions: list[dict[str, Any]]) -> str:
    applied = []
    for action in actions:
        if action.get("action") == "save_scene":
            payload: lightctl.WledPayload = {}
            state = client.get_state()
            for key in ("on", "bri", "seg", "transition"):
                if key in state:
                    payload[key] = state[key]  # type: ignore[literal-required]
            lightctl.save_scene(str(action.get("scene") or "custom"), payload)
            applied.append("save_scene")
            continue
        payload = payload_for_ai_action(action)
        if payload:
            client.post_state(payload)
        applied.append(str(action.get("action", "unknown")))
    return "AI applied: " + ", ".join(applied) + "."


def apply_ai_plan(client: lightctl.LightClient, plan: dict[str, Any]) -> dict[str, Any]:
    message = apply_ai_actions(client, plan["actions"])
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
        return lightctl.brightness_payload(int(data.get("value", 200)), transition_ms=transition_ms)
    if action == "color":
        return lightctl.color_payload(
            int(data.get("red", 255)),
            int(data.get("green", 255)),
            int(data.get("blue", 255)),
            int(data.get("white", 0)),
            red2=data.get("red2"), green2=data.get("green2"), blue2=data.get("blue2"), white2=data.get("white2"),
            red3=data.get("red3"), green3=data.get("green3"), blue3=data.get("blue3"), white3=data.get("white3"),
            transition_ms=transition_ms,
        )
    if action == "rgbw_bri":
        return lightctl.merge_payloads(
            lightctl.brightness_payload(int(data.get("brightness", 200)), transition_ms=transition_ms),
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
            c1=data.get("c1"), c2=data.get("c2"), c3=data.get("c3"),
            o1=data.get("o1"), o2=data.get("o2"), o3=data.get("o3"),
            transition_ms=transition_ms
        )
    if action == "scene":
        return lightctl.scene_payload(str(data.get("name", "warm")), transition_ms=transition_ms)
    if action == "temp":
        # Prefer native cct if provided, fall back to RGBW approx
        if data.get("cct") is not None:
            return lightctl.cct_payload(int(data["cct"]), transition_ms=transition_ms)
        return lightctl.color_payload(
            *lightctl.kelvin_to_rgbw(int(data.get("kelvin", 4000))),
            transition_ms=transition_ms,
        )
    if action == "cct":
        return lightctl.cct_payload(int(data.get("cct", 127)), transition_ms=transition_ms)
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
            lightctl.add_schedule(str(data.get("time", "00:00")), str(data.get("action", "on")), sched_data)
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
        return lightctl.preset_payload(int(data.get("id", 1)), transition_ms=transition_ms)
    if action == "save_preset":
        preset_id = int(data.get("id", 1))
        name = str(data.get("name", f"Preset {preset_id}"))
        return {"psave": preset_id, "n": name}
    if action == "delete_preset":
        return {"pdel": int(data.get("id", 1))}
    if action == "playlist":
        return lightctl.playlist_payload(int(data.get("id", 1)), transition_ms=transition_ms)
    if action == "palette":
        return {"seg": [{"pal": int(data.get("id", 0))}]}
    if action == "nightlight":
        return {
            "nl": {
                "on": bool(data.get("enabled")),
                "dur": int(data.get("minutes", 60)),
                "mode": int(data.get("mode", 1)),
                "tbri": int(data.get("target_brightness", 0)),
            }
        }
    if action == "udp_sync":
        return {"udpn": {"send": bool(data.get("send")), "recv": bool(data.get("receive"))}}
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

    POLL_SEC = 20          # song detection poll interval
    QUIET_SEC = 60         # seconds without beats → ambient dim
    SHAZAM_INTERVAL = 90   # min seconds between mic-recognition attempts

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
        self._main_thread = threading.Thread(target=self._run, daemon=True, name="auto-main")
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

            song_key = f"{now_playing.get('title', '')}||{now_playing.get('artist', '')}"
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
            self._client.post_state(lightctl.color_payload(
                *lightctl.kelvin_to_rgbw(2700), transition_ms=3000
            ))
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
            snapshot = self._client.get_device_snapshot()
            plan = call_openai_for_plan(prompt, now_playing, snapshot)
            plan["actions"] = [
                a for a in plan.get("actions", [])
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
            if a.get("effect") is not None and int(a["effect"]) in lightctl.SAFE_EFFECTS
        ]


# ---------------------------------------------------------------------------
# AI request serialisation — last-submitted request wins
# ---------------------------------------------------------------------------
_ai_sequence: int = 0
_ai_sequence_lock = threading.Lock()
_AI_MAX_PROMPT_LEN = 2000


class ScheduleExecutor:
    def __init__(self, client: lightctl.LightClient) -> None:
        self.client = client
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        logger.info("Schedule executor started.")
        while not self._stop.is_set():
            now = time.strftime("%H:%M")
            today = time.strftime("%Y-%m-%d")
            schedule = lightctl.list_schedule()
            modified = False
            for entry in schedule:
                if entry.get("time") == now and entry.get("last_run_date") != today:
                    try:
                        action = entry.get("action")
                        if not action:
                            continue
                        data = entry.get("data", {})
                        if action == "on":
                            self.client.post_state(lightctl.on_payload(True))
                        elif action == "off":
                            self.client.post_state(lightctl.on_payload(False))
                        elif action == "scene":
                            scene_name = data.get("scene", "warm")
                            self.client.post_state(lightctl.scene_payload(scene_name))
                        logger.info("Executed schedule: %s -> %s", entry["time"], action)
                    except Exception:
                        logger.exception("Schedule execution failed")
                    entry["last_run_date"] = today
                    modified = True
            if modified:
                lightctl._save_schedule(schedule)
            time.sleep(30)


class GuiState:
    def __init__(self, client: lightctl.LightClient) -> None:
        self.client = client
        self.mode1 = lightctl.ReactiveThread(client)
        self.autonomous = AutonomousMode(client)
        self.schedule = ScheduleExecutor(client)
        self.mood_session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=music_recognizer.recognize_audio_bytes_sync,
            generate_fn=lambda song: generate_mood_for_song(client, song),
        )
        self.schedule.start()
        self.fade_timer: lightctl.FadeTimer | None = None
        self.is_offline = False
        self.last_offline_check = 0.0
        self.cached_state = None
        self.cached_info = None
        self.state_lock = threading.Lock()

    def _fetch_throttled(self, cache_attr: str, fetch_fn: Callable[[], dict]) -> dict:
        now = time.time()
        if self.is_offline and (now - self.last_offline_check < 5.0):
            cached = getattr(self, cache_attr)
            if cached is not None:
                return cached
            raise RuntimeError("Controller is offline (throttled)")

        with self.state_lock:
            now = time.time()
            if self.is_offline and (now - self.last_offline_check < 5.0):
                cached = getattr(self, cache_attr)
                if cached is not None:
                    return cached
                raise RuntimeError("Controller is offline (throttled)")

            try:
                result = fetch_fn()
                self.is_offline = False
                self.last_offline_check = now
                setattr(self, cache_attr, result)
                return result
            except Exception as exc:
                self.is_offline = True
                self.last_offline_check = now
                cached = getattr(self, cache_attr)
                if cached is not None:
                    return cached
                raise exc

    def get_state_throttled(self) -> dict:
        return self._fetch_throttled("cached_state", self.client.get_state)

    def get_info_throttled(self) -> dict:
        return self._fetch_throttled("cached_info", self.client.get_info)

    def start_mode1(self) -> str:
        return self.mode1.start()

    def stop_mode1(self) -> str:
        return self.mode1.stop()

    def start_fade(self, minutes: float, brightness: int | None = None) -> str:
        if self.fade_timer and self.fade_timer.is_alive():
            return "Fade timer is already running."
        self.fade_timer = lightctl.FadeTimer(self.client, minutes, start_brightness=brightness)
        return self.fade_timer.start()

    def start_cycle(self, interval: float, items: list[str] | None = None) -> str:
        if hasattr(self, '_cycle') and self._cycle and self._cycle.is_alive():
            return "Cycle is already running."
        self._cycle = lightctl.CycleThread(self.client, items=items, interval_seconds=interval)
        return self._cycle.start()

    def stop_cycle(self) -> str:
        if hasattr(self, '_cycle') and self._cycle:
            return self._cycle.stop()
        return "Cycle is not running."

    def start_sunrise(self, minutes: float, brightness: int = 255) -> str:
        if hasattr(self, '_sunrise') and self._sunrise and self._sunrise.is_alive():
            return "Sunrise is already running."
        self._sunrise = lightctl.SunriseSimulator(self.client, duration_minutes=minutes, max_brightness=brightness)
        return self._sunrise.start()

    def stop_sunrise(self) -> str:
        if hasattr(self, '_sunrise') and self._sunrise:
            return self._sunrise.stop()
        return "Sunrise is not running."


def smart_suggestions(state_data: dict | None = None, now_playing: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Return context-aware one-tap lighting suggestions for the GUI."""
    hour = int(time.strftime("%H"))
    seg = {}
    if state_data and isinstance(state_data.get("seg"), list) and state_data["seg"]:
        seg = state_data["seg"][0] if isinstance(state_data["seg"][0], dict) else {}
    is_on = bool(state_data.get("on")) if state_data else False
    bri = int(state_data.get("bri", 0)) if state_data else 0
    suggestions: list[dict[str, Any]] = []

    if not is_on:
        suggestions.append({"title": "Wake the room", "reason": "Lights are off — start warm with a gentle fade.", "action": "scene", "payload": {"name": "warm"}})
    elif bri > 210 and hour >= 21:
        suggestions.append({"title": "Wind down", "reason": "It is late and bright — switch to a warmer low scene.", "action": "scene", "payload": {"name": "night"}})
    elif 6 <= hour < 11:
        suggestions.append({"title": "Morning focus", "reason": "Bright daylight tones help the room feel awake.", "action": "temp", "payload": {"kelvin": 5000, "transition": 700}})
    elif 17 <= hour < 22:
        suggestions.append({"title": "Evening ocean", "reason": "A calm blue flow fits the end of the day.", "action": "scene", "payload": {"name": "ocean"}})
    else:
        suggestions.append({"title": "Balanced focus", "reason": "Clean, bright, neutral light for everyday use.", "action": "scene", "payload": {"name": "focus"}})

    if now_playing and (now_playing.get("title") or now_playing.get("artist")):
        suggestions.append({"title": "Match the music", "reason": now_playing_text(now_playing), "action": "music_match", "payload": {}})
    else:
        suggestions.append({"title": "Listen + match", "reason": "Identify the song, then let AI build a show.", "action": "music_match", "payload": {}})

    current_fx = int(seg.get("fx", 0) or 0)
    if current_fx == 0:
        suggestions.append({"title": "Add motion", "reason": "Current output looks static — add smooth Colorwaves.", "action": "fx", "payload": {"effect": 67, "speed": 120, "transition": 500}})
    else:
        suggestions.append({"title": "Soften motion", "reason": "Use a slower, safer flow with gentle transitions.", "action": "fx", "payload": {"effect": 108, "speed": 78, "transition": 800}})

    suggestions.append({"title": "Surprise me", "reason": "Choose a random safe built-in scene.", "action": "random", "payload": {"transition": 600}})
    return suggestions[:4]


def make_handler(state: GuiState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/api/now-playing":
                query = urllib.parse.parse_qs(parsed.query)
                use_shazam = False
                now_playing = get_now_playing_with_shazam_fallback(use_shazam=use_shazam)
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
                        {"ok": False, "error": "Music recognition unavailable (need shazamio + pydub)"},
                        status=503,
                    )
                    return
                self.respond_json(
                    {"ok": False, "error": "Browser audio is required for song recognition."},
                    status=400,
                )
                return
            if path == "/api/match-lights":
                try:
                    result = match_lights_to_song(state.client, now_playing=get_now_playing())
                    self.respond_json(
                        {
                            "ok": result["ok"],
                            "message": result.get("message", ""),
                            "response": result.get("response", ""),
                            "confirmations": result.get("confirmations", ""),
                            "now_playing": result.get("now_playing"),
                        }
                    )
                except Exception as exc:
                    logger.exception("Match lights error")
                    self.respond_json({"ok": False, "error": str(exc)}, status=500)
                return
            if path == "/api/state":
                try:
                    if hasattr(state, "get_state_throttled"):
                        st = state.get_state_throttled()
                    else:
                        st = state.client.get_state()
                    self.respond_json({"ok": True, "state": st})
                except Exception as exc:
                    is_off = getattr(state, "is_offline", False)
                    if is_off:
                        self.respond_json({"ok": False, "error": "WLED controller is offline", "offline": True}, status=503)
                    else:
                        logger.exception("Error reading state")
                        self.respond_json({"ok": False, "error": str(exc)}, status=500)
                return
            if path == "/api/suggestions":
                try:
                    if hasattr(state, "get_state_throttled"):
                        st = state.get_state_throttled()
                    else:
                        st = state.client.get_state()
                except Exception:
                    st = getattr(state, "cached_state", None)
                self.respond_json({"ok": True, "suggestions": smart_suggestions(st, get_now_playing())})
                return
            if path == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    tick = 0
                    while True:
                        auto_status = {}
                        if hasattr(state, "autonomous"):
                            auto_status = state.autonomous.status()

                        try:
                            if hasattr(state, "get_state_throttled"):
                                st = state.get_state_throttled()
                            else:
                                st = state.client.get_state()

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
                                    intel["ps"] = st.get("ps")
                                    intel["pl"] = st.get("pl")
                                    seg0 = (st.get("seg") or [{}])[0]
                                    intel["cct"] = seg0.get("cct")
                            except Exception:
                                pass

                            payload = json.dumps({
                                "state": st,
                                "autonomous": auto_status,
                                "mood": state.mood_session.status() if hasattr(state, "mood_session") else {},
                                "intel": intel,
                            })
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        except Exception:
                            payload = json.dumps({"error": "device_unavailable", "autonomous": auto_status})
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))

                        try:
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
                        time.sleep(0.5)
                        tick += 1
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if path == "/wled-logo.png":
                try:
                    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wled-logo.png")
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

            if path != "/":
                self.send_error(404)
                return
            body = render_html().encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def do_HEAD(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path not in ("/", "/wled-logo.png", "/api/now-playing", "/api/recognize", "/api/match-lights", "/api/state", "/api/suggestions", "/api/mood/sample", "/api/mood/control"):
                self.send_error(404)
                return
            if path == "/wled-logo.png":
                try:
                    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wled-logo.png")
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
            if path in ("/api/now-playing", "/api/recognize", "/api/match-lights", "/api/state", "/api/suggestions", "/api/mood/sample", "/api/mood/control"):
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            body = render_html().encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def do_POST(self) -> None:
            path = self.path
            if path == "/api/ai_vision":
                try:
                    content_length = int(self.headers.get("Content-Length", 0))
                    post_data = self.rfile.read(content_length)
                    req = json.loads(post_data.decode("utf-8"))
                    image_base64 = req.get("image")
                    if not image_base64:
                        raise ValueError("Missing image data in request.")

                    # Run Multi-Modal Vision Analysis
                    result = call_openai_vision_for_plan(image_base64, client=state.client)
                    
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(result).encode("utf-8"))
                except Exception as exc:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
                return

            if path not in ("/api/action", "/api/ai", "/api/recognize", "/api/match-lights", "/api/mood/sample", "/api/mood/control"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) or b"{}"
                data = json.loads(raw)
                if path == "/api/recognize":
                    # Client-provided browser mic audio for song ID
                    audio_b64 = data.get("audio") or data.get("audio_b64")
                    shazam_result = None
                    if audio_b64:
                        if not music_recognizer.can_identify_song():
                            self.respond_json({"ok": False, "error": "Music recognition unavailable"}, status=503)
                            return
                        import base64
                        try:
                            audio_bytes = base64.b64decode(audio_b64)
                            shazam_result = music_recognizer.recognize_audio_bytes_sync(audio_bytes)
                        except Exception:
                            shazam_result = None
                    else:
                        self.respond_json(
                            {"ok": False, "error": "Browser audio is required for song recognition."},
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
                        self.respond_json({"ok": True, "now_playing": np, "text": now_playing_text(np)})
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
                            audio_bytes = base64.b64decode(audio_b64)
                            sh = music_recognizer.recognize_audio_bytes_sync(audio_bytes)
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
                    result = match_lights_to_song(state.client, now_playing=now_playing_for_match)
                    self.respond_json({
                        "ok": result["ok"],
                        "message": result.get("message", ""),
                        "response": result.get("response", ""),
                        "confirmations": result.get("confirmations", ""),
                        "now_playing": result.get("now_playing"),
                    })
                    return
                if path == "/api/mood/sample":
                    audio_b64 = data.get("audio_b64") or data.get("audio")
                    if not audio_b64:
                        self.respond_json({"ok": False, "error": "audio_b64 is required"}, status=400)
                        return
                    import base64
                    try:
                        audio_bytes = base64.b64decode(audio_b64)
                    except Exception as exc:
                        self.respond_json({"ok": False, "error": f"Invalid audio data: {exc}"}, status=400)
                        return

                    def _run_sample():
                        try:
                            state.mood_session.sample(audio_bytes)
                        except Exception as exc:
                            logger.exception("Mood sample processing failed: %s", exc)

                    threading.Thread(target=_run_sample, daemon=True).start()
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
                        self.respond_json({"ok": False, "error": "command must be start, stop, or status"}, status=400)
                        return
                    self.respond_json({"ok": True, "message": message, **state.mood_session.status()})
                    return

                if path == "/api/ai":
                    prompt = str(data.get("prompt", "")).strip()
                    if not prompt:
                        raise ValueError("Prompt is required.")
                    if len(prompt) > _AI_MAX_PROMPT_LEN:
                        self.respond_json({"ok": False, "error": "Prompt too long (max 2000 chars)."}, status=400)
                        return
                    with _ai_sequence_lock:
                        global _ai_sequence
                        _ai_sequence += 1
                        my_seq = _ai_sequence
                    now_playing = data.get("now_playing")
                    if not isinstance(now_playing, dict):
                        now_playing = get_now_playing()
                    device_snapshot = state.client.get_device_snapshot()
                    plan = call_openai_for_plan(prompt, now_playing, device_snapshot)
                    # Discard result if a newer request arrived while we were calling OpenAI
                    with _ai_sequence_lock:
                        still_current = (_ai_sequence == my_seq)
                    if not still_current:
                        self.respond_json({"ok": False, "error": "Superseded by a newer request."}, status=409)
                        return
                    result = apply_ai_plan(state.client, plan)
                    self.respond_json(
                        {
                            "ok": True,
                            "message": result["message"],
                            "response": result["response"],
                            "confirmations": result["confirmations"],
                            "client_actions": result["client_actions"],
                        }
                    )
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
                        st = state.client.get_state()
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
                        message = state.start_sunrise(float(data.get("minutes", 30)), int(data.get("brightness", 255)))
                    elif action == "sunrise_stop":
                        message = state.stop_sunrise()
                    elif action == "autonomous_start":
                        message = "Use browser Music Mode; server-side microphone autonomous mode is disabled to avoid duplicate listeners."
                    elif action == "autonomous_stop":
                        message = state.autonomous.stop()
                    elif action == "restart":
                        try:
                            state.client.post_state(lightctl.restart_payload())
                        except RuntimeError:
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
                            self.respond_json({"ok": True, "entries": entries, "message": "OK"})
                            return
                        else:
                            message = "Unknown schedule subaction."
                    else:
                        state.client.post_state(payload_for_action(action, data))
                        message = f"Sent {action}."
                self.respond_json({"ok": True, "message": message})
            except ValueError as exc:
                self.respond_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                logger.exception("Server error")
                self.respond_json({"ok": False, "error": "Internal server error"}, status=500)

        def respond_json(self, payload: dict, status: int = 200) -> None:
            try:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                # Client disconnected (tab close, refresh during slow AI call, etc.).
                # No point logging or crashing the handler thread.
                pass

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the browser GUI for the bedroom LED controller.")
    parser.add_argument("--host", default=lightctl.DEFAULT_HOST, help=f"Controller host, default {lightctl.DEFAULT_HOST}")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    client = lightctl.LightClient(args.host, dry_run=args.dry_run)
    server = ThreadingHTTPServer((args.listen, args.port), make_handler(GuiState(client)))
    logger.info("GUI running at http://%s:%d/", args.listen, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
