#!/usr/bin/env python3
"""Minimal MCP stdio server for the bedroom LED controller."""

from __future__ import annotations

import argparse
import atexit
import inspect
import json
import logging
import os
import sys
import threading
from typing import Any

import atmospheres
import dynamic_scenes
import columns
import fleet
import lightctl
import look_agents
import look_memory
import realtime
import music_recognizer
import shows
logger = logging.getLogger("mcp_light")

SERVER_INFO = {"name": "bedroom-light-controller", "version": "1.1.0"}
MCP_PROTOCOL_VERSION = "2024-11-05"


class StderrDryRunClient(lightctl.LightClient):
    def post_state(self, payload: lightctl.WledPayload) -> None:
        lightctl.validate_wled_payload(payload)
        logger.debug("dry-run: %s", json.dumps(payload, separators=(",", ":")))


# One long-lived timer per kind (fade / sunrise): a new call stops and replaces
# the previous timer instead of stacking competing threads.
_timers_lock = threading.Lock()
_timers: dict[str, Any] = {}


def _start_timer(key: str, timer: Any) -> str:
    """Register and start a FadeTimer/SunriseSimulator, replacing any previous one."""
    with _timers_lock:
        old = _timers.get(key)
        if old is not None:
            old.stop()
        _timers[key] = timer
        return timer.start()


def _stop_timers() -> None:
    with _timers_lock:
        timers = list(_timers.values())
        _timers.clear()
    for timer in timers:
        timer.stop()


def int_schema(description: str, minimum: int = 0, maximum: int = 255) -> dict:
    return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}


def safe_effect_schema() -> dict:
    return {
        "type": "integer",
        "description": "Effect id from the device's live catalog (0-255; avoid strobe/blink/flash/lightning/fireworks/sparkle types)",
        "minimum": 0,
        "maximum": 255,
    }


def _transition_schema() -> dict:
    return {
        "type": "integer",
        "description": "Transition time in milliseconds; converted to WLED 100ms units, clamped to 25500ms",
        "minimum": 0,
        "maximum": 25500,
    }


def _target_schema() -> dict:
    return {
        "type": "string",
        "description": (
            "Target: 'all' (all four strips), a group (outer/inner/center), a controller "
            "(left/right = both strips on that side), one strip (far-left, middle-left, "
            "middle-right, far-right), or a combo like 'far-left,middle-right'."
        ),
        "default": "all",
    }


def _segment_schema() -> dict:
    return {"type": "integer", "description": "Segment id on the target controller", "minimum": 0, "maximum": 31}


# Tools whose payload builders accept a seg_id (per-segment targeting).
SEGMENT_TOOLS = {"set_color", "set_hex_color", "set_temperature", "set_effect"}


