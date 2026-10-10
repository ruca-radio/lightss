from unittest.mock import patch
import light_gui
import light_gui_html


def test_player_defaults_off_without_configuration():
    assert light_gui._audio_player_enabled({}) is False
    assert light_gui._audio_player_enabled({'audio_player': {}}) is False
    assert light_gui._audio_player_enabled({'audio_player': {'enabled': True}}) is True


def test_default_render_is_non_player_without_smart_lighting_panel():
    page = light_gui.render_html()
    assert 'id="smartDirectorCard"' not in page
    assert 'Smart lighting' not in page
    assert 'id="playerSource"' not in page
    assert 'id="modelResponses"' in page
    assert 'id="musicDirectorEnabled"' in page
    assert 'id="aiInput"' in page
    assert 'setInterval(pollSmartDirector, 100)' not in page


def test_music_mode_starts_music_director_not_removed_smart_lighting():
    page = light_gui_html.render_main_html(False)
    start = page[page.index('async function startMusicMode'):page.index('function startBrowserMusicMode')]
    assert "postJson('/api/music-director', {enabled: true})" in start
    assert 'setSmartDirectorMode({enabled: true' not in start


def test_player_toggle_survives_settings_roundtrip():
    config = {'ai': {}, 'audio_player': {'enabled': True, 'youtube_host': 'https://fixture.test'}}
    with patch.object(light_gui.lightctl, 'load_config', return_value=config), patch.object(light_gui.lightctl, 'save_config') as save:
        light_gui.merge_settings_into_config({'audio_player': {'enabled': False}})
    assert save.call_args.args[0]['audio_player'] == {'enabled': False, 'youtube_host': 'https://fixture.test'}


def test_non_player_metadata_polls_without_starting_playback():
    page = light_gui_html.render_main_html(False)
    assert 'setInterval(refreshNowPlaying, 5000)' in page
    refresh = page[page.index('async function refreshNowPlaying'):page.index('// --- browser webcam')]
    assert 'lastObservedSong = np' in refresh
    assert 'updateMusicIdentity(' in refresh
    assert '/api/player' not in refresh


def test_director_model_output_reaches_model_response_ticker():
    page = light_gui_html.render_main_html(False)
    poll = page[page.index('async function pollMusicDirector'):page.index('function setMusicDirectorPolling')]
    assert 'last_response' in poll
    assert "appendModelResponse('ai'" in poll
    assert "appendModelResponse('error'" in poll


def test_director_song_metadata_is_available_with_player_disabled():
    with patch.object(light_gui, 'get_now_playing', return_value=None), patch.object(
        light_gui.music_director, 'director_status', return_value={
            'running': True, 'now_playing': {'title': 'Room Song', 'artist': 'Room Artist', 'source': 'shazam'}}):
        assert light_gui.observed_now_playing(None)['title'] == 'Room Song'


def test_room_observer_identifies_without_player_or_lighting(monkeypatch):
    import threading
    import music_recognizer
    finished = threading.Event()
    def identify(**kwargs):
        finished.set()
        return {'title': 'Room Song', 'artist': 'Room Artist'}
    monkeypatch.setattr(music_recognizer, 'is_available', lambda: True)
    monkeypatch.setattr(music_recognizer, 'recognize_ambient_sync', identify)
    observer = music_recognizer.AmbientSongObserver()
    observer.snapshot()
    assert finished.wait(2)
    observer._worker.join(2)
    assert observer.snapshot()['artist'] == 'Room Artist'


def test_room_observer_expires_previous_song(monkeypatch):
    import music_recognizer
    observer = music_recognizer.AmbientSongObserver()
    observer.remember({'title': 'Old Song'})
    monkeypatch.setattr(music_recognizer.time, 'monotonic', lambda: observer._identified_at + 181)
    assert observer.snapshot(refresh=False) is None
