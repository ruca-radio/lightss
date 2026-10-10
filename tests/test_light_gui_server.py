"""Tests for light_gui server internals: AI payloads, job pruning, scheduler, reload."""

from __future__ import annotations

import http.client
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import light_gui
import lightctl


class AiColorPayloadTest(unittest.TestCase):
    """payload_for_ai_action('color') must not send empty color slots."""

    def test_color_without_secondary_slots_sends_only_primary(self) -> None:
        payload = light_gui.payload_for_ai_action({"action": "color", "red": 10, "green": 20, "blue": 30})

        col = payload["seg"][0]["col"]
        self.assertEqual(col, [[10, 20, 30, 0]])
        for slot in col:
            self.assertTrue(slot, "WLED treats present-but-empty color arrays as [0,0,0,0]")

    def test_color_with_secondary_slot_sends_two_slots(self) -> None:
        payload = light_gui.payload_for_ai_action(
            {"action": "color", "red": 10, "green": 20, "blue": 30, "red2": 1, "green2": 2, "blue2": 3}
        )

        self.assertEqual(payload["seg"][0]["col"], [[10, 20, 30, 0], [1, 2, 3, 0]])


class SparsePayloadTest(unittest.TestCase):
    """udp_sync/nightlight must not clobber unspecified fields with False/defaults."""

    def test_ai_udp_sync_omits_absent_fields(self) -> None:
        with self.assertRaises(ValueError):
            light_gui.payload_for_ai_action({"action": "udp_sync"})
        self.assertEqual(
            light_gui.payload_for_ai_action({"action": "udp_sync", "send": True}),
            {"udpn": {"send": True}},
        )
        self.assertEqual(
            light_gui.payload_for_ai_action({"action": "udp_sync", "send": None, "receive": False}),
            {"udpn": {"recv": False}},
        )

    def test_ai_nightlight_omits_absent_fields(self) -> None:
        with self.assertRaises(ValueError):
            light_gui.payload_for_ai_action({"action": "nightlight"})
        self.assertEqual(
            light_gui.payload_for_ai_action({"action": "nightlight", "enabled": True, "minutes": 30}),
            {"nl": {"on": True, "dur": 30}},
        )

    def test_action_udp_sync_omits_absent_fields(self) -> None:
        self.assertEqual(light_gui.payload_for_action("udp_sync", {}), {"udpn": {}})
        self.assertEqual(
            light_gui.payload_for_action("udp_sync", {"receive": True}),
            {"udpn": {"recv": True}},
        )

    def test_action_nightlight_omits_absent_fields(self) -> None:
        self.assertEqual(light_gui.payload_for_action("nightlight", {}), {"nl": {}})
        self.assertEqual(
            light_gui.payload_for_action("nightlight", {"enabled": False, "target_brightness": 50}),
            {"nl": {"on": False, "tbri": 50}},
        )


class AiJobManagerPruneTest(unittest.TestCase):
    def test_finished_jobs_are_pruned_to_max_jobs(self) -> None:
        manager = light_gui.AiJobManager(max_workers=1)
        self.addCleanup(manager.shutdown)
        with patch.object(light_gui.AiJobManager, "MAX_JOBS", 5):
            for _ in range(9):
                job_id = manager.submit(lambda: {"ok": True})
                manager.wait(job_id, timeout=5)

            self.assertLessEqual(len(manager._jobs), 5)
            # Newest jobs survive pruning.
            self.assertEqual(manager.get(job_id)["status"], "complete")


