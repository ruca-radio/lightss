"""Fleet and GUI own the lifetime of their read-only controller observers."""
from unittest.mock import patch

import fleet
import light_gui
import lightctl


class LifecycleClient:
    def __init__(self, host="http://127.0.0.1"):
        self.host = host
        self.started = 0
        self.closed = 0

    def start_observing(self):
        self.started += 1

    def close(self):
        self.closed += 1


def test_fleet_construction_does_not_open_observer_network():
    device = LifecycleClient()
    wall = fleet.LightFleet({"left": device})
    assert device.started == 0
    wall.start_observing()
    assert device.started == 1
    wall.close()


def test_fleet_close_releases_each_shared_client_once():
    device = LifecycleClient()
    wall = fleet.LightFleet({"left": device, "alias": device})
    wall.close()
    wall.close()
    assert device.closed == 1
    wall.start_observing()
    assert device.started == 0


def test_fleet_accepts_fake_clients_without_lifecycle_methods():
    class Fake:
        host = "http://127.0.0.1"
    wall = fleet.LightFleet({"fake": Fake()})
    wall.start_observing()
    wall.close()


def test_production_factory_opts_into_python_wled_without_starting_io():
    controller = fleet.ControllerConfig("left", "http://127.0.0.1")
    with patch.object(fleet, "load_topology", return_value=(fleet.InstallationConfig(), [controller])):
        wall = fleet.LightFleet.from_config(dry_run=True)
    try:
        assert wall.clients["left"].use_python_wled is True
        assert getattr(wall.clients["left"], "_wled_observer", None) is None
    finally:
        wall.close()


def test_gui_initialization_starts_controller_observation():
    device, intel = LifecycleClient(), LifecycleClient()
    state = light_gui.GuiState(device, info_client=intel)
    try:
        assert device.started == 1
        assert intel.started == 1
    finally:
        state.shutdown()


def test_dry_run_gui_does_not_start_observers():
    device = LifecycleClient()
    state = light_gui.GuiState(device, dry_run=True)
    try:
        assert device.started == 0
    finally:
        state.shutdown()


def test_gui_shutdown_closes_deduplicated_controller_once():
    device = LifecycleClient()
    state = light_gui.GuiState(device, dry_run=True)
    state.shutdown()
    state.shutdown()
    assert device.closed == 1


def test_gui_reload_closes_replaced_clients_and_preserves_replacement():
    previous, old_intel = LifecycleClient(), LifecycleClient()
    replacement, new_intel = LifecycleClient(), LifecycleClient()
    state = light_gui.GuiState(previous, info_client=old_intel, dry_run=True)
    with (
        patch.object(light_gui, "_build_fleet_client", return_value=(replacement, new_intel)),
        patch.object(light_gui, "start_configured_smart_director"),
    ):
        state.reload_controllers()
    try:
        assert previous.closed == 1
        assert old_intel.closed == 1
        assert replacement.closed == 0
        assert new_intel.closed == 0
    finally:
        state.shutdown()
    assert replacement.closed == 1
    assert new_intel.closed == 1


def test_reload_failure_closes_unused_replacement_observers():
    previous, replacement, intel = LifecycleClient(), LifecycleClient(), LifecycleClient()
    state = light_gui.GuiState(previous, dry_run=True)
    try:
        with (
            patch.object(light_gui, "_build_fleet_client", return_value=(replacement, intel)),
            patch.object(light_gui.lightctl, "ReactiveThread", side_effect=RuntimeError("worker setup failed")),
        ):
            import pytest
            with pytest.raises(RuntimeError, match="worker setup failed"):
                state.reload_controllers()
        assert previous.closed == 0
        assert replacement.closed == 1
        assert intel.closed == 1
    finally:
        state.shutdown()


def test_shutdown_releases_replacement_from_in_progress_reload():
    import threading
    previous, replacement = LifecycleClient(), LifecycleClient()
    state = light_gui.GuiState(previous, dry_run=True)
    building = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    failures = []

    def build(**_kwargs):
        building.set()
        assert release.wait(2)
        return replacement, None

    def reload():
        try:
            state.reload_controllers()
        except Exception as exc:
            failures.append(exc)

    def shutdown():
        state.shutdown()
        stopped.set()

    with (
        patch.object(light_gui, "_build_fleet_client", side_effect=build),
        patch.object(light_gui, "start_configured_smart_director"),
    ):
        loader = threading.Thread(target=reload)
        loader.start()
        assert building.wait(2)
        stopper = threading.Thread(target=shutdown)
        stopper.start()
        stopped.wait(0.1)  # Give a concurrent shutdown opportunity while reload is blocked.
        release.set()
        loader.join(2)
        stopper.join(2)
    try:
        assert not loader.is_alive()
        assert not stopper.is_alive()
        assert not failures
        assert replacement.closed == 1
    finally:
        state.shutdown()


def test_gui_info_reads_share_the_primary_fleet_observer():
    controller = fleet.ControllerConfig("left", "http://127.0.0.1")
    with patch.object(fleet, "load_topology", return_value=(fleet.InstallationConfig(), [controller])):
        wall, intel = light_gui._build_fleet_client(dry_run=True)
    try:
        assert intel is wall.clients["left"]
    finally:
        wall.close()
