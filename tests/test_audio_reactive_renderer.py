from __future__ import annotations

import threading
import time

import fleet
import realtime


class FakeFleet:
    def __init__(self):
        self.installation, self.controllers = fleet.load_topology({})


class FakeTransport:
    def __init__(self, fail_forever: bool = False):
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False
        self.fail_forever = fail_forever
        self.lock = threading.Lock()

    def sendto(self, data, address):
        if self.fail_forever:
            raise OSError("simulated send failure")
        with self.lock:
            self.sent.append((bytes(data), address))
        return len(data)

    def close(self):
        self.closed = True


class SnapshotSource:
    def __init__(self, snapshot, *, increment_sequence: bool = True):
        self.snapshot = dict(snapshot)
        self.increment_sequence = increment_sequence
        self.calls = 0

    def __call__(self):
        self.calls += 1
        result = dict(self.snapshot)
        result["fft"] = list(self.snapshot.get("fft", []))
        if self.increment_sequence:
            result["receive_sequence"] = self.calls
        return result


def snapshot(*, level=0.0, fft=None, beat=False, peak=False, active=True, sequence=1):
    return {
        "active": active,
        "level": level,
        "fft": list(fft if fft is not None else [0.0] * 16),
        "beat": beat,
        "peak": peak,
        "receive_sequence": sequence,
    }


def wait_for_frames(runner, count=4, timeout=2.0):
    deadline = time.monotonic() + timeout
    while runner.status()["sent_frames"] < count and time.monotonic() < deadline:
        time.sleep(0.005)
    assert runner.status()["sent_frames"] >= count


def payloads_for_host(transport, host):
    with transport.lock:
        return [packet[10:] for packet, address in transport.sent if address[0] == host]


def run_scene(source_snapshot, frames=8, **kwargs):
    transport = FakeTransport()
    runner = realtime.AudioReactiveRunner(
        FakeFleet(), SnapshotSource(source_snapshot), fps=40, transport=transport, **kwargs
    )
    runner.start()
    wait_for_frames(runner, frames)
    runner.stop()
    runner.join(timeout=2)
    assert not runner.is_alive()
    assert transport.closed is True
    return runner, transport


def pixel_energy(frame):
    return [sum(frame[index:index + 3]) for index in range(0, len(frame), 3)]


def adjacent_detail(frame):
    values = pixel_energy(frame)
    return sum(abs(right - left) for left, right in zip(values, values[1:]))


def test_constructor_sanitizes_config_and_exposes_complete_status():
    runner = realtime.AudioReactiveRunner(
        FakeFleet(),
        SnapshotSource(snapshot()),
        fps=999,
        brightness=99,
        colors=["not-a-color", "#ff0000", [0, 999, 12]],
        transport=FakeTransport(),
    )
    runner.update_look(composition_mode="invalid")
    assert runner.fps == 40
    assert runner.brightness == 1.0
    assert runner.composition_mode == "unison"
    assert len(runner.colors) >= 2
    assert all(0 <= channel <= 210 for color in runner.colors for channel in color)
    assert runner.status().keys() >= {
        "running", "sent_frames", "audio_active", "beat_count", "bpm",
        "energy", "bass", "mid", "treble", "last_error",
    }


def test_bass_and_treble_create_distinct_audio_driven_frames_with_normalized_geometry():
    silence = snapshot(level=0.0, fft=[0.0] * 16)
    bass = snapshot(level=0.8, fft=[1.0, 0.9, 0.8, 0.7] + [0.05] * 12)
    treble = snapshot(level=0.8, fft=[0.05] * 10 + [0.8, 0.9, 1.0, 0.9, 0.8, 0.7])

    _, silent_tx = run_scene(silence)
    _, bass_tx = run_scene(bass)
    _, treble_tx = run_scene(treble)

    silent_left = payloads_for_host(silent_tx, "10.27.27.110")[-1]
    bass_left = payloads_for_host(bass_tx, "10.27.27.110")[-1]
    treble_left = payloads_for_host(treble_tx, "10.27.27.110")[-1]
    assert silent_left != bass_left != treble_left
    assert sum(bass_left) > sum(silent_left)
    assert adjacent_detail(treble_left) > adjacent_detail(silent_left)

    # Existing calibrated controller spans are preserved despite unequal strips.
    assert len(silent_left) == 82 * 3
    assert len(payloads_for_host(silent_tx, "10.27.27.112")[-1]) == 87 * 3


def test_rendering_is_deterministic_for_identical_audio_history_and_bounded():
    audio = snapshot(
        level=0.72,
        fft=[0.8, 0.6, 0.4, 0.2, 0.25, 0.5, 0.7, 0.6, 0.4, 0.2, 0.7, 0.9, 0.6, 0.3, 0.2, 0.1],
    )
    _, first = run_scene(audio, frames=6, colors=["#ff4400", "#0066ff", "#30d080"])
    _, second = run_scene(audio, frames=6, colors=["#ff4400", "#0066ff", "#30d080"])
    first_frames = payloads_for_host(first, "10.27.27.110")
    second_frames = payloads_for_host(second, "10.27.27.110")
    assert first_frames == second_frames
    assert all(byte <= 210 for frame in first_frames for byte in frame)


