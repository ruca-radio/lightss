"""Behavior that must survive combining the player and controller branches."""

import http.client
import io
import json
import threading
import tomllib
from pathlib import Path
from unittest.mock import Mock

import pytest

import desktop_bridge
import light_gui
import lightctl
import music_director
import music_recognizer


def test_wled_state_recovers_from_interrupted_response(monkeypatch):
    interrupted = io.BytesIO()
    interrupted.read = Mock(side_effect=http.client.IncompleteRead(b'{"on":', 5))
    responses = Mock(side_effect=[interrupted, io.BytesIO(b'{"on":true}')])
    monkeypatch.setattr(lightctl.urllib.request, 'urlopen', responses)
    monkeypatch.setattr(lightctl.time, 'sleep', lambda _: None)
    assert lightctl.LightClient('127.0.0.1').get_state() == {'on': True}
    assert responses.call_count == 2


def test_interrupted_wled_write_is_not_repeated(monkeypatch):
    interrupted = io.BytesIO()
    interrupted.read = Mock(side_effect=http.client.IncompleteRead(b'{', 5))
    responses = Mock(return_value=interrupted)
    monkeypatch.setattr(lightctl.urllib.request, 'urlopen', responses)
    with pytest.raises(RuntimeError, match='Incomplete WLED response'):
        lightctl.LightClient('127.0.0.1').post_state({'on': True})
    assert responses.call_count == 1


@pytest.fixture
def playing_desktop(monkeypatch):
    registry = desktop_bridge.DesktopBridgeRegistry()
    registry.activate()
    registry.publish('youtube_music', {
        'title': 'Current track', 'artist': 'Current artist', 'album': 'Album',
        'playing': True, 'position': 42.0, 'duration': 180.0,
    })
    monkeypatch.setattr(desktop_bridge, 'registry', registry)
    monkeypatch.setattr(music_recognizer, 'now_playing_mpris', lambda: None)
    yield registry
    registry.close()


def test_music_director_reads_integrated_player_without_recording(playing_desktop, monkeypatch):
    capture = Mock(return_value=None)
    monkeypatch.setattr(music_recognizer, 'recognize_ambient_sync', capture)
    director = music_director.MusicDirector(Mock())
    song = director._read_track()
    assert song and song['title'] == 'Current track'
    assert song['position_s'] == 42.0
    capture.assert_not_called()


def test_director_ignores_stale_integrated_player(playing_desktop, monkeypatch):
    playing_desktop._clock = lambda: 1e30
    capture = Mock(return_value={'title': 'Room track'})
    monkeypatch.setattr(music_recognizer, 'recognize_ambient_sync', capture)
    song = music_director.MusicDirector(Mock())._read_track()
    assert song['title'] == 'Room track'
    capture.assert_called_once()


def test_recognition_reads_integrated_player_without_recording(playing_desktop, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    capture = AsyncMock(return_value=None)
    monkeypatch.setattr(music_recognizer, 'recognize_microphone', capture)
    song = asyncio.run(music_recognizer.recognize())
    assert song and song['title'] == 'Current track'
    capture.assert_not_called()


def test_ai_request_reuses_the_identified_room_song(monkeypatch):
    import ai_chat
    monkeypatch.setattr(light_gui, 'get_now_playing', lambda: None)
    monkeypatch.setattr(light_gui, 'music_director_status_payload', lambda: {'status': {}})
    state = light_gui.GuiState(lightctl.LightClient('127.0.0.1', dry_run=True), dry_run=True)
    state.song_observer.remember({'title': 'Room track', 'artist': 'Room artist', 'genre': 'Soul'})
    monkeypatch.setattr(music_recognizer, 'is_available', lambda: False)
    context = Mock(return_value='song context')
    monkeypatch.setattr(light_gui, 'ai_context_text', context)
    monkeypatch.setattr(ai_chat, 'run_chat', lambda *args, **kwargs: {'text': 'Ready', 'log': []})
    server = light_gui.LightsThreadingHTTPServer(('127.0.0.1', 0), light_gui.make_handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request('POST', '/api/ai', json.dumps({'prompt': 'Match this song'}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200, body
        assert context.call_args.args[1]['title'] == 'Room track'
        assert context.call_args.args[1]['genre'] == 'Soul'
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        state.shutdown()


def test_ai_context_keeps_recognized_song_genre(monkeypatch):
    monkeypatch.setattr(light_gui, '_device_snapshot', lambda _: {})
    context = light_gui.ai_context_text(Mock(), {'title': 'Track', 'artist': 'Artist', 'genre': 'Soul'}, playback_clock={})
    assert 'Soul' in context


def test_wheel_includes_every_runtime_module():
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / 'pyproject.toml').read_text())
    modules = set(config['tool']['setuptools']['py-modules'])
    assert {path.stem for path in root.glob('*.py')} <= modules
