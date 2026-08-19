#!/usr/bin/env python3
"""FireTV control via ADB.

Wraps `adb` (no shell=True, stdlib only) to wake/sleep the living-room
FireTV, launch URLs on it, and query its power/foreground state so the TV
can complement the light visuals. All actions are gated behind the
"firetv.enabled" config flag — the user sometimes uses the TV for music,
so nothing touches it unless the UI switch is on.

Config (config.json "firetv" object):
  host    — adb target (default 10.27.27.207:5555)
  enabled — master switch (default False)
"""

from __future__ import annotations

import logging
import re
import subprocess

import lightctl

logger = logging.getLogger("lightss.firetv")

DEFAULT_HOST = "10.27.27.207:5555"
ADB_TIMEOUT = 8.0

DISABLED_MESSAGE = "FireTV control is disabled in settings"

KEYCODE_WAKEUP = 224
KEYCODE_SLEEP = 223


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
    """Run an adb command, returning stdout. Raises RuntimeError on failure."""
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
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or output or f"exit code {proc.returncode}"
        raise RuntimeError(f"adb error: {detail}")
    return output


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


def open_url(url: str, cfg: dict | None = None) -> str:
    """Open a URL on the FireTV via an ACTION_VIEW intent."""
    config = _require_enabled(cfg)
    _connect(config)
    logger.info("FireTV open URL: %s", url)
    return _adb_shell_resilient(["am", "start", "-a", "android.intent.action.VIEW", "-d", url], config)
