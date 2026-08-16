"""Tests for scripts/migrate_wled_topology.py.

All tests run with no network access: HTTP is faked by FakeTransport.
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "migrate_wled_topology.py"
)
spec = importlib.util.spec_from_file_location("migrate_wled_topology", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

EXPECTED = {
    "http://10.27.27.110": [
        {"start": 0, "len": 34, "pin": [16], "order": 2, "type": 22},
        {"start": 34, "len": 48, "pin": [2], "order": 2, "type": 22},
    ],
    "http://10.27.27.112": [
        {"start": 0, "len": 47, "pin": [16], "order": 2, "type": 22},
        {"start": 47, "len": 40, "pin": [2], "order": 2, "type": 22},
    ],
}


def make_bus(pin, start=0, length=80, order=0):
    return {
        "start": start,
        "len": length,
        "pin": [pin],
        "order": order,
        "type": 22,
        "rev": False,
        "skip": 0,
        "ref": False,
        "rgbwm": 0,
        "freq": 0,
        "maxpwr": 0,
        "ledma": 30,
        "drv": 0,
    }


def make_cfg(pins):
    return {
        "hw": {"led": {"ins": [make_bus(pin) for pin in pins], "total": sum(length for _pin, _start, length in migration.HOST_BUSES[next(h for h, ps in migration.HOST_PINS.items() if ps == list(pins))]) if list(pins) in migration.HOST_PINS.values() else 80}},
        "name": "wled-test",
    }


def make_state():
    return {
        "on": True,
        "bri": 128,
        "seg": [{"id": 0, "start": 0, "stop": 160, "on": True, "fx": 0}],
    }


class FakeTransport:
    """In-memory WLED stand-in. GETs serve canned JSON; POSTs mutate it."""

    def __init__(self, fail_path=None):
        self.fail_path = fail_path
        self.posts = []
        self.cfgs = {
            host: make_cfg(pins) for host, pins in migration.HOST_PINS.items()
        }
        self.states = {host: make_state() for host in migration.HOST_PINS}

    def get(self, host, path):
        if path == self.fail_path:
            raise OSError(f"simulated GET failure for {path}")
        if path == "/json/cfg":
            return copy.deepcopy(self.cfgs[host])
        if path == "/json/state":
            return copy.deepcopy(self.states[host])
        if path == "/json/info":
            count = sum(
                bus["len"] for bus in self.cfgs[host]["hw"]["led"]["ins"]
            )
            return {"leds": {"count": count}, "name": host}
        if path == "/json/eff":
            return [0, 1, 2]
        if path == "/json/pal":
            return [0, 1, 2]
        raise AssertionError(f"unexpected GET {host}{path}")

    def post(self, host, path, payload):
        self.posts.append((host, path, copy.deepcopy(payload)))
        if path == "/json/cfg":
            self.cfgs[host]["hw"]["led"].update(copy.deepcopy(payload["hw"]["led"]))
            return {"success": True}
        if path == "/json/state":
            self.states[host].update(copy.deepcopy(payload))
            return {"success": True}
        raise AssertionError(f"unexpected POST {host}{path}")


def test_build_bus_payload_preserves_unrelated_bus_fields():
    cfg = {"hw": {"led": {"ins": [{
        "start": 0, "len": 200, "pin": [16], "order": 1, "type": 22,
        "rev": False, "skip": 0, "ref": False, "rgbwm": 0,
        "freq": 0, "maxpwr": 0, "ledma": 30, "drv": 0,
    }]}}}
    payload = migration.build_config_payload(cfg, [(16, 0, 34), (2, 34, 48)])
    first = payload["hw"]["led"]["ins"][0]
    assert first["len"] == 34
    assert first["order"] == 2
    assert first["ledma"] == 30


def test_apply_refuses_without_complete_backup(tmp_path):
    transport = FakeTransport(fail_path="/json/pal")
    with pytest.raises(RuntimeError, match="backup incomplete"):
        migration.migrate(transport, tmp_path, apply=True)
    assert transport.posts == []


def test_readback_requires_calibrated_pixels_and_brg():
    with pytest.raises(RuntimeError, match="readback mismatch"):
        migration.verify_config({"hw": {"led": {"ins": [
            {"start": 0, "len": 40, "pin": [16], "order": 1},
            {"start": 40, "len": 40, "pin": [2], "order": 2},
        ]}}}, expected_pins=[16, 2])


def test_build_bus_payload_matches_expected_topology():
    for host, pins in migration.HOST_PINS.items():
        cfg = make_cfg(pins)
        pin_starts = migration.HOST_BUSES[host]
        payload = migration.build_config_payload(cfg, pin_starts)
        buses = payload["hw"]["led"]["ins"]
        assert len(buses) == len(EXPECTED[host])
        for expected_bus, actual_bus in zip(EXPECTED[host], buses):
            for key, value in expected_bus.items():
                assert actual_bus[key] == value


def test_build_bus_payload_rejects_unverified_gpio():
    cfg = {"hw": {"led": {"ins": [
        {"start": 0, "len": 80, "pin": [4], "order": 0},
    ]}}}
    with pytest.raises(RuntimeError, match="verified GPIO"):
        migration.build_config_payload(cfg, [(16, 0, 34), (2, 34, 48)])


def test_verify_config_accepts_expected_readback():
    cfg = {"hw": {"led": {"ins": [
        {"start": 0, "len": 34, "pin": [16], "order": 2},
        {"start": 34, "len": 48, "pin": [2], "order": 2},
    ]}}}
    migration.verify_config(cfg, expected_pins=[16, 2])


def test_dry_run_backs_up_and_posts_nothing(tmp_path):
    transport = FakeTransport()
    migration.migrate(transport, tmp_path, apply=False)
    assert transport.posts == []
    for host in migration.HOST_PINS:
        tag = host.split("://", 1)[1]
        for name in ("cfg", "state", "info", "eff", "pal"):
            data = json.loads((tmp_path / tag / f"{name}.json").read_text())
            assert data


def test_apply_posts_expected_payloads_and_verifies(tmp_path):
    transport = FakeTransport()
    migration.migrate(transport, tmp_path, apply=True)
    assert [p[1] for p in transport.posts] == [
        "/json/cfg",
        "/json/state",
        "/json/cfg",
        "/json/state",
    ]
    cfg_posts = [p for p in transport.posts if p[1] == "/json/cfg"]
    for host, _, payload in cfg_posts:
        for expected_bus, actual_bus in zip(
            EXPECTED[host], payload["hw"]["led"]["ins"]
        ):
            for key, value in expected_bus.items():
                assert actual_bus[key] == value
    state_posts = [p for p in transport.posts if p[1] == "/json/state"]
    for host, _, payload in state_posts:
        assert payload == migration.HOST_SEGMENT_PAYLOADS[host]


def test_restore_replays_backup_and_verifies(tmp_path):
    transport = FakeTransport()
    migration.migrate(transport, tmp_path, apply=True)
    transport.posts.clear()
    migration.restore(transport, tmp_path)
    cfg_posts = [p for p in transport.posts if p[1] == "/json/cfg"]
    assert len(cfg_posts) == 2
    for _, _, payload in cfg_posts:
        ins = payload["hw"]["led"]["ins"]
        assert [bus["len"] for bus in ins] == [80, 80]
        assert [bus["order"] for bus in ins] == [0, 0]
    state_posts = [p for p in transport.posts if p[1] == "/json/state"]
    assert len(state_posts) == 2
    for _, _, payload in state_posts:
        assert payload["seg"][0]["stop"] == 160
