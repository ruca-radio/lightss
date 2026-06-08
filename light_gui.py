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
from typing import Any

import lightctl
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
        "- nightlight: WLED nightlight on/off, duration minutes, mode, and target brightness.\n"
        "- udp_sync: WLED UDP send/receive sync toggles.\n"
        "- native_audio_reactive: device AudioReactive usermod on/off when installed.\n"
        "- segment_options: active segment on/off, freeze, reverse, mirror, brightness, CCT, grouping, spacing, offset.\n"
        "- mode1_start/mode1_stop: desktop/browser microphone reactive beat mode.\n"
        "- fade_off/cycle_start/cycle_stop/sunrise_start/sunrise_stop: local timer automations.\n"
        "- save_scene/delete_scene: local custom scene management.\n"
        "- schedule_add/schedule_remove: local schedule management with time HH:MM and action on/off/scene.\n"
        "- music_detect/music_listen/music_match: now-playing lookup, microphone Shazam listen, or song-matched lighting.\n"
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
    live = snapshot.get("live")
    if live:
        lines.append(f"Live endpoint status: {live}")
    nodes = snapshot.get("nodes")
    if nodes:
        lines.append(f"Nodes endpoint status: {nodes}")
    return "\n".join(lines)

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bedroom LED Controller</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0d0d0d;
      --card: #161616;
      --text: #f4f4f4;
      --text-secondary: #aaa;
      --accent: #2d6cdf;
      --accent-hover: #1a5ac8;
      --danger: #b83232;
      --danger-hover: #9a2a2a;
      --secondary: #333;
      --secondary-hover: #444;
      --border: #2a2a2a;
      --input-bg: #101010;
      --success: #20c997;
      --user-bubble: #1a4a8a;
      --ai-bubble: #1e3a2f;
      --radius-lg: 12px;
      --radius-md: 8px;
      --radius-sm: 6px;
      font-family: Inter, system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, system-ui, sans-serif;
      display: flex;
      flex-direction: column;
    }
    main {
      flex: 1;
      padding: 16px;
      max-width: 1200px;
      margin: 0 auto;
      width: 100%;
    }
    h1 { font-size: 24px; margin: 0 0 16px; font-weight: 700; }
    h2 { font-size: 16px; margin: 0 0 12px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
    .grid {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 16px;
    }
    @media (max-width: 640px) {
      .grid { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--card);
      border-radius: var(--radius-lg);
      padding: 16px;
      border: 1px solid var(--border);
      box-shadow: 0 2px 8px rgba(0,0,0,0.3);
      margin-bottom: 16px;
    }
    .card:last-child { margin-bottom: 0; }
    button {
      border: 0;
      border-radius: var(--radius-md);
      padding: 10px 14px;
      background: var(--accent);
      color: white;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
      font-size: 13px;
    }
    button:hover { background: var(--accent-hover); transform: translateY(-1px); }
    button:focus { outline: 2px solid var(--accent); outline-offset: 2px; }
    button.secondary { background: var(--secondary); }
    button.secondary:hover { background: var(--secondary-hover); }
    button.danger { background: var(--danger); }
    button.danger:hover { background: var(--danger-hover); }
    label { display: grid; gap: 6px; margin: 10px 0; font-size: 13px; color: var(--text-secondary); }
    input, select, textarea {
      padding: 8px 10px;
      border-radius: var(--radius-sm);
      border: 1px solid #444;
      background: var(--input-bg);
      color: white;
      font-family: inherit;
      transition: all 0.15s ease;
    }
    input:focus, select:focus, textarea:focus {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
      border-color: var(--accent);
    }
    input[type="range"] { width: 100%; padding: 0; }
    input[type="number"] { width: 80px; }
    select { min-width: 160px; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .swatch {
      width: 40px;
      height: 40px;
      border-radius: var(--radius-md);
      border: 1px solid #555;
      padding: 0;
      cursor: pointer;
      transition: transform 0.15s ease;
    }
    .swatch:hover { transform: scale(1.08); }
    .meter-row { display: grid; grid-template-columns: 40px 1fr 40px; gap: 10px; align-items: center; margin-top: 10px; }
    .meter { position: relative; height: 16px; border-radius: var(--radius-sm); overflow: hidden; background: #0b0b0b; border: 1px solid #444; }
    .meter-fill { width: 0%; height: 100%; background: linear-gradient(90deg, #20c997, #ffd43b 65%, #ff6b6b); transition: width 65ms linear; }
    .meter-peak { position: absolute; top: 0; bottom: 0; left: 0%; width: 2px; background: white; opacity: .8; transition: left 120ms linear; }
    .beat-lamp { width: 16px; height: 16px; border-radius: 50%; background: #333; border: 1px solid #555; transition: background 80ms linear, box-shadow 80ms linear; }
    .beat-lamp.on { background: #ffe066; box-shadow: 0 0 14px #ffd43b; }
    .chat-container { display: flex; flex-direction: column; height: 360px; }
    .chat-history {
      flex: 1;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 8px;
      background: #0f0f0f;
      border-radius: var(--radius-md);
      border: 1px solid var(--border);
      margin-bottom: 10px;
    }
    .chat-msg { max-width: 85%; padding: 10px 12px; border-radius: 14px; font-size: 13px; line-height: 1.4; word-wrap: break-word; animation: fadeIn 0.2s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
    .chat-msg.user { align-self: flex-end; background: var(--user-bubble); color: #fff; border-bottom-right-radius: 4px; }
    .chat-msg.ai { align-self: flex-start; background: var(--ai-bubble); color: #d5f5e3; border-bottom-left-radius: 4px; }
    .chat-msg.system { align-self: center; background: #222; color: #888; font-size: 11px; padding: 4px 10px; border-radius: 10px; }
    .chat-input-row { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: center; }
    .chat-input-row input { width: 100%; margin: 0; }
    .chat-input-row button { padding: 10px 14px; font-size: 16px; }
    .chat-controls { display: flex; gap: 8px; margin-bottom: 8px; justify-content: flex-end; }
    .music-display { margin-top: 10px; padding: 12px; background: #0f0f0f; border-radius: var(--radius-md); border: 1px solid var(--border); min-height: 60px; }
    .music-title { font-size: 16px; font-weight: 600; color: #d6e4ff; margin: 0; }
    .music-artist { font-size: 13px; color: var(--text-secondary); margin: 4px 0 0; }
    .music-genre { font-size: 11px; color: #8fa7bd; margin-top: 4px; display: inline-block; background: #1a2a3a; padding: 2px 8px; border-radius: 10px; }
    .album-art { width: 80px; height: 80px; border-radius: var(--radius-md); background: #222; display: none; object-fit: cover; margin-top: 10px; border: 1px solid var(--border); }
    .album-art.visible { display: block; }
    .state-display { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--text-secondary); background: #0b0b0b; padding: 10px; border-radius: var(--radius-sm); border: 1px solid var(--border); white-space: pre-wrap; line-height: 1.5; }
    .schedule-list { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--text-secondary); background: #0b0b0b; padding: 10px; border-radius: var(--radius-sm); border: 1px solid var(--border); min-height: 30px; white-space: pre-wrap; }
    .status-bar {
      position: sticky;
      bottom: 0;
      background: rgba(13,13,13,0.95);
      backdrop-filter: blur(8px);
      border-top: 1px solid var(--border);
      padding: 10px 16px;
      display: flex;
      gap: 16px;
      align-items: center;
      justify-content: space-between;
      font-size: 13px;
      z-index: 100;
    }
    .status-bar .indicator { display: flex; align-items: center; gap: 6px; }
    .status-bar .state-summary { color: var(--text-secondary); }
    .status-bar .last-action { color: #a8d6ff; flex-shrink: 0; }
    .example { margin-top: 8px; font-size: 12px; color: var(--text-secondary); cursor: pointer; padding: 6px 8px; background: #1a1a1a; border-radius: var(--radius-sm); border: 1px dashed #444; transition: all 0.15s ease; }
    .example:hover { background: #222; color: #ccc; }
    .knowledge { margin-top: 10px; color: #8fa7bd; font-size: 12px; line-height: 1.35; }
    .now-playing { display: none; }
    #aiReply, #aiConfirmations { display: none; }
  </style>
</head>
<body>
  <main>
    <h1>💡 Bedroom LED Controller</h1>
    <div class="grid">
      <div class="left-col">
        <div class="card">
          <h2>🤖 AI Chat</h2>
          <div class="chat-controls">
            <button class="secondary" onclick="clearChat()" title="Clear all chat messages">Clear Chat</button>
          </div>
          <div class="chat-container">
            <div id="chatHistory" class="chat-history"></div>
            <div class="chat-input-row">
              <input id="aiInput" type="text" placeholder="Try: slow rainbow chase at medium brightness" onkeydown="if(event.key==='Enter')askAI()">
              <button onclick="askAI()" title="Send message to AI">➤</button>
            </div>
          </div>
          <div class="example" onclick="useExamplePrompt()">Run effects with the beat. Make it interesting without strobes or harsh transitions.</div>
          <div class="knowledge">AI can control power, brightness, full RGBW color, safe effects, scenes, and browser mic beat mode.</div>
          <div id="aiReply" class="ai-reply"></div>
          <div id="aiConfirmations" class="confirmations"></div>
        </div>
        <div class="card">
          <h2>📊 State</h2>
          <div class="state-display" id="stateDisplay">Connecting...</div>
          <div class="row" style="margin-top:12px;">
            <button onclick="send('on')" title="Turn lights on">On</button>
            <button class="danger" onclick="send('off')" title="Turn lights off">Off</button>
            <button class="secondary" onclick="startAudioReactive()" title="Start microphone reactive beat mode">Mode 1</button>
            <button class="secondary" onclick="stopAudioReactive()" title="Stop microphone reactive mode">Stop Mode 1</button>
            <button class="danger" onclick="restartController()" title="Reboot the WLED controller — device will be offline briefly">Restart Device</button>
          </div>
          <div class="meter-row" id="vuMeter" aria-label="Microphone level">
            <span>Mic</span>
            <div class="meter">
              <div class="meter-fill" id="vuFill"></div>
              <div class="meter-peak" id="vuPeak"></div>
            </div>
            <div class="beat-lamp" id="beatLamp"></div>
          </div>
        </div>
        <div class="card" id="autonomousCard">
          <h2>🌊 Autonomous AI Mode</h2>
          <p style="font-size:12px;color:var(--text-secondary);margin:0 0 10px;">Continuously identifies songs and generates a unique beatmatched show. Dims to ambient during quiet/conversation.</p>
          <div class="row">
            <button onclick="startAutonomous()" title="Start autonomous AI light show">▶ Start</button>
            <button class="secondary" onclick="send('autonomous_stop')" title="Stop autonomous AI mode">■ Stop</button>
          </div>
          <div id="autonomousStatus" style="margin-top:10px;font-size:12px;color:var(--text-secondary);">Inactive</div>
        </div>
        <div class="card">
          <h2>🎵 Music</h2>
          <div class="row">
            <button class="secondary" onclick="refreshNowPlaying()" title="Detect currently playing song from media players">Detect Song</button>
            <button class="secondary" onclick="recognizeWithShazam()" title="Listen with microphone for 5 seconds to identify song">🎤 Listen</button>
            <button class="secondary" onclick="matchLightsToSong()" title="Auto-match lights to detected song mood">✨ Match Lights</button>
          </div>
          <div class="music-display">
            <p class="music-title" id="musicTitle">No song detected yet.</p>
            <p class="music-artist" id="musicArtist"></p>
            <span class="music-genre" id="musicGenre" style="display:none;"></span>
            <img class="album-art" id="albumArt" alt="Album art">
          </div>
          <span class="now-playing" id="nowPlaying"></span>
        </div>
      </div>
      <div class="right-col">
        <div class="card">
          <h2>🎨 Color</h2>
          <div class="row">
            <button class="swatch" style="background:#f00" onclick="setColor(255,0,0,0)" title="Red (255,0,0,0)"></button>
            <button class="swatch" style="background:#00f" onclick="setColor(0,0,255,0)" title="Blue (0,0,255,0)"></button>
            <button class="swatch" style="background:#ff66bf" onclick="setColor(255,100,0,255)" title="Pink white (255,100,0,255)"></button>
            <button class="swatch" style="background:#00ff80" onclick="setColor(0,255,120,0)" title="Green (0,255,120,0)"></button>
          </div>
          <div class="row">
            <label>R <input id="r" type="number" min="0" max="255" value="255"></label>
            <label>G <input id="g" type="number" min="0" max="255" value="100"></label>
            <label>B <input id="b" type="number" min="0" max="255" value="0"></label>
            <label>W <input id="w" type="number" min="0" max="255" value="255"></label>
            <button onclick="setCustom()" title="Apply custom RGBW color">Set Color</button>
          </div>
          <div class="row" style="margin-top:12px;">
            <input id="hexColor" type="text" placeholder="#ff6600" maxlength="9" style="flex:1;">
            <button onclick="send('hex', {color: hexColor.value, transition: Number(transition.value)})" title="Set color from hex value">Set Hex</button>
          </div>
        </div>
        <div class="card">
          <h2>🌡️ Temperature</h2>
          <div class="row">
            <button class="secondary" onclick="send('temp', {kelvin: 2700, transition: Number(transition.value)})" title="Set warm 2700K temperature">Warm 2700K</button>
            <button class="secondary" onclick="send('temp', {kelvin: 5000, transition: Number(transition.value)})" title="Set daylight 5000K temperature">Daylight 5000K</button>
            <button class="secondary" onclick="send('temp', {kelvin: 6500, transition: Number(transition.value)})" title="Set cool 6500K temperature">Cool 6500K</button>
          </div>
          <label style="margin-top:12px;">Kelvin <span id="kelvinText">4000</span>K
            <input id="kelvin" type="range" min="2000" max="6500" value="4000" oninput="kelvinText.textContent=this.value" onchange="send('temp', {kelvin: Number(this.value), transition: Number(transition.value)})">
          </label>
        </div>
        <div class="card">
          <h2>✨ Effects</h2>
          <div class="row">
            <label>Effect
              <select id="fx">
                __SAFE_EFFECT_OPTIONS__
              </select>
            </label>
            <label>Speed <input id="speed" type="number" min="0" max="255" value="128"></label>
            <button onclick="send('fx', {effect: Number(fx.value), speed: Number(speed.value), transition: Number(transition.value)})" title="Apply selected effect and speed">Set Effect</button>
          </div>
          <label style="margin-top:10px;">Brightness <span id="briText">200</span>
            <input id="bri" type="range" min="0" max="255" value="200" oninput="briText.textContent=this.value" onchange="send('bri', {value: Number(this.value), transition: Number(transition.value)})">
          </label>
          <label>Transition <span id="transText">0</span>ms
            <input id="transition" type="range" min="0" max="2000" value="0" oninput="transText.textContent=this.value">
          </label>
        </div>
        <div class="card">
          <h2>🎬 Scenes</h2>
          <div class="row">
            <button onclick="send('scene', {name: 'warm'})" title="Apply warm scene">Warm</button>
            <button onclick="send('scene', {name: 'night'})" title="Apply night scene">Night</button>
            <button onclick="send('scene', {name: 'focus'})" title="Apply focus scene">Focus</button>
            <button onclick="send('scene', {name: 'ocean'})" title="Apply ocean scene">Ocean</button>
            <button onclick="send('scene', {name: 'party'})" title="Apply party scene">Party</button>
            <button class="secondary" onclick="send('random')" title="Apply random scene">Random</button>
          </div>
          <div class="row" style="margin-top:12px;">
            <input id="saveSceneName" type="text" placeholder="Scene name" style="flex:1;">
            <button class="secondary" onclick="send('save_scene', {name: saveSceneName.value})" title="Save current state as named scene">Save Scene</button>
            <button class="danger" onclick="send('delete_scene', {name: saveSceneName.value})" title="Delete named scene">Delete</button>
          </div>
        </div>
        <div class="card">
          <h2>⏱️ Timers & Simulations</h2>
          <div class="row" style="margin-bottom:8px;">
            <label style="margin:0;">Preset ID (1-250)
              <input id="presetId" type="number" min="1" max="250" value="1" style="width:90px;">
            </label>
            <button onclick="send('preset', {id: Number(presetId.value), transition: Number(transition.value)})" title="Load WLED preset by ID">Load Preset</button>
          </div>
          <div class="row" style="margin-bottom:8px;">
            <label style="margin:0;">Interval (s)
              <input id="cycleInterval" type="number" min="5" max="3600" value="60" style="width:90px;">
            </label>
            <button onclick="send('cycle_start', {interval: Number(cycleInterval.value)})" title="Start automatic scene cycling">Start Cycle</button>
            <button class="secondary" onclick="send('cycle_stop')" title="Stop scene cycling">Stop Cycle</button>
          </div>
          <div class="row" style="margin-bottom:8px;">
            <label style="margin:0;">Sunrise (min)
              <input id="sunriseMinutes" type="number" min="1" max="120" value="30" style="width:90px;">
            </label>
            <button onclick="send('sunrise_start', {minutes: Number(sunriseMinutes.value)})" title="Start sunrise wake-up simulation">Start Sunrise</button>
            <button class="secondary" onclick="send('sunrise_stop')" title="Stop sunrise simulation">Stop</button>
          </div>
          <div class="row">
            <label style="margin:0;">Fade (min)
              <input id="fadeMinutes" type="number" min="1" max="120" value="30" style="width:90px;">
            </label>
            <button onclick="send('fade_off', {minutes: Number(fadeMinutes.value)})" title="Start gradual fade-off timer">Start Fade</button>
          </div>
        </div>
        <div class="card">
          <h2>📅 Schedule</h2>
          <div class="row">
            <label style="margin:0;">Time
              <input id="schedTime" type="time" style="padding:8px;">
            </label>
            <label style="margin:0;">Action
              <select id="schedAction">
                <option value="on">On</option>
                <option value="off">Off</option>
                <option value="scene">Scene</option>
              </select>
            </label>
            <input id="schedScene" type="text" placeholder="Scene name (if scene)" style="flex:1;">
            <button onclick="addSchedule()" title="Add schedule entry">Add</button>
          </div>
          <div class="schedule-list" id="scheduleList" style="margin-top:10px;">No schedules.</div>
          <button class="secondary" style="margin-top:8px;" onclick="listSchedule()" title="Refresh schedule list">Refresh List</button>
        </div>
      </div>
    </div>
  </main>
  <div class="status-bar">
    <div class="indicator">
      <span id="connIndicator">🔴</span>
      <span id="connText">Disconnected</span>
    </div>
    <div class="state-summary" id="stateSummary">--</div>
    <div class="last-action" id="status">Ready</div>
  </div>
  <script>
    let audioContext, analyser, micStream, audioData, rafId;
    let audioReactiveRunning = false;
    let baseline = 18;
    let lastBeat = 0;
    let paletteIndex = 0;
    let effectIndex = 0;
    let peakLevel = 0;
    const palette = [
      [255, 0, 0, 0],
      [255, 90, 0, 0],
      [255, 0, 180, 0],
      [0, 80, 255, 0],
      [0, 255, 120, 0],
      [255, 120, 0, 180],
      [0, 180, 255, 0],
      [255, 255, 120, 80]
    ];
    const beatEffects = __BEAT_EFFECTS__;

    function addChatMessage(role, text) {
      const div = document.createElement('div');
      div.className = 'chat-msg ' + role;
      div.textContent = text;
      chatHistory.appendChild(div);
      chatHistory.scrollTop = chatHistory.scrollHeight;
      saveChatHistory();
    }

    function saveChatHistory() {
      const messages = [];
      chatHistory.querySelectorAll('.chat-msg').forEach(el => {
        const role = el.classList.contains('user') ? 'user' : el.classList.contains('ai') ? 'ai' : 'system';
        messages.push({ role, text: el.textContent });
      });
      sessionStorage.setItem('light_chat_history', JSON.stringify(messages));
    }

    function loadChatHistory() {
      try {
        const raw = sessionStorage.getItem('light_chat_history');
        if (!raw) return;
        const messages = JSON.parse(raw);
        chatHistory.innerHTML = '';
        for (const m of messages) {
          const div = document.createElement('div');
          div.className = 'chat-msg ' + m.role;
          div.textContent = m.text;
          chatHistory.appendChild(div);
        }
        chatHistory.scrollTop = chatHistory.scrollHeight;
      } catch (e) { /* ignore */ }
    }

    function clearChat() {
      chatHistory.innerHTML = '';
      sessionStorage.removeItem('light_chat_history');
    }

    async function send(action, values = {}) {
      status.textContent = 'Sending...';
      try {
        const res = await fetch('/api/action', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action, ...values})
        });
        const data = await res.json();
        status.textContent = data.ok ? data.message : data.error;
      } catch (err) {
        status.textContent = 'Error: ' + err.message;
      }
    }

    async function askAI() {
      const prompt = aiInput.value.trim();
      if (!prompt) return;
      addChatMessage('user', prompt);
      aiInput.value = '';
      addChatMessage('system', 'Thinking...');
      const thinkingEls = chatHistory.querySelectorAll('.chat-msg.system');
      const thinkingEl = thinkingEls[thinkingEls.length - 1];
      aiReply.textContent = '';
      aiConfirmations.textContent = '';
      try {
        const song = await refreshNowPlaying();
        const res = await fetch('/api/ai', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prompt, now_playing: song})
        });
        const data = await res.json();
        if (thinkingEl) thinkingEl.remove();
        status.textContent = data.ok ? data.message : data.error;
        if (data.response) {
          addChatMessage('ai', data.response);
        }
        if (data.confirmations && data.confirmations.length) {
          addChatMessage('ai', data.confirmations.join(' '));
        }
        aiReply.textContent = data.response || '';
        aiConfirmations.textContent = (data.confirmations || []).join(' ');
        for (const action of data.client_actions || []) {
          if (action === 'startAudioReactive') await startAudioReactive();
          if (action === 'stopAudioReactive') stopAudioReactive();
          if (typeof action === 'object' && action.action === 'fadeOff') await send('fade_off', {minutes: action.minutes || 30});
          if (typeof action === 'object' && action.action === 'startCycle') await send('cycle_start', {interval: action.interval || 60});
          if (typeof action === 'object' && action.action === 'stopCycle') await send('cycle_stop');
          if (typeof action === 'object' && action.action === 'startSunrise') await send('sunrise_start', {minutes: action.minutes || 30, brightness: action.brightness || 255});
          if (typeof action === 'object' && action.action === 'stopSunrise') await send('sunrise_stop');
          if (typeof action === 'object' && action.action === 'detectSong') await refreshNowPlaying();
          if (typeof action === 'object' && action.action === 'listenForSong') await recognizeWithShazam();
          if (typeof action === 'object' && action.action === 'matchLightsToSong') await matchLightsToSong();
        }
      } catch (err) {
        if (thinkingEl) thinkingEl.remove();
        status.textContent = 'AI error: ' + err.message;
        addChatMessage('ai', 'Error: ' + err.message);
      }
    }

    function useExamplePrompt() {
      aiInput.value = 'Run effects with the beat. Make it interesting without strobes or harsh transitions.';
      aiInput.focus();
    }

    async function refreshNowPlaying() {
      try {
        const res = await fetch('/api/now-playing');
        const data = await res.json();
        const np = data.now_playing || null;
        if (np) {
          musicTitle.textContent = np.title || 'Unknown';
          musicArtist.textContent = np.artist || '';
          if (np.genre) { musicGenre.textContent = np.genre; musicGenre.style.display = 'inline-block'; }
          else { musicGenre.style.display = 'none'; }
          if (np.cover_url) { albumArt.src = np.cover_url; albumArt.classList.add('visible'); }
          else { albumArt.classList.remove('visible'); }
        } else {
          musicTitle.textContent = data.text || data.error || 'No song detected.';
          musicArtist.textContent = '';
          musicGenre.style.display = 'none';
          albumArt.classList.remove('visible');
        }
        nowPlaying.textContent = data.text || data.error || 'No song detected.';
        return np;
      } catch (err) {
        musicTitle.textContent = 'Error fetching song.';
        musicArtist.textContent = '';
        musicGenre.style.display = 'none';
        albumArt.classList.remove('visible');
        nowPlaying.textContent = 'Error fetching song.';
        return null;
      }
    }

    async function recognizeWithShazam() {
      musicTitle.textContent = '🎤 Listening... (5s)';
      musicArtist.textContent = '';
      musicGenre.style.display = 'none';
      albumArt.classList.remove('visible');
      try {
        const res = await fetch('/api/recognize');
        const data = await res.json();
        if (data.ok && data.now_playing) {
          const np = data.now_playing;
          musicTitle.textContent = np.title || 'Song recognized!';
          musicArtist.textContent = np.artist || '';
          if (np.genre) { musicGenre.textContent = np.genre; musicGenre.style.display = 'inline-block'; }
          nowPlaying.textContent = data.text || 'Song recognized!';
          return np;
        }
        musicTitle.textContent = data.error || 'No match found.';
        nowPlaying.textContent = data.error || 'No match found.';
        return null;
      } catch (err) {
        musicTitle.textContent = 'Recognition error: ' + err.message;
        nowPlaying.textContent = 'Recognition error: ' + err.message;
        return null;
      }
    }

    async function matchLightsToSong() {
      status.textContent = 'Detecting song and matching lights...';
      musicTitle.textContent = 'Listening...';
      musicArtist.textContent = '';
      musicGenre.style.display = 'none';
      albumArt.classList.remove('visible');
      try {
        const res = await fetch('/api/match-lights');
        const data = await res.json();
        if (data.ok) {
          status.textContent = data.message || 'Lights matched!';
          const np = data.now_playing;
          if (np) {
            musicTitle.textContent = np.title || 'Unknown';
            musicArtist.textContent = np.artist || '';
            if (np.genre) { musicGenre.textContent = np.genre; musicGenre.style.display = 'inline-block'; }
            if (np.cover_url) { albumArt.src = np.cover_url; albumArt.classList.add('visible'); }
          } else {
            musicTitle.textContent = 'Song matched!';
          }
          if (data.response) addChatMessage('ai', data.response);
          const conf = Array.isArray(data.confirmations) ? data.confirmations.join(' ') : (data.confirmations || '');
          if (conf) addChatMessage('ai', conf);
          aiReply.textContent = data.response || '';
          aiConfirmations.textContent = data.confirmations || '';
        } else {
          status.textContent = data.error || 'Could not match lights.';
          musicTitle.textContent = data.error || 'No match found.';
        }
      } catch (err) {
        status.textContent = 'Match lights error: ' + err.message;
        musicTitle.textContent = 'Error matching lights.';
      }
    }

    async function startAudioReactive() {
      if (audioReactiveRunning) return;
      try {
        micStream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false }
        });
      } catch (err) {
        status.textContent = 'Mic error: ' + err.name;
        return;
      }
      audioReactiveRunning = true;
      audioContext = new AudioContext();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      audioData = new Uint8Array(analyser.frequencyBinCount);
      audioContext.createMediaStreamSource(micStream).connect(analyser);
      status.textContent = 'Mode 1 listening...';
      analyzeAudio();
    }

    function stopAudioReactive() {
      if (!audioReactiveRunning) return;
      audioReactiveRunning = false;
      if (rafId) cancelAnimationFrame(rafId);
      rafId = null;
      if (micStream) micStream.getTracks().forEach(track => track.stop());
      if (audioContext) audioContext.close();
      peakLevel = 0;
      updateVu(0, false);
      status.textContent = 'Mode 1 stopped.';
    }

    function updateVu(energy, beat) {
      const level = Math.max(0, Math.min(100, Math.round(energy * 1.35)));
      peakLevel = Math.max(level, peakLevel * 0.94);
      if (peakLevel < 0.5) peakLevel = 0;
      vuFill.style.width = `${level}%`;
      vuPeak.style.left = `${Math.max(0, Math.min(99, peakLevel))}%`;
      beatLamp.classList.toggle('on', beat);
      if (beat) setTimeout(() => beatLamp.classList.remove('on'), 90);
    }

    function analyzeAudio() {
      if (!audioReactiveRunning) return;
      analyser.getByteFrequencyData(audioData);
      let total = 0;
      for (const value of audioData) total += value;
      const energy = total / audioData.length;
      baseline = baseline * 0.94 + energy * 0.06;
      const now = performance.now();
      const beat = energy > 22 && energy > baseline * 1.45 && now - lastBeat > 140;
      updateVu(energy, beat);
      if (beat) {
        lastBeat = now;
        const color = palette[paletteIndex++ % palette.length];
        const effect = beatEffects[effectIndex++ % beatEffects.length];
        const brightness = Math.max(90, Math.min(255, Math.round(energy * 3.2)));
        const speed = Math.max(100, Math.min(255, Math.round(80 + energy * 3.9)));
        send('beat', {red: color[0], green: color[1], blue: color[2], white: color[3], brightness, effect, speed});
      }
      rafId = requestAnimationFrame(analyzeAudio);
    }

    document.addEventListener('visibilitychange', () => {
      if (document.hidden && audioReactiveRunning) {
        if (rafId) cancelAnimationFrame(rafId);
        rafId = null;
        if (audioContext && audioContext.state === 'running') audioContext.suspend();
      } else if (!document.hidden && audioReactiveRunning && !rafId) {
        if (audioContext && audioContext.state === 'suspended') audioContext.resume();
        analyzeAudio();
      }
    });

    function setColor(red, green, blue, white) {
      r.value = red; g.value = green; b.value = blue; w.value = white;
      send('color', {red, green, blue, white, transition: Number(transition.value)});
    }

    function setCustom() {
      setColor(Number(r.value), Number(g.value), Number(b.value), Number(w.value));
    }

    async function addSchedule() {
      const timeStr = schedTime.value;
      const action = schedAction.value;
      const sceneName = schedScene.value;
      if (!timeStr) { status.textContent = 'Please select a time.'; return; }
      await send('schedule', {subaction: 'add', time: timeStr, action, scene_name: sceneName});
      listSchedule();
    }

    async function listSchedule() {
      try {
        const res = await fetch('/api/action', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action: 'schedule', subaction: 'list'})
        });
        const data = await res.json();
        if (data.ok && data.entries) {
          scheduleList.textContent = data.entries.length ? data.entries.map((e,i) => `${i}: ${e.time} -> ${e.action} ${e.data.scene||''}`).join('\\n') : 'No schedules.';
        } else {
          scheduleList.textContent = data.error || 'No schedules.';
        }
      } catch (err) {
        scheduleList.textContent = 'Error loading schedules.';
      }
    }

    async function startAutonomous() {
      // Stop browser Mode 1 first — autonomous has its own beat thread
      stopAudioReactive();
      await send('autonomous_start');
    }

    function updateAutonomousStatus(auto) {
      const el = document.getElementById('autonomousStatus');
      if (!el) return;
      if (!auto || !auto.running) {
        el.textContent = 'Inactive';
        el.style.color = 'var(--text-secondary)';
        return;
      }
      if (auto.quiet) {
        el.textContent = '🌙 Ambient — quiet detected';
        el.style.color = '#aaa';
        return;
      }
      const song = auto.song;
      if (song && song.title) {
        const genre = song.genre ? ` · ${song.genre}` : '';
        el.textContent = `🎵 ${song.title} — ${song.artist || ''}${genre}`;
        el.style.color = 'var(--success)';
      } else {
        el.textContent = '🔍 Listening for music...';
        el.style.color = '#ffd43b';
      }
    }

    async function restartController() {
      if (!confirm('Reboot the WLED controller? It will be offline for a few seconds.')) return;
      status.textContent = 'Restarting controller...';
      connIndicator.textContent = '🟡';
      connText.textContent = 'Restarting...';
      await send('restart');
    }

    // SSE state updates — polls the device every 2 seconds
    const evtSource = new EventSource('/api/events');
    evtSource.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        updateAutonomousStatus(payload.autonomous || null);
        if (payload.state) {
          const st = payload.state;
          const onOff = st.on ? 'ON' : 'OFF';
          const bri = st.bri ?? '?';
          const seg = st.seg && st.seg[0] ? st.seg[0] : {};
          const col = seg.col && seg.col[0] ? seg.col[0] : [0,0,0,0];
          stateDisplay.textContent = `Power: ${onOff}\\nBrightness: ${bri}\\nColor: RGBW(${col.join(',')})\\nEffect: ${seg.fx ?? '-'} Speed: ${seg.sx ?? '-'}`;
          stateSummary.textContent = `${onOff} | Bri ${bri} | Fx ${seg.fx ?? '-'} @ ${seg.sx ?? '-'}`;
          connIndicator.textContent = '🟢';
          connText.textContent = 'Connected';
        } else if (payload.error) {
          stateDisplay.textContent = 'Device offline — reconnecting...';
          stateSummary.textContent = '--';
          connIndicator.textContent = '🔴';
          connText.textContent = 'Disconnected';
        }
      } catch (e) {
        stateDisplay.textContent = 'State update error';
        connIndicator.textContent = '🔴';
        connText.textContent = 'Disconnected';
      }
    };
    evtSource.onerror = () => {
      stateDisplay.textContent = 'State connection lost. Retrying...';
      connIndicator.textContent = '🔴';
      connText.textContent = 'Disconnected';
    };

    // Init
    loadChatHistory();
    listSchedule();
  </script>
</body>
</html>
"""


@functools.lru_cache(maxsize=1)
def _render_html_cached() -> str:
    effect_options = "\n            ".join(
        f'<option value="{effect_id}">{name}</option>' for effect_id, name in lightctl.SAFE_EFFECTS.items()
    )
    return (
        HTML_TEMPLATE.replace("__SAFE_EFFECT_OPTIONS__", effect_options)
        .replace("__BEAT_EFFECTS__", json.dumps(list(lightctl.SAFE_EFFECTS)))
    )


def render_html() -> str:
    return _render_html_cached()


def safe_effect_prompt() -> str:
    return ", ".join(f"{effect_id}={name}" for effect_id, name in lightctl.SAFE_EFFECTS.items())


def system_knowledge_prompt() -> str:
    return (
        "You control a bedroom Wi-Fi LED controller through validated actions. "
        "Hardware endpoint: WLED-compatible /json/state. The live device also exposes /json, "
        "/json/info, /json/effects, /json/palettes, /json/nodes, and /json/live; normal control "
        "is performed by posting validated JSON state payloads. "
        "Available controls: power on/off, global brightness 0-255, full RGBW color channels "
        "red 0-255, green 0-255, blue 0-255, white 0-255, safe WLED effects, effect speed 0-255, "
        "effect intensity 0-255, palettes, segment options, named scenes warm/night/focus/ocean/party "
        "plus any saved custom scenes, color temperature 2000K-6500K, transition time 0-2000ms, "
        "nightlight, UDP sync, playlists, presets, native AudioReactive usermod control, and "
        "browser/desktop microphone Mode 1 start/stop. "
        "All colors the LEDs can produce are represented by RGBW values, so you may choose any "
        "combination from [0,0,0,0] through [255,255,255,255]. Named colors should be translated "
        "to RGBW values; use the white channel for softer pastel, warm, or room-light looks. "
        "Hex colors like #ff6600 are also accepted. "
        "Many WLED effects use two or three color slots. When the device snapshot shows "
        "'colors 1+2' or 'colors 1+2+3' for an effect, always set secondary (red2/green2/blue2/white2) "
        "and tertiary (red3/green3/blue3/white3) colors — this makes the effect look dramatically better. "
        "The device has 70+ named palettes (Ocean, Forest, Party, Rainbow, Sunset, etc.). Choose palettes "
        "by their id number from the 'All palettes' list in the device snapshot — match the palette name "
        "to the mood. Effects with palette support will ignore the color slots and use the palette instead. "
        "You can set random scenes, load WLED presets by ID from the 'Saved WLED presets' list, "
        "start a sunrise wake-up simulation, or begin a scene cycle that rotates through favorites automatically. "
        "Mode 1 uses the browser webcam microphone, a VU meter, beat detection, and beat-synced "
        "effect cycling. If the user asks to run effects with the beat, include mode1_start plus "
        "safe effect/brightness/color setup. "
        "When a Now playing song is provided, use the title, artist, album, genre, and playback status to infer "
        "mood, energy, palette, speed, and effect style. Genre is especially important: for example, use "
        "chill/warm colors and slow flow effects for jazz or acoustic; vibrant rainbows and fast chase for "
        "EDM or pop; deep reds and purples with slow pulse for metal or dark ambient; bright warm tones "
        "for reggae or funk. Match the color palette and effect tempo to the song's emotional feel. "
        "Mention the song in your response when relevant. "
        "When the user specifies an explicit numeric value — 'set brightness to 241', "
        "'set RGBW to 255 0 0 0', 'temperature 5000K', 'effect 28 speed 200' — use that exact "
        "value in the action. Do not substitute, approximate, or override explicit user values "
        "with your own aesthetic judgement. Explicit commands are instructions, not suggestions. "
        "Never use blink, strobe, flash, lightning, sparkle, fireworks, or seizure-like effects. "
        f"Allowed effect ids are: {safe_effect_prompt()}. "
        "Use chase/rainbow/flow effects when the user asks for motion. "
        "Always include a concise response and confirmations describing the operations.\n\n"
        f"{ai_action_reference()}"
    )


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
    if not music_recognizer.is_available():
        logger.warning("Shazam fallback requested but music_recognizer is unavailable")
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


def match_lights_to_song(client: lightctl.LightClient) -> dict[str, Any]:
    """Detect the currently playing song and ask the AI to create matching lights.

    Returns a dict with keys: ok, message, response, confirmations, client_actions, now_playing.
    """
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
        "Choose colors, effects, and speed that feel right for this song."
    )
    plan = call_openai_for_plan(prompt, now_playing, client.get_device_snapshot())
    result = apply_ai_plan(client, plan)
    result["now_playing"] = now_playing
    return result


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
                            "description": "A short user-facing response explaining what will happen.",
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
            client_actions.append(
                {
                    "action": "startSunrise",
                    "minutes": float(action.get("minutes") or 30),
                    "brightness": int(action.get("target_brightness") or 255),
                }
            )
        elif kind == "sunrise_stop":
            client_actions.append({"action": "stopSunrise"})
        elif kind == "music_detect":
            client_actions.append({"action": "detectSong"})
        elif kind == "music_listen":
            client_actions.append({"action": "listenForSong"})
        elif kind == "music_match":
            client_actions.append({"action": "matchLightsToSong"})
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
    if kind == "playlist":
        playlist_id = int_or_default("playlist_id", 1)
        payload: lightctl.WledPayload = {"pl": playlist_id}
        if transition_ms > 0:
            payload["transition"] = transition_ms
        return payload
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
            if action.get(source) is not None:
                value = action[source]
                seg[dest] = value if isinstance(value, bool) else int(value)
        if action.get("kelvin") is not None:
            seg["cct"] = lightctl.clamp_byte((int(action["kelvin"]) - 2000) * 255 / 4500)
        return {"seg": [seg]} if seg else {}
    if kind == "save_scene":
        name = str(action.get("scene") or "custom")
        lightctl.save_scene(name, {})
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
        return lightctl.effect_payload(int(data.get("effect", 1)), int(data.get("speed", 128)), transition_ms=transition_ms)
    if action == "scene":
        return lightctl.scene_payload(str(data.get("name", "warm")), transition_ms=transition_ms)
    if action == "temp":
        return lightctl.color_payload(
            *lightctl.kelvin_to_rgbw(int(data.get("kelvin", 4000))),
            transition_ms=transition_ms,
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
        try:
            logger.info("Autonomous: Shazam identification attempt")
            self._stop_beat()
            result = music_recognizer.recognize_sync()
            self._start_beat()
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
                "feel distinctly different. Reference the actual energy and genre of this song."
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
            schedule = lightctl.list_schedule()
            modified = False
            for entry in schedule:
                if entry.get("time") == now and not entry.get("_executed_today"):
                    try:
                        action = entry["action"]
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
                    entry["_executed_today"] = True
                    modified = True
            if now == "00:00":
                for entry in schedule:
                    entry.pop("_executed_today", None)
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
        self.schedule.start()
        self.fade_timer: lightctl.FadeTimer | None = None

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


def make_handler(state: GuiState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/api/now-playing":
                query = urllib.parse.parse_qs(parsed.query)
                use_shazam = query.get("shazam", [""])[0].lower() in ("1", "true", "yes")
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
                if not music_recognizer.is_available():
                    self.respond_json(
                        {"ok": False, "error": music_recognizer.available_reason()},
                        status=503,
                    )
                    return
                try:
                    shazam_result = music_recognizer.recognize_sync()
                    if shazam_result:
                        now_playing = {
                            "title": shazam_result.get("title", ""),
                            "artist": shazam_result.get("artist", ""),
                            "album": shazam_result.get("album", ""),
                            "status": "Playing",
                            "source": "shazam",
                        }
                        self.respond_json(
                            {
                                "ok": True,
                                "now_playing": now_playing,
                                "text": now_playing_text(now_playing),
                            }
                        )
                    else:
                        self.respond_json(
                            {"ok": False, "error": "No match found."}
                        )
                except Exception as exc:
                    logger.exception("Shazam recognition error")
                    self.respond_json({"ok": False, "error": str(exc)}, status=500)
                return
            if path == "/api/match-lights":
                try:
                    result = match_lights_to_song(state.client)
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
                    st = state.client.get_state()
                    self.respond_json({"ok": True, "state": st})
                except Exception as exc:
                    logger.exception("Error reading state")
                    self.respond_json({"ok": False, "error": str(exc)}, status=500)
                return
            if path == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    while True:
                        auto_status = state.autonomous.status()
                        try:
                            st = state.client.get_state()
                            payload = json.dumps({"state": st, "autonomous": auto_status})
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        except Exception:
                            payload = json.dumps({"error": "device_unavailable", "autonomous": auto_status})
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        time.sleep(2)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if path != "/":
                self.send_error(404)
                return
            body = render_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path not in ("/", "/api/now-playing", "/api/recognize", "/api/match-lights", "/api/state"):
                self.send_error(404)
                return
            if path in ("/api/now-playing", "/api/recognize", "/api/match-lights", "/api/state"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                return
            body = render_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

        def do_POST(self) -> None:
            if self.path not in ("/api/action", "/api/ai"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/ai":
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
                        # Stop browser Mode 1 if running — autonomous has its own beat thread
                        state.mode1.stop()
                        message = state.autonomous.start()
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
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
