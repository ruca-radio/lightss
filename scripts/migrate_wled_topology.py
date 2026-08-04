#!/usr/bin/env python3
"""Backup-first live WLED 16.x topology migration utility.

Migrates the two verified controllers to two WS2811 buses of 40 pixels
each (starts 0 and 40, BRG color order) and repairs the segments to
(0, 40) / (40, 80).

Safety invariants:
- never POST without a complete validated backup of /json/cfg, /json/state,
  /json/info, /json/eff and /json/pal for BOTH hosts;
- default mode is a dry-run that only backs up and prints the planned diff;
- writes require --apply;
- buses are matched by verified GPIO, never by array position;
- all existing bus fields are preserved; only start/len/order change;
- --restore replays the backed-up hw.led config + state and verifies readback;
- no strobe/blink/flash effects are ever sent.

Stdlib only. Usage:
    python scripts/migrate_wled_topology.py                  # dry-run + backup
    python scripts/migrate_wled_topology.py --apply          # backup, then write
    python scripts/migrate_wled_topology.py --restore DIR    # roll back from backup
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# WLED 16 authoritative color-order enum (wled00/const.h): COL_ORDER_BRG = 2.
WLED_BRG_ORDER = 2

BUS_LENGTH = 40
TOTAL_PIXELS = 80
BUS_STARTS = (0, 40)

HOST_PINS = {
    "http://10.27.27.110": [16, 2],
    "http://10.27.27.112": [2, 16],
}

SEGMENT_PAYLOAD = {
    "seg": [
        {"id": 0, "start": 0, "stop": 40, "on": True, "fx": 0},
        {"id": 1, "start": 40, "stop": 80, "on": True, "fx": 0},
    ],
    "udpn": {"nn": True},
}

BACKUP_ENDPOINTS = ("/json/cfg", "/json/state", "/json/info", "/json/eff", "/json/pal")

WAIT_ATTEMPTS = 30
WAIT_DELAY_S = 1.0


class HttpTransport:
    """Real HTTP transport (stdlib urllib). Tests substitute a fake."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def get(self, host: str, path: str):
        req = urllib.request.Request(host + path)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post(self, host: str, path: str, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            host + path, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text) if text.strip() else {}


def _endpoint_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _host_tag(host: str) -> str:
    return host.split("://", 1)[1]


def _pin_starts(pins) -> list[tuple[int, int]]:
    return [(pin, idx * BUS_LENGTH) for idx, pin in enumerate(pins)]


def build_config_payload(cfg: dict, pin_starts: list[tuple[int, int]]) -> dict:
    """Build the partial /json/cfg envelope for the target topology.

    Buses are matched by verified GPIO (pin[0]), never by array position.
    Every existing bus GPIO must be covered by ``pin_starts`` and vice versa.
    All existing bus fields are preserved; only start/len/order change.
    """
    try:
        led = cfg["hw"]["led"]
        buses = led["ins"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"config missing hw.led.ins: {exc}") from exc

    wanted = dict(pin_starts)
    by_gpio = {}
    for bus in buses:
        gpio = bus.get("pin", [None])[0]
        if gpio in by_gpio:
            raise RuntimeError(f"duplicate bus on verified GPIO {gpio}")
        by_gpio[gpio] = bus

    unknown = sorted(gpio for gpio in by_gpio if gpio not in wanted)
    if unknown:
        raise RuntimeError(
            "config contains buses not in the verified GPIO set "
            f"{sorted(wanted)}: unexpected GPIOs {unknown}"
        )

    new_ins = []
    for gpio, start in pin_starts:
        if gpio in by_gpio:
            bus = copy.deepcopy(by_gpio[gpio])
        else:
            bus = {"pin": [gpio]}
        bus["start"] = start
        bus["len"] = BUS_LENGTH
        bus["order"] = WLED_BRG_ORDER
        new_ins.append(bus)

    new_led = copy.deepcopy(led)
    new_led["ins"] = new_ins
    return {"hw": {"led": new_led}}


