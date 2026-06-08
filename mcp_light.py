#!/usr/bin/env python3
"""Minimal MCP stdio server for the bedroom LED controller."""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import sys
from typing import Any

import lightctl
import music_recognizer

logger = logging.getLogger("mcp_light")

SERVER_INFO = {"name": "bedroom-light-controller", "version": "1.1.0"}
MCP_PROTOCOL_VERSION = "2024-11-05"


class StderrDryRunClient(lightctl.LightClient):
    def post_state(self, payload: lightctl.WledPayload) -> None:
        logger.debug("dry-run: %s", json.dumps(payload, separators=(",", ":")))


def int_schema(description: str, minimum: int = 0, maximum: int = 255) -> dict:
    return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}


def safe_effect_schema() -> dict:
    allowed = ", ".join(f"{effect_id}={name}" for effect_id, name in lightctl.SAFE_EFFECTS.items())
    return {
        "type": "integer",
        "description": f"Safe non-strobe effect id: {allowed}",
        "enum": list(lightctl.SAFE_EFFECTS),
    }


def _transition_schema() -> dict:
    return {"type": "integer", "description": "Transition time in milliseconds", "minimum": 0, "maximum": 65535}


def build_tools() -> list[dict]:
    return [
        {
            "name": "light_on",
            "description": "Turn the bedroom LEDs on.",
            "inputSchema": {
                "type": "object",
                "properties": {"transition": _transition_schema()},
                "additionalProperties": False,
            },
        },
        {
            "name": "light_off",
            "description": "Turn the bedroom LEDs off.",
            "inputSchema": {
                "type": "object",
                "properties": {"transition": _transition_schema()},
                "additionalProperties": False,
            },
        },
        {
            "name": "get_state",
            "description": "Read the current LED controller state.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "set_brightness",
            "description": "Set LED brightness from 0 to 255.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "brightness": int_schema("Brightness level", 0, 255),
                    "transition": _transition_schema(),
                },
                "required": ["brightness"],
                "additionalProperties": False,
            },
        },
        {
            "name": "set_color",
            "description": "Set the LED RGBW color.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "red": int_schema("Red channel"),
                    "green": int_schema("Green channel"),
                    "blue": int_schema("Blue channel"),
                    "white": int_schema("White channel"),
                    "transition": _transition_schema(),
                },
                "required": ["red", "green", "blue"],
                "additionalProperties": False,
            },
        },
        {
            "name": "set_temperature",
            "description": "Set color temperature in Kelvin (2000-6500).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kelvin": int_schema("Color temperature", 2000, 6500),
                    "transition": _transition_schema(),
                },
                "required": ["kelvin"],
                "additionalProperties": False,
            },
        },
        {
            "name": "set_effect",
            "description": "Set a safe non-strobe WLED effect and speed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "effect": safe_effect_schema(),
                    "speed": int_schema("Effect speed", 0, 255),
                    "transition": _transition_schema(),
                },
                "required": ["effect"],
                "additionalProperties": False,
            },
        },
        {
            "name": "set_scene",
            "description": "Set a named scene: warm, night, focus, ocean, party, or any saved custom scene.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Scene name"},
                    "transition": _transition_schema(),
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "save_scene",
            "description": "Save the current LED state as a named custom scene.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Scene name to save"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "delete_scene",
            "description": "Delete a saved custom scene.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Scene name to delete"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_scenes",
            "description": "List all saved custom scene names.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "set_hex_color",
            "description": "Set the LED color from a hex string (#RRGGBB or #RRGGBBWW).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "hex": {"type": "string", "description": "Hex color string"},
                    "transition": _transition_schema(),
                },
                "required": ["hex"],
                "additionalProperties": False,
            },
        },
        {
            "name": "random_scene",
            "description": "Set a random built-in safe scene.",
            "inputSchema": {
                "type": "object",
                "properties": {"transition": _transition_schema()},
                "additionalProperties": False,
            },
        },
        {
            "name": "fade_off",
            "description": "Gradually fade the LEDs to off over a number of minutes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "minimum": 0.5, "maximum": 120, "description": "Duration in minutes"},
                    "brightness": {"type": ["integer", "null"], "minimum": 0, "maximum": 255, "description": "Starting brightness (null = current)"},
                },
                "required": ["minutes"],
                "additionalProperties": False,
            },
        },
        {
            "name": "load_preset",
            "description": "Load a WLED preset by ID (1-250).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": int_schema("Preset ID", 1, 250),
                    "transition": _transition_schema(),
                },
                "required": ["id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_info",
            "description": "Read WLED controller device info (name, version, LEDs, uptime, IP).",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "start_sunrise",
            "description": "Start a gradual sunrise wake-up simulation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "minimum": 1, "maximum": 120, "description": "Duration in minutes"},
                    "brightness": int_schema("Max brightness at end", 0, 255),
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "start_audio_reactive",
            "description": "Start Mode 1, which listens to the microphone and reacts to room music.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "stop_audio_reactive",
            "description": "Stop Mode 1 audio-reactive lighting.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "recognize_music",
            "description": "Listen to the room via microphone and identify the currently playing song using Shazam. Returns title, artist, and album if a match is found.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "match_lights_to_song",
            "description": "Detect the currently playing song (via playerctl, MPRIS, or microphone fallback) and automatically generate a light show that matches its genre, mood, and energy using the AI. Returns the song info and the applied lighting plan.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


def text_result(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}]}


