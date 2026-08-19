#!/usr/bin/env python3
"""Microphone-based music recognition via ShazamIO."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import shutil
import subprocess
import threading
from typing import Any

import lightctl

try:
    import numpy as np
except Exception as exc:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _numpy_import_error = exc
else:
    _numpy_import_error = None

logger = logging.getLogger("music_recognizer")

# Optional dependencies — gracefully degrade if unavailable
_try_import_errors: list[str] = []
if _numpy_import_error is not None:
    _try_import_errors.append(f"numpy: {_numpy_import_error}")

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

_sounddevice_available: bool | None = None
_sounddevice_import_error: BaseException | None = None
sd = None

if _try_import_errors:
    logger.debug("music_recognizer optional deps unavailable: %s", _try_import_errors)

DEFAULT_DURATION = 5.0
DEFAULT_SAMPLE_RATE = 0  # 0 = auto-detect from device at runtime
DEFAULT_AUDIO_SOURCE = "monitor"

# ShazamIO 0.2.0.0 busy-loops forever (without ever yielding to the event
# loop) when the audio is too short to produce a signature, and its HTTP
# request has no explicit timeout. Guard both before calling recognize_song.
MIN_AUDIO_MS = 1000
SHAZAM_TIMEOUT = 15.0
# Extra slack for the sync wrappers when joining their worker thread.
_SYNC_JOIN_MARGIN = 5.0


def _get_device_samplerate(device: str | int | None = None) -> int:
    """Return the native sample rate of the given (or default) input device."""
    sounddevice = _load_sounddevice()
    if sounddevice is None:
        return 44100
    try:
        info = sounddevice.query_devices(device=device, kind="input") if device is not None else sounddevice.query_devices(kind="input")
        return int(info.get("default_samplerate", 44100))
    except Exception:
        return 44100


def _load_sounddevice() -> Any | None:
    """Load sounddevice only when microphone recording actually needs it."""
    global sd, _sounddevice_available, _sounddevice_import_error
    if _sounddevice_available is not None:
        return sd
    try:
        import sounddevice as _sd
    except Exception as exc:  # pragma: no cover
        _sounddevice_available = False
        _sounddevice_import_error = exc
        return None
    sd = _sd
    _sounddevice_available = True
    _sounddevice_import_error = None
    return sd


def _record_audio(duration: float, sample_rate: int, device: str | int | None = None) -> Any:
    """Record audio from the preferred input device.

    If the specific device is unavailable, this may raise; callers should treat
    recording failures as 'no match' rather than fatal errors.
    """
    if np is None:
        raise RuntimeError("numpy is not available")
    sounddevice = _load_sounddevice()
    if sounddevice is None:
        raise RuntimeError("sounddevice is not available")
    if device is None:
        device = lightctl.get_mic_device()

    # If a concrete device was selected but is not currently usable, fall back to default (None)
    if device is not None:
        try:
            info = sounddevice.query_devices(device=device, kind="input")
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
        recording = sounddevice.rec(frames, samplerate=sample_rate, channels=1, dtype=np.float32, device=device)
        sounddevice.wait()
    except Exception as exc:
        # Let upper layers turn this into "no match" instead of crashing the process
        raise RuntimeError(f"Failed to open/record from audio device: {exc}") from exc
    # Convert float32 [-1.0, 1.0] to int16
    int16_data = np.clip(recording * 32767, -32768, 32767).astype(np.int16)
    return int16_data


def _make_audio_segment(audio_data: Any, sample_rate: int) -> Any:
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
    # A real Shazam match includes at least one entry in the matches list.
    # Shazam sometimes returns a track object for unrelated/fuzzy guesses with
    # an empty matches list; reject those to avoid getting stuck on a false
    # positive (e.g. repeatedly showing a popular song for silence/noise).
    matches = result.get("matches")
    if not isinstance(matches, list) or len(matches) == 0:
        return None
    track = result.get("track")
    if not track:
        return None
    # ShazamIO 0.2.0.0 returns plain dicts, not dataclasses
    heading = track.get("heading") or {}
    title = track.get("title") or heading.get("title")
    artist = track.get("subtitle") or heading.get("subtitle")
    album = None
    genre = None
    # Try genres.primary first
    genres_data = track.get("genres")
    if isinstance(genres_data, dict):
        genre = genres_data.get("primary")
    # Fallback to sections metadata
    sections = track.get("sections") or []
    for section in sections:
        if section.get("type") == "SONG":
            for meta in section.get("metadata") or []:
                label = meta.get("title", "").lower()
                if label in ("album", "album:"):
                    album = meta.get("text")
                elif label in ("genre", "genre:") and not genre:
                    genre = meta.get("text")
    share = track.get("share") or {}
    hub = track.get("hub") or {}
    actions = hub.get("actions") or [{}]
    first_action = actions[0] if isinstance(actions[0], dict) else {}
    images = track.get("images") or {}
    # Build normalized result
    parsed: dict[str, Any] = {
        "title": str(title).strip() if title else None,
        "artist": str(artist).strip() if artist else None,
        "album": str(album).strip() if album else None,
        "genre": str(genre).strip() if genre else None,
        "shazam_url": track.get("url") or share.get("href"),
        "spotify_url": first_action.get("uri"),
        "youtube_url": None,
        "cover_url": images.get("coverarthq") or images.get("coverart"),
        "source": "shazam",
    }
    # Clean None values
    parsed = {k: v for k, v in parsed.items() if v is not None}
    if not parsed.get("title") and not parsed.get("artist"):
        return None
    return parsed


# ---------------------------------------------------------------------------
# MPRIS now-playing via playerctl (stdlib subprocess, no D-Bus bindings)
# ---------------------------------------------------------------------------

_PREFERRED_MPRIS_PLAYERS = ("chrome", "chromium", "firefox")
_PLAYERCTL_TIMEOUT = 3.0


def _run_playerctl(args: list[str]) -> str | None:
    """Run a playerctl command, returning stdout or None on any failure."""
    if shutil.which("playerctl") is None:
        return None
    try:
        proc = subprocess.run(
            ["playerctl", *args],
            capture_output=True,
            text=True,
            timeout=_PLAYERCTL_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _list_mpris_players() -> list[str]:
    """Return playerctl player names, browser players (chrome/chromium/firefox) first."""
    output = _run_playerctl(["--list-all"])
    if not output:
        return []
    players = [line.strip() for line in output.splitlines() if line.strip()]
    preferred = [p for p in players if any(tok in p.lower() for tok in _PREFERRED_MPRIS_PLAYERS)]
    rest = [p for p in players if p not in preferred]
    return preferred + rest


def _run_gdbus(args: list[str]) -> str | None:
    """Run a gdbus command, returning stdout or None on any failure."""
    if shutil.which("gdbus") is None:
        return None
    try:
        proc = subprocess.run(
            ["gdbus", *args],
            capture_output=True,
            text=True,
            timeout=_PLAYERCTL_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _mpris_players_gdbus() -> list[str]:
    """MPRIS bus names via gdbus ListNames, browser players first."""
    output = _run_gdbus([
        "call", "--session",
        "--dest", "org.freedesktop.DBus",
        "--object-path", "/org/freedesktop/DBus",
        "--method", "org.freedesktop.DBus.ListNames",
    ])
    if not output:
        return []
    players = re.findall(r"org\.mpris\.MediaPlayer2\.[\w.-]+", output)
    preferred = [p for p in players if any(tok in p.lower() for tok in _PREFERRED_MPRIS_PLAYERS)]
    rest = [p for p in players if p not in preferred]
    return preferred + rest


def _gdbus_player_prop(player: str, prop: str) -> str | None:
    return _run_gdbus([
        "call", "--session",
        "--dest", player,
        "--object-path", "/org/mpris/MediaPlayer2",
        "--method", "org.freedesktop.DBus.Properties.Get",
        "org.mpris.MediaPlayer2.Player", prop,
    ])


def _now_playing_gdbus() -> dict[str, Any] | None:
    """Now-playing metadata straight off the session bus (no playerctl needed)."""
    for player in _mpris_players_gdbus():
        status_out = _gdbus_player_prop(player, "PlaybackStatus") or ""
        status_match = re.search(r"<\'(\w+)\'>", status_out) or re.search(r"'(\w+)'", status_out)
        if not status_match or status_match.group(1).lower() != "playing":
            continue
        metadata = _gdbus_player_prop(player, "Metadata") or ""

        def field(name: str) -> str:
            m = re.search(rf"'{re.escape(name)}':\s*<\[?'?\"?([^'\",\]>]+)", metadata)
            return m.group(1).strip() if m else ""

        title, artist, album, art_url = (
            field("xesam:title"), field("xesam:artist"), field("xesam:album"), field("mpris:artUrl"),
        )
        if not title and not artist:
            continue
        result: dict[str, Any] = {"source": "mpris", "player": player}
        if title:
            result["title"] = title
        if artist:
            result["artist"] = artist
        if album:
            result["album"] = album
        if art_url:
            result["cover_url"] = art_url
        logger.info("MPRIS now playing via gdbus (%s): %s — %s", player, artist, title)
        return result
    return None


def now_playing_mpris() -> dict[str, Any] | None:
    """Return now-playing track metadata from MPRIS.

    Returns a normalized dict (title/artist/album/cover_url/player,
    source="mpris") matching the shape of :func:`_parse_shazam_result`, or
    None when no player is playing or metadata is empty. Uses playerctl when
    available, falling back to gdbus on the session bus. Browser players
    (chrome/chromium/firefox) are preferred when several players are present.
    """
    fmt = "{{title}}\t{{artist}}\t{{album}}\t{{status}}\t{{mpris:artUrl}}"
    for player in _list_mpris_players():
        output = _run_playerctl(["--player", player, "metadata", "--format", fmt])
        if not output:
            continue
        parts = output.rstrip("\n").split("\t")
        parts += [""] * (5 - len(parts))
        title, artist, album, status, art_url = (p.strip() for p in parts[:5])
        if status.lower() != "playing":
            continue
        if not title and not artist:
            continue
        result: dict[str, Any] = {"source": "mpris", "player": player}
        if title:
            result["title"] = title
        if artist:
            result["artist"] = artist
        if album:
            result["album"] = album
        if art_url:
            result["cover_url"] = art_url
        logger.info("MPRIS now playing (%s): %s — %s", player, artist, title)
        return result
    return _now_playing_gdbus()


# ---------------------------------------------------------------------------
# Audio source selection (monitor vs mic) for the capture path
# ---------------------------------------------------------------------------


def _pactl_monitor_source() -> str | None:
    """Return the PulseAudio/PipeWire monitor source for the default sink."""
    if shutil.which("pactl") is None:
        return None
    try:
        proc = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True,
            text=True,
            timeout=_PLAYERCTL_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    names = []
    for line in proc.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            names.append(fields[1].strip())
    if "@DEFAULT_MONITOR@" in names:
        return "@DEFAULT_MONITOR@"
    default_monitor = ""
    try:
        sink = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True,
            text=True,
            timeout=_PLAYERCTL_TIMEOUT,
        )
        if sink.returncode == 0 and sink.stdout.strip():
            default_monitor = sink.stdout.strip() + ".monitor"
    except (subprocess.SubprocessError, OSError):
        pass
    if default_monitor and default_monitor in names:
        return default_monitor
    for name in names:
        if name.endswith(".monitor"):
            return name
    return None


def resolve_audio_source(cfg: dict | None = None) -> str:
    """Resolve which audio source the capture path should record from.

    Priority: ``LIGHT_AUDIO_SOURCE`` env override → ``cfg["audio_source"]``
    (default ``"monitor"``). ``"monitor"`` selects the PulseAudio/PipeWire
    monitor source of the default sink (falls back to ``"default"`` when
    pactl is unavailable or exposes no monitor); ``"mic"`` selects the
    configured ``mic_device`` (existing behavior); any other value is treated
    as an explicit device name and returned unchanged.
    """
    if cfg is None:
        cfg = lightctl.load_config()
    mode = os.environ.get("LIGHT_AUDIO_SOURCE", "").strip()
    if not mode:
        mode = str(cfg.get("audio_source", DEFAULT_AUDIO_SOURCE) or "").strip() or DEFAULT_AUDIO_SOURCE
    if mode == "monitor":
        source = _pactl_monitor_source()
        if source is None:
            logger.warning("No PulseAudio/PipeWire monitor source found; using default input")
            return "default"
        return source
    if mode == "mic":
        mic = cfg.get("mic_device", "")
        return str(mic) if mic else "default"
    return mode


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
    if np is None:
        raise RuntimeError("numpy is not available")
    if _load_sounddevice() is None:
        raise RuntimeError("sounddevice is not available")
    if AudioSegment is None:
        raise RuntimeError("pydub is not available")

    try:
        if device is None:
            device = resolve_audio_source()
        if sample_rate == 0:
            sample_rate = _get_device_samplerate(device)
        audio_data = await asyncio.to_thread(_record_audio, duration, sample_rate, device)
        segment = _make_audio_segment(audio_data, sample_rate)
        if len(segment) < MIN_AUDIO_MS:
            logger.info("Recorded audio too short for recognition (%d ms)", len(segment))
            return None

        audio_buf = io.BytesIO()
        segment.export(audio_buf, format="wav")

        shazam = Shazam()
        result = await asyncio.wait_for(shazam.recognize_song(audio_buf.getvalue()), timeout=SHAZAM_TIMEOUT)
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


async def recognize(
    duration: float = DEFAULT_DURATION,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    device: str | int | None = None,
) -> dict[str, Any] | None:
    """Identify the current track: MPRIS now-playing first, Shazam fallback.

    When music plays on this machine in a browser, the track metadata is
    already available over MPRIS and no audio capture is needed. Falls back
    to microphone recording + Shazam when MPRIS yields nothing.
    """
    mpris = await asyncio.to_thread(now_playing_mpris)
    if mpris:
        return mpris
    try:
        return await recognize_microphone(duration, sample_rate, device)
    except RuntimeError as exc:
        logger.info("MPRIS yielded nothing and Shazam fallback is unavailable: %s", exc)
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
        segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
        if len(segment) < MIN_AUDIO_MS:
            logger.info("Audio too short for recognition (%d ms)", len(segment))
            return None
        shazam = Shazam()
        result = await asyncio.wait_for(shazam.recognize_song(audio_bytes), timeout=SHAZAM_TIMEOUT)
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
    timeout = duration + SHAZAM_TIMEOUT + _SYNC_JOIN_MARGIN
    thread.join(timeout=timeout)
    if thread.is_alive():
        logger.warning("recognize_sync timed out after %.1fs; returning None", timeout)
        return None
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
    timeout = SHAZAM_TIMEOUT + _SYNC_JOIN_MARGIN
    thread.join(timeout=timeout)
    if thread.is_alive():
        logger.warning("recognize_audio_bytes_sync timed out after %.1fs; returning None", timeout)
        return None
    if error is not None:
        raise error
    return result


def is_available() -> bool:
    """Return True if known dependencies for mic recording are present.

    Do not import sounddevice here. In some desktop/sandbox environments the
    PortAudio import can block while probing devices, and this status check is
    called from GUI/request paths.
    """
    return _shazam_available and _sounddevice_available is not False and AudioSegment is not None and np is not None


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
    if np is None:
        reasons.append("numpy not installed")
    if _sounddevice_available is False:
        if _sounddevice_import_error is not None:
            reasons.append(f"sounddevice not installed ({_sounddevice_import_error})")
        else:
            reasons.append("sounddevice not installed")
    elif _sounddevice_available is None:
        reasons.append("sounddevice not checked until microphone recording")
    if AudioSegment is None:
        reasons.append("pydub not installed")
    return "Music recognition unavailable: " + ", ".join(reasons)
