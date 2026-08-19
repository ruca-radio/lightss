#!/usr/bin/env python3
"""Safe local DDP realtime renderer for Lightss."""

from __future__ import annotations

import math
import random
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import color_lab
import fleet as fleet_mod
import look_memory

DDP_PORT = 4048
DDP_FLAGS = 0x40
DDP_PUSH = 0x01
DDP_RGB8 = 0x0B
DDP_DEST = 1
MAX_DATA = 1200
SHADERS = set(color_lab.SHADERS)
COMPOSITION_MODES = set(color_lab.COMPOSITION_MODES)


@dataclass(frozen=True)
class DdpEntry:
    channel: str
    controller: str
    host: str
    pixels: int
    seg_id: int
    ddp_offset: int
    ddp_length: int
    pixel_zero: str
    wall_index: int


class UdpTransport:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def sendto(self, data: bytes, address):
        return self.sock.sendto(data, address)

    def close(self):
        self.sock.close()


def build_ddp_packets(data: bytes, offset: int = 0, seq: int = 0, max_data: int = MAX_DATA) -> list[bytes]:
    packets = []
    pos = 0
    while pos < len(data) or (not data and pos == 0):
        chunk = data[pos:pos + max_data]
        flags = DDP_FLAGS | (DDP_PUSH if pos + len(chunk) >= len(data) else 0)
        header = struct.pack("!BBBBLH", flags, seq & 0xFF, DDP_RGB8, DDP_DEST, offset + pos, len(chunk))
        packets.append(header + chunk)
        if not data:
            break
        pos += len(chunk)
    return packets


def _host_ip(host: str) -> str:
    parsed = urlparse(host)
    return parsed.hostname or host.replace("http://", "").replace("https://", "").split("/", 1)[0]


def ddp_topology(fleet: Any) -> list[DdpEntry]:
    installation = getattr(fleet, "installation", fleet_mod.InstallationConfig())
    wall_order = list(getattr(installation, "wall_order", fleet_mod.WALL_ORDER))
    by_channel = {}
    for controller in getattr(fleet, "controllers", []):
        for seg_id, segment in controller.segments.items():
            if not isinstance(segment, fleet_mod.SegmentConfig):
                continue
            if segment.start is None or segment.stop is None or segment.pixels is None:
                raise ValueError("realtime requires SegmentConfig start/stop/pixels")
            length = segment.stop - segment.start
            if length != segment.pixels or length <= 0:
                raise ValueError("realtime DDP length must match pixels")
            by_channel[segment.channel] = (controller, int(seg_id), segment)
    entries = []
    for idx, channel in enumerate(wall_order):
        if channel not in by_channel:
            continue
        controller, seg_id, segment = by_channel[channel]
        entries.append(DdpEntry(channel, controller.name, _host_ip(controller.host), segment.pixels, seg_id, segment.start, segment.stop - segment.start, getattr(installation, "pixel_zero", "bottom"), idx))
    if not entries:
        raise ValueError("realtime requires configured wall topology")
    by_controller: dict[str, list[DdpEntry]] = {}
    for entry in entries:
        by_controller.setdefault(entry.controller, []).append(entry)
    for ctrl, group in by_controller.items():
        ranges = sorted((e.ddp_offset, e.ddp_offset + e.ddp_length) for e in group)
        for a, b in ranges:
            if a < 0 or b <= a:
                raise ValueError(f"invalid DDP range on {ctrl}")
        for (_a, b), (c, _d) in zip(ranges, ranges[1:]):
            if b > c:
                raise ValueError(f"overlapping DDP ranges on {ctrl}")
    return entries


def _pal(mood: str, colors=None, seed=None):
    if colors:
        return color_lab.normalize_palette(colors)
    word = (mood or "").lower()
    if "red" in word or "rock" in word:
        return [(80, 5, 2), (180, 30, 12), (210, 90, 25)]
    if "aurora" in word or "cool" in word:
        return [(5, 40, 90), (20, 180, 150), (120, 60, 210)]
    if mood:
        return color_lab.palette_from_prompt(mood, seed=seed)
    return [(20, 50, 120), (120, 40, 180), (210, 120, 50)]


def _clamp_rgb(rgb, cap=color_lab.RGB_CAP):
    return color_lab.clamp_rgb(rgb, cap)


def _mix(a, b, t):
    return color_lab.mix(a, b, t)


