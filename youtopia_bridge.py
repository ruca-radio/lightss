#!/usr/bin/env python3
"""One-shot bridge from Youtopia player state to lightss AI-driven lighting.

Reads a JSON object from stdin with keys:
  - host: WLED controller URL (optional; forces a single-controller client,
          overriding the fleet)
  - target: fleet target (default: "all") — a controller name or channel name
  - song: dict with title, artist, album, genre, durationSeconds, videoType,
          isLive, likeStatus, volume and status

Writes a JSON object to stdout with keys:
  - ok: bool
  - message: str
  - response: str (AI text response)
  - error: str (if ok is False)
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import lightctl
import light_gui

logger = logging.getLogger("youtopia_bridge")
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


class TargetedFleet:
    """Duck-typed adapter binding a LightFleet to a fixed target.

    Exposes the LightClient-shaped surface the light_gui helpers use
    (post_state / get_state / get_device_snapshot) so an AI plan can be
    applied to a single controller or channel without special-casing. The
    resolve passthrough makes light_gui's duck-typed fleet detection
    (_is_fleet) recognize the adapter, so per-action targets are honored
    and primary_state unwraps the fleet-shaped get_state dict correctly.
    """

    def __init__(self, light_fleet: Any, target: str) -> None:
        self._fleet = light_fleet
        self._target = target

    def resolve(self, target: str) -> Any:
        """Passthrough so light_gui treats this adapter as a fleet."""
        return self._fleet.resolve(target)

    def post_state(self, payload: dict[str, Any], target: str | None = None) -> dict[str, Any]:
        # An explicit per-action target (other than the broadcast default)
        # overrides the bound target; otherwise post to the bound target.
        if not target or target == "all":
            target = self._target
        return self._fleet.post_state(payload, target=target)

    def get_state(self, target: str | None = None) -> dict[str, Any]:
        return self._fleet.get_state(target or self._target)

    def get_device_snapshot(self) -> dict[str, Any]:
        return self._fleet.get_fleet_snapshot()


def make_client(host: str | None, target: str = "all") -> Any:
    """Build the lighting client for a request.

    An explicit host forces a single-controller LightClient (back-compat
    escape hatch); otherwise a LightFleet is built from config and bound to
    the requested target ("all" broadcasts to every controller).
    """
    if host:
        return lightctl.LightClient(host=host)
    import fleet  # local import: fleet imports lightctl

    light_fleet = fleet.LightFleet.from_config()
    target = target or "all"
    if target != "all":
        # Raises ValueError early on an unknown target.
        light_fleet.resolve(target)
    return TargetedFleet(light_fleet, target)


def _video_type_name(video_type: int | str | None) -> str:
    mapping = {
        -1: "Unknown",
        0: "Music Audio",
        1: "Music Video",
        2: "Uploaded Music",
        3: "Podcast Episode",
    }
    try:
        return mapping.get(int(video_type), "Unknown")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(video_type) if video_type else "Unknown"


def _like_status_name(like_status: int | str | None) -> str:
    mapping = {
        -1: "Unknown",
        0: "Disliked",
        1: "Indifferent",
        2: "Liked",
    }
    try:
        return mapping.get(int(like_status), "Unknown")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(like_status) if like_status else "Unknown"


def build_prompt(song: dict[str, Any]) -> str:
    """Build an informative lighting prompt from all available Youtopia metadata.

    The AI is asked to infer missing musical context (genre, mood, BPM, energy)
    from the metadata it does have, so the resulting scene matches the song as
    closely as possible.
    """
    title = song.get("title", "Unknown") or "Unknown"
    artist = song.get("artist", "Unknown") or "Unknown"
    album = song.get("album", "")
    genre = song.get("genre", "")
    bpm = song.get("bpm", "")
    mood = song.get("mood", "")
    duration = song.get("durationSeconds")
    video_type = _video_type_name(song.get("videoType"))
    is_live = song.get("isLive", False)
    like_status = _like_status_name(song.get("likeStatus"))
    volume = song.get("volume")
    status = song.get("status", "")

    parts = [
        "Create a lighting scene for the currently playing track.",
        f"Title: {title}",
        f"Artist: {artist}",
    ]
    if album:
        parts.append(f"Album: {album}")
    if genre:
        parts.append(f"Genre: {genre}")
    if bpm:
        parts.append(f"BPM: {bpm}")
    if mood:
        parts.append(f"Mood: {mood}")
    if duration is not None:
        parts.append(f"Duration: {duration} seconds")
    parts.append(f"Video type: {video_type}")
    parts.append(f"Live performance: {'yes' if is_live else 'no'}")
    parts.append(f"User like status: {like_status}")
    if volume is not None:
        parts.append(f"Player volume: {volume}%")
    if status:
        parts.append(f"Player state: {status}")

    parts.append(
        "Use the title, artist, album and any other clues above to infer the "
        "genre, mood, energy level and approximate tempo/BPM if not provided. "
        "Pick colors, brightness, speed and an effect that visually match the "
        "song's feeling. Prefer smooth, atmospheric effects for calm or acoustic "
        "tracks; punchy, fast effects for high-energy electronic/rock/hip-hop; "
        "and warm colors for happy or intimate songs."
    )
    return "\n".join(parts)


def main() -> int:
    try:
        data: dict[str, Any] = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"Invalid JSON input: {exc}"}))
        return 1

    host = data.get("host")
    target = str(data.get("target") or "all")
    song = data.get("song") or {}

    if not song.get("title"):
        print(json.dumps({"ok": False, "error": "Missing song title"}))
        return 1

    try:
        client = make_client(str(host) if host else None, target)
        snapshot = client.get_device_snapshot()

        now_playing = {
            "title": str(song.get("title", "Unknown")),
            "artist": str(song.get("artist", "Unknown")),
            "album": str(song.get("album", "")),
            "genre": str(song.get("genre", "")),
            "status": str(song.get("status", "")),
        }

        prompt = build_prompt(song)
        plan = light_gui.call_openai_for_plan(prompt, now_playing, snapshot)
        result = light_gui.apply_ai_plan(client, plan)

        print(
            json.dumps(
                {
                    "ok": True,
                    "message": result.get("message", ""),
                    "response": result.get("response", ""),
                }
            )
        )
        return 0
    except Exception as exc:
        logger.exception("Failed to apply song lighting")
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
