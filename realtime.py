#!/usr/bin/env python3
"""Safe local DDP realtime renderer for Lightss."""

from __future__ import annotations

import colorsys
import logging
import math
import random
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import color_lab
import fleet as fleet_mod
import look_memory

logger = logging.getLogger("realtime")

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


def render_frames(entries: list[DdpEntry], shader="liquid_gradient", mood="", composition_mode=None, intensity=0.6, seed=None, t=0.0, colors=None) -> dict[str, bytes]:
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
    def __init__(self, fleet, shader="liquid_gradient", mood="", composition_mode=None, intensity=0.6, fps=24, duration_s=60, seed=None, transport=None, colors=None, energy="", motion=""):
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
        # Topology first: if it rejects the fleet config, no socket is leaked.
        self.entries = ddp_topology(fleet)
        self.transport = transport or UdpTransport()
        self.stop_event = threading.Event()
        self.sent_frames = 0
        self.hosts = {c.name: _host_ip(c.host) for c in fleet.controllers}

    def run(self):
        start = time.monotonic(); next_tick = start; seq = 0; prev: dict[str, bytes] | None = None
        send_errors = 0
        while not self.stop_event.is_set() and time.monotonic() - start < self.duration_s:
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(0.01, next_tick - now)); continue
            frames = render_frames(self.entries, self.shader, self.mood, self.composition_mode, self.intensity, self.seed, now - start, self.colors)
            if prev:
                frames = {k: _smooth(prev.get(k), v) for k, v in frames.items()}
            try:
                for ctrl, data in frames.items():
                    for pkt in build_ddp_packets(data, 0, seq):
                        self.transport.sendto(pkt, (self.hosts[ctrl], DDP_PORT))
            except OSError as exc:
                # Transient network failures must not silently kill a show;
                # only a sustained outage stops the renderer.
                send_errors += 1
                if send_errors <= 3 or send_errors % 50 == 0:
                    logger.warning("realtime DDP send error (#%d): %s", send_errors, exc)
                if send_errors >= 100:
                    logger.error("realtime DDP aborting after %d consecutive send errors", send_errors)
                    break
                next_tick += 1 / self.fps
                continue
            send_errors = 0
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


