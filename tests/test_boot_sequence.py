"""Tests for boot_sequence.py — fake fleet only, no hardware, no real sleeps.

The sequence is driven through a duck-typed fleet (channels/get_state/post_state/
topology_dict) and every time-dependent path uses an injected ``sleep``/clock so
the suite is instant and deterministic.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import boot_sequence
import fleet
import lightctl

WALL = ["far-left", "middle-left", "middle-right", "far-right"]
CHANNEL_MAP = {
    "far-left": ("left", 0),
    "middle-left": ("left", 1),
    "middle-right": ("right", 0),
    "far-right": ("right", 1),
}
INVERSE = {(controller, segment): channel for channel, (controller, segment) in CHANNEL_MAP.items()}
EFFECTS = ["Solid", "Blink", "Audio Beat", "Chase", "Fire", "Rainbow"]
PALETTES = ["Default", "* Colors", "Analogous", "Rainbow"]
CONFIG = {"enabled": True, "dwell_s": 1.1, "brightness": 160}
TOPOLOGY = {
    "installation": {"name": "test-wall", "wall_order": list(WALL)},
    "controllers": [
        {
            "name": "left",
            "host": "http://10.0.0.1",
            "segments": {
                "0": {"channel": "far-left", "gpio": 16, "pixels": 34, "start": 0, "stop": 34},
                "1": {"channel": "middle-left", "gpio": 2, "pixels": 47, "start": 34, "stop": 81},
            },
        },
        {
            "name": "right",
            "host": "http://10.0.0.2",
            "segments": {
                "0": {"channel": "middle-right", "gpio": 16, "pixels": 47, "start": 0, "stop": 47},
                "1": {"channel": "far-right", "gpio": 2, "pixels": 40, "start": 47, "stop": 87},
            },
        },
    ],
}


def base_states() -> dict[str, dict]:
    """Pre-boot wall programming captured by the sequence."""
    return {
        "left": {
            "on": True,
            "bri": 128,
            "seg": [
                {"id": 0, "on": True, "bri": 128, "fx": 1, "pal": 2, "sx": 200,
                 "ix": 180, "col": [[255, 0, 0, 0]], "start": 0, "stop": 34},
                {"id": 1, "on": False, "bri": 64, "fx": 3, "pal": 1, "sx": 100,
                 "ix": 60, "col": [[0, 255, 0, 0]], "start": 34, "stop": 81},
            ],
        },
        "right": {
            "on": True,
            "bri": 200,
            "seg": [
                {"id": 0, "on": True, "bri": 200, "fx": 2, "pal": 0, "sx": 128,
                 "ix": 128, "col": [[0, 0, 255, 0]], "start": 0, "stop": 47},
                {"id": 1, "on": False, "bri": 32, "fx": 4, "pal": 3, "sx": 50,
                 "ix": 90, "col": [[1, 2, 3, 0]], "start": 47, "stop": 87},
            ],
        },
    }


class StatefulClient(lightctl.LightClient):
    """Dry-run LightClient that answers reads from a fixed state and records posts."""

    def __init__(self, host: str, state: dict):
        super().__init__(host, dry_run=True)
        self._state = copy.deepcopy(state)
        self.payloads: list[dict] = []

    def get_state(self) -> dict:
        return copy.deepcopy(self._state)

    def get_effects(self) -> list[str]:
        return list(EFFECTS)

    def get_palettes(self) -> list[str]:
        return list(PALETTES)

    def get_config(self) -> dict:
        return {}

    def post_state(self, payload: dict) -> None:
        lightctl.validate_wled_payload(payload)
        self.payloads.append(copy.deepcopy(payload))


def make_real_fleet() -> fleet.LightFleet:
    """Real fleet.LightFleet over recording dry-run clients (no hardware)."""
    controllers = [
        fleet.ControllerConfig(
            "left",
            "http://10.0.0.1",
            {
                0: fleet.SegmentConfig("far-left", gpio=16, pixels=34, start=0, stop=34),
                1: fleet.SegmentConfig("middle-left", gpio=2, pixels=47, start=34, stop=81),
            },
        ),
        fleet.ControllerConfig(
            "right",
            "http://10.0.0.2",
            {
                0: fleet.SegmentConfig("middle-right", gpio=16, pixels=47, start=0, stop=47),
                1: fleet.SegmentConfig("far-right", gpio=2, pixels=40, start=47, stop=87),
            },
        ),
    ]
    states = base_states()
    clients = {controller.name: StatefulClient(controller.host, states[controller.name])
               for controller in controllers}
    return fleet.LightFleet(clients, controllers)


class FakeClient:
    """Duck-typed WLED client: state/effects/palettes, optionally offline."""

    def __init__(self, state: dict, error: str | None = None):
        self._state = copy.deepcopy(state)
        self._error = error

    def get_state(self) -> dict:
        if self._error:
            raise RuntimeError(self._error)
        return copy.deepcopy(self._state)

    def get_effects(self) -> list[str]:
        if self._error:
            raise RuntimeError(self._error)
        return list(EFFECTS)

    def get_palettes(self) -> list[str]:
        if self._error:
            raise RuntimeError(self._error)
        return list(PALETTES)


class FakeFleet:
    """Duck-typed fleet: channels + get_state + post_state + topology_dict."""

    WALL_ORDER = list(WALL)

    def __init__(
        self,
        states: dict[str, dict] | None = None,
        offline: tuple[str, ...] = (),
        post_failures: tuple[str, ...] = (),
        raise_on_post: bool = False,
    ):
        states = states if states is not None else base_states()
        self._states = copy.deepcopy(states)
        self.clients = {
            name: FakeClient(
                self._states.get(name) or {},
                error=f"{name} unreachable" if name in offline else None,
            )
            for name in ("left", "right")
        }
        self.post_failures = set(post_failures)
        self.raise_on_post = raise_on_post
        self.posts: list[tuple[dict, str]] = []

    def channels(self) -> dict[str, tuple[str, int]]:
        return dict(CHANNEL_MAP)

    def names(self) -> list[str]:
        return list(self.clients)

    def get_state(self, target: str = "all") -> dict[str, dict]:
        states: dict[str, dict] = {}
        for name, client in self.clients.items():
            try:
                states[name] = client.get_state()
            except Exception as exc:
                states[name] = {"error": str(exc)}
        return states

    def topology_dict(self) -> dict:
        return copy.deepcopy(TOPOLOGY)

    def post_state(self, payload: dict, target: str = "all") -> dict:
        self.posts.append((copy.deepcopy(payload), target))
        if self.raise_on_post:
            raise RuntimeError(f"post to {target} failed")
        if target in self.post_failures:
            return {target: {"ok": False, "error": f"{target} unreachable"}}
        return {target: {"ok": True, "response": None}}


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return False


def reset_registry() -> None:
    boot_sequence._current_boot = None
    boot_sequence._last_report = None
    boot_sequence._last_enabled = True
    boot_sequence._last_error = None


class BootTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry()
        self.tmp = tempfile.mkdtemp(prefix="lightss-boot-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._counter = 0

    # -- helpers -----------------------------------------------------------

    def report_path(self, name: str = "boot_report.json") -> str:
        self._counter += 1
        return os.path.join(self.tmp, f"{self._counter}-{name}")

    def run_boot(self, fleet: FakeFleet | None = None, config: dict | None = None, **kwargs):
        fleet = fleet if fleet is not None else FakeFleet()
        kwargs.setdefault("report_path", self.report_path())
        boot = boot_sequence.BootSequence(
            fleet,
            dict(CONFIG) if config is None else config,
            sleep=kwargs.pop("sleep", lambda _seconds: None),
            **kwargs,
        )
        boot.start()
        boot.join(5)
        self.assertFalse(boot.is_alive(), "boot thread did not finish")
        return boot, fleet

    @staticmethod
    def anim_posts(fleet: FakeFleet) -> list[tuple[dict, str]]:
        return [
            (payload, target)
            for payload, target in fleet.posts
            if payload.get("seg") and all(seg.get("fx") == 0 for seg in payload["seg"])
        ]

    @staticmethod
    def restore_posts(fleet: FakeFleet) -> list[tuple[dict, str]]:
        return [
            (payload, target)
            for payload, target in fleet.posts
            if payload.get("seg") and any(seg.get("fx") != 0 for seg in payload["seg"])
        ]


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

class ValidateConfigTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(
            boot_sequence.validate_config(None),
            {"enabled": True, "dwell_s": 1.1, "brightness": 160},
        )
        self.assertEqual(boot_sequence.validate_config({}), boot_sequence.DEFAULTS)

    def test_dwell_is_clamped_to_the_safe_range(self):
        self.assertAlmostEqual(boot_sequence.validate_config({"dwell_s": 0.1})["dwell_s"], 0.8)
        self.assertAlmostEqual(boot_sequence.validate_config({"dwell_s": 60})["dwell_s"], 5.0)
        self.assertAlmostEqual(
            boot_sequence.validate_config({"dwell_s": "nope"})["dwell_s"], 1.1
        )
        self.assertAlmostEqual(
            boot_sequence.validate_config({"dwell_s": float("nan")})["dwell_s"], 1.1
        )

    def test_brightness_is_clamped_and_parsed(self):
        self.assertEqual(boot_sequence.validate_config({"brightness": 0})["brightness"], 1)
        self.assertEqual(boot_sequence.validate_config({"brightness": 999})["brightness"], 255)
        self.assertEqual(boot_sequence.validate_config({"brightness": "80"})["brightness"], 80)
        self.assertEqual(boot_sequence.validate_config({"brightness": "x"})["brightness"], 160)

    def test_enabled_accepts_booleans_and_strings(self):
        self.assertFalse(boot_sequence.validate_config({"enabled": False})["enabled"])
        self.assertFalse(boot_sequence.validate_config({"enabled": "false"})["enabled"])
        self.assertFalse(boot_sequence.validate_config({"enabled": 0})["enabled"])
        self.assertTrue(boot_sequence.validate_config({"enabled": "yes"})["enabled"])


# ---------------------------------------------------------------------------
# Configuration digest
# ---------------------------------------------------------------------------

class ConfigDigestTests(unittest.TestCase):
    def test_digest_is_stable_and_config_sensitive(self):
        a = boot_sequence.BootSequence(FakeFleet(), dict(CONFIG))
        b = boot_sequence.BootSequence(FakeFleet(), dict(CONFIG))
        c = boot_sequence.BootSequence(FakeFleet(), {**CONFIG, "brightness": 42})
        self.assertEqual(
            boot_sequence._config_digest(a.config), boot_sequence._config_digest(b.config)
        )
        self.assertNotEqual(
            boot_sequence._config_digest(a.config), boot_sequence._config_digest(c.config)
        )
        self.assertEqual(len(boot_sequence._config_digest(a.config)), 64)


# ---------------------------------------------------------------------------
# Sequence + exclusivity
# ---------------------------------------------------------------------------

class SequenceTests(BootTestCase):
    def test_lines_articulate_the_wall_order_with_effect_names(self):
        boot, _fleet = self.run_boot()
        status = boot.status()
        self.assertEqual(status["phase"], "done")
        self.assertTrue(status["ok"])
        self.assertEqual(
            status["lines"][:4],
            [
                "1/4 far-left — left · segment 0 · 34 px · fx Blink (1) · pal Analogous (2) · sx 200 · ix 180 · col #ff0000",
                "2/4 middle-left — left · segment 1 · 47 px · fx Chase (3) · pal * Colors (1) · sx 100 · ix 60 · col #00ff00",
                "3/4 middle-right — right · segment 0 · 47 px · fx Audio Beat (2) · pal Default (0) · sx 128 · ix 128 · col #0000ff",
                "4/4 far-right — right · segment 1 · 40 px · fx Fire (4) · pal Rainbow (3) · sx 50 · ix 90 · col #010203",
            ],
        )
        self.assertIn("complete", status["lines"][-1])
        self.assertEqual(len(status["lines"]), 5)

    def test_each_step_lights_exactly_one_strip_with_a_distinct_static_color(self):
        boot, fleet = self.run_boot()
        anim = self.anim_posts(fleet)
        self.assertEqual(len(anim), 8)  # 4 steps, one post per controller each

        active_channels: list[str] = []
        for payload, target in anim:
            for segment in payload["seg"]:
                self.assertEqual(segment["fx"], 0)
                self.assertNotIn("sx", segment)
                self.assertNotIn("ix", segment)
            on_segments = [segment for segment in payload["seg"] if segment["on"]]
            self.assertLessEqual(len(on_segments), 1)
            if not on_segments:
                # The controller that owns no active strip has everything off.
                self.assertTrue(all(segment["col"] == [[0, 0, 0]] for segment in payload["seg"]))
                continue
            active = on_segments[0]
            channel = INVERSE[(target, active["id"])]
            active_channels.append(channel)
            expected_color = list(boot_sequence.STEP_COLORS[WALL.index(channel)])
            self.assertEqual(active["col"], [expected_color])
            for segment in payload["seg"]:
                if segment is not active:
                    self.assertFalse(segment["on"])
                    self.assertEqual(segment["col"], [[0, 0, 0]])
        self.assertEqual(active_channels, WALL)
        self.assertEqual(
            [channel for channel in active_channels],
            ["far-left", "middle-left", "middle-right", "far-right"],
        )

    def test_global_power_and_brightness_is_posted_first(self):
        _boot, fleet = self.run_boot()
        self.assertEqual(fleet.posts[0], ({"on": True, "bri": 160}, "all"))

    def test_each_strip_gets_its_own_distinct_color(self):
        colors = [list(color) for color in boot_sequence.STEP_COLORS]
        self.assertEqual(len(colors), 4)
        self.assertEqual(len({tuple(color) for color in colors}), 4)
        self.assertEqual(colors[0], [0, 255, 255])   # cyan
        self.assertEqual(colors[1], [255, 0, 255])   # magenta
        self.assertEqual(colors[2], [255, 191, 0])   # amber
        self.assertEqual(colors[3], [128, 255, 0])   # lime


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

class RestoreTests(BootTestCase):
    def test_pre_boot_state_is_restored_faithfully(self):
        boot, fleet = self.run_boot()
        restore = self.restore_posts(fleet)
        self.assertEqual(len(restore), 2)
        by_target = {target: payload for payload, target in restore}
        self.assertEqual(set(by_target), {"left", "right"})
        self.assertEqual(
            by_target["left"],
            {
                "on": True,
                "bri": 128,
                "seg": [
                    {"id": 0, "fx": 1, "pal": 2, "sx": 200, "ix": 180,
                     "col": [[255, 0, 0, 0]], "on": True, "bri": 128},
                    {"id": 1, "fx": 3, "pal": 1, "sx": 100, "ix": 60,
                     "col": [[0, 255, 0, 0]], "on": False, "bri": 64},
                ],
            },
        )
        self.assertEqual(
            by_target["right"],
            {
                "on": True,
                "bri": 200,
                "seg": [
                    {"id": 0, "fx": 2, "pal": 0, "sx": 128, "ix": 128,
                     "col": [[0, 0, 255, 0]], "on": True, "bri": 200},
                    {"id": 1, "fx": 4, "pal": 3, "sx": 50, "ix": 90,
                     "col": [[1, 2, 3, 0]], "on": False, "bri": 32},
                ],
            },
        )
        self.assertEqual(set(boot.status()["report"]["restored"]["applied"]), {"left", "right"})
        self.assertTrue(boot.status()["report"]["restored"]["ok"])

    def test_stop_interrupts_dwell_then_restores(self):
        holder: dict[str, boot_sequence.BootSequence] = {}
        fleet = FakeFleet()

        def stopping_sleep(_seconds: float) -> None:
            holder["boot"].stop()

        boot = boot_sequence.BootSequence(
            fleet, dict(CONFIG), sleep=stopping_sleep, report_path=self.report_path()
        )
        holder["boot"] = boot
        boot.start()
        boot.join(5)
        self.assertFalse(boot.is_alive())
        status = boot.status()
        self.assertEqual(status["phase"], "done")
        self.assertEqual(len(status["report"]["steps"]), 1)
        self.assertTrue(status["report"]["stopped"])
        self.assertIn("stopped", status["lines"][-1])
        restored = self.restore_posts(fleet)
        self.assertEqual({target for _payload, target in restored}, {"left", "right"})
        self.assertTrue(status["report"]["restored"]["ok"])


# ---------------------------------------------------------------------------
# Report + persistence
# ---------------------------------------------------------------------------

class ReportTests(BootTestCase):
    def test_report_is_persisted_with_schema_and_shape(self):
        path = self.report_path()
        boot, _fleet = self.run_boot(report_path=path)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        report = json.loads(Path(path).read_text(encoding="utf-8"))

        self.assertEqual(report["schema"], boot_sequence.REPORT_SCHEMA)
        self.assertEqual(report["phase"], "done")
        self.assertEqual(report["config"], CONFIG)
        self.assertEqual(
            report["config_digest"],
            hashlib.sha256(
                json.dumps(CONFIG, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        )
        self.assertTrue(report["started_at"].endswith("Z"))
        self.assertTrue(report["finished_at"].endswith("Z"))
        self.assertEqual(report["wall_order"], WALL)
        self.assertIsInstance(report["duration_s"], float)
        self.assertEqual(report["report_path"], path)

        self.assertEqual(set(report["controllers"]), {"left", "right"})
        self.assertTrue(report["controllers"]["left"]["reachable"])
        self.assertEqual(report["controllers"]["left"]["host"], "http://10.0.0.1")
        self.assertEqual(report["controllers"]["left"]["segments"]["0"]["pixels"], 34)
        self.assertNotIn("error", report["controllers"]["left"])

        self.assertEqual(set(report["channels"]), set(WALL))
        far_left = report["channels"]["far-left"]
        self.assertEqual(far_left["index"], 1)
        self.assertEqual(far_left["controller"], "left")
        self.assertEqual(far_left["segment"], 0)
        self.assertEqual(far_left["pixels"], 34)
        self.assertEqual(far_left["gpio"], 16)
        self.assertTrue(far_left["readable"])
        self.assertEqual(far_left["color"], list(boot_sequence.STEP_COLORS[0]))
        self.assertEqual(far_left["line"], boot.status()["lines"][0])
        self.assertEqual(far_left["program"]["fx"], 1)
        self.assertEqual(far_left["program"]["effect"], "Blink")
        self.assertEqual(far_left["program"]["pal"], 2)
        self.assertEqual(far_left["program"]["palette"], "Analogous")
        self.assertEqual(far_left["program"]["sx"], 200)
        self.assertEqual(far_left["program"]["start"], 0)
        self.assertEqual(far_left["program"]["stop"], 34)

        capabilities = report["capabilities"]
        self.assertEqual(capabilities["effect_count"], len(EFFECTS))
        self.assertEqual(capabilities["audio_reactive_count"], 1)
        self.assertEqual(capabilities["palette_count"], len(PALETTES))
        self.assertIn("per_strip", capabilities["wall_modes"])
        self.assertTrue(capabilities["controls"])

        self.assertEqual(report["mismatches"], [])
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["restored"]["ok"])
        self.assertEqual([step["channel"] for step in report["steps"]], WALL)
        self.assertEqual(report["steps"][0]["text"], report["lines"][0])
        self.assertIn("complete", report["lines"][-1])

        self.assertEqual(boot.status()["report"], report)

    def test_default_report_path_uses_lightctl_config_root(self):
        with mock.patch.object(lightctl, "_CONFIG_ROOT", self.tmp):
            boot, _fleet = self.run_boot(report_path=None)
        self.assertEqual(boot.status()["phase"], "done")
        path = os.path.join(self.tmp, "boot_report.json")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8"))["schema"], 1)

    def test_persistence_failure_is_recorded_not_fatal(self):
        blocker = os.path.join(self.tmp, "not-a-directory")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("x")
        boot, _fleet = self.run_boot(report_path=os.path.join(blocker, "boot_report.json"))
        status = boot.status()
        self.assertFalse(boot.is_alive())
        self.assertEqual(status["phase"], "done")
        self.assertTrue(status["ok"])
        report = status["report"]
        self.assertIn("could not persist boot report", " ".join(report["errors"]))
        self.assertEqual(report["report_path"], os.path.join(blocker, "boot_report.json"))


# ---------------------------------------------------------------------------
# Real fleet.LightFleet round trip (still no hardware: dry-run recording clients)
# ---------------------------------------------------------------------------

class RealFleetIntegrationTests(BootTestCase):
    def test_real_lightfleet_posts_one_step_at_a_time_and_restores(self):
        real = make_real_fleet()
        boot, _fleet = self.run_boot(real)
        report = boot.status()["report"]
        self.assertEqual(report["phase"], "done")
        self.assertEqual(report["mismatches"], [])
        self.assertEqual(report["wall_order"], WALL)
        self.assertTrue(report["restored"]["ok"])

        left = real.clients["left"].payloads
        right = real.clients["right"].payloads
        self.assertEqual(len(left), 6)   # global + 4 steps + restore
        self.assertEqual(len(right), 6)
        self.assertEqual(left[0], {"on": True, "bri": 160, "udpn": {"nn": True}})

        for payload in left[1:5] + right[1:5]:
            self.assertEqual(payload["udpn"], {"nn": True})
            self.assertEqual(len(payload["seg"]), 2)
            self.assertTrue(all(segment["fx"] == 0 for segment in payload["seg"]))
            self.assertLessEqual(sum(1 for segment in payload["seg"] if segment.get("on")), 1)

        expected_left_restore = {
            "on": True,
            "bri": 128,
            "seg": [
                {"id": 0, "fx": 1, "pal": 2, "sx": 200, "ix": 180,
                 "col": [[255, 0, 0, 0]], "on": True, "bri": 128},
                {"id": 1, "fx": 3, "pal": 1, "sx": 100, "ix": 60,
                 "col": [[0, 255, 0, 0]], "on": False, "bri": 64},
            ],
            "udpn": {"nn": True},
        }
        self.assertEqual(left[-1], expected_left_restore)
        self.assertEqual(right[-1]["on"], True)
        self.assertEqual(right[-1]["bri"], 200)
        self.assertEqual([segment["id"] for segment in right[-1]["seg"]], [0, 1])
        self.assertEqual(right[-1]["seg"][1]["fx"], 4)


class MismatchTests(BootTestCase):
    def test_topology_mismatch_checker_is_used_when_available(self):
        duck = FakeFleet()
        duck.controllers = []  # type: ignore[attr-defined]  # enable the real checker path
        with mock.patch.object(
            fleet, "topology_mismatches", return_value=["left: segment 1 drift"]
        ):
            boot, _fleet = self.run_boot(duck)
        self.assertEqual(
            boot.status()["report"]["mismatches"], ["left: segment 1 drift"]
        )


# ---------------------------------------------------------------------------
# Offline controllers / error phase
# ---------------------------------------------------------------------------

class OfflineTests(BootTestCase):
    def test_no_readable_state_finishes_in_error_phase_with_a_report(self):
        fleet = FakeFleet(offline=("left", "right"))
        path = self.report_path()
        boot, _fleet = self.run_boot(fleet, report_path=path)
        status = boot.status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["phase"], "error")
        self.assertIn("no controller state", status["error"])
        self.assertIn("failed", status["lines"][-1])
        self.assertIn("no controller state", status["lines"][-1])
        self.assertEqual(fleet.posts, [])
        self.assertTrue(os.path.exists(path))
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(report["phase"], "error")
        self.assertTrue(report["errors"])
        self.assertTrue(report["restored"]["skipped"])
        self.assertEqual(report["channels"]["far-left"]["readable"], False)
        self.assertIsNone(report["channels"]["far-left"]["program"])

    def test_partial_offline_still_animates_and_records_errors(self):
        fleet = FakeFleet(offline=("right",), post_failures=("right",))
        boot, _fleet = self.run_boot(fleet)
        report = boot.status()["report"]
        self.assertEqual(report["phase"], "done")
        self.assertTrue(report["controllers"]["left"]["reachable"])
        self.assertFalse(report["controllers"]["right"]["reachable"])
        self.assertFalse(report["channels"]["middle-right"]["readable"])
        self.assertIsNone(report["channels"]["middle-right"]["program"])
        self.assertEqual(len(report["steps"]), 4)
        self.assertTrue(any("right" in error for error in report["errors"]))
        self.assertEqual(set(report["restored"]["applied"]), {"left"})
        self.assertTrue(report["restored"]["ok"])


# ---------------------------------------------------------------------------
# Injection: observe_fn
# ---------------------------------------------------------------------------

class ObserveTests(BootTestCase):
    def test_observe_fn_supplies_snapshot_and_mismatches(self):
        observation = {
            "states": base_states(),
            "effects": {"left": EFFECTS, "right": EFFECTS},
            "palettes": {"left": PALETTES, "right": PALETTES},
            "mismatches": ["left: segment 0 bounds 0-30 != configured 0-34"],
        }
        fleet = FakeFleet(offline=("left", "right"))  # direct reads would fail
        boot, _fleet = self.run_boot(
            fleet, observe_fn=lambda _fleet: observation
        )
        report = boot.status()["report"]
        self.assertEqual(report["phase"], "done")
        self.assertEqual(report["errors"], [])
        self.assertEqual(
            report["mismatches"], ["left: segment 0 bounds 0-30 != configured 0-34"]
        )
        self.assertEqual(report["channels"]["far-left"]["program"]["fx"], 1)
        self.assertEqual(report["capabilities"]["audio_reactive_count"], 1)


# ---------------------------------------------------------------------------
# Write failure containment
# ---------------------------------------------------------------------------

class WriteFailureTests(BootTestCase):
    def test_post_failure_is_recorded_not_fatal(self):
        fleet = FakeFleet(raise_on_post=True)
        boot, _fleet = self.run_boot(fleet)
        status = boot.status()
        self.assertEqual(status["phase"], "done")
        self.assertTrue(status["ok"])
        report = status["report"]
        self.assertGreaterEqual(len(report["errors"]), 5)  # global + 4 channel steps
        self.assertIn("global power/brightness", report["errors"][0])


# ---------------------------------------------------------------------------
# Registry + status transitions
# ---------------------------------------------------------------------------

class RegistryTests(BootTestCase):
    def test_idle_status_shape_and_empty_context(self):
        self.assertEqual(boot_sequence.latest_report(), None)
        self.assertEqual(boot_sequence.context_text(), "")
        self.assertEqual(boot_sequence.summary_text(None), "No boot report available.")
        status = boot_sequence.boot_status()
        self.assertEqual(
            status,
            {
                "ok": True,
                "enabled": True,
                "running": False,
                "phase": "idle",
                "started_at": None,
                "finished_at": None,
                "step": None,
                "lines": [],
                "report": None,
                "error": None,
            },
        )

    def test_disabled_start_returns_none(self):
        fleet = FakeFleet()
        self.assertIsNone(
            boot_sequence.start_boot(
                fleet, {"enabled": False}, report_path=self.report_path()
            )
        )
        status = boot_sequence.boot_status()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["running"])
        self.assertEqual(status["phase"], "idle")
        self.assertEqual(fleet.posts, [])

    def test_status_transitions_running_then_done(self):
        gate = threading.Event()
        fleet = FakeFleet()
        boot = boot_sequence.start_boot(
            fleet,
            dict(CONFIG),
            sleep=lambda _seconds: gate.wait(5),
            report_path=self.report_path(),
        )
        self.assertIsNotNone(boot)
        self.assertTrue(wait_for(lambda: boot_sequence.boot_status()["step"] is not None))
        running = boot_sequence.boot_status()
        self.assertTrue(running["running"])
        self.assertEqual(running["phase"], "running")
        self.assertEqual(running["step"]["index"], 1)
        self.assertEqual(running["step"]["channel"], "far-left")
        self.assertIn("1/4 far-left", running["step"]["text"])

        gate.set()
        boot.join(5)
        done = boot_sequence.boot_status()
        self.assertFalse(done["running"])
        self.assertEqual(done["phase"], "done")
        self.assertTrue(done["ok"])
        self.assertIsNotNone(done["finished_at"])
        self.assertIsNotNone(done["report"])
        self.assertEqual(len(done["lines"]), 5)
        self.assertIs(boot_sequence.latest_report(), done["report"])

    def test_stop_boot_stops_the_registered_sequence(self):
        fleet = FakeFleet()
        boot = boot_sequence.start_boot(
            fleet,
            {"dwell_s": 5.0, "brightness": 160},
            sleep=lambda _seconds: time.sleep(0.002),
            report_path=self.report_path(),
        )
        self.assertIsNotNone(boot)
        self.assertTrue(wait_for(lambda: boot.status()["phase"] == "running"))
        self.assertEqual(boot_sequence.stop_boot(), "Boot sequence stopped.")
        boot.join(5)
        status = boot_sequence.boot_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["phase"], "done")
        report = boot_sequence.latest_report()
        self.assertTrue(report["stopped"])
        self.assertIn("stopped", report["lines"][-1])
        self.assertEqual(boot_sequence.stop_boot(), "No boot sequence is running.")


# ---------------------------------------------------------------------------
# Human + AI text output
# ---------------------------------------------------------------------------

class TextOutputTests(BootTestCase):
    def test_summary_text_is_compact_and_mentions_the_wall(self):
        boot = boot_sequence.start_boot(
            FakeFleet(), dict(CONFIG), sleep=lambda _seconds: None,
            report_path=self.report_path(),
        )
        boot.join(5)
        report = boot_sequence.latest_report()
        text = boot_sequence.summary_text(report)
        lines = text.splitlines()
        self.assertGreaterEqual(len(lines), 1)
        self.assertLessEqual(len(lines), 8)
        self.assertIn("done", lines[0])
        self.assertIn("far-left", text)
        self.assertIn("far-right", text)
        self.assertIn("Wall order:", text)

    def test_context_text_is_authoritative_and_lists_the_programming(self):
        boot = boot_sequence.start_boot(
            FakeFleet(), dict(CONFIG), sleep=lambda _seconds: None,
            report_path=self.report_path(),
        )
        boot.join(5)
        text = boot_sequence.context_text()
        self.assertTrue(text.startswith("=== BOOT SELF-CONFIG"))
        self.assertIn("Wall order (left → right): far-left → middle-left → middle-right → far-right", text)
        self.assertIn("far-left (left segment 0 · 34 px · GPIO 16)", text)
        self.assertIn("fx Blink (1)", text)
        self.assertIn("pal Analogous (2)", text)
        self.assertIn("Capabilities: 6 effects (1 audio-reactive)", text)
        self.assertIn("Topology mismatches: none", text)
        self.assertIn("Use these exact channel names", text)


if __name__ == "__main__":
    unittest.main()
