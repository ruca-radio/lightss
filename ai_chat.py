#!/usr/bin/env python3
"""Tool-calling AI chat for the WLED fleet.

Exposes the platform's functional API — the MCP tool surface (light_on,
set_color, set_effect, wall_mode, atmosphere, list_segments, …) — to any
OpenAI-compatible chat/completions provider as function definitions, then
executes the model's tool calls against the fleet until it produces a final
answer. This replaces prompt-engineered JSON plans with native function
calling: the model decides WHICH WLED operations to run, we run them.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

import lightctl
import mcp_light

MAX_TOOL_ROUNDS = 8

# Serialize provider calls: Moonshot (and friends) stall when several
# requests run concurrently, so the director classifier, chat loops, and
# match-lights all take turns here.
provider_lock = threading.Lock()

# Shared audio-reactive mode handle for start/stop_audio_reactive tool calls,
# created lazily on first use so plain chat stays dependency-light.
_modes: lightctl.ReactiveThread | None = None
_modes_lock = threading.Lock()


def _reactive_modes(client: Any) -> lightctl.ReactiveThread:
    global _modes
    with _modes_lock:
        if _modes is None:
            _modes = lightctl.ReactiveThread(client)
        return _modes
TOOL_CHAT_SYSTEM_PROMPT = """You are the AI lighting director for a wall of FOUR vertical LED bars
driven by TWO WLED controllers. Physical model:
- Physical order (left to right): far-left, middle-left, middle-right,
  far-right. Controller "left" drives far-left + middle-left; controller
  "right" drives middle-right + far-right. Each column is its own WLED
  segment and can run its own effect/palette/colors.
- Each bar is L-shaped: the bottom 1 m (LEDs 0-24) runs vertically up the
  wall, the top 1 m (LEDs 25-49) bends back along the angled roof. The bend
  at LED 25 is a compositional feature — think of it as the horizon between
  "wall zone" (bottom half) and "ceiling zone" (top half). Great shows use
  it: fire rises from the wall onto the ceiling, rain falls from the ceiling
  down the wall, the horizon glows while zones differ.
  50 individually addressable WS2811 IC LEDs per bar (≈4 cm resolution).
  By default LED 0 is at the BOTTOM and LED 49 at the roof's high end
  (config key led_orientation = "up"; if "down", it flips).

You control the lights by CALLING THE PROVIDED FUNCTIONS — never describe
changes without making the calls. Every tool accepts an optional "target":
"all" (default), a controller name ("left"/"right"), or a channel name
("far-left", "middle-left", "middle-right", "far-right"). Channel names are
also accepted as "Far Left" etc.

Capabilities:
- High-level looks: atmosphere (named curated looks), wall_mode
  (span/mirror/chase/versus across all four columns), and
  set_effect/set_color/set_brightness with target for per-column control.
- Zones: carve any column into sub-segments with explicit start/stop LED
  bounds — set_zone (named zones like top/middle/bottom half/third/quarter
  of one channel), set_segment_bounds (exact start/stop/grp/spc/of on a
  segment), delete_segment (remove a zone). Zones on the same column run
  different effects side by side along its height.
- Per-LED precision: set_leds writes exact RRGGBB colors per LED or per
  [start, stop, color] range on a segment. WARNING: writing LEDs freezes
  the running effect on that segment — it stays frozen until you change a
  segment property (effect, colors, bounds) again.
- Timed shows: start_show runs a multi-step light show — each step is a
  look (an atmosphere, a wall_mode with kwargs, or a raw payload plus
  optional target) plus duration_s (optional transition_s), with optional
  loop. Use stop_show to halt and show_status to check. Prefer a show
  whenever the request implies a sequence over time rather than one state.

