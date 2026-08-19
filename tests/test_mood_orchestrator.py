import io
import os
import struct
import tempfile
import threading
import time
import wave

import lightctl
import mood_orchestrator


def _make_wav(samples: list[int], amplitude: int = 1) -> bytes:
    """Build a mono 16-bit WAV payload from integer sample magnitudes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        frames = b"".join(struct.pack("<h", int(s * amplitude)) for s in samples)
        wf.writeframes(frames)
    return buf.getvalue()


def _make_wav_24bit(n: int = 100) -> bytes:
    """Build a mono 24-bit WAV payload (unsupported by _rms)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(3)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00\x40" * n)
    return buf.getvalue()


class TestTransitionSmoother:
    def test_adds_minimum_transition_for_mood_change(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 150}, {"bri": 100}, "recognized")
        assert result == [{"bri": 150, "tt": 1200}]

    def test_ambient_fallback_uses_longer_transition(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 60}, {"bri": 120}, "ambient")
        assert result == [{"bri": 60, "tt": 4000}]

    def test_splits_large_brightness_jump(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 250}, {"bri": 10}, "recognized")
        assert len(result) == 2
        assert result[0]["bri"] == 90  # 10 + 80
        assert result[0]["tt"] == 800
        assert result[1]["bri"] == 250
        assert result[1]["tt"] == 1200

    def test_exact_threshold_does_not_split(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 90}, {"bri": 10}, "recognized")
        assert len(result) == 1
        assert result[0]["bri"] == 90

    def test_just_above_threshold_splits(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 91}, {"bri": 10}, "recognized")
        assert len(result) == 2
        assert result[0]["bri"] == 90
        assert result[1]["bri"] == 91

    def test_preserves_existing_longer_transition(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 150, "tt": 3000}, {"bri": 100}, "recognized")
        assert result == [{"bri": 150, "tt": 3000}]


