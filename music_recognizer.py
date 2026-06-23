#!/usr/bin/env python3
"""Microphone-based music recognition via ShazamIO."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
from typing import Any

import numpy as np

import lightctl

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
DEFAULT_SAMPLE_RATE = 0  # 0 = auto-detect from device at runtime


def _get_device_samplerate(device: str | int | None = None) -> int:
    """Return the native sample rate of the given (or default) input device."""
    try:
        info = sd.query_devices(device=device, kind="input") if device is not None else sd.query_devices(kind="input")
        return int(info.get("default_samplerate", 44100))
    except Exception:
        return 44100


def _record_audio(duration: float, sample_rate: int, device: str | int | None = None) -> np.ndarray:
    """Record audio from the preferred input device.

    If the specific device is unavailable, this may raise; callers should treat
    recording failures as 'no match' rather than fatal errors.
    """
    if not _sounddevice_available:
        raise RuntimeError("sounddevice is not available")
    if device is None:
        device = lightctl.get_mic_device()

    # If a concrete device was selected but is not currently usable, fall back to default (None)
    if device is not None:
        try:
            info = sd.query_devices(device=device, kind="input")
            if not info or info.get("max_input_channels", 0) <= 0:
                logger.warning("Selected mic device %r is not a valid input; falling back to default", device)
                device = None
        except Exception:
            logger.warning("Selected mic device %r unavailable; falling back to default", device)
            device = None

    if sample_rate == 0:
        sample_rate = _get_device_samplerate(device)
    logger.info("Recording %.1fs from microphone @ %d Hz (device=%r)...", duration, sample_rate, device)
    frames = int(duration * sample_rate)
    try:
        # Record as float32, then convert to int16
        recording = sd.rec(frames, samplerate=sample_rate, channels=1, dtype=np.float32, device=device)
        sd.wait()
    except Exception as exc:
        # Let upper layers turn this into "no match" instead of crashing the process
        raise RuntimeError(f"Failed to open/record from audio device: {exc}") from exc
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
    device: str | int | None = None,
) -> dict[str, Any] | None:
    """Record audio from the microphone and recognize the song via Shazam.

    Returns a dict with keys like title, artist, album, genre, shazam_url,
    spotify_url, youtube_url, cover_url, source — or None if no match
    (including when no usable microphone device is available).
    """
    if not _shazam_available:
        raise RuntimeError("shazamio is not available")
    if not _sounddevice_available:
        raise RuntimeError("sounddevice is not available")
    if AudioSegment is None:
        raise RuntimeError("pydub is not available")

    try:
        if device is None:
            device = lightctl.get_mic_device()
        if sample_rate == 0:
            sample_rate = _get_device_samplerate(device)
        audio_data = await asyncio.to_thread(_record_audio, duration, sample_rate, device)
        segment = _make_audio_segment(audio_data, sample_rate)

        audio_buf = io.BytesIO()
        segment.export(audio_buf, format="wav")

        shazam = Shazam()
        result = await shazam.recognize_song(audio_buf.getvalue())
        parsed = _parse_shazam_result(result)
        if parsed:
            logger.info("Recognized: %s — %s", parsed.get("artist"), parsed.get("title"))
        else:
            logger.info("No match from Shazam")
        return parsed
    except Exception as exc:
        # Device unavailable, no mic, permission issues, PortAudio errors, etc.
        # Treat as "could not identify" rather than hard failure.
        logger.info("Microphone recording/identification failed: %s", exc)
        return None


async def recognize_audio_bytes(audio_bytes: bytes) -> dict[str, Any] | None:
    """Recognize song from provided audio bytes (WAV or other formats pydub can read).

    This allows using audio captured in the browser (webcam mic) and sent to the server.
    Returns None on any failure (bad audio, decode error, Shazam API issues, etc).
    """
    if not _shazam_available:
        raise RuntimeError("shazamio is not available")
    if AudioSegment is None:
        raise RuntimeError("pydub is not available")

    try:
        shazam = Shazam()
        result = await shazam.recognize_song(audio_bytes)
        parsed = _parse_shazam_result(result)
        if parsed:
            logger.info("Recognized: %s — %s", parsed.get("artist"), parsed.get("title"))
        else:
            logger.info("No match from Shazam")
        return parsed
    except Exception as exc:
        logger.info("Audio bytes identification failed: %s", exc)
        return None


def recognize_sync(
    duration: float = DEFAULT_DURATION,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    device: str | int | None = None,
) -> dict[str, Any] | None:
    """Synchronous wrapper around :func:`recognize_microphone`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(recognize_microphone(duration, sample_rate, device))

    result: dict[str, Any] | None = None
    error: BaseException | None = None

    def run_in_thread() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(recognize_microphone(duration, sample_rate, device))
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


def recognize_audio_bytes_sync(audio_bytes: bytes) -> dict[str, Any] | None:
    """Synchronous wrapper around :func:`recognize_audio_bytes`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(recognize_audio_bytes(audio_bytes))

    result: dict[str, Any] | None = None
    error: BaseException | None = None

    def run_in_thread() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(recognize_audio_bytes(audio_bytes))
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


def is_available() -> bool:
    """Return True if all required dependencies are present (for mic recording)."""
    return _shazam_available and _sounddevice_available and AudioSegment is not None


def can_identify_song() -> bool:
    """Return True if song identification is possible (from mic bytes or server mic).
    Only requires shazamio + pydub; sounddevice is only for direct mic recording.
    """
    return _shazam_available and AudioSegment is not None


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
