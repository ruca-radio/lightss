import copy
import io
import unittest
from unittest.mock import patch

import lightctl


class FakeClient:
    def __init__(self):
        self.payloads = []

    def post_state(self, payload):
        self.payloads.append(payload)


class LightCtlTests(unittest.TestCase):
    def test_normalizes_controller_host(self):
        client = lightctl.LightClient("10.27.27.110")
        self.assertEqual(client.state_url, "http://10.27.27.110/json/state")

    def test_exposes_wled_json_endpoint_urls(self):
        client = lightctl.LightClient("10.27.27.110")

        self.assertEqual(client.json_url, "http://10.27.27.110/json")
        self.assertEqual(client.info_url, "http://10.27.27.110/json/info")
        self.assertEqual(client.effects_url, "http://10.27.27.110/json/eff")
        self.assertEqual(client.palettes_url, "http://10.27.27.110/json/pal")
        self.assertEqual(client.nodes_url, "http://10.27.27.110/json/nodes")
        self.assertEqual(client.live_url, "http://10.27.27.110/json/live")
        self.assertEqual(client.config_url, "http://10.27.27.110/json/cfg")
        self.assertEqual(client.fxdata_url, "http://10.27.27.110/json/fxdata")
        self.assertEqual(client.networks_url, "http://10.27.27.110/json/net")

    def test_device_snapshot_combines_current_wled_data(self):
        class SnapshotClient(lightctl.LightClient):
            def get_json(self):
                return {
                    "state": {"on": True, "bri": 77},
                    "info": {"name": "WLED-Gledopto"},
                    "effects": ["Solid", "Rainbow"],
                    "palettes": ["Default", "Party"],
                }

            def get_config(self):
                return {"light": {"nl": {"dur": 60}}}

            def get_fxdata(self):
                return ["", "speed"]

            def get_networks(self):
                return {"networks": []}

        snapshot = SnapshotClient("10.27.27.110").get_device_snapshot()

        self.assertEqual(snapshot["state"]["bri"], 77)
        self.assertEqual(snapshot["info"]["name"], "WLED-Gledopto")
        self.assertEqual(snapshot["effects"], ["Solid", "Rainbow"])
        self.assertEqual(snapshot["config"]["light"]["nl"]["dur"], 60)

    def test_color_payload_uses_rgbw_segment_array(self):
        self.assertEqual(
            lightctl.color_payload(255, 100, 0, 255),
            {"seg": [{"col": [[255, 100, 0, 255]]}]},
        )

    def test_values_are_clamped_to_wled_byte_range(self):
        self.assertEqual(lightctl.brightness_payload(300), {"bri": 255})
        self.assertEqual(lightctl.color_payload(-1, 12, 999, 1), {"seg": [{"col": [[0, 12, 255, 1]]}]})

    def test_reactive_controller_sends_color_on_beat(self):
        fake = FakeClient()
        controller = lightctl.ReactiveMode(
            fake,
            palette=[(255, 0, 0, 0), (0, 0, 255, 0)],
            min_interval=0,
        )

        controller.handle_beat(0.8)
        controller.handle_beat(0.9)

        self.assertEqual(
            fake.payloads,
            [
                {"on": True, "bri": 204, "seg": [{"col": [[255, 0, 0, 0]], "fx": 2, "sx": 168}]},
                {"on": True, "bri": 229, "seg": [{"col": [[0, 0, 255, 0]], "fx": 8, "sx": 179}]},
            ],
        )

    def test_reactive_beat_payload_combines_color_brightness_and_effect(self):
        self.assertEqual(
            lightctl.reactive_beat_payload((0, 50, 255, 0), 212, 9, 190),
            {"on": True, "bri": 212, "seg": [{"col": [[0, 50, 255, 0]], "fx": 9, "sx": 190}]},
        )

    def test_scene_payload_combines_power_brightness_and_color(self):
        self.assertEqual(
            lightctl.scene_payload("night"),
            {"on": True, "bri": 25, "seg": [{"col": [[255, 70, 0, 0]]}]},
        )

    def test_strobe_like_effects_are_rejected(self):
        with self.assertRaises(ValueError):
            lightctl.effect_payload(1)

        with self.assertRaises(ValueError):
            lightctl.effect_payload(23)

        with self.assertRaises(ValueError):
            lightctl.effect_payload(31)

    def test_validate_wled_payload_rejects_out_of_range_segment_effect(self):
        with self.assertRaises(ValueError):
            lightctl.validate_wled_payload({"seg": [{"fx": 300}]})

    def test_validate_wled_payload_accepts_any_valid_effect(self):
        # live WLED devices expose 187-220 effects; the sanitizer must not
        # restrict payloads to the offline SAFE_EFFECTS whitelist
        self.assertEqual(lightctl.validate_wled_payload({"seg": [{"fx": 155}]}),
                         {"seg": [{"fx": 155}]})

    def test_validate_wled_payload_accepts_restart_payload(self):
        self.assertEqual(lightctl.validate_wled_payload(lightctl.restart_payload()), lightctl.restart_payload())

    def test_post_state_validates_payload_before_dry_run(self):
        client = lightctl.LightClient(dry_run=True)

        with self.assertRaises(ValueError):
            client.post_state({"seg": [{"fx": 300}]})

    def test_chase_and_rainbow_effects_are_allowed(self):
        self.assertEqual(lightctl.effect_payload(28, 170), {"seg": [{"fx": 28, "sx": 170}]})
        self.assertEqual(lightctl.effect_payload(30, 180), {"seg": [{"fx": 30, "sx": 180}]})

    def test_effect_payload_accepts_full_safe_effect_parameters(self):
        self.assertEqual(
            lightctl.effect_payload(
                28,
                speed=170,
                intensity=190,
                palette=6,
                c1=10,
                c2=20,
                c3=30,
                o1=1,
                o2=0,
                o3=1,
                transition_ms=500,
            ),
            {
                "seg": [
                    {
                        "fx": 28,
                        "sx": 170,
                        "ix": 190,
                        "pal": 6,
                        "c1": 10,
                        "c2": 20,
                        "c3": 30,
                        "o1": True,
                        "o2": False,
                        "o3": True,
                    }
                ],
                "transition": 5,
            },
        )

    def test_effect_payload_clamps_c3_to_wled_range_and_coerces_options(self):
        # WLED's c3 range is 0-31; o1/o2/o3 are boolean option flags.
        self.assertEqual(
            lightctl.effect_payload(28, c3=40, o1=1, o2=0, o3="yes"),
            {"seg": [{"fx": 28, "sx": 128, "c3": 31, "o1": True, "o2": False, "o3": True}]},
        )
        self.assertEqual(
            lightctl.effect_payload(28, c3=-5),
            {"seg": [{"fx": 28, "sx": 128, "c3": 0}]},
        )

    def test_resolves_default_input_samplerate_from_sounddevice(self):
        class FakeSoundDevice:
            @staticmethod
            def query_devices(device=None, kind=None):
                return {"default_samplerate": 48000.0}

        self.assertEqual(lightctl.resolve_input_samplerate(FakeSoundDevice, None, None), 48000)

    def test_explicit_samplerate_wins(self):
        class FakeSoundDevice:
            @staticmethod
            def query_devices(device=None, kind=None):
                raise AssertionError("should not query when explicit sample rate is provided")

        self.assertEqual(lightctl.resolve_input_samplerate(FakeSoundDevice, None, 22050), 22050)