def build_tools() -> list[dict]:
    tools = [
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
            "description": "Set LED brightness from 0 to 255. With a channel target, this sets only that strip's segment brightness.",
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
                    "minutes": {"type": "number", "minimum": 1, "maximum": 120, "description": "Duration in minutes (values below 1 are rounded up to 1)"},
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
            "description": "Detect the currently playing song (via playerctl, MPRIS, or microphone/Shazam fallback) and return its title, artist, album, and genre along with suggested lighting styles that match its mood. Does not change the lights itself — apply a suggested look with the other tools.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "restart_controller",
            "description": "Reboot the WLED LED controller. The device will be briefly offline (a few seconds) while it restarts.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "list_controllers",
            "description": "List configured WLED controllers with their hosts and channel aliases.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "list_segments",
            "description": "List segments/channels per controller, the physical wall order, and all valid targets.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "wall_mode",
            "description": "Run a wall-wide pattern across all four columns: span (same everywhere), mirror (left mirrors right), chase (staggered offsets), or versus (left vs right effects).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "description": "Wall mode",
                        "enum": ["span", "mirror", "chase", "versus"],
                    },
                    "fx": int_schema("Effect id (span/mirror/chase)", 0, 255),
                    "pal": int_schema("Palette id (span/mirror/chase)", 0, 255),
                    "fx_left": int_schema("Left-side effect id (versus)", 0, 255),
                    "fx_right": int_schema("Right-side effect id (versus)", 0, 255),
                    "pal_left": int_schema("Left-side palette id (versus)", 0, 255),
                    "pal_right": int_schema("Right-side palette id (versus)", 0, 255),
                    "speed": int_schema("Effect speed", 0, 255),
                    "intensity": int_schema("Effect intensity", 0, 255),
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
        },
        {
            "name": "strips",
            "description": (
                "Address any combination of the four wall strips. "
                "Same look: pass channels (e.g. [\"far-left\",\"far-right\"]) plus fx. "
                "Different looks: pass assignments [{channel, fx, pal}]. "
                "Unused strips are left unchanged."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Strip names sharing one look: far-left, middle-left, middle-right, far-right",
                    },
                    "fx": int_schema("Effect id when using channels", 0, 255),
                    "pal": int_schema("Palette id when using channels", 0, 255),
                    "speed": int_schema("Effect speed", 0, 255),
                    "intensity": int_schema("Effect intensity", 0, 255),
                    "assignments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "channel": {"type": "string"},
                                "fx": int_schema("Effect id", 0, 255),
                                "pal": int_schema("Palette id", 0, 255),
                                "sx": int_schema("Speed", 0, 255),
                                "ix": int_schema("Intensity", 0, 255),
                            },
                            "required": ["channel"],
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "atmosphere",
            "description": "Apply a named curated atmosphere (multi-part look) across the wall.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Atmosphere name",
                        "enum": atmospheres.atmosphere_names(),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "dynamic_scene",
            "description": "Apply an opinionated, topology-aware, safe dynamic scene from mood/energy/motion words. Pass colors and a seed to build a unique palette. Prefer for creative or vague vibe requests; no raw fx/pal passthrough.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mood": {"type": "string", "description": "Mood words, e.g. dreamy, ocean, dark, cozy"},
                    "energy": {"type": "string", "description": "Energy words, e.g. calm, bright, party"},
                    "motion": {"type": "string", "description": "Motion words, e.g. rise, flow, chase"},
                    "strategy": {"type": "string", "enum": ["quiet_gradient", "split_temperature", "mirror", "center_out", "left_to_right", "chase", "vertical_rise", "top_glow", "bottom_glow", "center_bloom", "shimmer"]},
                    "engine": {"type": "string", "enum": ["generated", "effect"], "description": "generated paints exact-length per-strip pixel frames; effect uses safe stock WLED effects"},
                    "composition_mode": {"type": "string", "enum": ["unison", "independent", "pairs", "center_vs_outer", "left_vs_right", "alternating", "random_groups"]},
                    "seed": {"type": ["integer", "string"], "description": "Optional deterministic seed"},
                    "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                    "colors": {"type": "array", "description": "Unique palette stops as #RRGGBB hex strings", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "look_feedback",
            "description": "Record user feedback on the last/current lighting look so future AI scene choices improve.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "minimum": -1, "maximum": 1},
                    "notes": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "look_id": {"type": "string"},
                    "applies_to": {"type": "string", "enum": ["last"]},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "look_memory_summary",
            "description": "Show concise remembered lighting feedback/preferences.",
            "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False},
        },
        {
            "name": "realtime_start",
            "description": "Start a unique bounded AI-directed realtime DDP look. Pass colors + shader (or auto) + seed; no raw pixels.",
            "inputSchema": {"type": "object", "properties": {
                "shader": {"type": "string", "enum": ["auto", "red_rocks", "aurora_flow", "bass_bloom", "liquid_gradient", "center_wave", "vertical_scan", "ember_rise", "tide_pull", "comet_fall", "dusk_bloom", "magma_column", "twin_helix", "ribbon_drift"]},
                "mood": {"type": "string"},
                "energy": {"type": "string"},
                "motion": {"type": "string"},
                "colors": {"type": "array", "description": "Unique palette stops as hex strings or RGB lists", "items": {"type": ["string", "array"]}},
                "composition_mode": {"type": "string", "enum": ["unison", "independent", "pairs", "center_vs_outer", "left_vs_right", "alternating", "random_groups"]},
                "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                "fps": {"type": "integer", "minimum": 1, "maximum": 40},
                "duration_s": {"type": "number", "minimum": 0.1, "maximum": 900},
                "seed": {"type": ["integer", "string"]},
            }, "additionalProperties": False},
        },
        {"name": "realtime_stop", "description": "Stop realtime DDP rendering.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "realtime_status", "description": "Realtime DDP renderer status.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {
            "name": "design_look",
            "description": (
                "Design a unique wall look with optional specialist models (colorist/motion/critic). "
                "Local color_lab fallback if agents are off. Optionally run it via realtime DDP."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Creative look brief, e.g. ember canyon dusk"},
                    "mood": {"type": "string", "description": "Mood words, e.g. dreamy, ocean, ember"},
                    "energy": {"type": "string", "description": "Energy words, e.g. calm, bright, party"},
                    "motion": {"type": "string", "description": "Motion words, e.g. rise, flow, drift"},
                    "colors": {"type": "array", "description": "Unique palette stops as #RRGGBB hex strings", "items": {"type": "string"}},
                    "seed": {"type": ["integer", "string"], "description": "Optional deterministic seed"},
                    "run": {"type": "boolean", "description": "If true, start realtime DDP with the designed look"},
                    "fps": {"type": "integer", "minimum": 1, "maximum": 40},
                    "duration_s": {"type": "number", "minimum": 0.1, "maximum": 900},
                    "composition_mode": {"type": "string", "enum": ["unison", "independent", "pairs", "center_vs_outer", "left_vs_right", "alternating", "random_groups"]},
                    "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                    "shader": {"type": "string", "enum": ["auto", "red_rocks", "aurora_flow", "bass_bloom", "liquid_gradient", "center_wave", "vertical_scan", "ember_rise", "tide_pull", "comet_fall", "dusk_bloom", "magma_column", "twin_helix", "ribbon_drift"]},
                },
                "additionalProperties": False,
            },
        },
    ]
    # Fleet args: every tool accepts an optional target; seg-emitting tools also accept a segment id.
    for tool in tools:
        properties = tool["inputSchema"].setdefault("properties", {})
        properties.setdefault("target", _target_schema())
        if tool["name"] in SEGMENT_TOOLS:
            properties.setdefault("segment", _segment_schema())
    # AI-complete control: zones, raw segment bounds, per-LED frames, and the show sequencer.
    tools.extend(
        [
            {
                "name": "set_zone",
                "description": (
                    "Light one named zone of a single column (channel), e.g. the top half of far-left. "
                    "The zone is carved as a NEW segment with absolute bus bounds, so the rest of the "
                    "column keeps its current look; the reply names the new segment id (remove it with "
                    "delete_segment). Zones on the same column stack side by side along its height."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "description": "Channel (column) name: far-left, middle-left, middle-right, or far-right",
                        },
                        "zone": {
                            "type": "string",
                            "description": "Zone within the column: top|middle|bottom half|third|quarter, or 'all' (e.g. 'top third')",
                        },
                        "fx": int_schema("Effect id", 0, 255),
                        "pal": int_schema("Palette id", 0, 255),
                        "col": {"type": "string", "description": "Hex color 'RRGGBB'"},
                        "sx": int_schema("Effect speed", 0, 255),
                        "ix": int_schema("Effect intensity", 0, 255),
                        "rev": {"type": "boolean", "description": "Reverse effect direction"},
                        "mi": {"type": "boolean", "description": "Mirror the effect"},
                    },
                    "required": ["channel", "zone"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "set_segment_bounds",
                "description": (
                    "Set explicit LED bounds and grouping for a segment: carve a column into arbitrary "
                    "sub-segments. start/stop are LED indices within the column; the runtime installation topology gives per-column pixel counts."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": _target_schema(),
                        "id": int_schema("Segment id", 0, 31),
                        "start": int_schema("First LED index of the segment", 0, 65535),
                        "stop": int_schema("LED index after the last LED of the segment", 0, 65535),
                        "grp": int_schema("Grouping: how many consecutive LEDs are grouped together", 1, 255),
                        "spc": int_schema("Spacing: how many LEDs are skipped between groups", 0, 255),
                        "of": int_schema("Effect offset", 0, 65535),
                        "rev": {"type": "boolean", "description": "Reverse effect direction"},
                        "mi": {"type": "boolean", "description": "Mirror the effect"},
                    },
                    "required": ["target", "id", "start", "stop"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "delete_segment",
                "description": "Delete a segment on the target (sets its stop to 0, which makes WLED remove the segment).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": _target_schema(),
                        "id": int_schema("Segment id to delete", 0, 31),
                    },
                    "required": ["target", "id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "set_leds",
                "description": (
                    "Set individual LED colors on the target. Setting individual LEDs freezes the running "
                    "effect on that segment until any segment property changes."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": _target_schema(),
                        "segment": _segment_schema(),
                        "leds": {
                            "type": "array",
                            "description": (
                                "Per-LED colors: either ['RRGGBB', ...] starting from LED 0, or "
                                "[[start, stop, 'RRGGBB'], ...] ranges (stop exclusive)."
                            ),
                            "items": {"type": ["string", "array"]},
                        },
                    },
                    "required": ["target", "leds"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "start_show",
                "description": (
                    "Start a timed multi-step light show. A show is "
                    "{'name'?, 'loop'?: bool, 'steps': [{'look': {...}, 'duration_s': float, 'transition_s'?}, ...]}; "
                    "a look is {'atmosphere': name}, {'wall_mode': 'span|mirror|chase|versus', ...kwargs}, or "
                    "{'payload': {...}, 'target'?}. With loop=true the show repeats until stop_show."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "show": {"type": "object", "description": "Show definition (see tool description)"},
                    },
                    "required": ["show"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "stop_show",
                "description": "Stop the currently running light show, if any.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "show_status",
                "description": "Report whether a light show is running, its name, current step, and loop setting.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        ]
    )
    # Fire TV companion: the TV can complement light shows (wake + open visuals, or sleep
    # for darkness). All control tools require Fire TV control to be enabled in the GUI settings.
    tools.extend(
        [
            {
                "name": "tv_status",
                "description": (
                    "Read the Fire TV status: whether control is enabled, the ADB connection state, "
                    "whether the screen is awake, and the foreground app. Works even when Fire TV "
                    "control is disabled in the GUI settings."
                ),
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "tv_wake",
                "description": (
                    "Wake the Fire TV screen so it can complement a light show (pair with tv_open_url "
                    "to display visuals). Requires Fire TV control to be enabled in the GUI settings."
                ),
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "tv_sleep",
                "description": (
                    "Put the Fire TV screen to sleep for darkness, e.g. before a light show or when "
                    "the room should go dark. Requires Fire TV control to be enabled in the GUI settings."
                ),
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "tv_open_url",
                "description": (
                    "Open a URL on the Fire TV (e.g. a generative visuals page) so the screen can "
                    "complement the light show. Requires Fire TV control to be enabled in the GUI settings."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to open on the Fire TV"},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
        ]
    )
    # Music Director: mood-matching music mode. Appended after the fleet-arg
    # injection loop above, so it does not get target/segment args.
    tools.append(
        {
            "name": "music_director",
            "description": (
                "Mood-matching music mode: a background director watches now-playing "
                "(MPRIS) and applies audio-reactive looks so the wall follows the mood "
                "of the music (beat handling is done by the controller hardware). "
                "When music stops, it leaves the current look alone by default."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Director action",
                        "enum": ["start", "stop", "status"],
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        }
    )
    tools.append(
        {
            "name": "music_show",
            "description": (
                "Inspect or tune the currently active Smart Director music renderer without "
                "stopping or replacing its live DDP stream. Tune its palette, geometry, EQ "
                "gains, brightness, and motion; accent triggers one transient EQ-band hit."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "tune", "accent"],
                        "default": "status",
                    },
                    "motion": {
                        "type": "string",
                        "enum": ["auto", "flow", "punch", "chase", "spectrum", "comet", "ripple"],
                    },
                    "speed": {"type": "number", "minimum": 0.25, "maximum": 4},
                    "brightness": {"type": "number", "minimum": 0, "maximum": 1},
                    "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                    "colorfulness": {"type": "number", "minimum": 0, "maximum": 1},
                    "colors": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "items": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "items": {"type": "integer", "minimum": 0, "maximum": 210},
                        },
                    },
                    "composition_mode": {
                        "type": "string",
                        "enum": ["unison", "independent", "pairs", "center_vs_outer", "left_vs_right", "alternating", "random_groups"],
                    },
                    "band_gains": {
                        "type": "array",
                        "minItems": 16,
                        "maxItems": 16,
                        "items": {"type": "number", "minimum": 0, "maximum": 3},
                    },
                    "band": {"type": "integer", "minimum": 0, "maximum": 15},
                    "strength": {"type": "number", "minimum": 0, "maximum": 1, "default": 1},
                },
                "additionalProperties": False,
            },
        }
    )
    # Raw WLED JSON API access: the escape hatch that lets the AI use ANY state
    # key WLED supports (playlists, psave, nightlight, udpn, seg options like
    # grp/spc/of, c1-c3 custom sliders) without waiting for a bespoke tool.
    # Appended after the fleet-arg injection loop (own schemas).
    tools.extend(
        [
            {
                "name": "wled_read",
                "description": (
                    "Read raw data straight from the WLED controllers' JSON API. Sections: "
                    "state (current /json/state), info (version/LED counts), effects (full live "
                    "effect name list), palettes, fxdata (per-effect parameter metadata: "
                    "sx/ix/c1-c3 slider labels, color slots, palette support, flags), config, "
                    "presets, nodes, networks, full (entire /json). Use to answer 'what can this "
                    "device do' questions or to check exactly what an effect's sliders mean."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "enum": ["state", "info", "effects", "palettes", "fxdata",
                                     "config", "presets", "nodes", "networks", "full"],
                            "description": "Which part of the JSON API to read (default: state)",
                        },
                        "target": _target_schema(),
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "wled_write",
                "description": (
                    "POST a raw WLED /json/state body to the target — full direct control for "
                    "anything the typed tools don't cover: playlists, psave/pset presets, "
                    "nightlight (nl), UDP sync (udpn), segment options (grp/spc/of/ranged bounds), "
                    "c1/c2/c3 custom effect sliders, per-segment bri/on/frz. Effect ids in the "
                    "payload are still checked against the 🚫 forbidden list. Keep payloads under "
                    "8KB; split larger writes into multiple calls."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "payload": {
                            "type": "object",
                            "description": "Raw WLED /json/state body, e.g. {'seg': {'id': 0, 'fx': 139, 'pal': 6}}",
                        },
                        "target": _target_schema(),
                    },
                    "required": ["payload"],
                    "additionalProperties": False,
                },
            },
        ]
    )
    # Self-calibration: probe the live controllers for real LED counts, bus
    # GPIOs, segment bounds, and color order; physically locate strips by
    # flashing them. Appended last (own schemas, no fleet-arg injection).
    tools.extend(
        [
            {
                "name": "calibrate",
                "description": (
                    "Self-calibrate the installation from the live controllers: probe each device for "
                    "its real LED counts, bus GPIOs, segment bounds, and color order, and report the "
                    "calibrated topology. Devices are only read, never written. With write=true the "
                    "calibrated topology is persisted to the local config (a timestamped backup is "
                    "written first). Use assignments to name channels after identify() reveals which "
                    "physical column a segment is; pixel_zero/orientation record the physical axis."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "write": {
                            "type": "boolean",
                            "description": "Persist the calibrated topology to the local config (backup first)",
                        },
                        "assignments": {
                            "type": "array",
                            "description": "Channel names to assign, e.g. [{'controller': 'left', 'segment': 0, 'channel': 'far-left'}]",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "controller": {"type": "string"},
                                    "segment": {"type": "integer", "minimum": 0, "maximum": 31},
                                    "channel": {"type": "string"},
                                },
                                "required": ["controller", "segment", "channel"],
                                "additionalProperties": False,
                            },
                        },
                        "pixel_zero": {
                            "type": "string",
                            "enum": ["top", "bottom", "left", "right"],
                            "description": "Physical location of LED 0 (written to the installation config)",
                        },
                        "orientation": {
                            "type": "string",
                            "enum": ["vertical", "horizontal"],
                            "description": "Physical column orientation (written to the installation config)",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "identify",
                "description": (
                    "Physically locate a strip or controller by flashing it off/on a few times "
                    "(segments and master power return to their prior state). Use before calibrate "
                    "assignments to learn which segment is which physical column."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": _target_schema(),
                        "flashes": int_schema("Number of off/on flashes", 1, 12),
                    },
                    "additionalProperties": False,
                },
            },
        ]
    )
    return tools


def text_result(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}]}


