#!/usr/bin/env python3
"""Opinionated dynamic scene composer for the calibrated Lightss wall.

Pure composition lives in ``compose_dynamic_scene``; ``apply_dynamic_scene`` only
posts the composed per-controller payloads. V2 defaults to generated static
pixel frames; stock WLED effects remain available with engine='effect'.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import color_lab
import fleet as fleet_mod
import lightctl
import look_memory


_DYNAMIC_EFFECT_NAMES = {
    "breathe": "Breathe",
    "gradient": "Fade",
    "chase": "Chase",
    "colorwaves": "Colorwaves",
    "sine": "Sine",
}


def _safe_id(name: str) -> int:
    for effect_id, effect_name in lightctl.SAFE_EFFECTS.items():
        if effect_name.lower() == name.lower():
            return effect_id
    raise RuntimeError(f"dynamic scene effect {name!r} is not in lightctl.SAFE_EFFECTS")


SAFE_EFFECTS = {key: _safe_id(name) for key, name in _DYNAMIC_EFFECT_NAMES.items()}
FALLBACK_FX = SAFE_EFFECTS["breathe"]

PALETTES = {
    "warm": 8,
    "ocean": 51,
    "forest": 52,
    "sunset": 35,
    "purple": 10,
    "rainbow": 11,
    "default": 0,
}

COLORS = {
    "warm": [[255, 154, 72, 40], [255, 92, 24, 0]],
    "cool": [[48, 140, 255, 0], [0, 220, 190, 0]],
    "dark": [[95, 0, 160, 0], [180, 0, 40, 0]],
    "soft": [[255, 190, 120, 32], [120, 90, 255, 0]],
    "party": [[255, 40, 160, 0], [0, 220, 255, 0]],
}

STRATEGIES = {"quiet_gradient", "split_temperature", "mirror", "center_out", "left_to_right", "chase", "vertical_rise", "top_glow", "bottom_glow", "center_bloom", "shimmer"}
ENGINES = {"generated", "effect"}
COMPOSITION_MODES = {"unison", "independent", "pairs", "center_vs_outer", "left_vs_right", "alternating", "random_groups"}


@dataclass(frozen=True)
class WallEntry:
    channel: str
    controller: str
    seg_id: int
    pixels: int
    start: int
    stop: int
    wall_index: int
    orientation: str
    pixel_zero: str


def _segment_channel(segment: Any) -> str:
    return segment.channel if isinstance(segment, fleet_mod.SegmentConfig) else str(segment)


def _wall_entries(fleet: Any) -> list[WallEntry]:
    controllers = list(getattr(fleet, "controllers", []))
    installation = getattr(fleet, "installation", fleet_mod.InstallationConfig())
    wall_order = list(getattr(installation, "wall_order", fleet_mod.WALL_ORDER))
    if not controllers:
        raise ValueError("dynamic_scene requires fleet.controllers topology with SegmentConfig pixel metadata")
    by_channel: dict[str, tuple[str, int, int, int, int]] = {}
    wall_channels = set(wall_order)
    for controller in controllers:
        for seg_id, segment in getattr(controller, "segments", {}).items():
            channel = _segment_channel(segment)
            if channel not in wall_channels:
                continue
            if not isinstance(segment, fleet_mod.SegmentConfig) or segment.pixels is None or segment.pixels <= 0:
                raise ValueError("dynamic_scene requires SegmentConfig pixels for every wall segment")
            if segment.start is None or segment.stop is None:
                raise ValueError("dynamic_scene requires SegmentConfig start/stop for every wall segment")
            if segment.stop <= segment.start or segment.stop - segment.start != segment.pixels:
                raise ValueError("dynamic_scene segment start/stop must match pixels")
            pixels = segment.pixels
            by_channel[channel] = (controller.name, int(seg_id), pixels, segment.start, segment.stop)
    ordered = [channel for channel in wall_order if channel in by_channel]
    if not ordered:
        raise ValueError("dynamic_scene requires configured wall channel topology")
    return [
        WallEntry(
            channel=channel,
            controller=by_channel[channel][0],
            seg_id=by_channel[channel][1],
            pixels=by_channel[channel][2],
            start=by_channel[channel][3],
            stop=by_channel[channel][4],
            wall_index=index,
            orientation=getattr(installation, "orientation", "vertical"),
            pixel_zero=getattr(installation, "pixel_zero", "bottom"),
        )
        for index, channel in enumerate(ordered)
    ]


def _auto_strategy(mood: str, energy: str, motion: str) -> str:
    words = f"{mood} {energy} {motion}".lower()
    if any(w in words for w in ("calm", "quiet", "sleep", "soft", "ambient")):
        return "quiet_gradient"
    if any(w in words for w in ("warm", "cool", "temperature", "cozy")):
        return "split_temperature"
    if any(w in words for w in ("rise", "uplift", "flame", "fire")):
        return "vertical_rise"
    if any(w in words for w in ("chase", "move", "flow", "left", "dance", "party")):
        return "left_to_right"
    if any(w in words for w in ("center", "mirror", "symmetry")):
        return "center_out"
    return "quiet_gradient"


def _profile(mood: str, energy: str, intensity: float | None, seed: int | str | None = None, colors: list | None = None) -> tuple[int, int, int, list[list[int]]]:
    words = f"{mood} {energy}".lower()
    level = 0.55 if intensity is None else max(0.0, min(1.0, float(intensity)))
    if any(w in words for w in ("party", "dance", "edm", "bright")):
        base = 150
        stock = COLORS["party"]
    elif any(w in words for w in ("ocean", "water", "blue", "cool")):
        base = 120
        stock = COLORS["cool"]
    elif any(w in words for w in ("dark", "metal", "noir", "purple")):
        base = 105
        stock = COLORS["dark"]
    elif any(w in words for w in ("forest", "green")):
        base = 115
        stock = COLORS["soft"]
    else:
        base = 110
        stock = COLORS["warm"]
    custom = color_lab.coerce_colors(colors)
    if custom:
        painted = color_lab.to_rgbw(custom)
    elif seed is not None or mood or energy:
        painted = color_lab.to_rgbw(color_lab.palette_from_prompt(mood, energy, seed))
    else:
        painted = stock
    bri = min(180, max(35, int(base * (0.65 + level * 0.55))))
    sx = min(170, max(35, int(45 + level * 95)))
    ix = min(190, max(70, int(95 + level * 75)))
    return bri, sx, ix, painted


def _mix(a: list[int], b: list[int], t: float) -> list[int]:
    t = max(0.0, min(1.0, t))
    return [max(0, min(255, int(a[i] + (b[i] - a[i]) * t))) for i in range(4)]


def _scale(c: list[int], factor: float) -> list[int]:
    return [max(0, min(255, int(v * factor))) for v in c]


def _y_for_index(index: int, pixels: int, pixel_zero: str) -> float:
    y = 0.0 if pixels <= 1 else index / (pixels - 1)
    return y if pixel_zero == "bottom" else 1.0 - y


def _group_for(entry: WallEntry, mode: str, rng: random.Random) -> int:
    if mode == "unison":
        return 0
    if mode == "independent":
        return entry.wall_index
    if mode == "pairs":
        return entry.wall_index // 2
    if mode == "center_vs_outer":
        return 0 if entry.wall_index in (1, 2) else 1
    if mode == "left_vs_right":
        return 0 if entry.wall_index < 2 else 1
    if mode == "alternating":
        return entry.wall_index % 2
    if mode == "random_groups":
        return rng.randrange(0, 3)
    return 0


def _frame(entry: WallEntry, strategy: str, colors: list[list[int]], group: int, rng: random.Random) -> list[list[int]]:
    primary = colors[group % len(colors)]
    secondary = colors[(group + 1) % len(colors)]
    frame: list[list[int]] = []
    for idx in range(entry.pixels):
        y = _y_for_index(idx, entry.pixels, entry.pixel_zero)
        wall_t = entry.wall_index / 3 if entry.wall_index <= 3 else 0
        if strategy in {"top_glow", "vertical_rise"}:
            glow = y ** 1.7
            color = _mix(_scale(primary, 0.18), secondary, glow)
        elif strategy == "bottom_glow":
            glow = (1.0 - y) ** 1.7
            color = _mix(_scale(secondary, 0.16), primary, glow)
        elif strategy in {"center_bloom", "center_out", "mirror"}:
            glow = max(0.0, 1.0 - abs(y - 0.52) * 2.2)
            color = _mix(_scale(primary, 0.2), secondary, glow)
        elif strategy == "shimmer":
            base = _mix(primary, secondary, y)
            sparkle = 1.45 if rng.random() < 0.10 else rng.uniform(0.55, 0.9)
            color = _scale(base, sparkle)
        else:  # quiet/symmetric wall gradient
            color = _mix(primary, secondary, (y * 0.65 + wall_t * 0.35))
        frame.append(color[:3])
    return frame


def _compose_generated(entries: list[WallEntry], mood: str, energy: str, motion: str, strategy: str, composition_mode: str, seed: int | str | None, intensity: float | None, colors: list | None = None) -> dict[str, dict]:
    rng = random.Random(seed)
    strategy = (strategy or _auto_strategy(mood, energy, motion)).strip().lower().replace("-", "_")
    if strategy == "quiet_gradient":
        strategy = "symmetric_gradient"
    if strategy not in STRATEGIES and strategy != "symmetric_gradient":
        strategy = "symmetric_gradient"
    mode = color_lab.choose_composition(mood, motion, energy, composition_mode, seed=seed)
    bri, _sx, _ix, colors = _profile(mood, energy, intensity, seed, colors)
    payloads: dict[str, dict] = {}
    for entry in entries:
        group = _group_for(entry, mode, rng)
        strip_bri = bri if mode == "unison" else max(25, min(180, int(bri * (0.78 + 0.07 * ((group + entry.wall_index) % 4)))))
        seg = {"id": entry.seg_id, "start": entry.start, "stop": entry.stop, "on": True, "fx": 0, "frz": False, "bri": strip_bri, "i": _frame(entry, strategy, colors, group, rng)}
        payloads.setdefault(entry.controller, {"on": True, "bri": min(180, bri), "transition": 10, "seg": [], "udpn": {"nn": True}})
        payloads[entry.controller]["seg"].append(seg)
    return payloads


def _compose_effect(entries: list[WallEntry], mood: str, energy: str, motion: str, strategy: str, seed: int | str | None, intensity: float | None, colors: list | None = None) -> dict[str, dict]:
    strategy = (strategy or _auto_strategy(mood, energy, motion)).strip().lower().replace("-", "_")
    if strategy == "chase":
        strategy = "left_to_right"
    if strategy not in STRATEGIES:
        strategy = _auto_strategy(mood, energy, motion)
    rng = random.Random(seed)
    bri, sx, ix, colors = _profile(mood, energy, intensity, seed, colors)
    transition = 18
    count = max(1, len(entries))
    payloads: dict[str, dict] = {}
    for entry in entries:
        seg: dict[str, Any] = {"id": entry.seg_id, "start": entry.start, "stop": entry.stop, "on": True, "bri": bri, "sx": sx, "ix": ix, "col": colors, "pal": PALETTES["warm"]}
        if strategy == "quiet_gradient":
            seg.update({"fx": SAFE_EFFECTS["gradient"], "pal": PALETTES["sunset"], "of": entry.wall_index * 7})
        elif strategy == "split_temperature":
            warm = entry.wall_index < count / 2
            seg.update({"fx": SAFE_EFFECTS["breathe"], "pal": PALETTES["warm" if warm else "ocean"], "col": COLORS["warm" if warm else "cool"]})
        elif strategy in {"mirror", "center_out"}:
            distance = abs(entry.wall_index - (count - 1) / 2)
            seg.update({"fx": SAFE_EFFECTS["colorwaves"], "pal": PALETTES["purple"], "mi": entry.wall_index < count / 2, "of": int(distance * 12)})
        elif strategy == "left_to_right":
            step = max(1, min(entry.pixels, 48) // count)
            seg.update({"fx": SAFE_EFFECTS["chase"], "pal": PALETTES["rainbow"], "of": entry.wall_index * step})
        else:
            seg.update({"fx": SAFE_EFFECTS["sine"], "pal": PALETTES["sunset"], "rev": entry.pixel_zero != "bottom", "of": rng.randrange(0, max(1, entry.pixels))})
        payloads.setdefault(entry.controller, {"on": True, "bri": bri, "transition": transition, "seg": [], "udpn": {"nn": True}})
        payloads[entry.controller]["seg"].append(seg)
    return payloads


def compose_dynamic_scene(
    fleet: Any,
    mood: str = "",
    energy: str = "",
    motion: str = "",
    strategy: str = "",
    composition_mode: str | None = None,
    engine: str = "generated",
    seed: int | str | None = None,
    intensity: float | None = None,
    colors: list | None = None,
) -> dict[str, dict]:
    entries = _wall_entries(fleet)
    engine = (engine or "generated").strip().lower()
    if engine == "effect":
        return _compose_effect(entries, mood, energy, motion, strategy, seed, intensity, colors)
    return _compose_generated(entries, mood, energy, motion, strategy, composition_mode, seed, intensity, colors)


def apply_dynamic_scene(fleet: Any, **kwargs) -> dict:
    results: dict = {}
    payloads = compose_dynamic_scene(fleet, **kwargs)
    for controller, payload in payloads.items():
        available = fleet.effect_ids(controller) if hasattr(fleet, "effect_ids") else None
        if available is not None:
            stock_segments = [seg for seg in payload.get("seg", []) if seg.get("fx") != 0]
            if stock_segments and FALLBACK_FX not in available:
                raise ValueError(f"dynamic_scene safe fallback effect {FALLBACK_FX} unavailable on {controller}")
            for seg in stock_segments:
                if seg.get("fx") not in available:
                    seg["fx"] = FALLBACK_FX
                    seg["pal"] = PALETTES["warm"]
        # Generated scenes carry per-LED 'i' frames, which WLED ignores when
        # 'on' rides in the same request from an off state (JSON API docs:
        # set power/brightness first). Prime before the frame post.
        if any("i" in seg for seg in payload.get("seg", [])):
            primer = {key: payload[key] for key in ("on", "bri", "transition", "udpn") if key in payload}
            results.update(fleet.post_state(primer, target=controller))
        results.update(fleet.post_state(payload, target=controller))
    look_memory.record_look(
        source="dynamic_scene",
        action="dynamic_scene",
        mood=str(kwargs.get("mood") or ""),
        parameters={key: value for key, value in kwargs.items() if key != "seed" or value is not None},
        summary=f"dynamic_scene {kwargs.get('engine', 'generated')}",
        payload_summary=_payload_summary(payloads),
    )
    return results


def _payload_summary(payloads: dict[str, dict]) -> dict:
    summary: dict[str, list[dict]] = {}
    for controller, payload in payloads.items():
        entries = []
        for seg in payload.get("seg", []):
            entries.append(
                {
                    "id": seg.get("id"),
                    "start": seg.get("start"),
                    "stop": seg.get("stop"),
                    "fx": seg.get("fx"),
                    "bri": seg.get("bri"),
                    "frame_len": len(seg.get("i") or []),
                }
            )
        summary[controller] = entries
    return summary
