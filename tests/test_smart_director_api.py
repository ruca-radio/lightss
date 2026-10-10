import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import light_desktop
import light_gui
import smart_director


def _state(client=None):
    return SimpleNamespace(
        client=client or object(),
        mode1=Mock(),
        mood_session=Mock(),
        schedule=Mock(),
        fade_timer=Mock(),
        _cycle=Mock(),
        _sunrise=Mock(),
        autonomous=Mock(),
    )


def test_configure_merges_validates_persists_and_stops_conflicting_writers():
    state = _state()
    saved = {
        "smart_director": {"enabled": False, "mode": "tv", "tv_theme": "blue"},
        "music_director": {"enabled": True, "other": "preserved"},
        "firetv": {"enabled": False, "observation_enabled": True},
    }

    with (
        patch.object(light_gui.lightctl, "load_config", return_value=saved),
        patch.object(light_gui.lightctl, "save_config") as save,
        patch.object(light_gui, "ai_settings", return_value={"model": "fixture"}),
        patch.object(smart_director, "start", return_value=Mock(status=lambda: {"running": True})) as start,
        patch.object(smart_director, "status", return_value={"running": True}),
    ):
        result = light_gui.configure_smart_director(
            state,
            {"enabled": True, "music_brightness": 0.4},
        )

    assert result["ok"] is True
    assert result["settings"]["enabled"] is True
    assert result["settings"]["mode"] == "tv"
    assert result["settings"]["tv_theme"] == "blue"
    assert result["settings"]["music_brightness"] == 0.4
    start.assert_called_once_with(state.client, result["settings"], {"model": "fixture"})
    for writer in (
        state.mode1,
        state.mood_session,
        state.schedule,
        state.fade_timer,
        state._cycle,
        state._sunrise,
        state.autonomous,
    ):
        writer.stop.assert_called_once()
    written = save.call_args.args[0]
    assert written["music_director"] == {"enabled": False, "other": "preserved"}
    assert written["firetv"] == {"enabled": False, "observation_enabled": True}
    assert written["smart_director"] == result["settings"]


def test_configure_rejects_unknown_keys_before_persisting_or_starting():
    with (
        patch.object(light_gui.lightctl, "load_config", return_value={}),
        patch.object(light_gui.lightctl, "save_config") as save,
        patch.object(smart_director, "start") as start,
    ):
        with pytest.raises(ValueError, match="Unknown Smart Director setting"):
            light_gui.configure_smart_director(_state(), {"enabled": True, "action": "restart"})
    save.assert_not_called()
    start.assert_not_called()


def test_enabled_director_is_rejected_in_dry_run_before_persist_or_start():
    state = _state()
    state.dry_run = True
    with (
        patch.object(light_gui.lightctl, "load_config", return_value={}),
        patch.object(light_gui.lightctl, "save_config") as save,
        patch.object(smart_director, "start") as start,
    ):
        with pytest.raises(ValueError, match="dry-run"):
            light_gui.configure_smart_director(state, {"enabled": True})
    save.assert_not_called()
    start.assert_not_called()


def test_disabling_stops_to_a_steady_state_and_persists_disabled():
    state = _state()
    with (
        patch.object(light_gui.lightctl, "load_config", return_value={"smart_director": {"enabled": True}}),
        patch.object(light_gui.lightctl, "save_config") as save,
        patch.object(smart_director, "stop") as stop,
        patch.object(smart_director, "status", return_value={"running": False, "mode": "off"}),
    ):
        result = light_gui.configure_smart_director(state, {"enabled": False})

    stop.assert_called_once_with(steady=True)
    assert result["settings"]["enabled"] is False
    assert save.call_args.args[0]["smart_director"]["enabled"] is False


