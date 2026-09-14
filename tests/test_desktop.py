import light_desktop
import json
import http.client
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def test_provider_navigation_is_bounded_but_allows_auth_redirects():
    assert light_desktop.provider_navigation_allowed("youtube_music", "https://music.youtube.com/")
    assert light_desktop.provider_navigation_allowed("youtube_music", "https://accounts.google.com/signin")
    assert light_desktop.provider_navigation_allowed("apple_music", "https://music.apple.com/us/browse")
    assert light_desktop.provider_navigation_allowed("apple_music", "https://idmsa.apple.com/appleauth/auth/signin")
    assert not light_desktop.provider_navigation_allowed("youtube_music", "http://127.0.0.1:8123/")
    assert not light_desktop.provider_navigation_allowed("apple_music", "file:///etc/passwd")
    assert not light_desktop.provider_navigation_allowed("apple_music", "https://example.com/")


def test_fixed_provider_urls_never_accept_arbitrary_urls_or_script():
    assert light_desktop.provider_command_url("youtube_music", "search", {"query": "jazz piano"}) == (
        "https://music.youtube.com/search?q=jazz+piano"
    )
    assert light_desktop.provider_command_url("apple_music", "library", {}) == "https://music.apple.com/library"
    assert light_desktop.provider_command_url("youtube_music", "play_id", {"id": "abc_9-x", "kind": "song"}) == (
        "https://music.youtube.com/watch?v=abc_9-x"
    )
    assert light_desktop.provider_command_url("youtube_music", "play_id", {"id": "MPREb_abc-9", "kind": "album"}) == (
        "https://music.youtube.com/browse/MPREb_abc-9"
    )
    assert light_desktop.provider_command_url("apple_music", "play_id", {"id": "12345", "kind": "album"}) == (
        "https://music.apple.com/us/album/12345"
    )
    assert light_desktop.provider_command_url("youtube_music", "eval", {"url": "file:///etc/passwd"}) is None


def test_loopback_aliases_are_blocked_from_remote_profiles():
    for url in (
        "http://127.0.0.1:8123/", "http://127.1:8123/", "http://2130706433:8123/",
        "http://[::1]:8123/", "file:///etc/passwd",
    ):
        assert light_desktop._private_or_local_url(url), url


def test_observer_extracts_provider_track_ids_and_latches_ended_event():
    youtube = light_desktop.provider_observation_script("youtube_music")
    apple = light_desktop.provider_observation_script("apple_music")
    assert "dataset.videoId" in youtube and "searchParams.get('v')" in youtube
    assert "nowPlayingItem" in apple and "playParams" in apple
    assert "endedTrackId" in youtube and "addEventListener('ended'" in youtube