def _compact_music_show_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep renderer facts complete while replacing bulky pixel previews with metadata."""
    compact = dict(result)
    status = result.get("status")
    if not isinstance(status, dict):
        return compact
    compact_status = dict(status)
    compact["status"] = compact_status
    renderer = status.get("renderer")
    if not isinstance(renderer, dict):
        return compact
    compact_renderer = dict(renderer)
    compact_status["renderer"] = compact_renderer
    preview = renderer.get("preview")
    if isinstance(preview, list):
        preview_metadata = []
        for item in preview:
            if not isinstance(item, dict):
                continue
            metadata = {key: value for key, value in item.items() if key != "colors"}
            colors = item.get("colors")
            metadata["color_count"] = len(colors) if isinstance(colors, list) else 0
            preview_metadata.append(metadata)
        compact_renderer["preview"] = preview_metadata
    return compact


def _kwargs_for(fn: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Keep kwargs the callee accepts so extra design_look hints can be ignored."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return kwargs
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in sig.parameters.values()):
        return kwargs
    allowed = {
        name
        for name, parameter in sig.parameters.items()
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return {key: value for key, value in kwargs.items() if key in allowed}


def _is_fleet(client: Any) -> bool:
    return hasattr(client, "channels") and hasattr(client, "resolve")


# ---------------------------------------------------------------------------
# Live effect-catalog policy
#
# The offline SAFE_EFFECTS allowlist in lightctl is a fallback for when no
# device catalog has been seen. Once a live catalog is seeded here (the GUI
# seeds it from every AI snapshot; the MCP server seeds it at startup), the
# AI-facing tools accept every id in the live catalog EXCEPT the 🚫-marked
# ones (strobe/blink/flash/lightning/fireworks/sparkle names + RSVD
# placeholders), exactly as the system prompt promises. Everything is
# cache-only: seeding never happens on the tool-call hot path, so tests and
# offline runs keep the legacy SAFE_EFFECTS behavior.
# ---------------------------------------------------------------------------

_fx_policy_lock = threading.Lock()
_fx_allowed: dict[str, set[int]] = {}  # controller name (or host) -> allowed live effect ids


def seed_effect_catalog(key: str, effects: Any, fxdata: Any = None) -> None:
    """Record the usable effect ids of one controller's live catalog."""
    if not isinstance(effects, list):
        with _fx_policy_lock:
            _fx_allowed.pop(str(key), None)
        return
    classified = atmospheres.classify_effects(effects, fxdata if isinstance(fxdata, list) else [])
    allowed = {effect_id for effect_id, info in classified.items() if not info["unsafe"]}
    with _fx_policy_lock:
        _fx_allowed[str(key)] = allowed


