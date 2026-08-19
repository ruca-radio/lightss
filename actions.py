"""Shared action metadata for Lightss control surfaces."""

from __future__ import annotations

import lightctl


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
    "music_match",
    "wall_span",
    "wall_mirror",
    "wall_chase",
    "wall_versus",
    "set_channel",
    "strips",
    "atmosphere",
    "dynamic_scene",
    "design_look",
    "look_feedback",
    "realtime_start",
    "realtime_stop",
    "realtime_status",
]

# Client actions produce no direct WLED payload from the AI path. The wall_*
# actions, set_channel, strips, atmosphere, dynamic_scene, and design_look are
# routed server-side through columns.py / atmospheres.py / dynamic_scenes.py /
# look_agents.py instead.
CLIENT_ACTIONS = {
    "mode1_start",
    "mode1_stop",
    "fade_off",
    "cycle_start",
    "cycle_stop",
    "sunrise_start",
    "sunrise_stop",
    "music_detect",
    "music_match",
    "wall_span",
    "wall_mirror",
    "wall_chase",
    "wall_versus",
    "set_channel",
    "strips",
    "atmosphere",
    "dynamic_scene",
    "design_look",
    "look_feedback",
    "realtime_start",
    "realtime_stop",
    "realtime_status",
}


def safe_effect_ids() -> list[int]:
    return list(lightctl.SAFE_EFFECTS)


def safe_effect_prompt() -> str:
    return ", ".join(f"{effect_id}={name}" for effect_id, name in lightctl.SAFE_EFFECTS.items())


def ai_action_names() -> list[str]:
    return list(AI_ACTIONS)