def verify_config(cfg: dict, expected_pins) -> None:
    """Require two buses: starts [0, 40], len 40, BRG order, expected pins."""
    try:
        buses = cfg["hw"]["led"]["ins"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"readback mismatch: config missing hw.led.ins ({exc})") from exc

    problems = []
    if len(buses) != len(BUS_STARTS):
        problems.append(f"expected {len(BUS_STARTS)} buses, got {len(buses)}")
    else:
        by_start = {bus.get("start"): bus for bus in buses}
        for start, pin in zip(BUS_STARTS, expected_pins):
            bus = by_start.get(start)
            if bus is None:
                problems.append(f"no bus at start {start}")
                continue
            if bus.get("len") != BUS_LENGTH:
                problems.append(f"bus@{start} len={bus.get('len')} (want {BUS_LENGTH})")
            if bus.get("order") != WLED_BRG_ORDER:
                problems.append(
                    f"bus@{start} order={bus.get('order')} (want {WLED_BRG_ORDER})"
                )
            if bus.get("pin", [None])[0] != pin:
                problems.append(f"bus@{start} pin={bus.get('pin')} (want [{pin}])")
    if problems:
        raise RuntimeError("readback mismatch: " + "; ".join(problems))


def verify_state(state: dict) -> None:
    """Require exactly the repaired segments (0, 40) and (40, 80)."""
    expected = [(seg["start"], seg["stop"]) for seg in SEGMENT_PAYLOAD["seg"]]
    segs = state.get("seg") or []
    actual = [(s.get("start"), s.get("stop")) for s in segs]
    if sorted(actual) != expected:
        raise RuntimeError(
            f"readback mismatch: segments {actual} (want {expected})"
        )


def verify_info(info: dict) -> None:
    count = (info.get("leds") or {}).get("count")
    if count != TOTAL_PIXELS:
        raise RuntimeError(
            f"readback mismatch: info.leds.count={count} (want {TOTAL_PIXELS})"
        )


def backup_host(transport, host: str, backup_dir: Path) -> None:
    host_dir = backup_dir / _host_tag(host)
    host_dir.mkdir(parents=True, exist_ok=True)
    for path in BACKUP_ENDPOINTS:
        try:
            data = transport.get(host, path)
        except Exception as exc:
            raise RuntimeError(
                f"backup incomplete: GET {host}{path} failed: {exc}"
            ) from exc
        (host_dir / f"{_endpoint_name(path)}.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n"
        )


def validate_backups(backup_dir: Path, hosts) -> None:
    problems = []
    for host in hosts:
        host_dir = backup_dir / _host_tag(host)
        for path in BACKUP_ENDPOINTS:
            fpath = host_dir / f"{_endpoint_name(path)}.json"
            try:
                json.loads(fpath.read_text())
            except Exception as exc:
                problems.append(f"{fpath}: {exc}")
    if problems:
        raise RuntimeError("backup incomplete: " + "; ".join(problems))


def wait_for_device(transport, host: str) -> None:
    """Bounded retry loop until the device answers /json/info again."""
    last = None
    for _ in range(WAIT_ATTEMPTS):
        try:
            transport.get(host, "/json/info")
            return
        except Exception as exc:  # device rebooting after cfg write
            last = exc
            time.sleep(WAIT_DELAY_S)
    raise RuntimeError(f"{host} did not come back after config write: {last}")


def readback_verify(transport, host: str, pins) -> None:
    cfg = transport.get(host, "/json/cfg")
    state = transport.get(host, "/json/state")
    info = transport.get(host, "/json/info")
    verify_config(cfg, expected_pins=pins)
    verify_state(state)
    verify_info(info)


