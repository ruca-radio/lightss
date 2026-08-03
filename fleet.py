#!/usr/bin/env python3
"""Fleet layer: multiple WLED controllers and named per-segment channels.

Resolution order for controller definitions:
  1. config.json "controllers" list
  2. LIGHT_HOSTS env var (comma-separated hosts, named light-1, light-2, ...)
  3. built-in default (the two wall controllers)
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field

import lightctl

logger = logging.getLogger("lightss.fleet")

WALL_ORDER: list[str] = ["far-left", "middle-left", "middle-right", "far-right"]
DEFAULT_TARGET = "all"

_BUILTIN_CONTROLLERS = [
    {"name": "right", "host": "http://10.27.27.110", "segments": {"0": "far-right", "1": "middle-right"}},
    {"name": "left", "host": "http://10.27.27.112", "segments": {"0": "middle-left", "1": "far-left"}},
]


# ---------------------------------------------------------------------------
# Controller configuration
# ---------------------------------------------------------------------------

@dataclass
class ControllerConfig:
    name: str
    host: str
    segments: dict[int, str] = field(default_factory=dict)  # segment id -> channel name


def _parse_controller(entry: dict) -> ControllerConfig:
    """Parse one controllers entry; raises ValueError on malformed entries."""
    if not isinstance(entry, dict):
        raise ValueError(f"controller entry must be an object, got {entry!r}")
    if not entry.get("name") or not entry.get("host"):
        raise ValueError(f"controller entry needs 'name' and 'host', got {entry!r}")
    raw_segments = entry.get("segments") or {}
    if not isinstance(raw_segments, dict):
        raise ValueError(f"controller {entry['name']!r} 'segments' must be an object, got {raw_segments!r}")
    try:
        segments = {int(seg_id): str(channel) for seg_id, channel in raw_segments.items()}
    except (TypeError, ValueError):
        raise ValueError(
            f"controller {entry['name']!r} has a non-integer segment id in {raw_segments!r}"
        ) from None
    return ControllerConfig(
        name=str(entry["name"]),
        host=lightctl.normalize_host(str(entry["host"])),
        segments=segments,
    )


def load_controllers(config: dict | None = None) -> list[ControllerConfig]:
    """Resolve the controller list: config file -> LIGHT_HOSTS env -> built-in default."""
    if config is None:
        config = lightctl.load_config()

    configured = config.get("controllers")
    if isinstance(configured, list) and configured:
        controllers = []
        for entry in configured:
            try:
                controllers.append(_parse_controller(entry))
            except ValueError as exc:
                _warn(f"skipping malformed controllers entry: {exc}")
        if controllers:
            return controllers

    env_hosts = os.environ.get("LIGHT_HOSTS", "").strip()
    if env_hosts:
        controllers = []
        for index, host in enumerate(h.strip() for h in env_hosts.split(",") if h.strip()):
            controllers.append(ControllerConfig(name=f"light-{index + 1}", host=lightctl.normalize_host(host)))
        if controllers:
            return controllers

    return [_parse_controller(entry) for entry in _BUILTIN_CONTROLLERS]


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
    ) -> None:
        self.clients = dict(clients)
        if controllers is None:
            controllers = [ControllerConfig(name=name, host=client.host) for name, client in clients.items()]
        self.controllers = controllers
        seen_names: set[str] = set()
        self._channels: dict[str, tuple[str, int]] = {}
        for controller in controllers:
            if controller.name in seen_names:
                _warn(f"duplicate controller name {controller.name!r}; its channels may be shadowed")
            seen_names.add(controller.name)
            for seg_id, channel in controller.segments.items():
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
        controllers = load_controllers()
        clients = {
            controller.name: lightctl.LightClient(host=controller.host, timeout=timeout, dry_run=dry_run)
            for controller in controllers
        }
        return cls(clients, controllers)

    def names(self) -> list[str]:
        """Controller names in configured order."""
        return list(self.clients.keys())

    def channels(self) -> dict[str, tuple[str, int]]:
        """Channel name -> (controller_name, segment id)."""
        return dict(self._channels)

    def valid_targets(self) -> list[str]:
        return [DEFAULT_TARGET, *self.names(), *self._channels.keys()]

    def resolve(self, target: str) -> list[tuple[str, int | None]]:
        """Resolve 'all' | controller name | channel name to [(controller_name, seg_id_or_None)].

        Channel/controller matching is forgiving: case-insensitive and spaces/
        underscores count as hyphens ("Far Left" == "far-left").
        """
        target = (target or "").strip() or DEFAULT_TARGET
        normalized = target.lower().replace(" ", "-").replace("_", "-")
        if normalized == DEFAULT_TARGET:
            return [(name, None) for name in self.names()]
        for name in self.clients:
            if name.lower() == normalized:
                return [(name, None)]
        for channel, mapping in self._channels.items():
            if channel.lower() == normalized:
                return [mapping]
        raise ValueError(
            f"Unknown target: {target!r}. Valid targets: {', '.join(self.valid_targets())}."
        )

    def post_state(self, payload: dict, target: str = DEFAULT_TARGET) -> dict[str, dict]:
        """Fan a payload out to the resolved controllers.

        Channel targets get the segment id injected into the payload's seg entries.
        A dead controller is reported in its result entry, never raised.
        Fleet posts carry udpn.nn (no-notify) so per-controller differences are
        not clobbered by WLED UDP sync; manual/UI changes still sync normally.
        """
        results: dict[str, dict] = {}
        for name, seg_id in self.resolve(target):
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
        """Per-controller labeled device snapshots for AI consumption."""
        snapshot: dict[str, dict] = {}
        for name in self.names():
            try:
                snapshot[name] = self.clients[name].get_device_snapshot()
            except Exception as exc:
                _warn(f"snapshot of controller {name!r} failed: {exc}")
                snapshot[name] = {"error": str(exc)}
        return snapshot

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
