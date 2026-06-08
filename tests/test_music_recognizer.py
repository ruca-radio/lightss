#!/usr/bin/env python3
"""Tests for music_recognizer module."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

import music_recognizer


class TestParseShazamResult:
    def test_empty_result_returns_none(self):
        assert music_recognizer._parse_shazam_result({}) is None

    def test_no_track_returns_none(self):
        assert music_recognizer._parse_shazam_result({"matches": []}) is None

    def test_basic_track_parsing(self):
        result = {
            "track": {
                "title": "Test Song",
                "subtitle": "Test Artist",
                "url": "https://shazam.com/track/123",
            }
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["title"] == "Test Song"
        assert parsed["artist"] == "Test Artist"
        assert parsed["shazam_url"] == "https://shazam.com/track/123"
        assert parsed["source"] == "shazam"

    def test_track_with_heading(self):
        result = {
            "track": {
                "heading": {"title": "Heading Song", "subtitle": "Heading Artist"},
            }
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["title"] == "Heading Song"
        assert parsed["artist"] == "Heading Artist"

    def test_track_with_sections_metadata(self):
        result = {
            "track": {
                "title": "Song",
                "subtitle": "Artist",
                "sections": [
                    {
                        "type": "SONG",
                        "metadata": [
                            {"title": "Album", "text": "Test Album"},
                            {"title": "Genre", "text": "Rock"},
                        ],
                    }
                ],
            }
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["album"] == "Test Album"
        assert parsed["genre"] == "Rock"

    def test_track_with_images(self):
        result = {
            "track": {
                "title": "Song",
                "subtitle": "Artist",
                "images": {"coverarthq": "https://example.com/hq.jpg", "coverart": "https://example.com/lq.jpg"},
            }
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["cover_url"] == "https://example.com/hq.jpg"

    def test_no_title_or_artist_returns_none(self):
        result = {"track": {"url": "https://shazam.com/track/123"}}
        assert music_recognizer._parse_shazam_result(result) is None

    def test_strips_whitespace(self):
        result = {"track": {"title": "  Song  ", "subtitle": "  Artist  "}}
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["title"] == "Song"
        assert parsed["artist"] == "Artist"


pydub_available = music_recognizer.AudioSegment is not None
requires_pydub = pytest.mark.skipif(not pydub_available, reason="pydub not installed")


@requires_pydub
class TestMakeAudioSegment:
    def test_creates_segment(self):
        audio_data = np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16)
        segment = music_recognizer._make_audio_segment(audio_data, 16000)
        assert segment.sample_width == 2
        assert segment.frame_rate == 16000
        assert segment.channels == 1

    def test_empty_audio(self):
        audio_data = np.array([], dtype=np.int16)
        segment = music_recognizer._make_audio_segment(audio_data, 16000)
        assert segment.frame_rate == 16000
        assert segment.channels == 1


class TestIsAvailable:
    def test_returns_bool(self):
        # Should return True in our test environment since deps are installed
        assert isinstance(music_recognizer.is_available(), bool)

    def test_available_reason(self):
        reason = music_recognizer.available_reason()
        assert isinstance(reason, str)
        assert "Music recognition" in reason


class TestRecognizeSync:
    def test_running_event_loop_uses_separate_loop(self, monkeypatch):
        async def fake_recognize_microphone(duration, sample_rate):
            return {"title": "Song", "artist": "Artist"}

        def fail_run_coroutine_threadsafe(coro, loop):
            coro.close()
            raise AssertionError("must not block on the current running event loop")

        monkeypatch.setattr(music_recognizer, "recognize_microphone", fake_recognize_microphone)
        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fail_run_coroutine_threadsafe)

        async def scenario():
            return music_recognizer.recognize_sync()

        assert asyncio.run(scenario()) == {"title": "Song", "artist": "Artist"}
