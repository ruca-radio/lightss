#!/usr/bin/env python3
"""Small controller for a WLED-compatible bedroom light."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TypedDict

logger = logging.getLogger("lightctl")

DEFAULT_HOST = "http://10.27.27.110"
JSON_PATH = "/json"
STATE_PATH = "/json/state"
EFFECTS_PATH = "/json/effects"
PALETTES_PATH = "/json/palettes"
NODES_PATH = "/json/nodes"
LIVE_PATH = "/json/live"
CONFIG_PATH = "/json/cfg"
FXDATA_PATH = "/json/fxdata"
NETWORKS_PATH = "/json/net"
SAFE_EFFECTS = {
    2: "Breathe",
    8: "Colorloop",
    9: "Rainbow",
    12: "Fade",
    28: "Chase",
    30: "Chase Rainbow",
    33: "Rainbow Runner",
    37: "Chase 2",
    52: "Running Dual",
    54: "Chase 3",
    62: "Oscillate",
    63: "Pride 2015",
    64: "Juggle",
    67: "Colorwaves",
    74: "Lake",
    76: "Meteor Smooth",
    90: "Sinelon",
    92: "Sinelon Rainbow",
    98: "Pacifica",
    105: "Sine",
    108: "Flow",
    115: "Drift Rose",
    120: "Waving Cell",
    122: "Pixelwave",
    130: "Waterfall",
    162: "Drift",
    163: "Waverly",
    172: "Swirl",
    179: "Flow Stripe",
    183: "Wavesins",
}


# ---------------------------------------------------------------------------
# Typed payloads
# ---------------------------------------------------------------------------

class SegPayload(TypedDict, total=False):
    col: list[list[int]]
    fx: int
    sx: int


class WledPayload(TypedDict, total=False):
    on: bool
    bri: int
    seg: list[SegPayload]
    transition: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clamp_byte(value: int | float) -> int:
    return max(0, min(255, int(value)))


def normalize_host(host: str) -> str:
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def on_payload(enabled: bool, transition_ms: int = 0) -> WledPayload:
    payload: WledPayload = {"on": enabled}
    if transition_ms > 0:
        payload["transition"] = transition_ms
    return payload


def brightness_payload(brightness: int, transition_ms: int = 0) -> WledPayload:
    payload: WledPayload = {"bri": clamp_byte(brightness)}
    if transition_ms > 0:
        payload["transition"] = transition_ms
    return payload


def color_payload(
    red: int, green: int, blue: int, white: int = 0, transition_ms: int = 0
) -> WledPayload:
    payload: WledPayload = {
        "seg": [{"col": [[clamp_byte(red), clamp_byte(green), clamp_byte(blue), clamp_byte(white)]]}]
    }
    if transition_ms > 0:
        payload["transition"] = transition_ms
    return payload


def effect_payload(effect: int, speed: int = 128, transition_ms: int = 0) -> WledPayload:
    effect = int(effect)
    if effect not in SAFE_EFFECTS:
        allowed = ", ".join(f"{effect_id}={name}" for effect_id, name in SAFE_EFFECTS.items())
        raise ValueError(f"Effect {effect} is not allowed. Safe effects: {allowed}.")
    payload: WledPayload = {"seg": [{"fx": effect, "sx": clamp_byte(speed)}]}
    if transition_ms > 0:
        payload["transition"] = transition_ms
    return payload


def reactive_beat_payload(
    color: tuple[int, int, int, int],
    brightness: int,
    effect: int,
    speed: int,
    transition_ms: int = 0,
) -> WledPayload:
    return merge_payloads(
        on_payload(True, transition_ms=transition_ms),
        brightness_payload(brightness, transition_ms=transition_ms),
        color_payload(*color, transition_ms=transition_ms),
        effect_payload(effect, speed, transition_ms=transition_ms),
    )


def merge_payloads(*payloads: WledPayload) -> WledPayload:
    merged: WledPayload = {}
    for payload in payloads:
        for key, value in payload.items():
            if key == "seg" and key in merged:
                merged[key][0].update(value[0])  # type: ignore[index]
            else:
                merged[key] = value  # type: ignore[literal-required]
    return merged


# ---------------------------------------------------------------------------
# Built-in scenes
# ---------------------------------------------------------------------------

_builtin_scenes: dict[str, WledPayload] = {
    "warm": merge_payloads(
        on_payload(True), brightness_payload(180), color_payload(255, 150, 60, 120)
    ),
    "night": merge_payloads(
        on_payload(True), brightness_payload(25), color_payload(255, 70, 0, 0)
    ),
    "focus": merge_payloads(
        on_payload(True), brightness_payload(230), color_payload(255, 255, 220, 180)
    ),
    "ocean": merge_payloads(
        on_payload(True), brightness_payload(190), color_payload(0, 70, 255, 0)
    ),
    "party": merge_payloads(
        on_payload(True),
        brightness_payload(230),
        effect_payload(9, 180),
        color_payload(255, 0, 180, 0),
    ),
}


# ---------------------------------------------------------------------------
# Scene persistence
# ---------------------------------------------------------------------------

_SCENE_DIR = os.path.expanduser("~/.config/lightss")
_SCENE_PATH = os.path.join(_SCENE_DIR, "scenes.json")


def _load_scenes() -> dict[str, WledPayload]:
    try:
        with open(_SCENE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_scenes(scenes: dict[str, WledPayload]) -> None:
    os.makedirs(_SCENE_DIR, exist_ok=True)
    with open(_SCENE_PATH, "w", encoding="utf-8") as f:
        json.dump(scenes, f, indent=2)


def save_scene(name: str, payload: WledPayload) -> None:
    scenes = _load_scenes()
    scenes[name.strip().lower()] = payload
    _save_scenes(scenes)


def delete_scene(name: str) -> None:
    scenes = _load_scenes()
    scenes.pop(name.strip().lower(), None)
    _save_scenes(scenes)


def list_scenes() -> list[str]:
    return sorted(_load_scenes().keys())


def load_scene_payload(name: str) -> WledPayload:
    scenes = _load_scenes()
    key = name.strip().lower()
    if key in scenes:
        return scenes[key]
    raise ValueError(f"Unknown saved scene: {name}. Saved scenes: {', '.join(list_scenes()) or 'none'}.")


def scene_payload(name: str, transition_ms: int = 0) -> WledPayload:
    key = name.strip().lower()
    if key in _builtin_scenes:
        payload = _builtin_scenes[key]
    else:
        payload = load_scene_payload(name)
    if transition_ms > 0:
        payload = dict(payload)
        payload["transition"] = transition_ms
    return payload


# ---------------------------------------------------------------------------
# Schedule persistence
# ---------------------------------------------------------------------------

_SCHEDULE_PATH = os.path.join(_SCENE_DIR, "schedule.json")


def _load_schedule() -> list[dict]:
    try:
        with open(_SCHEDULE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_schedule(schedule: list[dict]) -> None:
    os.makedirs(_SCENE_DIR, exist_ok=True)
    with open(_SCHEDULE_PATH, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2)


def add_schedule(time_str: str, action: str, data: dict | None = None) -> None:
    schedule = _load_schedule()
    schedule.append({"time": time_str, "action": action, "data": data or {}})
    _save_schedule(schedule)


def remove_schedule(index: int) -> None:
    schedule = _load_schedule()
    if 0 <= index < len(schedule):
        schedule.pop(index)
    _save_schedule(schedule)


def list_schedule() -> list[dict]:
    return _load_schedule()


# ---------------------------------------------------------------------------
# Kelvin → RGBW
# ---------------------------------------------------------------------------

def kelvin_to_rgbw(kelvin: int) -> tuple[int, int, int, int]:
    """Approximate RGBW from Kelvin (2000–6500)."""
    kelvin = max(2000, min(6500, kelvin))
    temp = kelvin / 100.0
    if temp <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(temp) - 161.1195681661
        if temp <= 19:
            b = 0.0
        else:
            b = 138.5177312231 * math.log(temp - 10) - 305.0447927307
    else:
        r = 329.698727446 * ((temp - 60) ** -0.1332047592)
        g = 288.1221695283 * ((temp - 60) ** -0.0755148492)
        b = 255.0
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    # White channel peaks around 4000K
    w = int(255 * (1.0 - abs(kelvin - 4000) / 2500.0))
    w = max(0, min(255, w))
    return (r, g, b, w)


def hex_to_rgbw(hex_color: str) -> tuple[int, int, int, int]:
    """Parse #RRGGBB or #RRGGBBWW into RGBW tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        return (
            int(hex_color[0:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
            0,
        )
    if len(hex_color) == 8:
        return (
            int(hex_color[0:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
            int(hex_color[6:8], 16),
        )
    raise ValueError("Hex color must be #RRGGBB or #RRGGBBWW")


def random_scene_payload(transition_ms: int = 0) -> WledPayload:
    """Return a random safe built-in scene."""
    import random
    names = list(_builtin_scenes.keys())
    name = random.choice(names)
    return scene_payload(name, transition_ms=transition_ms)


class FadeTimer:
    """Gradually reduce brightness over a duration, then turn off."""

    def __init__(self, client: LightClient, duration_minutes: float, start_brightness: int | None = None) -> None:
        self.client = client
        self.duration_minutes = max(1, duration_minutes)
        self.start_brightness = start_brightness
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Fade timer is already running."
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return f"Fade timer started ({self.duration_minutes} min)."

    def stop(self) -> str:
        self._stop.set()
        return "Fade timer stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            start_bri = self.start_brightness
            if start_bri is None:
                state = self.client.get_state()
                start_bri = state.get("bri", 128)
            steps = int(self.duration_minutes * 6)  # update every 10s
            for i in range(steps + 1):
                if self._stop.is_set():
                    return
                bri = int(start_bri * (1 - i / steps))
                self.client.post_state(brightness_payload(bri))
                time.sleep(10)
            self.client.post_state(on_payload(False))
        except Exception:
            logger.exception("Fade timer error")


# ---------------------------------------------------------------------------
# WLED Info
# ---------------------------------------------------------------------------

INFO_PATH = "/json/info"


@dataclass
class WledInfo:
    name: str
    version: str
    led_count: int
    udp_port: int
    live: bool
    arch: str
    core: str
    free_heap: int
    uptime: int
    opt: int
    brand: str
    product: str
    mac: str
    ip: str

    @classmethod
    def from_dict(cls, data: dict) -> "WledInfo":
        return cls(
            name=data.get("name", "Unknown"),
            version=data.get("ver", "?"),
            led_count=data.get("leds", {}).get("count", 0),
            udp_port=data.get("udpport", 0),
            live=data.get("live", False),
            arch=data.get("arch", "?"),
            core=data.get("core", "?"),
            free_heap=data.get("freeheap", 0),
            uptime=data.get("uptime", 0),
            opt=data.get("opt", 0),
            brand=data.get("brand", "WLED"),
            product=data.get("product", "?"),
            mac=data.get("mac", ""),
            ip=data.get("ip", ""),
        )


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

PRESET_MIN = 1
PRESET_MAX = 250


def preset_payload(preset_id: int, transition_ms: int = 0) -> WledPayload:
    preset_id = int(preset_id)
    if not (PRESET_MIN <= preset_id <= PRESET_MAX):
        raise ValueError(f"Preset ID must be between {PRESET_MIN} and {PRESET_MAX}.")
    payload: WledPayload = {"ps": preset_id}
    if transition_ms > 0:
        payload["transition"] = transition_ms
    return payload


# ---------------------------------------------------------------------------
# Scene Cycle / Playlist
# ---------------------------------------------------------------------------

class CycleThread:
    """Auto-rotate through a list of scenes or presets at a given interval."""

    def __init__(
        self,
        client: LightClient,
        items: list[str] | None = None,
        interval_seconds: float = 60.0,
        mode: str = "scene",
    ) -> None:
        self.client = client
        self.items = items or list(_builtin_scenes.keys())
        self.interval_seconds = max(5.0, interval_seconds)
        self.mode = mode  # "scene" or "preset"
        self._index = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Cycle is already running."
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return f"Cycle started ({len(self.items)} items, {self.interval_seconds}s interval)."

    def stop(self) -> str:
        self._stop.set()
        return "Cycle stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        logger.info("Scene cycle started.")
        while not self._stop.is_set():
            try:
                item = self.items[self._index % len(self.items)]
                if self.mode == "preset":
                    self.client.post_state(preset_payload(int(item)))
                else:
                    self.client.post_state(scene_payload(item))
                logger.info("Cycled to: %s", item)
            except Exception:
                logger.exception("Cycle step failed")
            self._index += 1
            # Sleep in small chunks so stop is responsive
            for _ in range(int(self.interval_seconds)):
                if self._stop.is_set():
                    break
                time.sleep(1)


# ---------------------------------------------------------------------------
# Sunrise Simulator
# ---------------------------------------------------------------------------

class SunriseSimulator:
    """Gradually increase brightness and shift color temperature from warm to daylight."""

    def __init__(
        self,
        client: LightClient,
        duration_minutes: float = 30.0,
        start_kelvin: int = 2000,
        end_kelvin: int = 5000,
        max_brightness: int = 255,
    ) -> None:
        self.client = client
        self.duration_minutes = max(1, duration_minutes)
        self.start_kelvin = start_kelvin
        self.end_kelvin = end_kelvin
        self.max_brightness = clamp_byte(max_brightness)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Sunrise simulation is already running."
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return f"Sunrise started ({self.duration_minutes} min)."

    def stop(self) -> str:
        self._stop.set()
        return "Sunrise stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        logger.info("Sunrise simulation started.")
        try:
            steps = int(self.duration_minutes * 6)  # every 10s
            for i in range(steps + 1):
                if self._stop.is_set():
                    return
                progress = i / steps
                bri = int(self.max_brightness * progress)
                kelvin = int(self.start_kelvin + (self.end_kelvin - self.start_kelvin) * progress)
                rgbw = kelvin_to_rgbw(kelvin)
                payload = merge_payloads(
                    on_payload(True),
                    brightness_payload(bri),
                    color_payload(*rgbw),
                )
                self.client.post_state(payload)
                time.sleep(10)
        except Exception:
            logger.exception("Sunrise simulation error")


# ---------------------------------------------------------------------------
# Configuration file
# ---------------------------------------------------------------------------

_CONFIG_PATH = os.path.join(_SCENE_DIR, "config.json")


def load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(config: dict) -> None:
    os.makedirs(_SCENE_DIR, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

@dataclass
class LightClient:
    host: str = DEFAULT_HOST
    timeout: float = 2.5
    dry_run: bool = False

    def __post_init__(self) -> None:
        self.host = normalize_host(self.host)

    @property
    def json_url(self) -> str:
        return f"{self.host}{JSON_PATH}"

    @property
    def state_url(self) -> str:
        return f"{self.host}{STATE_PATH}"

    @property
    def info_url(self) -> str:
        return f"{self.host}{INFO_PATH}"

    @property
    def effects_url(self) -> str:
        return f"{self.host}{EFFECTS_PATH}"

    @property
    def palettes_url(self) -> str:
        return f"{self.host}{PALETTES_PATH}"

    @property
    def nodes_url(self) -> str:
        return f"{self.host}{NODES_PATH}"

    @property
    def live_url(self) -> str:
        return f"{self.host}{LIVE_PATH}"

    @property
    def config_url(self) -> str:
        return f"{self.host}{CONFIG_PATH}"

    @property
    def fxdata_url(self) -> str:
        return f"{self.host}{FXDATA_PATH}"

    @property
    def networks_url(self) -> str:
        return f"{self.host}{NETWORKS_PATH}"

    def _get_json_url(self, url: str) -> dict | list:
        request = urllib.request.Request(url, method="GET")
        with self._request_with_retry(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_json(self) -> dict:
        data = self._get_json_url(self.json_url)
        return data if isinstance(data, dict) else {}

    def get_info(self) -> dict:
        data = self._get_json_url(self.info_url)
        return data if isinstance(data, dict) else {}

    def get_effects(self) -> list[str]:
        data = self._get_json_url(self.effects_url)
        return [str(item) for item in data] if isinstance(data, list) else []

    def get_palettes(self) -> list[str]:
        data = self._get_json_url(self.palettes_url)
        return [str(item) for item in data] if isinstance(data, list) else []

    def get_nodes(self) -> dict:
        data = self._get_json_url(self.nodes_url)
        return data if isinstance(data, dict) else {}

    def get_live(self) -> dict:
        data = self._get_json_url(self.live_url)
        return data if isinstance(data, dict) else {}

    def get_config(self) -> dict:
        data = self._get_json_url(self.config_url)
        return data if isinstance(data, dict) else {}

    def get_fxdata(self) -> list[str]:
        data = self._get_json_url(self.fxdata_url)
        return [str(item) for item in data] if isinstance(data, list) else []

    def get_networks(self) -> dict:
        data = self._get_json_url(self.networks_url)
        return data if isinstance(data, dict) else {}

    def _snapshot_part(self, name: str, loader: Callable[[], dict | list]) -> dict | list:
        try:
            return loader()
        except Exception as exc:
            logger.warning("Could not read WLED %s snapshot: %s", name, exc)
            return {"error": str(exc)}

    def get_device_snapshot(self) -> dict:
        combined = self._snapshot_part("combined", self.get_json)
        if not isinstance(combined, dict):
            combined = {}
        return {
            "state": combined.get("state") or self._snapshot_part("state", self.get_state),
            "info": combined.get("info") or self._snapshot_part("info", self.get_info),
            "effects": combined.get("effects") or self._snapshot_part("effects", self.get_effects),
            "palettes": combined.get("palettes") or self._snapshot_part("palettes", self.get_palettes),
            "config": self._snapshot_part("config", self.get_config),
            "fxdata": self._snapshot_part("fxdata", self.get_fxdata),
            "nodes": self._snapshot_part("nodes", self.get_nodes),
            "live": self._snapshot_part("live", self.get_live),
            "networks": self._snapshot_part("networks", self.get_networks),
        }

    def _request_with_retry(
        self, request: urllib.request.Request, retries: int = 3
    ) -> urllib.request.addinfourl:
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                return urllib.request.urlopen(request, timeout=self.timeout)
            except urllib.error.URLError as exc:
                last_exc = exc
                wait = 0.2 * (2 ** attempt)
                logger.warning("Request failed (attempt %d/%d), retrying in %.1fs: %s", attempt + 1, retries, wait, exc)
                time.sleep(wait)
        raise RuntimeError(f"Could not reach light controller at {request.full_url}: {last_exc}") from last_exc

    def get_state(self) -> dict:
        request = urllib.request.Request(self.state_url, method="GET")
        with self._request_with_retry(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_state(self, payload: WledPayload) -> None:
        body = json.dumps(payload).encode("utf-8")
        if self.dry_run:
            logger.info("dry-run: %s", body.decode("utf-8"))
            return

        request = urllib.request.Request(
            self.state_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self._request_with_retry(request) as response:
            response.read()


# ---------------------------------------------------------------------------
# Audio-reactive mode
# ---------------------------------------------------------------------------

class ReactiveMode:
    def __init__(
        self,
        client: LightClient,
        palette: Sequence[tuple[int, int, int, int]] | None = None,
        min_interval: float = 0.12,
    ) -> None:
        self.client = client
        self.palette = palette or (
            (255, 0, 0, 0),
            (255, 90, 0, 0),
            (255, 0, 180, 0),
            (0, 80, 255, 0),
            (0, 255, 120, 0),
            (255, 120, 0, 180),
        )
        self.min_interval = min_interval
        self.effects = tuple(SAFE_EFFECTS)
        self.color_index = 0
        self.last_sent_at = 0.0

    def handle_beat(self, energy: float) -> None:
        now = time.monotonic()
        if now - self.last_sent_at < self.min_interval:
            return

        color = self.palette[self.color_index % len(self.palette)]
        effect = self.effects[self.color_index % len(self.effects)]
        self.color_index += 1
        brightness = clamp_byte(max(80, min(255, energy * 255)))
        speed = clamp_byte(round(80 + (energy * 110)))
        payload = reactive_beat_payload(color, brightness, effect, speed)
        self.client.post_state(payload)
        self.last_sent_at = now


class BeatDetector:
    def __init__(self, threshold: float = 1.55, floor: float = 0.015) -> None:
        self.threshold = threshold
        self.floor = floor
        self.baseline = 0.03
        self.last_beat_at = 0.0

    def update(self, rms: float) -> bool:
        rms = max(0.0, float(rms))
        self.baseline = (self.baseline * 0.92) + (rms * 0.08)
        now = time.monotonic()
        is_loud_enough = rms >= self.floor
        is_spike = rms > self.baseline * self.threshold
        is_spaced = now - self.last_beat_at > 0.16
        if is_loud_enough and is_spike and is_spaced:
            self.last_beat_at = now
            return True
        return False


class ReactiveThread:
    """Thread-safe wrapper to start/stop audio-reactive mode."""

    def __init__(
        self,
        client: LightClient,
        device: str | int | None = None,
        samplerate: int | None = None,
        level_callback: Callable[[float, bool], None] | None = None,
        error_callback: Callable[[BaseException], None] | None = None,
    ) -> None:
        self.client = client
        self.device = device
        self.samplerate = samplerate
        self.level_callback = level_callback
        self.error_callback = error_callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self.is_alive():
            return "Audio-reactive mode is already running."
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
        )
        self._thread.start()
        return "Audio-reactive mode started."

    def stop(self) -> str:
        self._stop.set()
        return "Audio-reactive mode stopped."

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            run_mode1(
                client=self.client,
                device=self.device,
                samplerate=self.samplerate,
                stop_event=self._stop,
                level_callback=self.level_callback,
            )
        except BaseException as exc:
            if self.error_callback is not None:
                self.error_callback(exc)
            else:
                logger.exception("Audio-reactive mode failed")


def run_mode1(
    client: LightClient,
    device: str | int | None = None,
    samplerate: int | None = None,
    stop_event: threading.Event | None = None,
    level_callback: Callable[[float, bool], None] | None = None,
) -> None:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise SystemExit(
            "Mode 1 needs audio dependencies. Install them with: "
            "python3 -m pip install -r requirements.txt"
        ) from exc

    detector = BeatDetector()
    mode = ReactiveMode(client)
    blocksize = 1024

    logger.info("Mode 1 listening on the default microphone. Press Ctrl+C to stop.")
    if isinstance(device, str) and device.isdigit():
        device = int(device)
    samplerate = resolve_input_samplerate(sd, device, samplerate)

    try:
        with sd.InputStream(
            device=device, channels=1, samplerate=samplerate, blocksize=blocksize
        ) as stream:
            while stop_event is None or not stop_event.is_set():
                try:
                    samples, overflowed = stream.read(blocksize)
                    if overflowed:
                        continue
                    rms = float(np.sqrt(np.mean(np.square(samples))))
                    energy = min(1.0, rms * 12.0)
                    beat = detector.update(rms)
                    if level_callback is not None:
                        level_callback(energy, beat)
                    if beat:
                        mode.handle_beat(energy)
                except Exception:
                    logger.exception("Error in audio processing loop")
                    time.sleep(0.1)
    except Exception:
        logger.exception("Fatal error opening audio stream")
        raise


def resolve_input_samplerate(
    sounddevice_module: object,
    device: str | int | None,
    samplerate: int | None,
) -> int:
    if samplerate is not None:
        return int(samplerate)
    device_info = sounddevice_module.query_devices(device=device, kind="input")
    return int(device_info.get("default_samplerate", 44100))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_rgbw(values: Iterable[str]) -> tuple[int, int, int, int]:
    parsed = [int(value) for value in values]
    if len(parsed) not in (3, 4):
        raise argparse.ArgumentTypeError("color needs R G B or R G B W")
    if len(parsed) == 3:
        parsed.append(0)
    return (parsed[0], parsed[1], parsed[2], parsed[3])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control the bedroom Wi-Fi LED controller.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Controller host, default {DEFAULT_HOST}")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON instead of sending it")
    parser.add_argument(
        "--transition", type=int, default=0, help="Transition time in milliseconds (0-65535)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("on", help="Turn lights on")
    subparsers.add_parser("off", help="Turn lights off")

    bri = subparsers.add_parser("bri", help="Set brightness, 0-255")
    bri.add_argument("value", type=int)

    color = subparsers.add_parser("color", help="Set RGBW color")
    color.add_argument("values", nargs="+", help="R G B or R G B W")

    fx = subparsers.add_parser("fx", help="Set a safe non-strobe built-in effect")
    fx.add_argument("effect", type=int, choices=tuple(SAFE_EFFECTS))
    fx.add_argument("--speed", "-s", type=int, default=128)

    scene = subparsers.add_parser("scene", help="Set a named scene")
    scene.add_argument("name")

    mode1 = subparsers.add_parser("mode1", help="Audio-reactive mode using the default microphone")
    mode1.add_argument("--device", help="Optional sounddevice input device name or index")
    mode1.add_argument("--samplerate", type=int, default=None)

    temp = subparsers.add_parser("temp", help="Set color temperature in Kelvin (2000-6500)")
    temp.add_argument("kelvin", type=int)

    save_scene = subparsers.add_parser("save-scene", help="Save current state as a named scene")
    save_scene.add_argument("name")

    delete_scene = subparsers.add_parser("delete-scene", help="Delete a saved scene")
    delete_scene.add_argument("name")

    schedule_parser = subparsers.add_parser("schedule", help="Manage scheduled lighting changes")
    schedule_sub = schedule_parser.add_subparsers(dest="schedule_action", required=True)

    sched_add = schedule_sub.add_parser("add", help="Add a schedule entry")
    sched_add.add_argument("time", help="Time in HH:MM format")
    sched_add.add_argument("action", choices=("on", "off", "scene"), help="Action to perform")
    sched_add.add_argument("--scene-name", help="Scene name (required if action=scene)")

    sched_list = schedule_sub.add_parser("list", help="List schedule entries")
    sched_remove = schedule_sub.add_parser("remove", help="Remove a schedule entry by index")
    sched_remove.add_argument("index", type=int)

    hex_cmd = subparsers.add_parser("hex", help="Set color from hex #RRGGBB or #RRGGBBWW")
    hex_cmd.add_argument("color", help="Hex color string")

    subparsers.add_parser("random", help="Set a random built-in scene")

    fade = subparsers.add_parser("fade-off", help="Gradually fade to off over N minutes")
    fade.add_argument("minutes", type=float, help="Duration in minutes")
    fade.add_argument("--brightness", type=int, help="Starting brightness (defaults to current)")

    preset = subparsers.add_parser("preset", help="Load a WLED preset (1-250)")
    preset.add_argument("id", type=int, help="Preset ID")

    cycle = subparsers.add_parser("cycle", help="Auto-rotate through scenes")
    cycle.add_argument("--items", nargs="+", help="Scene names to cycle (default: built-in scenes)")
    cycle.add_argument("--interval", type=float, default=60, help="Seconds between changes")
    cycle.add_argument("--mode", choices=("scene", "preset"), default="scene", help="Cycle mode")

    sunrise = subparsers.add_parser("sunrise", help="Gradual wake-up light simulation")
    sunrise.add_argument("--minutes", type=float, default=30, help="Duration in minutes")
    sunrise.add_argument("--start-kelvin", type=int, default=2000, help="Starting color temp")
    sunrise.add_argument("--end-kelvin", type=int, default=5000, help="Ending color temp")
    sunrise.add_argument("--brightness", type=int, default=255, help="Max brightness at end")

    info_cmd = subparsers.add_parser("info", help="Read WLED controller info")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = LightClient(args.host, dry_run=args.dry_run)
    transition_ms = getattr(args, "transition", 0)

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    try:
        if args.command == "on":
            client.post_state(on_payload(True, transition_ms=transition_ms))
        elif args.command == "off":
            client.post_state(on_payload(False, transition_ms=transition_ms))
        elif args.command == "bri":
            client.post_state(brightness_payload(args.value, transition_ms=transition_ms))
        elif args.command == "color":
            client.post_state(color_payload(*parse_rgbw(args.values), transition_ms=transition_ms))
        elif args.command == "fx":
            client.post_state(effect_payload(args.effect, args.speed, transition_ms=transition_ms))
        elif args.command == "scene":
            client.post_state(scene_payload(args.name, transition_ms=transition_ms))
        elif args.command == "mode1":
            run_mode1(client, device=args.device, samplerate=args.samplerate)
        elif args.command == "temp":
            client.post_state(color_payload(*kelvin_to_rgbw(args.kelvin), transition_ms=transition_ms))
        elif args.command == "save-scene":
            state = client.get_state()
            payload: WledPayload = {}
            for key in ("on", "bri", "seg", "transition"):
                if key in state:
                    payload[key] = state[key]  # type: ignore[literal-required]
            save_scene(args.name, payload)
            logger.info("Saved scene '%s'.", args.name)
        elif args.command == "delete-scene":
            delete_scene(args.name)
            logger.info("Deleted scene '%s'.", args.name)
        elif args.command == "schedule":
            if args.schedule_action == "add":
                data: dict = {}
                if args.action == "scene":
                    if not args.scene_name:
                        raise ValueError("--scene-name is required when action=scene")
                    data["scene"] = args.scene_name
                add_schedule(args.time, args.action, data)
                logger.info("Added schedule for %s.", args.time)
            elif args.schedule_action == "list":
                entries = list_schedule()
                if not entries:
                    logger.info("No scheduled entries.")
                else:
                    for i, entry in enumerate(entries):
                        logger.info("%d: %s -> %s %s", i, entry["time"], entry["action"], entry.get("data", ""))
            elif args.schedule_action == "remove":
                remove_schedule(args.index)
                logger.info("Removed schedule entry %d.", args.index)
        elif args.command == "hex":
            client.post_state(color_payload(*hex_to_rgbw(args.color), transition_ms=transition_ms))
        elif args.command == "random":
            client.post_state(random_scene_payload(transition_ms=transition_ms))
        elif args.command == "fade-off":
            timer = FadeTimer(client, args.minutes, start_brightness=args.brightness)
            timer.start()
            logger.info("Fading to off over %.1f minutes. Press Ctrl+C to stop.", args.minutes)
            try:
                while timer.is_alive():
                    time.sleep(1)
            except KeyboardInterrupt:
                timer.stop()
                logger.info("Fade timer cancelled.")
                return 130
        elif args.command == "preset":
            client.post_state(preset_payload(args.id, transition_ms=transition_ms))
        elif args.command == "cycle":
            cycler = CycleThread(
                client,
                items=args.items,
                interval_seconds=args.interval,
                mode=args.mode,
            )
            cycler.start()
            logger.info("Cycling scenes. Press Ctrl+C to stop.")
            try:
                while cycler.is_alive():
                    time.sleep(1)
            except KeyboardInterrupt:
                cycler.stop()
                logger.info("Cycle stopped.")
                return 130
        elif args.command == "sunrise":
            sim = SunriseSimulator(
                client,
                duration_minutes=args.minutes,
                start_kelvin=args.start_kelvin,
                end_kelvin=args.end_kelvin,
                max_brightness=args.brightness,
            )
            sim.start()
            logger.info("Sunrise simulation for %.1f minutes. Press Ctrl+C to stop.", args.minutes)
            try:
                while sim.is_alive():
                    time.sleep(1)
            except KeyboardInterrupt:
                sim.stop()
                logger.info("Sunrise cancelled.")
                return 130
        elif args.command == "info":
            info = client.get_info()
            logger.info("Name: %s", info.get("name", "?"))
            logger.info("Version: %s", info.get("ver", "?"))
            logger.info("LEDs: %d", info.get("leds", {}).get("count", 0))
            logger.info("Uptime: %ds", info.get("uptime", 0))
            logger.info("Free heap: %d", info.get("freeheap", 0))
            logger.info("IP: %s", info.get("ip", "?"))
    except KeyboardInterrupt:
        logger.info("Stopped.")
        return 130
    except (RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