def allowed_effects_for(client: Any, target: str) -> set[int] | None:
    """Live effect ids valid for target (unsafe ones excluded); None when unknown.

    Multiple controllers intersect, so an id is only offered when every
    resolved controller can actually run it.
    """
    if _is_fleet(client):
        try:
            keys = [name for name, _seg_id in client.resolve(target)]
        except Exception:
            return None
    else:
        keys = [str(getattr(client, "host", ""))]
    with _fx_policy_lock:
        if not any(key in _fx_allowed for key in keys):
            return None
        sets = [_fx_allowed.get(key, set(lightctl.SAFE_EFFECTS) | {0}) for key in keys]
    if not sets:
        return None
    allowed = set(sets[0])
    for extra in sets[1:]:
        allowed = allowed & extra
    return allowed


def _check_fx_allowed(client: Any, target: str, *fx_ids: Any) -> None:
    """Validate live catalogs; missing catalogs use the conservative offline set."""
    allowed = allowed_effects_for(client, target)
    if allowed is None:
        allowed = set(lightctl.SAFE_EFFECTS) | {0}
    for fx in fx_ids:
        if fx is None:
            continue
        if int(fx) not in allowed:
            raise ValueError(
                f"Effect {fx} is not usable on the target: it is absent from the "
                "device's live catalog or 🚫 forbidden (strobe-type). Pick another "
                "id from the catalog in the device snapshot."
            )


def _post_state(client: Any, payload: lightctl.WledPayload, target: str) -> dict[str, dict] | None:
    """Post via the fleet when available; returns the per-controller result or None."""
    if _is_fleet(client):
        return client.post_state(payload, target=target)
    client.post_state(payload)
    return None


def _get_state(client: Any, target: str) -> dict:
    if _is_fleet(client):
        return client.get_state(target=target)
    return client.get_state()


def _is_channel_target(client: Any, target: str) -> bool:
    return _is_fleet(client) and target in client.channels()


def _with_fleet_status(message: str, result: dict[str, dict] | None) -> str:
    """Append per-controller failures (and fan-out note) to a tool result message."""
    if not result:
        return message
    failures = [
        f"{ctrl}: {entry.get('error', 'unknown error')}"
        for ctrl, entry in result.items()
        if isinstance(entry, dict) and not entry.get("ok", True)
    ]
    if failures:
        return f"{message} Failures: {'; '.join(failures)}"
    if len(result) > 1:
        return f"{message} (sent to {', '.join(result)})"
    return message


def _controllers_info(client: Any) -> list[dict]:
    if _is_fleet(client):
        channels = client.channels()
        clients = getattr(client, "clients", {})
        return [
            {
                "name": name,
                "host": getattr(clients.get(name), "host", None),
                "channels": sorted(ch for ch, (ctrl, _seg) in channels.items() if ctrl == name),
            }
            for name in client.names()
        ]
    return [{"name": "default", "host": getattr(client, "host", None), "channels": []}]


# ---------------------------------------------------------------------------
# Raw WLED JSON API tools (wled_read / wled_write)
# ---------------------------------------------------------------------------

_WLED_READ_SECTIONS = {
    "state": "get_state",
    "info": "get_info",
    "effects": "get_effects",
    "palettes": "get_palettes",
    "fxdata": "get_fxdata",
    "config": "get_config",
    "presets": "get_presets",
    "nodes": "get_nodes",
    "networks": "get_networks",
    "full": "get_json",
}

_WLED_WRITE_MAX_BYTES = 8192


def _payload_fx_ids(payload: dict) -> list[int]:
    """Collect effect ids from a raw /json/state body ('seg' dict or list)."""
    seg = payload.get("seg")
    entries = seg if isinstance(seg, list) else [seg] if isinstance(seg, dict) else []
    fx_ids: list[int] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("fx") is not None:
            try:
                fx_ids.append(int(entry["fx"]))
            except (TypeError, ValueError):
                continue
    return fx_ids


def _wled_read(client: Any, args: dict[str, Any]) -> str:
    section = str(args.get("section") or "state")
    method_name = _WLED_READ_SECTIONS.get(section)
    if method_name is None:
        raise ValueError(
            f"Unknown wled_read section '{section}' (valid: {', '.join(_WLED_READ_SECTIONS)})."
        )
    target = str(args.get("target") or fleet.DEFAULT_TARGET)

    def read_one(ctrl_client: Any) -> Any:
        method = getattr(ctrl_client, method_name, None)
        if method is None:
            return {"error": f"controller does not support reading '{section}'"}
        return method()

    if _is_fleet(client):
        clients = getattr(client, "clients", {})
        out: dict[str, Any] = {}
        for ctrl_name, _seg_id in client.resolve(target):
            ctrl_client = clients.get(ctrl_name)
            try:
                out[ctrl_name] = read_one(ctrl_client) if ctrl_client is not None else {"error": "no client"}
            except Exception as exc:
                out[ctrl_name] = {"error": str(exc)}
        return json.dumps(out, indent=2)
    return json.dumps(read_one(client), indent=2)


