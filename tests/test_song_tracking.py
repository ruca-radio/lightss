"""Local song-change gating: network recognition is an event, not a timer."""
import io
import math
import struct
import wave
from unittest.mock import Mock

import pytest

import song_tracking


def clip(hz=440, seconds=5, gain=0.25, rate=8000):
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b''.join(struct.pack('<h', int(32767 * gain * math.sin(2 * math.pi * hz * i / rate))) for i in range(int(rate * seconds))))
    return out.getvalue()


SONG = {'title': 'First', 'artist': 'Artist'}


def test_unchanged_audio_never_requeries_after_cooldown():
    remote = Mock(return_value=SONG)
    gate = song_tracking.ChangeAwareRecognizer(remote)
    for now in range(100, 701, 5):
        assert gate.recognize(clip(), now=now) == SONG
    assert remote.call_count == 1


def test_gain_changes_do_not_look_like_new_songs():
    remote = Mock(return_value=SONG)
    gate = song_tracking.ChangeAwareRecognizer(remote)
    for now, gain in [(100, .1), (130, .5), (160, .2), (190, .7)]:
        gate.recognize(clip(gain=gain), now=now)
    assert remote.call_count == 1


def test_sustained_spectral_change_triggers_once():
    remote = Mock(side_effect=[SONG, {'title': 'Second'}])
    gate = song_tracking.ChangeAwareRecognizer(remote)
    gate.recognize(clip(), now=100)
    gate.recognize(clip(2400), now=130)
    assert remote.call_count == 1
    assert gate.recognize(clip(2400), now=135) == {'title': 'Second'}
    gate.recognize(clip(2400), now=200)
    assert remote.call_count == 2


def test_one_transient_does_not_trigger():
    remote = Mock(return_value=SONG)
    gate = song_tracking.ChangeAwareRecognizer(remote)
    for now, hz in [(100, 440), (130, 2400), (135, 440), (160, 440)]:
        gate.recognize(clip(hz), now=now)
    assert remote.call_count == 1


def test_silence_and_invalid_audio_never_query():
    remote = Mock()
    gate = song_tracking.ChangeAwareRecognizer(remote)
    for audio in [b'', b'garbage', clip(gain=0), clip(seconds=.1)]:
        assert gate.recognize(audio, now=100) is None
    remote.assert_not_called()


def test_music_after_sustained_silence_reidentifies():
    remote = Mock(return_value=SONG)
    gate = song_tracking.ChangeAwareRecognizer(remote)
    gate.recognize(clip(), now=100)
    assert gate.recognize(clip(gain=0), now=130) is None
    gate.recognize(clip(), now=135)
    assert remote.call_count == 2


def test_no_match_retries_are_bounded_until_another_change():
    remote = Mock(return_value=None)
    gate = song_tracking.ChangeAwareRecognizer(remote)
    for now in range(100, 701, 5):
        gate.recognize(clip(), now=now)
    assert remote.call_count == 2
    gate.recognize(clip(2400), now=710)
    gate.recognize(clip(2400), now=715)
    assert remote.call_count == 3


def test_pending_change_survives_cooldown():
    remote = Mock(return_value=SONG)
    gate = song_tracking.ChangeAwareRecognizer(remote)
    for now, hz in [(100, 440), (105, 2400), (110, 2400)]:
        gate.recognize(clip(hz), now=now)
    assert remote.call_count == 1
    gate.recognize(clip(2400), now=125)
    assert remote.call_count == 2


def test_reset_allows_new_session_first_recognition():
    remote = Mock(return_value=SONG)
    gate = song_tracking.ChangeAwareRecognizer(remote)
    gate.recognize(clip(), now=100)
    gate.reset()
    gate.recognize(clip(), now=130)
    assert remote.call_count == 2


def test_failed_remote_is_bounded_and_reported():
    remote = Mock(side_effect=RuntimeError('offline'))
    gate = song_tracking.ChangeAwareRecognizer(remote)
    for now in range(100, 200, 5):
        gate.recognize(clip(), now=now)
    assert remote.call_count == 2
    assert gate.status()['last_error'] == 'offline'


def test_mood_session_uses_cached_identity_without_expiring_into_ambient(tmp_path, monkeypatch):
    import mood_orchestrator
    remote = Mock(return_value=SONG)
    client = Mock()
    client.get_state.return_value = {}
    generate = Mock(return_value={'bri': 90})
    session = mood_orchestrator.MoodSession(
        client, remote, generate, change_detection=True,
        cache=mood_orchestrator.SongCache(str(tmp_path / 'moods.json')),
    )
    monkeypatch.setattr(mood_orchestrator.time, 'monotonic', lambda: 100)
    session.start()
    for now in range(100, 301, 5):
        monkeypatch.setattr(mood_orchestrator.time, 'monotonic', lambda now=now: now)
        assert session.sample(clip())['state'] == 'recognized'
    assert remote.call_count == 1
    assert generate.call_count == 1
    assert session.status()['recognition']['mode'] == 'on_change'


