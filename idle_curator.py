#!/usr/bin/env python3
"""Idle curator: keeps the wall alive with fresh, varied looks when nothing else runs.

When no director, show, realtime render, or music director owns the wall, the
curator wakes it at the Smart Director schedule brightness and starts a new
curated look every cycle: usually a realtime render (300-900s), occasionally a
dynamic-scene one-shot. Manual output (anything calling
light_gui.pause_smart_director_for_manual_output) backs the curator off so it
never fights the person at the controls.
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

DEFAULTS = {
    "enabled": True,
    "min_cycle_s": 300.0,
    "max_cycle_s": 900.0,
    "dynamic_scene_chance": 0.25,
    "backoff_s": 1800.0,
}

# Evocative prompts for build_look. Unhinted moods now sweep the full hue
# wheel; hinted ones (drift, tide, ember...) lean into a matching shader.
MOODS = (
    "glacial drift",
    "velvet nebula",
    "moonlit fog",
    "electric dune",
    "bioluminescent tide",
    "ember storm",
    "midnight orchid",
    "sunken cathedral",
    "neon rain",
    "polar shimmer",
    "copper meridian",
    "aurora vault",
    "liquid obsidian",
    "salt flat noon",
    "deep current",
    "wildflower static",
    "ionosphere",
    "champagne fizz",
    "cobalt hour",
    "monsoon season",
    "black sand beach",
    "cathedral light",
    "northern lights over water",
    "dust devil",
)

FALLBACK_BRIGHTNESS = 120


def validate_config(raw: dict | None = None) -> dict:
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("Idle curator settings must be an object.")
    cfg = {**DEFAULTS, **(raw or {})}
    cfg["enabled"] = bool(cfg["enabled"])
    for key in ("min_cycle_s", "max_cycle_s", "backoff_s"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"Invalid {key}.")
        cfg[key] = float(value)
    if cfg["max_cycle_s"] < cfg["min_cycle_s"]:
        raise ValueError("max_cycle_s must be greater than or equal to min_cycle_s.")
    chance = cfg["dynamic_scene_chance"]
    if isinstance(chance, bool) or not isinstance(chance, (int, float)) or not 0 <= chance <= 1:
        raise ValueError("Invalid dynamic_scene_chance.")
    cfg["dynamic_scene_chance"] = float(chance)
    return cfg


class IdleCurator(threading.Thread):
    def __init__(
        self,
        fleet: Any,
        config: dict | None = None,
        *,
        clock: Any = time.monotonic,
        rng: random.Random | None = None,
        wall_clock: Any = None,
        poll_s: float = 5.0,
    ) -> None:
        super().__init__(name="lightss-idle-curator", daemon=True)
        self.fleet = fleet
        self.config = validate_config(config)
        self.clock = clock
        self._rng = rng if rng is not None else random.Random()
        self.wall_clock = wall_clock
        self._poll_s = float(poll_s)
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._backoff_until: float | None = None
        self._cycles = 0
        self._last_look: dict | None = None

    def run(self) -> None:
        while not self._stop_event.is_set():
            cycle_s = self._tick()
            wait_s = max(self._poll_s, cycle_s) if cycle_s is not None else self._poll_s
            self._stop_event.wait(wait_s)

    def stop(self) -> None:
        self._stop_event.set()

    def configure(self, config: dict | None) -> None:
        self.config = validate_config(config)

    def note_manual_activity(self) -> None:
        with self._state_lock:
            self._backoff_until = self.clock() + float(self.config["backoff_s"])

    def status(self) -> dict:
        with self._state_lock:
            backoff_until = self._backoff_until
            cycles = self._cycles
            last_look = dict(self._last_look) if self._last_look else None
        remaining = max(0.0, backoff_until - self.clock()) if backoff_until is not None else 0.0
        return {
            "running": self.is_alive() and not self._stop_event.is_set(),
            "enabled": bool(self.config["enabled"]),
            "cycles": cycles,
            "backoff_remaining_s": round(remaining, 1),
            "last_look": last_look,
        }

    def scheduled_brightness(self) -> int:
        """Night/day brightness from the Smart Director schedule (0-255)."""
        try:
            import lightctl
            import smart_director

            config = lightctl.load_config()
            section = config.get("smart_director") if isinstance(config, dict) else None
            raw = section if isinstance(section, dict) else {}
            cfg = smart_director.validate_config({key: raw[key] for key in smart_director.DEFAULTS if key in raw})
            if self.wall_clock is not None:
                when = self.wall_clock()
            else:
                when = datetime.now(ZoneInfo(cfg["timezone"]))
            fraction = smart_director.tv_brightness(cfg, when)
            return max(1, min(255, int(round(float(fraction) * 255))))
        except Exception:
            return FALLBACK_BRIGHTNESS

    def _busy(self) -> bool:
        """True whenever any other writer might own the wall; failures count as busy."""
        try:
            import smart_director

            director = smart_director.current()
            if director is not None and director.is_alive():
                return True
        except Exception:
            return True
        for module_name, fn_name in (
            ("realtime", "realtime_status"),
            ("shows", "show_status"),
            ("music_director", "director_status"),
        ):
            try:
                module = __import__(module_name)
                if getattr(module, fn_name)().get("running"):
                    return True
            except Exception:
                return True
        return False

    def _tick(self) -> float | None:
        """One scheduling decision; returns the curated cycle length or None."""
        with self._state_lock:
            backoff_until = self._backoff_until
        if backoff_until is not None and self.clock() < backoff_until:
            return None
        if not self.config["enabled"] or self._busy():
            return None
        cycle_s = self._rng.uniform(self.config["min_cycle_s"], self.config["max_cycle_s"])
        self._curate(cycle_s)
        return cycle_s

    def _curate(self, cycle_s: float) -> None:
        mood = self._rng.choice(MOODS)
        seed = self._rng.randrange(1 << 30)
        use_scene = self._rng.random() < float(self.config["dynamic_scene_chance"])
        if use_scene:
            import dynamic_scenes

            dynamic_scenes.apply_dynamic_scene(self.fleet, mood=mood, seed=seed)
            kind = "scene"
        else:
            try:
                self.fleet.post_state({"on": True, "bri": self.scheduled_brightness()})
            except Exception:
                pass
            import realtime

            realtime.realtime_start(self.fleet, mood=mood, seed=seed, duration_s=cycle_s, fps=24)
            kind = "realtime"
        with self._state_lock:
            self._cycles += 1
            self._last_look = {"kind": kind, "mood": mood, "seed": seed, "cycle_s": cycle_s}


_curator: IdleCurator | None = None
_registry_lock = threading.RLock()


def start_curator(fleet: Any, config: dict | None = None) -> IdleCurator:
    global _curator
    cfg = validate_config(config)
    with _registry_lock:
        if _curator is not None and _curator.is_alive():
            _curator.configure(cfg)
            return _curator
        curator = IdleCurator(fleet, cfg)
        curator.start()
        _curator = curator
        return curator


def stop_curator() -> str:
    global _curator
    with _registry_lock:
        curator, _curator = _curator, None
    if curator is None:
        return "No idle curator is running."
    curator.stop()
    curator.join(timeout=5.0)
    return "Idle curator stopped."


def curator_status() -> dict:
    with _registry_lock:
        curator = _curator
    if curator is None:
        return {"running": False, "enabled": False, "cycles": 0, "backoff_remaining_s": 0, "last_look": None}
    return curator.status()


def note_manual_activity() -> None:
    with _registry_lock:
        curator = _curator
    if curator is not None:
        curator.note_manual_activity()
