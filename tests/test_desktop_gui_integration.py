"""Real HTTP boundaries for reviewed calibration and the local library."""
import base64
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import light_gui


@pytest.fixture
def server():
    calibration = Mock()
    calibration.scan.return_value = {'ok': True, 'token': 'review-me', 'can_apply': True, 'probes': {}}
    calibration.apply.return_value = {'ok': True, 'written': True}
    library = Mock()
    library.snapshot.return_value = {'ok': True, 'playlists': [], 'queue': []}
    library.handle.return_value = {'ok': True, 'playlists': [{'id': 'local', 'name': 'Test', 'items': []}], 'queue': []}
    state = SimpleNamespace(client=object(), reload_controllers=Mock(return_value=['left']))
    with patch.object(light_gui, 'calibration_service_for', return_value=calibration, create=True), patch.object(light_gui, 'player_library_for', return_value=library, create=True):
        http = ThreadingHTTPServer(('127.0.0.1', 0), light_gui.make_handler(state))
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        yield http.server_port, state, calibration, library
        http.shutdown()
        http.server_close()
        thread.join(2)


def request(server, path, data=None, origin=None):
    conn = http.client.HTTPConnection('127.0.0.1', server[0], timeout=3)
    headers = {'Content-Type': 'application/json'}
    if origin:
        headers['Origin'] = origin
    conn.request('GET' if data is None else 'POST', path, body=None if data is None else json.dumps(data), headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, json.loads(raw) if raw.startswith(b'{') else {'raw': raw.decode()}


def test_calibration_scan_never_applies(server):
    status, data = request(server, '/api/calibration', {'action': 'scan'})
    assert status == 200 and data['token'] == 'review-me'
    server[2].apply.assert_not_called()
    server[1].reload_controllers.assert_not_called()


def test_calibration_apply_uses_review_token_and_reloads_only_after_write(server):
    status, data = request(server, '/api/calibration', {'action': 'apply', 'token': 'review-me'})
    assert status == 200 and data['ok']
    server[2].apply.assert_called_once_with('review-me')
    server[1].reload_controllers.assert_called_once()


def test_failed_calibration_does_not_reload(server):
    server[2].apply.return_value = {'ok': False, 'written': False, 'message': 'stale'}
    assert request(server, '/api/calibration', {'action': 'apply', 'token': 'stale'})[1]['ok'] is False
    server[1].reload_controllers.assert_not_called()


def test_cross_origin_calibration_rejected_before_scan(server):
    status, _ = request(server, '/api/calibration', {'action': 'scan'}, origin='https://music.youtube.com')
    assert status == 403
    server[2].scan.assert_not_called()


def test_library_get_and_mutation(server):
    assert request(server, '/api/player/library')[1]['queue'] == []
    assert request(server, '/api/player/library', {'action': 'create', 'name': 'Test'})[1]['playlists'][0]['name'] == 'Test'


def test_calibration_requires_object_body(server):
    status, data = request(server, '/api/calibration', ['scan'])
    assert status == 400 and not data['ok']
    server[2].scan.assert_not_called()


def test_calibration_vision_only_observes_no_planner():
    encoded = base64.b64encode(b'\xff\xd8\xffsample').decode()
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps({'output_text': 'Four vertical strips; orientation uncertain.'}).encode()
    with patch.object(light_gui, 'ai_settings', return_value={'vision_model': 'vision', 'api_key_env': 'KEY'}), patch.object(light_gui, '_ai_requires_key', return_value=False), patch.object(light_gui, '_openai_request') as build, patch.object(light_gui.urllib.request, 'urlopen', return_value=response), patch.object(light_gui, 'apply_ai_plan') as apply, patch.object(light_gui, 'call_openai_for_plan') as plan:
        data = light_gui.call_openai_calibration_observation(encoded, {'left': {'led_count': 82}})
    assert data['ok'] and 'Four vertical strips' in data['observation']
    assert build.call_args.args[0]['max_output_tokens'] == 384
    apply.assert_not_called()
    plan.assert_not_called()


def test_desktop_clock_does_not_probe_external_player():
    import sys
    registry = SimpleNamespace(active=lambda: True, snapshot=lambda source: {'source': source, 'connected': True, 'stale': False, 'playing': True, 'title': 'Inside', 'artist': 'Artist', 'position': 24.5, 'duration': 180, 'observed_at': 1})
    with patch.dict(sys.modules, {'desktop_bridge': SimpleNamespace(registry=registry)}), patch.object(light_gui.music_recognizer, 'now_playing_mpris') as external:
        clock = light_gui.playback_clock_payload()['clock']
    assert clock['position_s'] == 24.5 and clock['duration_s'] == 180
    assert clock['source'] == 'desktop_player'
    external.assert_not_called()


def test_desktop_now_playing_avoids_shazam_and_playerctl():
    import sys
    registry = SimpleNamespace(active=lambda: True, snapshot=lambda source: {'source': source, 'connected': True, 'stale': False, 'playing': True, 'title': 'Inside', 'artist': 'Artist', 'position': 24.5, 'duration': 180, 'observed_at': 1})
    with patch.dict(sys.modules, {'desktop_bridge': SimpleNamespace(registry=registry)}), patch.object(light_gui, 'get_now_playing_playerctl') as external:
        song = light_gui.get_now_playing()
    assert song['title'] == 'Inside'
    external.assert_not_called()


def test_vision_does_not_create_or_evict_review_tokens(server):
    server[2].observe.return_value = {'ok': True, 'probes': {}}
    with patch.object(light_gui, 'call_openai_calibration_observation', return_value={'ok': True, 'observation': 'advisory'}):
        assert request(server, '/api/calibration/vision', {'image': 'fixture'})[1]['ok']
    server[2].scan.assert_not_called()
    server[2].observe.assert_called_once()
