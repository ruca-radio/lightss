#!/usr/bin/env python3
"""Fleet layer: multiple WLED controllers and named per-segment channels.

Resolution order for controller definitions:
  1. config.json "controllers" list
  2. LIGHT_HOSTS env var (comma-separated hosts, named light-1, light-2, ...)
  3. built-in default (the two wall controllers)
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import sys
from dataclasses import dataclass, field

import lightctl

logger = logging.getLogger("lightss.fleet")

WALL_ORDER: list[str] = ["far-left", "middle-left", "middle-right", "far-right"]
DEFAULT_TARGET = "all"
STRIP_GROUPS: dict[str, tuple[str, ...]] = {
    "outer": ("far-left", "far-right"),
    "inner": ("middle-left", "middle-right"),
    "center": ("middle-left", "middle-right"),
}

_BUILTIN_CONTROLLERS = [
    {
        "name": "left", "host": "http://10.27.27.110",
        "segments": {
            "0": {"channel": "far-left", "gpio": 16, "pixels": 34, "start": 0, "stop": 34},
            "1": {"channel": "middle-left", "gpio": 2, "pixels": 48, "start": 34, "stop": 82},
        },
    },
    {
        "name": "right", "host": "http://10.27.27.112",
        "segments": {
            "0": {"channel": "far-right", "gpio": 2, "pixels": 40, "start": 47, "stop": 87},
            "1": {"channel": "middle-right", "gpio": 16, "pixels": 47, "start": 0, "stop": 47},
        },
    },
]


# ---------------------------------------------------------------------------
# Installation and controller configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InstallationConfig:
    name: str = "bedroom-wall"
    wall_order: list[str] = field(default_factory=lambda: list(WALL_ORDER))
    spacing_inches: float = 32.0
    orientation: str = "vertical"
    pixel_zero: str = "bottom"
    column_length_m: float = 2.0
    pixels_per_meter: int = 20
    visible_leds_per_meter: int = 720
    color_order: str = "BRG"
    addressable_pixel_physical_leds: int = 5


@dataclass(frozen=True)
class SegmentConfig:
    channel: str
    gpio: int | None = None
    pixels: int | None = None
    start: int | None = None
    stop: int | None = None


@dataclass
class ControllerConfig:
    name: str
    host: str
    segments: dict[int, SegmentConfig] = field(default_factory=dict)  # segment id -> segment config


_VALID_ORIENTATIONS = ("vertical", "horizontal")
_VALID_PIXEL_ZERO = ("top", "bottom", "left", "right")


def _parse_installation(config: dict) -> InstallationConfig:
    """Parse the optional "installation" section; raises ValueError on bad values."""
    raw = config.get("installation") or {}
    if not isinstance(raw, dict):
        raise ValueError(f"'installation' must be an object, got {raw!r}")
    wall_order = raw.get("wall_order")
    if wall_order is None:
        wall_order = list(WALL_ORDER)
    else:
        if not isinstance(wall_order, list) or not all(isinstance(c, str) and c.strip() for c in wall_order):
            raise ValueError(f"installation 'wall_order' must be a list of channel names, got {wall_order!r}")
        wall_order = [c.strip() for c in wall_order]
        duplicates = sorted({c for c in wall_order if wall_order.count(c) > 1})
        if duplicates:
            raise ValueError(f"duplicate wall channel(s) in installation wall_order: {', '.join(duplicates)}")
    orientation = str(raw.get("orientation") or "vertical")
    if orientation not in _VALID_ORIENTATIONS:
        raise ValueError(f"unsupported orientation {orientation!r}; expected one of {', '.join(_VALID_ORIENTATIONS)}")
    pixel_zero = str(raw.get("pixel_zero") or "bottom")
    if pixel_zero not in _VALID_PIXEL_ZERO:
        raise ValueError(f"unsupported pixel_zero {pixel_zero!r}; expected one of {', '.join(_VALID_PIXEL_ZERO)}")
    return InstallationConfig(
        name=str(raw.get("name") or "bedroom-wall"),
        wall_order=wall_order,
        spacing_inches=float(raw.get("spacing_inches") or 32.0),
        orientation=orientation,
        pixel_zero=pixel_zero,
        column_length_m=float(raw.get("column_length_m") or 2.0),
        pixels_per_meter=int(raw.get("pixels_per_meter") or 20),
        visible_leds_per_meter=int(raw.get("visible_leds_per_meter") or 720),
        color_order=str(raw.get("color_order") or "BRG"),
        addressable_pixel_physical_leds=int(raw.get("addressable_pixel_physical_leds") or 5),
    )


def _parse_segment(value: object, controller_name: str, seg_id: object) -> SegmentConfig:
    """Parse one segment value: a plain channel name or an object with channel/gpio/pixels."""
    if isinstance(value, str):
        return SegmentConfig(channel=value.strip())
    if isinstance(value, dict):
        channel = str(value.get("channel") or "").strip()
        gpio = value.get("gpio")
        pixels = value.get("pixels")
        start = value.get("start")
        stop = value.get("stop")
        if gpio is not None and (not isinstance(gpio, int) or isinstance(gpio, bool)):
            raise ValueError(
                f"controller {controller_name!r} segment {seg_id!r} has a non-integer gpio in {value!r}"
            )
        if pixels is not None and (not isinstance(pixels, int) or isinstance(pixels, bool)):
            raise ValueError(
                f"controller {controller_name!r} segment {seg_id!r} has a non-integer pixels in {value!r}"
            )
        for key, number in (("start", start), ("stop", stop)):
            if number is not None and (not isinstance(number, int) or isinstance(number, bool)):
                raise ValueError(
                    f"controller {controller_name!r} segment {seg_id!r} has a non-integer {key} in {value!r}"
                )
        return SegmentConfig(channel=channel, gpio=gpio, pixels=pixels, start=start, stop=stop)
    raise ValueError(
        f"controller {controller_name!r} segment {seg_id!r} must be a channel name or object, got {value!r}"
    )


def _parse_controller(entry: dict) -> ControllerConfig:
    """Parse one controllers entry; raises ValueError on malformed entries."""
    if not isinstance(entry, dict):
        raise ValueError(f"controller entry must be an object, got {entry!r}")
    if not entry.get("name") or not entry.get("host"):
        raise ValueError(f"controller entry needs 'name' and 'host', got {entry!r}")
    raw_segments = entry.get("segments") or {}
    if not isinstance(raw_segments, dict):
        raise ValueError(f"controller {entry['name']!r} 'segments' must be an object, got {raw_segments!r}")
    segments: dict[int, SegmentConfig] = {}
    for seg_id, value in raw_segments.items():
        try:
            seg_key = int(seg_id)
        except (TypeError, ValueError):
            raise ValueError(
                f"controller {entry['name']!r} has a non-integer segment id in {raw_segments!r}"
            ) from None
        segments[seg_key] = _parse_segment(value, str(entry["name"]), seg_id)
    return ControllerConfig(
        name=str(entry["name"]),
        host=lightctl.normalize_host(str(entry["host"])),
        segments=segments,
    )


def _validate_topology(
    installation: InstallationConfig,
    controllers: list[ControllerConfig],
    *,
    enforce_coverage: bool,
) -> None:
    """Reject semantically invalid topologies (structural issues are caught earlier)."""
    seen: set[str] = set()
    for controller in controllers:
        for seg_id, segment in controller.segments.items():
            if not segment.channel:
                raise ValueError(f"controller {controller.name!r} segment {seg_id} has an empty channel name")
            if segment.channel in seen:
                raise ValueError(f"duplicate channel {segment.channel!r} configured more than once")
            seen.add(segment.channel)
            if segment.pixels is not None and segment.pixels <= 0:
                raise ValueError(
                    f"controller {controller.name!r} segment {seg_id} has nonpositive pixels {segment.pixels}"
                )
            if segment.start is not None and segment.start < 0:
                raise ValueError(f"controller {controller.name!r} segment {seg_id} has negative start {segment.start}")
            if segment.stop is not None and segment.stop <= 0:
                raise ValueError(f"controller {controller.name!r} segment {seg_id} has nonpositive stop {segment.stop}")
            if segment.start is not None and segment.stop is not None:
                if segment.stop <= segment.start:
                    raise ValueError(f"controller {controller.name!r} segment {seg_id} has invalid bounds {segment.start}..{segment.stop}")
                if segment.pixels is not None and segment.stop - segment.start != segment.pixels:
                    raise ValueError(
                        f"controller {controller.name!r} segment {seg_id} bounds {segment.start}..{segment.stop} do not match pixels {segment.pixels}"
                    )
    if enforce_coverage:
        missing = [channel for channel in installation.wall_order if channel not in seen]
        if missing:
            raise ValueError(f"wall_order channel(s) have no matching segment: {', '.join(missing)}")


def load_topology(config: dict | None = None) -> tuple[InstallationConfig, list[ControllerConfig]]:
    """Resolve (installation, controllers): config file -> LIGHT_HOSTS env -> built-in default."""
    if config is None:
        config = lightctl.load_config()

    installation = _parse_installation(config)

    controllers: list[ControllerConfig] | None = None
    configured = config.get("controllers")
    if isinstance(configured, list) and configured:
        parsed = []
        for entry in configured:
            try:
                parsed.append(_parse_controller(entry))
            except ValueError as exc:
                _warn(f"skipping malformed controllers entry: {exc}")
        if not parsed:
            raise ValueError(
                "config 'controllers' list is non-empty but every entry is malformed; "
                "refusing to fall back to env/built-in defaults"
            )
        controllers = parsed

    if controllers is None:
        env_hosts = os.environ.get("LIGHT_HOSTS", "").strip()
        if env_hosts:
            env_controllers = []
            for index, host in enumerate(h.strip() for h in env_hosts.split(",") if h.strip()):
                env_controllers.append(
                    ControllerConfig(name=f"light-{index + 1}", host=lightctl.normalize_host(host))
                )
            if env_controllers:
                controllers = env_controllers

    if controllers is None:
        controllers = [_parse_controller(entry) for entry in _BUILTIN_CONTROLLERS]

    raw_installation = config.get("installation")
    enforce_coverage = isinstance(raw_installation, dict) and "wall_order" in raw_installation
    _validate_topology(installation, controllers, enforce_coverage=enforce_coverage)
    return installation, controllers


def load_controllers(config: dict | None = None) -> list[ControllerConfig]:
    """Compatibility wrapper: return just the controller list from load_topology()."""
    return load_topology(config)[1]


def _channel_of(segment: object) -> str:
    """Channel name of a segment; tolerates legacy plain-string segment values."""
    return segment.channel if isinstance(segment, SegmentConfig) else str(segment)


def _derive_installation(controllers: list[ControllerConfig]) -> InstallationConfig:
    """Installation for directly-constructed fleets: the canonical installation when the
    configured channels are exactly the wall channels, otherwise a minimal installation
    with the provided channel order so custom fleets do not acquire invented aliases."""
    channels: list[str] = []
    for controller in controllers:
        for segment in controller.segments.values():
            channel = _channel_of(segment)
            if channel not in channels:
                channels.append(channel)
    if len(channels) == len(WALL_ORDER) and set(channels) == set(WALL_ORDER):
        return InstallationConfig()
    return InstallationConfig(wall_order=channels)


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------

def _warn(message: str) -> None:
    logger.warning("%s", message)
    print(f"lightss fleet: {message}", file=sys.stderr)


def _inject_segment_id(payload: dict, seg_id: int) -> dict:
    """Return a copy of payload with every seg entry pinned to seg_id."""
    segments = payload.get("seg")
    if not segments:
        return dict(payload)
    injected = dict(payload)
    injected["seg"] = [{**seg, "id": seg_id} for seg in segments]
    return injected


class LightFleet:
    """Fan-out controller for multiple WLED devices and their named channels."""

    def __init__(
        self,
        clients: dict[str, lightctl.LightClient],
        controllers: list[ControllerConfig] | None = None,
        installation: InstallationConfig | None = None,
    ) -> None:
        self.clients = dict(clients)
        if controllers is None:
            controllers = [ControllerConfig(name=name, host=client.host) for name, client in clients.items()]
        self.controllers = controllers
        self.installation = installation or _derive_installation(controllers)
        self.WALL_ORDER = list(self.installation.wall_order)
        seen_names: set[str] = set()
        self._channels: dict[str, tuple[str, int]] = {}
        for controller in controllers:
            if controller.name in seen_names:
                _warn(f"duplicate controller name {controller.name!r}; its channels may be shadowed")
            seen_names.add(controller.name)
            for seg_id, segment in controller.segments.items():
                channel = _channel_of(segment)
                if channel in self.clients:
                    _warn(f"channel {channel!r} collides with a controller name; ignoring it")
                    continue
                if channel in self._channels:
                    _warn(f"duplicate channel name {channel!r}; keeping the first mapping")
                    continue
                self._channels[channel] = (controller.name, seg_id)
        self._effect_ids: dict[str, set[int]] = {}

    @classmethod
    def from_config(cls, *, dry_run: bool = False, timeout: float = 10.0) -> "LightFleet":
        installation, controllers = load_topology()
        clients = {
            controller.name: lightctl.LightClient(host=controller.host, timeout=timeout, dry_run=dry_run)
            for controller in controllers
        }
        return cls(clients, controllers, installation=installation)

    def topology_dict(self) -> dict:
        """JSON-ready dump of the installation and controller topology."""
        return {
            "installation": dataclasses.asdict(self.installation),
            "controllers": [
                {
                    "name": controller.name,
                    "host": controller.host,
                    "segments": {
                        str(seg_id): dataclasses.asdict(segment)
                        for seg_id, segment in controller.segments.items()
                    },
                }
                for controller in self.controllers
            ],
        }

    def names(self) -> list[str]:
        """Controller names in configured order."""
        return list(self.clients.keys())

    def channels(self) -> dict[str, tuple[str, int]]:
        """Channel name -> (controller_name, segment id)."""
        return dict(self._channels)

    def valid_targets(self) -> list[str]:
        groups = [name for name in STRIP_GROUPS if all(ch in self._channels for ch in STRIP_GROUPS[name])]
        return [DEFAULT_TARGET, *groups, *self.names(), *self._channels.keys()]

    def _normalize_target(self, target: str) -> str:
        return (target or "").strip().lower().replace(" ", "-").replace("_", "-")

    def _resolve_one(self, token: str) -> list[tuple[str, int | None]]:
        if token == DEFAULT_TARGET:
            return [(name, None) for name in self.names()]
        if token in STRIP_GROUPS:
            return self._resolve_channel_list(STRIP_GROUPS[token])
        for name in self.clients:
            if name.lower() == token:
                return [(name, None)]
        for channel, mapping in self._channels.items():
            if channel.lower() == token:
                return [mapping]
        raise ValueError(
            f"Unknown target: {token!r}. Valid targets: {', '.join(self.valid_targets())}."
        )

    def _resolve_channel_list(self, tokens: tuple[str, ...] | list[str]) -> list[tuple[str, int | None]]:
        resolved: list[tuple[str, int | None]] = []
        seen: set[tuple[str, int | None]] = set()
        for token in tokens:
            for item in self._resolve_one(self._normalize_target(str(token))):
                if item in seen:
                    continue
                seen.add(item)
                resolved.append(item)
        return resolved

    def resolve(self, target: str) -> list[tuple[str, int | None]]:
        """Resolve 'all' | group | controller | channel | combo to [(controller, seg_id_or_None)].

        Groups: outer (far columns), inner/center (middle columns).
        Combos: 'far-left,middle-right' or 'far-left+far-right'.
        Channel/controller matching is forgiving: case-insensitive and spaces/
        underscores count as hyphens ("Far Left" == "far-left").
        """
        target = (target or "").strip() or DEFAULT_TARGET
        parts = [part for part in re.split(r"[,+]", target) if part.strip()]
        if len(parts) > 1:
            return self._resolve_channel_list(parts)
        return self._resolve_one(self._normalize_target(parts[0] if parts else DEFAULT_TARGET))

    def _controller_segments(self, name: str) -> list[int]:
        for controller in self.controllers:
            if controller.name == name:
                return list(controller.segments.keys())
        return []

    def _segment_config(self, name: str, seg_id: int) -> SegmentConfig | None:
        for controller in self.controllers:
            if controller.name == name:
                segment = controller.segments.get(seg_id)
                return segment if isinstance(segment, SegmentConfig) else None
        return None

    def _expand_resolved(self, resolved: list[tuple[str, int | None]]) -> list[tuple[str, int | None]]:
        expanded: list[tuple[str, int | None]] = []
        seen: set[tuple[str, int | None]] = set()
        for name, seg_id in resolved:
            items = [(name, seg_id)] if seg_id is not None else [(name, sid) for sid in self._controller_segments(name)] or [(name, None)]
            for item in items:
                if item in seen:
                    continue
                seen.add(item)
                expanded.append(item)
        return expanded

    def _decorate_segment(self, name: str, seg_id: int | None, template: dict) -> dict:
        entry = dict(template)
        if seg_id is None:
            return entry
        entry["id"] = seg_id
        config = self._segment_config(name, seg_id)
        if config is not None and config.start is not None and config.stop is not None:
            entry.setdefault("start", config.start)
            entry.setdefault("stop", config.stop)
            entry.setdefault("on", True)
        return entry

    def post_state(self, payload: dict, target: str = DEFAULT_TARGET) -> dict[str, dict]:
        """Fan a payload out to the resolved controllers.

        Segment payloads without ids expand onto every selected strip (all four
        wall columns for target=all, both strips on a controller target, or any
        explicit combo). Channel targets still inject that one segment id.
        A dead controller is reported in its result entry, never raised.
        Fleet posts carry udpn.nn (no-notify) so per-controller differences are
        not clobbered by WLED UDP sync; manual/UI changes still sync normally.
        """
        director_module = sys.modules.get("smart_director")
        if director_module is not None:
            before_write = getattr(director_module, "before_external_write", None)
            if before_write is not None:
                before_write(self)
        resolved = self.resolve(target)
        templates = payload.get("seg")
        if templates:
            addressed = all(isinstance(entry, dict) and "id" in entry for entry in templates)
            if not addressed:
                resolved = self._expand_resolved(resolved)
            if not addressed and any(seg_id is not None for _name, seg_id in resolved):
                grouped: dict[str, list[int | None]] = {}
                for name, seg_id in resolved:
                    grouped.setdefault(name, []).append(seg_id)
                single_strip = len(grouped) == 1 and len(next(iter(grouped.values()))) == 1
                results: dict[str, dict] = {}
                for name, seg_ids in grouped.items():
                    if single_strip:
                        entries = [self._decorate_segment(name, seg_ids[0], template) for template in templates]
                    else:
                        entries = [self._decorate_segment(name, seg_id, templates[0]) for seg_id in seg_ids]
                    outgoing = dict(payload)
                    outgoing["seg"] = entries
                    udpn = dict(outgoing.get("udpn") or {})
                    udpn["nn"] = True
                    outgoing["udpn"] = udpn
                    try:
                        response = self.clients[name].post_state(outgoing)
                        results[name] = {"ok": True, "response": response}
                    except Exception as exc:
                        _warn(f"post_state to controller {name!r} failed: {exc}")
                        results[name] = {"ok": False, "error": str(exc)}
                return results
        results = {}
        for name, seg_id in resolved:
            outgoing = _inject_segment_id(payload, seg_id) if seg_id is not None else dict(payload)
            udpn = dict(outgoing.get("udpn") or {})
            udpn["nn"] = True
            outgoing["udpn"] = udpn
            try:
                response = self.clients[name].post_state(outgoing)
                results[name] = {"ok": True, "response": response}
            except Exception as exc:
                _warn(f"post_state to controller {name!r} failed: {exc}")
                results[name] = {"ok": False, "error": str(exc)}
        return results

    def get_state(self, target: str = DEFAULT_TARGET) -> dict[str, dict]:
        """Per-controller state dicts; failures become {"error": ...} entries."""
        states: dict[str, dict] = {}
        for name, _seg_id in self.resolve(target):
            try:
                states[name] = self.clients[name].get_state()
            except Exception as exc:
                _warn(f"get_state from controller {name!r} failed: {exc}")
                states[name] = {"error": str(exc)}
        return states

    def get_fleet_snapshot(self) -> dict:
        """Topology plus per-controller device snapshots for AI consumption.

        Returns {"topology": self.topology_dict(), "devices": {name: snapshot}};
        a dead controller becomes a {"error": ...} entry under "devices".
        """
        devices: dict[str, dict] = {}
        for name in self.names():
            try:
                devices[name] = self.clients[name].get_device_snapshot()
            except Exception as exc:
                _warn(f"snapshot of controller {name!r} failed: {exc}")
                devices[name] = {"error": str(exc)}
        return {"topology": self.topology_dict(), "devices": devices}

    def effect_ids(self, controller: str) -> set[int] | None:
        """Live effect id set for a controller (indices of /json/eff), cached; None on failure."""
        if controller not in self.clients:
            raise ValueError(
                f"Unknown controller: {controller!r}. Valid controllers: {', '.join(self.names())}."
            )
        if controller not in self._effect_ids:
            try:
                effects = self.clients[controller].get_effects()
            except Exception as exc:
                _warn(f"effect list from controller {controller!r} failed: {exc}")
                return None
            if not effects:
                return None
            self._effect_ids[controller] = set(range(len(effects)))
        return self._effect_ids[controller]
