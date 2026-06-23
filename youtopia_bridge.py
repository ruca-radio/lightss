#!/usr/bin/env python3
"""One-shot bridge from Youtopia player state to lightss AI-driven lighting.

Reads a JSON object from stdin with keys:
  - host: WLED controller URL (default: lightctl.DEFAULT_HOST)
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
        f"Create a lighting scene for the currently playing track.",
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

    host = data.get("host") or lightctl.DEFAULT_HOST
    song = data.get("song") or {}

    if not song.get("title"):
        print(json.dumps({"ok": False, "error": "Missing song title"}))
        return 1

    try:
        client = lightctl.LightClient(host=host)
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