class ScheduleExecutorTest(unittest.TestCase):
    def _run_briefly(self, executor: light_gui.ScheduleExecutor) -> None:
        executor.start()
        try:
            time.sleep(0.3)
        finally:
            executor.stop()
            executor._thread.join(timeout=5)
        self.assertFalse(executor._thread.is_alive(), "executor must stop promptly via the stop event")

    def test_unsupported_action_is_not_marked_executed(self) -> None:
        entry = {"time": time.strftime("%H:%M"), "action": "bogus", "data": {}}
        client = MagicMock()
        executor = light_gui.ScheduleExecutor(client)
        with (
            patch.object(lightctl, "list_schedule", return_value=[entry]),
            patch.object(lightctl, "_save_schedule") as save,
        ):
            self._run_briefly(executor)

        client.post_state.assert_not_called()
        save.assert_not_called()
        self.assertNotIn("last_run_date", entry)

    def test_supported_action_marks_last_run_date(self) -> None:
        entry = {"time": time.strftime("%H:%M"), "action": "on", "data": {}}
        client = MagicMock()
        executor = light_gui.ScheduleExecutor(client)
        with (
            patch.object(lightctl, "list_schedule", return_value=[entry]),
            patch.object(lightctl, "_save_schedule") as save,
        ):
            self._run_briefly(executor)

        client.post_state.assert_called()
        save.assert_called()
        self.assertEqual(entry["last_run_date"], time.strftime("%Y-%m-%d"))


class ReloadControllersThreadsTest(unittest.TestCase):
    def test_reload_stops_and_clears_cycle_and_sunrise(self) -> None:
        client = lightctl.LightClient("http://127.0.0.1", dry_run=True)
        state = light_gui.GuiState(client, dry_run=True)
        self.addCleanup(state.schedule.stop)

        state._cycle = MagicMock()
        state._sunrise = MagicMock()
        fake_fleet = MagicMock()
        fake_fleet.names.return_value = ["left"]

        with patch.object(light_gui, "_build_fleet_client", return_value=(fake_fleet, None)):
            state.reload_controllers()

        self.assertIsNone(state._cycle)
        self.assertIsNone(state._sunrise)
        state.schedule.stop()

    def test_reload_calls_stop_on_cycle_and_sunrise(self) -> None:
        client = lightctl.LightClient("http://127.0.0.1", dry_run=True)
        state = light_gui.GuiState(client, dry_run=True)
        self.addCleanup(state.schedule.stop)

        cycle = MagicMock()
        sunrise = MagicMock()
        state._cycle = cycle
        state._sunrise = sunrise
        fake_fleet = MagicMock()
        fake_fleet.names.return_value = ["left"]

        with patch.object(light_gui, "_build_fleet_client", return_value=(fake_fleet, None)):
            state.reload_controllers()

        cycle.stop.assert_called_once()
        sunrise.stop.assert_called_once()
        state.schedule.stop()


class SmartSuggestionsTest(unittest.TestCase):
    def test_null_brightness_does_not_crash(self) -> None:
        suggestions = light_gui.smart_suggestions({"on": True, "bri": None})

        self.assertIsInstance(suggestions, list)
        self.assertTrue(suggestions)


class FetchThrottledTest(unittest.TestCase):
    def _bare_state(self) -> light_gui.GuiState:
        state = light_gui.GuiState.__new__(light_gui.GuiState)
        state._offline = {}
        state._offline_checked_at = {}
        state.state_lock = threading.Lock()
        state.cached_state = None
        return state

    def test_reraises_original_exception_without_cache(self) -> None:
        state = self._bare_state()

        def fail() -> dict:
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            state._fetch_throttled("cached_state", fail)

    def test_throttled_offline_raises_runtime_error(self) -> None:
        state = self._bare_state()
        state._offline["cached_state"] = True
        state._offline_checked_at["cached_state"] = time.time()

        with self.assertRaises(RuntimeError):
            state._fetch_throttled("cached_state", lambda: {})


class ApiPostPathsTest(unittest.TestCase):
    def test_tv_trivia_is_get_only(self) -> None:
        self.assertIn("/api/tv-trivia", light_gui.API_GET_PATHS)
        self.assertNotIn("/api/tv-trivia", light_gui.API_POST_PATHS)