def _group(entry: DdpEntry, mode: str, rng: random.Random):
    if mode == "independent": return entry.wall_index
    if mode == "pairs": return entry.wall_index // 2
    if mode == "center_vs_outer": return 0 if entry.wall_index in (1, 2) else 1
    if mode == "left_vs_right": return 0 if entry.wall_index < 2 else 1
    if mode == "alternating": return entry.wall_index % 2
    if mode == "random_groups": return rng.randrange(3)
    return 0


def pixel_color(shader: str, entry: DdpEntry, pixel: int, t: float, mood: str = "", intensity: float = 0.6, composition_mode: str = "unison", seed: int | str | None = None, colors=None):
    rng = random.Random(f"{seed}:{entry.wall_index}")
    g = _group(entry, composition_mode, rng)
    y = 0 if entry.pixels <= 1 else pixel / (entry.pixels - 1)
    if entry.pixel_zero != "bottom":
        y = 1 - y
    p = _pal(mood, colors, seed)
    amp = min(0.82, max(0.1, float(intensity)))
    phase = t * 0.6 + entry.wall_index * 0.17 + g * 0.21
    if shader == "red_rocks":
        v = 0.35 + 0.35 * math.sin(phase * 2 + y * 4)
        rgb = _mix(p[0], p[-1], v)
    elif shader == "aurora_flow":
        v = (math.sin(phase + y * 5) + 1) / 2
        rgb = color_lab.sample_palette(p, v)
    elif shader == "bass_bloom":
        v = max(0, 1 - abs(y - 0.45) * 2) * (0.6 + 0.25 * math.sin(phase * 3))
        rgb = _mix(p[0], p[-1], v)
    elif shader == "center_wave":
        center = abs(entry.wall_index - 1.5) / 1.5
        v = max(0, 1 - center) * 0.5 + (math.sin(y * 6 + phase) + 1) * 0.25
        rgb = _mix(p[0], p[min(1, len(p) - 1)], v)
    elif shader == "vertical_scan":
        scan = (phase * 0.25) % 1.0
        v = max(0.15, 1 - abs(y - scan) * 5)
        rgb = _mix(p[0], p[-1], v)
    elif shader == "ember_rise":
        heat = (1.0 - y) ** 1.45
        v = max(0.12, heat * (0.55 + 0.4 * math.sin(phase * 1.7 + y * 3.2)))
        rgb = color_lab.sample_palette(p, 1.0 - y)
        rgb = _mix((12, 2, 1), rgb, v)
    elif shader == "tide_pull":
        wave = (math.sin(y * 7.0 - phase * 1.35) + 1) / 2
        v = wave * 0.62 + y * 0.28 + 0.1
        rgb = color_lab.sample_palette(p, v)
    elif shader == "comet_fall":
        head = (1.0 - ((t * 0.16 + g * 0.13 + entry.wall_index * 0.07) % 1.0))
        v = max(0.12, 1.0 - abs(y - head) * 4.2)
        rgb = _mix(p[0], p[-1], v)
    elif shader == "dusk_bloom":
        center = 0.5 + 0.16 * math.sin(phase * 0.45)
        v = max(0.14, 1.0 - abs(y - center) * 2.1) * (0.55 + 0.25 * math.sin(phase))
        rgb = color_lab.sample_palette(p, v)
    elif shader == "magma_column":
        v = max(0.12, (1.0 - y) ** 1.15 * (0.42 + 0.38 * math.sin(phase * 1.8 + y * 7.5)))
        rgb = color_lab.sample_palette(p, 1.0 - y)
        rgb = _mix(p[0], rgb, v)
    elif shader == "twin_helix":
        a = (math.sin(y * 8.0 + phase * 2.0) + 1) / 2
        b = (math.sin(y * 8.0 - phase * 2.0 + math.pi) + 1) / 2
        rgb = _mix(_mix(p[0], p[-1], a), p[min(1, len(p) - 1)], b * 0.55)
    elif shader == "ribbon_drift":
        v = (math.sin(phase + y * 2.1 + entry.wall_index * 0.8) + 1) / 2
        rgb = color_lab.sample_palette(p, v)
    else:
        rgb = _mix(p[g % len(p)], p[(g + 1) % len(p)], y)
    return _clamp_rgb([c * amp for c in rgb])


