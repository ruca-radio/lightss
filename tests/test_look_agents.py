from __future__ import annotations

import color_lab
import look_agents


PARENT = {
    "provider": "ollama",
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "llama3.2",
    "api_key_env": "OLLAMA_KEY",
}


def test_disabled_agents_inherit_parent_but_stay_off():
    settings = {
        **PARENT,
        "agents": {
            "colorist": {"enabled": False, "model": "color-specialist"},
            "motion": {"enabled": True, "model": ""},
        },
    }
    agents = look_agents.resolve_agents(settings)
    assert tuple(agents) == look_agents.ROLES
    assert agents["colorist"]["enabled"] is False
    assert agents["colorist"]["model"] == "color-specialist"
    assert agents["colorist"]["provider"] == "ollama"
    assert agents["colorist"]["base_url"] == PARENT["base_url"]
    assert agents["colorist"]["api_key_env"] == "OLLAMA_KEY"
    assert agents["motion"]["enabled"] is False
    assert agents["motion"]["provider"] == "ollama"
    assert agents["motion"]["base_url"] == PARENT["base_url"]
    assert agents["critic"]["enabled"] is False
    assert agents["critic"]["model"] == "llama3.2"
    assert agents["critic"]["provider"] == "ollama"


def test_enabled_role_requires_flag_and_model():
    settings = {
        **PARENT,
        "agents": {"colorist": {"enabled": True, "model": "colorist-1", "provider": "openai"}},
    }
    agents = look_agents.resolve_agents(settings)
    assert agents["colorist"]["enabled"] is True
    assert agents["colorist"]["model"] == "colorist-1"
    assert agents["colorist"]["provider"] == "openai"
    assert agents["motion"]["enabled"] is False
    assert agents["motion"]["model"] == "llama3.2"


def test_resolve_agents_none_is_all_local():
    agents = look_agents.resolve_agents(None)
    assert set(agents) == set(look_agents.ROLES)
    assert all(role["enabled"] is False for role in agents.values())


