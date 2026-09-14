from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

import fleet
import idle_curator
import lightctl
import pytest


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(lightctl, "_SCENE_DIR", str(tmp_path))


@pytest.fixture
def fake_config(monkeypatch):
    config: dict = {}
    monkeypatch.setattr(lightctl, "load_config", lambda: config)
    return config


@pytest.fixture
def idle_sources(monkeypatch):
    """Every competing wall writer reports idle; flip `busy` to simulate activity."""

    class Alive:
        def is_alive(self):
            return True

    toggles = {name: {"busy": False} for name in ("smart_director", "realtime", "shows", "music")}

    def current():
        return Alive() if toggles["smart_director"]["busy"] else None

    monkeypatch.setattr("smart_director.current", current)
    monkeypatch.setattr("realtime.realtime_status", lambda: {"running": toggles["realtime"]["busy"]})
    monkeypatch.setattr("shows.show_status", lambda: {"running": toggles["shows"]["busy"]})
    monkeypatch.setattr("music_director.director_status", lambda: {"running": toggles["music"]["busy"]})
    return toggles


@pytest.fixture
def ops(monkeypatch):
    """Record curator output decisions in call order."""
    record: list[tuple] = []

    def fake_realtime_start(fleet_client, **kwargs):
        record.append(("realtime", kwargs))
        return "Realtime started"

    def fake_apply_scene(fleet_client, **kwargs):
        record.append(("scene", kwargs))
        return {}

    monkeypatch.setattr("realtime.realtime_start", fake_realtime_start)
    monkeypatch.setattr("dynamic_scenes.apply_dynamic_scene", fake_apply_scene)
    return record


class RecordingFleet:
    def __init__(self, ops=None):
        self.installation, self.controllers = fleet.load_topology({})
        self.ops = ops if ops is not None else []

    def channels(self):
        return {}

    def resolve(self, target):
        return [(target, None)]

    def names(self):
        return [controller.name for controller in self.controllers]

    def effect_ids(self, controller):
        return None

    def post_state(self, payload, target="all"):
        self.ops.append(("post", target, payload))
        return {target: {"ok": True}}


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_curator(fleet_client, config=None, clock=None, seed=7, wall_clock=None):
    merged = {"dynamic_scene_chance": 0.0, **(config or {})}
    return idle_curator.IdleCurator(
        fleet_client,
        merged,
        clock=clock or FakeClock(),
        rng=random.Random(seed),
        wall_clock=wall_clock,
    )


def test_mood_bank_is_large_varied_and_lowercase():
    assert len(idle_curator.MOODS) >= 16
    assert len(set(idle_curator.MOODS)) == len(idle_curator.MOODS)
    assert all(mood == mood.strip().lower() for mood in idle_curator.MOODS)


def test_validate_config_fills_defaults_and_rejects_bad_values():
    assert idle_curator.validate_config({}) == idle_curator.DEFAULTS
    with pytest.raises(ValueError):
        idle_curator.validate_config({"min_cycle_s": 900, "max_cycle_s": 300})
    with pytest.raises(ValueError):
        idle_curator.validate_config({"dynamic_scene_chance": 1.5})
    with pytest.raises(ValueError):
        idle_curator.validate_config({"backoff_s": -5})
    with pytest.raises(ValueError):
        idle_curator.validate_config({"max_cycle_s": 5})


@pytest.mark.parametrize("source", ["smart_director", "realtime", "shows", "music"])
def test_curator_skips_when_any_writer_is_active(idle_sources, ops, source):
    idle_sources[source]["busy"] = True
    f = RecordingFleet(ops)
    make_curator(f)._tick()
    assert ops == []


def test_idle_curator_picks_varied_moods_and_seeds(idle_sources, ops, fake_config):
    f = RecordingFleet(ops)
    c = make_curator(f, config={"min_cycle_s": 120, "max_cycle_s": 120})
    for _ in range(10):
        c._tick()
    starts = [op[1] for op in ops if op[0] == "realtime"]
    assert len(starts) == 10
    assert len({s["mood"] for s in starts}) >= 3
    assert len({s["seed"] for s in starts}) == 10
    assert all(s["mood"] in idle_curator.MOODS for s in starts)
    assert all(s["duration_s"] == 120 for s in starts)
    assert all(s.get("composition_mode") is None for s in starts)


