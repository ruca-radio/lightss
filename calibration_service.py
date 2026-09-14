"""Reviewed, fail-closed calibration staging for the local WLED fleet."""

from __future__ import annotations

import copy
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import calibrate
import fleet
import lightctl


_LIMITATIONS = [
    "A scan reports controller-declared topology; it cannot prove physical strip order or orientation.",
    "Webcam guidance is optional and advisory; this service does not access a camera or overwrite mappings from images.",
    "GPIOs, bus types, power limits, and other electrical settings are never written to a controller.",
]


class CalibrationService:
    """Hold short-lived scan proposals and apply them only after fresh validation."""

    TOKEN_TTL_SECONDS = 300.0
    MAX_STAGED_SCANS = 8
    IDENTIFY_SECONDS = 1.0

    def __init__(self, client: Any, config_path: str | None = None) -> None:
        self.client = client
        self.config_path = os.path.abspath(os.path.expanduser(config_path or lightctl._CONFIG_PATH))
        self._lock = threading.RLock()
        self._staged: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._clock = time.monotonic
        self._sleep = time.sleep

    def _read_config_bytes(self) -> bytes:
        try:
            return Path(self.config_path).read_bytes()
        except FileNotFoundError:
            return b""

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _load_config(self, raw: bytes | None = None) -> dict:
        raw = self._read_config_bytes() if raw is None else raw
        if not raw:
            return {}
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("local config must contain a JSON object")
        return value

    def _fleet_parts(self) -> tuple[dict[str, Any], list[fleet.ControllerConfig], fleet.InstallationConfig]:
        clients = getattr(self.client, "clients", None)
        controllers = getattr(self.client, "controllers", None)
        installation = getattr(self.client, "installation", None)
        if not isinstance(clients, dict) or not isinstance(controllers, list):
            raise ValueError("calibration requires a configured fleet")
        if not isinstance(installation, fleet.InstallationConfig):
            installation = fleet.InstallationConfig()
        names = [controller.name for controller in controllers]
        if len(names) != len(set(names)) or set(names) != set(clients):
            raise ValueError("fleet client/controller membership is invalid")
        return clients, controllers, installation

    @staticmethod
    def _hardware_facts(probes: dict[str, dict]) -> dict[str, dict]:
        facts: dict[str, dict] = {}
        for name, probe in probes.items():
            if "error" in probe:
                facts[name] = {"error": probe["error"]}
                continue
            facts[name] = {
                "host": probe.get("host"),
                "name": probe.get("name"),
                "version": probe.get("version"),
                "led_count": probe.get("led_count"),
                "maxseg": probe.get("maxseg"),
                "buses": probe.get("buses") or [],
                "segments": [
                    {key: segment.get(key) for key in ("id", "start", "stop", "pixels", "gpio")}
                    for segment in (probe.get("segments") or [])
                ],
            }
        return facts

    @staticmethod
    def _proposal(config: dict, installation: fleet.InstallationConfig, controllers: list[fleet.ControllerConfig]) -> dict:
        installation_json = copy.deepcopy(config.get("installation"))
        if not isinstance(installation_json, dict):
            # Do not turn dataclass defaults into purportedly observed geometry.
            installation_json = {}
        raw_controllers = config.get("controllers") if isinstance(config.get("controllers"), list) else []
        by_name = {
            str(item.get("name")): item
            for item in raw_controllers
            if isinstance(item, dict) and item.get("name")
        }
        merged_controllers = []
        for controller in controllers:
            generated = calibrate._controller_to_json(controller)
            prior = copy.deepcopy(by_name.get(controller.name) or {})
            prior_segments = prior.get("segments") if isinstance(prior.get("segments"), dict) else {}
            merged_segments = {}
            for seg_id, geometry in generated["segments"].items():
                segment = copy.deepcopy(prior_segments.get(seg_id) or {})
                segment.update(geometry)
                merged_segments[seg_id] = segment
            prior.update(name=generated["name"], host=generated["host"], segments=merged_segments)
            merged_controllers.append(prior)
        return {
            "installation": installation_json,
            "controllers": merged_controllers,
        }

    @staticmethod
    def _membership_errors(config: dict, controllers: list[fleet.ControllerConfig]) -> list[str]:
        raw = config.get("controllers")
        if raw is None or raw == []:
            return []  # effective fleet may legitimately come from env/built-in defaults
        if not isinstance(raw, list):
            return ["disk controller membership is malformed"]
        disk: list[tuple[str, str]] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("name") or not item.get("host"):
                return ["disk controller membership is malformed"]
            disk.append((str(item["name"]), lightctl.normalize_host(str(item["host"]))))
        runtime = [(item.name, lightctl.normalize_host(item.host)) for item in controllers]
        if len(disk) != len(set(disk)) or set(disk) != set(runtime):
            return ["disk controller membership does not match the runtime fleet"]
        return []

    def _scan_facts(self, config: dict) -> tuple[dict, dict, list[str]]:
        clients, existing, installation = self._fleet_parts()
        probes = calibrate.collect_probes(clients=clients)
        errors: list[str] = self._membership_errors(config, existing)
        for controller in existing:
            probe = probes.get(controller.name)
            if not probe or "error" in probe:
                errors.append(f"controller {controller.name!r} is offline or unreadable")
            elif not probe.get("segments"):
                errors.append(f"controller {controller.name!r} reported no valid segments")
            elif probe.get("validation_errors"):
                errors.extend(
                    f"controller {controller.name!r}: {error}"
                    for error in probe["validation_errors"]
                )
        if set(probes) != {controller.name for controller in existing}:
            errors.append("probe results do not match the configured fleet")
        proposed_controllers = calibrate.build_controllers(probes, existing)
        proposal = self._proposal(config, installation, proposed_controllers)
        if len(proposed_controllers) != len(existing):
            errors.append("proposal would omit one or more configured controllers")
        try:
            candidate = copy.deepcopy(config)
            candidate.update(copy.deepcopy(proposal))
            fleet.load_topology(candidate)
        except (TypeError, ValueError) as exc:
            errors.append(f"proposed topology is invalid: {exc}")
        return probes, proposal, errors

    def _base(self, *, ok: bool, message: str, token: str | None, probes: dict, proposed: dict, can_apply: bool) -> dict:
        return {
            "ok": ok,
            "message": message,
            "token": token,
            "probes": probes,
            "proposed": proposed,
            "can_apply": can_apply,
            "limitations": list(_LIMITATIONS),
        }

    def _purge(self, now: float) -> None:
        expired = [token for token, item in self._staged.items() if now - item["created"] > self.TOKEN_TTL_SECONDS]
        for token in expired:
            self._staged.pop(token, None)

    def scan(self) -> dict:
        with self._lock:
            try:
                raw = self._read_config_bytes()
                config = self._load_config(raw)
                probes, proposal, errors = self._scan_facts(config)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._base(ok=False, message=f"Calibration scan failed: {exc}", token=None, probes={}, proposed={}, can_apply=False)
            if errors:
                return self._base(ok=False, message="; ".join(errors), token=None, probes=probes, proposed=proposal, can_apply=False)
            now = self._clock()
            self._purge(now)
            token = secrets.token_urlsafe(24)
            self._staged[token] = {
                "created": now,
                "config_digest": self._digest(raw),
                "hardware": self._hardware_facts(probes),
                "probes": copy.deepcopy(probes),
                "proposal": copy.deepcopy(proposal),
            }
            while len(self._staged) > self.MAX_STAGED_SCANS:
                self._staged.popitem(last=False)
            return self._base(ok=True, message="Scan complete. Review the proposal before applying it.", token=token, probes=probes, proposed=proposal, can_apply=True)

    def observe(self) -> dict:
        """Read and validate current facts without creating or changing review tokens."""
        with self._lock:
            try:
                raw = self._read_config_bytes()
                config = self._load_config(raw)
                probes, proposal, errors = self._scan_facts(config)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._base(
                    ok=False,
                    message=f"Calibration observation failed: {exc}",
                    token=None,
                    probes={},
                    proposed={},
                    can_apply=False,
                )
            if errors:
                return self._base(
                    ok=False,
                    message="; ".join(errors),
                    token=None,
                    probes=probes,
                    proposed=proposal,
                    can_apply=False,
                )
            return self._base(
                ok=True,
                message="Current controller facts observed without staging an apply token.",
                token=None,
                probes=probes,
                proposed=proposal,
                can_apply=False,
            )

    def _write_atomic(self, config: dict, previous: bytes) -> str | None:
        directory = os.path.dirname(self.config_path) or "."
        os.makedirs(directory, exist_ok=True)
        backup_path = None
        if previous:
            for _ in range(10):
                candidate = f"{self.config_path}.bak-{time.time_ns()}"
                try:
                    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    continue
                with os.fdopen(fd, "wb") as handle:
                    handle.write(previous)
                    handle.flush()
                    os.fsync(handle.fileno())
                backup_path = candidate
                break
            if backup_path is None:
                raise OSError("could not create a unique config backup")
        fd, temporary = tempfile.mkstemp(prefix=".config-calibration-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(config, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.config_path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return backup_path

    @contextlib.contextmanager
    def _config_lock(self):
        """Serialize cooperating calibration writers across processes.

        Legacy config writers do not take this lock, so the digest recheck below
        remains a best-effort optimistic guard for those callers.
        """
        lock_path = self.config_path + ".calibration.lock"
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        with open(lock_path, "a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def apply(self, token: str) -> dict:
        with self._lock:
            now = self._clock()
            item = self._staged.pop(str(token or ""), None)
            if item is None:
                return self._base(ok=False, message="Unknown or already-used scan token.", token=None, probes={}, proposed={}, can_apply=False) | {"written": False, "config_backup": None}
            if now - item["created"] > self.TOKEN_TTL_SECONDS:
                return self._base(ok=False, message="Scan token expired; scan again.", token=None, probes=item["probes"], proposed=item["proposal"], can_apply=False) | {"written": False, "config_backup": None}
            try:
                with self._config_lock():
                    previous = self._read_config_bytes()
                    if self._digest(previous) != item["config_digest"]:
                        raise ValueError("Local config changed after the scan; scan again.")
                    config = self._load_config(previous)
                    probes, proposal, errors = self._scan_facts(config)
                    if errors:
                        raise ValueError("Fresh hardware/fleet validation failed: " + "; ".join(errors))
                    if self._hardware_facts(probes) != item["hardware"]:
                        raise ValueError("Hardware facts changed after the scan; scan again.")
                    if proposal != item["proposal"]:
                        raise ValueError("Proposed topology changed after the scan; scan again.")
                    if self._digest(self._read_config_bytes()) != item["config_digest"]:
                        raise ValueError("Local config changed during validation; scan again.")
                    updated = copy.deepcopy(config)
                    updated["controllers"] = copy.deepcopy(item["proposal"]["controllers"])
                    # Installation is intentionally preserved: physical geometry and electrical
                    # properties cannot be inferred safely from controller declarations.
                    backup = self._write_atomic(updated, previous)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._base(ok=False, message=str(exc), token=None, probes=item["probes"], proposed=item["proposal"], can_apply=False) | {"written": False, "config_backup": None}
            return self._base(ok=True, message="Calibration topology applied.", token=None, probes=probes, proposed=item["proposal"], can_apply=False) | {"written": True, "config_backup": backup}

    def _renderer_active(self) -> bool:
        try:
            import realtime

            if realtime.realtime_status().get("running"):
                return True
        except Exception:
            # Optional status introspection must not make identification unusable.
            pass
        for name in ("realtime_running", "renderer_running", "dynamic_effect_running"):
            value = getattr(self.client, name, False)
            try:
                value = value() if callable(value) else value
            except Exception:
                return True
            if value:
                return True
        return False

    def identify(self, controller: str, segment: int) -> dict:
        result = {"ok": False, "message": "", "controller": controller, "segment": segment, "limitations": list(_LIMITATIONS)}
        with self._lock:
            try:
                clients, controllers, _installation = self._fleet_parts()
                name = str(controller or "").strip()
                if not name:
                    raise ValueError("An explicit controller is required.")
                try:
                    segment_id = int(segment)
                except (TypeError, ValueError):
                    raise ValueError("An integer segment is required.") from None
                configured = next((item for item in controllers if item.name == name), None)
                if configured is None or segment_id not in configured.segments:
                    raise ValueError("The selected controller/segment is not configured.")
                if self._renderer_active():
                    raise ValueError("Stop the active renderer before identifying a segment.")
                poster = clients[name]
                prior = poster.get_state()
                if not isinstance(prior, dict):
                    raise ValueError("Could not read prior controller state.")
                prior_segments = prior.get("seg") if isinstance(prior.get("seg"), list) else []
                any_frozen = any(isinstance(item, dict) and item.get("frz") is True for item in prior_segments)
                if prior.get("live") is True or any_frozen:
                    raise ValueError("Stop realtime/live or frozen per-pixel output before identification.")
                marker = {
                    "on": True,
                    "bri": 24,
                    "seg": [{"id": segment_id, "on": True, "bri": 24, "fx": 0, "col": [[255, 160, 32, 0]]}],
                    "udpn": {"nn": True},
                }
                try:
                    poster.post_state(marker)
                    self._sleep(self.IDENTIFY_SECONDS)
                finally:
                    restore = {key: copy.deepcopy(prior[key]) for key in ("on", "bri", "seg") if key in prior}
                    restore["udpn"] = {"nn": True}
                    poster.post_state(restore)
                result.update(ok=True, message="Low-brightness identification marker displayed and prior state restored.", segment=segment_id)
            except Exception as exc:
                result["message"] = f"Identification failed: {exc}"
            return result
