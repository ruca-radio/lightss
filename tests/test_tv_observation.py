"""Observation has its own permission; every device boundary is faked."""
import json
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import firetv
import light_gui
import lightctl

OBSERVE_ONLY = {'firetv': {'host': '10.0.0.8:5555', 'enabled': False, 'observation_enabled': True}}
YOUTUBE = 'com.amazon.firetv.youtube'
SPOTIFY = 'com.spotify.tv.android'


def session(package=YOUTUBE, active=True, state=3, description='A song, An artist, null'):
    return f'''    player {package}/player (userId=0)
      ownerPid=1, ownerUid=2, userId=0
      package={package}
      active={str(active).lower()}
      state=PlaybackState {{state={state}, position=1261, speed=1.0}}
      metadata:size=4, description={description}
      queueTitle=null, size=0
'''


@pytest.fixture
def adb(monkeypatch):
    calls = []
    outputs = {'power': 'mWakefulness=Awake',
               'window': f'mCurrentFocus=Window{{1 u0 {YOUTUBE}/dev.cobalt.MainActivity}}',
               'media_session': session()}

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, outputs.get(argv[-1], ''), '')

    monkeypatch.setattr(firetv.subprocess, 'run', run)
    return calls, outputs


def test_observation_defaults_to_disabled_without_adb(adb):
    result = firetv.observe({})
    assert result['observation_enabled'] is False
    assert result['connected'] is False
    assert result['awake'] is None
    assert result['activity_hint'] == 'unknown'
    assert adb[0] == []


def test_control_permission_does_not_implicitly_enable_observation(adb):
    result = firetv.observe({'firetv': {'enabled': True}})
    assert result['control_enabled'] is True
    assert result['observation_enabled'] is False
    assert adb[0] == []


@pytest.mark.parametrize('value', ['false', 1, None, [], {}])
def test_observation_permission_requires_literal_true(adb, value):
    firetv.observe({'firetv': {'observation_enabled': value}})
    assert adb[0] == []


def test_observe_only_reads_power_focus_and_foreground_media(adb):
    result = firetv.observe(OBSERVE_ONLY)
    assert result['control_enabled'] is False
    assert result['connected'] is True
    assert result['awake'] is True
    assert result['foreground_app'] == YOUTUBE
    assert result['media_session'] == {'package': YOUTUBE, 'active': True, 'state': 3,
                                       'description': 'A song, An artist, null'}
    assert [argv[3:] for argv, _ in adb[0]] == [
        ['shell', 'dumpsys', 'power'], ['shell', 'dumpsys', 'window'],
        ['shell', 'dumpsys', 'media_session']]
    assert all(0 < kw['timeout'] <= 3 for _, kw in adb[0])


def test_observation_does_not_unlock_control(adb, monkeypatch):
    monkeypatch.setattr(lightctl, 'load_config', lambda: OBSERVE_ONLY)
    firetv.observe()
    calls = list(adb[0])
    for action in [firetv.wake, firetv.sleep, lambda: firetv.keyevent(85),
                   lambda: firetv.open_url('https://example.test')]:
        with pytest.raises(ValueError, match='disabled'):
            action()
    assert adb[0] == calls


def test_youtube_remains_ambiguous_even_with_song_like_title(adb):
    result = firetv.observe(OBSERVE_ONLY)
    assert result['activity_hint'] == 'unknown'
    assert 'music and video' in result['reason']


def test_music_only_app_requires_active_playback(adb):
    adb[1]['window'] = f'mCurrentFocus=Window{{1 u0 {SPOTIFY}/Activity}}'
    adb[1]['media_session'] = session(SPOTIFY)
    assert firetv.observe(OBSERVE_ONLY)['activity_hint'] == 'music'
    adb[1]['media_session'] = session(SPOTIFY, state=2)
    assert firetv.observe(OBSERVE_ONLY)['activity_hint'] == 'idle'


def test_background_music_does_not_override_foreground_tv(adb):
    app = 'com.amazon.avod.tv.client'
    adb[1]['window'] = f'mCurrentFocus=Window{{1 u0 {app}/Activity}}'
    adb[1]['media_session'] = session(SPOTIFY) + session(app)
    result = firetv.observe(OBSERVE_ONLY)
    assert result['media_session']['package'] == app
    assert result['activity_hint'] == 'tv'


def test_inactive_foreground_sessions_are_ignored(adb):
    adb[1]['media_session'] = session(active=False) + session(SPOTIFY)
    assert firetv.observe(OBSERVE_ONLY)['media_session'] is None


