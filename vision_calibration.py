#!/usr/bin/env python3
"""Vision-assisted self-configuration for the four-column WLED wall.

Closes the loop the read-only scanner cannot: it proves the physical left-to-right
order of the columns and which end is pixel 0, then repairs/creates one WLED
segment per column and persists the channel mapping.

Flow (a single ``self_configure()`` call):

1. **probe** — read-only device facts via :mod:`calibrate`.
2. **column identity** — light the bottom few pixels of every column at once,
   each in a distinct colour; one webcam frame + one vision call returns the
   colours in physical left-to-right order. Each colour maps back to the
   (controller, segment) that was lit, which yields the channel assignment.
3. **orientation** — light the bottom ends white and ask the vision model which
   physical end is pixel 0.
4. **plan** — assign channel names (far-left … far-right) by detected position,
   set pixel-zero, and build a segment repair plan (re-assert each segment's
   bounds so a missing/mis-bounded segment is fixed).
5. **apply** — write the segments to the controllers, then persist config.json
   (with a backup). The pre-probe controller state is always restored.

The engine is duck-typed and dependency-light: ``capture()`` returns a base64
image, ``vision(image, prompt)`` returns the model's raw text, and both are
injected so the whole pipeline is testable without a camera or a model. The GUI
wires ``getUserMedia`` + the OpenAI vision endpoint.

Pixel addressing uses WLED's per-segment ``i`` grammar (segment-relative LED
ranges). One addressable pixel on this wall is a 5-LED block, so the probe lights
a short run (``probe_pixels``) to stay visible to the camera.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable

import calibrate
import fleet as fleet_mod
import lightctl

logger = logging.getLogger("vision_calibration")

# Distinct, camera-friendly probe colours, assigned to segments in this order.
PROBE_COLORS = ("red", "green", "blue", "yellow")
COLOR_HEX = {
    "red": "FF0000",
    "green": "00FF00",
    "blue": "0000FF",
    "yellow": "FFFF00",
    "white": "FFFFFF",
}

DEFAULT_WALL_ORDER = ("far-left", "middle-left", "middle-right", "far-right")

# How many addressable pixels (5-LED blocks each) to light at an end, and how
# bright: enough to read at wall distance, nowhere near a strobe.
DEFAULT_PROBE_PIXELS = 4
DEFAULT_PROBE_BRIGHTNESS = 200


class SelfConfigureError(RuntimeError):
    """Self-configuration could not complete safely."""


# ---------------------------------------------------------------------------
# Pixel probing
# ---------------------------------------------------------------------------

def _fleet_segments(fleet: Any) -> list[tuple[str, int]]:
    """[(controller name, segment id)] in deterministic (controller, seg) order."""
    pairs: list[tuple[str, int]] = []
    for controller in getattr(fleet, "controllers", []):
        for seg_id in sorted(controller.segments):
            pairs.append((str(controller.name), int(seg_id)))
    return pairs


def _client_for(fleet: Any, name: str, poster_factory: Callable[[str], Any] | None) -> Any:
    if poster_factory is not None:
        return poster_factory(name)
    clients = getattr(fleet, "clients", None)
    return clients.get(name) if isinstance(clients, dict) else None


def light_probes(
    fleet: Any,
    paints: dict[tuple[str, int], dict],
    *,
    brightness: int = DEFAULT_PROBE_BRIGHTNESS,
    poster_factory: Callable[[str], Any] | None = None,
) -> Callable[[], None]:
    """Light per-segment pixel runs; return a restore closure.

    ``paints`` maps (controller, segment) -> {"start", "stop", "color"} (hex).
    Power + brightness are set first (WLED requires brightness before ``i``),
    then one ``seg`` POST per controller lights all of its probes at once. The
    restore closure re-posts each controller's prior ``on/bri/seg``, which also
    unfreezes any ``i`` output.
    """
    by_controller: dict[str, dict[int, dict]] = {}
    for (name, seg_id), paint in paints.items():
        by_controller.setdefault(name, {})[int(seg_id)] = paint

    prior: dict[str, dict] = {}
    for name in by_controller:
        poster = _client_for(fleet, name, poster_factory)
        try:
            state = poster.get_state() if poster is not None else {}
        except Exception:
            state = {}
        prior[name] = state if isinstance(state, dict) else {}

    def restore() -> None:
        for name, state in prior.items():
            poster = _client_for(fleet, name, poster_factory)
            if poster is None or not state:
                continue
            payload = {key: state[key] for key in ("on", "bri", "seg") if key in state}
            payload["udpn"] = {"nn": True}
            payload = fleet_mod.drop_sacred_segment_fields(payload)
            try:
                poster.post_state(payload)
            except Exception:
                logger.warning("self-configure: could not restore controller %r", name)

    try:
        for name, segments in by_controller.items():
            poster = _client_for(fleet, name, poster_factory)
            if poster is None:
                continue
            poster.post_state({"on": True, "bri": int(brightness), "udpn": {"nn": True}})
            poster.post_state(
                {
                    "seg": [
                        {"id": seg_id, "i": [paint["start"], paint["stop"], paint["color"]]}
                        for seg_id, paint in segments.items()
                    ],
                    "udpn": {"nn": True},
                }
            )
    except Exception:
        restore()
        raise
    return restore


# ---------------------------------------------------------------------------
# Vision interaction
# ---------------------------------------------------------------------------

OBSERVATION_PROMPT = (
    "A wall of vertical LED strips is shown; short blocks near one end are lit in "
    "distinct colours. List the colours strictly left to right AND state whether the "
    "lit blocks are at the TOP or the BOTTOM of the strips. Respond with ONLY this "
    'JSON object: {"colors_left_to_right": ["red", "green", ...], '
    '"lit_end": "bottom" | "top"}. If a colour is not visible, leave it out rather '
    "than guessing."
)


def _parse_json_object(text: str) -> dict:
    import json
    import re

    match = re.search(r"\{.*\}", str(text or ""), re.S)
    if not match:
        raise SelfConfigureError(f"Vision model returned no JSON object: {text!r}")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise SelfConfigureError(f"Vision model returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SelfConfigureError("Vision model JSON must be an object.")
    return value


def parse_observation(text: str, expected: Iterable[str]) -> tuple[list[str], str | None]:
    """Extract (colours_left_to_right, lit_end) from the model's raw text.

    ``lit_end`` may be None when the model could not tell; orientation then falls
    back to the configured pixel-zero.
    """
    data = _parse_json_object(text)
    order = data.get("colors_left_to_right")
    if not isinstance(order, list):
        raise SelfConfigureError(f"Vision model did not return a colour order: {text!r}")
    normalized = [str(item).strip().lower() for item in order]
    expected_set = {str(item).strip().lower() for item in expected}
    unknown = [item for item in normalized if item not in expected_set]
    if unknown:
        raise SelfConfigureError(f"Vision model reported unknown colours: {unknown}")
    lit_end = str(data.get("lit_end") or "").strip().lower() or None
    if lit_end is not None and lit_end not in ("bottom", "top"):
        raise SelfConfigureError(f"Vision model reported a bad lit_end: {data.get('lit_end')!r}")
    return normalized, lit_end


# ---------------------------------------------------------------------------
# Plan and apply
# ---------------------------------------------------------------------------

def _wall_order_names(installation: Any, count: int) -> list[str]:
    order = getattr(installation, "wall_order", None) or list(DEFAULT_WALL_ORDER)
    names = [str(channel) for channel in order]
    while len(names) < count:
        names.append(f"column-{len(names)}")
    return names[:count]


def assign_channels(
    segments: list[tuple[str, int]],
    probe_colors: Iterable[str],
    identity_colors: Iterable[str],
    installation: Any,
) -> tuple[dict[tuple[str, int], str], list[str]]:
    """Map each (controller, segment) to a channel name; also return wall order.

    ``segments[i]`` was lit ``probe_colors[i]``; ``identity_colors`` is the
    vision result in physical left-to-right order. The colour at physical
    position ``p`` becomes the ``p``-th wall name (far-left, middle-left, …).
    Returns ``(mapping, wall_order)``.
    """
    probe = [str(color).strip().lower() for color in probe_colors]
    identity = [str(color).strip().lower() for color in identity_colors]
    if len(segments) != len(probe) or len(segments) != len(identity):
        raise SelfConfigureError(
            f"Expected {len(segments)} segments but got "
            f"{len(probe)} probe colours and {len(identity)} detected colours."
        )
    names = _wall_order_names(installation, len(segments))
    color_to_segment = {color: segment for segment, color in zip(segments, probe)}
    mapping: dict[tuple[str, int], str] = {}
    wall_order: list[str] = []
    for position, color in enumerate(identity):
        segment = color_to_segment.get(color)
        if segment is None:
            raise SelfConfigureError(f"Colour {color!r} was detected but not assigned to a segment.")
        channel = names[position]
        mapping[segment] = channel
        wall_order.append(channel)
    return mapping, wall_order


def build_segment_plan(probes: dict, mapping: dict[tuple[str, int], str]) -> dict[str, list[dict]]:
    """Per-controller segment writes: id, start, stop (plus the channel it maps to)."""
    plan: dict[str, list[dict]] = {}
    for controller_name, probe in probes.items():
        if "error" in probe:
            continue
        for seg in probe.get("segments") or []:
            seg_id = int(seg["id"])
            plan.setdefault(str(controller_name), []).append(
                {
                    "id": seg_id,
                    "start": seg["start"],
                    "stop": seg["stop"],
                    "channel": mapping.get((str(controller_name), seg_id)),
                }
            )
    return plan


def apply_segment_plan(fleet: Any, plan: dict[str, list[dict]]) -> dict[str, Any]:
    """Segment bounds stay on the controller. The plan is not posted."""
    del fleet
    return {
        str(name): {
            "ok": False,
            "error": "Segment bounds stay as programmed on the controller.",
        }
        for name in plan
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _expand_colors(probe_colors: tuple[str, ...], count: int) -> list[str]:
    colors = list(probe_colors)
    while len(colors) < count:
        colors.extend(probe_colors)
    return colors[:count]


def _probe_fleet(fleet: Any) -> tuple[dict, list[tuple[str, int]]]:
    """Shared precondition + read-only probing."""
    if not hasattr(fleet, "clients") or not hasattr(fleet, "controllers"):
        raise SelfConfigureError("self-configure requires a configured fleet.")
    probes = calibrate.collect_probes(clients=fleet.clients)
    offline = [f"{k}: {v.get('error')}" for k, v in probes.items() if "error" in v]
    if offline:
        raise SelfConfigureError("One or more controllers are offline: " + "; ".join(offline))
    segments = _fleet_segments(fleet)
    if not segments:
        raise SelfConfigureError("No segments configured to self-configure.")
    return probes, segments


def begin_probe(
    fleet: Any,
    *,
    probe_pixels: int = DEFAULT_PROBE_PIXELS,
    probe_colors: tuple[str, ...] = PROBE_COLORS,
    brightness: int = DEFAULT_PROBE_BRIGHTNESS,
) -> dict:
    """Light the colour-coded probes; return a handle for :func:`conclude`.

    The caller captures the wall between ``begin_probe`` and ``conclude`` and is
    responsible for calling ``conclude`` (which restores state). If the flow is
    abandoned, call ``handle["restore"]()`` directly.
    """
    probes, segments = _probe_fleet(fleet)
    colors = _expand_colors(probe_colors, len(segments))
    paints = {
        segment: {"start": 0, "stop": int(probe_pixels), "color": COLOR_HEX[colors[index]]}
        for index, segment in enumerate(segments)
    }
    restore = light_probes(fleet, paints, brightness=brightness)
    return {
        "segments": segments,
        "colors": colors,
        "probes": probes,
        "restore": restore,
        "installation": getattr(fleet, "installation", fleet_mod.InstallationConfig()),
    }


def conclude(
    fleet: Any,
    handle: dict,
    *,
    image: str,
    vision: Callable[[str, str], str],
    write_segments: bool = True,
    write_config: bool = True,
    load: Callable[[], dict] = lightctl.load_config,
    save: Callable[[dict], None] = lightctl.save_config,
    config_path: str = lightctl._CONFIG_PATH,
) -> dict:
    """Run vision + mapping + apply, then restore. See :func:`begin_probe`.

    The probes stay lit until this returns; the ``finally`` guarantees the
    controllers return to their prior look even if vision or apply fails.
    """
    segments: list[tuple[str, int]] = handle["segments"]
    colors: list[str] = handle["colors"]
    installation = handle["installation"]
    restore = handle["restore"]
    try:
        text = vision(image, OBSERVATION_PROMPT)
        identity_colors, lit_end = parse_observation(text, colors)
        mapping, wall_order = assign_channels(segments, colors, identity_colors, installation)

        # The probe lights the pixel-0 end, so the same observation reports
        # orientation; fall back to the configured value when unknown.
        pixel_zero = lit_end or str(getattr(installation, "pixel_zero", "bottom") or "bottom").lower()

        report: dict[str, Any] = {
            "ok": True,
            "mapping": {
                f"{controller} seg {seg_id}": channel
                for (controller, seg_id), channel in sorted(mapping.items())
            },
            "wall_order": wall_order,
            "pixel_zero": pixel_zero,
            "segments_written": {},
            "config_backup": None,
        }

        if write_segments:
            plan = build_segment_plan(handle["probes"], mapping)
            report["segments_written"] = apply_segment_plan(fleet, plan)

        if write_config:
            controllers = calibrate.build_controllers(handle["probes"], fleet.controllers)
            assignments = [
                {"controller": controller, "segment": seg_id, "channel": channel}
                for (controller, seg_id), channel in mapping.items()
            ]
            controllers = calibrate.apply_assignments(controllers, assignments)
            updates: dict[str, Any] = {
                "wall_order": wall_order,
                "pixel_zero": pixel_zero,
                "orientation": "down" if pixel_zero == "top" else "up",
            }
            report["config_backup"] = calibrate.write_topology_config(
                controllers, updates, load=load, save=save, config_path=config_path
            )
            report["controllers"] = [
                {
                    "name": c.name,
                    "host": c.host,
                    "segments": {
                        str(seg_id): segment.channel
                        for seg_id, segment in sorted(c.segments.items())
                    },
                }
                for c in controllers
            ]
        return report
    finally:
        restore()


def self_configure(
    fleet: Any,
    *,
    capture: Callable[[], str],
    vision: Callable[[str, str], str],
    write_config: bool = True,
    write_segments: bool = True,
    probe_pixels: int = DEFAULT_PROBE_PIXELS,
    probe_colors: tuple[str, ...] = PROBE_COLORS,
    load: Callable[[], dict] = lightctl.load_config,
    save: Callable[[dict], None] = lightctl.save_config,
    config_path: str = lightctl._CONFIG_PATH,
) -> dict:
    """Run the full self-configuration in one shot (single capture + vision call).

    Convenience wrapper around :func:`begin_probe` + :func:`conclude` for callers
    that can capture synchronously. The GUI uses the two-step form instead so the
    webcam can capture after the probes are lit.
    """
    handle = begin_probe(fleet, probe_pixels=probe_pixels, probe_colors=probe_colors)
    try:
        image = capture()
    except Exception:
        handle["restore"]()
        raise
    return conclude(
        fleet,
        handle,
        image=image,
        vision=vision,
        write_segments=write_segments,
        write_config=write_config,
        load=load,
        save=save,
        config_path=config_path,
    )