class TestSongCache:
    def test_get_missing_returns_none(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(mood_orchestrator, "_CONFIG_DIR", tmp)
            cache = mood_orchestrator.SongCache()
            assert cache.get("missing") is None

    def test_set_and_get_roundtrip(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(mood_orchestrator, "_CONFIG_DIR", tmp)
            cache = mood_orchestrator.SongCache()
            cache.set("artist||title||album", {"bri": 180, "seg": [{"fx": 9}]})
            assert cache.get("artist||title||album") == {"bri": 180, "seg": [{"fx": 9}]}

    def test_corrupt_cache_logs_warning(self, monkeypatch, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(mood_orchestrator, "_CONFIG_DIR", tmp)
            path = os.path.join(tmp, "song_moods.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not json")
            with caplog.at_level("WARNING", logger="mood_orchestrator"):
                cache = mood_orchestrator.SongCache(path=path)
            assert cache.get("anything") is None
            assert "corrupt" in caplog.text.lower()

    def test_save_with_bare_filename(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cache = mood_orchestrator.SongCache(path="song_moods.json")
        cache.set("artist||title||", {"bri": 120})
        assert cache.get("artist||title||") == {"bri": 120}
        assert os.path.isfile(tmp_path / "song_moods.json")


class TestAudioSampleBuffer:
    def test_runs_recognizer_on_first_sample(self):
        calls = []

        def fake_recognize(data: bytes) -> dict:
            calls.append(data)
            return {"title": "Song"}

        buf = mood_orchestrator.AudioSampleBuffer(recognize_fn=fake_recognize, cooldown_seconds=0)
        result = buf.maybe_recognize(b"audio")
        assert result == {"title": "Song"}
        assert calls == [b"audio"]

    def test_respects_cooldown(self):
        calls = []

        def fake_recognize(data: bytes) -> dict:
            calls.append(data)
            return {"title": "Song"}

        buf = mood_orchestrator.AudioSampleBuffer(recognize_fn=fake_recognize, cooldown_seconds=10)
        assert buf.maybe_recognize(b"audio1") == {"title": "Song"}
        assert buf.maybe_recognize(b"audio2") is None
        assert len(calls) == 1

    def test_thread_safety_runs_recognizer_once(self):
        calls = []
        barrier = threading.Barrier(10)

        def fake_recognize(data: bytes) -> dict:
            calls.append(data)
            return {"title": "Song"}

        buf = mood_orchestrator.AudioSampleBuffer(recognize_fn=fake_recognize, cooldown_seconds=10)
        threads = [
            threading.Thread(target=lambda: (barrier.wait(), buf.maybe_recognize(b"audio")))
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1

    def test_recognizer_exception_logs_and_returns_none(self, caplog):
        def fake_recognize(_data: bytes) -> dict:
            raise RuntimeError("shazam down")

        buf = mood_orchestrator.AudioSampleBuffer(recognize_fn=fake_recognize, cooldown_seconds=0)
        with caplog.at_level("ERROR", logger="mood_orchestrator"):
            result = buf.maybe_recognize(b"audio")
        assert result is None
        assert "Recognizer failed" in caplog.text

    def test_rms_gating_skips_silent_sample(self):
        calls = []

        def fake_recognize(data: bytes) -> dict:
            calls.append(data)
            return {"title": "Song"}

        silent_wav = _make_wav([0] * 1600)
        buf = mood_orchestrator.AudioSampleBuffer(
            recognize_fn=fake_recognize, cooldown_seconds=0, rms_threshold=0.01
        )
        assert buf.maybe_recognize(silent_wav) is None
        assert len(calls) == 0

    def test_rms_gating_allows_loud_sample(self):
        calls = []

        def fake_recognize(data: bytes) -> dict:
            calls.append(data)
            return {"title": "Song"}

        loud_wav = _make_wav([1, -1] * 800, amplitude=10000)
        buf = mood_orchestrator.AudioSampleBuffer(
            recognize_fn=fake_recognize, cooldown_seconds=0, rms_threshold=0.01
        )
        assert buf.maybe_recognize(loud_wav) == {"title": "Song"}
        assert len(calls) == 1

    def test_silent_sample_does_not_advance_cooldown(self):
        calls = []

        def fake_recognize(data: bytes) -> dict:
            calls.append(data)
            return {"title": "Song"}

        silent_wav = _make_wav([0] * 1600)
        loud_wav = _make_wav([1, -1] * 800, amplitude=10000)
        buf = mood_orchestrator.AudioSampleBuffer(
            recognize_fn=fake_recognize, cooldown_seconds=10, rms_threshold=0.01
        )
        assert buf.maybe_recognize(silent_wav) is None
        assert buf.maybe_recognize(loud_wav) == {"title": "Song"}
        assert len(calls) == 1

    def test_unsupported_sample_width_warns_once(self, caplog):
        buf = mood_orchestrator.AudioSampleBuffer(recognize_fn=lambda d: None, cooldown_seconds=0)
        with caplog.at_level("WARNING", logger="mood_orchestrator"):
            assert buf._rms(_make_wav_24bit()) == 0.0
            assert buf._rms(_make_wav_24bit()) == 0.0
        warnings = [r for r in caplog.records if "sample width" in r.getMessage().lower()]
        assert len(warnings) == 1


class TestMoodSession:
    def test_sample_before_start_returns_promptly(self):
        """sample() on a never-started session must not deadlock the lock."""
        client = lightctl.LightClient("http://example.com", dry_run=True)
        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=lambda data: None,
            generate_fn=lambda song: {"bri": 100},
        )
        result: dict = {}
        done = threading.Event()

        def call_sample():
            result["status"] = session.sample(b"audio")
            done.set()

        thread = threading.Thread(target=call_sample)
        thread.start()
        thread.join(timeout=2.0)
        assert done.is_set(), "sample() deadlocked on a session that was never started"
        assert result["status"]["running"] is False
        assert result["status"]["state"] == "idle"

    def test_start_stop_lifecycle(self):
        client = lightctl.LightClient("http://example.com", dry_run=True)
        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=lambda data: None,
            generate_fn=lambda song: {"bri": 100},
        )
        session.start()
        assert session.status()["state"] == "listening"
        session.stop()
        assert session.status()["state"] == "idle"

    def test_recognized_song_applies_mood(self):
        client = lightctl.LightClient("http://example.com", dry_run=True)
        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=lambda data: {"title": "Song", "artist": "Artist", "album": "Album"},
            generate_fn=lambda song: {"bri": 222},
            recognize_cooldown=0,
            ambient_timeout=10,
        )
        session.start()
        session.sample(b"audio")
        status = session.status()
        assert status["state"] == "recognized"
        assert status["song"]["title"] == "Song"
        session.stop()

    def test_status_reports_last_payload_cache_and_errors(self):
        class FailingClient:
            def get_state(self):
                return {"bri": 10}

            def post_state(self, payload):
                raise RuntimeError("wled offline")

        session = mood_orchestrator.MoodSession(
            client=FailingClient(),
            recognize_fn=lambda data: {"title": "Song", "artist": "Artist", "album": "Album"},
            generate_fn=lambda song: {"bri": 222},
            cache=mood_orchestrator.SongCache(path=os.path.join(tempfile.mkdtemp(), "song_moods.json")),
            recognize_cooldown=0,
            ambient_timeout=10,
        )
        session.start()
        session.sample(b"audio")
        status = session.status()

        assert status["last_payload"] == {"bri": 222, "tt": 1200}
        assert status["last_error"] == "wled offline"
        assert status["last_cache_hit"] is False
        assert status["next_recognition_in"] == 0
        session.stop()

    def test_generate_failure_falls_back_to_ambient(self):
        client = lightctl.LightClient("http://example.com", dry_run=True)

        def bad_generate(song):
            raise RuntimeError("llm down")

        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=lambda data: {"title": "Song", "artist": "Artist", "album": "Album"},
            generate_fn=bad_generate,
            cache=mood_orchestrator.SongCache(path=os.path.join(tempfile.mkdtemp(), "song_moods.json")),
            recognize_cooldown=0,
            ambient_timeout=10,
        )
        session.start()
        status = session.sample(b"audio")
        assert status["state"] == "recognized"
        assert status["last_cache_hit"] is False
        session.stop()

    def test_cache_write_failure_does_not_escape(self):
        client = lightctl.LightClient("http://example.com", dry_run=True)

        class BrokenCache(mood_orchestrator.SongCache):
            def _save(self):
                raise OSError("disk full")

        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=lambda data: {"title": "Song", "artist": "Artist", "album": "Album"},
            generate_fn=lambda song: {"bri": 222},
            cache=BrokenCache(path=os.path.join(tempfile.mkdtemp(), "song_moods.json")),
            recognize_cooldown=0,
            ambient_timeout=10,
        )
        session.start()
        status = session.sample(b"audio")
        assert status["state"] == "recognized"
        session.stop()

    def test_failed_intermediate_post_keeps_error(self):
        """A failed first post of a split batch must not be cleared by the second."""

        class FlakyClient:
            def __init__(self):
                self.calls = 0

            def get_state(self):
                return {"bri": 10}

            def post_state(self, payload):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("first post failed")

        session = mood_orchestrator.MoodSession(
            client=FlakyClient(),
            recognize_fn=lambda data: {"title": "Song", "artist": "Artist", "album": "Album"},
            generate_fn=lambda song: {"bri": 222},  # 10 -> 222 splits into two posts
            cache=mood_orchestrator.SongCache(path=os.path.join(tempfile.mkdtemp(), "song_moods.json")),
            recognize_cooldown=0,
            ambient_timeout=10,
        )
        session.start()
        status = session.sample(b"audio")
        assert status["last_error"] == "first post failed"
        session.stop()