def test_parser_prefers_playing_session_over_paused_session():
    result = firetv.parse_media_session(session(state=2) + session(description='Playing'), YOUTUBE)
    assert result['state'] == 3
    assert result['description'] == 'Playing'


def test_parser_handles_null_metadata_and_no_foreground():
    assert firetv.parse_media_session(session(description='null'), YOUTUBE)['description'] is None
    assert firetv.parse_media_session(session(), None) is None
    assert firetv.parse_media_session('no sessions', YOUTUBE) is None


def test_parser_does_not_include_trailing_audio_playback_dump():
    dump = session() + '\nAudio playback (lastly played comes first)\nsecret irrelevant lines'
    result = firetv.parse_media_session(dump, YOUTUBE)
    assert 'secret' not in json.dumps(result)


def test_sleeping_tv_is_idle_without_media_read(adb):
    adb[1]['power'] = 'mWakefulness=Asleep'
    result = firetv.observe(OBSERVE_ONLY)
    assert result['awake'] is False
    assert result['activity_hint'] == 'idle'
    assert result['media_session'] is None
    assert not any(argv[-1] == 'media_session' for argv, _ in adb[0])


def test_unknown_power_is_not_reported_as_off_or_music(adb):
    adb[1]['power'] = 'unrecognized firmware response'
    assert firetv.observe(OBSERVE_ONLY)['awake'] is None
    assert firetv.observe(OBSERVE_ONLY)['activity_hint'] == 'unknown'


