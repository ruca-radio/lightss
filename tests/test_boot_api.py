"""Self-config boot API + startup ordering (Lead-owned integration tests)."""

from __future__ import annotations

import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import light_gui


class FakeBoot:
    """Minimal stand-in for boot_sequence.py used through sys.modules."""

    DEFAULTS = {"enabled": True, "dwell_s": 1.1, "brightness": 160}

    def __init__(self):
        self.begun = []
        self.status = {
            "running": False,
            "phase": "idle",
            "started_at": None,
            "finished_at": None,
            "step": None,
            "lines": [],
            "report": None,
            "error": None,
        }
        self.context = "Self-config boot report: far-left left seg 0; middle-left left seg 1."

    def validate_config(self, raw):
        validated = dict(self.DEFAULTS)
        validated.update({key: value for key, value in (raw or {}).items() if key in self.DEFAULTS})
        if not isinstance(validated["enabled"], bool):
            raise ValueError("enabled must be a boolean.")
        return validated

    def start_boot(self, fleet, config=None):
        self.begun.append((fleet, dict(config or {})))
        return None

    def boot_status(self):
        return dict(self.status)

    def context_text(self):
        return self.context

    def summary_text(self, report):
        return "boot line one\nboot line two"


def _state():
    return SimpleNamespace(client=object(), dry_run=False)


def _with_fake_boot(config):
    fake = FakeBoot()
    return fake, patch.dict(sys.modules, {"boot_sequence": fake}), patch.object(
        light_gui.lightctl, "load_config", side_effect=lambda: dict(config)
    )


def test_boot_config_merges_defaults_and_section():
    fake, modules, load = _with_fake_boot({"boot_sequence": {"dwell_s": 1.4}})
    with modules, load:
        settings = light_gui.boot_sequence_config()
    assert settings == {"enabled": True, "dwell_s": 1.4, "brightness": 160}


def test_boot_status_payload_includes_enabled_and_report_shape():
    fake, modules, load = _with_fake_boot({"boot_sequence": {"enabled": False}})
    fake.status["phase"] = "done"
    with modules, load:
        payload = light_gui.boot_status_payload()
    assert payload["ok"] is True
    assert payload["enabled"] is False
    assert payload["phase"] == "done"
    for key in ("started_at", "finished_at", "step", "lines", "report", "error"):
        assert key in payload


def test_boot_status_degrades_when_module_is_unavailable():
    with patch.dict(sys.modules, {"boot_sequence": None}):
        payload = light_gui.boot_status_payload()
    assert payload["ok"] is False
    assert payload["phase"] == "idle"
    assert "Self-config unavailable" in payload["error"]


def test_configure_boot_sequence_persists_only_its_own_section():
    config = {"music_director": {"enabled": True}}
    fake, modules, load = _with_fake_boot(config)

    def mutate(mutator):
        mutator(config)
        return config

    with (
        modules,
        load,
        patch.object(light_gui.lightctl, "mutate_config", side_effect=mutate),
    ):
        payload = light_gui.configure_boot_sequence(_state(), {"enabled": False, "brightness": 90})

    assert payload["enabled"] is False
    assert config["music_director"] == {"enabled": True}
    assert config["boot_sequence"] == {"enabled": False, "dwell_s": 1.1, "brightness": 90}


def test_configure_boot_sequence_rejects_unknown_keys_before_writing():
    fake, modules, load = _with_fake_boot({})
    with (
        modules,
        load,
        patch.object(light_gui.lightctl, "mutate_config") as mutate,
    ):
        with pytest.raises(ValueError, match="Unknown self-config setting"):
            light_gui.configure_boot_sequence(_state(), {"action": "restart"})
    mutate.assert_not_called()


def test_run_now_refuses_while_another_wall_owner_is_active():
    fake, modules, load = _with_fake_boot({})
    with (
        modules,
        load,
        patch.object(light_gui, "_is_fleet", return_value=True),
        patch.object(light_gui, "wall_automation_busy", return_value="Smart Director"),
    ):
        with pytest.raises(RuntimeError, match="stop Smart Director first"):
            light_gui.configure_boot_sequence(_state(), {"run": True})
    assert fake.begun == []


