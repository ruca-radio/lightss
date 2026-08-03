"""Tests for the settings backend (light_gui settings/system-prompt/AI-config API)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import light_gui
import lightctl


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _StubState:
    """Minimal GuiState stand-in for the settings HTTP handlers."""

    def __init__(self) -> None:
        self.reload_calls = 0

    def reload_controllers(self) -> list[str]:
        self.reload_calls += 1
        return ["left", "right"]


class SettingsApiHttpTest(unittest.TestCase):
    """End-to-end tests against a real ThreadingHTTPServer with a tmp config."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        self._write_config(
            {
                "controllers": [
                    {"name": "right", "host": "http://10.0.0.1", "segments": {"0": "right", "1": "right-center"}},
                    {"name": "left", "host": "http://10.0.0.2", "segments": {"0": "left-center", "1": "left"}},
                ],
                "audio_source": "monitor",
            }
        )
        config_patch = patch.object(lightctl, "_CONFIG_PATH", self.config_path)
        config_patch.start()
        self.addCleanup(config_patch.stop)

        self.state = _StubState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), light_gui.make_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _write_config(self, config: dict) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)

    def _read_config(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _get(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(f"{self.base}{path}", timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body if body is not None else {}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_get_settings_shape(self) -> None:
        status, payload = self._get("/api/settings")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        settings = payload["settings"]
        self.assertEqual(
            set(settings["ai"].keys()),
            {"provider", "base_url", "model", "vision_model", "api_key_env", "api_key_set"},
        )
        self.assertIsInstance(settings["ai"]["api_key_set"], bool)
        self.assertNotIn("api_key", settings["ai"])
        self.assertEqual(settings["ai"]["provider"], "openai")
        self.assertEqual(settings["ai"]["base_url"], light_gui.DEFAULT_AI_BASE_URL)
        self.assertEqual(settings["controllers"][0]["name"], "right")
        self.assertEqual(settings["audio_source"], "monitor")
        self.assertIsNone(settings["mic_device"])
        self.assertIsNone(settings["system_prompt_override"])

    def test_post_settings_merge_roundtrip(self) -> None:
        self._write_config({"existing_key": 1, "ai": {"model": "cfg-model"}})

        status, payload = self._post(
            "/api/settings",
            {"ai": {"provider": "ollama", "base_url": "http://localhost:11434/v1"}, "audio_source": "mic", "mic_device": "2"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        config = self._read_config()
        self.assertEqual(config["existing_key"], 1)
        self.assertEqual(config["ai"]["model"], "cfg-model")  # ai merged key-by-key
        self.assertEqual(config["ai"]["provider"], "ollama")
        self.assertEqual(config["audio_source"], "mic")
        self.assertEqual(config["mic_device"], "2")

        _, settings_payload = self._get("/api/settings")
        settings = settings_payload["settings"]
        self.assertEqual(settings["ai"]["provider"], "ollama")
        self.assertEqual(settings["ai"]["model"], "cfg-model")
        self.assertEqual(settings["audio_source"], "mic")
        self.assertEqual(settings["mic_device"], "2")

    def test_post_settings_ignores_unknown_keys(self) -> None:
        status, payload = self._post("/api/settings", {"bogus": True})

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertNotIn("bogus", self._read_config())

    def test_post_settings_reload_rebuilds_fleet(self) -> None:
        status, payload = self._post("/api/settings/reload")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["controllers"], ["left", "right"])
        self.assertEqual(self.state.reload_calls, 1)

    def test_system_prompt_get_override_and_reset(self) -> None:
        status, payload = self._get("/api/system-prompt")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["default"], light_gui.system_knowledge_prompt())
        self.assertIsNone(payload["override"])
        self.assertEqual(payload["current"], payload["default"])

        status, payload = self._post("/api/system-prompt", {"prompt": "CUSTOM SYSTEM PROMPT"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["current"], "CUSTOM SYSTEM PROMPT")
        self.assertEqual(self._read_config()["system_prompt_override"], "CUSTOM SYSTEM PROMPT")
        # The override is what build_openai_request actually sends.
        request = light_gui.build_openai_request("make it warm")
        self.assertEqual(request["input"][0]["content"], "CUSTOM SYSTEM PROMPT")

        _, payload = self._get("/api/system-prompt")
        self.assertEqual(payload["override"], "CUSTOM SYSTEM PROMPT")
        self.assertEqual(payload["current"], "CUSTOM SYSTEM PROMPT")

        # null and "" both reset to the default prompt.
        for reset_body in ({"prompt": None}, {"prompt": ""}):
            status, payload = self._post("/api/system-prompt", reset_body)
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertIsNone(payload["override"])
            self.assertNotIn("system_prompt_override", self._read_config())
            request = light_gui.build_openai_request("make it warm")
            self.assertEqual(request["input"][0]["content"], light_gui.system_knowledge_prompt())

    def test_controllers_verify_with_mocked_urlopen(self) -> None:
        real_urlopen = urllib.request.urlopen

        def fake_urlopen(url, timeout: float | None = None):
            if isinstance(url, str) and url.startswith("http://10.0.0.1"):
                return _FakeResponse({"ver": "0.15.1", "fxcount": 187})
            if isinstance(url, str) and url.startswith("http://10.0.0."):
                raise urllib.error.URLError("connection refused")
            return real_urlopen(url, timeout=timeout)

        with patch.object(light_gui.urllib.request, "urlopen", side_effect=fake_urlopen):
            status, payload = self._post("/api/controllers/verify")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        results = payload["results"]
        self.assertEqual(set(results.keys()), {"right", "left"})
        self.assertEqual(
            results["right"],
            {"ok": True, "version": "0.15.1", "effects": 187, "error": None},
        )
        self.assertFalse(results["left"]["ok"])
        self.assertIsNone(results["left"]["version"])
        self.assertIsNone(results["left"]["effects"])
        self.assertIn("connection refused", results["left"]["error"])

    def test_ai_test_reports_failure_without_raising(self) -> None:
        real_urlopen = urllib.request.urlopen

        def fake_urlopen(url, timeout: float | None = None):
            if isinstance(url, urllib.request.Request) and url.full_url.startswith(self.base):
                return real_urlopen(url, timeout=timeout)
            raise urllib.error.URLError("no route to host")

        with patch.object(light_gui.urllib.request, "urlopen", side_effect=fake_urlopen):
            status, payload = self._post("/api/ai/test")

        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("no route to host", payload["message"])

    def test_ai_test_success_and_keyless_provider_sends_no_auth_header(self) -> None:
        self._write_config({"ai": {"provider": "ollama", "base_url": "http://localhost:11434/v1", "model": "llama3"}})
        real_urlopen = urllib.request.urlopen
        captured: dict = {}

        def fake_urlopen(request, timeout: float | None = None):
            if isinstance(request, urllib.request.Request) and request.full_url.startswith(self.base):
                return real_urlopen(request, timeout=timeout)
            captured["request"] = request
            return _FakeResponse({"output_text": "pong"})

        with patch.dict(light_gui.os.environ, {"OPENAI_API_KEY": ""}):
            with patch.object(light_gui.urllib.request, "urlopen", side_effect=fake_urlopen):
                status, payload = self._post("/api/ai/test")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("ollama", payload["message"])
        request = captured["request"]
        self.assertTrue(request.full_url.startswith("http://localhost:11434/v1"))
        self.assertNotIn("Authorization", request.headers)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "llama3")

    def test_ai_test_missing_key_reported(self) -> None:
        with patch.dict(light_gui.os.environ, {"OPENAI_API_KEY": ""}):
            status, payload = self._post("/api/ai/test")

        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("OPENAI_API_KEY", payload["message"])


class AiSettingsPrecedenceTest(unittest.TestCase):
    """ai_settings(): config.ai.* beats env, env beats defaults."""

    def test_defaults_when_no_config_and_no_env(self) -> None:
        with patch.dict(light_gui.os.environ, {"LIGHT_AI_MODEL": "", "OPENAI_MODEL": "", "OPENAI_BASE_URL": ""}):
            settings = light_gui.ai_settings({})

        self.assertEqual(settings["provider"], "openai")
        self.assertEqual(settings["base_url"], "https://api.openai.com/v1")
        self.assertEqual(settings["model"], light_gui.DEFAULT_AI_MODEL)
        self.assertEqual(settings["vision_model"], light_gui.DEFAULT_AI_MODEL)
        self.assertEqual(settings["api_key_env"], "OPENAI_API_KEY")

    def test_env_beats_defaults(self) -> None:
        env = {
            "LIGHT_AI_MODEL": "env-model",
            "OPENAI_BASE_URL": "https://env.example/v1",
            "OPENAI_VISION_MODEL": "env-vision",
        }
        with patch.dict(light_gui.os.environ, env):
            settings = light_gui.ai_settings({})

        self.assertEqual(settings["model"], "env-model")
        self.assertEqual(settings["base_url"], "https://env.example/v1")
        self.assertEqual(settings["vision_model"], "env-vision")

    def test_config_beats_env(self) -> None:
        config = {
            "ai": {
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "cfg-model",
                "vision_model": "cfg-vision",
                "api_key_env": "OPENROUTER_API_KEY",
            }
        }
        env = {"LIGHT_AI_MODEL": "env-model", "OPENAI_BASE_URL": "https://env.example/v1"}
        with patch.dict(light_gui.os.environ, env):
            settings = light_gui.ai_settings(config)

        self.assertEqual(settings["provider"], "openrouter")
        self.assertEqual(settings["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(settings["model"], "cfg-model")
        self.assertEqual(settings["vision_model"], "cfg-vision")
        self.assertEqual(settings["api_key_env"], "OPENROUTER_API_KEY")

    def test_api_key_set_flag_uses_configured_env_var(self) -> None:
        config = {"ai": {"api_key_env": "MY_AI_KEY"}}
        with patch.dict(light_gui.os.environ, {"MY_AI_KEY": "secret-value"}):
            settings = light_gui.ai_settings(config)
        self.assertTrue(settings["api_key_set"])
        self.assertNotIn("secret-value", json.dumps(settings))

        with patch.dict(light_gui.os.environ, {"MY_AI_KEY": ""}):
            settings = light_gui.ai_settings(config)
        self.assertFalse(settings["api_key_set"])

    def test_openai_model_env_still_works_without_config_ai(self) -> None:
        with patch.dict(light_gui.os.environ, {"OPENAI_MODEL": "legacy-env-model", "LIGHT_AI_MODEL": ""}):
            self.assertEqual(light_gui.ai_settings({})["model"], "legacy-env-model")
            request = light_gui.build_openai_request("make it warm")
        self.assertEqual(request["model"], "legacy-env-model")

    def test_build_openai_request_defaults_without_config_or_env(self) -> None:
        with patch.object(lightctl, "load_config", return_value={}):
            with patch.dict(light_gui.os.environ, {"LIGHT_AI_MODEL": "", "OPENAI_MODEL": ""}):
                request = light_gui.build_openai_request("make it warm")
        self.assertEqual(request["model"], light_gui.DEFAULT_AI_MODEL)
        self.assertEqual(request["input"][0]["content"], light_gui.system_knowledge_prompt())


class ReloadControllersTest(unittest.TestCase):
    """GuiState.reload_controllers hot-swaps the fleet client."""

    def test_reload_swaps_client_and_dependents(self) -> None:
        client = lightctl.LightClient("http://127.0.0.1", dry_run=True)
        state = light_gui.GuiState(client, dry_run=True)
        self.addCleanup(state.schedule.stop)

        fake_fleet = MagicMock()
        fake_fleet.names.return_value = ["left", "right"]

        with patch.object(light_gui, "_build_fleet_client", return_value=(fake_fleet, None)) as builder:
            names = state.reload_controllers()

        builder.assert_called_once()
        self.assertEqual(names, ["left", "right"])
        self.assertIs(state.client, fake_fleet)
        self.assertIs(state._info_client, fake_fleet)
        self.assertTrue(state.is_fleet)
        self.assertIs(state.mode1.client, fake_fleet)
        self.assertIs(state.schedule.client, fake_fleet)
        self.assertIs(state.mood_session.client, fake_fleet)
        self.assertIsNone(state.fade_timer)
        self.assertIsNone(state.cached_state)
        self.assertIsNone(state.cached_info)
        self.assertEqual(state._offline, {})
        state.schedule.stop()


if __name__ == "__main__":
    unittest.main()
