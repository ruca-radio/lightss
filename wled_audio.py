#!/usr/bin/env python3
"""UDP listener for WLED-SR audio-sync packets (AudioReactive usermod).

The left wall controller (10.27.27.110) has the PDM mic and broadcasts
44-byte "v2" audio-sync packets on UDP 11988; this module listens for
them so Lightss can react to music without a mic of its own.

Packet layout (packed, little-endian) per struct audioSyncPacket:
https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/main/audio_reactive.h
  offset  0: char[6]   header "00002\\0"
  offset  6: uint8[2]  reserved (legacy pressure metadata)
  offset  8: float     sampleRaw (or rawSampleAgc)
  offset 12: float     sampleSmth (sampleAvg or sampleAgc)
  offset 16: uint8     samplePeak (>=1 = peak detected)
  offset 17: uint8     reserved (not a frame counter)
  offset 18: uint8[16] fftResult (16 GEQ channels, 0..255)
  offset 34: uint16    reserved (legacy zero-crossing metadata)
  offset 36: float     FFT_Magnitude
  offset 40: float     FFT_MajorPeak (Hz)
"""

from __future__ import annotations

import contextlib
import logging
import math
import socket
import struct
import threading
import statistics
import urllib.parse
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger("wled_audio")

AUDIO_SYNC_PORT = 11988
AUDIO_SYNC_GROUP = "239.0.0.1"
HEADER_V2 = b"00002\x00"
PACKET_V2_SIZE = 44
PACKET_V2_STRUCT = struct.Struct("<6sBBffBB16sHff")
STALE_AFTER_S = 2.0
LEVEL_FULL_SCALE = 255.0
BEAT_WINDOW = 43  # ~1s of history at the ~43 packets/s WLED sends
BEAT_FACTOR = 1.5
BEAT_MIN_LEVEL = 0.1
BEAT_MIN_HISTORY = 8
BEAT_REFRACTORY_S = 0.16


def parse_packet(data: bytes) -> dict[str, Any] | None:
    """Parse one v2 audio-sync packet; returns None for anything else."""
    if len(data) != PACKET_V2_SIZE:
        return None
    (
        header,
        pressure_int,
        pressure_frac,
        sample_raw,
        sample_avg,
        sample_peak,
        reserved_byte_17,
        fft_raw,
        zero_crossings,
        fft_magnitude,
        fft_major_peak,
    ) = PACKET_V2_STRUCT.unpack(data)
    if header != HEADER_V2 or not all(
        math.isfinite(value)
        for value in (sample_raw, sample_avg, fft_magnitude, fft_major_peak)
    ):
        return None
    return {
        "sample_raw": sample_raw,
        "sample_avg": sample_avg,
        "sample_peak": sample_peak,
        # Kept for compatibility with older consumers. In current v2 packets
        # byte 17 is reserved and must not be used to identify new packets.
        "frame_counter": reserved_byte_17,
        "fft": list(fft_raw),
        "zero_crossings": zero_crossings,
        "fft_magnitude": fft_magnitude,
        "fft_major_peak": fft_major_peak,
        "pressure": pressure_int + pressure_frac / 256.0,
    }


def _normalize_level(sample_avg: float) -> float:
    return min(max(sample_avg / LEVEL_FULL_SCALE, 0.0), 1.0)


