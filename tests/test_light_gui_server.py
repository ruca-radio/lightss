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
        self.assertEqual(light_gui.payload_for_ai_action({"action": "udp_sync"}), {"udpn": {}})
        self.assertEqual(
            light_gui.payload_for_ai_action({"action": "udp_sync", "send": True}),
            {"udpn": {"send": True}},
        )
        self.assertEqual(
            light_gui.payload_for_ai_action({"action": "udp_sync", "send": None, "receive": False}),
            {"udpn": {"recv": False}},
        )

    def test_ai_nightlight_omits_absent_fields(self) -> None:
        self.assertEqual(light_gui.payload_for_ai_action({"action": "nightlight"}), {"nl": {}})
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


if __name__ == "__main__":
    unittest.main()
