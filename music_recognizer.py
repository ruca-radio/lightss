#!/usr/bin/env python3
"""Microphone-based music recognition via ShazamIO."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
from typing import Any

import numpy as np

logger = logging.getLogger("music_recognizer")

# Optional dependencies — gracefully degrade if unavailable
_try_import_errors: list[str] = []

_shazam_available = False
Shazam = None
AudioSegment = None

try:
    from pydub import AudioSegment as _AudioSegment
    AudioSegment = _AudioSegment
except Exception as exc:  # pragma: no cover
    _try_import_errors.append(f"pydub: {exc}")

try:
    from shazamio import Shazam as _Shazam
    Shazam = _Shazam
    _shazam_available = True
except Exception as exc:  # pragma: no cover
    _try_import_errors.append(f"shazamio: {exc}")

_sounddevice_available = False
try:
    import sounddevice as sd

    _sounddevice_available = True
except Exception as exc:  # pragma: no cover
    _try_import_errors.append(f"sounddevice: {exc}")

if _try_import_errors:
    logger.debug("music_recognizer optional deps unavailable: %s", _try_import_errors)

DEFAULT_DURATION = 5.0
DEFAULT_SAMPLE_RATE = 16000


def _record_audio(duration: float, sample_rate: int) -> np.ndarray:
    """Record audio from the default input device."""
    if not _sounddevice_available:
        raise RuntimeError("sounddevice is not available")
    logger.info("Recording %.1fs from microphone @ %d Hz...", duration, sample_rate)
    frames = int(duration * sample_rate)
    # Record as float32, then convert to int16
    recording = sd.rec(frames, samplerate=sample_rate, channels=1, dtype=np.float32)
    sd.wait()
    # Convert float32 [-1.0, 1.0] to int16
    int16_data = np.clip(recording * 32767, -32768, 32767).astype(np.int16)
    return int16_data


def _make_audio_segment(audio_data: np.ndarray, sample_rate: int) -> Any:
    """Wrap raw int16 mono PCM in a pydub AudioSegment."""
    if AudioSegment is None:
        raise RuntimeError("pydub is not available")
    raw_bytes = audio_data.tobytes()
    return AudioSegment(
        data=raw_bytes,
        sample_width=2,
        frame_rate=sample_rate,
        channels=1,
    )


def _parse_shazam_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract normalized fields from a ShazamIO response dict."""
    track = result.get("track")
    if not track:
        return None
    # ShazamIO 0.2.0.0 returns plain dicts, not dataclasses
    title = track.get("title") or track.get("heading", {}).get("title")
    artist = track.get("subtitle") or track.get("heading", {}).get("subtitle")
    album = None
    genre = None
    # Try genres.primary first
    genres_data = track.get("genres")
    if isinstance(genres_data, dict):
        genre = genres_data.get("primary")
    # Fallback to sections metadata
    sections = track.get("sections", [])
    for section in sections:
        if section.get("type") == "SONG":
            for meta in section.get("metadata", []):
                label = meta.get("title", "").lower()
                if label in ("album", "album:"):
                    album = meta.get("text")
                elif label in ("genre", "genre:") and not genre:
                    genre = meta.get("text")
    # Build normalized result
    parsed: dict[str, Any] = {
        "title": str(title).strip() if title else None,
        "artist": str(artist).strip() if artist else None,
        "album": str(album).strip() if album else None,
        "genre": str(genre).strip() if genre else None,
        "shazam_url": track.get("url") or track.get("share", {}).get("href"),
        "spotify_url": track.get("hub", {}).get("actions", [{}])[0].get("uri")
        if track.get("hub")
        else None,
        "youtube_url": None,
        "cover_url": track.get("images", {}).get("coverarthq")
        or track.get("images", {}).get("coverart"),
        "source": "shazam",
    }
    # Clean None values
    parsed = {k: v for k, v in parsed.items() if v is not None}
    if not parsed.get("title") and not parsed.get("artist"):
        return None
    return parsed


async def recognize_microphone(
    duration: float = DEFAULT_DURATION,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> dict[str, Any] | None:
    """Record audio from the microphone and recognize the song via Shazam.

    Returns a dict with keys like title, artist, album, genre, shazam_url,
    spotify_url, youtube_url, cover_url, source — or None if no match.
    """
    if not _shazam_available:
        raise RuntimeError("shazamio is not available")
    if not _sounddevice_available:
        raise RuntimeError("sounddevice is not available")
    if AudioSegment is None:
        raise RuntimeError("pydub is not available")

    audio_data = await asyncio.to_thread(_record_audio, duration, sample_rate)
    segment = _make_audio_segment(audio_data, sample_rate)

    shazam = Shazam()
    result = await shazam.recognize_song(segment)
    parsed = _parse_shazam_result(result)
    if parsed:
        logger.info("Recognized: %s — %s", parsed.get("artist"), parsed.get("title"))
    else:
        logger.info("No match from Shazam")
    return parsed


def recognize_sync(
    duration: float = DEFAULT_DURATION,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> dict[str, Any] | None:
    """Synchronous wrapper around :func:`recognize_microphone`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(recognize_microphone(duration, sample_rate))

    result: dict[str, Any] | None = None
    error: BaseException | None = None

    def run_in_thread() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(recognize_microphone(duration, sample_rate))
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


def is_available() -> bool:
    """Return True if all required dependencies are present."""
    return _shazam_available and _sounddevice_available and AudioSegment is not None


def available_reason() -> str:
    """Return a human-readable string explaining availability status."""
    if is_available():
        return "Music recognition is available."
    reasons = []
    if not _shazam_available:
        reasons.append("shazamio not installed")
    if not _sounddevice_available:
        reasons.append("sounddevice not installed")
    if AudioSegment is None:
        reasons.append("pydub not installed")
    return "Music recognition unavailable: " + ", ".join(reasons)