def call_tool(
    client: lightctl.LightClient,
    name: str,
    arguments: dict[str, Any] | None,
    modes: lightctl.ReactiveThread,
) -> dict:
    args = arguments or {}
    transition_ms = int(args.get("transition", 0))

    if name == "light_on":
        client.post_state(lightctl.on_payload(True, transition_ms=transition_ms))
        return text_result("Turned bedroom LEDs on.")
    if name == "light_off":
        client.post_state(lightctl.on_payload(False, transition_ms=transition_ms))
        return text_result("Turned bedroom LEDs off.")
    if name == "get_state":
        state = client.get_state()
        return text_result(json.dumps(state, indent=2))
    if name == "set_brightness":
        brightness = int(args["brightness"])
        client.post_state(lightctl.brightness_payload(brightness, transition_ms=transition_ms))
        return text_result(f"Set brightness to {lightctl.clamp_byte(brightness)}.")
    if name == "set_color":
        red = int(args.get("red", 0))
        green = int(args.get("green", 0))
        blue = int(args.get("blue", 0))
        white = int(args.get("white", 0))
        client.post_state(lightctl.color_payload(red, green, blue, white, transition_ms=transition_ms))
        return text_result(f"Set color to RGBW({red}, {green}, {blue}, {white}).")
    if name == "set_temperature":
        kelvin = int(args["kelvin"])
        rgbw = lightctl.kelvin_to_rgbw(kelvin)
        client.post_state(lightctl.color_payload(*rgbw, transition_ms=transition_ms))
        return text_result(f"Set temperature to {kelvin}K (RGBW{rgbw}).")
    if name == "set_effect":
        effect = int(args["effect"])
        speed = int(args.get("speed", 128))
        client.post_state(lightctl.effect_payload(effect, speed, transition_ms=transition_ms))
        return text_result(f"Set effect {effect} at speed {lightctl.clamp_byte(speed)}.")
    if name == "set_scene":
        scene = str(args["name"])
        client.post_state(lightctl.scene_payload(scene, transition_ms=transition_ms))
        return text_result(f"Set scene to {scene}.")
    if name == "save_scene":
        scene_name = str(args["name"])
        state = client.get_state()
        payload: lightctl.WledPayload = {}
        for key in ("on", "bri", "seg", "transition"):
            if key in state:
                payload[key] = state[key]  # type: ignore[literal-required]
        lightctl.save_scene(scene_name, payload)
        return text_result(f"Saved scene '{scene_name}'.")
    if name == "delete_scene":
        scene_name = str(args["name"])
        lightctl.delete_scene(scene_name)
        return text_result(f"Deleted scene '{scene_name}'.")
    if name == "list_scenes":
        scenes = lightctl.list_scenes()
        return text_result("Saved scenes: " + (", ".join(scenes) or "none"))
    if name == "set_hex_color":
        hex_color = str(args["hex"])
        rgbw = lightctl.hex_to_rgbw(hex_color)
        client.post_state(lightctl.color_payload(*rgbw, transition_ms=transition_ms))
        return text_result(f"Set hex color {hex_color} -> RGBW{rgbw}.")
    if name == "random_scene":
        payload = lightctl.random_scene_payload(transition_ms=transition_ms)
        client.post_state(payload)
        return text_result("Set a random built-in scene.")
    if name == "fade_off":
        minutes = float(args["minutes"])
        start_brightness = args.get("brightness")
        if start_brightness is not None:
            start_brightness = int(start_brightness)
        timer = lightctl.FadeTimer(client, minutes, start_brightness=start_brightness)
        timer.start()
        return text_result(f"Fade to off started ({minutes} min).")
    if name == "load_preset":
        preset_id = int(args["id"])
        client.post_state(lightctl.preset_payload(preset_id, transition_ms=transition_ms))
        return text_result(f"Loaded preset {preset_id}.")
    if name == "get_info":
        info = client.get_info()
        return text_result(json.dumps(info, indent=2))
    if name == "start_sunrise":
        minutes = float(args.get("minutes", 30))
        brightness = args.get("brightness")
        if brightness is not None:
            brightness = int(brightness)
        timer = lightctl.SunriseSimulator(client, duration_minutes=minutes, max_brightness=brightness or 255)
        timer.start()
        return text_result(f"Sunrise simulation started ({minutes} min).")
    if name == "start_audio_reactive":
        return text_result(modes.start())
    if name == "stop_audio_reactive":
        return text_result(modes.stop())
    if name == "recognize_music":
        if not music_recognizer.is_available():
            return text_result(f"Music recognition unavailable: {music_recognizer.available_reason()}")
        try:
            result = music_recognizer.recognize_sync()
            if result:
                parts = [f"Recognized: {result.get('title', 'Unknown')}"]
                if result.get("artist"):
                    parts.append(f"Artist: {result['artist']}")
                if result.get("album"):
                    parts.append(f"Album: {result['album']}")
                if result.get("genre"):
                    parts.append(f"Genre: {result['genre']}")
                return text_result("\n".join(parts))
            return text_result("No match found. Try playing music louder or closer to the microphone.")
        except Exception as exc:
            return text_result(f"Recognition failed: {exc}")
    if name == "match_lights_to_song":
        # Try playerctl / MPRIS first, then fall back to Shazam microphone
        song = None
        try:
            import shutil, subprocess, re
            if shutil.which("playerctl"):
                meta = subprocess.run(
                    ["playerctl", "metadata", "--format", "{{artist}}\n{{title}}\n{{album}}"],
                    capture_output=True, text=True, timeout=2,
                ).stdout
                status = subprocess.run(
                    ["playerctl", "status"],
                    capture_output=True, text=True, timeout=2,
                ).stdout.strip()
                lines = [l.strip() for l in meta.splitlines()]
                if len(lines) >= 2 and (lines[0] or lines[1]):
                    song = {"artist": lines[0], "title": lines[1], "album": lines[2] if len(lines) > 2 else "", "status": status}
            if not song:
                # Try MPRIS via dbus-send
                dbus = subprocess.run(
                    ["dbus-send", "--session", "--dest=org.freedesktop.DBus", "--type=method_call", "--print-reply", "/org/freedesktop/DBus", "org.freedesktop.DBus.ListNames"],
                    capture_output=True, text=True, timeout=2,
                ).stdout
                players = re.findall(r"org\.mpris\.MediaPlayer2\.([A-Za-z0-9_.-]+)", dbus)
                for player in players:
                    meta_out = subprocess.run(
                        ["dbus-send", "--session", "--dest=org.mpris.MediaPlayer2." + player, "--type=method_call",
                         "--print-reply", "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties.Get",
                         "string:org.mpris.MediaPlayer2.Player", "string:Metadata"],
                        capture_output=True, text=True, timeout=2,
                    ).stdout
                    title = re.search(r"'xesam:title': <'([^']*)'>", meta_out)
                    artist = re.search(r"'xesam:artist': <\['([^']*)'", meta_out)
                    album = re.search(r"'xesam:album': <'([^']*)'>", meta_out)
                    if title or artist:
                        song = {"artist": artist.group(1) if artist else "", "title": title.group(1) if title else "", "album": album.group(1) if album else "", "status": "Playing"}
                        break
            if not song and music_recognizer.is_available():
                result = music_recognizer.recognize_sync()
                if result:
                    song = {"artist": result.get("artist", ""), "title": result.get("title", ""), "album": result.get("album", ""), "genre": result.get("genre", ""), "status": "Playing", "source": "shazam"}
        except Exception:
            logger.exception("Song detection failed")
        if not song:
            return text_result("No music detected. Try playing a song first, or ensure a music player is active.")
        parts = [f"Now playing: {song.get('title', 'Unknown')} by {song.get('artist', 'Unknown')}"]
        if song.get("album"):
            parts.append(f"Album: {song['album']}")
        if song.get("genre"):
            parts.append(f"Genre: {song['genre']}")
        parts.append("Use this song info to choose lighting colors, effects, and speed that match its mood and genre. For example:")
        parts.append("- EDM/Pop: vibrant rainbows, fast chase, high energy")
        parts.append("- Jazz/Acoustic: warm amber, slow breathe, intimate")
        parts.append("- Metal/Dark: deep reds/purples, slow pulse, intense")
        parts.append("- Reggae/Funk: bright warm tones, flowing waves, upbeat")
        return text_result("\n".join(parts))
    raise ValueError(f"Unknown tool: {name}")