class SegIdBuilderTests(unittest.TestCase):
    """Segment builders gain an optional seg_id that stamps "id" into the
    emitted seg entry; omitting it must stay byte-identical to before."""

    def test_color_payload_with_seg_id(self):
        self.assertEqual(
            lightctl.color_payload(255, 100, 0, 255, seg_id=1),
            {"seg": [{"id": 1, "col": [[255, 100, 0, 255]]}]},
        )

    def test_color_payload_without_seg_id_unchanged(self):
        self.assertEqual(
            lightctl.color_payload(255, 100, 0, 255),
            {"seg": [{"col": [[255, 100, 0, 255]]}]},
        )

    def test_effect_payload_with_seg_id(self):
        self.assertEqual(
            lightctl.effect_payload(28, 170, seg_id=1),
            {"seg": [{"id": 1, "fx": 28, "sx": 170}]},
        )

    def test_effect_payload_without_seg_id_unchanged(self):
        self.assertEqual(
            lightctl.effect_payload(28, 170),
            {"seg": [{"fx": 28, "sx": 170}]},
        )

    def test_palette_payload_with_and_without_seg_id(self):
        self.assertEqual(lightctl.palette_payload(6), {"seg": [{"pal": 6}]})
        self.assertEqual(
            lightctl.palette_payload(6, seg_id=0),
            {"seg": [{"id": 0, "pal": 6}]},
        )