class ServerHttpTest(unittest.TestCase):
    """End-to-end checks against a real ThreadingHTTPServer."""

    def setUp(self) -> None:
        self.state = MagicMock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), light_gui.make_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_address[1]

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_ai_vision_rejects_oversized_body_with_413(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/ai_vision")
        conn.putheader("Content-Length", str(11 * 1024 * 1024))
        conn.endheaders()
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        conn.close()

        self.assertEqual(response.status, 413)
        self.assertIn("too large", body["error"])

    def test_tv_trivia_post_is_404(self) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/tv-trivia",
            data=b"{}",
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)

        self.assertEqual(ctx.exception.code, 404)

    def test_restart_action_swallows_http_error(self) -> None:
        http_error = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
        with patch.object(light_gui, "_post_state", side_effect=http_error):
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/action",
                data=json.dumps({"action": "restart"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("Restart command sent", payload["message"])

    def test_look_feedback_and_memory_summary_actions(self) -> None:
        with (
            patch.object(light_gui.look_memory, "add_feedback", return_value="look-123") as add_feedback,
            patch.object(light_gui.look_memory, "memory_summary", return_value="Look memory summary text") as summary,
        ):
            feedback_request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/action",
                data=json.dumps({"action": "look_feedback", "score": 1, "notes": "worked", "tags": ["warm"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(feedback_request, timeout=5) as response:
                feedback_payload = json.loads(response.read().decode("utf-8"))

            summary_request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/action",
                data=json.dumps({"action": "look_memory_summary", "limit": 5}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(summary_request, timeout=5) as response:
                summary_payload = json.loads(response.read().decode("utf-8"))

        add_feedback.assert_called_once()
        self.assertTrue(feedback_payload["ok"])
        self.assertIn("Recorded feedback", feedback_payload["message"])
        summary.assert_called_once_with(limit=5)
        self.assertTrue(summary_payload["ok"])
        self.assertEqual(summary_payload["message"], "Look memory summary text")

    def test_post_recognize_without_audio_uses_ambient_mic(self) -> None:
        song = {"title": "Poker Face", "artist": "Lady Gaga", "album": "The Fame", "genre": "Pop"}
        with (
            patch.object(light_gui, "get_now_playing", return_value=None),
            patch.object(light_gui.music_recognizer, "can_identify_song", return_value=True),
            patch.object(light_gui.music_recognizer, "is_available", return_value=True),
            patch.object(light_gui.music_recognizer, "recognize_ambient_sync", return_value=song) as ambient,
        ):
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/recognize",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

        ambient.assert_called_once()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["now_playing"]["title"], "Poker Face")
        self.assertEqual(payload["now_playing"]["artist"], "Lady Gaga")

    def test_get_recognize_uses_ambient_mic(self) -> None:
        song = {"title": "Song", "artist": "Artist"}
        with (
            patch.object(light_gui, "get_now_playing", return_value=None),
            patch.object(light_gui.music_recognizer, "can_identify_song", return_value=True),
            patch.object(light_gui.music_recognizer, "is_available", return_value=True),
            patch.object(light_gui.music_recognizer, "recognize_ambient_sync", return_value=song),
        ):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/recognize", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["now_playing"]["artist"], "Artist")


class ShazamFallbackTest(unittest.TestCase):
    def test_shazam_fallback_uses_ambient_mic(self) -> None:
        with (
            patch.object(light_gui, "get_now_playing", return_value=None),
            patch.object(light_gui.music_recognizer, "can_identify_song", return_value=True),
            patch.object(light_gui.music_recognizer, "is_available", return_value=True),
            patch.object(
                light_gui.music_recognizer,
                "recognize_ambient_sync",
                return_value={"title": "Live Song", "artist": "Gaga", "album": "Coachella"},
            ) as ambient,
        ):
            result = light_gui.get_now_playing_with_shazam_fallback(use_shazam=True)
        ambient.assert_called_once()
        assert result is not None
        self.assertEqual(result["title"], "Live Song")
        self.assertEqual(result["source"], "shazam")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Live effect catalog for the manual pickers
# ---------------------------------------------------------------------------

class _EffectsClient:
    def __init__(self, effects):
        self._effects = effects

    def get_effects(self):
        return list(self._effects)


class _EffectsFleet:
    def __init__(self, per_controller):
        self.clients = {
            name: _EffectsClient(effects) for name, effects in per_controller.items()
        }


class LiveEffectCatalogTest(unittest.TestCase):
    def setUp(self):
        light_gui.invalidate_effect_catalog()
        self.addCleanup(light_gui.invalidate_effect_catalog)

    def test_keeps_only_ids_every_controller_names_identically(self):
        catalog = light_gui.live_effect_catalog(
            _EffectsFleet(
                {
                    "left": ["Solid", "Blink", "DJ Light"],
                    "right": ["Solid", "Blink", "Something else"],
                }
            )
        )
        # id 2 differs between controllers, so one dropdown index cannot mean
        # the same effect everywhere and it is left out.
        self.assertEqual(
            catalog, [{"id": 0, "name": "Solid"}, {"id": 1, "name": "Blink"}]
        )

    def test_includes_effects_that_used_to_be_prohibited(self):
        catalog = light_gui.live_effect_catalog(
            _EffectsFleet({"left": ["Solid", "Blink", "Strobe", "Fireworks 1D"]})
        )
        self.assertEqual([e["name"] for e in catalog], ["Solid", "Blink", "Strobe", "Fireworks 1D"])

    def test_falls_back_to_the_curated_list_when_no_controller_answers(self):
        class Dead:
            clients = {"left": object()}  # no get_effects -> unreadable

        catalog = light_gui.live_effect_catalog(Dead())
        self.assertEqual([e["id"] for e in catalog], sorted(lightctl.SAFE_EFFECTS))

    def test_catalog_is_cached_and_invalidated(self):
        fleet_ = _EffectsFleet({"left": ["Solid", "Blink"]})
        first = light_gui.effect_catalog_for(fleet_)
        self.assertEqual([e["id"] for e in first], [0, 1])
        # A different fleet is ignored while the cache is warm...
        second = light_gui.effect_catalog_for(_EffectsFleet({"left": ["Only"]}))
        self.assertEqual(second, first)
        # ...and picked up once it is dropped.
        light_gui.invalidate_effect_catalog()
        third = light_gui.effect_catalog_for(_EffectsFleet({"left": ["Only"]}))
        self.assertEqual([e["name"] for e in third], ["Only"])


class EffectsEndpointTest(unittest.TestCase):
    """GET /api/effects feeds the manual pickers."""

    def setUp(self):
        light_gui.invalidate_effect_catalog()
        self.addCleanup(light_gui.invalidate_effect_catalog)
        self.state = MagicMock()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), light_gui.make_handler(self.state)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_address[1]

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()

    def test_effects_endpoint_returns_picker_entries(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/effects", timeout=5
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["effects"])
        for entry in payload["effects"]:
            self.assertIn("id", entry)
            self.assertIn("name", entry)


class ManualEffectValidationTest(unittest.TestCase):
    """The manual picker and the manual action path must agree on the catalog."""

    def setUp(self):
        light_gui.invalidate_effect_catalog()
        self.addCleanup(light_gui.invalidate_effect_catalog)

    def test_live_catalog_unlocks_manual_effect_actions(self):
        fleet_ = _EffectsFleet({"left": ["Solid", "Blink", "Strobe", "DJ Light"]})
        allowed, names = light_gui.manual_effect_validation(fleet_)
        self.assertEqual(allowed, {0, 1, 2, 3})
        self.assertEqual(names[3], "DJ Light")
        payload = light_gui.payload_for_action(
            "fx", {"effect": 1, "speed": 200}, allowed_effects=allowed, effect_names=names
        )
        self.assertEqual(payload["seg"][0]["fx"], 1)

    def test_manual_validation_names_are_indexed_by_effect_id(self):
        # The offline fallback uses sparse ids; names must stay aligned with them.
        class Dead:
            clients = {"left": object()}

        allowed, names = light_gui.manual_effect_validation(Dead())
        self.assertEqual(allowed, set(lightctl.SAFE_EFFECTS))
        for effect_id in allowed:
            self.assertEqual(names[effect_id], lightctl.SAFE_EFFECTS[effect_id])

    def test_ids_absent_from_the_live_catalog_are_still_rejected(self):
        fleet_ = _EffectsFleet({"left": ["Solid", "Blink"]})
        allowed, names = light_gui.manual_effect_validation(fleet_)
        with self.assertRaises(ValueError):
            light_gui.payload_for_action(
                "fx", {"effect": 9}, allowed_effects=allowed, effect_names=names
            )


class ManualEffectActionHttpTest(unittest.TestCase):
    """POST /api/action with action=fx uses the live catalog."""

    class _State:
        def __init__(self, client):
            self.client = client
            self.target = "all"

        def invalidate_caches(self):
            return None

    def setUp(self):
        light_gui.invalidate_effect_catalog()
        self.addCleanup(light_gui.invalidate_effect_catalog)
        state = self._State(_EffectsFleet({"left": ["Solid", "Blink", "Strobe"]}))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), light_gui.make_handler(state))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_address[1]

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()

    def _post(self, body):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/action",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_action_fx_accepts_a_live_effect(self):
        with patch.object(light_gui, "_post_state") as post:
            status, payload = self._post({"action": "fx", "effect": 1, "speed": 200})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(post.call_args[0][1]["seg"][0]["fx"], 1)

    def test_action_fx_rejects_an_unknown_effect(self):
        with patch.object(light_gui, "_post_state"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._post({"action": "fx", "effect": 250, "speed": 200})
        self.assertEqual(ctx.exception.code, 400)


class _SelfConfigureClient:
    """Minimal WLED client for the self-configure endpoints."""

    def __init__(self, host, buses, segments):
        self.host = host
        self._buses = buses
        self._segments = segments
        self.posts = []

    def get_info(self):
        return {"name": "WLED-Gledopto", "ver": "16.0.1",
                "leds": {"count": max((s["stop"] for s in self._segments), default=0), "maxseg": 32}}

    def get_state(self):
        return {"on": True, "bri": 128, "seg": list(self._segments)}

    def get_config(self):
        return {"hw": {"led": {"ins": list(self._buses)}}}

    def post_state(self, payload):
        self.posts.append(json.loads(json.dumps(payload)))
        return {"success": True}


class SelfConfigureEndpointsTest(unittest.TestCase):
    """The webcam self-configure flow routes light -> finish and fails cleanly."""

    def setUp(self):
        self.state = MagicMock()
        import fleet as fleet_mod

        buses = [{"pin": [16], "start": 0, "len": 34, "order": 2},
                 {"pin": [2], "start": 34, "len": 48, "order": 2}]
        self.wall = fleet_mod.LightFleet(
            {
                "left": _SelfConfigureClient("http://10.27.27.110", buses,
                                             [{"id": 0, "start": 0, "stop": 34, "on": True},
                                              {"id": 1, "start": 34, "stop": 82, "on": True}]),
                "right": _SelfConfigureClient("http://10.27.27.112", buses,
                                              [{"id": 0, "start": 0, "stop": 34, "on": True},
                                               {"id": 1, "start": 34, "stop": 82, "on": True}]),
            },
            [
                fleet_mod.ControllerConfig("left", "http://10.27.27.110", {
                    0: fleet_mod.SegmentConfig("far-left", gpio=16, pixels=34, start=0, stop=34),
                    1: fleet_mod.SegmentConfig("middle-left", gpio=2, pixels=48, start=34, stop=82),
                }),
                fleet_mod.ControllerConfig("right", "http://10.27.27.112", {
                    0: fleet_mod.SegmentConfig("middle-right", gpio=16, pixels=47, start=0, stop=47),
                    1: fleet_mod.SegmentConfig("far-right", gpio=2, pixels=40, start=47, stop=87),
                }),
            ],
            installation=fleet_mod.InstallationConfig(),
        )
        self.state.client = self.wall
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), light_gui.make_handler(self.state)
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.port = self.server.server_address[1]

    def _post(self, path, body):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_light_returns_a_token_and_colors(self):
        status, payload = self._post("/api/calibration/self-configure/light", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["token"])
        self.assertEqual(payload["colors"], ["red", "green", "blue", "yellow"])

    def test_finish_rejects_an_unknown_token(self):
        status, payload = self._post(
            "/api/calibration/self-configure/finish", {"token": "nope", "image": "aW1n"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("No active self-configure probe", payload["error"])