class McpServer:
    def __init__(self, client: lightctl.LightClient) -> None:
        self.client = client
        self.modes = lightctl.ReactiveThread(client)
        atexit.register(self._cleanup)

    def _cleanup(self) -> None:
        self.modes.stop()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "notifications/initialized":
            return None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                }
            elif method == "tools/list":
                result = {"tools": build_tools()}
            elif method == "tools/call":
                params = message.get("params", {})
                result = call_tool(
                    self.client,
                    str(params.get("name", "")),
                    params.get("arguments") or {},
                    self.modes,
                )
            else:
                return self.error(request_id, -32601, f"Method not found: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            logger.exception("Error handling MCP request")
            return self.error(request_id, -32000, str(exc))

    @staticmethod
    def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def serve(self) -> None:
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    response = self.handle(json.loads(line))
                except json.JSONDecodeError as exc:
                    response = self.error(None, -32700, f"Parse error: {exc}")
                if response is not None:
                    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                    sys.stdout.flush()
        except (EOFError, OSError):
            logger.info("Stdin closed, shutting down.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bedroom LED MCP stdio server.")
    parser.add_argument("--host", default=os.environ.get("LIGHT_HOST", lightctl.DEFAULT_HOST))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    client = StderrDryRunClient(args.host) if args.dry_run else lightctl.LightClient(args.host)
    McpServer(client).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