@pytest.fixture
def api_server():
    state = _state()
    config = {
        "smart_director": {"enabled": False, "mode": "auto"},
        "music_director": {"enabled": True},
        "firetv": {"enabled": False, "observation_enabled": True},
    }

    def save(updated):
        config.clear()
        config.update(updated)

    with (
        patch.object(light_gui.lightctl, "load_config", side_effect=lambda: dict(config)),
        patch.object(light_gui.lightctl, "save_config", side_effect=save),
        patch.object(smart_director, "start", return_value=Mock()),
        patch.object(smart_director, "stop"),
        patch.object(smart_director, "status", return_value={"running": False, "mode": "off"}),
    ):
        server = ThreadingHTTPServer(("127.0.0.1", 0), light_gui.make_handler(state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server, state, config
        server.shutdown()
        server.server_close()
        thread.join(2)


def _request(server, method="GET", body=None, *, origin=None, content_type="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    headers = {"Content-Type": content_type}
    if origin is not None:
        headers["Origin"] = origin
    encoded = None if body is None else json.dumps(body).encode()
    conn.request(method, "/api/smart-director", body=encoded, headers=headers)
    response = conn.getresponse()
    payload = json.loads(response.read())
    conn.close()
    return response.status, payload


def test_get_and_partial_post_return_full_settings_and_status(api_server):
    server, _, _ = api_server
    status, payload = _request(server)
    assert status == 200
    assert payload["ok"] is True
    assert payload["status"] == {"running": False, "mode": "off"}
    assert set(payload["settings"]) == set(smart_director.DEFAULTS)

    origin = f"http://127.0.0.1:{server.server_port}"
    status, payload = _request(
        server,
        "POST",
        {"enabled": True, "mode": "music", "music_brightness": 0.52},
        origin=origin,
    )
    assert status == 200
    assert payload["settings"]["enabled"] is True
    assert payload["settings"]["mode"] == "music"
    assert payload["settings"]["music_brightness"] == 0.52


def test_post_enforces_origin_json_size_and_default_key_whitelist(api_server):
    server, _, config = api_server
    before = json.loads(json.dumps(config))
    assert _request(server, "POST", {"enabled": True}, origin="https://music.youtube.com")[0] == 403
    assert _request(server, "POST", {"enabled": True}, content_type="text/plain")[0] == 400
    assert _request(server, "POST", {"enabled": True, "action": "off"})[0] == 400
    assert config == before

    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    conn.putrequest("POST", "/api/smart-director")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", "4097")
    conn.endheaders()
    response = conn.getresponse()
    assert response.status == 413
    response.read()
    conn.close()


def test_post_reports_startup_runtime_error_without_persisting_enabled(api_server):
    server, _, config = api_server
    before = json.loads(json.dumps(config))
    with patch.object(smart_director, "start", side_effect=RuntimeError("renderer busy")):
        status, payload = _request(server, "POST", {"enabled": True})
    assert status == 409
    assert payload == {"ok": False, "error": "renderer busy"}
    assert config == before


def test_browser_beat_owner_is_rejected_and_manual_ddp_hands_off_first(monkeypatch):
    state = _state()
    owner = Mock()
    owner.fleet = state.client
    owner.status.return_value = {"running": True, "selected_mode": "auto"}
    monkeypatch.setattr(smart_director, "current", lambda: owner)

    with pytest.raises(ValueError, match="Smart Director owns"):
        state_obj = light_gui.GuiState.__new__(light_gui.GuiState)
        state_obj.client = state.client
        state_obj.mode1 = state.mode1
        state_obj.start_mode1()
    state.mode1.start.assert_not_called()

    fleet = Mock()
    fleet.resolve = Mock()
    fleet.post_state = Mock()
    owner.fleet = fleet
    calls = []
    monkeypatch.setattr(smart_director, "before_external_write", lambda client: calls.append(("pause", client)))
    import realtime
    monkeypatch.setattr(realtime, "realtime_start", lambda client, **kwargs: calls.append(("start", client)) or "started")

    assert light_gui._apply_wall_action(fleet, {"action": "realtime_start"}) == "started"
    assert calls == [("pause", fleet), ("start", fleet)]


def test_smart_owner_rejects_stale_browser_mood_writers(monkeypatch):
    state = _state()
    owner = Mock(fleet=state.client)
    owner.status.return_value = {"running": True, "selected_mode": "music"}
    monkeypatch.setattr(smart_director, "current", lambda: owner)

    with pytest.raises(ValueError, match="Smart Director owns"):
        light_gui.reject_conflicting_browser_writer(state.client, "mood_sample")
    with pytest.raises(ValueError, match="Smart Director owns"):
        light_gui.reject_conflicting_browser_writer(state.client, "mood_start")
    light_gui.reject_conflicting_browser_writer(state.client, "mood_status")


def test_wall_action_passes_unset_composition_through(monkeypatch):
    import dynamic_scenes
    import realtime

    fleet = Mock()
    fleet.resolve = Mock()
    fleet.post_state = Mock()

    realtime_kwargs: dict = {}
    monkeypatch.setattr(
        realtime, "realtime_start",
        lambda client, **kwargs: realtime_kwargs.update(kwargs) or "started",
    )
    light_gui._apply_wall_action(fleet, {"action": "realtime_start"})
    assert "composition_mode" in realtime_kwargs
    assert realtime_kwargs["composition_mode"] is None

    scene_kwargs: dict = {}
    monkeypatch.setattr(
        dynamic_scenes, "apply_dynamic_scene",
        lambda fleet_, **kwargs: scene_kwargs.update(kwargs) or {},
    )
    light_gui._apply_wall_action(fleet, {"action": "dynamic_scene", "mood": "x"})
    assert "composition_mode" in scene_kwargs
    assert scene_kwargs["composition_mode"] is None


def test_desktop_backend_starts_configured_director_and_stops_it_steady():
    state = _state()
    fake_server = Mock()
    fake_server.server_address = ("127.0.0.1", 8123)
    fake_server.serve_forever = lambda: None
    owner = SimpleNamespace(fleet=state.client)
    with (
        patch.object(light_gui, "start_configured_smart_director") as start,
        patch.object(smart_director, "current", return_value=owner),
        patch.object(smart_director, "stop") as stop,
    ):
        class Handler:
            pass

        backend = light_desktop.BackendServer(
            server_factory=lambda *args: fake_server,
            handler_factory=lambda state: Handler,
            state_factory=lambda: state,
            token_factory=lambda: "token",
        )
        backend.start()
        backend.close()

    start.assert_called_once_with(state)
    stop.assert_called_once_with(steady=True)


def test_desktop_backend_survives_smart_startup_runtime_error_and_reports_it():
    state = _state()
    fake_server = Mock()
    fake_server.server_address = ("127.0.0.1", 8124)
    fake_server.serve_forever = lambda: None

    class Handler:
        pass

    with (
        patch.object(light_gui, "start_configured_smart_director", side_effect=RuntimeError("microphone busy")),
        patch.object(smart_director, "status", return_value={"running": False, "mode": "off"}),
    ):
        backend = light_desktop.BackendServer(
            server_factory=lambda *args: fake_server,
            handler_factory=lambda state: Handler,
            state_factory=lambda: state,
        )
        assert backend.start() == "http://127.0.0.1:8124/"
        payload = light_gui.smart_director_status_payload({})
        backend.close()

    assert payload["ok"] is True
    assert payload["status"]["startup_error"] == "microphone busy"
    light_gui._smart_director_startup_error = None


def test_controller_reload_stops_old_director_and_starts_it_on_new_client():
    old_client = Mock()
    state = light_gui.GuiState(old_client)
    state._cycle = None
    state._sunrise = None
    new_client = Mock()
    new_client.names.return_value = ["new"]
    with (
        patch.object(light_gui, "_build_fleet_client", return_value=(new_client, None)),
        patch.object(light_gui, "smart_director_config", return_value={"enabled": True}),
        patch.object(smart_director, "stop") as stop,
        patch.object(light_gui, "start_configured_smart_director") as start,
    ):
        names = state.reload_controllers()

    assert names == ["new"]
    stop.assert_called_once_with(steady=True)
    start.assert_called_once_with(state)
    assert state.client is new_client
    state.schedule.stop()


def test_manual_output_pauses_the_idle_curator_too():
    import idle_curator

    with patch.object(idle_curator, "note_manual_activity") as note:
        light_gui.pause_smart_director_for_manual_output(object())
    note.assert_called_once_with()


def test_configure_idle_curator_persists_and_starts():
    import idle_curator

    state = SimpleNamespace(client=object(), dry_run=False)
    saved = {"idle_curator": {"enabled": True, "min_cycle_s": 120}}
    with (
        patch.object(light_gui.lightctl, "load_config", return_value=saved),
        patch.object(light_gui.lightctl, "save_config") as save,
        patch.object(idle_curator, "start_curator", return_value=object()) as start,
    ):
        result = light_gui.configure_idle_curator(state, {"min_cycle_s": 600})

    assert result["ok"] is True
    assert result["settings"]["min_cycle_s"] == 600
    assert result["settings"]["enabled"] is True
    start.assert_called_once_with(state.client, result["settings"])
    persisted = save.call_args[0][0]
    assert persisted["idle_curator"]["min_cycle_s"] == 600
    assert persisted["idle_curator"]["enabled"] is True


def test_configure_idle_curator_disabled_stops_and_persists():
    import idle_curator

    state = SimpleNamespace(client=object(), dry_run=False)
    saved = {"idle_curator": {"enabled": True}}
    with (
        patch.object(light_gui.lightctl, "load_config", return_value=saved),
        patch.object(light_gui.lightctl, "save_config") as save,
        patch.object(idle_curator, "stop_curator", return_value="Idle curator stopped.") as stop,
    ):
        result = light_gui.configure_idle_curator(state, {"enabled": False})

    assert result["ok"] is True
    stop.assert_called_once_with()
    assert save.call_args[0][0]["idle_curator"]["enabled"] is False


def test_configure_idle_curator_rejects_unknown_settings():
    with pytest.raises(ValueError):
        light_gui.configure_idle_curator(SimpleNamespace(client=object(), dry_run=False), {"cadence": 5})
