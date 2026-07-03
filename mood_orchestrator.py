from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

import lightctl

_CONFIG_DIR = os.path.expanduser("~/.config/lightss")
_SONG_CACHE_PATH = os.path.join(_CONFIG_DIR, "song_moods.json")


class SongCache:
    """Persistent cache of song key -> last WLED mood payload."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or _SONG_CACHE_PATH
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def set(self, key: str, payload: dict) -> None:
        self._data[key] = payload
        self._save()


class TransitionSmoother:
    """Make WLED payload changes gentle and non-jarring."""

    MIN_TRANSITION_MS = 1200
    AMBIENT_TRANSITION_MS = 4000
    AMBIENT_TO_MOOD_MS = 2500
    BRIGHTNESS_JUMP_THRESHOLD = 80
    INTERMEDIATE_STEP_MS = 800

    def smooth(
        self,
        payload: lightctl.WledPayload,
        current_state: dict[str, Any] | None,
        source: str,
    ) -> list[lightctl.WledPayload]:
        current_state = current_state or {}
        current_bri = current_state.get("bri", 128)
        target_bri = payload.get("bri", current_bri)

        if source == "ambient":
            min_tt = self.AMBIENT_TRANSITION_MS
        elif source == "ambient_to_mood":
            min_tt = self.AMBIENT_TO_MOOD_MS
        else:
            min_tt = self.MIN_TRANSITION_MS

        tt = payload.get("tt", 0)
        if tt < min_tt:
            payload = dict(payload)
            payload["tt"] = min_tt

        # If brightness jump is too large, split into two posts.
        if abs(target_bri - current_bri) > 2 * self.BRIGHTNESS_JUMP_THRESHOLD:
            direction = 1 if target_bri > current_bri else -1
            intermediate_bri = current_bri + direction * self.BRIGHTNESS_JUMP_THRESHOLD
            intermediate = dict(payload)
            intermediate["bri"] = lightctl.clamp_byte(intermediate_bri)
            intermediate["tt"] = self.INTERMEDIATE_STEP_MS
            final = dict(payload)
            final["bri"] = lightctl.clamp_byte(target_bri)
            return [intermediate, final]

        return [payload]


class AudioSampleBuffer:
    """Throttle incoming audio samples and route them to recognition."""

    def __init__(
        self,
        recognize_fn: Callable[[bytes], dict[str, str] | None],
        cooldown_seconds: float = 20.0,
    ) -> None:
        self.recognize_fn = recognize_fn
        self.cooldown_seconds = cooldown_seconds
        self._last_attempt: float = 0.0

    def maybe_recognize(self, audio_bytes: bytes) -> dict[str, str] | None:
        now = time.monotonic()
        if now - self._last_attempt < self.cooldown_seconds:
            return None
        self._last_attempt = now
        try:
            return self.recognize_fn(audio_bytes)
        except Exception:
            return None


class MoodSession:
    """Orchestrate continuous mic -> song -> mood -> WLED with ambient fallback."""

    STATE_IDLE = "idle"
    STATE_LISTENING = "listening"
    STATE_RECOGNIZED = "recognized"
    STATE_AMBIENT = "ambient"

    def __init__(
        self,
        client: lightctl.LightClient,
        recognize_fn: Callable[[bytes], dict[str, str] | None],
        generate_fn: Callable[[dict[str, str]], lightctl.WledPayload],
        cache: SongCache | None = None,
        smoother: TransitionSmoother | None = None,
        ambient_payload: lightctl.WledPayload | None = None,
        sample_duration: float = 5.0,
        recognize_cooldown: float = 20.0,
        ambient_timeout: float = 60.0,
    ) -> None:
        self.client = client
        self.recognize_fn = recognize_fn
        self.generate_fn = generate_fn
        self.cache = cache or SongCache()
        self.smoother = smoother or TransitionSmoother()
        self.ambient_payload = ambient_payload or lightctl.merge_payloads(
            lightctl.on_payload(True),
            lightctl.brightness_payload(60),
            lightctl.color_payload(*lightctl.kelvin_to_rgbw(2700)),
        )
        self.ambient_timeout = ambient_timeout
        self._buffer = AudioSampleBuffer(recognize_fn, cooldown_seconds=recognize_cooldown)
        self._state = self.STATE_IDLE
        self._lock = threading.Lock()
        self._current_song: dict[str, str] | None = None
        self._last_recognition_time: float = 0.0
        self._last_song_key: str = ""
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._state = self.STATE_LISTENING
            self._current_song = None
            self._last_recognition_time = time.monotonic()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._state = self.STATE_IDLE
            self._current_song = None

    def sample(self, audio_bytes: bytes) -> dict[str, Any]:
        with self._lock:
            if not self._running:
                return self.status()

        result = self._buffer.maybe_recognize(audio_bytes)

        if result:
            return self._handle_recognition(result)

        self._check_ambient_timeout()
        return self.status()

    def _handle_recognition(self, song: dict[str, str]) -> dict[str, Any]:
        key = self._song_key(song)
        with self._lock:
            self._last_recognition_time = time.monotonic()
            changed = key != self._last_song_key

        if not changed:
            return self.status()

        cached = self.cache.get(key)
        if cached:
            payload = dict(cached)
        else:
            payload = self.generate_fn(song)
            self.cache.set(key, dict(payload))

        source = "ambient_to_mood" if self._state == self.STATE_AMBIENT else "recognized"
        self._apply_payload(payload, source)

        with self._lock:
            self._state = self.STATE_RECOGNIZED
            self._current_song = song
            self._last_song_key = key

        return self.status()

    def _check_ambient_timeout(self) -> None:
        with self._lock:
            if self._state not in (self.STATE_LISTENING, self.STATE_RECOGNIZED):
                return
            if time.monotonic() - self._last_recognition_time < self.ambient_timeout:
                return
            self._state = self.STATE_AMBIENT
            self._current_song = None

        self._apply_payload(self.ambient_payload, "ambient")

    def _apply_payload(
        self,
        payload: lightctl.WledPayload,
        source: str,
    ) -> None:
        try:
            current_state = self.client.get_state()
        except Exception:
            current_state = None
        for smoothed in self.smoother.smooth(payload, current_state, source):
            try:
                self.client.post_state(smoothed)
            except Exception:
                # Don't let a transient WLED error crash the session.
                pass

    def _song_key(self, song: dict[str, str]) -> str:
        parts = [
            str(song.get("artist", "")).strip().lower(),
            str(song.get("title", "")).strip().lower(),
            str(song.get("album", "")).strip().lower(),
        ]
        return "||".join(parts)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "state": self._state,
                "song": self._current_song,
                "last_recognition_time": self._last_recognition_time,
            }