def test_playback_clock_advances_without_player_or_network():
    clock = song_tracking.PlaybackClock()
    clock.observe({**SONG, 'position_s': 63.5, 'duration_s': 180, 'status': 'Playing'}, now=100)
    assert clock.snapshot(now=112)['position_s'] == 75.5
    assert clock.snapshot(now=112)['source'] == 'reported_position'


def test_playback_clock_freezes_on_pause_and_accepts_seek():
    clock = song_tracking.PlaybackClock()
    clock.observe({**SONG, 'position_s': 30, 'status': 'Playing'}, now=100)
    clock.observe({**SONG, 'status': 'Paused'}, now=110)
    assert clock.snapshot(now=150)['position_s'] == 40
    clock.observe({**SONG, 'position_s': 80, 'status': 'Playing'}, now=160)
    assert clock.snapshot(now=165)['position_s'] == 85


def test_recognition_time_is_not_fabricated_song_position():
    clock = song_tracking.PlaybackClock()
    clock.observe(SONG, now=100)
    snapshot = clock.snapshot(now=150)
    assert snapshot['position_s'] is None
    assert snapshot['observed_for_s'] == 50
    assert snapshot['source'] == 'unknown'


def test_clock_track_change_discards_old_alignment():
    clock = song_tracking.PlaybackClock()
    clock.observe({**SONG, 'position_s': 50}, now=100)
    clock.observe({'title': 'Next'}, now=110)
    assert clock.snapshot(now=120)['position_s'] is None


def test_clock_rejects_nonfinite_position():
    clock = song_tracking.PlaybackClock()
    clock.observe({**SONG, 'position_s': float('nan')}, now=100)
    assert clock.snapshot(now=110)['position_s'] is None


def test_gate_status_does_not_wait_for_shazam():
    import threading
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    def remote(_):
        started.set()
        release.wait(3)
        return SONG
    gate = song_tracking.ChangeAwareRecognizer(remote)
    worker = threading.Thread(target=lambda: gate.recognize(clip(), now=100))
    worker.start()
    try:
        assert started.wait(2)
        threading.Thread(target=lambda: (gate.status(), done.set()), daemon=True).start()
        assert done.wait(.2), 'status blocked behind remote Shazam call'
    finally:
        release.set()
        worker.join(3)


def test_reset_discards_inflight_recognition():
    gate = None
    def remote(_):
        gate.reset()
        return SONG
    gate = song_tracking.ChangeAwareRecognizer(remote)
    assert gate.recognize(clip(), now=100) is None


def test_stopped_session_does_not_apply_inflight_song(tmp_path):
    import mood_orchestrator
    session = None
    def remote(_):
        session.stop()
        return SONG
    client = Mock()
    generate = Mock(return_value={'bri': 90})
    session = mood_orchestrator.MoodSession(client, remote, generate, change_detection=True,
        cache=mood_orchestrator.SongCache(str(tmp_path/'moods.json')))
    session.start()
    assert session.sample(clip())['state'] == 'idle'
    generate.assert_not_called()
    client.post_state.assert_not_called()


def test_director_ambient_calls_use_change_detector():
    import music_director
    import music_recognizer
    from unittest.mock import patch
    director = music_director.MusicDirector(Mock())
    with patch.object(music_recognizer, 'now_playing_mpris', return_value=None), patch.object(music_recognizer, 'recognize_ambient_sync', return_value=None) as ambient:
        director._read_track()
    assert isinstance(ambient.call_args.kwargs.get('change_tracker'), song_tracking.ChangeAwareRecognizer)


def test_mic_capture_routes_automatic_audio_through_gate(monkeypatch):
    import asyncio
    import numpy as np
    import music_recognizer as mr
    gate = song_tracking.ChangeAwareRecognizer(Mock(return_value=SONG))
    monkeypatch.setattr(mr, '_load_sounddevice', lambda: object())
    monkeypatch.setattr(mr, '_record_audio', lambda *a: (np.sin(np.arange(8000)*.3)*10000).astype(np.int16))
    monkeypatch.setattr(mr, 'Shazam', lambda: pytest.fail('must not bypass automatic gate'))
    assert asyncio.run(mr.recognize_microphone(1, 8000, 'fake', change_tracker=gate)) == SONG


def test_silence_clears_previous_room_clock():
    gate = song_tracking.ChangeAwareRecognizer(Mock(return_value=SONG))
    gate.recognize(clip(), now=100)
    assert gate.status()['clock']['title'] == SONG['title']
    gate.recognize(clip(gain=0), now=105)
    assert gate.status()['clock']['title'] is None
    assert gate.status()['clock']['playing'] is False
