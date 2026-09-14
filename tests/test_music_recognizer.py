#!/usr/bin/env python3
"""Tests for music_recognizer module."""

from __future__ import annotations

import asyncio
import io
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import numpy as np
import pytest

import music_recognizer


def test_import_does_not_load_sounddevice_at_module_import_time():
    script = """
import builtins
import sys

real_import = builtins.__import__
seen_sounddevice = False

def guarded_import(name, *args, **kwargs):
    global seen_sounddevice
    if name == "sounddevice" or name.startswith("sounddevice."):
        seen_sounddevice = True
        raise ImportError("sounddevice must be lazy")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import music_recognizer
sys.exit(1 if seen_sounddevice else 0)
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=".", check=False)

    assert result.returncode == 0


class TestParseShazamResult:
    def test_empty_result_returns_none(self):
        assert music_recognizer._parse_shazam_result({}) is None

    def test_no_track_returns_none(self):
        assert music_recognizer._parse_shazam_result({"matches": []}) is None

    def test_basic_track_parsing(self):
        result = {
            "matches": [{"id": "match-1"}],
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
            "matches": [{"id": "match-1"}],
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
            "matches": [{"id": "match-1"}],
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
            "matches": [{"id": "match-1"}],
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
        result = {"matches": [{"id": "match-1"}], "track": {"url": "https://shazam.com/track/123"}}
        assert music_recognizer._parse_shazam_result(result) is None

    def test_strips_whitespace(self):
        result = {"matches": [{"id": "match-1"}], "track": {"title": "  Song  ", "subtitle": "  Artist  "}}
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["title"] == "Song"
        assert parsed["artist"] == "Artist"

    def test_track_with_empty_matches_returns_none(self):
        """Reject fuzzy/no-match responses that include a track but no actual matches."""
        result = {
            "matches": [],
            "track": {
                "title": "FEIN",
                "subtitle": "Travis Scott",
                "url": "https://shazam.com/track/123",
            }
        }
        assert music_recognizer._parse_shazam_result(result) is None

    def test_hub_with_empty_actions(self):
        """hub.actions == [] must not raise IndexError and drop a valid match."""
        result = {
            "matches": [{"id": "match-1"}],
            "track": {
                "title": "Song",
                "subtitle": "Artist",
                "hub": {"actions": []},
            }
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["title"] == "Song"
        assert "spotify_url" not in parsed

    def test_none_valued_nested_keys(self):
        """Keys present with value None must not raise TypeError."""
        result = {
            "matches": [{"id": "match-1"}],
            "track": {
                "title": "Song",
                "subtitle": "Artist",
                "heading": None,
                "share": None,
                "images": None,
                "sections": None,
                "hub": None,
            }
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["title"] == "Song"
        assert parsed["artist"] == "Artist"

    def test_hub_actions_none_and_non_dict_entry(self):
        result = {
            "matches": [{"id": "match-1"}],
            "track": {
                "title": "Song",
                "subtitle": "Artist",
                "hub": {"actions": None},
            }
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert "spotify_url" not in parsed

        result["track"]["hub"] = {"actions": [None]}
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert "spotify_url" not in parsed


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
        async def fake_recognize_microphone(duration, sample_rate, device=None):
            return {"title": "Song", "artist": "Artist"}

        def fail_run_coroutine_threadsafe(coro, loop):
            coro.close()
            raise AssertionError("must not block on the current running event loop")

        monkeypatch.setattr(music_recognizer, "recognize_microphone", fake_recognize_microphone)
        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fail_run_coroutine_threadsafe)

        async def scenario():
            return music_recognizer.recognize_sync()

        assert asyncio.run(scenario()) == {"title": "Song", "artist": "Artist"}

    def test_join_timeout_returns_none(self, monkeypatch):
        """A hung worker thread must not freeze the host event loop forever."""
        async def hanging_recognize(duration, sample_rate, device=None):
            await asyncio.Event().wait()

        monkeypatch.setattr(music_recognizer, "recognize_microphone", hanging_recognize)
        monkeypatch.setattr(music_recognizer, "SHAZAM_TIMEOUT", 0.1)
        monkeypatch.setattr(music_recognizer, "_SYNC_JOIN_MARGIN", 0.1)

        async def scenario():
            start = time.monotonic()
            result = music_recognizer.recognize_sync(duration=0.1)
            assert time.monotonic() - start < 5.0
            return result

        assert asyncio.run(scenario()) is None

    def test_audio_bytes_sync_join_timeout_returns_none(self, monkeypatch):
        async def hanging_recognize(audio_bytes):
            await asyncio.Event().wait()

        monkeypatch.setattr(music_recognizer, "recognize_audio_bytes", hanging_recognize)
        monkeypatch.setattr(music_recognizer, "SHAZAM_TIMEOUT", 0.1)
        monkeypatch.setattr(music_recognizer, "_SYNC_JOIN_MARGIN", 0.1)

        async def scenario():
            start = time.monotonic()
            result = music_recognizer.recognize_audio_bytes_sync(b"audio")
            assert time.monotonic() - start < 5.0
            return result

        assert asyncio.run(scenario()) is None


def _make_wav_bytes(duration_ms: int, sample_rate: int = 16000) -> bytes:
    samples = np.zeros(int(sample_rate * duration_ms / 1000), dtype=np.int16)
    segment = music_recognizer._make_audio_segment(samples, sample_rate)
    buf = io.BytesIO()
    segment.export(buf, format="wav")
    return buf.getvalue()


@requires_pydub
class TestShortAudioGuard:
    """ShazamIO 0.2.0.0 busy-loops the event loop forever on too-short audio;
    recognize_* must return None before ever calling recognize_song."""

    def test_empty_wav_returns_none_promptly(self, monkeypatch):
        def shazam_must_not_be_constructed():
            raise AssertionError("Shazam must not be called for too-short audio")

        monkeypatch.setattr(music_recognizer, "Shazam", shazam_must_not_be_constructed)
        result = asyncio.run(asyncio.wait_for(
            music_recognizer.recognize_audio_bytes(_make_wav_bytes(0)), timeout=5.0,
        ))
        assert result is None

    def test_short_wav_returns_none_promptly(self, monkeypatch):
        def shazam_must_not_be_constructed():
            raise AssertionError("Shazam must not be called for too-short audio")

        monkeypatch.setattr(music_recognizer, "Shazam", shazam_must_not_be_constructed)
        result = asyncio.run(asyncio.wait_for(
            music_recognizer.recognize_audio_bytes(_make_wav_bytes(500)), timeout=5.0,
        ))
        assert result is None

    def test_recognize_microphone_short_recording_returns_none(self, monkeypatch):
        def shazam_must_not_be_constructed():
            raise AssertionError("Shazam must not be called for too-short audio")

        monkeypatch.setattr(music_recognizer, "Shazam", shazam_must_not_be_constructed)
        monkeypatch.setattr(music_recognizer, "_load_sounddevice", lambda: object())
        monkeypatch.setattr(music_recognizer, "resolve_audio_source", lambda: "default")
        monkeypatch.setattr(music_recognizer, "_get_device_samplerate", lambda device=None: 16000)
        monkeypatch.setattr(
            music_recognizer, "_record_audio",
            lambda duration, sample_rate, device=None: np.zeros(160, dtype=np.int16),
        )
        result = asyncio.run(asyncio.wait_for(
            music_recognizer.recognize_microphone(duration=0.01, sample_rate=16000), timeout=5.0,
        ))
        assert result is None

    def test_recognition_http_timeout_returns_none(self, monkeypatch):
        """A stalled Shazam HTTP request must be bounded by SHAZAM_TIMEOUT."""
        class SlowShazam:
            async def recognize_song(self, data):
                await asyncio.Event().wait()

        monkeypatch.setattr(music_recognizer, "Shazam", SlowShazam)
        monkeypatch.setattr(music_recognizer, "SHAZAM_TIMEOUT", 0.1)
        result = asyncio.run(asyncio.wait_for(
            music_recognizer.recognize_audio_bytes(_make_wav_bytes(2000)), timeout=5.0,
        ))
        assert result is None



class GdbusMprisTests(unittest.TestCase):
    """now_playing_mpris gdbus fallback (no playerctl installed)."""

    LIST_NAMES = "(['org.freedesktop.DBus', 'org.mpris.MediaPlayer2.sidra', 'org.mpris.MediaPlayer2.chromium'],)"
    STATUS_PLAYING = "(<'Playing'>,)"
    STATUS_PAUSED = "(<'Paused'>,)"
    METADATA = ("(<'xesam:album': <'Almost Healed'>, 'mpris:trackid': <'/track/1'>, "
                "'xesam:artist': <['Lil Durk']>, 'xesam:title': <'All My Life (feat. J. Cole)'>, "
                "'mpris:artUrl': <'file:///tmp/art.jpg'>,)")

    def test_gdbus_playing_track_detected(self):
        def fake_gdbus(args):
            if "ListNames" in args[-1]:
                return self.LIST_NAMES
            if args[-1] == "PlaybackStatus":
                return self.STATUS_PLAYING
            if args[-1] == "Metadata":
                return self.METADATA
            return None

        with patch.object(music_recognizer, "_list_mpris_players", return_value=[]), \
             patch.object(music_recognizer, "_run_gdbus", side_effect=fake_gdbus):
            result = music_recognizer.now_playing_mpris()
        assert result["title"] == "All My Life (feat. J. Cole)"
        assert result["artist"] == "Lil Durk"
        assert result["album"] == "Almost Healed"
        assert result["source"] == "mpris"

    def test_gdbus_paused_players_skipped(self):
        def fake_gdbus(args):
            if "ListNames" in args[-1]:
                return self.LIST_NAMES
            return self.STATUS_PAUSED

        with patch.object(music_recognizer, "_list_mpris_players", return_value=[]), \
             patch.object(music_recognizer, "_run_gdbus", side_effect=fake_gdbus):
            assert music_recognizer.now_playing_mpris() is None

    def test_gdbus_browser_players_preferred(self):
        with patch.object(music_recognizer, "_run_gdbus", return_value=self.LIST_NAMES):
            order = music_recognizer._mpris_players_gdbus()
        assert order[0] == "org.mpris.MediaPlayer2.chromium"

    def test_playerctl_empty_falls_back_to_gdbus(self):
        with patch.object(music_recognizer, "_list_mpris_players", return_value=[]), \
             patch.object(music_recognizer, "_run_gdbus", return_value=None):
            assert music_recognizer.now_playing_mpris() is None


class TestShazamErrorPageBackoff:
    """The pinned shazamio 0.2.0.0 reports Shazam's non-JSON error pages (HTTP
    429 rate limiting, see ShazamIO issue #81) as "Check args, URL is invalid".
    Our wrapper paces calls process-wide and backs off on that signature."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setattr(music_recognizer, "SHAZAM_MIN_INTERVAL_S", 0.0)
        monkeypatch.setattr(music_recognizer, "_last_shazam_call", 0.0)
        self.sleeps: list[float] = []

        async def fake_sleep(seconds):
            self.sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        yield
        music_recognizer._last_shazam_call = 0.0

    def test_url_template_has_no_stray_space(self):
        from shazamio.misc import ShazamUrl

        assert "sampling=true " not in ShazamUrl.SEARCH_FROM_FILE
        assert "sampling=true&" in ShazamUrl.SEARCH_FROM_FILE

    def test_retries_error_page_then_succeeds(self):
        calls = []

        class FlakyShazam:
            async def recognize_song(self, data):
                calls.append(data)
                if len(calls) < 3:
                    raise Exception("Check args, URL is invalid\nURL- https://amp.shazam.com/x")
                return {"matches": [], "track": {}}

        result = asyncio.run(music_recognizer._recognize_with_backoff(FlakyShazam(), b"audio"))
        assert result == {"matches": [], "track": {}}
        assert len(calls) == 3
        assert len(self.sleeps) == 2  # backoff between attempts, growing
        assert self.sleeps[0] < self.sleeps[1]

    def test_non_error_page_failure_is_not_retried(self):
        calls = []

        class BrokenShazam:
            async def recognize_song(self, data):
                calls.append(data)
                raise ValueError("bad audio")

        with pytest.raises(ValueError):
            asyncio.run(music_recognizer._recognize_with_backoff(BrokenShazam(), b"audio"))
        assert len(calls) == 1

    def test_error_page_exhaustion_raises(self):
        class AlwaysDown:
            async def recognize_song(self, data):
                raise Exception("Check args, URL is invalid\nURL- x")

        with pytest.raises(Exception, match="URL is invalid"):
            asyncio.run(music_recognizer._recognize_with_backoff(AlwaysDown(), b"audio", attempts=2))

    def test_pacer_spaces_calls(self, monkeypatch):
        monkeypatch.setattr(music_recognizer, "SHAZAM_MIN_INTERVAL_S", 30.0)
        music_recognizer._last_shazam_call = time.monotonic()

        class OkShazam:
            async def recognize_song(self, data):
                return {}

        asyncio.run(music_recognizer._recognize_with_backoff(OkShazam(), b"a"))
        assert self.sleeps and self.sleeps[0] > 25

    def test_recognize_audio_bytes_returns_none_after_exhaustion(self, monkeypatch):
        class DownShazam:
            async def recognize_song(self, data):
                raise Exception("Check args, URL is invalid\nURL- x")

        monkeypatch.setattr(music_recognizer, "Shazam", DownShazam)
        wav = music_recognizer.AudioSegment.silent(duration=1500).export(format="wav").read()
        assert asyncio.run(music_recognizer.recognize_audio_bytes(wav)) is None