def _wled_write(client: Any, args: dict[str, Any]) -> str:
    payload = args.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise ValueError("wled_write needs a non-empty 'payload' object (a WLED /json/state body).")
    try:
        encoded = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload is not JSON-serializable: {exc}")
    if len(encoded) > _WLED_WRITE_MAX_BYTES:
        raise ValueError(
            f"payload is {len(encoded)} bytes (max {_WLED_WRITE_MAX_BYTES}); "
            "split it into multiple wled_write calls."
        )
    target = str(args.get("target") or fleet.DEFAULT_TARGET)
    fx_ids = _payload_fx_ids(payload)
    if fx_ids:
        _check_fx_allowed(client, target, *fx_ids)
    # WLED accepts 'seg' as object or list; the local validation chain requires
    # a list, so normalize the dict form (on a copy — never mutate the caller's).
    if isinstance(payload.get("seg"), dict):
        payload = {**payload, "seg": [payload["seg"]]}
    result = _post_state(client, payload, target)
    for fx in fx_ids:
        lightctl.record_fx_use(fx, source="wled_write")
    return _with_fleet_status(f"Posted raw WLED state ({len(encoded)} bytes).", result)


def _segments_info(client: Any) -> dict:
    if not _is_fleet(client):
        return {
            "wall_order": [],
            "controllers": {},
            "channels": {},
            "valid_targets": [fleet.DEFAULT_TARGET],
        }
    channels = client.channels()
    controllers: dict[str, dict[str, str]] = {}
    for channel, (ctrl, seg_id) in channels.items():
        controllers.setdefault(ctrl, {})[str(seg_id)] = channel
    return {
        "wall_order": list(getattr(getattr(client, "installation", None), "wall_order", fleet.WALL_ORDER)),
        "controllers": controllers,
        "channels": {ch: [ctrl, seg_id] for ch, (ctrl, seg_id) in channels.items()},
        "valid_targets": list(client.valid_targets()) if hasattr(client, "valid_targets") else list(dict.fromkeys([fleet.DEFAULT_TARGET, *client.names(), *channels])),
    }


def _tv_action(action: Any, success: str) -> dict:
    """Run a firetv.py action; disabled-state ValueErrors surface as error text."""
    import firetv

    try:
        action(firetv)
    except ValueError as exc:
        return text_result(str(exc))
    except Exception as exc:
        return text_result(f"Fire TV command failed: {exc}")
    return text_result(success)


def _led_orientation() -> str:
    """Configured LED orientation ('up' = LED 0 at the bottom); drives zone math."""
    return str(lightctl.load_config().get("led_orientation", "up"))


def _segment_config_for(client: Any, controller: str, seg_id: int | None) -> Any:
    """The configured SegmentConfig for (controller, seg_id), if available."""
    for ctrl in getattr(client, "controllers", []):
        if getattr(ctrl, "name", None) == controller:
            return getattr(ctrl, "segments", {}).get(seg_id)
    return None


def _used_segment_ids(client: Any, controller: str) -> set[int]:
    """Segment ids already taken on a controller: configured plus live state."""
    used = {
        seg_id
        for _channel, (ctrl, seg_id) in client.channels().items()
        if ctrl == controller
    }
    try:
        states = client.get_state(target=controller)
        state = states.get(controller) if isinstance(states, dict) else None
    except Exception:
        state = None  # unreadable state must never block a zone
    if isinstance(state, dict):
        for seg in state.get("seg") or []:
            if isinstance(seg, dict) and isinstance(seg.get("id"), int):
                used.add(seg["id"])
    return used


def _allocate_segment_id(client: Any, controller: str) -> int:
    """Lowest segment id free on the controller (honoring the device maxseg)."""
    used = _used_segment_ids(client, controller)
    maxseg = 16  # ESP32 default; conservative when the device can't be asked
    try:
        ctrl_client = getattr(client, "clients", {}).get(controller)
        info = ctrl_client.get_info() if ctrl_client is not None else {}
        reported = int(info.get("leds", {}).get("maxseg") or 0)
        if reported > 0:
            maxseg = reported
    except Exception:
        pass
    for candidate in range(maxseg):
        if candidate not in used:
            return candidate
    raise ValueError(
        f"No free segment on {controller} (maxseg {maxseg}). "
        "Remove a zone with delete_segment first."
    )


def _set_zone(client: Any, args: dict[str, Any]) -> str:
    if not _is_fleet(client):
        raise ValueError("set_zone requires fleet mode (run without --host).")
    channel = str(args["channel"])
    controller, main_seg = client.resolve(channel)[0]  # raises on unknown channels
    if args.get("fx") is not None:
        _check_fx_allowed(client, channel, int(args["fx"]))
    zone_name = str(args["zone"])
    zone: dict[str, Any] = {}
    if args.get("fx") is not None:
        zone["fx"] = int(args["fx"])
    if args.get("pal") is not None:
        zone["pal"] = int(args["pal"])
    if args.get("col") is not None:
        zone["col"] = str(args["col"])  # zone_payload parses the RRGGBB hex string
    if args.get("sx") is not None:
        zone["sx"] = lightctl.clamp_byte(int(args["sx"]))
    if args.get("ix") is not None:
        zone["ix"] = lightctl.clamp_byte(int(args["ix"]))
    if args.get("rev") is not None:
        zone["rev"] = bool(args["rev"])
    if args.get("mi") is not None:
        zone["mi"] = bool(args["mi"])

    # Two fixes relative to the legacy behavior:
    # 1. The zone becomes a NEW segment on the controller instead of resizing
    #    the channel's main segment, so the rest of the column keeps its look.
    # 2. Bounds are computed against the channel's real pixel count and offset
    #    to its absolute bus range (WLED start/stop are bus-absolute). Without
    #    configured segment geometry we fall back to the legacy raw bounds.
    seg_config = _segment_config_for(client, controller, main_seg)
    pixels = getattr(seg_config, "pixels", None)
    bus_start = getattr(seg_config, "start", None)
    bus_stop = getattr(seg_config, "stop", None)
    orientation = _led_orientation()
    if pixels and bus_start is not None and bus_stop is not None:
        local_start, local_stop = lightctl.zone_bounds(zone_name, length=pixels, orientation=orientation)
        zone["start"] = bus_start + local_start
        zone["stop"] = bus_start + local_stop
        payload_length = bus_stop
    else:
        zone["zone"] = zone_name
        payload_length = lightctl.LEDS_PER_COLUMN
    zone["id"] = _allocate_segment_id(client, controller)
    payload = lightctl.zone_payload([zone], length=payload_length, orientation=orientation)
    result = _post_state(client, payload, controller)
    if "fx" in zone:
        lightctl.record_fx_use(zone["fx"], source="set_zone")
    return _with_fleet_status(
        f"Set {zone_name} of {channel} as new segment {zone['id']} on {controller} "
        f"(delete_segment target={controller} id={zone['id']} removes it).",
        result,
    )


