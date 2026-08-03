#!/usr/bin/env python3
"""Show sequencer — timed, multi-step light shows for the WLED fleet.

A show is a list of steps, each applying one look for a fixed duration:

    {"name"?: str, "loop"?: bool,
     "steps": [{"look": {...}, "duration_s": float, "transition_s"?: float}]}

Look forms (exactly one per step):
    {"atmosphere": name}                    -> atmospheres.apply_atmosphere
    {"wall_mode": "span|mirror|chase|versus", ...kwargs} -> columns wall mode
    {"payload": {...}, "target"?: str}      -> fleet.post_state(payload, target)

ShowRunner is a daemon thread stepping through the normalized steps;
stop() is cooperative (the current step's sleep is interruptible). The
module-level registry (start_show/stop_show/show_status) enforces one
show at a time: starting a new show stops the old one.

The fleet is duck-typed: only post_state() is used, so a recording fake
fleet works in tests without importing fleet.py.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import TYPE_CHECKING

import atmospheres
import columns
import lightctl

if TYPE_CHECKING:
    from fleet import LightFleet

logger = logging.getLogger("shows")

# Fallback default target (fleet.DEFAULT_TARGET; duplicated so shows stays
# duck-typed and importable without fleet.py).
DEFAULT_TARGET = "all"

# Accepted wall_mode names -> columns function. "left_vs_right" is accepted
# as an alias for "versus".
_WALL_MODES = {
    "span": columns.wall_span,
    "mirror": columns.mirror,
    "chase": columns.chase,
    "versus": columns.left_vs_right,
    "left_vs_right": columns.left_vs_right,
}

# Required kwargs per wall mode (the rest pass through as seg options).
_WALL_MODE_REQUIRED = {
    "span": ("fx",),
    "mirror": ("fx",),
    "chase": ("fx",),
    "versus": ("fx_left", "fx_right"),
    "left_vs_right": ("fx_left", "fx_right"),
}


# ---------------------------------------------------------------------------
# Validation / normalization
# ---------------------------------------------------------------------------

def _as_seconds(value: object, field: str, step_index: int) -> float:
    """Coerce a duration field to a non-negative float or raise ValueError."""
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(
            f"Step {step_index}: {field} must be a number, got {value!r}."
        ) from None
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(
            f"Step {step_index}: {field} must be a finite number >= 0, got {seconds}."
        )
    return seconds


def _validate_look(look: object, step_index: int) -> dict:
    """Validate one step's look and return it normalized (shallow copy)."""
    if not isinstance(look, dict):
        raise ValueError(f"Step {step_index}: 'look' must be a dict, got {look!r}.")
    forms = [key for key in ("atmosphere", "wall_mode", "payload") if key in look]
    if len(forms) != 1:
        raise ValueError(
            f"Step {step_index}: look must contain exactly one of "
            f"'atmosphere', 'wall_mode' or 'payload'; found {forms or 'none'}."
        )

    normalized = dict(look)
    if "atmosphere" in look:
        name = look["atmosphere"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Step {step_index}: 'atmosphere' must be a non-empty name.")
        if name.strip().lower() not in atmospheres.ATMOSPHERES:
            raise ValueError(
                f"Step {step_index}: unknown atmosphere {name!r}. "
                f"Available: {', '.join(atmospheres.atmosphere_names())}."
            )
        normalized["atmosphere"] = name.strip().lower()
    elif "wall_mode" in look:
        mode = look["wall_mode"]
        if mode not in _WALL_MODES:
            raise ValueError(
                f"Step {step_index}: unknown wall_mode {mode!r}. "
                f"Valid: {', '.join(sorted(_WALL_MODES))}."
            )
        missing = [key for key in _WALL_MODE_REQUIRED[mode] if key not in look]
        if missing:
            raise ValueError(
                f"Step {step_index}: wall_mode {mode!r} is missing required "
                f"kwarg(s): {', '.join(missing)}."
            )
    else:
        if not isinstance(look["payload"], dict):
            raise ValueError(f"Step {step_index}: 'payload' must be a dict.")
        if "target" in look and not isinstance(look["target"], str):
            raise ValueError(f"Step {step_index}: 'target' must be a string.")
    return normalized


def validate_show(show: dict) -> list[dict]:
    """Validate a show dict and return its normalized steps.

    Each normalized step is {"look": {...}, "duration_s": float,
    "transition_s": float} with defaults filled in. Raises ValueError with
    a clear message on any malformed input.
    """
    if not isinstance(show, dict):
        raise ValueError(f"Show must be a dict, got {show!r}.")
    if not isinstance(show.get("loop", False), bool):
        raise ValueError(f"'loop' must be a boolean, got {show['loop']!r}.")
    steps = show.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Show needs a non-empty 'steps' list.")

    normalized: list[dict] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"Step {index}: must be a dict, got {step!r}.")
        if "look" not in step:
            raise ValueError(f"Step {index}: missing 'look'.")
        if "duration_s" not in step:
            raise ValueError(f"Step {index}: missing 'duration_s'.")
        normalized.append({
            "look": _validate_look(step["look"], index),
            "duration_s": _as_seconds(step["duration_s"], "duration_s", index),
            "transition_s": _as_seconds(step.get("transition_s", 0.0),
                                        "transition_s", index),
        })
    return normalized


