import json
import os
import tempfile

import pytest

import lightctl
import mood_orchestrator


class TestTransitionSmoother:
    def test_adds_minimum_transition_for_mood_change(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 200}, {"bri": 100}, "recognized")
        assert result == [{"bri": 200, "tt": 1200}]

    def test_ambient_fallback_uses_longer_transition(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 60}, {"bri": 200}, "ambient")
        assert result == [{"bri": 60, "tt": 4000}]

    def test_splits_large_brightness_jump(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 250}, {"bri": 10}, "recognized")
        assert len(result) == 2
        assert result[0]["bri"] == 90  # 10 + 80
        assert result[0]["tt"] == 800
        assert result[1]["bri"] == 250
        assert result[1]["tt"] == 1200

    def test_preserves_existing_longer_transition(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 200, "tt": 3000}, {"bri": 100}, "recognized")
        assert result == [{"bri": 200, "tt": 3000}]


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

        import time

        buf = mood_orchestrator.AudioSampleBuffer(recognize_fn=fake_recognize, cooldown_seconds=10)
        assert buf.maybe_recognize(b"audio1") == {"title": "Song"}
        assert buf.maybe_recognize(b"audio2") is None
        assert len(calls) == 1


class TestMoodSession:
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