def _set_segment_bounds(client: Any, args: dict[str, Any]) -> str:
    start = int(args["start"])
    stop = int(args["stop"])
    if start >= stop:
        raise ValueError(
            f"start ({start}) must be less than stop ({stop}); "
            "WLED deletes segments whose stop <= start."
        )
    segment: dict[str, Any] = {
        "id": int(args["id"]),
        "start": start,
        "stop": stop,
    }
    for key in ("grp", "spc", "of"):
        if args.get(key) is not None:
            segment[key] = int(args[key])
    for key in ("rev", "mi"):
        if args.get(key) is not None:
            segment[key] = bool(args[key])
    result = _post_state(client, lightctl.segment_payload([segment]), target=str(args["target"]))
    return _with_fleet_status(
        f"Segment {segment['id']} bounds set to {segment['start']}-{segment['stop']}.", result
    )


def _wall_mode(client: Any, args: dict[str, Any]) -> str:
    if not _is_fleet(client):
        raise ValueError("wall_mode requires fleet mode (run without --host).")
    mode = str(args["mode"])
    seg_opts: dict[str, Any] = {}
    if args.get("speed") is not None:
        seg_opts["sx"] = lightctl.clamp_byte(int(args["speed"]))
    if args.get("intensity") is not None:
        seg_opts["ix"] = lightctl.clamp_byte(int(args["intensity"]))

    def _opt_int(key: str) -> int | None:
        return int(args[key]) if args.get(key) is not None else None

    if mode in ("span", "mirror", "chase"):
        if args.get("fx") is None:
            raise ValueError(f"wall_mode '{mode}' requires fx.")
        _check_fx_allowed(client, fleet.DEFAULT_TARGET, int(args["fx"]))
        composer = {"span": columns.wall_span, "mirror": columns.mirror, "chase": columns.chase}[mode]
        result = composer(client, int(args["fx"]), _opt_int("pal"), **seg_opts)
        lightctl.record_fx_use(int(args["fx"]), source=f"wall_mode:{mode}")
    elif mode == "versus":
        if args.get("fx_left") is None or args.get("fx_right") is None:
            raise ValueError("wall_mode 'versus' requires fx_left and fx_right.")
        _check_fx_allowed(client, fleet.DEFAULT_TARGET, int(args["fx_left"]), int(args["fx_right"]))
        result = columns.left_vs_right(
            client,
            int(args["fx_left"]),
            int(args["fx_right"]),
            pal_left=_opt_int("pal_left"),
            pal_right=_opt_int("pal_right"),
            **seg_opts,
        )
        lightctl.record_fx_use(int(args["fx_left"]), source="wall_mode:versus")
        lightctl.record_fx_use(int(args["fx_right"]), source="wall_mode:versus")
    else:
        raise ValueError(f"Unknown wall mode: {mode} (valid: span, mirror, chase, versus)")
    return _with_fleet_status(f"Wall mode {mode} applied.", result)