class TestParseYoutubeUrl:
    def test_hub_providers_youtube(self):
        result = {
            "matches": [{"id": "m1"}],
            "track": {
                "title": "Poker Face",
                "subtitle": "Lady Gaga",
                "hub": {
                    "providers": [
                        {"type": "SPOTIFY", "actions": [{"uri": "spotify:track:x"}]},
                        {
                            "type": "YOUTUBE",
                            "actions": [{"uri": "https://www.youtube.com/watch?v=bESGLojNYSo"}],
                        },
                    ]
                },
            },
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["youtube_url"] == "https://www.youtube.com/watch?v=bESGLojNYSo"

    def test_video_section_youtubeurl_actions(self):
        result = {
            "matches": [{"id": "m1"}],
            "track": {
                "title": "Song",
                "subtitle": "Artist",
                "sections": [
                    {
                        "type": "VIDEO",
                        "youtubeurl": {"actions": [{"uri": "https://youtu.be/abc123"}]},
                    }
                ],
            },
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["youtube_url"] == "https://youtu.be/abc123"

    def test_video_section_youtubeurl_string(self):
        result = {
            "matches": [{"id": "m1"}],
            "track": {
                "title": "Song",
                "subtitle": "Artist",
                "sections": [{"type": "VIDEO", "youtubeurl": "https://youtu.be/plain"}],
            },
        }
        parsed = music_recognizer._parse_shazam_result(result)
        assert parsed is not None
        assert parsed["youtube_url"] == "https://youtu.be/plain"


class TestResolveIdentifyDevice:
    def test_ignores_monitor_audio_source(self, monkeypatch):
        monkeypatch.delenv("LIGHT_AUDIO_SOURCE", raising=False)
        monkeypatch.delenv("LIGHT_MIC_DEVICE", raising=False)
        monkeypatch.setattr(
            music_recognizer.lightctl,
            "load_config",
            lambda: {"audio_source": "monitor", "mic_device": "hw:USB,0"},
        )
        device = music_recognizer.resolve_identify_device()
        assert device == "hw:USB,0"
        assert "monitor" not in str(device).lower()

    def test_defaults_to_default_input_not_monitor(self, monkeypatch):
        monkeypatch.delenv("LIGHT_AUDIO_SOURCE", raising=False)
        monkeypatch.delenv("LIGHT_MIC_DEVICE", raising=False)
        monkeypatch.setattr(
            music_recognizer.lightctl,
            "load_config",
            lambda: {"audio_source": "monitor"},
        )
        device = music_recognizer.resolve_identify_device()
        assert device not in ("monitor", "@DEFAULT_MONITOR@")
        assert not str(device).endswith(".monitor")

    def test_does_not_query_pactl_monitor(self, monkeypatch):
        monkeypatch.delenv("LIGHT_MIC_DEVICE", raising=False)
        monkeypatch.setattr(
            music_recognizer.lightctl,
            "load_config",
            lambda: {"audio_source": "monitor", "mic_device": ""},
        )

        def boom():
            raise AssertionError("ambient ID must not use the system monitor")

        monkeypatch.setattr(music_recognizer, "_pactl_monitor_source", boom)
        music_recognizer.resolve_identify_device()

    def test_light_mic_device_env_wins(self, monkeypatch):
        monkeypatch.setenv("LIGHT_MIC_DEVICE", "42")
        monkeypatch.setattr(
            music_recognizer.lightctl,
            "load_config",
            lambda: {"audio_source": "monitor", "mic_device": "hw:USB,0"},
        )
        assert music_recognizer.resolve_identify_device() == 42


class TestRecognizeAmbient:
    def test_does_not_consult_mpris(self, monkeypatch):
        def boom():
            raise AssertionError("ambient ID must not use MPRIS")

        monkeypatch.setattr(music_recognizer, "now_playing_mpris", boom)
        seen: list = []

        async def fake_mic(duration, sample_rate, device=None):
            seen.append(device)
            return {"title": "Song", "artist": "Artist", "source": "shazam"}

        monkeypatch.setattr(music_recognizer, "recognize_microphone", fake_mic)
        monkeypatch.setattr(music_recognizer, "resolve_identify_device", lambda: "hw:mic")
        result = asyncio.run(music_recognizer.recognize_ambient())
        assert result == {"title": "Song", "artist": "Artist", "source": "shazam"}
        assert seen == ["hw:mic"]

    def test_retries_second_window_on_no_match(self, monkeypatch):
        attempts: list[float] = []

        async def fake_mic(duration, sample_rate, device=None):
            attempts.append(duration)
            if len(attempts) == 1:
                return None
            return {"title": "Live Song", "artist": "Lady Gaga"}

        monkeypatch.setattr(music_recognizer, "recognize_microphone", fake_mic)
        monkeypatch.setattr(music_recognizer, "resolve_identify_device", lambda: "mic")
        result = asyncio.run(music_recognizer.recognize_ambient())
        assert result is not None
        assert result["title"] == "Live Song"
        assert len(attempts) == 2

    def test_does_not_retry_after_match(self, monkeypatch):
        attempts: list[int] = []

        async def fake_mic(duration, sample_rate, device=None):
            attempts.append(1)
            return {"title": "Hit"}

        monkeypatch.setattr(music_recognizer, "recognize_microphone", fake_mic)
        monkeypatch.setattr(music_recognizer, "resolve_identify_device", lambda: "mic")
        result = asyncio.run(music_recognizer.recognize_ambient())
        assert result == {"title": "Hit"}
        assert len(attempts) == 1

    def test_never_passes_monitor_device(self, monkeypatch):
        monkeypatch.setattr(
            music_recognizer, "resolve_audio_source", lambda cfg=None: "alsa_output.pci.monitor"
        )
        monkeypatch.setattr(music_recognizer, "resolve_identify_device", lambda: "hw:USB")
        seen: list = []

        async def fake_mic(duration, sample_rate, device=None):
            seen.append(device)
            return {"title": "X"}

        monkeypatch.setattr(music_recognizer, "recognize_microphone", fake_mic)
        asyncio.run(music_recognizer.recognize_ambient())
        assert seen == ["hw:USB"]

    def test_default_duration_is_at_least_ten_seconds(self, monkeypatch):
        seen: list[float] = []

        async def fake_mic(duration, sample_rate, device=None):
            seen.append(duration)
            return {"title": "X"}

        monkeypatch.setattr(music_recognizer, "recognize_microphone", fake_mic)
        monkeypatch.setattr(music_recognizer, "resolve_identify_device", lambda: "mic")
        asyncio.run(music_recognizer.recognize_ambient())
        assert seen[0] >= 10.0

    def test_sync_wrapper_calls_ambient(self, monkeypatch):
        async def fake_ambient(duration=None, sample_rate=None, device=None, windows=None):
            return {"title": "Ambient", "artist": "Mic"}

        monkeypatch.setattr(music_recognizer, "recognize_ambient", fake_ambient)
        assert music_recognizer.recognize_ambient_sync() == {"title": "Ambient", "artist": "Mic"}