class SegmentPayloadTests(unittest.TestCase):
    def test_builds_multi_segment_array(self):
        payload = lightctl.segment_payload([
            {"id": 0, "fx": 9},
            {"id": 1, "fx": 28, "sx": 170},
        ])
        self.assertEqual(
            payload,
            {"seg": [{"id": 0, "fx": 9}, {"id": 1, "fx": 28, "sx": 170}]},
        )

    def test_validates_effect_range_per_segment(self):
        with self.assertRaises(ValueError):
            lightctl.segment_payload([{"id": 0, "fx": 300}])

    def test_rejects_non_list(self):
        with self.assertRaises(ValueError):
            lightctl.segment_payload({"id": 0, "fx": 9})


class ValidateEffectTests(unittest.TestCase):
    def test_offline_fallback_uses_safe_effects(self):
        self.assertEqual(lightctl.validate_effect(9), 9)
        self.assertEqual(lightctl.validate_effect(28), 28)

    def test_offline_fallback_rejects_unsafe_effect(self):
        with self.assertRaises(ValueError):
            lightctl.validate_effect(1)

    def test_live_set_accepts_any_known_id(self):
        live = set(range(220))  # WLED 16.0.1 has 220 effects
        self.assertEqual(lightctl.validate_effect(200, allowed=live), 200)

    def test_live_set_rejects_unknown_id(self):
        live = set(range(187))  # WLED 0.15.4 has only 187 effects
        with self.assertRaises(ValueError):
            lightctl.validate_effect(200, allowed=live)

    def test_blocked_effects_rejected_even_when_live_set_allows(self):
        with patch.object(lightctl, "BLOCKED_EFFECTS", {9}):
            with self.assertRaises(ValueError):
                lightctl.validate_effect(9, allowed={9})
            with self.assertRaises(ValueError):
                lightctl.validate_effect(9)

    def test_blocked_effects_defaults_to_empty_set(self):
        self.assertEqual(lightctl.BLOCKED_EFFECTS, set())


class PlaylistPayloadTests(unittest.TestCase):
    def test_playlist_payload_sets_pl(self):
        self.assertEqual(lightctl.playlist_payload(3), {"pl": 3})


class DefaultHostTests(unittest.TestCase):
    def test_default_host_remains_right_controller_alias(self):
        # Back-compat: DEFAULT_HOST is the first configured controller's host.
        self.assertEqual(lightctl.DEFAULT_HOST, "http://10.27.27.110")