def test_run_now_works_even_when_periodic_boot_is_disabled():
    fake, modules, load = _with_fake_boot({"boot_sequence": {"enabled": False}})
    with (
        modules,
        load,
        patch.object(light_gui, "_is_fleet", return_value=True),
        patch.object(light_gui, "wall_automation_busy", return_value=None),
    ):
        light_gui.configure_boot_sequence(_state(), {"run": True})
    assert len(fake.begun) == 1
    assert isinstance(fake.begun[0][0], object)


def test_startup_waits_for_boot_before_starting_automation():
    order = []
    boot = Mock()
    boot.join.side_effect = lambda: order.append("boot-joined")
    with (
        patch.object(light_gui, "_start_boot_sequence", side_effect=lambda state: (order.append("boot-started"), boot)[1]),
        patch.object(light_gui, "_start_configured_automation", side_effect=lambda state: order.append("automation")),
        patch.object(light_gui, "boot_status_payload", return_value={"ok": True, "report": None}),
    ):
        light_gui._boot_then_automation(_state())
    assert order == ["boot-started", "boot-joined", "automation"]


def test_startup_still_starts_automation_when_boot_raises():
    order = []
    with (
        patch.object(light_gui, "_start_boot_sequence", side_effect=RuntimeError("boom")),
        patch.object(light_gui, "_start_configured_automation", side_effect=lambda state: order.append("automation")),
        patch.object(light_gui, "boot_status_payload", return_value={"ok": True, "report": None}),
    ):
        light_gui._boot_then_automation(_state())
    assert order == ["automation"]


def test_configured_automation_prefers_smart_director():
    state = _state()
    with (
        patch.object(light_gui, "smart_director_config", return_value={"enabled": True}),
        patch.object(light_gui, "auto_start_smart_director") as smart,
        patch.object(light_gui, "music_director_config", return_value={"enabled": True}),
    ):
        light_gui._start_configured_automation(state)
    smart.assert_called_once_with(state)


def test_configured_automation_falls_back_to_legacy_music_director():
    state = _state()
    with (
        patch.object(light_gui, "smart_director_config", return_value={"enabled": False}),
        patch.object(light_gui, "auto_start_smart_director") as smart,
        patch.object(light_gui, "music_director_config", return_value={"enabled": True}),
        patch.object(light_gui, "_music_director_start_kwargs", return_value={"ai_settings": {}}),
        patch.dict(sys.modules, {"music_director": Mock(start_director=Mock())}),
    ):
        light_gui._start_configured_automation(state)
    smart.assert_not_called()


def test_enabling_legacy_music_director_is_refused_while_ai_lighting_is_on():
    """A stray legacy-toggle click must never silently disable AI lighting."""
    music = Mock()

    with (
        patch.object(light_gui, "smart_director_config", return_value={"enabled": True}),
        patch.object(light_gui, "_music_director_start_kwargs", return_value={"ai_settings": {}}),
        patch.object(light_gui, "set_music_director_enabled") as persist,
        patch.dict(sys.modules, {"music_director": music}),
    ):
        with pytest.raises(RuntimeError, match="AI lighting \\(auto\\) is on"):
            light_gui.set_music_director(object(), True)

    music.start_director.assert_not_called()
    persist.assert_not_called()


def test_enabling_legacy_music_director_works_when_ai_lighting_is_off():
    music = Mock()
    music.director_status.return_value = {"running": True}

    with (
        patch.object(light_gui, "smart_director_config", return_value={"enabled": False}),
        patch.object(light_gui, "smart_director_owns_wall", return_value=False),
        patch.object(light_gui, "_music_director_start_kwargs", return_value={"ai_settings": {}}),
        patch.object(light_gui, "set_music_director_enabled") as persist,
        patch.dict(sys.modules, {"music_director": music}),
    ):
        result = light_gui.set_music_director(object(), True)

    assert result["ok"] is True
    music.start_director.assert_called_once()
    persist.assert_called_once_with(True)