def render_frames(entries: list[DdpEntry], shader="liquid_gradient", mood="", composition_mode="unison", intensity=0.6, seed=None, t=0.0, colors=None) -> dict[str, bytes]:
    lengths: dict[str, int] = {}
    for e in entries:
        lengths[e.controller] = max(lengths.get(e.controller, 0), e.ddp_offset + e.ddp_length)
    frames = {ctrl: bytearray(n * 3) for ctrl, n in lengths.items()}
    look = color_lab.build_look(mood=mood, colors=colors, shader=shader, composition_mode=composition_mode, intensity=intensity, seed=seed)
    for e in entries:
        for i in range(e.pixels):
            rgb = pixel_color(look["shader"], e, i, t, mood, look["intensity"], look["composition_mode"], seed, look["colors"])
            pos = (e.ddp_offset + i) * 3
            frames[e.controller][pos:pos + 3] = bytes(rgb)
    return {k: bytes(v) for k, v in frames.items()}


class RealtimeRunner(threading.Thread):
    def __init__(self, fleet, shader="liquid_gradient", mood="", composition_mode="unison", intensity=0.6, fps=24, duration_s=60, seed=None, transport=None, colors=None, energy="", motion=""):
        super().__init__(daemon=True)
        look = color_lab.build_look(mood=mood, energy=energy, motion=motion, colors=colors, shader=shader, composition_mode=composition_mode, intensity=intensity, seed=seed)
        self.fleet = fleet
        self.shader = look["shader"]
        self.mood = mood
        self.energy = energy
        self.motion = motion
        self.colors = look["colors"]
        self.composition_mode = look["composition_mode"]
        self.intensity = look["intensity"]
        self.fps = min(40, max(1, int(fps or 24)))
        self.duration_s = min(900, max(0.1, float(duration_s or 60)))
        self.seed = seed
        self.transport = transport or UdpTransport()
        self.stop_event = threading.Event()
        self.sent_frames = 0
        self.entries = ddp_topology(fleet)
        self.hosts = {c.name: _host_ip(c.host) for c in fleet.controllers}

    def run(self):
        start = time.monotonic(); next_tick = start; seq = 0; prev: dict[str, bytes] | None = None
        while not self.stop_event.is_set() and time.monotonic() - start < self.duration_s:
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(0.01, next_tick - now)); continue
            frames = render_frames(self.entries, self.shader, self.mood, self.composition_mode, self.intensity, self.seed, now - start, self.colors)
            if prev:
                frames = {k: _smooth(prev.get(k), v) for k, v in frames.items()}
            for ctrl, data in frames.items():
                for pkt in build_ddp_packets(data, 0, seq):
                    self.transport.sendto(pkt, (self.hosts[ctrl], DDP_PORT))
            prev = frames; self.sent_frames += 1; seq = (seq + 1) % 256
            next_tick += 1 / self.fps
            if time.monotonic() - next_tick > 1 / self.fps:
                next_tick = time.monotonic()
        look_memory.record_look(source="realtime", action="realtime_start", mood=self.mood, parameters={"shader": self.shader, "composition_mode": self.composition_mode, "fps": self.fps, "duration_s": self.duration_s, "seed": self.seed, "colors": self.colors}, summary=f"realtime {self.shader}")

    def stop(self):
        self.stop_event.set()


def _smooth(prev: bytes | None, cur: bytes, max_delta=32) -> bytes:
    if prev is None or len(prev) != len(cur): return cur
    out = bytearray(len(cur))
    for i, v in enumerate(cur):
        p = prev[i]; out[i] = p + max(-max_delta, min(max_delta, v - p))
    return bytes(out)


_runner: RealtimeRunner | None = None
_lock = threading.Lock()


def realtime_start(fleet, **kwargs) -> str:
    global _runner
    with _lock:
        realtime_stop()
        _runner = RealtimeRunner(fleet, **kwargs)
        _runner.start()
    return f"Realtime started: {_runner.shader} at {_runner.fps} fps for {_runner.duration_s:g}s with {len(_runner.colors)} colors."


def realtime_stop() -> str:
    global _runner
    if _runner:
        _runner.stop(); _runner.join(timeout=2)
        try: _runner.transport.close()
        except Exception: pass
        look_memory.record_look(source="realtime", action="realtime_stop", summary="realtime stopped")
        _runner = None
        return "Realtime stopped."
    return "Realtime not running."


def realtime_status() -> dict:
    r = _runner
    return {
        "running": bool(r and r.is_alive()),
        "shader": getattr(r, "shader", None),
        "mood": getattr(r, "mood", None),
        "colors": list(getattr(r, "colors", []) or []),
        "composition_mode": getattr(r, "composition_mode", None),
        "intensity": getattr(r, "intensity", None),
        "seed": getattr(r, "seed", None),
        "fps": getattr(r, "fps", None),
        "sent_frames": getattr(r, "sent_frames", 0),
    }