class WledAudioListener:
    """Background UDP listener for WLED-SR audio-sync packets."""

    def __init__(
        self,
        port: int = AUDIO_SYNC_PORT,
        host: str = "",
        sock: Any = None,
        clock: Callable[[], float] = time.monotonic,
        interface_ip: str = "0.0.0.0",
        allowed_source: str | None = None,
        on_packet: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.port = port
        self.host = host
        self._sock = sock
        self._clock = clock
        self.interface_ip = interface_ip
        self.allowed_source = allowed_source
        self._on_packet = on_packet
        self._lock = threading.Lock()
        self._last: dict[str, Any] | None = None
        self._last_at = 0.0
        self._levels: deque[float] = deque(maxlen=BEAT_WINDOW)
        self._last_beat_at: float | None = None
        self._receive_sequence = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._reset_state()
        if self._sock is None:
            # Build the socket locally: if bind or the multicast join fails, the
            # half-configured socket must not survive, or the next start() would
            # skip setup and run a listener that can never receive audio.
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host, self.port))
                membership = socket.inet_aton(AUDIO_SYNC_GROUP) + socket.inet_aton(
                    self.interface_ip
                )
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership
                )
            except OSError:
                sock.close()
                raise
            self._sock = sock
        self._sock.settimeout(0.25)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wled-audio", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._reset_state()

    def _reset_state(self) -> None:
        with self._lock:
            self._last = None
            self._last_at = 0.0
            self._levels.clear()
            self._last_beat_at = None
            self._receive_sequence = 0

    def feed_packet(self, data: bytes, addr: Any = None) -> dict[str, Any] | None:
        """Ingest one datagram; returns the parsed packet or None."""
        source_ip = addr[0] if isinstance(addr, (tuple, list)) and addr else None
        if self.allowed_source is not None and source_ip != self.allowed_source:
            return None
        parsed = parse_packet(data)
        if parsed is None:
            return None
        level = _normalize_level(parsed["sample_avg"])
        now = self._clock()
        with self._lock:
            history = self._levels
            beat = False
            if len(history) >= BEAT_MIN_HISTORY and level >= BEAT_MIN_LEVEL:
                baseline = sum(history) / len(history)
                candidate = level > baseline * BEAT_FACTOR
                spaced = (
                    self._last_beat_at is None
                    or now - self._last_beat_at >= BEAT_REFRACTORY_S
                )
                beat = candidate and spaced
                if beat:
                    self._last_beat_at = now
            history.append(level)
            self._receive_sequence += 1
            parsed["level"] = level
            parsed["beat"] = beat
            parsed["receive_sequence"] = self._receive_sequence
            parsed["source_ip"] = source_ip
            parsed["received_at_monotonic_s"] = now
            self._last = parsed
            self._last_at = now
        if self._on_packet is not None:
            try:
                self._on_packet(parsed)
            except Exception:
                logger.exception("WLED audio observer callback failed")
        return parsed

    def get_snapshot(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            last = self._last
            levels_beat = last["beat"] if last else False
            age_s = max(0.0, now - self._last_at) if last is not None else 0.0
        active = last is not None and age_s <= STALE_AFTER_S
        if not active or last is None:
            return {
                "active": False,
                "status": "stale" if last is not None else "waiting_for_packets",
                "received_at_monotonic_s": last.get("received_at_monotonic_s") if last else None,
                "sample_raw": None, "fft_magnitude": None, "fft_major_peak": None,
                "age_s": age_s,
                "level": 0.0,
                "peak": False,
                "beat": False,
                "sample_avg": 0.0,
                "sample_peak": 0,
                "fft": [0.0] * 16,
                "frame_counter": None,
                "receive_sequence": None,
                "source_ip": None,
            }
        return {
            "active": True,
            "status": "live",
            "received_at_monotonic_s": last["received_at_monotonic_s"],
            "sample_raw": last["sample_raw"],
            "fft_magnitude": last["fft_magnitude"],
            "fft_major_peak": last["fft_major_peak"],
            "age_s": age_s,
            "level": last["level"],
            "peak": last["sample_peak"] > 0,
            "beat": levels_beat,
            "sample_avg": last["sample_avg"],
            "sample_peak": last["sample_peak"],
            "fft": [value / 255.0 for value in last["fft"]],
            "receive_sequence": last["receive_sequence"],
            "source_ip": last["source_ip"],
            # Legacy wire metadata only. Byte 17 is reserved in current v2
            # packets and is intentionally not used as a receive sequence.
            "frame_counter": last["frame_counter"],
        }

    def _run(self) -> None:
        errors = 0
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                # stop() closes the socket to wake recvfrom: leave quietly then.
                if self._stop.is_set():
                    break
                # Transient network failures (Wi-Fi flap, ENETDOWN) must not
                # kill the listener permanently — a show would run blind.
                errors += 1
                if errors <= 3 or errors % 100 == 0:
                    logger.warning("WLED audio listener recv error (#%d): %s", errors, exc)
                time.sleep(0.5)
                continue
            errors = 0
            self.feed_packet(data, addr)


class ControllerAudioMonitor:
    """Observe controller microphone telemetry without owning lights or playback."""

    def __init__(self, client: Any, *, dry_run: bool = False) -> None:
        self.client = client
        self.dry_run = dry_run
        self._lock = threading.Lock()
        self._listener = None
        self._closed = False
        self._retry_at = 0.0
        self._error: str | None = None
        self._history = deque(maxlen=1200)
        self._last_sequence = None

    def _make_listener(self) -> Any:
        import wled_audio
        controllers = getattr(self.client, "controllers", [])
        host = controllers[0].host if controllers else getattr(self.client, "host", "")
        source = urllib.parse.urlparse(str(host)).hostname
        if not source:
            raise ValueError("Controller audio requires a configured controller host.")
        source = socket.gethostbyname(source)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect((source, wled_audio.AUDIO_SYNC_PORT))
            interface = route.getsockname()[0]
        return wled_audio.WledAudioListener(interface_ip=interface, allowed_source=source,
                                            on_packet=self._record_audio_packet)

    def _record_audio_packet(self, packet: dict) -> None:
        # Ingest at UDP receive rate, independently of browser frame/poll rate.
        audio = {**packet, "active": True, "fft": [v / 255 for v in packet.get("fft", [0] * 16)]}
        with self._lock:
            if not self._closed and audio.get("receive_sequence") != self._last_sequence:
                self._last_sequence = audio.get("receive_sequence")
                self._history.append((time.monotonic(), audio))

    def get_snapshot(self) -> dict:
        with self._lock:
            if self._closed or self.dry_run:
                return {"active": False, "level": 0, "fft": [0] * 16}
            if self._listener is None and time.monotonic() >= self._retry_at:
                listener = None
                try:
                    listener = self._make_listener()
                    listener.start()
                    self._listener = listener
                    self._error = None
                except Exception as exc:
                    if listener is not None:
                        with contextlib.suppress(Exception):
                            listener.stop()
                    self._error = str(exc)
                    self._retry_at = time.monotonic() + 5
                    logger.warning("Controller audio observation unavailable: %s", exc)
            if self._listener is not None:
                audio = self._listener.get_snapshot()
                sequence = audio.get("receive_sequence")
                if audio.get("active") and sequence != self._last_sequence:
                    self._last_sequence = sequence
                    self._history.append((time.monotonic(), dict(audio)))
                return audio
            return {"active": False, "level": 0, "fft": [0] * 16, "error": self._error}

    def ai_summary(self) -> dict:
        """Expose fresh measurements separately from rolling/estimated features."""
        audio = self.get_snapshot()
        now = time.monotonic()
        with self._lock:
            history = list(self._history)
        return summarize_audio(audio, history, now)

    def stop(self) -> None:
        with self._lock:
            self._closed = True
            if self._listener is not None:
                self._listener.stop()
                self._listener = None



def summarize_audio(audio: dict, history: list, now: float) -> dict:
    """Summarize receiver-timed mic features; no song identity or raw audio is inferred."""
    active = bool(audio.get("active"))
    recent = [(at, item) for at, item in history if 0 <= now-at <= 30] if active else []
    def window(seconds):
        items = [item for at, item in recent if now-at <= seconds]
        count = len(items)
        bands = [sum((item.get("fft") or [0]*16)[i] for item in items)/count if count else 0
                 for i in range(16)]
        levels = [item.get("level", 0) for item in items]
        return {"samples": count, "level_average": sum(levels)/count if count else 0,
                "level_peak": max(levels, default=0), "fft_average": bands,
                "bass": sum(bands[:4])/4, "mid": sum(bands[4:10])/6,
                "treble": sum(bands[10:])/6,
                "beat_count": sum(bool(item.get("beat")) for item in items)}
    windows = {f"{seconds}s": window(seconds) for seconds in (2, 10, 30)}
    long = windows["30s"]
    beats = [at for at, item in recent if item.get("beat") and now-at <= 10]
    intervals = [b-a for a,b in zip(beats,beats[1:]) if .25 <= b-a <= 1.5]
    bpm = None
    confidence = 0.0
    if len(intervals) >= 3 and now-beats[-1] <= 2:
        median = statistics.median(intervals)
        deviation = sum(abs(value-median) for value in intervals)/len(intervals)
        confidence = min(1.0,len(intervals)/7)*max(0.0,1-deviation/median)
        if confidence >= .25:
            bpm = round(60/median, 1)
    return {"active": active, "status": audio.get("status", "live" if active else "unavailable"),
            "source": "controller_audio_sync", "source_ip": audio.get("source_ip"),
            "age_s": audio.get("age_s"), "receive_sequence": audio.get("receive_sequence"),
            "received_at_monotonic_s": audio.get("received_at_monotonic_s"),
            "level": audio.get("level",0) if active else None,
            "level_average": long["level_average"], "level_peak": long["level_peak"],
            "fft": long["fft_average"], "fft_latest": audio.get("fft") if active else None,
            "sample_raw": audio.get("sample_raw") if active else None,
            "controller_peak": bool(audio.get("peak",audio.get("sample_peak",0))) if active else None,
            "dominant_frequency_hz": audio.get("fft_major_peak") if active else None,
            "fft_magnitude": audio.get("fft_magnitude") if active else None,
            "beat_count": long["beat_count"], "beat_source": "local_energy_onset_estimate",
            "bpm": bpm, "bpm_confidence": round(confidence,3), "bpm_source": "local_onset_intervals",
            "samples": long["samples"], "window_s": 30, "windows": windows}
