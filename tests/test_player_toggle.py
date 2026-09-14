"""Tests for the audio player enabled/disabled toggle."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import audio_player
import light_gui
import lightctl


class SettingsFromConfigEnabledTest(unittest.TestCase):
    def test_enabled_passes_through_as_string(self) -> None:
        settings = audio_player.settings_from_config({"audio_player": {"enabled": False}})
        self.assertEqual(settings["enabled"], "false")
        settings = audio_player.settings_from_config({"audio_player": {"enabled": True}})
        self.assertEqual(settings["enabled"], "true")
        settings = audio_player.settings_from_config({"audio_player": {"enabled": "false"}})
        self.assertEqual(settings["enabled"], "false")

    def test_enabled_absent_when_unset(self) -> None:
        self.assertNotIn("enabled", audio_player.settings_from_config({"audio_player": {}}))
        self.assertNotIn("enabled", audio_player.settings_from_config({}))
        self.assertNotIn("enabled", audio_player.settings_from_config(None))


class AudioPlayerEnabledHelperTest(unittest.TestCase):
    def test_defaults_true_when_unset(self) -> None:
        self.assertTrue(light_gui._audio_player_enabled({}))
        self.assertTrue(light_gui._audio_player_enabled({"audio_player": {}}))
        self.assertTrue(light_gui._audio_player_enabled({"audio_player": {"enabled": True}}))

    def test_false_values_disable(self) -> None:
        for value in (False, "false", "False", "0", "no", "off"):
            self.assertFalse(light_gui._audio_player_enabled({"audio_player": {"enabled": value}}))

    def test_public_settings_include_enabled(self) -> None:
        self.assertTrue(light_gui.audio_player_public_settings({})["enabled"])
        self.assertFalse(
            light_gui.audio_player_public_settings({"audio_player": {"enabled": False}})["enabled"]
        )


class PlayerToggleHttpTest(unittest.TestCase):
    """Player API routes answer with a disabled payload when toggled off."""

    DISABLED = {"ok": False, "disabled": True, "message": "Audio player is disabled in settings."}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        config_patch = patch.object(lightctl, "_CONFIG_PATH", self.config_path)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), light_gui.make_handler(MagicStubState()))
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

    def test_get_routes_disabled(self) -> None:
        self._write_config({"audio_player": {"enabled": False}})
        for path in (
            "/api/player",
            "/api/player/search?q=x",
            "/api/player/playlists",
            "/api/player/apple/session",
            "/api/player/apple/config",
        ):
            status, payload = self._get(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(payload, self.DISABLED, path)

    def test_post_routes_disabled(self) -> None:
        self._write_config({"audio_player": {"enabled": False}})
        for path, body in (
            ("/api/player", {"source": "youtube_music", "command": "playPause"}),
            ("/api/player/apple/complete", {"session": "s", "musicUserToken": "t"}),
            ("/api/player/apple/now", {"title": "x"}),
        ):
            status, payload = self._post(path, body)
            self.assertEqual(status, 200, path)
            self.assertEqual(payload, self.DISABLED, path)

    def test_get_player_enabled_by_default(self) -> None:
        self._write_config({})
        # apple_music with no token fails fast without any network access.
        status, payload = self._get("/api/player?source=apple_music")
        self.assertEqual(status, 200)
        self.assertNotIn("disabled", payload)
        self.assertFalse(payload["connected"])

    def _get_text(self, path: str) -> tuple[int, str]:
        with urllib.request.urlopen(f"{self.base}{path}", timeout=5) as response:
            return response.status, response.read().decode("utf-8")

    def test_index_page_reflects_player_toggle(self) -> None:
        self._write_config({"audio_player": {"enabled": False}})
        status, body = self._get_text("/")
        self.assertEqual(status, 200)
        self.assertNotIn("playerLibrary", body)
        self.assertIn("visualizerHero", body)

        self._write_config({})
        status, body = self._get_text("/")
        self.assertEqual(status, 200)
        self.assertIn("playerLibrary", body)

    def test_settings_enabled_roundtrip_preserves_false(self) -> None:
        self._write_config({"audio_player": {"youtube_host": "http://127.0.0.1:9863"}})

        status, payload = self._post("/api/settings", {"audio_player": {"enabled": False}})

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        config = self._read_config()
        self.assertIs(config["audio_player"]["enabled"], False)
        self.assertEqual(config["audio_player"]["youtube_host"], "http://127.0.0.1:9863")

        _, settings_payload = self._get("/api/settings")
        self.assertFalse(settings_payload["settings"]["audio_player"]["enabled"])

        status, _ = self._post("/api/settings", {"audio_player": {"enabled": True}})
        self.assertEqual(status, 200)
        self.assertIs(self._read_config()["audio_player"]["enabled"], True)


class MagicStubState:
    """Minimal GuiState stand-in for the player HTTP handlers."""

    def reload_controllers(self) -> list[str]:
        return []


if __name__ == "__main__":
    unittest.main()
