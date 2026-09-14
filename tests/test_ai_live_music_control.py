"""AI/MCP contract for tuning the active Smart Director music renderer."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

import ai_chat
import mcp_light
import smart_director


def _music_tool() -> dict:
    return next(tool for tool in mcp_light.build_tools() if tool["name"] == "music_show")


def _tool_names(prompt: str | None = None) -> set[str]:
    return {tool["function"]["name"] for tool in ai_chat.chat_tools(prompt)}


def test_music_show_is_core_without_pruning_any_existing_tools():
    before = {tool["name"] for tool in mcp_light.build_tools() if tool["name"] != "music_show"}
    assert "music_show" in _tool_names("make the room cozy")
    assert before <= _tool_names()
    assert {"set_effect", "wled_write", "strips", "music_director"} <= _tool_names()


def test_music_show_schema_exposes_full_bounded_status_tune_accent_contract():
    schema = _music_tool()["inputSchema"]
    props = schema["properties"]
    assert schema["additionalProperties"] is False
    assert props["action"]["enum"] == ["status", "tune", "accent"]
    assert props["action"]["default"] == "status"
    assert props["motion"]["enum"] == ["auto", "flow", "punch", "chase", "spectrum", "comet", "ripple"]
    assert (props["speed"]["minimum"], props["speed"]["maximum"]) == (0.25, 4)
    for key in ("brightness", "intensity", "colorfulness"):
        assert (props[key]["minimum"], props[key]["maximum"]) == (0, 1)
    colors = props["colors"]
    assert (colors["minItems"], colors["maxItems"]) == (1, 5)
    assert colors["items"]["minItems"] == colors["items"]["maxItems"] == 3
    assert colors["items"]["items"]["minimum"] == 0
    assert colors["items"]["items"]["maximum"] == 210
    gains = props["band_gains"]
    assert gains["minItems"] == gains["maxItems"] == 16
    assert (gains["items"]["minimum"], gains["items"]["maximum"]) == (0, 3)
    assert (props["band"]["minimum"], props["band"]["maximum"]) == (0, 15)
    assert props["strength"]["default"] == 1
    assert "composition_mode" in props


@pytest.mark.parametrize("arguments", [
    {},
    {"action": "tune", "motion": "punch", "speed": 1.5, "colors": [[10, 20, 30]]},
    {"action": "accent", "band": 7, "strength": 0.8},
])
def test_music_show_dispatches_verbatim_and_returns_status_envelope(monkeypatch, arguments):
    fleet = Mock()
    expected = {"active": True, "motion": "punch"}
    control = Mock(return_value={"ok": True, "status": expected})
    handoff = Mock()
    monkeypatch.setattr(smart_director, "control_show", control, raising=False)
    monkeypatch.setattr(smart_director, "before_external_write", handoff)

    result = mcp_light.call_tool(fleet, "music_show", arguments, Mock())

    control.assert_called_once_with(fleet, arguments)
    handoff.assert_not_called()
    assert json.loads(result["content"][0]["text"]) == {"ok": True, "status": expected}


@pytest.mark.parametrize("error", [ValueError("bad tuning"), RuntimeError("music renderer inactive")])
def test_music_show_preserves_control_errors(monkeypatch, error):
    monkeypatch.setattr(smart_director, "control_show", Mock(side_effect=error), raising=False)
    with pytest.raises(type(error), match=str(error)):
        mcp_light.call_tool(Mock(), "music_show", {"action": "tune"}, Mock())


def test_prompt_routes_live_music_tuning_without_removing_explicit_manual_handoff():
    prompt = ai_chat.TOOL_CHAT_SYSTEM_PROMPT.lower()
    assert "music_show" in prompt
    assert "live music" in prompt
    assert "native" in prompt and "static" in prompt and "manual" in prompt
    assert "rainbow" in prompt
    assert "white" in prompt
    assert "saturation" in prompt and "contrast" in prompt and "motion" in prompt


def test_run_chat_sends_compact_complete_music_status_to_model(monkeypatch):
    preview = [
        {"channel": f"strip-{index}", "controller": "left" if index < 2 else "right",
         "colors": [[pixel % 211, (pixel + 1) % 211, (pixel + 2) % 211] for pixel in range(32)]}
        for index in range(4)
    ]
    status = {
        "running": True,
        "mode": "music",
        "brightness_percent": 73,
        "audio": {"active": True, "fft": [0.1] * 16},
        "tv": {"foreground_app": "music.app", "media_session": {"description": "Song, Artist, Album"}},
        "intelligence": {"kind": "music", "composition_mode": "pairs", "colors": [[10, 20, 30]]},
        "renderer": {
            "preview": preview,
            "motion": "punch",
            "requested_motion": "auto",
            "speed": 1.328,
            "intensity": 0.87,
            "band_gains": [1.0] * 16,
            "band_levels": [0.2] * 16,
        },
    }
    monkeypatch.setattr(smart_director, "control_show", Mock(return_value={"ok": True, "status": status}))
    captured = {}

    def provider(_url, _headers, body, _timeout):
        if "tool" not in [message.get("role") for message in body["messages"]]:
            return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "music-status", "type": "function",
                "function": {"name": "music_show", "arguments": '{"action":"status"}'},
            }]}}]}
        content = next(message["content"] for message in body["messages"] if message.get("role") == "tool")
        captured["content"] = content
        parsed = json.loads(content)
        renderer = parsed["status"]["renderer"]
        assert renderer["motion"] == "punch"
        assert renderer["requested_motion"] == "auto"
        assert renderer["speed"] == 1.328
        assert renderer["intensity"] == 0.87
        assert len(renderer["band_gains"]) == len(renderer["band_levels"]) == 16
        assert all("colors" not in item and item["color_count"] == 32 for item in renderer["preview"])
        return {"choices": [{"message": {"role": "assistant", "content": "Actual speed is 1.328× with punch motion."}}]}

    monkeypatch.setattr(ai_chat, "_chat_round", provider)
    result = ai_chat.run_chat(Mock(), "What is the live music status?", settings={
        "base_url": "https://example.test/v1", "model": "test", "api_key_env": "UNSET",
    })

    assert result["text"] == "Actual speed is 1.328× with punch motion."
    assert len(captured["content"].encode("utf-8")) < 8192
    assert {"set_effect", "wled_write", "strips", "music_show"} <= _tool_names()


def test_run_chat_appends_live_protocol_to_legacy_custom_prompt(monkeypatch):
    legacy = """MY CUSTOM STYLE\nOUTPUT FORMAT: return {\"actions\": [], \"response\": \"...\"}\nUse brightness 100-250."""
    captured = {}

    def provider(_url, _headers, body, _timeout):
        captured["system"] = body["messages"][0]["content"]
        return {"choices": [{"message": {"role": "assistant", "content": "Readable answer."}}]}

    monkeypatch.setattr(ai_chat, "_chat_round", provider)
    result = ai_chat.run_chat(Mock(), "Tune the active show", system_prompt=legacy, settings={
        "base_url": "https://example.test/v1", "model": "test", "api_key_env": "UNSET",
    })

    system = captured["system"]
    assert system.startswith(legacy)
    assert "MY CUSTOM STYLE" in system and "100-250" in system
    assert "music_show" in system
    assert all(action in system for action in ("status", "tune", "accent"))
    assert "native" in system.lower() and "static" in system.lower() and "manual" in system.lower()
    assert "provided tools" in system.lower()
    assert "human-readable" in system.lower()
    assert result["text"] == "Readable answer."
