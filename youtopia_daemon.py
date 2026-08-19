#!/usr/bin/env python3
"""Persistent bridge daemon between Youtopia and lightss.

Youtopia spawns this process once while the Lightss integration is enabled and
streams JSON-line commands via stdin:

    {"type": "host", "host": "http://10.27.27.110"}
    {"type": "target", "target": "left"}
    {"type": "song",  "song": {"title": "...", "artist": "...", ...}}
    {"type": "audio", "data": [0, 255, 12, ...]}
    {"type": "stop"}
    {"type": "quit"}

* "host" configures a single WLED controller address, overriding the fleet;
  omit "host" to broadcast to every controller in the fleet.
* "target" selects the fleet target ("all", a controller name or a channel
  name) applied to song scenes and reactive beats. A "target" key on "song"
  or "host" messages works too.
* "song" asks the lightss AI for a scene based on rich metadata and applies it.
* "audio" feeds Youtopia's own VU-meter / frequency-bin data into a beat detector
  for audio-reactive lighting (no microphone required).
* "stop" pauses reactive beat handling while keeping the connection open;
  the next "song" or "host" message resumes it.
* "quit" terminates the daemon cleanly.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from typing import Any

# Allow running against a lightss checkout anywhere on disk by accepting the
# lightss directory as the first positional argument.
_LIGHTSS_DIR = sys.argv[1] if len(sys.argv) > 1 else "/home/rucaradio/lightss"
sys.path.insert(0, _LIGHTSS_DIR)

import fleet  # noqa: E402
import lightctl  # noqa: E402
import light_gui  # noqa: E402
from youtopia_bridge import TargetedFleet, build_prompt  # noqa: E402

logger = logging.getLogger("youtopia_daemon")
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Prompt construction (shared helpers live in youtopia_bridge)
# ---------------------------------------------------------------------------

build_song_prompt = build_prompt


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

    def __init__(self, client: Any) -> None:
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
        # Normalize the frequency-bin energy to a 0..1 range, skipping any
        # bin values that cannot be coerced to a number.
        total = 0.0
        count = 0
        for value in data:
            try:
                total += max(0.0, float(value))
                count += 1
            except (TypeError, ValueError):
                logger.warning("Skipping non-numeric audio bin: %r", value)
        if not count:
            return
        energy = min(1.0, total / (count * 255.0))
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
        self.target: str = "all"
        self.client: Any = None  # lightctl.LightClient or fleet.LightFleet
        self.reactive: VUReactiveMode | None = None
        self._paused = False
        self._lock = threading.Lock()

    def _bind_reactive(self) -> None:
        """(Re)build the reactive mode against the current client/target."""
        client = self.client
        if client is None:
            self.reactive = None
            return
        if isinstance(client, fleet.LightFleet):
            client = TargetedFleet(client, self.target)
        self.reactive = VUReactiveMode(client)

    def _ensure_client(self, host: str | None = None) -> Any:
        with self._lock:
            if host:
                # Explicit host: single-controller client (back-compat override).
                if self.host != host or self.client is None:
                    self.host = host
                    self.client = lightctl.LightClient(host=host)
                    self._bind_reactive()
                    logger.info("Connected to WLED at %s", host)
            elif self.client is None:
                # No client yet: broadcast to the fleet. Keep any existing
                # host-override client, and only assign state after the fleet
                # is built so a config failure is retried on the next call.
                new_fleet = fleet.LightFleet.from_config()
                self.host = None
                self.client = new_fleet
                self._bind_reactive()
                logger.info("Connected to WLED fleet: %s", ", ".join(self.client.names()))
            return self.client

    def _set_target(self, target: str) -> None:
        with self._lock:
            if target == self.target:
                return
            if isinstance(self.client, fleet.LightFleet):
                try:
                    self.client.resolve(target)
                except ValueError as exc:
                    logger.warning("Ignoring unknown fleet target: %s", exc)
                    return
            self.target = target
            self._bind_reactive()
            logger.info("Fleet target set to %s", target)

    def handle_host(self, payload: dict[str, Any]) -> None:
        self._paused = False
        host = payload.get("host")
        target = payload.get("target")
        if target:
            self._set_target(str(target))
        self._ensure_client(str(host) if host else None)

    def handle_target(self, payload: dict[str, Any]) -> None:
        self._set_target(str(payload.get("target") or "all"))
        self._ensure_client()

    def handle_song(self, payload: dict[str, Any]) -> None:
        self._paused = False
        song = payload.get("song", {})
        host = song.get("host") or payload.get("host")
        target = str(song.get("target") or payload.get("target") or self.target)
        client = self._ensure_client(str(host) if host else None)

        prompt = build_song_prompt(song)
        now_playing = build_now_playing(song)

        apply_client = client
        if isinstance(client, fleet.LightFleet):
            apply_client = TargetedFleet(client, target)

        try:
            snapshot = apply_client.get_device_snapshot()
        except Exception as exc:
            logger.warning("Could not fetch WLED snapshot: %s", exc)
            snapshot = None

        logger.info("Applying AI scene for %s - %s", now_playing["artist"], now_playing["title"])
        try:
            plan = light_gui.call_openai_for_plan(prompt, now_playing, snapshot)
            result = light_gui.apply_ai_plan(apply_client, plan)
            self._emit({"ok": True, "message": result["message"], "response": result.get("response", "")})
        except Exception as exc:
            logger.exception("Failed to apply song scene")
            self._emit({"ok": False, "error": str(exc)})

    def handle_audio(self, payload: dict[str, Any]) -> None:
        if self._paused or self.reactive is None:
            return
        data = payload.get("data", [])
        if not isinstance(data, list):
            return
        self.reactive.handle_frame(data)

    def handle_stop(self, _payload: dict[str, Any]) -> None:
        self._paused = True
        if self.reactive is not None:
            self.reactive.reset()
            logger.info("Reactive beat handling paused and detector reset")

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
            if not isinstance(msg, dict):
                logger.warning("Ignoring non-object JSON on stdin: %r", msg)
                continue

            cmd = msg.get("type")
            try:
                if cmd == "host":
                    self.handle_host(msg)
                elif cmd == "target":
                    self.handle_target(msg)
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
            except Exception as exc:
                # A failing command (e.g. malformed fleet config) must not
                # kill the daemon; report it and keep serving stdin.
                logger.exception("Command %r failed", cmd)
                self._emit({"ok": False, "error": str(exc)})

        logger.info("Youtopia lightss daemon shutting down")


if __name__ == "__main__":
    YoutopiaDaemon().run()
