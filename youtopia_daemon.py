#!/usr/bin/env python3
"""Persistent bridge daemon between Youtopia and lightss.

Youtopia spawns this process once while the Lightss integration is enabled and
streams JSON-line commands via stdin:

    {"type": "host", "host": "http://10.27.27.110"}
    {"type": "song",  "song": {"title": "...", "artist": "...", ...}}
    {"type": "audio", "data": [0, 255, 12, ...]}
    {"type": "stop"}
    {"type": "quit"}

* "host" configures the WLED controller address.
* "song" asks the lightss AI for a scene based on rich metadata and applies it.
* "audio" feeds Youtopia's own VU-meter / frequency-bin data into a beat detector
  for audio-reactive lighting (no microphone required).
* "stop" pauses reactive beat handling while keeping the connection open.
* "quit" terminates the daemon cleanly.
"""

from __future__ import annotations

import json
import logging
import math
import sys
import threading
import time
from typing import Any

# Allow running against a lightss checkout anywhere on disk by accepting the
# lightss directory as the first positional argument.
_LIGHTSS_DIR = sys.argv[1] if len(sys.argv) > 1 else "/home/rucaradio/lightss"
sys.path.insert(0, _LIGHTSS_DIR)

import lightctl  # noqa: E402
import light_gui  # noqa: E402

logger = logging.getLogger("youtopia_daemon")
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _video_type_name(video_type: int | str | None) -> str:
    """Map Youtopia VideoType enum values to human-readable names."""
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
    """Map Youtopia LikeStatus enum values to human-readable names."""
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


def build_song_prompt(song: dict[str, Any]) -> str:
    """Build an informative lighting prompt from all available Youtopia metadata.

    The AI is asked to infer missing musical context (genre, mood, BPM, energy)
    from the metadata it does have, so the resulting scene matches the song as
    closely as possible.
    """
    title = song.get("title", "Unknown")
    artist = song.get("artist", "Unknown")
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


def build_now_playing(song: dict[str, Any]) -> dict[str, str]:
    """Build a now_playing dict compatible with light_gui helpers."""
    return {
        "title": str(song.get("title", "Unknown")),
        "artist": str(song.get("artist", "Unknown")),
        "album": str(song.get("album", "")),
        "genre": str(song.get("genre", "")),
        "status": str(song.get("status", "")),
    }


# ---------------------------------------------------------------------------
# Reactive audio handling
# ---------------------------------------------------------------------------

class VUReactiveMode:
    """Audio-reactive lighting driven by Youtopia's frequency-bin data.

    The incoming data is an array of 0-255 values from the player's analyser.
    We convert that into a normalized energy value and run it through the same
    adaptive beat detector used by lightctl's microphone mode.
    """

    def __init__(self, client: lightctl.LightClient) -> None:
        self.client = client
        self.beat_detector = lightctl.BeatDetector(threshold=1.3, floor=0.01)
        self.reactive_mode = lightctl.ReactiveMode(client, min_interval=0.14)
        self._last_energy = 0.0

    def reset(self) -> None:
        self.beat_detector = lightctl.BeatDetector(threshold=1.3, floor=0.01)
        self._last_energy = 0.0

    def handle_frame(self, data: list[int | float]) -> None:
        if not data:
            return
        # Normalize the frequency-bin energy to a 0..1 range.
        total = sum(max(0, float(v)) for v in data)
        energy = min(1.0, total / (len(data) * 255.0))
        self._last_energy = energy
        beat = self.beat_detector.update(energy)
        if beat:
            try:
                self.reactive_mode.handle_beat(energy)
            except Exception as exc:
                # WLED may be rebooting or temporarily unreachable; stay alive.
                logger.warning("Reactive beat send failed: %s", exc)


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class YoutopiaDaemon:
    def __init__(self) -> None:
        self.host: str | None = None
        self.client: lightctl.LightClient | None = None
        self.reactive: VUReactiveMode | None = None
        self._lock = threading.Lock()

    def _ensure_client(self, host: str) -> lightctl.LightClient:
        with self._lock:
            if self.client is None or host != self.host:
                self.host = host
                self.client = lightctl.LightClient(host=host)
                self.reactive = VUReactiveMode(self.client)
                logger.info("Connected to WLED at %s", host)
            return self.client

    def handle_host(self, payload: dict[str, Any]) -> None:
        host = payload.get("host") or lightctl.DEFAULT_HOST
        self._ensure_client(str(host))

    def handle_song(self, payload: dict[str, Any]) -> None:
        song = payload.get("song", {})
        host = song.get("host") or payload.get("host") or lightctl.DEFAULT_HOST
        client = self._ensure_client(str(host))

        prompt = build_song_prompt(song)
        now_playing = build_now_playing(song)

        try:
            snapshot = client.get_device_snapshot()
        except Exception as exc:
            logger.warning("Could not fetch WLED snapshot: %s", exc)
            snapshot = None

        logger.info("Applying AI scene for %s - %s", now_playing["artist"], now_playing["title"])
        try:
            plan = light_gui.call_openai_for_plan(prompt, now_playing, snapshot)
            result = light_gui.apply_ai_plan(client, plan)
            self._emit({"ok": True, "message": result["message"], "response": result.get("response", "")})
        except Exception as exc:
            logger.exception("Failed to apply song scene")
            self._emit({"ok": False, "error": str(exc)})

    def handle_audio(self, payload: dict[str, Any]) -> None:
        if self.reactive is None:
            return
        data = payload.get("data", [])
        if not isinstance(data, list):
            return
        self.reactive.handle_frame(data)

    def handle_stop(self, _payload: dict[str, Any]) -> None:
        if self.reactive is not None:
            self.reactive.reset()
            logger.info("Reactive beat detector reset")

    def _emit(self, message: dict[str, Any]) -> None:
        try:
            print(json.dumps(message))
            sys.stdout.flush()
        except Exception:
            logger.exception("Failed to emit daemon message")

    def run(self) -> None:
        logger.info("Youtopia lightss daemon started")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Malformed JSON on stdin: %s", exc)
                continue

            cmd = msg.get("type")
            if cmd == "host":
                self.handle_host(msg)
            elif cmd == "song":
                self.handle_song(msg)
            elif cmd == "audio":
                self.handle_audio(msg)
            elif cmd == "stop":
                self.handle_stop(msg)
            elif cmd == "quit":
                break
            else:
                logger.warning("Unknown command type: %s", cmd)

        logger.info("Youtopia lightss daemon shutting down")


if __name__ == "__main__":
    YoutopiaDaemon().run()
