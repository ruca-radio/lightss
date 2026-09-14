from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest

import fleet
from calibration_service import CalibrationService


INFO = {"name": "WLED", "ver": "0.16.0", "leds": {"count": 10, "maxseg": 16}}
CFG = {"hw": {"led": {"ins": [{"pin": [16], "start": 0, "len": 10, "order": 2, "type": 22}]}}}
STATE = {"on": True, "bri": 123, "seg": [{"id": 0, "start": 0, "stop": 10, "on": True, "bri": 99, "fx": 9}]}


class FakeClient:
    def __init__(self, host: str, *, fail: bool = False):
        self.host = host
        self.info = copy.deepcopy(INFO)
        self.config = copy.deepcopy(CFG)
        self.state = copy.deepcopy(STATE)
        self.fail = fail
        self.posts: list[dict] = []

    def get_info(self):
        if self.fail:
            raise OSError("offline")
        return copy.deepcopy(self.info)

    def get_config(self):
        return copy.deepcopy(self.config)

    def get_state(self):
        return copy.deepcopy(self.state)

    def post_state(self, payload):
        self.posts.append(copy.deepcopy(payload))


def make_fleet(*, right_offline: bool = False):
    controllers = [
        fleet.ControllerConfig("left", "http://left", {0: fleet.SegmentConfig("far-left")}),
        fleet.ControllerConfig("right", "http://right", {0: fleet.SegmentConfig("far-right")}),
    ]
    clients = {
        "left": FakeClient("http://left"),
        "right": FakeClient("http://right", fail=right_offline),
    }
    return fleet.LightFleet(clients, controllers, fleet.InstallationConfig(wall_order=["far-left", "far-right"]))


class CalibrationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "config.json")
        self.original = {
            "unknown_root": {"keep": True},
            "installation": {"wall_order": ["far-left", "far-right"], "unknown_installation": 7},
            "controllers": [
                {"name": "left", "host": "http://left", "vendor_note": "keep-controller", "segments": {"0": {"channel": "far-left", "physical_label": "keep-segment"}}},
                {"name": "right", "host": "http://right", "segments": {"0": {"channel": "far-right"}}},
            ],
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.original, handle)

    def test_scan_stages_complete_json_ready_proposal_without_writes(self):
        light_fleet = make_fleet()
        result = CalibrationService(light_fleet, self.path).scan()
        self.assertTrue(result["ok"])
        self.assertTrue(result["can_apply"])
        self.assertIsInstance(result["token"], str)
        self.assertEqual(set(result["probes"]), {"left", "right"})
        self.assertEqual(result["proposed"]["controllers"][0]["segments"]["0"]["pixels"], 10)
        self.assertEqual(light_fleet.clients["left"].posts, [])
        json.dumps(result)

    def test_scan_rejects_partial_offline_fleet_and_issues_no_token(self):
        result = CalibrationService(make_fleet(right_offline=True), self.path).scan()
        self.assertFalse(result["ok"])
        self.assertFalse(result["can_apply"])
        self.assertIsNone(result["token"])
        self.assertIn("error", result["probes"]["right"])

    def test_observe_is_read_only_and_never_evicts_reviewed_apply_token(self):
        service = CalibrationService(make_fleet(), self.path)
        reviewed = service.scan()
        token = reviewed["token"]

        for _ in range(service.MAX_STAGED_SCANS + 3):
            observation = service.observe()
            self.assertTrue(observation["ok"])
            self.assertIsNone(observation["token"])
            self.assertFalse(observation["can_apply"])
            self.assertEqual(set(observation["probes"]), {"left", "right"})

        applied = service.apply(token)
        self.assertTrue(applied["ok"])
        self.assertTrue(applied["written"])

    def test_apply_is_single_use_preserves_unknown_config_and_makes_backup(self):
        service = CalibrationService(make_fleet(), self.path)
        token = service.scan()["token"]
        result = service.apply(token)
        self.assertTrue(result["ok"])
        self.assertTrue(result["written"])
        self.assertTrue(os.path.exists(result["config_backup"]))
        with open(self.path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["unknown_root"], {"keep": True})
        self.assertEqual(saved["installation"]["unknown_installation"], 7)
        self.assertEqual(saved["controllers"][0]["vendor_note"], "keep-controller")
        self.assertEqual(saved["controllers"][0]["segments"]["0"]["physical_label"], "keep-segment")
        self.assertEqual(saved["controllers"][0]["segments"]["0"]["pixels"], 10)
        replay = service.apply(token)
        self.assertFalse(replay["ok"])
        self.assertFalse(replay["written"])

    def test_apply_rejects_expired_token_and_changed_local_config(self):
        service = CalibrationService(make_fleet(), self.path)
        now = [100.0]
        service._clock = lambda: now[0]
        token = service.scan()["token"]
        now[0] += service.TOKEN_TTL_SECONDS + 1
        self.assertFalse(service.apply(token)["ok"])

        token = service.scan()["token"]
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write("\n")
        changed = service.apply(token)
        self.assertFalse(changed["ok"])
        self.assertIn("changed", changed["message"].lower())

    def test_apply_rejects_hardware_drift_before_write(self):
        light_fleet = make_fleet()
        service = CalibrationService(light_fleet, self.path)
        token = service.scan()["token"]
        before = open(self.path, "rb").read()
        light_fleet.clients["left"].config["hw"]["led"]["ins"][0]["len"] = 11
        result = service.apply(token)
        self.assertFalse(result["ok"])
        self.assertIn("hardware", result["message"].lower())
        self.assertEqual(open(self.path, "rb").read(), before)

    def test_scan_rejects_raw_hardware_that_would_otherwise_be_silently_subsetted(self):
        cases = {
            "nonpositive led count": lambda client: client.info["leds"].update(count=0),
            "invalid segment bounds": lambda client: client.state["seg"].append({"id": 1, "start": 9, "stop": 12}),
            "duplicate segment id": lambda client: client.state["seg"].append({"id": 0, "start": 1, "stop": 2}),
            "maxseg conflict": lambda client: client.info["leds"].update(maxseg=0),
            "malformed bus": lambda client: client.config["hw"]["led"]["ins"][0].pop("len"),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                light_fleet = make_fleet()
                mutate(light_fleet.clients["left"])
                result = CalibrationService(light_fleet, self.path).scan()
                self.assertFalse(result["can_apply"], result)
                self.assertIsNone(result["token"])

    def test_scan_rejects_disk_controller_membership_different_from_runtime(self):
        changed = copy.deepcopy(self.original)
        changed["controllers"][1]["host"] = "http://replacement"
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(changed, handle)
        result = CalibrationService(make_fleet(), self.path).scan()
        self.assertFalse(result["can_apply"])
        self.assertIn("membership", result["message"].lower())

    def test_identify_requires_explicit_valid_segment_and_restores_in_finally(self):
        light_fleet = make_fleet()
        service = CalibrationService(light_fleet, self.path)
        sleeps = []
        service._sleep = sleeps.append
        result = service.identify("left", 0)
        self.assertTrue(result["ok"])
        posts = light_fleet.clients["left"].posts
        self.assertEqual(posts[0], {"on": True, "bri": 24, "seg": [{"id": 0, "on": True, "bri": 24, "fx": 0, "col": [[255, 160, 32, 0]]}], "udpn": {"nn": True}})
        self.assertEqual(posts[-1], {"on": True, "bri": 123, "seg": [{"id": 0, "start": 0, "stop": 10, "on": True, "bri": 99, "fx": 9}], "udpn": {"nn": True}})
        self.assertEqual(sleeps, [service.IDENTIFY_SECONDS])
        self.assertFalse(service.identify("left", 9)["ok"])
        self.assertFalse(service.identify("", 0)["ok"])

        broken = make_fleet()
        original_post = broken.clients["left"].post_state
        calls = [0]
        def fail_once(payload):
            calls[0] += 1
            original_post(payload)
            if calls[0] == 1:
                raise RuntimeError("marker failed")
        broken.clients["left"].post_state = fail_once
        failed = CalibrationService(broken, self.path).identify("left", 0)
        self.assertFalse(failed["ok"])
        self.assertEqual(broken.clients["left"].posts[-1]["bri"], 123)

    def test_identify_rejects_detectably_active_renderer(self):
        light_fleet = make_fleet()
        light_fleet.realtime_running = True
        result = CalibrationService(light_fleet, self.path).identify("left", 0)
        self.assertFalse(result["ok"])
        self.assertIn("renderer", result["message"].lower())
        self.assertEqual(light_fleet.clients["left"].posts, [])

    def test_identify_rejects_frozen_or_live_segment_state(self):
        for state_change in ({"live": True}, {"seg": [{"id": 0, "start": 0, "stop": 10, "frz": True}]}):
            with self.subTest(state_change=state_change):
                light_fleet = make_fleet()
                light_fleet.clients["left"].state.update(copy.deepcopy(state_change))
                service = CalibrationService(light_fleet, self.path)
                result = service.identify("left", 0)
                self.assertFalse(result["ok"])
                self.assertEqual(light_fleet.clients["left"].posts, [])


if __name__ == "__main__":
    unittest.main()