class TestMoodSessionAmbient:
    def test_enters_ambient_after_silence(self):
        client = lightctl.LightClient("http://example.com", dry_run=True)
        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=lambda data: None,
            generate_fn=lambda song: {"bri": 222},
            recognize_cooldown=0,
            ambient_timeout=0.05,
        )
        session.start()
        session.sample(b"audio")  # no match
        time.sleep(0.1)
        session.sample(b"audio")  # triggers timeout check
        assert session.status()["state"] == "ambient"
        session.stop()

    def test_ambient_to_mood_source_decision_is_atomic(self):
        """After ambient, a new song should be applied with the ambient_to_mood source."""
        client = lightctl.LightClient("http://example.com", dry_run=True)
        sources = []

        class SpySmoother(mood_orchestrator.TransitionSmoother):
            def smooth(self, payload, current_state, source):
                sources.append(source)
                return super().smooth(payload, current_state, source)

        calls = []

        def recognize_fn(data: bytes):
            calls.append(data)
            # First call: recognize; second call: no match so ambient can trigger;
            # third call: recognize a different song from ambient.
            if len(calls) == 1:
                return {"title": "Song", "artist": "Artist", "album": "Album"}
            if len(calls) == 3:
                return {"title": "Other", "artist": "Artist", "album": "Album"}
            return None

        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=recognize_fn,
            generate_fn=lambda song: {"bri": 222},
            smoother=SpySmoother(),
            recognize_cooldown=0,
            ambient_timeout=0.05,
        )
        session.start()
        session.sample(b"audio")  # recognizes song, state -> recognized
        time.sleep(0.1)
        session.sample(b"audio")  # no match, state -> ambient
        session.sample(b"audio")  # recognizes new sample from ambient
        assert "ambient_to_mood" in sources
        session.stop()
