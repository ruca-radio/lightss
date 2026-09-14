from unittest.mock import patch

import light_gui
import music_recognizer


def test_external_player_position_does_not_require_integrated_player():
    def playerctl(args):
        if args[-1] == 'position':
            return '63.5'
        return 'Title\tArtist\tAlbum\tPaused\t\t180000000'
    with patch.object(music_recognizer, '_list_mpris_players', return_value=['external']), patch.object(music_recognizer, '_run_playerctl', side_effect=playerctl):
        song = music_recognizer.now_playing_mpris(include_paused=True, include_timing=True)
    assert song['position_s'] == 63.5
    assert song['duration_s'] == 180
    assert song['status'] == 'Paused'


def test_clock_payload_falls_back_to_room_observation():
    from unittest.mock import Mock
    state = Mock()
    state.mood_session.status.return_value = {'recognition': {'clock': {'position_s': None, 'observed_for_s': 42, 'source': 'unknown'}}}
    with patch.object(music_recognizer, 'now_playing_mpris', return_value=None):
        payload = light_gui.playback_clock_payload(state)
    assert payload['clock']['position_s'] is None
    assert payload['clock']['observed_for_s'] == 42


def test_ai_context_reports_clock_without_guessing_position():
    from unittest.mock import Mock
    with patch.object(light_gui, '_device_snapshot', return_value=None), patch.object(light_gui, 'playback_clock_payload', return_value={'clock': {'position_s':None, 'source':'unknown'}}):
        text = light_gui.ai_context_text(Mock())
    assert 'Playback clock' in text
    assert 'unknown' in text


def test_external_clock_is_cleared_when_player_disappears():
    from song_tracking import PlaybackClock
    with patch.object(light_gui, '_external_playback_clock', PlaybackClock()), patch.object(music_recognizer, 'now_playing_mpris', side_effect=[{'title': 'External', 'position_s': 60}, None]):
        assert light_gui.playback_clock_payload()['clock']['position_s'] is not None
        light_gui.playback_clock_payload()
        assert light_gui.playback_clock_payload(refresh=False)['clock']['position_s'] is None


def test_ai_cached_external_clock_expires_without_refresh():
    from song_tracking import PlaybackClock
    clock = PlaybackClock()
    clock.observe({'title': 'External', 'position_s': 60}, now=100)
    with patch.object(light_gui, '_external_playback_clock', clock), patch('song_tracking.time.monotonic', return_value=120):
        assert light_gui.playback_clock_payload(refresh=False)['clock']['position_s'] is None