def test_adb_failure_is_unknown_not_tv_off(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired('adb', 2)
    monkeypatch.setattr(firetv.subprocess, 'run', fail)
    result = firetv.observe(OBSERVE_ONLY)
    assert result['connected'] is False
    assert result['awake'] is None
    assert result['activity_hint'] == 'unknown'
    assert 'timed out' in result['error']


def test_media_failure_preserves_valid_power_and_focus(adb, monkeypatch):
    original = firetv.subprocess.run
    def fail_media(argv, **kwargs):
        if argv[-1] == 'media_session':
            raise subprocess.TimeoutExpired('adb', 2)
        return original(argv, **kwargs)
    monkeypatch.setattr(firetv.subprocess, 'run', fail_media)
    result = firetv.observe(OBSERVE_ONLY)
    assert result['connected'] is True
    assert result['awake'] is True
    assert result['media_session'] is None
    assert result['activity_hint'] == 'unknown'
    assert 'media_error' in result


def test_offline_observer_reconnects_once_without_control(adb, monkeypatch):
    original = firetv.subprocess.run
    calls = []
    def offline_once(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 1, '', 'error: device offline')
        return original(argv, **kwargs)
    monkeypatch.setattr(firetv.subprocess, 'run', offline_once)
    assert firetv.observe(OBSERVE_ONLY)['connected'] is True
    assert ['adb', 'connect', '10.0.0.8:5555'] in calls
    assert not any('keyevent' in c or 'start' in c for c in calls)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    data = {'firetv': {'enabled': False, 'host': '10.0.0.8:5555'}, 'keep': {'value': 7}}
    path.write_text(json.dumps(data))
    monkeypatch.setattr(lightctl, '_SCENE_DIR', str(tmp_path))
    monkeypatch.setattr(lightctl, '_CONFIG_PATH', str(path))
    return path


@pytest.fixture
def server(config_file):
    # No GuiState threads or physical LightClient: only the observation routes.
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), light_gui.make_handler(object()))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_port}'
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def request(server, data=None, origin=None, content_type='application/json'):
    headers = {'Content-Type': content_type}
    if origin:
        headers['Origin'] = origin
    req = urllib.request.Request(server + '/api/tv-observation',
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {'ok': False, 'error': body.decode()}


def test_http_toggle_preserves_control_host_and_unrelated_settings(server, config_file, adb):
    code, data = request(server, {'enabled': True})
    assert code == 200
    assert data['observation_enabled'] is True
    assert data['control_enabled'] is False
    assert json.loads(config_file.read_text()) == {
        'firetv': {'enabled': False, 'observation_enabled': True, 'host': '10.0.0.8:5555'},
        'keep': {'value': 7}}
    assert adb[0] == []  # permission write itself must not talk to TV
    code, data = request(server)
    assert code == 200 and data['connected'] is True
    assert data['foreground_app'] == YOUTUBE
    request(server, {'enabled': False})
    calls = list(adb[0])
    assert request(server)[1]['observation_enabled'] is False
    assert adb[0] == calls


@pytest.mark.parametrize('body', ['yes', [], {}, {'enabled': 'false'}, {'enabled': 1},
                                  {'enabled': True, 'action': 'wake'}, {'enabled': None}])
def test_http_rejects_invalid_permission_body_without_changes(server, config_file, adb, body):
    before = config_file.read_text()
    code, data = request(server, body)
    assert code == 400 and data['ok'] is False
    assert config_file.read_text() == before
    assert adb[0] == []


def test_http_rejects_cross_origin_permission_change(server, config_file, adb):
    before = config_file.read_text()
    assert request(server, {'enabled': True}, origin='https://example.test')[0] == 403
    assert config_file.read_text() == before
    assert adb[0] == []


def test_http_rejects_non_json_permission_change(server, config_file):
    before = config_file.read_text()
    assert request(server, {'enabled': True}, content_type='text/plain')[0] == 400
    assert config_file.read_text() == before


def test_http_rejects_oversize_permission_body(server, config_file):
    before = config_file.read_text()
    assert request(server, {'enabled': True, 'extra': 'x' * 2000})[0] == 413
    assert config_file.read_text() == before


def test_routes_are_registered():
    assert '/api/tv-observation' in light_gui.API_GET_PATHS
    assert '/api/tv-observation' in light_gui.API_POST_PATHS


def test_ui_has_independent_read_only_observation_control():
    html = light_gui.render_html()
    assert 'id="tvObservationEnabled"' in html
    assert 'Observe TV (read-only)' in html
    assert 'id="firetvEnabled"' in html
    assert 'TV control' in html
    assert 'id="tvObservationRefresh"' in html
    assert 'id="tvObservationStatus"' in html
    assert 'This switch only permits observation' in html
    assert 'Smart lighting uses TV context when enabled' in html


def test_ui_observation_toggle_uses_only_observation_permission():
    html = light_gui.render_html()
    start = html.index('async function toggleTvObservation')
    end = html.index('// --- Music Director', start)
    body = html[start:end]
    assert "postJson('/api/tv-observation', {enabled: !!enabled})" in body
    assert "postJson('/api/firetv'" not in body
    assert 'toggleFiretv(' not in body
    assert "postAction(" not in body


@pytest.mark.parametrize('power', ['mWakefulness=Dreaming', 'mWakefulness=Dozing', 'mWakefulness=Unexpected'])
def test_unconfirmed_wakefulness_is_not_asleep(adb, power):
    adb[1]['power'] = power
    result = firetv.observe(OBSERVE_ONLY)
    assert result['awake'] is None
    assert result['activity_hint'] == 'unknown'


@pytest.mark.parametrize('dump', ['package=', 'package=\n', 'package=\npackage='])
def test_parser_ignores_truncated_empty_records(dump):
    assert firetv.parse_media_session(dump, YOUTUBE) is None


def test_concurrent_observation_enable_cannot_undo_control_disable(config_file, monkeypatch):
    config_file.write_text(json.dumps({'firetv': {'enabled': True}, 'keep': 7}))
    original_load = lightctl.load_config
    observation_read = threading.Event()
    release_observation = threading.Event()
    control_done = threading.Event()
    errors = []

    def load():
        data = original_load()
        if threading.current_thread().name == 'observation-writer':
            observation_read.set()
            assert release_observation.wait(3)
        return data

    monkeypatch.setattr(lightctl, 'load_config', load)
    def observe_write():
        try:
            light_gui.set_tv_observation_enabled(True)
        except Exception as exc:
            errors.append(exc)
    def control_write():
        try:
            light_gui.set_firetv_enabled(False)
        except Exception as exc:
            errors.append(exc)
        finally:
            control_done.set()

    observer = threading.Thread(target=observe_write, name='observation-writer')
    control = threading.Thread(target=control_write)
    observer.start()
    assert observation_read.wait(2)
    control.start()
    # Old unlocked code finishes this write before the stale observation save.
    # Serialized code blocks here until the observation transaction completes.
    control_done.wait(.2)
    release_observation.set()
    observer.join(3)
    control.join(3)
    assert not errors
    assert not observer.is_alive() and not control.is_alive()
    data = original_load()
    assert data['firetv']['enabled'] is False
    assert data['firetv']['observation_enabled'] is True
    assert data['keep'] == 7