def test_complete_json_parses_fenced_json_and_returns_none_on_error():
    captured = {}

    def transport(url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        captured["timeout"] = timeout
        return {
            "choices": [{
                "message": {
                    "content": '```json\n{"colors": [[1, 2, 3]], "mood": "dusk"}\n```',
                }
            }]
        }

    result = look_agents.complete_json(
        {"base_url": "http://example.test/v1", "model": "m"},
        "sys",
        "user",
        transport=transport,
        timeout=7.5,
    )
    assert result == {"colors": [[1, 2, 3]], "mood": "dusk"}
    assert captured["url"] == "http://example.test/v1/chat/completions"
    assert "Authorization" not in captured["headers"]
    assert captured["body"]["model"] == "m"
    assert captured["body"]["messages"][0]["content"] == "sys"
    assert captured["body"]["messages"][1]["content"] == "user"
    assert captured["timeout"] == 7.5

    def boom(_url, _headers, _body, _timeout):
        raise RuntimeError("offline")

    assert look_agents.complete_json(
        {"base_url": "http://x/v1", "model": "m"}, "s", "u", transport=boom
    ) is None

    def bad(_url, _headers, _body, _timeout):
        return {"choices": [{"message": {"content": "not json"}}]}

    assert look_agents.complete_json(
        {"base_url": "http://x/v1", "model": "m"}, "s", "u", transport=bad
    ) is None

    def array_json(_url, _headers, _body, _timeout):
        return {"choices": [{"message": {"content": "[1, 2, 3]"}}]}

    assert look_agents.complete_json(
        {"base_url": "http://x/v1", "model": "m"}, "s", "u", transport=array_json
    ) is None


def test_complete_json_skips_authorization_when_key_env_empty(monkeypatch):
    captured = {}

    def transport(_url, headers, _body, _timeout):
        captured["headers"] = dict(headers)
        return {"choices": [{"message": {"content": "{}"}}]}

    look_agents.complete_json({"base_url": "http://x/v1", "model": "m"}, "s", "u", transport=transport)
    assert "Authorization" not in captured["headers"]

    look_agents.complete_json(
        {"base_url": "http://x/v1", "model": "m", "api_key_env": ""},
        "s",
        "u",
        transport=transport,
    )
    assert "Authorization" not in captured["headers"]

    monkeypatch.setenv("LOOK_AGENTS_TEST_KEY", "secret")
    look_agents.complete_json(
        {"base_url": "http://x/v1", "model": "m", "api_key_env": "LOOK_AGENTS_TEST_KEY"},
        "s",
        "u",
        transport=transport,
    )
    assert captured["headers"].get("Authorization") == "Bearer secret"


def test_design_look_honors_shader_and_composition_hints():
    look = look_agents.design_look(
        "calm",
        shader="twin_helix",
        composition_mode="pairs",
        intensity=0.4,
        seed=2,
    )
    assert look["shader"] == "twin_helix"
    assert look["composition_mode"] == "pairs"
    assert look["intensity"] == 0.4


def test_design_look_local_fallback():
    look = look_agents.design_look("ember dusk", energy="soft", motion="rise", seed=7)
    baseline = color_lab.build_look("ember dusk", "soft", "rise", None, seed=7)
    assert look["prompt"] == "ember dusk"
    assert look["mood"] == "ember dusk"
    assert look["energy"] == "soft"
    assert look["motion"] == "rise"
    assert look["seed"] == 7
    assert look["shader"] == baseline["shader"]
    assert look["colors"] == baseline["colors"]
    assert look["composition_mode"] == baseline["composition_mode"]
    assert look["intensity"] == baseline["intensity"]
    assert look["shader"] in color_lab.SHADERS
    assert look["composition_mode"] in color_lab.COMPOSITION_MODES
    assert look["intensity"] <= 0.82
    assert 2 <= len(look["colors"]) <= 5
    assert look["agents"] == {"colorist": "local", "motion": "local", "critic": "local"}


def test_design_look_colorist_override():
    calls = []

    def complete(settings, system, user):
        calls.append((settings["model"], system, user))
        return {"colors": ["#ff6600", [10, 20, 200], "#112233"], "mood": "canyon ember"}

    settings = {
        **PARENT,
        "agents": {"colorist": {"enabled": True, "model": "colorist-1"}},
    }
    look = look_agents.design_look("sunset", settings=settings, complete=complete, seed=1)
    assert look["agents"]["colorist"] == "model"
    assert look["agents"]["motion"] == "local"
    assert look["mood"] == "canyon ember"
    expected = color_lab.normalize_palette(
        color_lab.coerce_colors(["#ff6600", [10, 20, 200], "#112233"])
    )
    assert look["colors"] == expected
    assert all(channel <= color_lab.RGB_CAP for rgb in look["colors"] for channel in rgb)
    assert calls and calls[0][0] == "colorist-1"


def test_design_look_motion_override():
    def complete(settings, system, user):
        return {"shader": "twin_helix", "composition_mode": "pairs", "intensity": 0.44}

    settings = {
        **PARENT,
        "agents": {"motion": {"enabled": True, "model": "motion-1"}},
    }
    look = look_agents.design_look("calm water", energy="low", settings=settings, complete=complete)
    assert look["agents"]["motion"] == "model"
    assert look["shader"] == "twin_helix"
    assert look["composition_mode"] == "pairs"
    assert look["intensity"] == 0.44
    assert look["shader"] in color_lab.SHADERS


def test_design_look_critic_caps_intensity():
    def complete(settings, system, user):
        return {"shader": "aurora_flow", "composition_mode": "unison", "intensity": 1.5}

    settings = {
        **PARENT,
        "agents": {"motion": {"enabled": True, "model": "motion-1"}},
    }
    look = look_agents.design_look("aurora", settings=settings, complete=complete)
    assert look["intensity"] <= 0.82
    assert look["shader"] == "aurora_flow"
    assert look["agents"]["critic"] == "local"


def test_critic_model_tightens_but_does_not_loosen():
    def complete(settings, system, user):
        if settings.get("model") == "motion-1":
            return {"shader": "aurora_flow", "composition_mode": "unison", "intensity": 0.8}
        if settings.get("model") == "critic-1":
            return {"shader": "strobe", "composition_mode": "unison", "intensity": 0.99}
        return None

    settings = {
        **PARENT,
        "agents": {
            "motion": {"enabled": True, "model": "motion-1"},
            "critic": {"enabled": True, "model": "critic-1"},
        },
    }
    look = look_agents.design_look("aurora", settings=settings, complete=complete)
    assert look["agents"]["critic"] == "model"
    assert look["intensity"] <= 0.82
    assert look["intensity"] <= 0.8
    assert look["shader"] != "strobe"
    assert look["shader"] in color_lab.SHADERS
    assert look["composition_mode"] in color_lab.COMPOSITION_MODES


def test_design_look_model_failure_falls_back_local():
    def complete(settings, system, user):
        return None

    settings = {
        **PARENT,
        "agents": {
            "colorist": {"enabled": True, "model": "c"},
            "motion": {"enabled": True, "model": "mot"},
            "critic": {"enabled": True, "model": "crit"},
        },
    }
    look = look_agents.design_look(
        "ocean tide", energy="calm", motion="fall", seed=3, settings=settings, complete=complete
    )
    baseline = color_lab.build_look("ocean tide", "calm", "fall", None, seed=3)
    assert look["shader"] == baseline["shader"]
    assert look["colors"] == baseline["colors"]
    assert look["agents"]["colorist"] == "failed"
    assert look["agents"]["motion"] == "failed"
    assert look["agents"]["critic"] == "failed"
    assert look["intensity"] <= 0.82
    assert look["shader"] in color_lab.SHADERS


def test_design_look_never_keeps_strobe_shader():
    def complete(settings, system, user):
        return {"shader": "blink_flash", "composition_mode": "unison", "intensity": 0.9}

    settings = {
        **PARENT,
        "agents": {"motion": {"enabled": True, "model": "motion-1"}},
    }
    look = look_agents.design_look("strobe party flash", settings=settings, complete=complete)
    assert look["shader"] in color_lab.SHADERS
    assert "strobe" not in look["shader"]
    assert "blink" not in look["shader"]
    assert "flash" not in look["shader"]
    assert look["intensity"] <= 0.82


def test_apply_look_starts_realtime(monkeypatch):
    import realtime

    calls = []
    monkeypatch.setattr(
        realtime,
        "realtime_start",
        lambda fleet, **kwargs: calls.append((fleet, kwargs)) or "Realtime started: x",
    )
    look = {
        "shader": "ember_rise",
        "mood": "ember",
        "energy": "soft",
        "motion": "rise",
        "colors": [(180, 40, 12), (40, 8, 4)],
        "composition_mode": "unison",
        "intensity": 0.5,
        "seed": 9,
    }
    result = look_agents.apply_look("fleet-obj", look, fps=18, duration_s=12)
    assert "Realtime started" in result
    fleet, kwargs = calls[0]
    assert fleet == "fleet-obj"
    assert kwargs["shader"] == "ember_rise"
    assert kwargs["mood"] == "ember"
    assert kwargs["energy"] == "soft"
    assert kwargs["motion"] == "rise"
    assert kwargs["colors"] == look["colors"]
    assert kwargs["composition_mode"] == "unison"
    assert kwargs["intensity"] == 0.5
    assert kwargs["seed"] == 9
    assert kwargs["fps"] == 18
    assert kwargs["duration_s"] == 12