def print_plan(host: str, cfg: dict, payload: dict) -> None:
    print(f"\n{host} planned bus diff:")
    old = {bus.get("pin", [None])[0]: bus for bus in cfg["hw"]["led"]["ins"]}
    for bus in payload["hw"]["led"]["ins"]:
        gpio = bus["pin"][0]
        prev = old.get(gpio, {})
        print(
            f"  GPIO {gpio}: start {prev.get('start')} -> {bus['start']}, "
            f"len {prev.get('len')} -> {bus['len']}, "
            f"order {prev.get('order')} -> {bus['order']}"
        )


def migrate(transport, backup_dir, apply: bool = False) -> None:
    """Back up both hosts, then (only with apply=True) rewrite the topology."""
    backup_dir = Path(backup_dir)
    for host in HOST_PINS:
        backup_host(transport, host, backup_dir)
    validate_backups(backup_dir, HOST_PINS)
    print(f"backups complete and validated under {backup_dir}")

    cfgs = {host: transport.get(host, "/json/cfg") for host in HOST_PINS}
    payloads = {
        host: build_config_payload(cfgs[host], _pin_starts(pins))
        for host, pins in HOST_PINS.items()
    }

    if not apply:
        for host in HOST_PINS:
            print_plan(host, cfgs[host], payloads[host])
        print("\ndry-run: no writes performed (use --apply to write)")
        return

    for host, pins in HOST_PINS.items():
        transport.post(host, "/json/cfg", payloads[host])
        wait_for_device(transport, host)
        transport.post(host, "/json/state", copy.deepcopy(SEGMENT_PAYLOAD))
        readback_verify(transport, host, pins)
        print(f"{host} migrated and verified")


def _subset_mismatches(expected: dict, actual: dict, prefix: str = "") -> list[str]:
    problems = []
    for key, want in expected.items():
        got = actual.get(key, "<missing>") if isinstance(actual, dict) else "<missing>"
        label = f"{prefix}{key}"
        if isinstance(want, dict) and isinstance(got, dict):
            problems.extend(_subset_mismatches(want, got, prefix=label + "."))
        elif want != got:
            problems.append(f"{label}: backup={want!r} device={got!r}")
    return problems


def restore(transport, backup_dir) -> None:
    """Replay the backed-up hw.led config and state, then verify readback."""
    backup_dir = Path(backup_dir)
    validate_backups(backup_dir, HOST_PINS)
    for host in HOST_PINS:
        host_dir = backup_dir / _host_tag(host)
        cfg = json.loads((host_dir / "cfg.json").read_text())
        state = json.loads((host_dir / "state.json").read_text())

        transport.post(host, "/json/cfg", {"hw": {"led": cfg["hw"]["led"]}})
        wait_for_device(transport, host)
        transport.post(host, "/json/state", state)

        now_cfg = transport.get(host, "/json/cfg")
        now_state = transport.get(host, "/json/state")
        problems = _subset_mismatches(cfg["hw"]["led"], now_cfg["hw"]["led"], "hw.led.")
        problems += _subset_mismatches(state, now_state, "state.")
        if problems:
            raise RuntimeError("readback mismatch: " + "; ".join(problems))
        print(f"{host} restored and verified from {host_dir}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Backup-first WLED topology migration (dry-run by default)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write to the controllers (default: dry-run)",
    )
    parser.add_argument(
        "--restore",
        metavar="BACKUP_DIR",
        help="restore both controllers from a previous backup directory",
    )
    parser.add_argument(
        "--backup-dir",
        metavar="DIR",
        default=None,
        help="where to write backups (default: wled-backup-<timestamp>)",
    )
    args = parser.parse_args(argv)

    transport = HttpTransport()
    try:
        if args.restore:
            restore(transport, Path(args.restore))
        else:
            backup_dir = Path(
                args.backup_dir
                or f"wled-backup-{datetime.now():%Y%m%d-%H%M%S}"
            )
            migrate(transport, backup_dir, apply=args.apply)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