def call_tool(
    client: lightctl.LightClient | fleet.LightFleet,
    name: str,
    arguments: dict[str, Any] | None,
    modes: lightctl.ReactiveThread,
) -> dict:
    args = arguments or {}
    # DDP/show workers can bypass fleet.post_state; hand off before spawning one.
    streaming = name in {"realtime_start", "start_show", "start_audio_reactive", "identify"}
    streaming = streaming or (name == "design_look" and bool(args.get("run")))
    streaming = streaming or (name == "music_director" and str(args.get("action", "")).lower() == "start")
    if streaming:
        director = sys.modules.get("smart_director")
        if director is not None:
            director.before_external_write(client)
    transition_ms = int(args.get("transition", 0))
    target = str(args.get("target", fleet.DEFAULT_TARGET))
    segment = args.get("segment")
    seg_kwargs: dict[str, Any] = {"seg_id": int(segment)} if segment is not None else {}

    if name == "light_on":
        result = _post_state(client, lightctl.on_payload(True, transition_ms=transition_ms), target)
        return text_result(_with_fleet_status("Turned bedroom LEDs on.", result))
    if name == "light_off":
        result = _post_state(client, lightctl.on_payload(False, transition_ms=transition_ms), target)
        return text_result(_with_fleet_status("Turned bedroom LEDs off.", result))
    if name == "get_state":
        state = _get_state(client, target)
        return text_result(json.dumps(state, indent=2))
    if name == "set_brightness":
        brightness = int(args["brightness"])
        if _is_channel_target(client, target):
            payload = lightctl.segment_payload([{"bri": lightctl.clamp_byte(brightness)}])
            if transition_ms > 0:
                payload["transition"] = lightctl._transition_units(transition_ms)
        else:
            payload = lightctl.brightness_payload(brightness, transition_ms=transition_ms)
        result = _post_state(client, payload, target)
        return text_result(_with_fleet_status(f"Set brightness to {lightctl.clamp_byte(brightness)}.", result))
    if name == "set_color":
        red = int(args.get("red", 0))
        green = int(args.get("green", 0))
        blue = int(args.get("blue", 0))
        white = int(args.get("white", 0))
        payload = lightctl.color_payload(red, green, blue, white, transition_ms=transition_ms, **seg_kwargs)
        result = _post_state(client, payload, target)
        return text_result(_with_fleet_status(f"Set color to RGBW({red}, {green}, {blue}, {white}).", result))
    if name == "set_temperature":
        kelvin = int(args["kelvin"])
        rgbw = lightctl.kelvin_to_rgbw(kelvin)
        payload = lightctl.color_payload(*rgbw, transition_ms=transition_ms, **seg_kwargs)
        result = _post_state(client, payload, target)
        return text_result(_with_fleet_status(f"Set temperature to {kelvin}K (RGBW{rgbw}).", result))
    if name == "set_effect":
        effect = int(args["effect"])
        speed = int(args.get("speed", 128))
        # Live catalog (seeded from the device) unlocks every non-forbidden
        # effect id; offline we fall back to the SAFE_EFFECTS allowlist.
        allowed = allowed_effects_for(client, target)
        payload = lightctl.effect_payload(effect, speed, transition_ms=transition_ms, allowed=allowed, **seg_kwargs)
        result = _post_state(client, payload, target)
        lightctl.record_fx_use(effect, source="set_effect")
        return text_result(_with_fleet_status(f"Set effect {effect} at speed {lightctl.clamp_byte(speed)}.", result))
    if name == "set_scene":
        scene = str(args["name"])
        result = _post_state(client, lightctl.scene_payload(scene, transition_ms=transition_ms), target)
        return text_result(_with_fleet_status(f"Set scene to {scene}.", result))
    if name == "save_scene":
        scene_name = str(args["name"])
        state = _get_state(client, target)
        if _is_fleet(client):
            state = next(
                (
                    entry
                    for entry in state.values()
                    if isinstance(entry, dict) and any(key in entry for key in ("on", "bri", "seg"))
                ),
                {},
            )
            if not state:
                return text_result(f"No state available for target '{target}'; scene not saved.")
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
        payload = lightctl.color_payload(*rgbw, transition_ms=transition_ms, **seg_kwargs)
        result = _post_state(client, payload, target)
        return text_result(_with_fleet_status(f"Set hex color {hex_color} -> RGBW{rgbw}.", result))
    if name == "random_scene":
        payload = lightctl.random_scene_payload(transition_ms=transition_ms)
        result = _post_state(client, payload, target)
        return text_result(_with_fleet_status("Set a random built-in scene.", result))
    if name == "fade_off":
        minutes = max(1.0, float(args["minutes"]))  # FadeTimer clamps to >= 1 minute
        start_brightness = args.get("brightness")
        if start_brightness is not None:
            start_brightness = int(start_brightness)
        elif _is_fleet(client):
            # LightFleet.get_state returns {controller: state} with no top-level
            # bri; take the first available controller's brightness instead.
            states = client.get_state(target=target)
            start_brightness = next(
                (
                    int(state["bri"])
                    for state in states.values()
                    if isinstance(state, dict) and isinstance(state.get("bri"), (int, float))
                ),
                None,
            )
        timer = lightctl.FadeTimer(client, minutes, start_brightness=start_brightness)
        message = _start_timer("fade", timer)
        return text_result(message)
    if name == "load_preset":
        preset_id = int(args["id"])
        result = _post_state(client, lightctl.preset_payload(preset_id, transition_ms=transition_ms), target)
        return text_result(_with_fleet_status(f"Loaded preset {preset_id}.", result))
    if name == "get_info":
        if _is_fleet(client):
            clients = getattr(client, "clients", {})
            infos: dict[str, Any] = {}
            for ctrl_name, _seg_id in client.resolve(target):
                ctrl_client = clients.get(ctrl_name)
                try:
                    infos[ctrl_name] = ctrl_client.get_info() if ctrl_client is not None else {"error": "no client"}
                except Exception as exc:
                    infos[ctrl_name] = {"error": str(exc)}
            return text_result(json.dumps(infos, indent=2))
        info = client.get_info()
        return text_result(json.dumps(info, indent=2))
    if name == "start_sunrise":
        minutes = float(args.get("minutes", 30))
        brightness = args.get("brightness")
        if brightness is not None:
            brightness = int(brightness)
        timer = lightctl.SunriseSimulator(
            client,
            duration_minutes=minutes,
            max_brightness=255 if brightness is None else brightness,
        )
        message = _start_timer("sunrise", timer)
        return text_result(message)
    if name == "start_audio_reactive":
        return text_result(modes.start())
    if name == "stop_audio_reactive":
        return text_result(modes.stop())
    if name == "recognize_music":
        if not music_recognizer.is_available():
            return text_result(f"Music recognition unavailable: {music_recognizer.available_reason()}")
        try:
            result = music_recognizer.recognize_ambient_sync()
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
    if name == "restart_controller":
        try:
            result = _post_state(client, lightctl.restart_payload(), target)
        except RuntimeError:
            result = None  # Device may reboot before completing the HTTP response
        return text_result(
            _with_fleet_status("Restart command sent. Device will reconnect in a few seconds.", result)
        )
    if name == "match_lights_to_song":
        # Try playerctl / MPRIS first, then fall back to Shazam microphone
        song = None
        try:
            import shutil
            import subprocess
            import re
            if shutil.which("playerctl"):
                meta = subprocess.run(
                    ["playerctl", "metadata", "--format", "{{artist}}\n{{title}}\n{{album}}"],
                    capture_output=True, text=True, timeout=2,
                ).stdout
                status = subprocess.run(
                    ["playerctl", "status"],
                    capture_output=True, text=True, timeout=2,
                ).stdout.strip()
                lines = [line.strip() for line in meta.splitlines()]
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
        except Exception:
            logger.exception("Song detection failed")
        # Microphone/Shazam fallback runs even when the playerctl/dbus probing failed.
        if not song and music_recognizer.is_available():
            try:
                result = music_recognizer.recognize_ambient_sync()
                if result:
                    song = {"artist": result.get("artist", ""), "title": result.get("title", ""), "album": result.get("album", ""), "genre": result.get("genre", ""), "status": "Playing", "source": "shazam"}
            except Exception:
                logger.exception("Microphone song detection failed")
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
    if name == "list_controllers":
        return text_result(json.dumps(_controllers_info(client), indent=2))
    if name == "list_segments":
        return text_result(json.dumps(_segments_info(client), indent=2))
    if name == "wall_mode":
        return text_result(_wall_mode(client, args))
    if name == "strips":
        if not _is_fleet(client):
            raise ValueError("strips requires fleet mode (run without --host).")
        assignments = args.get("assignments")
        if isinstance(assignments, list) and assignments:
            for spec in assignments:
                if isinstance(spec, dict) and spec.get("fx") is not None:
                    _check_fx_allowed(client, str(spec.get("channel") or ""), int(spec["fx"]))
            result = columns.per_strip(client, assignments)
            for spec in assignments:
                if isinstance(spec, dict) and spec.get("fx") is not None:
                    lightctl.record_fx_use(int(spec["fx"]), source="strips")
            return text_result(f"Applied per-strip looks: {result}")
        channels = args.get("channels") or []
        if not channels:
            raise ValueError("strips requires channels or assignments.")
        fx = int(args.get("fx") or 9)
        _check_fx_allowed(client, ",".join(str(ch) for ch in channels), fx)
        opts = {}
        if args.get("speed") is not None:
            opts["sx"] = args["speed"]
        if args.get("intensity") is not None:
            opts["ix"] = args["intensity"]
        result = columns.apply_channels(client, list(channels), fx, args.get("pal"), **opts)
        lightctl.record_fx_use(fx, source="strips")
        return text_result(f"Applied look to {', '.join(channels)}: {result}")
    if name == "atmosphere":
        if not _is_fleet(client):
            raise ValueError("atmosphere requires fleet mode (run without --host).")
        applied = atmospheres.apply_atmosphere(client, args['name'])
        definition = atmospheres.ATMOSPHERES.get(str(args["name"]), {})
        for _func, step_kwargs in definition.get("steps", []):
            if isinstance(step_kwargs, dict) and step_kwargs.get("fx") is not None:
                lightctl.record_fx_use(step_kwargs["fx"], source=f"atmosphere:{args['name']}")
        return text_result(f"Applied atmosphere: {applied}")
    if name == "dynamic_scene":
        if not _is_fleet(client):
            raise ValueError("dynamic_scene requires fleet mode (run without --host).")
        scene_args = dict(args)
        scene_args.pop("target", None)  # dynamic scenes are wall-wide/topology-aware.
        result = dynamic_scenes.apply_dynamic_scene(client, **scene_args)
        return text_result(f"Applied dynamic scene: {result}")
    if name == "look_feedback":
        look_id = look_memory.add_feedback(
            look_id=args.get("look_id"),
            score=args.get("score"),
            tags=args.get("tags"),
            notes=str(args.get("notes") or ""),
            applies_to=str(args.get("applies_to") or "last"),
        )
        return text_result(f"Recorded feedback for look {look_id or 'none'}.")
    if name == "look_memory_summary":
        return text_result(look_memory.memory_summary(int(args.get("limit") or 8)))
    if name == "realtime_start":
        if not _is_fleet(client):
            raise ValueError("realtime_start requires fleet mode (run without --host).")
        scene_args = dict(args); scene_args.pop("target", None)
        return text_result(realtime.realtime_start(client, **scene_args))
    if name == "realtime_stop":
        return text_result(realtime.realtime_stop())
    if name == "realtime_status":
        return text_result(json.dumps(realtime.realtime_status(), indent=2))
    if name == "design_look":
        if not _is_fleet(client):
            raise ValueError("design_look requires fleet mode (run without --host).")
        scene_args = dict(args)
        scene_args.pop("target", None)
        try:
            import light_gui
            settings = light_gui.ai_settings()
        except Exception:
            settings = None
        design_kwargs: dict[str, Any] = {"settings": settings}
        for key in ("mood", "energy", "motion", "seed", "colors", "composition_mode", "intensity", "shader"):
            if scene_args.get(key) is not None:
                design_kwargs[key] = scene_args[key]
        look = look_agents.design_look(
            str(scene_args.get("prompt") or ""),
            **_kwargs_for(look_agents.design_look, design_kwargs),
        )
        started = False
        apply_result = ""
        if scene_args.get("run"):
            apply_kwargs: dict[str, Any] = {}
            if scene_args.get("fps") is not None:
                apply_kwargs["fps"] = scene_args["fps"]
            if scene_args.get("duration_s") is not None:
                apply_kwargs["duration_s"] = scene_args["duration_s"]
            apply_result = str(
                look_agents.apply_look(client, look, **_kwargs_for(look_agents.apply_look, apply_kwargs)) or ""
            )
            started = True
        agents = look.get("agents") or {}
        if isinstance(agents, dict):
            ran = [f"{name}={value}" for name, value in agents.items() if value]
            agents_text = ", ".join(ran) if ran else "none (color_lab fallback)"
        else:
            agents_text = str(agents)
        status = "Realtime started" if started else "Realtime not started"
        if apply_result:
            status = f"{status}: {apply_result}"
        return text_result(
            f"Look recipe: shader={look.get('shader')}, colors={look.get('colors')}, "
            f"agents={agents_text}. {status}."
        )
    if name == "set_zone":
        return text_result(_set_zone(client, args))
    if name == "set_segment_bounds":
        return text_result(_set_segment_bounds(client, args))
    if name == "delete_segment":
        seg_id = int(args["id"])
        payload = lightctl.segment_payload([{"id": seg_id, "stop": 0}])
        result = _post_state(client, payload, str(args["target"]))
        return text_result(_with_fleet_status(f"Deleted segment {seg_id} (stop=0).", result))
    if name == "set_leds":
        payload = lightctl.leds_payload(
            args["leds"], seg_id=int(segment) if segment is not None else None
        )
        # WLED ignores per-LED colors when the light is off and 'on' rides in
        # the same request (JSON API docs: brightness/on must be set first).
        _post_state(client, lightctl.on_payload(True), target)
        result = _post_state(client, payload, target)
        return text_result(
            _with_fleet_status("Set individual LEDs (running effect frozen until a segment property changes).", result)
        )
    if name == "start_show":
        if not _is_fleet(client):
            raise ValueError("start_show requires fleet mode (run without --host).")
        return text_result(shows.start_show(client, args["show"]))
    if name == "stop_show":
        return text_result(shows.stop_show())
    if name == "show_status":
        return text_result(json.dumps(shows.show_status(), indent=2))
    if name == "music_show":
        import smart_director

        result = _compact_music_show_result(smart_director.control_show(client, args))
        return text_result(json.dumps(result, separators=(",", ":")))
    if name == "wled_read":
        return text_result(_wled_read(client, args))
    if name == "wled_write":
        return text_result(_wled_write(client, args))
    if name == "tv_status":
        import firetv

        try:
            return text_result(json.dumps(firetv.status(), indent=2))
        except Exception as exc:
            return text_result(f"Fire TV status failed: {exc}")
    if name == "tv_wake":
        return _tv_action(lambda tv: tv.wake(), "Woke the Fire TV screen.")
    if name == "tv_sleep":
        return _tv_action(lambda tv: tv.sleep(), "Put the Fire TV screen to sleep.")
    if name == "tv_open_url":
        url = str(args["url"])
        return _tv_action(lambda tv: tv.open_url(url), f"Opened {url} on the Fire TV.")
    if name == "calibrate":
        import calibrate as calibrate_mod

        updates: dict[str, Any] = {}
        if args.get("pixel_zero"):
            updates["pixel_zero"] = str(args["pixel_zero"])
        if args.get("orientation"):
            updates["orientation"] = str(args["orientation"])
        cal_kwargs: dict[str, Any] = {
            "assignments": args.get("assignments"),
            "installation_updates": updates or None,
            "write": bool(args.get("write")),
        }
        if _is_fleet(client):
            cal_kwargs["clients"] = {ctrl: client.clients[ctrl] for ctrl in client.names()}
            cal_kwargs["topology"] = (client.installation, client.controllers)
        report = calibrate_mod.calibrate(**cal_kwargs)
        return text_result(json.dumps(report, indent=2, default=str))
    if name == "identify":
        import calibrate as calibrate_mod

        flashes = int(args.get("flashes") or 4)
        return text_result(calibrate_mod.identify(client, target=target, flashes=flashes))
    if name == "music_director":
        if not _is_fleet(client):
            raise ValueError("music_director requires fleet mode (run without --host).")
        try:
            import music_director  # lazy: module is built in parallel and may be missing
        except Exception as exc:
            return text_result(f"Music Director unavailable: {exc}")
        action = str(args.get("action", "")).strip().lower()
        try:
            if action == "start":
                return text_result(str(music_director.start_director(client)))
            if action == "stop":
                return text_result(str(music_director.stop_director()))
            if action == "status":
                return text_result(json.dumps(music_director.director_status(), indent=2))
            return text_result("action must be start, stop, or status.")
        except Exception as exc:
            return text_result(f"Music Director failed: {exc}")
    raise ValueError(f"Unknown tool: {name}")


