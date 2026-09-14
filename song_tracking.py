"""Local audio change detection. No network or model calls without a change.

This is a conservative novelty detector, not a song fingerprint database:
section changes can resemble track changes, and seamless mixes can be missed.
"""
from __future__ import annotations

import io
import math
import threading
import time
import wave
from typing import Callable

import numpy as np


def audio_profile(audio: bytes) -> tuple[np.ndarray | None, float, float]:
    """Return gain-invariant spectral profile, RMS and duration for PCM WAV."""
    try:
        with wave.open(io.BytesIO(audio), 'rb') as wav:
            rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            if not 4000 <= rate <= 192000 or channels not in (1, 2) or width != 2:
                return None, 0.0, 0.0
            # Bound processing independently of the request-body limit.
            samples = np.frombuffer(wav.readframes(rate * 15), dtype='<i2').astype(np.float64)
        samples = samples.reshape(-1, channels).mean(axis=1) / 32768.0
        duration = len(samples) / rate
        if duration < 1:
            return None, 0.0, duration
        rms = float(np.sqrt(np.mean(samples ** 2)))
        if rms < 0.006:
            return None, rms, duration
        # Short-time spectra reduce sensitivity to phase and individual beats.
        size = 1 << round(np.log2(rate * 0.064))
        frames = samples[:len(samples) // size * size].reshape(-1, size)
        power = (np.abs(np.fft.rfft(frames * np.hanning(size))) ** 2).mean(axis=0)
        frequencies = np.fft.rfftfreq(size, 1 / rate)
        edges = np.geomspace(60, min(8000, rate / 2), 25)
        bands = np.array([power[(frequencies >= lo) & (frequencies < hi)].sum()
                          for lo, hi in zip(edges[:-1], edges[1:])])
        total = float(bands.sum())
        if not np.isfinite(total) or total <= 0:
            return None, rms, duration
        return np.sqrt(bands / total), rms, duration
    except (ValueError, EOFError, wave.Error, OSError):
        return None, 0.0, 0.0


class ChangeAwareRecognizer:
    """One initial query, then queries only on confirmed changes (two tries max).

    Local samples are still observed during the cooldown. A failed identification
    gets one delayed retry, not an endless timer loop. The lock also coalesces
    concurrent browser samples. A stable match is returned from memory.
    """

    def __init__(self, recognize: Callable, cooldown_seconds: float = 20.0):
        self._remote = recognize
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._generation = getattr(self, '_generation', 0) + 1
            self._inflight = False
            self.clock = PlaybackClock()
            self._baseline = None
            self._song = None
            self._novel_seconds = 0.0
            self._silent_seconds = 0.0
            self._pending = True
            self._attempts = 0
            self._last_query = None
            self._last_observed = None
            self.last_audio_at = None
            self._queries = 0
            self._reason = 'listening'
            self._last_error = ''

    def recognize(self, audio: bytes, *, now: float | None = None) -> dict | None:
        now = time.monotonic() if now is None else now
        profile, rms, duration = audio_profile(audio)
        with self._lock:
            if self._inflight:
                return dict(self._song) if self._song else None
            elapsed = duration if self._last_observed is None else min(duration, max(0, now - self._last_observed))
            self._last_observed = now
            if profile is None:
                self._reason = 'silent' if duration >= 1 and rms < 0.006 else 'invalid_audio'
                if self._reason == 'silent':
                    self._silent_seconds += elapsed
                    if self._silent_seconds >= 3:
                        self._baseline = None
                        self._song = None
                        self.clock = PlaybackClock()
                        self._pending = True
                        self._attempts = 0
                        self._novel_seconds = 0
                return None
            self.last_audio_at = now
            self._silent_seconds = 0
            if self._baseline is None:
                self._baseline = profile
            else:
                distance = float(np.linalg.norm(profile - self._baseline) / np.sqrt(2))
                if distance > 0.45:
                    self._novel_seconds += elapsed
                    self._reason = 'confirming_change'
                    if self._novel_seconds < 10:
                        return self._song
                    self._baseline = profile
                    self._song = None
                    self.clock = PlaybackClock()
                    self._pending = True
                    self._attempts = 0
                    self._novel_seconds = 0
                else:
                    self._novel_seconds = 0
                    # Slowly learn natural within-song changes, not volume shifts.
                    blended = .95 * self._baseline + .05 * profile
                    self._baseline = blended / np.linalg.norm(blended)
            if not self._pending:
                self._reason = 'waiting_for_change'
                return dict(self._song) if self._song else None
            if self._last_query is not None and now - self._last_query < self.cooldown_seconds:
                self._reason = 'retry_pending' if self._attempts else 'change_pending'
                return None
            self._last_query = now
            self._attempts += 1
            self._queries += 1
            self._reason = 'recognizing'
            self._inflight = True
            generation = self._generation
        error = ''
        try:
            result = self._remote(audio)
        except Exception as exc:
            result = None
            error = str(exc)
        with self._lock:
            if generation != self._generation:
                return None
            self._inflight = False
            self._last_error = error
            if isinstance(result, dict) and (result.get('title') or result.get('artist')):
                self._song = dict(result)
                self.clock.observe(self._song, now=now)
                self._pending = False
                self._reason = 'waiting_for_change'
            elif self._attempts >= 2:
                self._pending = False
                self._reason = 'waiting_for_change'
            else:
                self._reason = 'retry_pending'
            return dict(self._song) if self._song else None

    def status(self) -> dict:
        with self._lock:
            return {'mode': 'on_change', 'state': self._reason,
                    'queries': self._queries, 'last_error': self._last_error,
                    'clock': self.clock.snapshot()}


class PlaybackClock:
    """Observer clock independent of playback. Never invent a song offset.

    position_s is an explicitly reported anchor. observed_for_s is only time
    since identification, and is not a substitute for position within a song.
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._key = None
        self._position = None
        self._duration = None
        self._anchor = None
        self._first_seen = None
        self._playing = False
        self._title = None
        self._source = 'unknown'

    @staticmethod
    def _seconds(value):
        if isinstance(value, bool) or value is None:
            return None
        try:
            value = float(value)
        except (ValueError, TypeError):
            return None
        return value if math.isfinite(value) and value >= 0 else None

    def observe(self, song: dict, *, now: float | None = None):
        now = time.monotonic() if now is None else now
        key = tuple(str(song.get(k) or '') for k in ('source', 'artist', 'title', 'album'))
        with self._lock:
            if key != self._key:
                self._key = key
                self._first_seen = now
                self._position = None
                self._duration = None
                self._source = 'unknown'
                self._title = song.get('title')
            elif self._position is not None and self._playing and self._anchor is not None:
                self._position += max(0, now - self._anchor)
            position = self._seconds(song.get('position_s'))
            if position is not None:
                self._position = position
                self._source = 'reported_position'
            duration = self._seconds(song.get('duration_s'))
            if duration is not None:
                self._duration = duration
            self._playing = str(song.get('status', 'Playing')).lower() == 'playing'
            self._anchor = now

    def snapshot(self, *, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        with self._lock:
            position = self._position
            if position is not None and self._playing and self._anchor is not None:
                position += max(0, now - self._anchor)
            if position is not None and self._duration is not None:
                position = min(position, self._duration)
            return {'title': self._title, 'position_s': round(position, 3) if position is not None else None,
                    'duration_s': self._duration, 'playing': self._playing,
                    'updated_ago_s': max(0, now - self._anchor) if self._anchor is not None else None,
                    'observed_for_s': round(max(0, now - self._first_seen), 3) if self._first_seen is not None else 0,
                    'source': self._source}
