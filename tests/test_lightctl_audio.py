import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

import lightctl


class FakeClient:
    def __init__(self):
        self.payloads = []

    def post_state(self, payload):
        self.payloads.append(payload)


class FakeReactiveMode:
    def __init__(self):
        self.beats = []

    def handle_beat(self, energy):
        self.beats.append(energy)


def make_snapshot(level=0.5, beat=True, active=True, receive_sequence=None):
    snapshot = {
        "active": active,
        "age_s": 0.1,
        "level": level,
        "peak": False,
        "beat": beat,
        "sample_avg": level * 255.0,
        "sample_peak": 1,
        "fft": [0.0] * 16,
    }
    if receive_sequence is not None:
        snapshot["receive_sequence"] = receive_sequence
    return snapshot


class FakeWledListener:
    """Stands in for wled_audio.WledAudioListener; ends the loop via stop_event."""

    def __init__(
        self, snapshot, stop_event, stop_after=2, fresh_frames=True, fresh_sequences=False
    ):
        self.snapshot = snapshot
        self._stop_event = stop_event
        self._stop_after = stop_after
        self._fresh_frames = fresh_frames
        self._fresh_sequences = fresh_sequences
        self._frame = 0
        self._sequence = 0
        self._polls = 0
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def get_snapshot(self):
        self._polls += 1
        if self._polls >= self._stop_after:
            self._stop_event.set()
        if self._fresh_frames:
            self._frame += 1
        snapshot = {**self.snapshot, "frame_counter": self._frame}
        if self._fresh_sequences:
            self._sequence += 1
            snapshot["receive_sequence"] = self._sequence
        return snapshot


class WledMicSourceTests(unittest.TestCase):
    def _run(self, fake_listener, **kwargs):
        stop_event = kwargs.pop("stop_event", threading.Event())
        sd_mock = MagicMock(name="sounddevice")
        with (
            patch.object(lightctl.wled_audio, "WledAudioListener", return_value=fake_listener),
            patch.dict(sys.modules, {"sounddevice": sd_mock}),
        ):
            lightctl.run_mode1(FakeClient(), stop_event=stop_event, **kwargs)
        return sd_mock

    def test_wled_mic_uses_listener_and_never_opens_sounddevice(self):
        stop_event = threading.Event()
        listener = FakeWledListener(make_snapshot(), stop_event)
        sd_mock = self._run(listener, source="wled_mic", stop_event=stop_event)

        self.assertTrue(listener.started)
        self.assertTrue(listener.stopped)
        sd_mock.InputStream.assert_not_called()

    def test_wled_mic_forwards_snapshot_level_and_beat(self):
        stop_event = threading.Event()
        listener = FakeWledListener(make_snapshot(level=0.42, beat=True), stop_event)
        levels = []
        self._run(listener, source="wled_mic", stop_event=stop_event,
                  level_callback=lambda energy, beat: levels.append((energy, beat)))

        self.assertTrue(levels)
        self.assertTrue(all(entry == (0.42, True) for entry in levels))

    def test_wled_mic_calls_handle_beat_only_on_beat(self):
        stop_event = threading.Event()
        listener = FakeWledListener(make_snapshot(level=0.7, beat=True), stop_event)
        mode = FakeReactiveMode()
        self._run(listener, source="wled_mic", stop_event=stop_event, reactive_mode=mode)

        self.assertTrue(mode.beats)
        self.assertTrue(all(energy == 0.7 for energy in mode.beats))

        stop_event2 = threading.Event()
        quiet = FakeWledListener(make_snapshot(level=0.2, beat=False), stop_event2)
        mode2 = FakeReactiveMode()
        self._run(quiet, source="wled_mic", stop_event=stop_event2, reactive_mode=mode2)
        self.assertEqual(mode2.beats, [])

    def test_wled_mic_edge_triggers_beat_on_frame_counter(self):
        """A beat flag latched across polls must fire handle_beat once per packet.

        The snapshot's beat flag stays set until the next packet arrives; with
        a static frame counter (no new packets) the poll loop must not re-fire.
        """
        stop_event = threading.Event()
        listener = FakeWledListener(
            make_snapshot(level=0.7, beat=True), stop_event, stop_after=6, fresh_frames=False
        )
        mode = FakeReactiveMode()
        self._run(listener, source="wled_mic", stop_event=stop_event, reactive_mode=mode)

        self.assertEqual(len(mode.beats), 1)

    def test_wled_mic_edge_triggers_on_local_receive_sequence_not_reserved_byte(self):
        stop_event = threading.Event()
        listener = FakeWledListener(
            make_snapshot(level=0.7, beat=True, receive_sequence=1),
            stop_event,
            stop_after=6,
            fresh_frames=True,
        )
        mode = FakeReactiveMode()
        self._run(listener, source="wled_mic", stop_event=stop_event, reactive_mode=mode)

        self.assertEqual(len(mode.beats), 1)

    def test_wled_mic_accepts_distinct_beats_with_constant_reserved_byte(self):
        stop_event = threading.Event()
        listener = FakeWledListener(
            make_snapshot(level=0.7, beat=True),
            stop_event,
            stop_after=3,
            fresh_frames=False,
            fresh_sequences=True,
        )
        mode = FakeReactiveMode()
        self._run(listener, source="wled_mic", stop_event=stop_event, reactive_mode=mode)

        self.assertEqual(len(mode.beats), 3)

    def test_listener_stopped_when_iteration_raises(self):
        stop_event = threading.Event()
        listener = FakeWledListener(make_snapshot(), stop_event)
        mode = MagicMock()

        def explode_and_stop(_energy):
            stop_event.set()
            raise RuntimeError("light exploded")

        mode.handle_beat.side_effect = explode_and_stop
        # Per-iteration errors are logged, not raised; the loop must still
        # exit on stop_event and stop the listener via finally.
        self._run(listener, source="wled_mic", stop_event=stop_event, reactive_mode=mode)

        self.assertTrue(listener.stopped)

    def test_source_resolved_from_config_when_not_passed(self):
        stop_event = threading.Event()
        listener = FakeWledListener(make_snapshot(beat=False), stop_event)
        sd_mock = MagicMock(name="sounddevice")
        with (
            patch.object(lightctl, "load_config", return_value={"audio_source": "wled_mic"}),
            patch.object(lightctl.wled_audio, "WledAudioListener", return_value=listener),
            patch.dict(sys.modules, {"sounddevice": sd_mock}),
        ):
            lightctl.run_mode1(FakeClient(), stop_event=stop_event)

        self.assertTrue(listener.started)
        self.assertTrue(listener.stopped)
        sd_mock.InputStream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