Guidance:
- Gravity matters on vertical bars: fire/plasma should RISE, rain/waterfall
  should FALL. Effects that move the wrong way need rev=true (rev flips a
  column's direction); with default orientation "up", rising = rev=false,
  falling = rev=true. In wall_mode versus/mirror layouts keep the two
  sides physically plausible.
- The device snapshot in the user message contains the full effect catalog
  grouped by mood (♪ audio-reactive, [2D] matrix-style, 🚫 forbidden) and the
  palette list. Match effects to the requested vibe; pick palettes by name.
- NEVER use 🚫-marked effects (strobe/blink/flash/lightning/fireworks/sparkle
  — seizure risk). Every other catalog effect id is fair game.
- Effects with palette support ignore color slots; effects with color hints
  look best with primary + secondary colors set.
- When music is playing, match the mood: jazz/acoustic → warm slow flow;
  EDM/pop → vibrant fast chase; metal/dark → deep reds/purples slow pulse.
- Chain multiple tool calls when the request implies several changes.
- After acting, reply with a short, fun confirmation (1-3 sentences) of what
  you did — this text is shown in the UI marquee.

FireTV: the living-room TV can complement the lights. For big-screen
ambience, tv_wake it and tv_open_url the ambient visuals page (path /tv on
the GUI host) so it plays alongside the show; for pitch-dark scenes,
tv_sleep it. Check tv_status first when unsure of its state. These tools
error politely when the user has disabled TV control in the UI — respect
that: do not retry, and tell the user TV control is off.
"""


class ToolChatError(RuntimeError):
    """Provider/network failure during the tool-chat loop."""


_CORE_TOOL_NAMES = {
    "light_on", "light_off", "get_state", "get_info", "set_brightness",
    "set_color", "set_hex_color", "set_temperature", "set_effect",
    "set_scene", "list_scenes", "random_scene", "load_preset",
    "wall_mode", "atmosphere", "list_controllers", "list_segments",
}

_SPECIALIZED_TOOL_GROUPS = (
    (("zone", "segment", "top", "bottom", "middle", "half", "third", "quarter"),
     {"set_zone", "set_segment_bounds", "delete_segment"}),
    (("per-led", "per led", "individual led", "pixel", "exact led"), {"set_leds"}),
    (("show", "sequence", "loop", "step", "theatrical"),
     {"start_show", "stop_show", "show_status"}),
    (("tv", "fire tv", "firetv", "cast", "screen"),
     {"tv_status", "tv_wake", "tv_sleep", "tv_open_url"}),
    (("music", "song", "track", "beat", "audio", "director"),
     {"recognize_music", "match_lights_to_song", "start_audio_reactive",
      "stop_audio_reactive", "music_director"}),
    (("sunrise", "wake up", "wake-up", "fade", "timer"), {"start_sunrise", "fade_off"}),
    (("save scene", "delete scene", "save", "delete"), {"save_scene", "delete_scene"}),
    (("restart", "reboot"), {"restart_controller"}),
)


def chat_tools(user_prompt: str | None = None) -> list[dict]:
    """Return MCP tools in OpenAI function format.

    A real prompt gets the common lighting surface plus relevant specialized
    groups.  Keeping unrelated schemas out of each provider round materially
    reduces latency on reasoning models.  ``None`` retains the full surface
    for callers that need discovery or introspection.
    """
    allowed: set[str] | None = None
    if user_prompt is not None:
        lowered = user_prompt.lower()
        allowed = set(_CORE_TOOL_NAMES)
        for keywords, names in _SPECIALIZED_TOOL_GROUPS:
            if any(keyword in lowered for keyword in keywords):
                allowed.update(names)

    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
            },
        }
        for tool in mcp_light.build_tools()
        if allowed is None or tool["name"] in allowed
    ]


def _result_text(result: Any) -> str:
    """Flatten an MCP tool result to plain text."""
    if isinstance(result, dict):
        parts = [
            str(item.get("text", ""))
            for item in result.get("content", [])
            if isinstance(item, dict) and item.get("text")
        ]
        if parts:
            return "\n".join(parts)
    return str(result)


def _chat_round(url: str, headers: dict, body: dict, timeout: float) -> dict:
    def _once() -> dict:
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ToolChatError(f"AI provider HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ToolChatError(f"AI provider unreachable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ToolChatError(f"AI provider returned invalid JSON: {exc}") from exc

    with provider_lock:
        try:
            return _once()
        except ToolChatError as first_exc:
            if "unreachable" not in str(first_exc):
                raise
            # One retry — providers occasionally stall a single connection.
            return _once()


def run_chat(
    client: Any,
    user_prompt: str,
    *,
    system_prompt: str = TOOL_CHAT_SYSTEM_PROMPT,
    settings: dict,
    context_text: str | None = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Run the tool-calling loop. Returns {"text", "log", "rounds"}.

    settings: {"base_url", "model", "api_key_env"} — api key read from the
    named env var (absent = keyless provider).
    """
    import os

    base_url = str(settings.get("base_url") or "").strip()
    model = str(settings.get("model") or "").strip()
    if not base_url or not model:
        raise ToolChatError("AI settings incomplete: base_url and model are required.")

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get(str(settings.get("api_key_env") or "").lstrip("$"), "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    user_content = user_prompt if not context_text else f"{context_text}\n\nUser request: {user_prompt}"
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    tools = chat_tools(user_prompt)
    log: list[str] = []

    for round_number in range(1, max_rounds + 1):
        body = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        data = _chat_round(url, headers, body, timeout)
        choices = data.get("choices") or []
        if not choices:
            raise ToolChatError("AI provider returned no choices.")
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            return {
                "text": (message.get("content") or "").strip(),
                "log": log,
                "rounds": round_number,
            }

        messages.append(message)
        for call in tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name", ""))
            try:
                args = json.loads(function.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            try:
                result = mcp_light.call_tool(client, name, args, _reactive_modes(client))
                content = _result_text(result)
            except Exception as exc:  # tool errors go back to the model, never crash the loop
                content = f"error: {exc}"
            log.append(f"{name}({json.dumps(args, default=str)})")
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": content[:2000]}
            )

    return {
        "text": "I made several lighting changes but ran out of steps — check the wall!",
        "log": log,
        "rounds": max_rounds,
    }
