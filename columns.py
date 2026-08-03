#!/usr/bin/env python3
"""Wall-wide composers for the WLED fleet — the "crazy" layer.

Each function takes a fleet.LightFleet and returns its post_state result dict
({controller_name: {"ok": True, "response": ...} | {"ok": False, "error": str}}).
Payloads are built per controller as multi-segment arrays via lightctl builders,
so each controller receives exactly one POST per wall mode.

The fleet is duck-typed: only channels() and post_state() are used, so a
recording fake fleet works in tests without importing fleet.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import lightctl

if TYPE_CHECKING:
    from fleet import LightFleet

# Fallback wall order (physical, left→right). fleet.WALL_ORDER is preferred
# when available so columns stays in sync with the fleet module.
_WALL_ORDER = ["far-left", "middle-left", "middle-right", "far-right"]

# Segment fields that are 0-255 byte values (clamped via lightctl.clamp_byte).
_BYTE_SEG_KEYS = frozenset({
    "sx", "ix", "c1", "c2", "c3", "o1", "o2", "o3", "bri", "grp", "spc", "cct",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wall_channels(fleet: LightFleet) -> list[tuple[str, str, int]]:
    """Return [(channel, controller_name, seg_id)] in physical wall order."""
    channels = fleet.channels()
    wall_order = list(getattr(fleet, "WALL_ORDER", _WALL_ORDER))
    ordered = [c for c in wall_order if c in channels]
    # Channels without a wall alias (e.g. LIGHT_HOSTS env fallback) keep
    # working: they are appended after the known wall columns.
    ordered += [c for c in channels if c not in ordered]
    if not ordered:
        raise ValueError(
            "Wall modes need channel aliases (config 'controllers' with "
            "'segments'); none are configured."
        )
    return [(channel, *channels[channel]) for channel in ordered]


def _seg_entry(fx: int | None, pal: int | None, seg_opts: dict) -> dict:
    """Build one segment entry, clamping byte-valued fields."""
    seg: dict = {}
    if fx is not None:
        seg["fx"] = int(fx)
    if pal is not None:
        seg["pal"] = int(pal)
    for key, value in seg_opts.items():
        if value is None:
            continue
        if key in _BYTE_SEG_KEYS:
            seg[key] = lightctl.clamp_byte(value)
        elif key == "col":
            # Accept a hex string ("FF8800") or a flat RGB list ([255, 0, 0])
            # as a single color, like mcp_light's set_zone does.
            if isinstance(value, str):
                value = [lightctl._hex_to_rgb(value)]
            elif value and isinstance(value[0], (int, float)):
                value = [value]
            seg["col"] = [
                [lightctl.clamp_byte(component) for component in color]
                for color in value
            ]
        else:
            seg[key] = value
    return seg


def _post_per_controller(fleet: LightFleet, payloads: dict[str, dict]) -> dict:
    """POST one payload per controller and merge the result dicts."""
    results: dict = {}
    for controller, payload in payloads.items():
        results.update(fleet.post_state(payload, target=controller))
    return results


def _grouped_entries(
    fleet: LightFleet,
    entry_for_index: Callable[[int], dict],
) -> dict[str, dict]:
    """Build {controller_name: segment_payload} from wall-ordered channels.

    entry_for_index(wall_index) must return one segment entry dict
    (without the "id" field; it is injected here).
    """
    per_controller: dict[str, list[dict]] = {}
    for index, (_channel, controller, seg_id) in enumerate(_wall_channels(fleet)):
        entry = entry_for_index(index)
        entry["id"] = seg_id
        per_controller.setdefault(controller, []).append(entry)
    return {
        controller: lightctl.segment_payload(entries)
        for controller, entries in per_controller.items()
    }


# ---------------------------------------------------------------------------
# Wall modes
# ---------------------------------------------------------------------------

def wall_span(fleet: LightFleet, fx: int, pal: int | None = None, **seg_opts) -> dict:
    """Same fx/pal on all 4 channels (per-controller 2-seg payloads)."""
    payloads = _grouped_entries(fleet, lambda _i: _seg_entry(fx, pal, seg_opts))
    return _post_per_controller(fleet, payloads)


def mirror(fleet: LightFleet, fx: int, pal: int | None = None, **seg_opts) -> dict:
    """Left pair mirrors the right pair (mi=True on the left controller)."""
    wall = _wall_channels(fleet)
    left_controllers = {controller for _c, controller, _s in wall[: len(wall) // 2]}

    def entry_for_index(index: int) -> dict:
        entry = _seg_entry(fx, pal, seg_opts)
        entry["mi"] = wall[index][1] in left_controllers
        return entry

    payloads = _grouped_entries(fleet, entry_for_index)
    return _post_per_controller(fleet, payloads)


def chase(fleet: LightFleet, fx: int, pal: int | None = None, **seg_opts) -> dict:
    """Same fx on all channels, effect offsets staggered along the wall.

    seg_opts may include offset_step (default LEDS_PER_COLUMN // 4 = 10); it is
    consumed here and not sent to WLED. Channel i gets of = i * offset_step.
    Note: WLED wraps `of` modulo the segment length (json.cpp: of %= len), so a
    step >= the segment length is a no-op — the default 10 spreads one 40-pixel
    effect period across the 4 columns (0/10/20/30).
    """
    offset_step = int(seg_opts.pop("offset_step", lightctl.LEDS_PER_COLUMN // 4))

    def entry_for_index(index: int) -> dict:
        entry = _seg_entry(fx, pal, seg_opts)
        entry["of"] = index * offset_step
        return entry

    payloads = _grouped_entries(fleet, entry_for_index)
    return _post_per_controller(fleet, payloads)


def left_vs_right(
    fleet: LightFleet,
    fx_left: int,
    fx_right: int,
    pal_left: int | None = None,
    pal_right: int | None = None,
    **seg_opts,
) -> dict:
    """Different fx/pal on the left pair vs the right pair of columns."""
    wall = _wall_channels(fleet)
    half = len(wall) // 2

    def entry_for_index(index: int) -> dict:
        if index < half:
            return _seg_entry(fx_left, pal_left, seg_opts)
        return _seg_entry(fx_right, pal_right, seg_opts)

    payloads = _grouped_entries(fleet, entry_for_index)
    return _post_per_controller(fleet, payloads)


def set_channel(fleet: LightFleet, channel: str, **seg_opts) -> dict:
    """Thin wrapper: apply raw seg options to a single channel.

    The fleet injects the segment id into the payload when the target is a
    channel, so the entry here is intentionally id-less.
    """
    entry = _seg_entry(None, None, seg_opts)
    return fleet.post_state(lightctl.segment_payload([entry]), target=channel)