def _unit_float(value: Any, default: float = 0.0) -> float:
    """Return a finite number clamped to 0..1 for untrusted audio/config data."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(number):
        return default
    return min(1.0, max(0.0, number))


def _expanded_palette(colors: Any) -> list[tuple[int, int, int]]:
    """Normalize arbitrary color input into stable stops for smooth live blending."""
    palette = color_lab.normalize_palette(colors)
    return [color_lab.sample_palette(palette, index / 4) for index in range(5)]


def _sample_music_palette(palette, amount):
    """Select an authored color, never manufacture a rainbow between stops."""
    index = min(len(palette) - 1, int(max(0.0, min(1.0, amount)) * len(palette)))
    return color_lab.clamp_rgb(palette[index])


def _music_palette(colors, colorfulness):
    """Lift existing chroma without recoloring neutrals or changing chosen hues."""
    stops = color_lab.normalize_palette(colors, min_stops=1)
    shaped = []
    for rgb in stops:
        hue, sat, value = colorsys.rgb_to_hsv(*(v / 210 for v in rgb))
        if sat > .05:
            sat += (1.0 - sat) * colorfulness
        shaped.append(color_lab.clamp_rgb(tuple(v * 210 for v in colorsys.hsv_to_rgb(hue, sat, value))))
    # Fixed-size targets support smooth transitions between different stop counts.
    return [shaped[round(i * (len(shaped) - 1) / 4)] for i in range(5)]


def _audio_group_offset(entry: DdpEntry, mode: str) -> float:
    """Give composition modes stable spatial offsets without time/random input."""
    index = entry.wall_index
    if mode == "independent":
        group = index
    elif mode == "pairs":
        group = index // 2
    elif mode == "center_vs_outer":
        group = 0 if index in (1, 2) else 1
    elif mode == "left_vs_right":
        group = 0 if index < 2 else 1
    elif mode == "alternating":
        group = index % 2
    elif mode == "random_groups":
        # A fixed hash-like mapping keeps frames deterministic between runners.
        group = (index * 5 + 1) % 3
    else:
        group = 0
    return group * 0.37


class AudioReactiveRunner(threading.Thread):
    """Direct, topology-aware DDP painter driven by WLED audio snapshots.

    Unlike :class:`RealtimeRunner`, this renderer has no wall-clock animation.
    Its phase advances only while smoothed audio energy exists, so silence
    converges to a dim, stationary background instead of an endless effect.
    """

    MAX_CONSECUTIVE_ERRORS = 100
    RGB_SLEW = 18
    MAX_BRIGHTNESS = 1.0
    MOTIONS = {"auto", "flow", "punch", "chase", "spectrum", "comet", "ripple"}
    DEFAULT_COLORS = ((210, 35, 0), (0, 150, 210))

    def __init__(
        self,
        fleet,
        audio_source: Callable[[], dict[str, Any]],
        fps=30,
        brightness=0.6,
        colors=None,
        transport=None,
    ):
        super().__init__(daemon=True, name="audio-reactive-ddp")
        if not callable(audio_source):
            raise TypeError("audio_source must be callable")

        # Validate topology before allocating the default socket.
        self.fleet = fleet
        self.entries = ddp_topology(fleet)
        self.hosts = {controller.name: _host_ip(controller.host) for controller in fleet.controllers}
        self.audio_source = audio_source
        self.transport = transport or UdpTransport()
        self.stop_event = threading.Event()
        self._lock = threading.RLock()

        try:
            requested_fps = int(fps)
        except (TypeError, ValueError, OverflowError):
            requested_fps = 30
        self.fps = min(40, max(1, requested_fps))
        self.brightness = self._clean_brightness(brightness, 0.6)
        self._brightness_current = self.brightness
        self.colors = color_lab.normalize_palette(colors or self.DEFAULT_COLORS, min_stops=1)
        self.motion = "auto"
        self.speed = 1.0
        self.intensity = .85
        self.colorfulness = .9
        self._active_motion = "flow"
        self._pending_motion = "flow"
        self._pending_motion_s = 0.0
        self._palette_target = _music_palette(self.colors, self.colorfulness)
        self._palette_current = [tuple(color) for color in self._palette_target]
        self.composition_mode = "unison"

        self.sent_frames = 0
        self.audio_active = False
        self._last_frames: dict[str, bytes] = {}
        self.beat_count = 0
        self.bpm = 0.0
        self.bpm_confidence = 0.0
        self.energy = 0.0
        self.bass = 0.0
        self.mid = 0.0
        self.treble = 0.0
        self.last_error: str | None = None

        self._beat_envelope = 0.0
        self._peak_envelope = 0.0
        self._motion_phase = 0.0
        self._beat_phase = 0.0
        self._beat_times: deque[float] = deque(maxlen=12)
        self._last_receive_sequence: Any = object()
        self._beat_latched = False
        self.band_gains = [1.0] * 16
        self.band_levels = [0.0] * 16
        self._accent_levels = [0.0] * 16
        self.accent_count = 0
        self.last_accent_band = None

    @classmethod
    def _clean_brightness(cls, value: Any, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = default
        if not math.isfinite(number):
            number = default
        return min(cls.MAX_BRIGHTNESS, max(0.0, number))

    def update_look(self, colors=None, brightness=None, composition_mode=None,
                    motion=None, speed=None, intensity=None, colorfulness=None, band_gains=None):
        """Change the target look without replacing the runner or resetting phase."""
        with self._lock:
            if colors is not None:
                self.colors = color_lab.normalize_palette(colors, min_stops=1)
            if colorfulness is not None:
                self.colorfulness = _unit_float(colorfulness, self.colorfulness)
            if colors is not None or colorfulness is not None:
                self._palette_target = _music_palette(self.colors, self.colorfulness)
            if motion is not None:
                self.motion = motion if isinstance(motion, str) and motion in self.MOTIONS else "auto"
                if self.motion != "auto":
                    self._active_motion = self.motion
            if speed is not None:
                self.speed = max(.25, _unit_float(speed / 4 if isinstance(speed, (int, float)) else None, self.speed / 4) * 4)
            if band_gains is not None:
                self.band_gains = list(band_gains)
            if intensity is not None:
                self.intensity = _unit_float(intensity, self.intensity)
            if brightness is not None:
                self.brightness = self._clean_brightness(brightness, self.brightness)
            if composition_mode is not None:
                requested = str(composition_mode).strip().lower()
                self.composition_mode = requested if requested in COMPOSITION_MODES else "unison"

    def accent(self, band, strength=1.0):
        """Retrigger an independent short-lived frequency accent on every click."""
        if type(band) is not int or not 0 <= band < 16:
            raise ValueError('band must be an integer from 0 through 15.')
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError('strength must be between 0 and 1.')
        with self._lock:
            self._accent_levels[band] = float(strength)
            self.accent_count += 1
            self.last_accent_band = band

    def status(self) -> dict[str, Any]:
        with self._lock:
            preview = []
            for entry in self.entries:
                data = self._last_frames.get(entry.controller)
                if data is None:
                    continue
                count = min(32, entry.pixels)
                colors = []
                for index in range(count):
                    pixel = round(index * (entry.pixels - 1) / max(1, count - 1))
                    offset = (entry.ddp_offset + pixel) * 3
                    colors.append(list(data[offset:offset + 3]))
                preview.append({'channel': entry.channel, 'controller': entry.controller, 'colors': colors})
            return {
                "running": self.is_alive(),
                "sent_frames": self.sent_frames,
                "audio_active": self.audio_active,
                "preview": preview,
                "motion": self._active_motion,
                "requested_motion": self.motion,
                "speed": self.speed,
                "intensity": self.intensity,
                "colorfulness": self.colorfulness,
                "colors": [list(rgb) for rgb in self._palette_target],
                "band_gains": list(self.band_gains),
                "band_levels": [round(v, 4) for v in self.band_levels],
                "accent_levels": [round(v, 4) for v in self._accent_levels],
                "accent_count": self.accent_count,
                "last_accent_band": self.last_accent_band,
                "beat_count": self.beat_count,
                "bpm": round(self.bpm, 1),
                "bpm_confidence": round(self.bpm_confidence, 3),
                "energy": round(self.energy, 4),
                "bass": round(self.bass, 4),
                "mid": round(self.mid, 4),
                "treble": round(self.treble, 4),
                "last_error": self.last_error,
            }

    def stop(self):
        self.stop_event.set()

    @staticmethod
    def _band(fft: list[float], start: int, stop: int) -> float:
        values = fft[start:stop]
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _follow(current: float, target: float, dt: float, attack: float, release: float) -> float:
        tau = attack if target > current else release
        amount = 1.0 - math.exp(-dt / max(0.001, tau))
        result = current + (target - current) * amount
        # Quantize the inaudible tail to zero.  Below this point it can only
        # cause one-byte shimmer, not useful motion, after a source goes stale.
        return 0.0 if target == 0.0 and result < 0.02 else result

    def _record_beat(self, now: float) -> None:
        self.beat_count += 1
        self._beat_envelope = min(1.0, self._beat_envelope + 0.58)
        self._beat_phase = 0.0
        self._beat_times.append(now)
        if len(self._beat_times) < 3:
            self.bpm_confidence = max(self.bpm_confidence, 0.15 * (len(self._beat_times) - 1))
            return
        intervals = [
            right - left
            for left, right in zip(self._beat_times, list(self._beat_times)[1:])
            if 0.25 <= right - left <= 2.0
        ]
        if len(intervals) < 2:
            return
        ordered = sorted(intervals)
        median = ordered[len(ordered) // 2]
        raw_bpm = 60.0 / median
        while raw_bpm < 60.0:
            raw_bpm *= 2.0
        while raw_bpm > 190.0:
            raw_bpm /= 2.0
        deviation = sum(abs(interval - median) for interval in intervals) / len(intervals)
        confidence = min(1.0, len(intervals) / 7.0) * max(0.0, 1.0 - deviation / median)
        self.bpm = raw_bpm if self.bpm == 0.0 else self.bpm * 0.72 + raw_bpm * 0.28
        self.bpm_confidence = confidence

    def _consume_snapshot(self, raw: Any, dt: float, now: float) -> None:
        snapshot = raw if isinstance(raw, dict) else {}
        active = bool(snapshot.get("active", False))
        level = _unit_float(snapshot.get("level")) if active else 0.0
        raw_fft = snapshot.get("fft", ())
        if not isinstance(raw_fft, (list, tuple)):
            raw_fft = ()
        fft = [_unit_float(value) for value in list(raw_fft)[:16]]
        fft.extend([0.0] * (16 - len(fft)))

        with self._lock:
            fft = [min(1.0, value * gain) for value, gain in zip(fft, self.band_gains)]
            self.band_levels = [self._follow(old, value if active else 0.0, dt, .025, .16)
                                for old, value in zip(self.band_levels, fft)]
            self._accent_levels = [self._follow(value, 0.0, dt, .01, .45) for value in self._accent_levels]
        bass_target = self._band(fft, 0, 4) if active else 0.0
        mid_target = self._band(fft, 4, 10) if active else 0.0
        treble_target = self._band(fft, 10, 16) if active else 0.0
        spectral = bass_target * 0.42 + mid_target * 0.34 + treble_target * 0.24
        energy_target = max(level, spectral * 0.88) if active else 0.0

        with self._lock:
            self.audio_active = active
            self.energy = self._follow(self.energy, energy_target, dt, 0.055, 0.14)
            self.bass = self._follow(self.bass, bass_target, dt, 0.045, 0.16)
            self.mid = self._follow(self.mid, mid_target, dt, 0.065, 0.18)
            self.treble = self._follow(self.treble, treble_target, dt, 0.035, 0.11)

            sequence = snapshot.get("receive_sequence", snapshot.get("frame_counter"))
            beat = active and bool(snapshot.get("beat", False))
            if sequence is not None:
                is_new = sequence != self._last_receive_sequence
                self._last_receive_sequence = sequence
                if beat and is_new:
                    self._record_beat(now)
            else:
                if beat and not self._beat_latched:
                    self._record_beat(now)
                self._beat_latched = beat

            if active and bool(snapshot.get("peak", False)):
                self._peak_envelope = min(1.0, self._peak_envelope + 0.34)
            self._beat_envelope = self._follow(self._beat_envelope, 0.0, dt, 0.01, 0.19)
            self._peak_envelope = self._follow(self._peak_envelope, 0.0, dt, 0.01, 0.12)

            # Both phases are signal clocks. Once the envelopes reach zero,
            # constant silence produces byte-identical frames forever.
            # A sustained section change, not a playlist timer, selects motion.
            if self.motion == "auto":
                desired = ("punch" if self.energy > .5 and self.bass > .3 else
                           "comet" if self.treble > .3 and self.energy > .3 else
                           "spectrum" if self.mid > .25 and self.bass < .2 else
                           "ripple" if self.bass > .2 and .4 < self.energy < .5 else
                           "chase" if self.treble > .12 and self.energy > .18 else "flow")
                if desired != self._pending_motion:
                    self._pending_motion = desired
                    self._pending_motion_s = 0.0
                self._pending_motion_s += dt
                if self._pending_motion_s >= 1.5:
                    self._active_motion = desired
            drive = math.sqrt(self.energy) * (0.45 + self.bass * 0.85 + self.treble * 0.35)
            drive *= self.speed
            if drive > 0.001:
                self._motion_phase = (self._motion_phase + dt * drive * 3.1) % (math.tau * 128)
            if self.bpm > 0 and self.bpm_confidence > 0:
                self._beat_phase = (self._beat_phase + dt * self.bpm / 60.0) % 1.0
            elif drive > 0.001:
                self._beat_phase = (self._beat_phase + dt * drive) % 1.0

    def _blend_look(self) -> tuple[list[tuple[int, int, int]], float, str]:
        with self._lock:
            self._palette_current = [
                tuple(c + (t - c) * .10 for c, t in zip(current, target))
                for current, target in zip(self._palette_current, self._palette_target)
            ]
            self._brightness_current += (self.brightness - self._brightness_current) * 0.12
            return list(self._palette_current), self._brightness_current, self.composition_mode

    def _render_frames(self) -> dict[str, bytes]:
        palette, brightness, mode = self._blend_look()
        lengths: dict[str, int] = {}
        for entry in self.entries:
            lengths[entry.controller] = max(
                lengths.get(entry.controller, 0), entry.ddp_offset + entry.ddp_length
            )
        frames = {controller: bytearray(length * 3) for controller, length in lengths.items()}

        with self._lock:
            energy, bass, mid, treble = self.energy, self.bass, self.mid, self.treble
            beat, peak = self._beat_envelope, self._peak_envelope
            motion, beat_phase = self._motion_phase, self._beat_phase

        with self._lock:
            motion_type, intensity = self._active_motion, self.intensity
            bands, accents = list(self.band_levels), list(self._accent_levels)
        drive = math.sqrt(energy)
        dynamics = .5 + .5 * intensity
        for entry in self.entries:
            offset = _audio_group_offset(entry, mode) * 3
            for pixel in range(entry.pixels):
                y = 0.0 if entry.pixels <= 1 else pixel / (entry.pixels - 1)
                if entry.pixel_zero != "bottom":
                    y = 1.0 - y
                texture = (math.sin(y * math.tau * 1.6 - motion + offset) + 1) / 2
                head = (motion / math.tau + offset / math.tau) % 1
                travel = math.exp(-((y - head) / .13) ** 2)
                bloom_center = .2 + .55 * (math.sin(motion + offset) + 1) / 2
                bloom = math.exp(-((y - bloom_center) / .23) ** 2)
                detail = treble * (math.sin(y * math.tau * 7 + motion * 2 + offset) + 1) / 2
                if motion_type == "chase":
                    luminance = .06 + dynamics * (drive * (.22 + .9 * travel) + detail * .5)
                    color_pos = travel * .95
                elif motion_type == "punch":
                    # Localized bass blooms and beat fronts, never whole-wall flashes.
                    front = math.exp(-((y - beat_phase) / .2) ** 2)
                    luminance = .06 + dynamics * (drive * .3 + bass * .85 * bloom + beat * .5 * front + detail * .25)
                    color_pos = max(bloom * bass, front * beat)
                elif motion_type == "spectrum":
                    band = min(15, int(y * 16))
                    level = bands[(band + int(offset * 3)) % 16]
                    cell = (y * 16) % 1
                    filled = max(0.0, min(1.0, (level - cell * .55) * 8))
                    luminance = .025 + dynamics * (level * .9 + filled * .45)
                    color_pos = .95 if level > .55 else 0.0
                elif motion_type == "comet":
                    distance = (head - y) % 1
                    tail = math.exp(-distance / (.08 + mid * .25))
                    luminance = .025 + dynamics * (tail * (drive + beat * .7) + detail * .25)
                    color_pos = .95 if distance < .08 else 0.0
                elif motion_type == "ripple":
                    radius = (motion / math.tau) % .8
                    ring = math.exp(-((abs(y - .5) - radius) / (.045 + bass * .09)) ** 2)
                    luminance = .025 + dynamics * (ring * (drive + bass * .7) + detail * .25)
                    color_pos = .95 if ring > .7 else 0.0
                else:
                    luminance = .06 + dynamics * (drive * (.45 + .5 * texture) + bass * .5 * bloom + detail * .25)
                    color_pos = .95 if detail > .25 else (texture * mid)
                # Each clicked FFT band paints its own expanding local wave. This
                # is a transient layer, not a preset change or a second DDP writer.
                accent = max((amount * math.exp(-((abs(y - index / 15) - (1 - amount) * .35) / .075) ** 2)
                              for index, amount in enumerate(accents)), default=0.0)
                luminance = min(1.0, luminance + accent * .95)
                if accent > .15:
                    color_pos = .95
                # Spectral motion colors and musical peaks share one final gain.
                # No fixed white overlay or second hidden RGB dimmer.
                color = _sample_music_palette(palette, min(1, color_pos))
                luminance = min(1.0, luminance + peak * travel * .12 * intensity)
                rgb = _clamp_rgb(channel * brightness * luminance for channel in color)
                position = (entry.ddp_offset + pixel) * 3
                frames[entry.controller][position:position + 3] = bytes(rgb)
        return {controller: bytes(data) for controller, data in frames.items()}

    def run(self):
        interval = 1.0 / self.fps
        next_tick = time.monotonic()
        previous: dict[str, bytes] | None = None
        sequence = 0
        source_errors = 0
        send_errors = 0
        try:
            while not self.stop_event.is_set():
                delay = next_tick - time.monotonic()
                if delay > 0 and self.stop_event.wait(delay):
                    break
                now = time.monotonic()
                try:
                    raw_snapshot = self.audio_source()
                except Exception as exc:
                    source_errors += 1
                    with self._lock:
                        self.last_error = f"audio source: {exc}"
                    if source_errors <= 3 or source_errors % 25 == 0:
                        logger.warning("audio reactive source error (#%d): %s", source_errors, exc)
                    raw_snapshot = {"active": False}
                    if source_errors >= self.MAX_CONSECUTIVE_ERRORS:
                        break
                else:
                    source_errors = 0

                self._consume_snapshot(raw_snapshot, interval, now)
                frames = self._render_frames()
                if previous:
                    frames = {
                        controller: _smooth(previous.get(controller), data, self.RGB_SLEW)
                        for controller, data in frames.items()
                    }
                try:
                    for controller, data in frames.items():
                        for packet in build_ddp_packets(data, 0, sequence):
                            self.transport.sendto(packet, (self.hosts[controller], DDP_PORT))
                except (OSError, RuntimeError) as exc:
                    send_errors += 1
                    with self._lock:
                        self.last_error = f"DDP send: {exc}"
                    if send_errors <= 3 or send_errors % 25 == 0:
                        logger.warning("audio reactive DDP send error (#%d): %s", send_errors, exc)
                    if send_errors >= self.MAX_CONSECUTIVE_ERRORS:
                        break
                else:
                    send_errors = 0
                    previous = frames
                    with self._lock:
                        self._last_frames = dict(frames)
                        self.sent_frames += 1
                    sequence = (sequence + 1) % 256

                next_tick += interval
                if time.monotonic() - next_tick > interval:
                    next_tick = time.monotonic()
        except Exception as exc:
            # Malformed dependency behavior is visible to the director instead
            # of disappearing as an unreported thread exception.
            with self._lock:
                self.last_error = f"renderer: {exc}"
            logger.exception("audio reactive renderer stopped unexpectedly")
        finally:
            try:
                self.transport.close()
            except Exception as exc:
                with self._lock:
                    self.last_error = f"transport close: {exc}"


_runner: RealtimeRunner | None = None
_frame_runner: StaticFrameRunner | None = None
_lock = threading.Lock()


class StaticFrameRunner(threading.Thread):
    """Re-stream one fixed per-controller bus frame over DDP.

    WLED 0.15+ renders a JSON ``seg.i`` write for a single frame (the old
    freeze flag is set but never honored), so an exact per-LED picture only
    persists while frames keep arriving. This runner re-sends the same frames
    at a low bounded rate — persistence, not animation. It owns whole
    controllers while running (DDP realtime overrides every segment), so
    painting one strip blanks its controller's other strips for the duration.
    """

    MAX_CONSECUTIVE_ERRORS = 100

    def __init__(self, fleet, frames, fps=6, duration_s=300, transport=None):
        super().__init__(daemon=True, name="static-frame-ddp")
        self.fleet = fleet
        # Validate topology before allocating the default socket.
        self.entries = ddp_topology(fleet)
        lengths: dict[str, int] = {}
        for entry in self.entries:
            lengths[entry.controller] = max(lengths.get(entry.controller, 0), entry.ddp_offset + entry.ddp_length)
        expected = {name: length * 3 for name, length in lengths.items()}
        if not isinstance(frames, dict) or not frames:
            raise ValueError("frame_start requires a dict of {controller: bytes} frames.")
        for controller, data in frames.items():
            if controller not in expected:
                raise ValueError(f"Unknown controller {controller!r} in frame set (known: {sorted(expected)}).")
            if len(data) != expected[controller]:
                raise ValueError(
                    f"Frame for {controller!r} is {len(data)} bytes, expected {expected[controller]} "
                    f"({lengths[controller]} pixels RGB)."
                )
        self.frames = {name: bytes(data) for name, data in frames.items()}
        self.fps = min(40, max(1, int(fps or 6)))
        self.duration_s = min(900, max(1.0, float(duration_s or 300)))
        self.transport = transport or UdpTransport()
        self.stop_event = threading.Event()
        self.sent_frames = 0
        self.hosts = {controller.name: _host_ip(controller.host) for controller in fleet.controllers}

    def run(self):
        start = time.monotonic(); next_tick = start; seq = 0
        send_errors = 0
        while not self.stop_event.is_set() and time.monotonic() - start < self.duration_s:
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(0.01, next_tick - now)); continue
            try:
                for controller, data in self.frames.items():
                    for packet in build_ddp_packets(data, 0, seq):
                        self.transport.sendto(packet, (self.hosts[controller], DDP_PORT))
            except OSError as exc:
                send_errors += 1
                if send_errors <= 3 or send_errors % 50 == 0:
                    logger.warning("static frame DDP send error (#%d): %s", send_errors, exc)
                if send_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    logger.error("static frame DDP aborting after %d consecutive send errors", send_errors)
                    break
                next_tick += 1 / self.fps
                continue
            send_errors = 0
            self.sent_frames += 1; seq = (seq + 1) % 256
            next_tick += 1 / self.fps
            if time.monotonic() - next_tick > 1 / self.fps:
                next_tick = time.monotonic()

    def stop(self):
        self.stop_event.set()


def realtime_start(fleet, **kwargs) -> str:
    global _runner
    try:
        import shows
        # DDP repaints every frame; a running show's steps would be invisible.
        shows.stop_show()
    except Exception:
        pass
    with _lock:
        realtime_stop()
        _runner = RealtimeRunner(fleet, **kwargs)
        _runner.start()
    return f"Realtime started: {_runner.shader} at {_runner.fps} fps for {_runner.duration_s:g}s with {len(_runner.colors)} colors."


def _stop_runner(runner: threading.Thread | None) -> None:
    if runner is None:
        return
    runner.stop()
    runner.join(timeout=2)
    try:
        runner.transport.close()
    except Exception:
        pass


def realtime_stop() -> str:
    global _runner, _frame_runner
    if _runner or _frame_runner:
        _stop_runner(_runner); _runner = None
        _stop_runner(_frame_runner); _frame_runner = None
        look_memory.record_look(source="realtime", action="realtime_stop", summary="realtime stopped")
        return "Realtime stopped."
    return "Realtime not running."


def frame_start(fleet, frames, fps=6, duration_s=300) -> str:
    """Persist one exact per-LED frame on the wall via bounded DDP streaming."""
    global _frame_runner
    try:
        import shows
        # DDP repaints every frame; a running show's steps would be invisible.
        shows.stop_show()
    except Exception:
        pass
    with _lock:
        frame_stop()
        _frame_runner = StaticFrameRunner(fleet, frames, fps=fps, duration_s=duration_s)
        _frame_runner.start()
    return (
        f"Per-LED frame streaming at {_frame_runner.fps} fps for {_frame_runner.duration_s:g}s "
        f"across {', '.join(sorted(_frame_runner.frames))}; stops on the next state write or realtime_stop."
    )


def frame_stop() -> str:
    global _frame_runner
    if _frame_runner:
        _stop_runner(_frame_runner)
        _frame_runner = None
        return "Static frame stopped."
    return "Static frame not running."


def frame_status() -> dict:
    r = _frame_runner
    return {
        "running": bool(r and r.is_alive()),
        "fps": getattr(r, "fps", None),
        "duration_s": getattr(r, "duration_s", None),
        "remaining_s": max(0.0, r.duration_s - r.sent_frames / max(1, r.fps)) if r else None,
        "sent_frames": getattr(r, "sent_frames", 0),
        "controllers": sorted(r.frames) if r else [],
    }


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
