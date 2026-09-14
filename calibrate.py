#!/usr/bin/env python3
"""Self-calibration for the Lightss installation.

The local fleet config (controllers, channels, pixel counts, orientation) can
drift from what the WLED devices actually run. This module closes the loop:

1. probe — read each controller's live /json/info, /json/state and /json/cfg
   to learn its real LED count, maxseg, bus GPIOs, bus color order, and
   segment bounds. Devices are only READ, never written.
2. build — turn probes into fleet.ControllerConfig entries, preserving
   existing channel names for segments that already have them and naming new
   ones "<controller>-seg<id>".
3. identify — flash one target so a human (or the AI asking the user) can say
   which physical column a segment is and which end LED 0 sits at; the answer
   is applied via calibrate(assignments=..., installation_updates=...).
4. write — persist the calibrated topology to the local config.json; the
   previous file is backed up first. If every probed bus reports the same
   color order, installation.color_order is calibrated along with it.

Everything is stdlib-only and duck-typed: probes work against any object with
get_info()/get_state()/get_config(), so tests inject fakes.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Callable, Iterable

import fleet as fleet_mod
import lightctl

logger = logging.getLogger("calibrate")

# WLED color-order enum (wled00/const.h): 2 == BRG on this installation.
COLOR_ORDER_NAMES = {0: "GRB", 1: "RGB", 2: "BRG", 3: "RBG", 4: "BGR", 5: "GBR"}


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

def _parse_buses(config: dict) -> list[dict]:
    """Bus facts from /json/cfg hw.led.ins: gpio, start, len, color order."""
    buses = []
    ins = ((config.get("hw") or {}).get("led") or {}).get("ins")
    if not isinstance(ins, list):
        return buses
    for bus in ins:
        if not isinstance(bus, dict):
            continue
        pins = bus.get("pin")
        gpio = pins[0] if isinstance(pins, list) and pins else None
        start = bus.get("start")
        length = bus.get("len")
        if not isinstance(start, int) or not isinstance(length, int):
            continue
        order = bus.get("order")
        buses.append(
            {
                "gpio": gpio,
                "start": start,
                "len": length,
                "order": order,
                "color_order": COLOR_ORDER_NAMES.get(order, f"order-{order}"),
            }
        )
    return buses


def _bus_gpio(segment_start: int, segment_stop: int, buses: list[dict]) -> int | None:
    """GPIO of the bus fully containing [segment_start, segment_stop)."""
    for bus in buses:
        if bus["start"] <= segment_start and segment_stop <= bus["start"] + bus["len"]:
            return bus["gpio"]
    return None


def _raw_probe_errors(info: Any, state: Any, config: Any) -> list[str]:
    """Reject incomplete/contradictory declarations before parsers drop them."""
    errors: list[str] = []
    if not isinstance(info, dict) or not isinstance(state, dict) or not isinstance(config, dict):
        return ["info, state, and config responses must be JSON objects"]
    leds = info.get("leds")
    count = leds.get("count") if isinstance(leds, dict) else None
    maxseg = leds.get("maxseg") if isinstance(leds, dict) else None
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        errors.append("declared LED count must be a positive integer")
    if not isinstance(maxseg, int) or isinstance(maxseg, bool) or maxseg <= 0:
        errors.append("declared maxseg must be a positive integer")

    raw_buses = ((config.get("hw") or {}).get("led") or {}).get("ins")
    valid_buses: list[tuple[int, int]] = []
    if not isinstance(raw_buses, list) or not raw_buses:
        errors.append("bus geometry is missing")
    else:
        for index, bus in enumerate(raw_buses):
            if not isinstance(bus, dict):
                errors.append(f"bus {index} is not an object")
                continue
            pins, start, length = bus.get("pin"), bus.get("start"), bus.get("len")
            pin_ok = isinstance(pins, list) and bool(pins) and all(
                isinstance(pin, int) and not isinstance(pin, bool) and pin >= 0 for pin in pins
            )
            geometry_ok = (
                isinstance(start, int) and not isinstance(start, bool) and start >= 0
                and isinstance(length, int) and not isinstance(length, bool) and length > 0
            )
            if not pin_ok or not geometry_ok:
                errors.append(f"bus {index} has malformed pin/start/len geometry")
                continue
            if isinstance(count, int) and count > 0 and start + length > count:
                errors.append(f"bus {index} extends beyond declared LED count")
            valid_buses.append((start, start + length))

    raw_segments = state.get("seg")
    seen_ids: set[int] = set()
    if not isinstance(raw_segments, list) or not raw_segments:
        errors.append("segment geometry is missing")
    else:
        for index, segment in enumerate(raw_segments):
            if not isinstance(segment, dict):
                errors.append(f"segment {index} is not an object")
                continue
            seg_id, start, stop = segment.get("id"), segment.get("start"), segment.get("stop")
            if not isinstance(seg_id, int) or isinstance(seg_id, bool) or seg_id < 0:
                errors.append(f"segment {index} has an invalid id")
                continue
            if seg_id in seen_ids:
                errors.append(f"duplicate segment id {seg_id}")
            seen_ids.add(seg_id)
            if isinstance(maxseg, int) and maxseg > 0 and seg_id >= maxseg:
                errors.append(f"segment id {seg_id} conflicts with maxseg {maxseg}")
            bounds_ok = (
                isinstance(start, int) and not isinstance(start, bool) and start >= 0
                and isinstance(stop, int) and not isinstance(stop, bool) and stop > start
            )
            if not bounds_ok:
                errors.append(f"segment {seg_id} has invalid bounds")
                continue
            if isinstance(count, int) and count > 0 and stop > count:
                errors.append(f"segment {seg_id} extends beyond declared LED count")
            if not any(bus_start <= start and stop <= bus_stop for bus_start, bus_stop in valid_buses):
                errors.append(f"segment {seg_id} is not fully covered by one declared bus")
        if isinstance(maxseg, int) and maxseg > 0 and len(raw_segments) > maxseg:
            errors.append("declared segment count conflicts with maxseg")
    return errors


def probe_client(client: Any) -> dict:
    """Read one controller and return a JSON-ready probe dict.

    Works against any duck-typed client with get_info()/get_state()/
    get_config() (lightctl.LightClient included).
    """
    info = client.get_info()
    state = client.get_state()
    config = client.get_config()
    leds = info.get("leds") if isinstance(info.get("leds"), dict) else {}
    buses = _parse_buses(config if isinstance(config, dict) else {})
    segments = []
    for seg in state.get("seg") or []:
        if not isinstance(seg, dict):
            continue
        start, stop = seg.get("start"), seg.get("stop")
        if not isinstance(start, int) or not isinstance(stop, int) or stop <= start:
            continue
        seg_id = seg.get("id", len(segments))
        segments.append(
            {
                "id": int(seg_id),
                "start": start,
                "stop": stop,
                "pixels": stop - start,
                "gpio": _bus_gpio(start, stop, buses),
                "on": bool(seg.get("on", True)),
            }
        )
    return {
        "host": getattr(client, "host", None),
        "name": info.get("name"),
        "version": info.get("ver"),
        "led_count": leds.get("count"),
        "maxseg": leds.get("maxseg"),
        "buses": buses,
        "segments": segments,
        "validation_errors": _raw_probe_errors(info, state, config),
    }


def probe_host(host: str, timeout: float = 5.0) -> dict:
    """Probe one controller by host (constructs a lightctl.LightClient)."""
    return probe_client(lightctl.LightClient(host, timeout=timeout))


def collect_probes(
    *,
    clients: dict[str, Any] | None = None,
    hosts: Iterable[str] | None = None,
) -> dict[str, dict]:
    """Probe every target; failures become {"error": ...} entries, never raised."""
    probes: dict[str, dict] = {}
    if clients is not None:
        for name, client in clients.items():
            try:
                probe = probe_client(client)
                probe["name"] = probe.get("name") or name
                probes[name] = probe
            except Exception as exc:
                logger.warning("calibrate: probe of %r failed: %s", name, exc)
                probes[name] = {"error": str(exc), "host": getattr(client, "host", None)}
        return probes
    for host in hosts or []:
        host = lightctl.normalize_host(str(host))
        try:
            probes[host] = probe_host(host)
        except Exception as exc:
            logger.warning("calibrate: probe of %s failed: %s", host, exc)
            probes[host] = {"error": str(exc), "host": host}
    return probes


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_controllers(probes: dict[str, dict], existing: list[fleet_mod.ControllerConfig] | None = None) -> list[fleet_mod.ControllerConfig]:
    """Turn probes into ControllerConfig entries, preserving channel names.

    Channel preservation matches on (controller name, segment id) first, then
    on (host, segment id) so renamed controllers keep their mapping.
    """
    by_name = {c.name: c for c in (existing or [])}
    by_host = {c.host: c for c in (existing or [])}
    controllers: list[fleet_mod.ControllerConfig] = []
    for key, probe in probes.items():
        if "error" in probe:
            continue
        host = lightctl.normalize_host(str(probe.get("host") or key))
        prior = by_name.get(str(key)) or by_host.get(host)
        # Name preference: existing config > local key (clients dict name) >
        # device-reported name > host.
        if prior is not None:
            name = prior.name
        elif "://" not in str(key):
            name = str(key)
        else:
            name = str(probe.get("name") or host)
        prior_segments = prior.segments if prior is not None else {}
        segments: dict[int, fleet_mod.SegmentConfig] = {}
        for seg in probe.get("segments") or []:
            seg_id = int(seg["id"])
            prior_seg = prior_segments.get(seg_id)
            channel = (
                prior_seg.channel
                if isinstance(prior_seg, fleet_mod.SegmentConfig) and prior_seg.channel
                else f"{name}-seg{seg_id}"
            )
            segments[seg_id] = fleet_mod.SegmentConfig(
                channel=channel,
                gpio=seg.get("gpio"),
                pixels=seg["pixels"],
                start=seg["start"],
                stop=seg["stop"],
            )
        controllers.append(fleet_mod.ControllerConfig(name=name, host=host, segments=segments))
    return controllers


def apply_assignments(
    controllers: list[fleet_mod.ControllerConfig],
    assignments: Iterable[dict] | None,
) -> list[fleet_mod.ControllerConfig]:
    """Rename channels per {"controller", "segment", "channel"} entries."""
    entries = [a for a in (assignments or []) if isinstance(a, dict)]
    if not entries:
        return controllers
    by_name = {c.name: c for c in controllers}
    for entry in entries:
        controller = by_name.get(str(entry.get("controller") or ""))
        channel = str(entry.get("channel") or "").strip()
        if controller is None or not channel:
            raise ValueError(
                f"calibrate assignment needs a known controller and a channel, got {entry!r}"
            )
        try:
            seg_id = int(entry.get("segment"))
        except (TypeError, ValueError):
            raise ValueError(f"calibrate assignment has a bad segment id in {entry!r}") from None
        segment = controller.segments.get(seg_id)
        if segment is None:
            raise ValueError(
                f"calibrate: controller {controller.name!r} has no segment {seg_id} to assign"
            )
        controller.segments[seg_id] = dataclasses.replace(segment, channel=channel)
    return controllers


def _unanimous_color_order(probes: dict[str, dict]) -> str | None:
    """The single color order every probed bus agrees on, else None."""
    orders = {
        bus["color_order"]
        for probe in probes.values()
        if "error" not in probe
        for bus in probe.get("buses") or []
        if bus.get("color_order")
    }
    return orders.pop() if len(orders) == 1 else None


def _controller_to_json(controller: fleet_mod.ControllerConfig) -> dict:
    segments: dict[str, dict] = {}
    for seg_id, segment in controller.segments.items():
        entry = {"channel": segment.channel}
        if segment.gpio is not None:
            entry["gpio"] = segment.gpio
        if segment.pixels is not None:
            entry["pixels"] = segment.pixels
        if segment.start is not None:
            entry["start"] = segment.start
        if segment.stop is not None:
            entry["stop"] = segment.stop
        segments[str(seg_id)] = entry
    return {"name": controller.name, "host": controller.host, "segments": segments}


def write_topology_config(
    controllers: list[fleet_mod.ControllerConfig],
    installation_updates: dict | None = None,
    *,
    load: Callable[[], dict] = lightctl.load_config,
    save: Callable[[dict], None] = lightctl.save_config,
    config_path: str = lightctl._CONFIG_PATH,
) -> str | None:
    """Persist controllers (+ installation updates) to config.json.

    The previous config file is copied to config.json.bak-<timestamp> first;
    returns the backup path (None when there was nothing to back up).
    """
    config = load()
    config["controllers"] = [_controller_to_json(c) for c in controllers]
    if installation_updates:
        installation = dict(config.get("installation") or {})
        installation.update(installation_updates)
        config["installation"] = installation
    backup_path = None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            previous = f.read()
        backup_path = f"{config_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(previous)
    except FileNotFoundError:
        backup_path = None
    save(config)
    return backup_path


def calibrate(
    *,
    clients: dict[str, Any] | None = None,
    hosts: Iterable[str] | None = None,
    assignments: Iterable[dict] | None = None,
    installation_updates: dict | None = None,
    write: bool = False,
    topology: tuple[fleet_mod.InstallationConfig, list[fleet_mod.ControllerConfig]] | None = None,
    load: Callable[[], dict] = lightctl.load_config,
    save: Callable[[dict], None] = lightctl.save_config,
    config_path: str = lightctl._CONFIG_PATH,
) -> dict:
    """Probe the controllers and report (optionally persist) calibrated topology.

    topology defaults to the currently configured one (its controllers supply
    channel-name preservation and its installation supplies write defaults).
    """
    if topology is None:
        topology = fleet_mod.load_topology()
    installation, existing = topology
    probes = collect_probes(clients=clients, hosts=hosts)
    controllers = build_controllers(probes, existing)
    controllers = apply_assignments(controllers, assignments)

    updates = dict(installation_updates or {})
    if write and "color_order" not in updates:
        color_order = _unanimous_color_order(probes)
        if color_order:
            updates["color_order"] = color_order

    backup_path = None
    if write:
        backup_path = write_topology_config(
            controllers, updates or None, load=load, save=save, config_path=config_path
        )

    return {
        "written": bool(write),
        "config_backup": backup_path,
        "installation": {
            **dataclasses.asdict(installation),
            **updates,
        },
        "controllers": [
            {
                "name": c.name,
                "host": c.host,
                "version": (probes.get(c.name) or probes.get(c.host) or {}).get("version"),
                "led_count": (probes.get(c.name) or probes.get(c.host) or {}).get("led_count"),
                "maxseg": (probes.get(c.name) or probes.get(c.host) or {}).get("maxseg"),
                "segments": [
                    {
                        "id": seg_id,
                        "channel": seg.channel,
                        "gpio": seg.gpio,
                        "pixels": seg.pixels,
                        "start": seg.start,
                        "stop": seg.stop,
                    }
                    for seg_id, seg in sorted(c.segments.items())
                ],
            }
            for c in controllers
        ],
        "errors": [
            {"target": key, "error": probe["error"]}
            for key, probe in probes.items()
            if "error" in probe
        ],
    }


# ---------------------------------------------------------------------------
# Identify (physical location of a target)
# ---------------------------------------------------------------------------

def identify(
    client: Any,
    target: str = "all",
    flashes: int = 4,
    interval_s: float = 0.35,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Flash a target off/on so a human can physically locate it.

    Toggles each resolved segment with WLED's on:"t" (an even number of
    toggles, so segments return to their prior on/off state) and restores the
    master power state afterwards. Works against a LightFleet (target =
    channel/controller/group/combo) or a single LightClient (toggles the main
    segment).
    """
    flashes = max(1, min(12, int(flashes)))
    if hasattr(client, "resolve") and hasattr(client, "channels"):
        pairs = [(client.clients[name], seg_id) for name, seg_id in client.resolve(target)]
    else:
        pairs = [(client, None)]

    was_on: dict[int, bool] = {}
    for poster, _seg_id in pairs:
        if id(poster) in was_on:
            continue
        try:
            was_on[id(poster)] = bool(poster.get_state().get("on", True))
        except Exception:
            was_on[id(poster)] = True

    try:
        for poster, seg_id in pairs:
            poster.post_state({"on": True, "udpn": {"nn": True}})
        for _ in range(flashes * 2):
            for poster, seg_id in pairs:
                seg: dict[str, Any] = {"on": "t"}
                if seg_id is not None:
                    seg["id"] = seg_id
                poster.post_state({"seg": [seg], "udpn": {"nn": True}})
            sleep(interval_s)
    finally:
        for poster, _seg_id in pairs:
            try:
                poster.post_state({"on": was_on[id(poster)], "udpn": {"nn": True}})
            except Exception:
                pass
    return f"Flashed {target} {flashes} time(s)."