# ---------------------------------------------------------------------------
# Look execution
# ---------------------------------------------------------------------------

def _transition_units(transition_s: float) -> int | None:
    """Convert seconds to WLED transition units (100ms each, clamped 0-255)."""
    if not math.isfinite(transition_s) or transition_s <= 0:
        return None
    return lightctl.clamp_byte(round(transition_s * 10))


def apply_look(fleet: LightFleet, look: dict, transition_s: float = 0.0) -> None:
    """Apply one normalized look to the fleet.

    Transitions are sent as WLED 'transition' units of 100ms: injected into
    raw payload looks directly, and posted ahead of atmosphere/wall_mode
    looks (whose payloads are built inside atmospheres/columns) so the
    following look POST fades in.
    """
    transition = _transition_units(transition_s)
    if "atmosphere" in look:
        if transition is not None:
            fleet.post_state({"transition": transition})
        atmospheres.apply_atmosphere(fleet, look["atmosphere"])
    elif "wall_mode" in look:
        if transition is not None:
            fleet.post_state({"transition": transition})
        mode = look["wall_mode"]
        kwargs = {key: value for key, value in look.items() if key != "wall_mode"}
        _WALL_MODES[mode](fleet, **kwargs)
    else:
        payload = dict(look["payload"])
        if transition is not None:
            payload["transition"] = transition
        fleet.post_state(payload, target=look.get("target", DEFAULT_TARGET))


# ---------------------------------------------------------------------------
# ShowRunner
# ---------------------------------------------------------------------------

class ShowRunner(threading.Thread):
    """Daemon thread stepping through normalized show steps.

    stop() is cooperative: it interrupts the current step's sleep and the
    thread exits after the in-flight look application returns.
    """

    def __init__(self, fleet: LightFleet, steps: list[dict], loop: bool = False):
        super().__init__(daemon=True, name="lightss-show")
        self.fleet = fleet
        self.steps = steps
        self.loop = loop
        self.current_step = 0
        self._stop_event = threading.Event()

    def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                for index, step in enumerate(self.steps):
                    if self._stop_event.is_set():
                        return
                    self.current_step = index
                    try:
                        look = step["look"]
                        transition_s = step["transition_s"]
                        duration_s = step["duration_s"]
                    except (KeyError, TypeError) as exc:
                        logger.warning("Show step %d is malformed: %s", index, exc)
                        continue
                    try:
                        apply_look(self.fleet, look, transition_s)
                    except Exception as exc:  # keep the show alive on bad posts
                        logger.warning("Show step %d failed: %s", index, exc)
                    if self._stop_event.wait(duration_s):
                        return
                if not self.loop:
                    return
        finally:
            self._stop_event.set()

    def stop(self) -> None:
        """Ask the runner to stop; returns immediately (cooperative)."""
        self._stop_event.set()

    def is_running(self) -> bool:
        return self.is_alive() and not self._stop_event.is_set()


# ---------------------------------------------------------------------------
# Module-level registry (one show at a time)
# ---------------------------------------------------------------------------

_registry_lock = threading.Lock()
_current_runner: ShowRunner | None = None
_current_name: str | None = None


def _stop_locked() -> ShowRunner | None:
    """Stop the registered runner (caller holds the lock); returns it."""
    global _current_runner, _current_name
    runner = _current_runner
    if runner is not None:
        runner.stop()
        _current_runner = None
        _current_name = None
    return runner


def start_show(fleet: LightFleet, show: dict) -> str:
    """Validate and start a show, replacing any show already running."""
    global _current_runner, _current_name
    steps = validate_show(show)
    loop = show.get("loop", False)
    name = str(show.get("name") or "untitled")
    runner = ShowRunner(fleet, steps, loop=loop)

    with _registry_lock:
        old = _stop_locked()
        _current_runner = runner
        _current_name = name
        # Start under the lock so a concurrent stop_show() never sees a
        # registered-but-unstarted runner (join would raise RuntimeError).
        runner.start()
    if old is not None:
        old.join(timeout=5.0)
    return (
        f"Show {name!r} started: {len(steps)} step(s)"
        f"{', looping' if loop else ''}."
    )


def stop_show() -> str:
    """Stop the running show, if any."""
    with _registry_lock:
        old = _stop_locked()
    if old is None:
        return "No show is running."
    old.join(timeout=5.0)
    return "Show stopped."


def show_status() -> dict:
    """Status of the current show: running flag, name, step position."""
    with _registry_lock:
        runner = _current_runner
        name = _current_name
    if runner is None or not runner.is_running():
        return {"running": False, "name": None, "step": None, "steps": 0, "loop": False}
    return {
        "running": True,
        "name": name,
        "step": runner.current_step,
        "steps": len(runner.steps),
        "loop": runner.loop,
    }