def test_beat_is_edge_triggered_once_per_receive_sequence_with_frame_counter_fallback():
    repeated = snapshot(level=0.8, fft=[0.8] * 16, beat=True, sequence=77)
    transport = FakeTransport()
    runner = realtime.AudioReactiveRunner(
        FakeFleet(), SnapshotSource(repeated, increment_sequence=False), fps=40, transport=transport
    )
    runner.start(); wait_for_frames(runner, 6); runner.stop(); runner.join(timeout=2)
    assert runner.status()["beat_count"] == 1

    old_fixture = repeated.copy()
    old_fixture.pop("receive_sequence")
    old_fixture["frame_counter"] = 8
    transport = FakeTransport()
    runner = realtime.AudioReactiveRunner(
        FakeFleet(), SnapshotSource(old_fixture, increment_sequence=False), fps=40, transport=transport
    )
    runner.start(); wait_for_frames(runner, 6); runner.stop(); runner.join(timeout=2)
    assert runner.status()["beat_count"] == 1


def test_silence_settles_to_a_dim_steady_scene_without_timer_animation():
    runner, transport = run_scene(snapshot(level=0.0, fft=[0.0] * 16), frames=12)
    frames = payloads_for_host(transport, "10.27.27.110")
    assert frames[-1] == frames[-2] == frames[-3]
    assert 0 < max(frames[-1]) <= 40
    assert runner.status()["energy"] == 0.0


def test_inactive_feed_decays_smoothly_to_the_same_silence_scene():
    class ActiveThenStale:
        def __init__(self): self.calls = 0
        def __call__(self):
            self.calls += 1
            if self.calls <= 3:
                return snapshot(level=1.0, fft=[1.0] * 16, sequence=self.calls)
            return snapshot(active=False, sequence=self.calls)

    transport = FakeTransport()
    runner = realtime.AudioReactiveRunner(FakeFleet(), ActiveThenStale(), fps=40, transport=transport)
    runner.start()
    wait_for_frames(runner, 30)
    runner.stop(); runner.join(timeout=2)
    frames = payloads_for_host(transport, "10.27.27.110")
    assert frames[-1] == frames[-2]
    assert runner.status()["audio_active"] is False
    assert runner.status()["energy"] < 0.02


def test_update_look_blends_in_place_without_reset_or_brightness_jumps():
    source = SnapshotSource(snapshot(level=0.65, fft=[0.6] * 16))
    transport = FakeTransport()
    runner = realtime.AudioReactiveRunner(
        FakeFleet(), source, fps=40, colors=["#ff2000", "#804000"], transport=transport
    )
    runner.start()
    wait_for_frames(runner, 5)
    identity = id(runner)
    sent_before = runner.status()["sent_frames"]
    runner.update_look(colors=["#0020ff", "#00d0a0"], brightness=0.3, composition_mode="alternating")
    wait_for_frames(runner, sent_before + 8)
    runner.stop(); runner.join(timeout=2)

    frames = payloads_for_host(transport, "10.27.27.110")
    assert id(runner) == identity
    assert runner.composition_mode == "alternating"
    assert runner.brightness == 0.3
    assert max(abs(a - b) for a, b in zip(frames[4], frames[5])) <= 18


def test_source_errors_are_reported_and_recovery_keeps_renderer_alive():
    class FlakySource:
        def __init__(self): self.calls = 0
        def __call__(self):
            self.calls += 1
            if self.calls <= 2:
                raise RuntimeError("audio read failed")
            return snapshot(level=0.4, fft=[0.4] * 16, sequence=self.calls)

    transport = FakeTransport()
    runner = realtime.AudioReactiveRunner(FakeFleet(), FlakySource(), fps=40, transport=transport)
    runner.start()
    wait_for_frames(runner, 4)
    assert "audio read failed" in (runner.status()["last_error"] or "")
    assert runner.is_alive()
    runner.stop(); runner.join(timeout=2)
    assert transport.closed is True


def test_sustained_send_errors_end_finitely_and_close_transport(monkeypatch):
    monkeypatch.setattr(realtime.AudioReactiveRunner, "MAX_CONSECUTIVE_ERRORS", 3)
    transport = FakeTransport(fail_forever=True)
    runner = realtime.AudioReactiveRunner(
        FakeFleet(), SnapshotSource(snapshot(level=0.5, fft=[0.5] * 16)), fps=40, transport=transport
    )
    runner.start()
    runner.join(timeout=2)
    assert not runner.is_alive()
    assert transport.closed is True
    assert "simulated send failure" in (runner.status()["last_error"] or "")


def test_stop_is_responsive_and_closes_transport():
    transport = FakeTransport()
    runner = realtime.AudioReactiveRunner(
        FakeFleet(), SnapshotSource(snapshot()), fps=1, transport=transport
    )
    runner.start()
    wait_for_frames(runner, 1)
    started = time.monotonic()
    runner.stop(); runner.join(timeout=0.3)
    assert time.monotonic() - started < 0.3
    assert not runner.is_alive()
    assert transport.closed is True


def test_preview_contains_only_actual_successfully_sent_ddp_pixels():
    runner, transport = run_scene(snapshot(level=.7, fft=[.6] * 16), frames=4)
    preview = runner.status()['preview']
    assert [item['channel'] for item in preview] == [entry.channel for entry in runner.entries]
    for item, entry in zip(preview, runner.entries):
        frame = payloads_for_host(transport, runner.hosts[entry.controller])[-1]
        assert item['controller'] == entry.controller
        assert 1 <= len(item['colors']) <= 32
        count = len(item['colors'])
        expected = [list(frame[(entry.ddp_offset + round(i * (entry.pixels - 1) / max(1, count - 1))) * 3:][:3]) for i in range(count)]
        assert item['colors'] == expected
    runner.status()['preview'][0]['colors'][0][0] = 999
    assert runner.status()['preview'][0]['colors'][0][0] <= 210


def test_unsent_renderer_has_no_fabricated_preview():
    runner = realtime.AudioReactiveRunner(FakeFleet(), lambda: snapshot(), transport=FakeTransport())
    assert runner.status()['preview'] == []
