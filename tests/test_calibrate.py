"""Tests for calibrate.py — self-calibration from live controller reads."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import calibrate
import fleet
import lightctl
import mcp_light


INFO = {
    "name": "WLED-left",
    "ver": "0.16.0",
    "leds": {"count": 82, "maxseg": 16},
}
STATE = {
    "on": True,
    "bri": 128,
    "seg": [
        {"id": 0, "start": 0, "stop": 34, "on": True},
        {"id": 1, "start": 34, "stop": 82, "on": True},
    ],
}
CFG = {
    "hw": {
        "led": {
            "ins": [
                {"pin": [16], "start": 0, "len": 34, "order": 2},
                {"pin": [2], "start": 34, "len": 48, "order": 2},
            ]
        }
    }
}


class FakeControllerClient(lightctl.LightClient):
    """LightClient stand-in serving canned info/state/config; records posts."""

    def __init__(self, host, info=None, state=None, config=None):
        super().__init__(host, dry_run=True)
        self._info = info if info is not None else INFO
        self._state = state if state is not None else STATE
        self._config = config if config is not None else CFG
        self.payloads: list[dict] = []

    def get_info(self):
        return self._info

    def get_state(self):
        return self._state

    def get_config(self):
        return self._config

    def post_state(self, payload):
        lightctl.validate_wled_payload(payload)
        self.payloads.append(payload)


def make_fleet(state=None):
    controllers = [
        fleet.ControllerConfig(
            "left",
            "http://10.27.27.112",
            {
                0: fleet.SegmentConfig(channel="far-left", gpio=16, pixels=34, start=0, stop=34),
                1: fleet.SegmentConfig(channel="middle-left", gpio=2, pixels=48, start=34, stop=82),
            },
        ),
        fleet.ControllerConfig(
            "right",
            "http://10.27.27.110",
            {0: fleet.SegmentConfig(channel="far-right", gpio=2, pixels=40, start=47, stop=87)},
        ),
    ]
    clients = {
        "left": FakeControllerClient("http://10.27.27.112", state=state or STATE),
        "right": FakeControllerClient("http://10.27.27.110"),
    }
    return fleet.LightFleet(clients, controllers)


class ProbeTests(unittest.TestCase):
    def test_probe_reads_counts_buses_and_segments(self):
        probe = calibrate.probe_client(FakeControllerClient("http://10.27.27.112"))
        self.assertEqual(probe["led_count"], 82)
        self.assertEqual(probe["maxseg"], 16)
        self.assertEqual(probe["version"], "0.16.0")
        self.assertEqual(
            [(b["gpio"], b["start"], b["len"], b["color_order"]) for b in probe["buses"]],
            [(16, 0, 34, "BRG"), (2, 34, 48, "BRG")],
        )
        self.assertEqual(
            [(s["id"], s["start"], s["stop"], s["pixels"], s["gpio"]) for s in probe["segments"]],
            [(0, 0, 34, 34, 16), (1, 34, 82, 48, 2)],
        )

    def test_probe_tolerates_missing_config_and_empty_segments(self):
        client = FakeControllerClient(
            "http://x",
            info={"name": "n"},
            state={"seg": [{"id": 0, "start": 0, "stop": 0}]},  # deleted segment
            config={},
        )
        probe = calibrate.probe_client(client)
        self.assertEqual(probe["buses"], [])
        self.assertEqual(probe["segments"], [])  # stop <= start is skipped

    def test_collect_probes_records_errors_without_raising(self):
        class DeadClient:
            host = "http://dead"

            def get_info(self):
                raise OSError("unreachable")

        probes = calibrate.collect_probes(clients={"dead": DeadClient()})
        self.assertIn("error", probes["dead"])


class BuildTests(unittest.TestCase):
    def test_build_preserves_existing_channel_names(self):
        existing = [
            fleet.ControllerConfig(
                "left",
                "http://10.27.27.112",
                {0: fleet.SegmentConfig(channel="far-left"), 1: fleet.SegmentConfig(channel="middle-left")},
            )
        ]
        probes = {"left": calibrate.probe_client(FakeControllerClient("http://10.27.27.112"))}
        controllers = calibrate.build_controllers(probes, existing)
        self.assertEqual(controllers[0].name, "left")
        self.assertEqual(controllers[0].segments[0].channel, "far-left")
        self.assertEqual(controllers[0].segments[1].channel, "middle-left")
        # geometry comes from the probe, not the prior config
        self.assertEqual(controllers[0].segments[1].pixels, 48)
        self.assertEqual(controllers[0].segments[1].gpio, 2)

    def test_build_names_unknown_segments_with_placeholders(self):
        probes = {"left": calibrate.probe_client(FakeControllerClient("http://10.27.27.112"))}
        controllers = calibrate.build_controllers(probes, [])
        self.assertEqual(controllers[0].segments[0].channel, "left-seg0")

    def test_apply_assignments_renames_channels(self):
        controllers = [
            fleet.ControllerConfig(
                "left", "http://h", {0: fleet.SegmentConfig(channel="left-seg0", pixels=5, start=0, stop=5)}
            )
        ]
        calibrate.apply_assignments(controllers, [{"controller": "left", "segment": 0, "channel": "far-left"}])
        self.assertEqual(controllers[0].segments[0].channel, "far-left")
        with self.assertRaises(ValueError):
            calibrate.apply_assignments(controllers, [{"controller": "left", "segment": 9, "channel": "x"}])


class CalibrateWriteTests(unittest.TestCase):
    def test_calibrate_report_and_write_with_backup(self):
        fleet_ = make_fleet()
        clients = {name: fleet_.clients[name] for name in fleet_.names()}
        topology = (fleet_.installation, fleet_.controllers)
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.json")
            store = {"installation": {"pixel_zero": "bottom"}}
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(store, f)

            def load():
                return json.loads(json.dumps(store))

            def save(config):
                store.clear()
                store.update(config)
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f)

            report = calibrate.calibrate(
                clients=clients,
                topology=topology,
                installation_updates={"pixel_zero": "bottom"},
                write=True,
                load=load,
                save=save,
                config_path=config_path,
            )
            self.assertTrue(report["written"])
            self.assertTrue(report["config_backup"].startswith(config_path + ".bak-"))
            self.assertEqual(report["errors"], [])
            left = next(c for c in report["controllers"] if c["name"] == "left")
            self.assertEqual(
                [(s["id"], s["channel"], s["pixels"]) for s in left["segments"]],
                [(0, "far-left", 34), (1, "middle-left", 48)],
            )
            # unanimous BRG buses calibrate installation.color_order
            self.assertEqual(store["installation"]["color_order"], "BRG")
            # controllers were persisted with full geometry
            persisted_left = next(c for c in store["controllers"] if c["name"] == "left")
            self.assertEqual(persisted_left["segments"]["1"]["pixels"], 48)

    def test_calibrate_dry_run_writes_nothing(self):
        fleet_ = make_fleet()
        writes = []

        def save(config):
            writes.append(config)

        report = calibrate.calibrate(
            clients={"left": fleet_.clients["left"]},
            topology=(fleet_.installation, fleet_.controllers),
            write=False,
            save=save,
        )
        self.assertFalse(report["written"])
        self.assertEqual(writes, [])


class IdentifyTests(unittest.TestCase):
    def test_identify_restores_state_when_flashing_fails(self):
        fleet_ = make_fleet()
        client = fleet_.clients["left"]
        original_post = client.post_state
        calls = 0

        def fail_during_flash(payload):
            nonlocal calls
            calls += 1
            original_post(payload)
            if calls == 2:
                raise RuntimeError("flash failed")

        client.post_state = fail_during_flash
        with self.assertRaises(RuntimeError):
            calibrate.identify(fleet_, target="middle-left", flashes=1, sleep=lambda _s: None)
        self.assertEqual(client.payloads[-1]["on"], True)

    def test_identify_toggles_segments_and_restores_power(self):
        fleet_ = make_fleet()
        calibrate.identify(fleet_, target="middle-left", flashes=2, sleep=lambda _s: None)
        posts = fleet_.clients["left"].payloads
        # master on + 4 toggles (2 flashes x2) + master restore
        self.assertEqual(len(posts), 6)
        self.assertEqual(posts[0]["on"], True)
        for toggle in posts[1:5]:
            self.assertEqual(toggle["seg"], [{"on": "t", "id": 1}])
        self.assertEqual(posts[5]["on"], True)  # was on in STATE
        # the other controller is untouched
        self.assertEqual(fleet_.clients["right"].payloads, [])

    def test_identify_restores_off_state(self):
        dark = dict(STATE, on=False)
        fleet_ = make_fleet(state=dark)
        calibrate.identify(fleet_, target="far-left", flashes=1, sleep=lambda _s: None)
        posts = fleet_.clients["left"].payloads
        self.assertEqual(posts[-1]["on"], False)

    def test_identify_single_client_toggles_main_segment(self):
        client = FakeControllerClient("http://10.27.27.112")
        calibrate.identify(client, flashes=1, sleep=lambda _s: None)
        toggles = [p for p in client.payloads if "seg" in p]
        self.assertEqual(toggles, [{"seg": [{"on": "t"}], "udpn": {"nn": True}}] * 2)


class McpCalibrationToolTests(unittest.TestCase):
    def test_tools_are_exposed(self):
        names = {tool["name"] for tool in mcp_light.build_tools()}
        self.assertIn("calibrate", names)
        self.assertIn("identify", names)

    def test_calibrate_tool_reports_probed_topology(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(fleet_, "calibrate", {}, None)
        report = json.loads(result["content"][0]["text"])
        self.assertFalse(report["written"])
        left = next(c for c in report["controllers"] if c["name"] == "left")
        self.assertEqual(left["led_count"], 82)
        self.assertEqual(left["segments"][1]["pixels"], 48)

    def test_calibrate_tool_rejects_bad_assignment(self):
        fleet_ = make_fleet()
        with self.assertRaises(ValueError):
            mcp_light.call_tool(
                fleet_,
                "calibrate",
                {"assignments": [{"controller": "left", "segment": 9, "channel": "x"}]},
                None,
            )

    def test_identify_tool_flashes_target(self):
        fleet_ = make_fleet()
        result = mcp_light.call_tool(fleet_, "identify", {"target": "far-right", "flashes": 1}, None)
        self.assertIn("far-right", result["content"][0]["text"])
        self.assertEqual(len(fleet_.clients["right"].payloads), 4)  # on + 2 toggles + restore


if __name__ == "__main__":
    unittest.main()