class RequestRetryTests(unittest.TestCase):
    """WLED's ESP-based HTTP server can reset the connection mid-response
    (e.g. while rebooting after a restart command). Regression coverage for
    a bug where that raw socket error bypassed retry entirely and wasn't
    wrapped into the RuntimeError that CLI/GUI/MCP restart handlers rely on
    to distinguish "device unreachable" from other failures."""

    def setUp(self):
        self.client = lightctl.LightClient("10.27.27.110", timeout=0.01)
        self.sleep_patch = patch("lightctl.time.sleep")
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()

    def test_connection_reset_mid_response_is_retried_then_wrapped(self):
        with patch("lightctl.urllib.request.urlopen", side_effect=ConnectionResetError("reset by peer")) as mock_urlopen:
            with self.assertRaises(RuntimeError):
                self.client.get_state()
        self.assertEqual(mock_urlopen.call_count, 3)

    def test_exhausted_url_error_retries_raise_runtime_error(self):
        with patch(
            "lightctl.urllib.request.urlopen",
            side_effect=lightctl.urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.get_state()

    def test_success_after_transient_reset_returns_response(self):
        good_response = io.BytesIO(b'{"on": true}')
        with patch(
            "lightctl.urllib.request.urlopen",
            side_effect=[ConnectionResetError("reset by peer"), good_response],
        ):
            with self.client._request_with_retry(
                lightctl.urllib.request.Request(self.client.state_url)
            ) as response:
                self.assertEqual(response.read(), b'{"on": true}')


class ScenePayloadMutationTests(unittest.TestCase):
    """Regression: scene_payload() must not hand out the shared _builtin_scenes
    dicts - _with_seg_id() mutates nested seg entries in place, which would
    permanently stamp a segment id into the module-global scene."""

    def test_builtin_scene_is_not_mutated_by_seg_id_stamping(self):
        before = copy.deepcopy(lightctl._builtin_scenes["warm"])

        payload = lightctl._with_seg_id(lightctl.scene_payload("warm"), 3)

        self.assertEqual(payload["seg"][0]["id"], 3)
        self.assertEqual(lightctl._builtin_scenes["warm"], before)

    def test_scene_payload_with_transition_does_not_mutate_builtin(self):
        before = copy.deepcopy(lightctl._builtin_scenes)

        payload = lightctl.scene_payload("night", transition_ms=500)

        self.assertEqual(payload["transition"], 5)
        self.assertEqual(lightctl._builtin_scenes, before)


class CctPayloadTests(unittest.TestCase):
    def test_cct_payload_sets_cct(self):
        self.assertEqual(lightctl.cct_payload(127), {"seg": [{"cct": 127}]})

    def test_cct_payload_clamps_to_byte_range(self):
        self.assertEqual(lightctl.cct_payload(300), {"seg": [{"cct": 255}]})
        self.assertEqual(lightctl.cct_payload(-5), {"seg": [{"cct": 0}]})

    def test_cct_payload_with_seg_id(self):
        self.assertEqual(
            lightctl.cct_payload(127, seg_id=2),
            {"seg": [{"cct": 127, "id": 2}]},
        )

    def test_cct_payload_with_transition(self):
        self.assertEqual(
            lightctl.cct_payload(200, transition_ms=500),
            {"seg": [{"cct": 200}], "transition": 5},
        )


class InvalidJsonResponseTests(unittest.TestCase):
    """A truncated/empty 200 body must surface as RuntimeError, not
    json.JSONDecodeError, so callers only handle one controller-failure type."""

    def setUp(self):
        self.client = lightctl.LightClient("10.27.27.110", timeout=0.01)

    def test_get_state_wraps_invalid_json_in_runtime_error(self):
        with patch("lightctl.urllib.request.urlopen", return_value=io.BytesIO(b"not json")):
            with self.assertRaises(RuntimeError):
                self.client.get_state()

    def test_get_info_wraps_empty_body_in_runtime_error(self):
        with patch("lightctl.urllib.request.urlopen", return_value=io.BytesIO(b"")):
            with self.assertRaises(RuntimeError):
                self.client.get_info()


class HttpErrorCloseTests(unittest.TestCase):
    """HTTPError is also a response object; retried/re-raised error responses
    must be closed so their socket fd is released."""

    def setUp(self):
        self.client = lightctl.LightClient("10.27.27.110", timeout=0.01)
        self.sleep_patch = patch("lightctl.time.sleep")
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()

    def _http_error(self, code):
        return lightctl.urllib.error.HTTPError(
            self.client.state_url, code, "err", hdrs=None, fp=io.BytesIO(b"")
        )

    def test_retried_http_error_response_is_closed(self):
        error = self._http_error(500)
        with patch(
            "lightctl.urllib.request.urlopen",
            side_effect=[error, io.BytesIO(b'{"on": true}')],
        ):
            with self.client._request_with_retry(
                lightctl.urllib.request.Request(self.client.state_url)
            ):
                pass
        self.assertTrue(error.fp.closed)

    def test_reraised_http_error_response_is_closed(self):
        error = self._http_error(404)
        with patch("lightctl.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(lightctl.urllib.error.HTTPError):
                self.client.get_state()
        self.assertTrue(error.fp.closed)

    def test_exhausted_http_error_responses_are_all_closed(self):
        errors = [self._http_error(500) for _ in range(3)]
        with patch("lightctl.urllib.request.urlopen", side_effect=errors):
            with self.assertRaises(RuntimeError):
                self.client.get_state()
        self.assertTrue(all(error.fp.closed for error in errors))


class FadeTimerSleepTests(unittest.TestCase):
    def test_no_sleep_after_final_step(self):
        client = FakeClient()
        timer = lightctl.FadeTimer(client, 1, start_brightness=120)
        with patch("lightctl.time.sleep") as sleep_mock:
            timer._run()
        # 6 steps -> 7 brightness updates (i=0..6); sleep only between steps.
        self.assertEqual(sleep_mock.call_count, 6)
        self.assertEqual(len(client.payloads), 8)  # 7 brightness + final off


class SunriseSimulatorSleepTests(unittest.TestCase):
    def test_no_sleep_after_final_step(self):
        client = FakeClient()
        sim = lightctl.SunriseSimulator(client, duration_minutes=1)
        with patch("lightctl.time.sleep") as sleep_mock:
            sim._run()
        self.assertEqual(sleep_mock.call_count, 6)
        self.assertEqual(len(client.payloads), 7)


class CycleThreadIntervalTests(unittest.TestCase):
    def test_fractional_interval_is_fully_honored(self):
        client = FakeClient()
        cycler = lightctl.CycleThread(client, items=["warm"], interval_seconds=5.5)
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if sum(sleeps) >= cycler.interval_seconds:
                cycler._stop.set()

        with patch("lightctl.time.sleep", side_effect=fake_sleep):
            cycler._run()
        self.assertAlmostEqual(sum(sleeps), 5.5)


class ScheduleTests(unittest.TestCase):
    def test_add_schedule_validates_hh_mm_format(self):
        for bad in ("7pm", "25:00", "12:99", "12-30", "1200"):
            with self.assertRaises(ValueError):
                lightctl.add_schedule(bad, "on")

    def test_remove_schedule_reports_whether_an_entry_was_removed(self):
        entries = [{"time": "08:00", "action": "on", "data": {}}]
        with (
            patch.object(lightctl, "_load_schedule", return_value=list(entries)),
            patch.object(lightctl, "_save_schedule") as save_mock,
        ):
            self.assertTrue(lightctl.remove_schedule(0))
            self.assertFalse(lightctl.remove_schedule(5))
        self.assertEqual(save_mock.call_count, 1)


class ZoneBoundsTests(unittest.TestCase):
    def test_zone_name_path_ignores_stray_start_stop_keys(self):
        # start/stop must not silently override the computed zone bounds.
        payload = lightctl.zone_payload([{"zone": "top half", "start": 0, "stop": 5}])
        self.assertEqual(payload["seg"][0]["start"], 20)
        self.assertEqual(payload["seg"][0]["stop"], 40)

    def test_zone_bounds_rejects_empty_range(self):
        # Per WLED, stop <= start deletes the segment; never emit empty bounds.
        with self.assertRaises(ValueError):
            lightctl.zone_bounds("bottom quarter", length=1)
        with self.assertRaises(ValueError):
            lightctl.zone_bounds("top quarter", length=2)


if __name__ == "__main__":
    unittest.main()