def test_curator_wakes_wall_with_scheduled_brightness_before_the_look(idle_sources, ops, fake_config):
    f = RecordingFleet(ops)
    c = make_curator(f)
    c._tick()
    posts = [op for op in ops if op[0] == "post"]
    assert posts, "curator must wake the wall before starting a look"
    expected = {"on": True, "bri": c.scheduled_brightness()}
    assert all(payload == expected for _, _, payload in posts)
    assert ops[-1][0] == "realtime"


def test_scheduled_brightness_follows_the_smart_director_schedule(idle_sources, ops, fake_config):
    fake_config["smart_director"] = {
        "day_brightness": 0.9,
        "evening_brightness": 0.4,
        "night_brightness": 0.1,
    }
    f = RecordingFleet(ops)
    wall_clock = lambda: datetime(2026, 9, 14, 19, 0, tzinfo=ZoneInfo("America/Detroit"))
    c = make_curator(f, wall_clock=wall_clock)
    c._tick()
    posts = [op for op in ops if op[0] == "post"]
    assert posts
    assert all(payload == {"on": True, "bri": round(0.4 * 255)} for _, _, payload in posts)


def test_scheduled_brightness_falls_back_when_config_is_unusable(idle_sources, ops, fake_config):
    fake_config["smart_director"] = {"evening_brightness": "bright"}
    f = RecordingFleet(ops)
    c = make_curator(f)
    assert 1 <= c.scheduled_brightness() <= 255


def test_manual_activity_backs_off_the_curator(idle_sources, ops, fake_config):
    f = RecordingFleet(ops)
    clock = FakeClock()
    c = make_curator(f, config={"backoff_s": 1800}, clock=clock)
    c.note_manual_activity()
    c._tick()
    assert ops == []
    clock.advance(1801)
    c._tick()
    assert [kind for kind, *_ in ops if kind in {"realtime", "scene"}] == ["realtime"]


def test_dynamic_scene_one_shots_replace_realtime_at_full_chance(idle_sources, ops, fake_config):
    f = RecordingFleet(ops)
    c = make_curator(f, config={"dynamic_scene_chance": 1.0})
    c._tick()
    scenes = [kwargs for kind, kwargs in ops if kind == "scene"]
    assert len(scenes) == 1
    assert scenes[0]["mood"] in idle_curator.MOODS
    assert scenes[0].get("composition_mode") is None
    assert not any(kind == "realtime" for kind, *_ in ops)


def test_disabled_curator_never_curates(idle_sources, ops, fake_config):
    f = RecordingFleet(ops)
    make_curator(f, config={"enabled": False})._tick()
    assert ops == []


def test_status_reports_cycles_and_backoff(idle_sources, ops, fake_config):
    f = RecordingFleet(ops)
    clock = FakeClock()
    c = make_curator(f, config={"backoff_s": 600}, clock=clock)
    st = c.status()
    assert st["running"] is False
    assert st["cycles"] == 0
    assert st["backoff_remaining_s"] == 0
    c.note_manual_activity()
    assert 0 < c.status()["backoff_remaining_s"] <= 600
    clock.advance(601)
    c._tick()
    st = c.status()
    assert st["cycles"] == 1
    assert st["last_look"]["kind"] == "realtime"
    assert st["last_look"]["mood"] in idle_curator.MOODS


def test_registry_start_is_idempotent_and_stop_releases(idle_sources, fake_config):
    idle_sources["smart_director"]["busy"] = True  # keep the background thread from curating
    f = RecordingFleet()
    first = idle_curator.start_curator(f, {})
    try:
        second = idle_curator.start_curator(f, {})
        assert first is second
        assert idle_curator.curator_status()["running"] is True
    finally:
        assert "stopped" in idle_curator.stop_curator().lower()
    assert idle_curator.curator_status()["running"] is False


def test_module_note_manual_activity_is_safe_without_a_curator():
    idle_curator.note_manual_activity()  # must not raise
