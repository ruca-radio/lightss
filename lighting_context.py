"""Bounded controller observations shared by chat, MCP and music planning.

Observe WLED state, Peek pixel buffers and mic features without changing renderer
ownership. Controller buffers and successfully sent DDP frames are distinct evidence;
neither confirms the physical LEDs, subjective appearance, or exact acoustic timing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import time

_monitor_lock = threading.RLock()


def summarize_pixels(colors, *, source):
    """Keep spatial samples and a compact distribution instead of raw frame arrays."""
    valid = [
        list(color[:3])
        for color in colors
        if isinstance(color, (list, tuple))
        and len(color) >= 3
        and all(type(value) is int and 0 <= value <= 255 for value in color[:3])
    ]
    if not valid:
        return {"source": source, "color_count": 0, "sample_colors": []}
    count = len(valid)
    samples = [
        valid[round(index * (count - 1) / max(1, min(8, count) - 1))]
        for index in range(min(8, count))
    ]
    # Group nearby shades to avoid letting tiny frame variations hide the palette.
    buckets = {}
    for color in valid:
        key = tuple(value // 32 for value in color)
        buckets.setdefault(key, []).append(color)
    dominant = sorted(buckets.values(), key=len, reverse=True)[:3]
    means = [
        tuple(round(sum(color[i] for color in group) / len(group)) for i in range(3))
        for group in dominant
    ]
    luminance = [(0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 for r, g, b in valid]
    return {
        "source": source,
        "color_count": count,
        "sample_colors": samples,
        "dominant_colors": ["#%02x%02x%02x" % color for color in means],
        "lit_fraction": round(sum(max(color) > 0 for color in valid) / count, 3),
        "mean_rgb_luminance": round(sum(luminance) / count, 4),
        "max_rgb_channel": max(max(color) for color in valid),
    }


def ensure_audio_monitor(client):
    """Share one mic listener with GUI/standalone MCP; create no socket until queried."""
    from wled_audio import ControllerAudioMonitor
    from lightctl import LightClient
    from fleet import LightFleet

    monitor = getattr(client, "_controller_audio_monitor", None)
    if isinstance(monitor, ControllerAudioMonitor):
        return monitor
    if not isinstance(client, (LightClient, LightFleet)):
        return None
    with _monitor_lock:
        monitor = getattr(client, "_controller_audio_monitor", None)
        if not isinstance(monitor, ControllerAudioMonitor):
            dry_run = bool(getattr(client, "dry_run", False))
            if isinstance(client, LightFleet):
                dry_run = bool(client.clients) and all(
                    bool(getattr(child, "dry_run", False))
                    for child in client.clients.values()
                )
            monitor = ControllerAudioMonitor(client, dry_run=dry_run)
            client._controller_audio_monitor = monitor
        return monitor


def _live_snapshot(client):
    """Read only state/info/effects for telemetry, avoiding configuration/preset IO."""
    from lightctl import LightClient
    from fleet import LightFleet

    def read(device):
        try:
            if isinstance(device, LightClient):
                raw = device.get_json()
                observed_at = device._state_observed_at
                return {
                    **raw,
                    "observation": {
                        "source": "controller_json",
                        "age_s": max(0, time.monotonic() - observed_at)
                        if observed_at is not None
                        else None,
                    },
                }
            getter = getattr(device, "get_device_snapshot", None)
            return getter() if callable(getter) else {}
        except Exception as exc:
            return {"error": type(exc).__name__}

    if isinstance(client, LightFleet):
        return {
            "topology": client.topology_dict(),
            "devices": {name: read(device) for name, device in client.clients.items()},
        }
    if isinstance(client, LightClient):
        return read(client)
    getter = getattr(client, "get_fleet_snapshot", None) or getattr(
        client, "get_device_snapshot", None
    )
    return getter() if callable(getter) else {}


def observe_lighting(client, *, snapshot=None):
    """Capture current facts with independent source ages; unavailable is not silence."""
    if snapshot is None:
        try:
            snapshot = _live_snapshot(client)
        except Exception as exc:
            snapshot = {"error": type(exc).__name__}
    if not isinstance(snapshot, dict):
        snapshot = {}
    monitor = ensure_audio_monitor(client)
    audio = (
        monitor.ai_summary() if monitor else {"active": False, "status": "unavailable"}
    )
    is_fleet = isinstance(snapshot.get("devices"), dict)
    devices = snapshot["devices"] if is_fleet else {"controller": snapshot}
    topology = (snapshot.get("topology") or {}).get("controllers", [])
    channels = {
        item.get("name"): item.get("segments", {})
        for item in topology
        if isinstance(item, dict)
    }
    children = getattr(client, "clients", {}) if is_fleet else {"controller": client}
    result = {}
    for name, device in list(devices.items())[:16]:
        if not isinstance(device, dict):
            continue
        state = device.get("state") or {}
        info = device.get("info") or {}
        target = children.get(name) if isinstance(children, dict) else None
        peek = {"active": False, "status": "unavailable"}
        getter = getattr(target, "get_led_preview", None)
        if callable(getter):
            try:
                peek = getter()
            except Exception as exc:
                peek = {
                    "active": False,
                    "status": "unavailable",
                    "error": type(exc).__name__,
                }
        pixels = peek.get("colors", []) if peek.get("active") else []
        total = (info.get("leds") or {}).get("count", 0)
        limit = 256 if str(info.get("arch", "")).lower() == "esp8266" else 1024
        step = (
            max(1, (total - 1) // limit + 1) if type(total) is int and total > 0 else 1
        )
        effects = device.get("effects") or []
        segments = []
        for seg in (state.get("seg") or [])[:32]:
            if not isinstance(seg, dict):
                continue
            seg_id = seg.get("id", 0)
            mapping = channels.get(name, {}).get(str(seg_id), {})
            effect = seg.get("fx")
            fields = {
                key: seg[key]
                for key in (
                    "id",
                    "on",
                    "bri",
                    "frz",
                    "fx",
                    "sx",
                    "ix",
                    "pal",
                    "col",
                    "start",
                    "stop",
                    "rev",
                )
                if key in seg
            }
            fields["channel"] = mapping.get("channel")
            fields["effect_name"] = (
                effects[effect]
                if type(effect) is int and 0 <= effect < len(effects)
                else None
            )
            output = {
                key: value
                for key, value in peek.items()
                if key not in ("colors", "previous_colors", "frame_change")
            }
            output["motion"] = "unknown"
            if pixels:
                start, stop = seg.get("start", 0), seg.get("stop", total)
                if type(start) is int and type(stop) is int and 0 <= start < stop:
                    relevant = [
                        color
                        for i, color in enumerate(pixels)
                        if start <= i * step < stop
                    ]
                    output.update(
                        summarize_pixels(relevant, source="controller_pixel_buffer")
                    )
                    output["sample_step"] = step
                    previous = peek.get("previous_colors") or []
                    before = [
                        color
                        for i, color in enumerate(previous)
                        if start <= i * step < stop
                    ]
                    if relevant and len(before) == len(relevant):
                        delta = sum(
                            abs(a - b)
                            for old, new in zip(before, relevant)
                            for a, b in zip(old, new)
                        ) / (len(relevant) * 3 * 255)
                        output.update(
                            {
                                "motion": "changing" if delta > 0 else "unchanged",
                                "motion_delta": round(delta, 4),
                                "motion_window_s": peek.get("frame_interval_s"),
                            }
                        )
            fields["output"] = output
            segments.append(fields)
        result[name] = {
            "on": state.get("on"),
            "brightness": state.get("bri"),
            "renderer_owner": "realtime_input"
            if info.get("live")
            else "native_wled"
            if "live" in info
            else "unknown",
            "realtime_mode": info.get("lm"),
            "state_observation": device.get("observation", {"age_s": None}),
            "segments": segments,
        }
        if device.get("error"):
            result[name]["error"] = device["error"]
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "captured_at_monotonic_s": time.monotonic(),
        "controllers": result,
        "audio": audio,
        "physical_output_verified": False,
        "timing_note": "Receiver timestamps; sources are asynchronous, not hardware-synchronized.",
    }
