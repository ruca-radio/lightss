"""A single writer for music-driven WLED output, with a slower AI director."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import math
import threading
import time
import uuid

import lightctl


def unit(value: object) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Audio measurements must be finite")
    return max(0.0, min(1.0, number))


class ShowRenderer:
    """Preserve an AI theme; modulate it using measured audio and beat envelopes."""

    def __init__(self, snapshot: dict) -> None:
        state = snapshot.get("state", {})
        self.segments = [s["id"] for s in state.get("seg", [])
                         if "id" in s and s.get("stop", 1) > s.get("start", 0)]
        if not self.segments:
            raise ValueError("WLED returned no active segments; configure the strip first")
        effects = snapshot.get("effects", [])
        self.allowed_effects = {i for i in lightctl.SAFE_EFFECTS if i < len(effects)
                                and effects[i].casefold() == lightctl.SAFE_EFFECTS[i].casefold()}
        self.palette_count = len(snapshot.get("palettes", []))
        self.theme = {"bri": 180, "seg": [{"fx": 0, "sx": 110, "ix": 128,
                      "pal": 0, "col": [[30, 100, 210, 0], [180, 35, 100, 0], [15, 160, 130, 0]]}]}
        self.beats = 0
        self.last_beat = -100.0
        self.last_sound = -100.0
        self.last_tick: float | None = None
        self.drive = 0.0

    def set_theme(self, payload: dict) -> None:
        theme = deepcopy(self.theme)
        if "bri" in payload:
            theme["bri"] = max(40, min(210, int(payload["bri"])))
        for segment in payload.get("seg", []):
            target = theme["seg"][0]
            for key in ("sx", "ix", "c1", "c2", "c3"):
                if key in segment:
                    target[key] = lightctl.clamp_byte(segment[key])
            if segment.get("fx") in self.allowed_effects | {0}:
                target["fx"] = int(segment["fx"])
            if "pal" in segment and 0 <= int(segment["pal"]) < self.palette_count:
                target["pal"] = int(segment["pal"])
            if segment.get("col"):
                colors = segment["col"]
                for i, color in enumerate(colors[:3]):
                    if len(color) >= 3:
                        target["col"][i] = [max(0, min(210, int(v))) for v in
                                            (list(color[:4]) + [0])[:4]]
        self.theme = theme

    def render(self, features: dict, now: float, fresh: bool = True) -> dict:
        dt = 0.1 if self.last_tick is None else max(0.001, now - self.last_tick)
        self.last_tick = now
        rms = features.get("rms", 0) if fresh else 0
        target = features.get("drive", 0) if rms > 0.003 else 0
        self.drive += (target - self.drive) * (1 - math.exp(-dt / 0.22))
        if rms > 0.003:
            self.last_sound = now
        if fresh and features.get("beat") and rms > 0.003 and now - self.last_beat >= 0.18:
            self.last_beat = now
            self.beats += 1
        pulse = math.exp(-max(0, now - self.last_beat) / 0.20)
        quiet = now - self.last_sound > 3
        theme = self.theme["seg"][0]
        brightness = 30 if quiet else round(self.theme["bri"] * (0.30 + 0.42 * self.drive + 0.28 * pulse))
        segments = []
        for index, segment_id in enumerate(self.segments):
            seg = deepcopy(theme)
            seg["id"] = segment_id
            # Rotate colors only on musical phrases, retaining the AI's color family.
            rotation = (self.beats // 8 + index) % 3
            seg["col"] = seg["col"][rotation:] + seg["col"][:rotation]
            seg["sx"] = lightctl.clamp_byte(theme["sx"] * (0.65 + self.drive * 0.55))
            seg["ix"] = lightctl.clamp_byte(theme["ix"] * (0.65 + features.get("bass", 0) * 0.5 + pulse * 0.2))
            seg.update(on=True, bri=255)
            segments.append(seg)
        # WLED JSON durations are deciseconds, not milliseconds.
        return {"on": True, "bri": min(210, brightness), "tt": 4 if quiet else 1, "seg": segments}


class MusicShow:
    FRAME_INTERVAL = 0.12
    AI_INTERVAL = 24.0
    INPUT_TIMEOUT = 3.0

    def __init__(self, client, director, recognize, metadata) -> None:
        self.client = client
        self.director = director
        self.recognize = recognize
        self.metadata = metadata
        self.lock = threading.RLock()
        self.output_lock = threading.Lock()
        self.recognition_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.director_thread = None
        self.running = False
        self.session_id = ""
        self.renderer = None
        self.features = {}
        self.history = deque(maxlen=240)
        self.received_at = 0.0
        self.song = None
        self.song_revision = 0
        self.ai_state = "idle"
        self.ai_error = ""
        self.output_error = ""
        self.response = ""
        self.last_recognition = -100.0
        self.last_output = None
        self.sent_frames = 0

    def start(self) -> dict:
        self.stop()
        snapshot = self.client.get_device_snapshot()
        renderer = ShowRenderer(snapshot)
        with self.lock:
            self.renderer = renderer
            self.snapshot = snapshot
            self.session_id = uuid.uuid4().hex
            self.running = True
            self.features = {}
            self.history.clear()
            self.received_at = 0
            self.song = None
            self.song_revision = 0
            self.ai_state = "waiting_for_audio"
            self.ai_error = self.output_error = self.response = ""
            self.last_output = None
            self.sent_frames = 0
            self.last_recognition = -100.0
            stop = threading.Event()
            self.stop_event = stop
            token = self.session_id
        self.thread = threading.Thread(target=self._run, args=(token, stop), daemon=True)
        self.director_thread = threading.Thread(target=self._direct, args=(token, stop), daemon=True)
        self.thread.start()
        self.director_thread.start()
        return self.status()

    def stop(self, token: str | None = None) -> None:
        with self.lock:
            if token is not None and token != self.session_id:
                return
            self.running = False
            self.stop_event.set()
        # A stopped session cannot leave an HTTP write queued behind the new one.
        with self.output_lock:
            pass

    def ingest(self, data: dict) -> dict:
        features = {key: unit(data.get(key, 0)) for key in
                    ("rms", "drive", "bass", "mid", "treble", "beatConfidence")}
        features["beat"] = data.get("beat") is True
        bpm = float(data.get("bpm", 0))
        if not math.isfinite(bpm):
            raise ValueError("BPM must be finite")
        features["bpm"] = max(0, min(240, bpm))
        with self.lock:
            if not self.running or data.get("session_id") != self.session_id:
                raise ValueError("Music show session is no longer active")
            # Preserve an onset while a slow WLED write is in flight, coalesce all other data.
            features["beat"] |= bool(self.features.get("beat"))
            self.features = features
            self.received_at = time.monotonic()
            self.history.append(dict(features))
        return self.status()

    def sample(self, audio: bytes, token: str) -> None:
        with self.lock:
            if not self.running or token != self.session_id:
                return
            now = time.monotonic()
            if now - self.last_recognition < 25:
                return
        if not self.recognition_lock.acquire(blocking=False):
            return
        with self.lock:
            self.last_recognition = now

        def identify():
            try:
                song = self.recognize(audio)
                with self.lock:
                    if song and self.running and token == self.session_id:
                        if song != self.song:
                            self.song_revision += 1
                        self.song = song
            except Exception as exc:
                with self.lock:
                    if token == self.session_id:
                        self.ai_error = f"Song recognition: {exc}"
            finally:
                self.recognition_lock.release()
        threading.Thread(target=identify, daemon=True).start()

    def _run(self, token: str, stop: threading.Event) -> None:
        while not stop.wait(self.FRAME_INTERVAL):
            with self.output_lock:
                with self.lock:
                    if not self.running or token != self.session_id:
                        return
                    if not self.received_at:
                        continue
                    now = time.monotonic()
                    age = now - self.received_at
                    payload = self.renderer.render(dict(self.features), now, age < self.INPUT_TIMEOUT)
                    self.features["beat"] = False
                    if payload == self.last_output:
                        continue
                try:
                    self.client.post_state(payload)
                    with self.lock:
                        self.last_output = payload
                        self.sent_frames += 1
                        self.output_error = ""
                except Exception as exc:
                    with self.lock:
                        self.output_error = str(exc)
                    stop.wait(1.0)

    def _direct(self, token: str, stop: threading.Event) -> None:
        last_attempt = -100.0
        previous_song = None
        while not stop.wait(0.5):
            with self.lock:
                if token != self.session_id or not self.running:
                    return
                if len(self.history) < 8 or time.monotonic() - self.received_at > self.INPUT_TIMEOUT:
                    continue
                song = deepcopy(self.song)
                song_revision = self.song_revision
                if time.monotonic() - last_attempt < self.AI_INTERVAL and song == previous_song:
                    continue
                history = list(self.history)
                summary = {k: sum(f[k] for f in history) / len(history)
                           for k in ("rms", "drive", "bass", "mid", "treble", "bpm")}
                if summary["rms"] < 0.003:
                    continue
                self.ai_state = "designing"
                snapshot = self.snapshot
                previous = deepcopy(self.renderer.theme)
            last_attempt = time.monotonic()
            try:
                current = self.metadata()
                if current and current.get("status", "Playing").lower() == "playing":
                    song = current
                payload, response = self.director(song, summary, snapshot, previous)
                with self.lock:
                    if token != self.session_id or not self.running:
                        return
                    if song_revision != self.song_revision:
                        last_attempt = -100.0
                        continue
                    self.renderer.set_theme(payload)
                    self.song = song
                    self.response = response
                    self.ai_state = "directing"
                    self.ai_error = ""
                previous_song = song
            except Exception as exc:
                with self.lock:
                    if token != self.session_id:
                        return
                    self.ai_state = "fallback"
                    self.ai_error = str(exc)
                previous_song = song

    def status(self) -> dict:
        with self.lock:
            age = time.monotonic() - self.received_at if self.received_at else None
            return {"running": self.running, "session_id": self.session_id,
                    "audio_live": self.running and age is not None and age < self.INPUT_TIMEOUT,
                    "ai_state": self.ai_state, "ai_error": self.ai_error,
                    "output_error": self.output_error, "song": deepcopy(self.song),
                    "response": self.response, "sent_frames": self.sent_frames,
                    "theme": deepcopy(self.renderer.theme) if self.renderer else None}