@pytest.mark.parametrize(
    ("source", "fixture", "expected"),
    [
        ("youtube_music", "global.MusicKit=null;m.dataset.videoId='yt-track-1'", "yt-track-1"),
        ("apple_music", "global.MusicKit={getInstance:()=>({nowPlayingItem:{id:'apple.track-1'}})}", "apple.track-1"),
    ],
)
def test_observer_runs_against_media_dom_fixture_and_reports_latched_end(source, fixture, expected):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    script = light_desktop.provider_observation_script(source)
    harness = f"""
const listeners={{}};
const m={{dataset:{{}},paused:false,ended:false,currentTime:4,duration:30,
  getAttribute:()=>null,addEventListener:(name,fn)=>listeners[name]=fn}};
global.location={{href:'https://music.youtube.com/watch?v=url-fallback'}};
global.navigator={{mediaSession:{{metadata:{{title:'Fixture',artist:'Artist',album:'Album'}}}}}};
global.document={{title:'Fixture',querySelector:(selector)=>selector==='video,audio'?m:null}};
{fixture};
const first=eval({json.dumps(script)}); listeners.ended(); m.paused=true;
const ended=eval({json.dumps(script)});
console.log(JSON.stringify({{first,ended}}));
"""
    result = subprocess.run(["node", "-e", harness], text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["first"]["track_id"] == expected
    assert data["first"]["playing"] is True
    assert data["ended"]["track_id"] == expected
    assert data["ended"]["ended"] is True
    assert data["ended"]["playing"] is False


class FakeServer:
    server_address = ("127.0.0.1", 43123)
    served = False
    stopped = False
    closed = False

    def serve_forever(self):
        self.served = True

    def shutdown(self):
        self.stopped = True

    def server_close(self):
        self.closed = True


def test_backend_owner_uses_in_process_loopback_ephemeral_port_and_cleans_up():
    server = FakeServer()
    calls = []
    backend = light_desktop.BackendServer(
        server_factory=lambda address, handler: calls.append((address, handler)) or server,
        handler_factory=lambda state: BaseHTTPRequestHandler,
        state_factory=lambda: "state",
    )
    assert backend.start() == "http://127.0.0.1:43123/"
    assert calls[0][0] == ('127.0.0.1', 0)
    assert issubclass(calls[0][1], BaseHTTPRequestHandler)
    backend.close()
    assert server.stopped and server.closed


def test_backend_cleanup_closes_all_known_state_workers():
    calls = []

    class Worker:
        def stop(self):
            calls.append("stop")

    class Executor:
        def shutdown(self, **kwargs):
            calls.append(("shutdown", kwargs))

    state = type("State", (), {})()
    for name in ("schedule", "mode1", "mood_session", "fade_timer", "_cycle", "_sunrise", "autonomous"):
        setattr(state, name, Worker())
    state.ai_jobs = Executor()
    state._mood_executor = Executor()
    backend = light_desktop.BackendServer(
        server_factory=lambda address, handler: FakeServer(),
        handler_factory=lambda state: BaseHTTPRequestHandler,
        state_factory=lambda: state,
    )
    backend.start()
    backend.close()
    assert calls.count("stop") == 7
    assert len([item for item in calls if isinstance(item, tuple)]) == 2


def test_authenticated_handler_rejects_missing_secret_for_all_methods():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def _ok(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        do_GET = _ok
        do_POST = _ok
        do_HEAD = _ok

    server = ThreadingHTTPServer(("127.0.0.1", 0), light_desktop.authenticated_handler(Handler, "secret"))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        for method in ("GET", "POST", "HEAD"):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
            conn.request(method, "/")
            assert conn.getresponse().status == 403
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
            conn.request(method, "/", headers={"X-Lightss-Desktop-Token": "secret"})
            assert conn.getresponse().status == 200
            conn.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_backend_wraps_server_handler_with_generated_secret():
    captured = []
    backend = light_desktop.BackendServer(
        server_factory=lambda address, handler: captured.append(handler) or FakeServer(),
        handler_factory=lambda state: BaseHTTPRequestHandler,
        state_factory=lambda: object(),
        token_factory=lambda: "per-process-secret",
    )
    backend.start()
    try:
        assert backend.token == "per-process-secret"
        assert captured[0].desktop_token == "per-process-secret"
    finally:
        backend.close()


def test_backend_start_failure_cleans_constructed_state():
    calls = []
    class Worker:
        def stop(self): calls.append("stopped")
    state = type("State", (), {"schedule": Worker()})()
    backend = light_desktop.BackendServer(
        server_factory=lambda *_args: (_ for _ in ()).throw(OSError("bind failed")),
        handler_factory=lambda _state: BaseHTTPRequestHandler,
        state_factory=lambda: state,
    )
    with pytest.raises(OSError, match="bind failed"):
        backend.start()
    assert calls == ["stopped"]
    assert backend.state is None and backend.server is None


def test_privileged_profile_routes_only_exact_backend_origin():
    backend = "http://127.0.0.1:43123/"
    assert light_desktop.is_exact_backend_url("http://127.0.0.1:43123/api/state", backend)
    assert not light_desktop.is_exact_backend_url("http://127.0.0.1:43124/api/state", backend)
    assert not light_desktop.is_exact_backend_url("http://127.1:43123/api/state", backend)
    assert not light_desktop.is_exact_backend_url("https://music.youtube.com/", backend)


def test_capture_permission_is_only_eligible_for_local_ui_media_types():
    backend = "http://127.0.0.1:43123/"
    for kind in ("MediaAudioCapture", "MediaVideoCapture", "MediaAudioVideoCapture"):
        assert light_desktop.local_capture_permission_allowed("http://127.0.0.1:43123/", backend, kind)
    assert not light_desktop.local_capture_permission_allowed("https://music.youtube.com/", backend, "MediaAudioCapture")
    assert not light_desktop.local_capture_permission_allowed("http://127.0.0.1:43123/", backend, "Geolocation")


def test_desktop_source_contains_explicit_capture_confirmation():
    source = open(light_desktop.__file__, encoding="utf-8").read()
    assert "QMessageBox.question" in source
    assert "permission.grant()" in source and "permission.deny()" in source


def test_desktop_source_guards_async_observation_order_and_resets_navigation():
    source = open(light_desktop.__file__, encoding="utf-8").read()
    assert "observation_pending" in source
    assert "observation_generation" in source
    assert "bridge.reset(source)" in source


def test_main_smoke_does_not_import_qt(monkeypatch):
    monkeypatch.setattr(light_desktop, "run_desktop", lambda **kwargs: 0)
    assert light_desktop.main(["--smoke-test"]) == 0
