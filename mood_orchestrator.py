from __future__ import annotations

import io
import json
import logging
import math
import os
import struct
import threading
import time
import wave
from typing import TYPE_CHECKING, Any, Callable

import lightctl
import song_tracking

if TYPE_CHECKING:
    import fleet

_LOGGER = logging.getLogger(__name__)

_CONFIG_DIR = os.path.expanduser("~/.config/lightss")


def _song_cache_path() -> str:
    return os.path.join(_CONFIG_DIR, "song_moods.json")


def _extract_state_dict(raw: Any) -> dict[str, Any] | None:
    """Normalize ``get_state()`` from a LightClient or a LightFleet.

    A LightClient returns a single WLED state dict; a LightFleet returns
    ``{controller_name: {"ok": ..., "response"/"state": state}}``. Pick one
    representative state dict for transition smoothing, or None.
    """
    if not isinstance(raw, dict):
        return None
    if "bri" in raw or "seg" in raw or "on" in raw:
        return raw
    for value in raw.values():
        if not isinstance(value, dict):
            continue
        inner = value.get("response") or value.get("state") or value
        if isinstance(inner, dict) and ("bri" in inner or "seg" in inner or "on" in inner):
            return inner
    return None


class SongCache:
    """Persistent cache of song key -> last WLED mood payload."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or _song_cache_path()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            pass
        except json.JSONDecodeError as exc:
            _LOGGER.warning("Song cache file %s is corrupt (%s); resetting.", self.path, exc)
        return {}

    def _save(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:  # a bare filename has no parent to create
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def set(self, key: str, payload: dict) -> None:
        self._data[key] = payload
        self._save()


class TransitionSmoother:
    """Make WLED payload changes gentle and non-jarring.

    Durations are expressed in milliseconds here, but WLED's 'tt' field is in
    100ms units (0-65535, per-call only — see the JSON API docs), so values
    are converted when written. Payloads passing an existing 'tt' are compared
    in the same units.
    """

    MIN_TRANSITION_MS = 1200
    AMBIENT_TRANSITION_MS = 4000
    AMBIENT_TO_MOOD_MS = 2500
    BRIGHTNESS_JUMP_THRESHOLD = 80
    INTERMEDIATE_STEP_MS = 800

    @staticmethod
    def _tt_units(milliseconds: float) -> int:
        """Milliseconds -> WLED 'tt' units (100ms each, 0-65535)."""
        return max(0, min(65535, round(milliseconds / 100)))

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

        min_units = self._tt_units(min_tt)
        tt = payload.get("tt", 0)
        if tt < min_units:
            payload = dict(payload)
            payload["tt"] = min_units

        # If brightness jump is too large, split into two posts.
        if abs(target_bri - current_bri) > self.BRIGHTNESS_JUMP_THRESHOLD:
            direction = 1 if target_bri > current_bri else -1
            intermediate_bri = current_bri + direction * self.BRIGHTNESS_JUMP_THRESHOLD
            intermediate = dict(payload)
            intermediate["bri"] = lightctl.clamp_byte(intermediate_bri)
            intermediate["tt"] = self._tt_units(self.INTERMEDIATE_STEP_MS)
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
        rms_threshold: float | None = None,
        change_detection: bool = False,
    ) -> None:
        self.recognize_fn = recognize_fn
        self.cooldown_seconds = cooldown_seconds
        self.rms_threshold = rms_threshold
        self._last_attempt: float = 0.0
        self._lock = threading.Lock()
        self._rms_width_warned = False
        self.change_tracker = (
            song_tracking.ChangeAwareRecognizer(recognize_fn, cooldown_seconds)
            if change_detection else None
        )

    def maybe_recognize(self, audio_bytes: bytes) -> dict[str, str] | None:
        if self.change_tracker is not None:
            return self.change_tracker.recognize(audio_bytes)
        if self.rms_threshold is not None:
            rms = self._rms(audio_bytes)
            if rms < self.rms_threshold:
                _LOGGER.debug(
                    "Audio sample RMS %.4f below threshold %.4f; skipping.",
                    rms,
                    self.rms_threshold,
                )
                return None

        with self._lock:
            now = time.monotonic()
            if now - self._last_attempt < self.cooldown_seconds:
                return None
            self._last_attempt = now

        try:
            return self.recognize_fn(audio_bytes)
        except Exception:
            _LOGGER.exception("Recognizer failed")
            return None

    def next_available_in(self) -> float:
        with self._lock:
            remaining = self.cooldown_seconds - (time.monotonic() - self._last_attempt)
        return max(0.0, remaining)

    def _rms(self, audio_bytes: bytes) -> float:
        """Compute RMS of a WAV audio payload, normalized to [0, 1]."""
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                nframes = wf.getnframes()
                if nframes == 0:
                    return 0.0
                sampwidth = wf.getsampwidth()
                frames = wf.readframes(nframes)
        except Exception:
            return 0.0

        if sampwidth == 1:
            samples = [(b - 128) / 128.0 for b in frames]
        elif sampwidth == 2:
            fmt = f"<{len(frames) // 2}h"
            samples = [s / 32768.0 for s in struct.unpack(fmt, frames)]
        else:
            if not self._rms_width_warned:
                self._rms_width_warned = True
                _LOGGER.warning(
                    "Unsupported WAV sample width %d bytes; RMS gating treats it as silence.",
                    sampwidth,
                )
            return 0.0

        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples))


class MoodSession:
    """Orchestrate continuous mic -> song -> mood -> WLED with ambient fallback.

    ``client`` may be a ``lightctl.LightClient`` (single controller) or a
    ``fleet.LightFleet`` (duck-typed ``post_state(payload)`` / ``get_state()``;
    the fleet's ``target`` defaults to ``"all"``, so it is a drop-in).
    """

    STATE_IDLE = "idle"
    STATE_LISTENING = "listening"
    STATE_RECOGNIZED = "recognized"
    STATE_AMBIENT = "ambient"

    def __init__(
        self,
        client: lightctl.LightClient | fleet.LightFleet,
        recognize_fn: Callable[[bytes], dict[str, str] | None],
        generate_fn: Callable[[dict[str, str]], lightctl.WledPayload],
        cache: SongCache | None = None,
        smoother: TransitionSmoother | None = None,
        ambient_payload: lightctl.WledPayload | None = None,
        recognize_cooldown: float = 20.0,
        ambient_timeout: float = 60.0,
        change_detection: bool = False,
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
        self._buffer = AudioSampleBuffer(
            recognize_fn, cooldown_seconds=recognize_cooldown,
            change_detection=change_detection,
        )
        self._state = self.STATE_IDLE
        self._lock = threading.Lock()
        self._current_song: dict[str, str] | None = None
        self._last_recognition_time: float = 0.0
        self._last_song_key: str = ""
        self._last_payload: dict | None = None
        self._last_error: str = ""
        self._last_generation_error: str = ""
        self._generation_status: str | None = None
        self._last_cache_hit: bool | None = None
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._state = self.STATE_LISTENING
            self._current_song = None
            self._last_song_key = ""
            self._last_generation_error = ""
            self._generation_status = None
            self._last_recognition_time = time.monotonic()
            if self._buffer.change_tracker is not None:
                self._buffer.change_tracker.reset()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._state = self.STATE_IDLE
            self._current_song = None
            if self._buffer.change_tracker is not None:
                self._buffer.change_tracker.reset()

    def sample(self, audio_bytes: bytes) -> dict[str, Any]:
        with self._lock:
            running = self._running
        if not running:
            # status() takes the lock itself — never call it while held.
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
            cache_hit = True
            generation_status = "cache_hit"
            generation_error = ""
        else:
            try:
                payload = self.generate_fn(song)
            except Exception as exc:
                _LOGGER.warning("Mood generation failed for %r: %s; using ambient.", key, exc)
                payload = dict(self.ambient_payload)
                generation_status = "failed"
                generation_error = str(exc)[:1000]
            else:
                generation_status = "generated"
                generation_error = ""
                try:
                    self.cache.set(key, dict(payload))
                except Exception as exc:
                    _LOGGER.warning("Failed to persist song mood cache: %s", exc)
            cache_hit = False

        with self._lock:
            source = "ambient_to_mood" if self._state == self.STATE_AMBIENT else "recognized"
            self._state = self.STATE_RECOGNIZED
            self._current_song = song
            self._last_song_key = key
            self._last_cache_hit = cache_hit
            self._generation_status = generation_status
            self._last_generation_error = generation_error

        self._apply_payload(payload, source)

        return self.status()

    def _check_ambient_timeout(self) -> None:
        with self._lock:
            if self._state not in (self.STATE_LISTENING, self.STATE_RECOGNIZED):
                return
            last_activity = self._last_recognition_time
            if self._buffer.change_tracker is not None:
                last_activity = max(last_activity, self._buffer.change_tracker.last_audio_at or 0)
            if time.monotonic() - last_activity < self.ambient_timeout:
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
            current_state = _extract_state_dict(self.client.get_state())
        except Exception:
            current_state = None
        with self._lock:
            self._last_error = ""
        for smoothed in self.smoother.smooth(payload, current_state, source):
            with self._lock:
                self._last_payload = dict(smoothed)
            try:
                self.client.post_state(smoothed)
            except Exception as exc:
                # Don't let a transient WLED error crash the session.
                with self._lock:
                    self._last_error = str(exc)

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
                "last_payload": self._last_payload,
                "last_error": self._last_error,
                "last_generation_error": self._last_generation_error,
                "generation_status": self._generation_status,
                "last_cache_hit": self._last_cache_hit,
                "next_recognition_in": round(self._buffer.next_available_in(), 3),
                "recognition": self._buffer.change_tracker.status() if self._buffer.change_tracker else {"mode": "legacy"},
            }