class McpServer:
    def __init__(self, client: lightctl.LightClient | fleet.LightFleet) -> None:
        self.client = client
        self.modes = lightctl.ReactiveThread(client)
        atexit.register(self._cleanup)

    def _cleanup(self) -> None:
        self.modes.stop()
        _stop_timers()

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
                params = message.get("params") or {}
                result = call_tool(
                    self.client,
                    str(params.get("name", "")),
                    params.get("arguments") or {},
                    self.modes,
                )
            elif request_id is None:
                return None  # JSON-RPC notifications must not be answered
            else:
                return self.error(request_id, -32601, f"Method not found: {method}")
            if request_id is None:
                return None  # notification: no response
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            logger.exception("Error handling MCP request")
            if request_id is None:
                return None  # notification: no response
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
    parser.add_argument(
        "--host",
        default=os.environ.get("LIGHT_HOST"),
        help="Force a single controller (back-compat escape hatch); default: fleet from config/LIGHT_HOSTS",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    client: lightctl.LightClient | fleet.LightFleet
    if args.host:
        client = StderrDryRunClient(args.host) if args.dry_run else lightctl.LightClient(args.host)
        try:
            seed_effect_catalog(client.host, client.get_effects(), client.get_fxdata())
        except Exception:
            pass  # offline: SAFE_EFFECTS fallback stays in effect
    else:
        client = fleet.LightFleet.from_config(dry_run=args.dry_run)
        for controller in client.names():
            try:
                ctrl_client = client.clients[controller]
                seed_effect_catalog(controller, ctrl_client.get_effects(), ctrl_client.get_fxdata())
            except Exception:
                pass  # offline: SAFE_EFFECTS fallback stays in effect
    McpServer(client).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
