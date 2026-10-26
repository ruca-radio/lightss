#!/usr/bin/env python3
"""FireTV control via ADB.

Wraps `adb` (no shell=True, stdlib only) to wake/sleep the living-room
FireTV, launch URLs on it, and query its power/foreground state so the TV
can complement the light visuals. All actions are gated behind the
"firetv.enabled" config flag — the user sometimes uses the TV for music,
so control stays disabled unless the UI switch is on. Read-only observe()
uses its own "firetv.observation_enabled" permission and never invokes
the control methods.

Config (config.json "firetv" object):
  host    — adb target (default 10.27.27.207:5555)
  enabled — master switch (default False)
  observation_enabled — read-only power/app/media observation (default False)
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading

import lightctl

logger = logging.getLogger("lightss.firetv")

DEFAULT_HOST = "10.27.27.207:5555"
ADB_TIMEOUT = 8.0
_adb_lock = threading.Lock()

DISABLED_MESSAGE = "FireTV control is disabled in settings"

KEYCODE_WAKEUP = 224
KEYCODE_SLEEP = 223
KEYCODE_MEDIA_PLAY_PAUSE = 85
KEYCODE_MEDIA_NEXT = 87
KEYCODE_MEDIA_PREVIOUS = 88
KEYCODE_VOLUME_UP = 24
KEYCODE_VOLUME_DOWN = 25
KEYCODE_VOLUME_MUTE = 164


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_firetv_config(cfg: dict | None = None) -> dict:
    """Resolve FireTV settings: config.json "firetv" object over built-in defaults."""
    if cfg is None:
        cfg = lightctl.load_config()
    configured = cfg.get("firetv")
    if not isinstance(configured, dict):
        configured = {}
    return {"host": DEFAULT_HOST, "enabled": False, **configured}


def is_enabled(cfg: dict | None = None) -> bool:
    """True when FireTV control is switched on in settings."""
    return bool(load_firetv_config(cfg).get("enabled"))


def _serial(config: dict) -> str:
    """Build the adb target from a resolved firetv config (see load_firetv_config)."""
    return str(config["host"])


# ---------------------------------------------------------------------------
# ADB plumbing
# ---------------------------------------------------------------------------


def _run_adb(args: list[str], timeout: float = ADB_TIMEOUT) -> str:
    """Serialize ADB startup and retry one daemon failure without killing the server."""
    with _adb_lock:
        for attempt in range(2):
            try:
                proc = subprocess.run(
                    ["adb", *args],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"adb timed out after {timeout}s") from exc
            except subprocess.SubprocessError as exc:
                raise RuntimeError(f"adb error: {exc}") from exc
            except OSError as exc:
                raise RuntimeError(f"adb failed to run: {exc}") from exc
            output = (proc.stdout or "").strip()
            if proc.returncode == 0:
                return output
            detail = (proc.stderr or "").strip() or output or f"exit code {proc.returncode}"
            daemon_error = detail.lower()
            if attempt == 0 and (
                "cannot connect to daemon" in daemon_error
                or "failed to start daemon" in daemon_error
                or ("smartsocket" in daemon_error and "address already in use" in daemon_error)
            ):
                logger.info("FireTV adb daemon startup failed; retrying command once")
                continue
            raise RuntimeError(f"adb error: {detail}")


def _adb_shell(command: list[str], config: dict) -> str:
    return _run_adb(["-s", _serial(config), "shell", *command])


def _require_enabled(cfg: dict | None = None) -> dict:
    config = load_firetv_config(cfg)
    if not config.get("enabled"):
        raise ValueError(DISABLED_MESSAGE)
    return config


def _connect(config: dict) -> str:
    target = _serial(config)
    output = _run_adb(["connect", target])
    # adb connect exits 0 even when it can't reach the device; the failure
    # only shows up in stdout ("unable to connect to ...").
    if "unable to connect" in output.lower() or "cannot connect" in output.lower():
        raise RuntimeError(f"adb connect to {target} failed: {output}")
    logger.info("FireTV adb connect %s: %s", target, output)
    return output


def _reconnect(config: dict) -> None:
    """Clear a stale 'device offline' entry: full disconnect + connect."""
    target = _serial(config)
    try:
        _run_adb(["disconnect", target])
    except RuntimeError:
        pass
    _connect(config)


def _adb_shell_resilient(command: list[str], config: dict) -> str:
    """Shell with one offline-recovery retry (adb drops when the TV sleeps)."""
    try:
        return _adb_shell(command, config)
    except RuntimeError as exc:
        if "offline" not in str(exc) and "not found" not in str(exc) and "no devices" not in str(exc):
            raise
        logger.info("FireTV adb %s; reconnecting", exc)
        _reconnect(config)
        return _adb_shell(command, config)


def connect(cfg: dict | None = None) -> str:
    """Connect adb to the FireTV; returns the adb connect output line."""
    return _connect(_require_enabled(cfg))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _parse_awake(power_dump: str) -> bool | None:
    match = re.search(r"mWakefulness=(\w+)", power_dump)
    if not match:
        return None
    return match.group(1).lower() == "awake"


def _parse_foreground_app(window_dump: str) -> str | None:
    match = re.search(r"mCurrentFocus=\S+\s+\S+\s+([\w.]+)/", window_dump)
    if match:
        return match.group(1)
    match = re.search(r"mCurrentFocus=.*?([\w.]+)/[\w.]+}", window_dump)
    return match.group(1) if match else None


def status(cfg: dict | None = None) -> dict:
    """Return {'enabled', 'connected', 'awake', 'foreground_app'}; never raises.

    Any adb failure is reported as connected=False plus an 'error' string.
    """
    config = load_firetv_config(cfg)
    result: dict = {
        "enabled": bool(config.get("enabled")),
        "connected": False,
        "awake": None,
        "foreground_app": None,
    }
    if not result["enabled"]:
        return result
    try:
        power_dump = _adb_shell_resilient(["dumpsys", "power"], config)
        window_dump = _adb_shell_resilient(["dumpsys", "window"], config)
    except RuntimeError as exc:
        result["error"] = str(exc)
        logger.warning("FireTV status failed: %s", exc)
        return result
    result["connected"] = True
    result["awake"] = _parse_awake(power_dump)
    result["foreground_app"] = _parse_foreground_app(window_dump)
    return result


# Observation is deliberately independent of the permission to control the TV.
_MUSIC_APPS = {"com.spotify.tv.android", "com.google.android.apps.youtube.music",
               "com.amazon.music.tv"}
_VIDEO_APPS = {"com.amazon.avod.tv.client", "com.netflix.ninja",
               "com.disney.disneyplus", "com.hulu.plus"}
_MIXED_APPS = {"com.amazon.firetv.youtube", "com.google.android.youtube.tv"}

# Android PlaybackState line: `state=PlaybackState {state=3, position=550097,
# buffered position=0, speed=1.0, updated=112529869, ...}`. Field order is not
# guaranteed and `buffered position` is a separate field from `position`, so the
# brace body is split into key=value pairs instead of matched ordinally.
_PLAYBACK_STATE_RE = re.compile(r"state=PlaybackState\s*\{([^}]*)\}")


def _playback_fields(text: str) -> dict[str, str]:
    """Split a PlaybackState body into {field: raw value} pairs."""
    fields: dict[str, str] = {}
    for part in re.split(r",\s*", text):
        key, separator, value = part.partition("=")
        key = key.strip()
        if separator and key:
            fields[key] = value.strip()
    return fields


def _playback_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _playback_float(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_media_session(text: str, foreground_app: str | None) -> dict | None:
    """Summarize an active foreground session, not unrelated session history.

    Description is untrusted display text, not proof of genre or music playback.
    Android's comma-separated description is retained intact: commas also occur
    inside titles and cannot reliably distinguish title from artist.

    Playback position is reported from PlaybackState as ``position_ms`` plus the
    monotonic ``updated_elapsed_ms`` timestamp and ``speed``; Android only
    refreshes those fields on state changes, so callers extrapolate with device
    uptime (see ``observe``).
    """
    if not foreground_app:
        return None
    candidates = []
    for block in re.split(r"(?m)^\s*package=", text)[1:]:
        if not block.strip():
            continue
        package = block.splitlines()[0].strip()
        if package != foreground_app or not re.search(r"(?m)^\s*active=true\s*$", block):
            continue
        playback = _PLAYBACK_STATE_RE.search(block)
        fields = _playback_fields(playback.group(1)) if playback else {}
        state = _playback_int(fields.get("state"))
        description = re.search(r"(?m)^\s*metadata:.*?description=(.*)$", block)
        description = description.group(1).strip()[:500] if description else None
        if description in ("", "null", "null, null, null"):
            description = None
        candidates.append({"package": package, "active": True,
                           "state": state,
                           "description": description,
                           "position_ms": _playback_int(fields.get("position")),
                           "speed": _playback_float(fields.get("speed")),
                           "updated_elapsed_ms": _playback_int(fields.get("updated"))})
    return next((item for item in candidates if item["state"] == 3),
                candidates[0] if candidates else None)


def activity_hint(awake: bool | None, foreground_app: str | None,
                  media_session: dict | None) -> tuple[str, str]:
    """Conservative evidence only; this function never selects a light mode."""
    if awake is False:
        return "idle", "TV is asleep."
    if awake is not True:
        return "unknown", "TV power state is unavailable."
    if foreground_app in _MIXED_APPS:
        return "unknown", "This app plays both music and video; content type is ambiguous."
    if foreground_app in _VIDEO_APPS:
        return "tv", "A video app is in the foreground."
    if foreground_app in _MUSIC_APPS and media_session:
        if media_session.get("state") == 3:
            return "music", "A music app has an active playing session."
        if media_session.get("state") in (0, 1, 2):
            return "idle", "The foreground music session is not playing."
    return "unknown", "Not enough evidence to distinguish music from TV viewing."


_OBSERVATION_COMMANDS: dict[str, list[str]] = {
    "power": ["dumpsys", "power"],
    "window": ["dumpsys", "window"],
    "media_session": ["dumpsys", "media_session"],
    # Monotonic device clock (seconds since boot) used to correct the stale
    # PlaybackState position; read-only and cheap.
    "uptime": ["cat", "/proc/uptime"],
}


def _observation_read(service: str, config: dict) -> str:
    """Fixed read-only device reads, bounded timeouts and one reconnect."""
    observed = _OBSERVATION_COMMANDS.get(str(service))
    if observed is None:
        raise ValueError("Unsupported TV observation service")
    command = ["-s", _serial(config), "shell", *observed]
    try:
        return _run_adb(command, timeout=2.0)
    except RuntimeError as exc:
        if not any(word in str(exc) for word in ("offline", "not found", "no devices")):
            raise
        output = _run_adb(["connect", _serial(config)], timeout=2.0)
        if "unable to connect" in output.lower() or "cannot connect" in output.lower():
            raise RuntimeError("TV observation connection failed") from exc
        return _run_adb(command, timeout=2.0)


def _uptime_ms(config: dict) -> float | None:
    """Device uptime in milliseconds (first /proc/uptime value); None on failure."""
    try:
        output = _observation_read("uptime", config)
        match = re.search(r"\d+(?:\.\d+)?", output or "")
        if not match:
            return None
        return float(match.group(0)) * 1000.0
    except Exception as exc:  # observation is best effort; never raise to observe()
        logger.info("FireTV uptime read failed: %s", exc)
        return None


def _media_position_s(media: dict, config: dict) -> float | None:
    """True playback position in seconds from a stale PlaybackState snapshot.

    Playing sessions extrapolate ``position_ms`` with the device uptime elapsed
    since ``updated_elapsed_ms``; paused (or speed 0) uses the reported position.
    A playing session whose uptime cannot be read reports None rather than a
    stale position.
    """
    position_ms = media.get("position_ms")
    if position_ms is None:
        return None
    state = media.get("state")
    speed = media.get("speed")
    updated_ms = media.get("updated_elapsed_ms")
    playing = state == 3 and isinstance(speed, (int, float)) and speed > 0
    if playing and updated_ms is not None:
        uptime_ms = _uptime_ms(config)
        if uptime_ms is None:
            return None
        position_ms = position_ms + max(0.0, uptime_ms - float(updated_ms)) * float(speed)
    return round(max(0.0, float(position_ms) / 1000.0), 1)


def observe(cfg: dict | None = None) -> dict:
    """Read TV context only when explicitly allowed; never control TV or lights."""
    config = load_firetv_config(cfg)
    result = {"observation_enabled": config.get("observation_enabled") is True,
              "control_enabled": bool(config.get("enabled")),
              "connected": False, "awake": None, "foreground_app": None,
              "media_session": None, "activity_hint": "unknown",
              "reason": "Read-only TV observation is disabled."}
    if not result["observation_enabled"]:
        return result
    try:
        power = _observation_read("power", config)
        focus = _observation_read("window", config)
    except RuntimeError as exc:
        result.update(error=str(exc), reason="TV observation is unavailable.")
        return result
    wakefulness = re.search(r"mWakefulness=(\w+)", power)
    awake = {"awake": True, "asleep": False}.get(wakefulness.group(1).lower()) if wakefulness else None
    result.update(connected=True, awake=awake,
                  foreground_app=_parse_foreground_app(focus))
    if result["awake"] is True:
        try:
            result["media_session"] = parse_media_session(
                _observation_read("media_session", config), result["foreground_app"])
        except RuntimeError as exc:
            result["media_error"] = str(exc)
        if result["media_session"] is not None:
            result["media_session"]["position_s"] = _media_position_s(
                result["media_session"], config)
    result["activity_hint"], result["reason"] = activity_hint(
        result["awake"], result["foreground_app"], result["media_session"])
    return result


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def keyevent(code: int | str, cfg: dict | None = None) -> str:
    """Send an Android keyevent code (e.g. 224 wake, 223 sleep) to the FireTV."""
    config = _require_enabled(cfg)
    _connect(config)
    return _adb_shell_resilient(["input", "keyevent", str(code)], config)


def wake(cfg: dict | None = None) -> str:
    """Wake the FireTV screen (KEYCODE_WAKEUP)."""
    return keyevent(KEYCODE_WAKEUP, cfg)


def sleep(cfg: dict | None = None) -> str:
    """Put the FireTV screen to sleep (KEYCODE_SLEEP)."""
    return keyevent(KEYCODE_SLEEP, cfg)


def play_pause(cfg: dict | None = None) -> str:
    """Toggle media playback (KEYCODE_MEDIA_PLAY_PAUSE)."""
    return keyevent(KEYCODE_MEDIA_PLAY_PAUSE, cfg)


def next_track(cfg: dict | None = None) -> str:
    """Skip to the next media track (KEYCODE_MEDIA_NEXT)."""
    return keyevent(KEYCODE_MEDIA_NEXT, cfg)


def previous_track(cfg: dict | None = None) -> str:
    """Skip to the previous media track (KEYCODE_MEDIA_PREVIOUS)."""
    return keyevent(KEYCODE_MEDIA_PREVIOUS, cfg)


def volume_up(cfg: dict | None = None) -> str:
    """Raise FireTV volume (KEYCODE_VOLUME_UP)."""
    return keyevent(KEYCODE_VOLUME_UP, cfg)


def volume_down(cfg: dict | None = None) -> str:
    """Lower FireTV volume (KEYCODE_VOLUME_DOWN)."""
    return keyevent(KEYCODE_VOLUME_DOWN, cfg)


def mute(cfg: dict | None = None) -> str:
    """Toggle FireTV mute (KEYCODE_VOLUME_MUTE)."""
    return keyevent(KEYCODE_VOLUME_MUTE, cfg)


def open_url(url: str, cfg: dict | None = None) -> str:
    """Open a URL on the FireTV via an ACTION_VIEW intent."""
    config = _require_enabled(cfg)
    _connect(config)
    logger.info("FireTV open URL: %s", url)
    return _adb_shell_resilient(["am", "start", "-a", "android.intent.action.VIEW", "-d", url], config)