def test_explicit_music_director_request_hands_wall_over_from_ai_lighting():
    """A GUI click on Music Director must switch, not dead-end, when AI lighting is on."""
    state = SimpleNamespace(client=object())

    with (
        patch.object(light_gui, "smart_director_config", return_value={"enabled": True}),
        patch.object(light_gui, "configure_smart_director") as configure,
        patch.object(
            light_gui, "set_music_director", return_value={"ok": True, "enabled": True}
        ) as start,
    ):
        result = light_gui.apply_music_director_request(state, True)

    assert result == {"ok": True, "enabled": True, "switched_from": "ai_lighting"}
    configure.assert_called_once_with(state, {"enabled": False})
    start.assert_called_once_with(state.client, True)


def test_explicit_music_director_request_restores_ai_lighting_when_start_fails():
    state = SimpleNamespace(client=object())

    with (
        patch.object(light_gui, "smart_director_config", return_value={"enabled": True}),
        patch.object(light_gui, "configure_smart_director") as configure,
        patch.object(light_gui, "set_music_director", side_effect=RuntimeError("no audio")),
    ):
        with pytest.raises(RuntimeError, match="no audio"):
            light_gui.apply_music_director_request(state, True)

    assert [call.args[1] for call in configure.call_args_list] == [
        {"enabled": False},
        {"enabled": True, "mode": "auto"},
    ]


def test_music_director_request_leaves_ai_lighting_alone_when_it_is_off():
    state = SimpleNamespace(client=object())

    with (
        patch.object(light_gui, "smart_director_config", return_value={"enabled": False}),
        patch.object(light_gui, "configure_smart_director") as configure,
        patch.object(light_gui, "set_music_director", return_value={"ok": True}) as start,
    ):
        result = light_gui.apply_music_director_request(state, True)

    assert result == {"ok": True}
    configure.assert_not_called()
    start.assert_called_once_with(state.client, True)


def test_stopping_music_director_never_touches_ai_lighting():
    state = SimpleNamespace(client=object())

    with (
        patch.object(light_gui, "smart_director_config", return_value={"enabled": True}),
        patch.object(light_gui, "configure_smart_director") as configure,
        patch.object(
            light_gui, "set_music_director", return_value={"ok": True, "enabled": False}
        ) as stop,
    ):
        light_gui.apply_music_director_request(state, False)

    configure.assert_not_called()
    stop.assert_called_once_with(state.client, False)


def test_music_director_handover_persists_a_single_wall_owner():
    """The full switch writes AI lighting off and Music Director on to config."""
    config = {
        "smart_director": {"enabled": True, "mode": "auto"},
        "music_director": {"enabled": False},
    }

    def save(updated):
        config.clear()
        config.update(json.loads(json.dumps(updated)))

    music = Mock()
    music.director_status.return_value = {"running": True}
    state = SimpleNamespace(client=object())

    with (
        patch.object(
            light_gui.lightctl,
            "load_config",
            side_effect=lambda: json.loads(json.dumps(config)),
        ),
        patch.object(light_gui.lightctl, "save_config", side_effect=save),
        patch.object(
            light_gui, "_music_director_start_kwargs", return_value={"ai_settings": {}}
        ),
        patch.object(light_gui, "smart_director_owns_wall", return_value=False),
        patch.dict(sys.modules, {"music_director": music}),
    ):
        result = light_gui.apply_music_director_request(state, True)

    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["switched_from"] == "ai_lighting"
    assert config["smart_director"]["enabled"] is False
    assert config["music_director"]["enabled"] is True
    music.start_director.assert_called_once()


def test_ai_context_self_config_block_is_authoritative(monkeypatch):
    fake = FakeBoot()

    async def fake_observe(*args, **kwargs):  # pragma: no cover - not awaited
        return {}

    monkeypatch.setitem(sys.modules, "boot_sequence", fake)
    monkeypatch.setattr(light_gui, "_device_snapshot", lambda client: {})
    import lighting_context

    monkeypatch.setattr(lighting_context, "observe_lighting", lambda client, snapshot=None: {"audio": {"active": False}})
    monkeypatch.setattr(light_gui.lightctl, "recent_fx_text", lambda effects: "")

    text = light_gui.ai_context_text(object())
    assert text.startswith(fake.context)
    assert "Playback clock" in text
