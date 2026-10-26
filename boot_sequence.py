#!/usr/bin/env python3
"""Boot self-config sequence: identify every wall strip, read the wall, restore it.

The sequence runs once at GUI start, before any director owns the wall:

1. Snapshot each controller's state (``fleet.get_state()``), map every named
   channel to its controller/segment (``fleet.channels()``), record the
   programming found on each strip, the pixels/GPIO from
   ``fleet.topology_dict()`` and any ``fleet.topology_mismatches(fleet)`` drift.
2. If no controller state can be read, finish with phase ``"error"`` (no
   animation) but still persist a report.
3. Otherwise light ``wall_order`` strips one at a time: every strip forced off
   except the active one, which shows static solid (fx 0) in a distinct color
   (cyan, magenta, amber, lime). Whole-controller power/brightness is raised
   once first. Dwell is bounded to 0.8-5.0 s; nothing flashes.
4. Restore the captured per-controller state faithfully in ``finally``.
5. Persist a JSON report (schema 1) atomically to
   ``lightctl._CONFIG_ROOT / "boot_report.json"`` (0600). Persistence failures
   are recorded, never fatal.
6. Serve the state through a module-level registry: ``start_boot``,
   ``stop_boot``, ``boot_status``, ``latest_report``, ``summary_text``,
   ``context_text``.

Duck typing: the fleet only needs ``channels()``, ``post_state()``,
``get_state()`` and (optionally) ``topology_dict()``, ``WALL_ORDER``,
``clients``. Effects/palettes are read from the controller clients when
available. Every side effect (sleep, clock, observation) is injectable so tests
never touch hardware or really sleep.

The thread is a daemon like ``music_director``/``shows``; ``run()`` never
raises and ``stop()`` is cooperative.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger("lightss.boot_sequence")

#: Validated configuration defaults for the ``boot_sequence`` section.
DEFAULTS = {"enabled": True, "dwell_s": 1.1, "brightness": 160}

#: Report schema version written to disk and served over the API.
REPORT_SCHEMA = 1

#: Static, never-flashing colors assigned to the strips left-to-right.
STEP_COLORS: tuple[tuple[int, int, int], ...] = (
    (0, 255, 255),    # cyan
    (255, 0, 255),    # magenta
    (255, 191, 0),    # amber
    (128, 255, 0),    # lime
)

#: Fallback physical wall order; ``fleet.WALL_ORDER`` wins when present.
_WALL_ORDER = ["far-left", "middle-left", "middle-right", "far-right"]

#: Wall-wide composer entry points advertised in the boot report capabilities.
_WALL_MODES = [
    "wall_span",
    "mirror",
    "chase",
    "left_vs_right",
    "apply_channels",
    "per_strip",
    "set_channel",
]

#: Segment fields snapshotted from the controller state and restored afterwards.
_RESTORE_SEG_KEYS = ("fx", "pal", "sx", "ix", "col", "on", "bri")

#: Segment fields recorded per channel in the report (includes geometry markers).
_CAPTURE_SEG_KEYS = (*_RESTORE_SEG_KEYS, "start", "stop")

_MIN_DWELL_S = 0.8
_MAX_DWELL_S = 5.0
_MAX_SLEEP_CHUNK_S = 0.1
_DEFAULT_REPORT_NAME = "boot_report.json"


# ---------------------------------------------------------------------------
# Configuration / small helpers
# ---------------------------------------------------------------------------

def validate_config(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize a ``boot_sequence`` config section; never raises.

    ``dwell_s`` is clamped to 0.8-5.0 s (static colors only, no flashing),
    ``brightness`` to 1-255, and ``enabled`` accepts JSON booleans plus the
    usual string spellings.
    """
    raw = raw if isinstance(raw, Mapping) else {}
    enabled_raw = raw.get("enabled", DEFAULTS["enabled"])
    if isinstance(enabled_raw, str):
        enabled = enabled_raw.strip().lower() not in {"", "0", "false", "no", "off"}
    else:
        enabled = bool(enabled_raw)
    try:
        dwell = float(raw.get("dwell_s", DEFAULTS["dwell_s"]))
    except (TypeError, ValueError):
        dwell = float(DEFAULTS["dwell_s"])
    if not math.isfinite(dwell):
        dwell = float(DEFAULTS["dwell_s"])
    dwell = min(_MAX_DWELL_S, max(_MIN_DWELL_S, dwell))
    try:
        brightness = int(raw.get("brightness", DEFAULTS["brightness"]))
    except (TypeError, ValueError):
        brightness = int(DEFAULTS["brightness"])
    brightness = min(255, max(1, brightness))
    return {"enabled": enabled, "dwell_s": dwell, "brightness": brightness}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _config_digest(config: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(config), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _default_report_path() -> Path:
    """Report lives next to the config, but is a report, not configuration."""
    import lightctl  # lazy: keeps module import cheap and dependency-light

    root = getattr(lightctl, "_CONFIG_ROOT", None)
    if root is None:  # pragma: no cover - lightctl always defines it
        root = Path("~/.config/lightsctl").expanduser()
    return Path(root) / _DEFAULT_REPORT_NAME


def _atomic_write_json(path: Path | str, data: Any) -> None:
    """Write JSON with a temp file + fsync + os.replace, mode 0600."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _color_text(value: Any) -> str | None:
    """Render the first color stop as a hex string, or None when unusable."""
    try:
        first = value[0] if isinstance(value, (list, tuple)) and value else value
        components = list(first)[:3] if isinstance(first, (list, tuple)) else list(first)[:3]
        if len(components) != 3:
            return None
        rgb = [max(0, min(255, int(component))) for component in components]
        return "#%02x%02x%02x" % tuple(rgb)
    except (TypeError, ValueError):
        return None


def _format_channel_line(
    index: int,
    total: int,
    channel: str,
    controller: str,
    segment: int,
    pixels: Any,
    effect_name: str | None,
    palette_name: str | None,
    sx: Any,
    ix: Any,
    *,
    effect_id: Any = None,
    palette_id: Any = None,
    color: Any = None,
) -> str:
    """Articulation line for one lit strip, stating how it was programmed.

    ``1/4 far-left — left · segment 0 · 34 px · fx Blink (1) · pal Analogous (2) · sx 200 · ix 180 · col #ff0000``
    """
    pixels_text = str(pixels) if pixels is not None else "?"
    effect_text = effect_name if effect_name else "unknown"
    palette_text = palette_name if palette_name else "unknown"
    sx_text = sx if sx is not None else 128
    ix_text = ix if ix is not None else 128
    effect_ref = f" ({effect_id})" if effect_id is not None else ""
    palette_ref = f" ({palette_id})" if palette_id is not None else ""
    line = (
        f"{index}/{total} {channel} — {controller} · segment {segment} · "
        f"{pixels_text} px · fx {effect_text}{effect_ref} · pal {palette_text}{palette_ref} · "
        f"sx {sx_text} · ix {ix_text}"
    )
    color_text = _color_text(color)
    if color_text:
        line += f" · col {color_text}"
    return line


# ---------------------------------------------------------------------------
# BootSequence
# ---------------------------------------------------------------------------

class BootSequence(threading.Thread):
    """Daemon thread running the one-shot boot light show and self-config read.

    ``stop()`` is cooperative: it interrupts the dwell sleep and the thread
    restores the pre-boot state, then exits. ``run()`` never raises.
    """

    def __init__(
        self,
        fleet: Any,
        config: Mapping[str, Any] | None = None,
        *,
        sleep: Callable[[float], Any] = time.sleep,
        observe_fn: Callable[[Any], dict] | None = None,
        report_path: Path | str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(daemon=True, name="lightss-boot-sequence")
        self.fleet = fleet
        self.config = validate_config(config)
        self._sleep = sleep
        self._observe = observe_fn
        self._clock = clock or time.monotonic
        self.report_path = report_path

        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._phase = "idle"
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._step: dict[str, Any] | None = None
        self._lines: list[str] = []
        self._steps: list[dict[str, Any]] = []
        self._errors: list[str] = []
        self._error: str | None = None
        self._report: dict[str, Any] | None = None
        self._stopped = False
        self._wrote = False
        self._restore_done = False
        self._restored: dict[str, Any] | None = None
        self._t0: float | None = None

        # Snapshot state (filled by _snapshot()).
        self._topology: dict[str, Any] = {}
        self._channel_map: dict[str, tuple[str, int]] = {}
        self._segment_info: dict[str, dict[str, Any]] = {}
        self._channel_info: dict[str, dict[str, Any]] = {}
        self._wall_order: list[str] = []
        self._states: dict[str, dict[str, Any]] = {}
        self._readable: list[str] = []
        self._effects: dict[str, list[str]] = {}
        self._palettes: dict[str, list[str]] = {}
        self._mismatches: list[str] = []
        self._controller_names: list[str] = []

    # -- public API --------------------------------------------------------

    @property
    def report(self) -> dict[str, Any] | None:
        return self._report

    def status(self) -> dict[str, Any]:
        """Status shape served by ``GET /api/boot`` (design §Interfaces)."""
        with self._lock:
            phase = self._phase
            return {
                "ok": phase != "error",
                "enabled": bool(self.config.get("enabled", True)),
                "running": phase == "running" and self.is_alive(),
                "phase": phase,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "step": dict(self._step) if self._step else None,
                "lines": list(self._lines),
                "report": self._report,
                "error": self._error if phase == "error" else None,
            }

    def stop(self) -> None:
        """Ask the sequence to stop; returns immediately (cooperative)."""
        self._stop_event.set()

    def is_running(self) -> bool:
        with self._lock:
            return self._phase == "running" and self.is_alive()

    # -- thread body -------------------------------------------------------

    def run(self) -> None:
        self._t0 = self._clock()
        self._started_at = _utc_now_iso()
        with self._lock:
            self._phase = "running"
        try:
            self._snapshot()
        except Exception as exc:  # defensive: never let run() raise
            self._record_error(f"snapshot failed: {exc}")
        try:
            if not self._readable:
                self._fail("no controller state could be read")
            elif not self._wall_order:
                self._fail("no wall channels are configured")
            else:
                try:
                    self._animate()
                except Exception as exc:
                    self._fail(f"animation failed: {exc}")
        finally:
            try:
                self._restore()
            except Exception as exc:
                self._record_error(f"restore failed: {exc}")
                self._restored = {
                    "ok": False,
                    "applied": {},
                    "errors": [str(exc)],
                    "skipped": False,
                }
            try:
                self._finish()
            except Exception as exc:  # last resort: status must still settle
                logger.exception("Boot sequence finalization failed")
                self._finished_at = _utc_now_iso()
                with self._lock:
                    self._errors.append(f"finalization failed: {exc}")
                    self._error = f"finalization failed: {exc}"
                    self._phase = "error"
                    self._step = None

    # -- helpers -----------------------------------------------------------

    def _record_error(self, message: str) -> None:
        logger.warning("Boot sequence: %s", message)
        with self._lock:
            self._errors.append(str(message))

    def _fail(self, message: str) -> None:
        """Record a terminal error and move to the ``error`` phase."""
        self._record_error(message)
        with self._lock:
            self._phase = "error"
            self._error = str(message)

    def _record_result_errors(self, label: str, result: Any) -> None:
        if not isinstance(result, dict):
            return
        for controller, entry in result.items():
            if isinstance(entry, dict) and entry.get("ok") is False:
                reason = entry.get("error") or "controller rejected the write"
                self._record_error(f"{label} on {controller}: {reason}")

    def _wait(self, seconds: float) -> None:
        """Cooperative dwell: sleep in small chunks so stop() interrupts."""
        remaining = float(seconds)
        while remaining > 0:
            if self._stop_event.is_set():
                return
            chunk = min(_MAX_SLEEP_CHUNK_S, remaining)
            self._sleep(chunk)
            remaining -= chunk

    # -- snapshot ----------------------------------------------------------

    def _snapshot(self) -> None:
        self._read_topology()
        self._read_channel_map()
        self._read_states()
        if self._observe is None:
            self._read_effects_and_palettes()
            self._read_mismatches()
        self._capture_channels()

    def _read_topology(self) -> None:
        try:
            topology = self.fleet.topology_dict()
        except Exception as exc:
            self._record_error(f"topology unavailable: {exc}")
            topology = {}
        self._topology = topology if isinstance(topology, dict) else {}

    def _topology_controllers(self) -> dict[str, dict[str, Any]]:
        entries: dict[str, dict[str, Any]] = {}
        for controller in self._topology.get("controllers") or []:
            if not isinstance(controller, dict):
                continue
            name = str(controller.get("name") or "").strip()
            if name:
                entries[name] = controller
        return entries

    def _read_channel_map(self) -> None:
        try:
            raw_channels = self.fleet.channels()
        except Exception as exc:
            self._record_error(f"channel map unavailable: {exc}")
            raw_channels = {}
        if not isinstance(raw_channels, dict):
            raw_channels = {}
        channel_map: dict[str, tuple[str, int]] = {}
        for name, mapping in raw_channels.items():
            try:
                controller, segment = mapping
                channel_map[str(name)] = (str(controller), int(segment))
            except (TypeError, ValueError):
                continue
        self._channel_map = channel_map

        wall_order: list[str] = []
        configured_wall = getattr(self.fleet, "WALL_ORDER", None) or _WALL_ORDER
        for channel in configured_wall:
            channel = str(channel)
            if channel in channel_map and channel not in wall_order:
                wall_order.append(channel)
        for channel in channel_map:
            if channel not in wall_order:
                wall_order.append(channel)
        self._wall_order = wall_order

        topology_controllers = self._topology_controllers()
        segment_info: dict[str, dict[str, Any]] = {}
        for controller_name, controller in topology_controllers.items():
            segments = controller.get("segments") or {}
            if not isinstance(segments, dict):
                continue
            for raw_id, segment in segments.items():
                if not isinstance(segment, dict):
                    continue
                channel = str(segment.get("channel") or "").strip()
                if not channel:
                    continue
                segment_info[channel] = {
                    "controller": controller_name,
                    "segment": _as_int(raw_id),
                    "pixels": segment.get("pixels"),
                    "gpio": segment.get("gpio"),
                    "start": segment.get("start"),
                    "stop": segment.get("stop"),
                }
        for channel, (controller, segment) in channel_map.items():
            info = dict(segment_info.get(channel) or {})
            info["controller"] = controller
            info["segment"] = segment
            info.setdefault("pixels", None)
            info.setdefault("gpio", None)
            info.setdefault("start", None)
            info.setdefault("stop", None)
            segment_info[channel] = info
        self._segment_info = segment_info

    def _read_states(self) -> None:
        if self._observe is not None:
            try:
                observed = self._observe(self.fleet)
            except Exception as exc:
                self._record_error(f"observation failed: {exc}")
                observed = {}
            observed = observed if isinstance(observed, dict) else {}
            states = observed.get("states") or {}
            self._effects = self._normalize_name_lists(observed.get("effects"))
            self._palettes = self._normalize_name_lists(observed.get("palettes"))
            self._mismatches = [str(item) for item in (observed.get("mismatches") or [])]
        else:
            try:
                states = self.fleet.get_state()
            except Exception as exc:
                self._record_error(f"controller state read failed: {exc}")
                states = {}
        if not isinstance(states, dict):
            states = {}
        self._states = {
            str(name): dict(state) for name, state in states.items() if isinstance(state, dict)
        }
        self._readable = [
            name for name, state in self._states.items() if state and not state.get("error")
        ]

        names: list[str] = []
        for controller in self._topology.get("controllers") or []:
            if isinstance(controller, dict) and controller.get("name"):
                names.append(str(controller["name"]))
        for channel in self._wall_order:
            info = self._segment_info.get(channel) or {}
            controller_name = info.get("controller")
            if controller_name:
                names.append(str(controller_name))
        names.extend(self._states)
        self._controller_names = list(dict.fromkeys(names))

    @staticmethod
    def _normalize_name_lists(raw: Any) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        if not isinstance(raw, Mapping):
            return result
        for name, values in raw.items():
            if isinstance(values, (list, tuple)):
                result[str(name)] = [str(item) for item in values]
        return result

    def _read_effects_and_palettes(self) -> None:
        clients = getattr(self.fleet, "clients", None)
        if not isinstance(clients, dict):
            return
        for controller_name in self._controller_names:
            client = clients.get(controller_name)
            if client is None:
                continue
            if controller_name not in self._effects:
                getter = getattr(client, "get_effects", None)
                if callable(getter):
                    try:
                        effects = getter()
                    except Exception as exc:
                        self._record_error(f"effects from {controller_name!r} unavailable: {exc}")
                    else:
                        if isinstance(effects, (list, tuple)):
                            self._effects[controller_name] = [str(item) for item in effects]
            if controller_name not in self._palettes:
                getter = getattr(client, "get_palettes", None)
                if callable(getter):
                    try:
                        palettes = getter()
                    except Exception as exc:
                        self._record_error(f"palettes from {controller_name!r} unavailable: {exc}")
                    else:
                        if isinstance(palettes, (list, tuple)):
                            self._palettes[controller_name] = [str(item) for item in palettes]

    def _read_mismatches(self) -> None:
        # ``fleet.topology_mismatches(fleet)``: a real fleet has both
        # ``controllers`` and ``clients``; lighter fakes simply skip the check.
        if not (hasattr(self.fleet, "controllers") and hasattr(self.fleet, "clients")):
            return
        try:
            import fleet as fleet_module

            checker = getattr(fleet_module, "topology_mismatches", None)
            if callable(checker):
                problems = checker(self.fleet)
                self._mismatches = [str(item) for item in (problems or [])]
        except Exception as exc:
            self._record_error(f"topology mismatch check failed: {exc}")

    def _find_segment(self, state: Mapping[str, Any] | None, segment: int) -> dict[str, Any] | None:
        if not isinstance(state, Mapping):
            return None
        for entry in state.get("seg") or []:
            if not isinstance(entry, dict):
                continue
            if _as_int(entry.get("id")) == int(segment):
                return entry
        return None

    def _capture_channels(self) -> None:
        channel_info: dict[str, dict[str, Any]] = {}
        for channel in self._wall_order:
            info = dict(self._segment_info.get(channel) or {})
            info.setdefault("controller", "")
            info.setdefault("segment", 0)
            state = self._states.get(info["controller"])
            readable = info["controller"] in self._readable
            error: str | None = None
            program: dict[str, Any] | None = None
            if not readable:
                error = (
                    (state or {}).get("error")
                    if isinstance(state, dict)
                    else None
                ) or f"controller {info['controller']!r} is not readable"
            else:
                segment = self._find_segment(state, int(info["segment"]))
                if segment is None:
                    error = f"segment {info['segment']} is missing from the controller state"
                    readable = False
                else:
                    program = {
                        key: copy.deepcopy(segment[key])
                        for key in _CAPTURE_SEG_KEYS
                        if key in segment
                    }
                    program["effect"] = self._resolve_effect_name(
                        info["controller"], program.get("fx")
                    )
                    program["palette"] = self._resolve_palette_name(
                        info["controller"], program.get("pal")
                    )
            info.update(
                {
                    "readable": readable,
                    "error": error,
                    "program": program,
                    "line": None,
                }
            )
            channel_info[channel] = info
        self._channel_info = channel_info

    def _resolve_effect_name(self, controller: str, effect: Any) -> str | None:
        names = self._effects.get(controller) or []
        index = _as_int(effect)
        if index is not None and 0 <= index < len(names):
            return names[index]
        return None

    def _resolve_palette_name(self, controller: str, palette: Any) -> str | None:
        names = self._palettes.get(controller) or []
        index = _as_int(palette)
        if index is not None and 0 <= index < len(names):
            return names[index]
        if index == 0:
            return "Default"  # WLED palette 0 has a stable name
        return None

    # -- animation ---------------------------------------------------------

    def _post_global(self) -> None:
        payload = {"on": True, "bri": self.config["brightness"]}
        try:
            result = self.fleet.post_state(payload)
        except Exception as exc:
            self._record_error(f"global power/brightness write failed: {exc}")
            return
        self._wrote = True
        self._record_result_errors("global power/brightness", result)

    def _apply_step(self, channel: str) -> None:
        import columns  # lazy: boot only needs columns while lighting the wall

        specs = []
        for position, other in enumerate(self._wall_order):
            active = other == channel
            specs.append(
                {
                    "channel": other,
                    "on": active,
                    "fx": 0,
                    "col": list(STEP_COLORS[position % len(STEP_COLORS)])
                    if active
                    else [0, 0, 0],
                }
            )
        try:
            result = columns.per_strip(self.fleet, specs)
        except Exception as exc:
            self._record_error(f"channel {channel}: could not be lit: {exc}")
            return
        self._wrote = True
        self._record_result_errors(f"channel {channel}", result)

    def _channel_line(self, index: int, total: int, channel: str) -> str:
        info = self._channel_info.get(channel) or {}
        program = info.get("program") or {}
        controller = str(info.get("controller") or "")
        segment = info.get("segment", 0)
        # State what was programmed BEFORE the boot look replaced it, so the
        # report/articulation remembers each strip's real programming.
        return _format_channel_line(
            index,
            total,
            channel,
            controller=controller,
            segment=int(segment) if segment is not None else 0,
            pixels=info.get("pixels"),
            effect_name=program.get("effect"),
            palette_name=program.get("palette"),
            sx=program.get("sx"),
            ix=program.get("ix"),
            effect_id=program.get("fx"),
            palette_id=program.get("pal"),
            color=program.get("col"),
        )

    def _animate(self) -> None:
        total = len(self._wall_order)
        self._post_global()
        for index, channel in enumerate(self._wall_order, start=1):
            if self._stop_event.is_set():
                self._stopped = True
                break
            line = self._channel_line(index, total, channel)
            step = {"index": index, "total": total, "channel": channel, "text": line}
            with self._lock:
                self._step = step
            self._apply_step(channel)
            with self._lock:
                self._lines.append(line)
                self._steps.append(dict(step))
                self._channel_info.setdefault(channel, {})["line"] = line
            self._wait(self.config["dwell_s"])
        if self._stop_event.is_set():
            self._stopped = True
        with self._lock:
            self._step = None

    # -- restore -----------------------------------------------------------

    def _restore(self) -> None:
        with self._lock:
            if self._restore_done:
                return
            self._restore_done = True
        if not self._readable:
            self._restored = {"ok": True, "applied": {}, "errors": [], "skipped": True}
            return
        if not self._wrote:
            # Nothing was written, so the pre-boot state is still in place.
            self._restored = {"ok": True, "applied": {}, "errors": [], "skipped": True}
            return
        applied: dict[str, Any] = {}
        errors: list[str] = []
        for controller_name in self._readable:
            state = self._states.get(controller_name) or {}
            payload: dict[str, Any] = {}
            for key in ("on", "bri"):
                if key in state:
                    payload[key] = copy.deepcopy(state[key])
            segments = []
            for segment in state.get("seg") or []:
                if not isinstance(segment, dict) or "id" not in segment:
                    continue
                entry: dict[str, Any] = {"id": copy.deepcopy(segment["id"])}
                for key in _RESTORE_SEG_KEYS:
                    if key in segment:
                        entry[key] = copy.deepcopy(segment[key])
                segments.append(entry)
            if segments:
                payload["seg"] = segments
            if not payload:
                continue
            try:
                result = self.fleet.post_state(payload, target=controller_name)
            except Exception as exc:
                errors.append(f"restore {controller_name}: {exc}")
                continue
            applied[controller_name] = result
            if isinstance(result, dict):
                for name, entry in result.items():
                    if isinstance(entry, dict) and entry.get("ok") is False:
                        errors.append(
                            f"restore {name}: {entry.get('error') or 'controller rejected the write'}"
                        )
        self._restored = {
            "ok": not errors,
            "applied": applied,
            "errors": errors,
            "skipped": not applied,
        }

    # -- report + persistence ---------------------------------------------

    def _append_final_line(self) -> None:
        total = len(self._wall_order)
        done = len(self._steps)
        with self._lock:
            phase = self._phase
        if phase == "error":
            reason = self._error or (self._errors[0] if self._errors else "unknown error")
            line = f"Boot self-config failed — {reason}"
        elif self._stopped:
            suffix = (
                "pre-boot state unchanged"
                if (self._restored or {}).get("skipped")
                else "pre-boot state restored"
            )
            line = f"Boot self-config stopped after {done}/{total} strips — {suffix}"
        elif (self._restored or {}).get("skipped") and not (self._restored or {}).get("applied"):
            line = f"Boot self-config complete — {done}/{total} strips verified, nothing to restore"
        else:
            line = (
                f"Boot self-config complete — {done}/{total} strips verified, "
                "pre-boot state restored"
            )
        with self._lock:
            self._lines.append(line)

    def _capabilities(self) -> dict[str, Any]:
        per_controller: dict[str, dict[str, int]] = {}
        for controller_name in self._controller_names:
            effects = self._effects.get(controller_name)
            if not effects:
                continue
            audio_reactive = 0
            try:
                import lightctl  # lazy: only needed to classify effect names

                catalog = lightctl.build_effect_catalog([str(name) for name in effects])
                groups = catalog.get("groups") or {}
                audio_reactive = len(groups.get("audio-reactive") or [])
            except Exception:
                audio_reactive = 0
            per_controller[controller_name] = {
                "effect_count": len(effects),
                "audio_reactive_count": audio_reactive,
            }
        effect_count = max(
            (entry["effect_count"] for entry in per_controller.values()), default=0
        )
        audio_reactive_count = max(
            (entry["audio_reactive_count"] for entry in per_controller.values()), default=0
        )
        palette_count = max(
            (len(names) for names in self._palettes.values() if names), default=0
        )
        return {
            "effect_count": effect_count,
            "audio_reactive_count": audio_reactive_count,
            "palette_count": palette_count,
            "wall_modes": list(_WALL_MODES),
            "controls": [
                "per-strip: one channel, any subset, or all channels; each strip keeps "
                "its own effect, palette, colors, speed and intensity",
                "options: fx (effect id), pal (palette id), col (RGB), sx (speed), "
                "ix (intensity), on (bool), bri (segment brightness)",
                "wall modes: " + ", ".join(_WALL_MODES),
            ],
            "per_controller": per_controller,
        }

    def _controllers_report(self) -> dict[str, dict[str, Any]]:
        topology_controllers = self._topology_controllers()
        report: dict[str, dict[str, Any]] = {}
        for controller_name in self._controller_names:
            state = self._states.get(controller_name)
            topology = topology_controllers.get(controller_name) or {}
            entry: dict[str, Any] = {
                "host": topology.get("host"),
                "reachable": controller_name in self._readable,
            }
            segments = topology.get("segments")
            if isinstance(segments, dict):
                entry["segments"] = copy.deepcopy(segments)
            if isinstance(state, dict) and state.get("error"):
                entry["error"] = str(state["error"])
            if controller_name in self._readable and isinstance(state, dict):
                observed: dict[str, Any] = {}
                for segment in state.get("seg") or []:
                    if not isinstance(segment, dict) or "id" not in segment:
                        continue
                    observed[str(segment["id"])] = {
                        key: copy.deepcopy(segment[key])
                        for key in _CAPTURE_SEG_KEYS
                        if key in segment
                    }
                entry["program"] = observed
            report[controller_name] = entry
        return report

    def _channels_report(self) -> dict[str, dict[str, Any]]:
        report: dict[str, dict[str, Any]] = {}
        for index, channel in enumerate(self._wall_order, start=1):
            info = self._channel_info.get(channel) or {}
            entry: dict[str, Any] = {
                "index": index,
                "controller": info.get("controller"),
                "segment": info.get("segment"),
                "pixels": info.get("pixels"),
                "gpio": info.get("gpio"),
                "readable": bool(info.get("readable")),
                "program": copy.deepcopy(info.get("program")),
                "color": list(STEP_COLORS[(index - 1) % len(STEP_COLORS)]),
                "line": info.get("line"),
            }
            if info.get("error"):
                entry["error"] = info["error"]
            report[channel] = entry
        return report

    def _build_report(self) -> dict[str, Any]:
        duration = None
        if self._t0 is not None:
            try:
                duration = round(max(0.0, self._clock() - self._t0), 3)
            except Exception:
                duration = None
        with self._lock:
            phase = "error" if self._phase == "error" else "done"
            lines = list(self._lines)
            steps = copy.deepcopy(self._steps)
            errors = list(self._errors)
        return {
            "schema": REPORT_SCHEMA,
            "phase": phase,
            "stopped": bool(self._stopped),
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "duration_s": duration,
            "config": dict(self.config),
            "config_digest": _config_digest(self.config),
            "wall_order": list(self._wall_order),
            "controllers": self._controllers_report(),
            "channels": self._channels_report(),
            "capabilities": self._capabilities(),
            "mismatches": list(self._mismatches),
            "errors": errors,
            "lines": lines,
            "steps": steps,
            "restored": copy.deepcopy(
                self._restored
                or {"ok": False, "applied": {}, "errors": ["restore did not run"], "skipped": False}
            ),
        }

    def _report_path(self) -> Path:
        if self.report_path is not None:
            return Path(self.report_path)
        return _default_report_path()

    def _persist_report(self, report: dict[str, Any]) -> str | None:
        try:
            path = self._report_path()
        except Exception as exc:
            self._record_error(f"could not resolve boot report path: {exc}")
            return None
        report["report_path"] = str(path)
        try:
            _atomic_write_json(path, report)
        except Exception as exc:  # persistence failures are recorded, never fatal
            self._record_error(f"could not persist boot report: {exc}")
            with self._lock:
                report["errors"] = list(self._errors)
            return None
        return str(path)

    def _finish(self) -> None:
        with self._lock:
            if self._phase != "error":
                self._phase = "done"
            self._step = None
        self._finished_at = _utc_now_iso()
        self._append_final_line()
        report = self._build_report()
        self._persist_report(report)
        with self._lock:
            report["errors"] = list(self._errors)
            self._report = report
            self._phase = "error" if report["phase"] == "error" else "done"
            if self._phase == "error" and self._error is None and self._errors:
                self._error = self._errors[0]
        _remember_report(report)


# ---------------------------------------------------------------------------
# Module-level registry (one boot sequence at a time)
# ---------------------------------------------------------------------------

_registry_lock = threading.Lock()
_current_boot: BootSequence | None = None
_last_report: dict[str, Any] | None = None
_last_enabled = bool(DEFAULTS["enabled"])
_last_error: str | None = None


def _remember_report(report: dict[str, Any]) -> None:
    global _last_report
    with _registry_lock:
        _last_report = report


def _join_boot(boot: BootSequence | None) -> None:
    """Join a boot thread that was actually started; never raise."""
    if boot is None:
        return
    if not boot.is_alive() and boot.ident is None:
        return
    try:
        boot.join(timeout=10.0)
    except RuntimeError:  # pragma: no cover - defensive
        logger.debug("Boot sequence join skipped (thread never started)")


def start_boot(
    fleet: Any,
    config: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> BootSequence | None:
    """Start the boot sequence, replacing any sequence already running.

    Returns ``None`` when ``boot_sequence.enabled`` is false (or the thread
    could not be started); the status registry still reflects the config.
    """
    global _current_boot, _last_enabled, _last_error

    validated = validate_config(config)
    with _registry_lock:
        _last_enabled = validated["enabled"]
        _last_error = None
        old = _current_boot
        if not validated["enabled"]:
            _current_boot = None
    if not validated["enabled"]:
        if old is not None:
            old.stop()
            _join_boot(old)
        return None

    if old is not None:
        old.stop()
        _join_boot(old)

    boot = BootSequence(fleet, validated, **kwargs)
    with _registry_lock:
        _current_boot = boot
        try:
            boot.start()
        except Exception as exc:  # a failed start must not poison the registry
            if _current_boot is boot:
                _current_boot = None
            _last_error = str(exc)
            logger.warning("Boot sequence could not start: %s", exc)
            return None
    return boot


def stop_boot() -> str:
    """Stop the running boot sequence, if any."""
    with _registry_lock:
        boot = _current_boot
    if boot is None or not boot.is_alive():
        return "No boot sequence is running."
    boot.stop()
    _join_boot(boot)
    return "Boot sequence stopped."


def boot_status() -> dict[str, Any]:
    """Status of the registered boot sequence (idle shape when none ran)."""
    with _registry_lock:
        boot = _current_boot
        last_report = _last_report
        enabled = _last_enabled
        last_error = _last_error
    if boot is not None:
        return boot.status()
    return {
        "ok": last_error is None,
        "enabled": enabled,
        "running": False,
        "phase": "error" if last_error else "idle",
        "started_at": None,
        "finished_at": None,
        "step": None,
        "lines": [],
        "report": last_report,
        "error": last_error,
    }


def latest_report() -> dict[str, Any] | None:
    """Most recent boot report (in-memory registry), or ``None``."""
    with _registry_lock:
        boot = _current_boot
        last_report = _last_report
    if boot is not None and boot.report is not None:
        return boot.report
    return last_report


def summary_text(report: Mapping[str, Any] | None) -> str:
    """Compact human summary (at most 8 lines) of a boot report."""
    if not isinstance(report, Mapping):
        return "No boot report available."
    channels = report.get("channels") or {}
    controllers = report.get("controllers") or {}
    wall_order = report.get("wall_order") or []
    steps = report.get("steps") or []
    phase = report.get("phase") or "unknown"
    controller_count = len(controllers) if isinstance(controllers, Mapping) else 0
    reachable = (
        sum(
            1
            for entry in controllers.values()
            if isinstance(entry, Mapping) and entry.get("reachable")
        )
        if isinstance(controllers, Mapping)
        else 0
    )
    duration = report.get("duration_s")
    duration_text = f"{duration:g}s" if isinstance(duration, (int, float)) else "?"
    lines = [
        f"Boot self-config {phase} — {len(steps)}/{len(channels)} strips lit · "
        f"{reachable}/{controller_count} controllers reachable · {duration_text}",
    ]
    lines.append(
        "Wall order: " + (" → ".join(str(channel) for channel in wall_order) or "unknown")
    )
    channel_lines = [
        str(entry.get("line"))
        for entry in (channels.values() if isinstance(channels, Mapping) else [])
        if isinstance(entry, Mapping) and entry.get("line")
    ]
    lines.extend(channel_lines[:4])
    restored = report.get("restored") or {}
    restored_text = "skipped" if restored.get("skipped") else ("yes" if restored.get("ok") else "failed")
    lines.append(
        f"Restored pre-boot look: {restored_text} · "
        f"mismatches: {len(report.get('mismatches') or [])} · "
        f"errors: {len(report.get('errors') or [])}"
    )
    capabilities = report.get("capabilities") or {}
    modes = capabilities.get("wall_modes") or []
    lines.append(
        f"Capabilities: {capabilities.get('effect_count', 0)} effects "
        f"({capabilities.get('audio_reactive_count', 0)} audio-reactive) · "
        f"modes: {', '.join(str(mode) for mode in modes[:4])}"
    )
    return "\n".join(lines[:8])


def _program_text(entry: Mapping[str, Any]) -> str:
    program = entry.get("program") or {}
    effect = program.get("effect") or "unknown"
    palette = program.get("palette") or "unknown"
    parts = [
        f"fx {effect} ({program.get('fx')})",
        f"pal {palette} ({program.get('pal')})",
    ]
    for key in ("sx", "ix"):
        if program.get(key) is not None:
            parts.append(f"{key} {program[key]}")
    if program.get("on") is not None:
        parts.append(f"on {program['on']}")
    if program.get("bri") is not None:
        parts.append(f"bri {program['bri']}")
    if program.get("start") is not None or program.get("stop") is not None:
        parts.append(f"bounds {program.get('start')}-{program.get('stop')}")
    if program.get("col") is not None:
        parts.append(f"col {program['col']}")
    return " · ".join(parts)


def context_text() -> str:
    """Authoritative AI-context block describing the verified wall, or ``""``.

    Empty when no boot report exists yet, so callers can append it
    unconditionally.
    """
    report = latest_report()
    if not isinstance(report, Mapping):
        return ""
    phase = report.get("phase") or "unknown"
    finished_at = report.get("finished_at") or "unknown"
    restored = report.get("restored") or {}
    restored_text = "skipped" if restored.get("skipped") else ("yes" if restored.get("ok") else "no")
    lines = [
        "=== BOOT SELF-CONFIG (authoritative, read from the controllers at boot) ===",
        f"Verified at {finished_at} · phase {phase} · pre-boot look restored: {restored_text}",
        "Wall order (left → right): "
        + (" → ".join(str(channel) for channel in (report.get("wall_order") or [])) or "unknown"),
    ]
    controllers = report.get("controllers") or {}
    if isinstance(controllers, Mapping):
        controller_bits = [
            f"{name} ({'reachable' if entry.get('reachable') else 'offline'})"
            for name, entry in controllers.items()
            if isinstance(entry, Mapping)
        ]
        if controller_bits:
            lines.append("Controllers: " + ", ".join(controller_bits))
    channels = report.get("channels") or {}
    if isinstance(channels, Mapping) and channels:
        lines.append("Per-strip verified programming:")
        for channel, entry in channels.items():
            if not isinstance(entry, Mapping):
                continue
            geometry = f"segment {entry.get('segment')} · {entry.get('pixels')} px"
            if entry.get("gpio") is not None:
                geometry += f" · GPIO {entry.get('gpio')}"
            program = _program_text(entry) if entry.get("readable") else "unreadable"
            lines.append(f"- {channel} ({entry.get('controller')} {geometry}): {program}")
    capabilities = report.get("capabilities") or {}
    modes = ", ".join(str(mode) for mode in (capabilities.get("wall_modes") or []))
    lines.append(
        f"Capabilities: {capabilities.get('effect_count', 0)} effects "
        f"({capabilities.get('audio_reactive_count', 0)} audio-reactive), "
        f"{capabilities.get('palette_count', 0)} palettes; wall modes: {modes}"
    )
    for control in capabilities.get("controls") or []:
        lines.append(f"- {control}")
    mismatches = report.get("mismatches") or []
    if mismatches:
        lines.append("Topology mismatches (configured vs controller):")
        lines.extend(f"- {problem}" for problem in mismatches)
    else:
        lines.append("Topology mismatches: none")
    errors = report.get("errors") or []
    if errors:
        lines.append("Boot errors recorded: " + "; ".join(str(error) for error in errors))
    lines.append(
        "Use these exact channel names, effects and palettes when controlling the wall."
    )
    return "\n".join(lines)


__all__ = [
    "DEFAULTS",
    "REPORT_SCHEMA",
    "STEP_COLORS",
    "BootSequence",
    "validate_config",
    "start_boot",
    "stop_boot",
    "boot_status",
    "latest_report",
    "summary_text",
    "context_text",
]
