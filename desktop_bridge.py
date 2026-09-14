"""Bounded in-process contract between the local engine and desktop providers."""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from typing import Any, Callable
from urllib.parse import urlparse

SOURCES = frozenset({"youtube_music", "apple_music"})
COMMANDS = frozenset({"play", "pause", "toggle", "next", "previous", "seek", "volume", "search", "library", "play_id"})
_NO_DATA = frozenset({"play", "pause", "toggle", "next", "previous", "library"})
_KINDS = frozenset({"song", "album", "playlist", "artist"})
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,512}$")
_PUBLISH_FIELDS = frozenset({"playing", "ended", "position", "duration", "title", "artist", "album", "track_id", "url"})
_ORIGINS = {
    "youtube_music": frozenset({"music.youtube.com"}),
    "apple_music": frozenset({"music.apple.com"}),
}


def _finite(value: Any, *, minimum: float = 0.0, maximum: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        return None
    return result


def valid_provider_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def provider_url_allowed(source: str, value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 4096:
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.hostname in _ORIGINS.get(source, ()) and not parsed.username


class DesktopBridgeRegistry:
    def __init__(self, *, max_commands: int = 128, stale_after: float = 3.0, clock: Callable[[], float] = time.monotonic):
        if max_commands < 1 or stale_after <= 0:
            raise ValueError("bounds must be positive")
        self._commands: deque[dict[str, Any]] = deque()
        self._max_commands = max_commands
        self._stale_after = float(stale_after)
        self._clock = clock
        self._states: dict[str, dict[str, Any]] = {}
        self._closed = False
        self._desktop_active = False
        self._lock = threading.RLock()

    def active(self) -> bool:
        with self._lock:
            return not self._closed and self._desktop_active

    def activate(self) -> None:
        """Mark the desktop command consumer ready (desktop-internal lifecycle hook)."""
        with self._lock:
            if self._closed:
                self._closed = False
            self._desktop_active = True

    def snapshot(self, source: str) -> dict[str, Any]:
        if source not in SOURCES:
            return {"ok": False, "error": "unsupported provider source"}
        with self._lock:
            state = self._states.get(source)
            if state is None:
                return self._empty(source)
            result = dict(state)
            stale = result["observed_at"] is None or self._clock() - result["observed_at"] > self._stale_after
            result["stale"] = stale
            if stale:
                result.update(playing=False, ended=False, position=None, duration=None)
            return result

    def command(self, source: str, command: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        error, normalized = self._validate_command(source, command, data)
        if error:
            return {"ok": False, "error": error}
        with self._lock:
            if self._closed:
                return {"ok": False, "error": "desktop bridge is closed"}
            if len(self._commands) >= self._max_commands:
                return {"ok": False, "error": "command queue is full"}
            self._commands.append({"source": source, "command": command, "data": normalized})
        return {"ok": True, "queued": True, "source": source, "command": command}

    def publish(self, source: str, payload: dict[str, Any]) -> dict[str, Any]:
        if source not in SOURCES:
            return {"ok": False, "error": "unsupported provider source"}
        if not isinstance(payload, dict) or not set(payload).issubset(_PUBLISH_FIELDS):
            return {"ok": False, "error": "invalid provider observation fields"}
        normalized: dict[str, Any] = {}
        for field in ("playing", "ended"):
            if field in payload:
                if not isinstance(payload[field], bool):
                    return {"ok": False, "error": f"{field} must be boolean"}
                normalized[field] = payload[field]
        for field in ("position", "duration"):
            if field in payload:
                value = _finite(payload[field])
                if value is None:
                    return {"ok": False, "error": f"invalid {field}"}
                normalized[field] = value
        for field in ("title", "artist", "album"):
            if field in payload:
                value = payload[field]
                if not isinstance(value, str) or len(value) > 1024:
                    return {"ok": False, "error": f"invalid {field}"}
                normalized[field] = value
        if "track_id" in payload:
            if not valid_provider_id(payload["track_id"]):
                return {"ok": False, "error": "invalid track_id"}
            normalized["track_id"] = payload["track_id"]
        if "url" in payload:
            if not provider_url_allowed(source, payload["url"]):
                return {"ok": False, "error": "invalid provider url"}
            normalized["url"] = payload["url"]
        with self._lock:
            if self._closed:
                return {"ok": False, "error": "desktop bridge is closed"}
            previous = self._states.get(source, self._empty(source))
            if normalized.get("ended") and normalized.get("track_id") and not previous.get("ended"):
                previous["last_ended_track_id"] = normalized["track_id"]
                previous["ended_sequence"] = int(previous.get("ended_sequence") or 0) + 1
            previous.update(normalized, connected=True, stale=False, observed_at=self._clock())
            self._states[source] = previous
            return dict(previous)

    def reset(self, source: str) -> None:
        """Clear observations when application-owned navigation changes a provider page."""
        if source not in SOURCES:
            raise ValueError("unsupported provider source")
        with self._lock:
            self._states[source] = self._empty(source)

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            commands = list(self._commands)
            self._commands.clear()
            return commands

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._desktop_active = False
            self._commands.clear()
            self._states.clear()

    @staticmethod
    def _empty(source: str) -> dict[str, Any]:
        return {"source": source, "desktop": True, "connected": False, "stale": True, "playing": False, "ended": False,
                "last_ended_track_id": None, "ended_sequence": 0,
                "position": None, "duration": None, "title": None, "artist": None, "album": None,
                "track_id": None, "url": None, "observed_at": None}

    @staticmethod
    def _validate_command(source: str, command: str, data: dict[str, Any] | None) -> tuple[str | None, dict[str, Any]]:
        if source not in SOURCES:
            return "unsupported provider source", {}
        if command not in COMMANDS:
            return "unsupported provider command", {}
        value = {} if data is None else data
        if not isinstance(value, dict):
            return "command data must be an object", {}
        if command in _NO_DATA:
            return (None, {}) if not value else ("command does not accept data", {})
        if command == "seek" and set(value) == {"position"}:
            position = _finite(value["position"])
            return (None, {"position": position}) if position is not None else ("invalid seek position", {})
        if command == "volume" and set(value) == {"volume"}:
            volume = _finite(value["volume"], maximum=1.0)
            return (None, {"volume": volume}) if volume is not None else ("invalid volume", {})
        if command == "search" and set(value) == {"query"}:
            query = value["query"]
            if isinstance(query, str) and 0 < len(query.strip()) <= 512:
                return None, {"query": query.strip()}
            return "invalid search query", {}
        if command == "play_id" and set(value) == {"id", "kind"}:
            if valid_provider_id(value["id"]) and value["kind"] in _KINDS:
                return None, {"id": value["id"], "kind": value["kind"]}
            return "invalid provider item", {}
        return "invalid command data", {}


registry = DesktopBridgeRegistry()
