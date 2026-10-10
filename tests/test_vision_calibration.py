"""Tests for the vision-assisted self-configuration engine.

Nothing here touches a camera, a model, or real hardware: the fleet is a fake
with WLED-shaped get_info/get_state/get_config responses, the vision model is a
scripted function, and config persistence writes to a temp file.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import fleet
import lightctl
import vision_calibration as vc


class FakeClient:
    """A duck-typed WLED client that records posts and reports a fixed layout."""

    def __init__(self, host, buses, segments):
        self.host = host
        self._buses = buses  # [{"pin":[...], "start":.., "len":.., "order":2}, ...]
        self._segments = segments  # [{id,start,stop,on}, ...]
        self.posts = []

    def get_info(self):
        return {
            "name": "WLED-Gledopto",
            "ver": "16.0.1",
            "leds": {
                "count": max((s["stop"] for s in self._segments), default=0),
                "maxseg": 32,
            },
        }

    def get_state(self):
        return {"on": True, "bri": 128, "seg": list(self._segments)}

    def get_config(self):
        return {"hw": {"led": {"ins": list(self._buses)}}}

    def post_state(self, payload):
        lightctl.validate_wled_payload(payload)
        self.posts.append(json.loads(json.dumps(payload)))
        return {"success": True}


def _make_fleet(probe_colors=vc.PROBE_COLORS):
    """Two controllers: left (far-left/middle-left), right (middle-right/far-right)."""
    left_buses = [{"pin": [16], "start": 0, "len": 34, "order": 2},
                  {"pin": [2], "start": 34, "len": 48, "order": 2}]
    right_buses = [{"pin": [16], "start": 0, "len": 47, "order": 2},
                   {"pin": [2], "start": 47, "len": 40, "order": 2}]
    clients = {
        "left": FakeClient("http://10.27.27.110", left_buses,
                           [{"id": 0, "start": 0, "stop": 34, "on": True},
                            {"id": 1, "start": 34, "stop": 82, "on": True}]),
        "right": FakeClient("http://10.27.27.112", right_buses,
                            [{"id": 0, "start": 0, "stop": 47, "on": True},
                             {"id": 1, "start": 47, "stop": 87, "on": True}]),
    }
    controllers = [
        fleet.ControllerConfig("left", "http://10.27.27.110", {
            0: fleet.SegmentConfig("left-seg0", gpio=16, pixels=34, start=0, stop=34),
            1: fleet.SegmentConfig("left-seg1", gpio=2, pixels=48, start=34, stop=82),
        }),
        fleet.ControllerConfig("right", "http://10.27.27.112", {
            0: fleet.SegmentConfig("right-seg0", gpio=16, pixels=47, start=0, stop=47),
            1: fleet.SegmentConfig("right-seg1", gpio=2, pixels=40, start=47, stop=87),
        }),
    ]
    # wall_order drives the target channel names, as it does on the real wall.
    return fleet.LightFleet(clients, controllers, installation=fleet.InstallationConfig())


class FakeVision:
    """Scripted vision model returning the single combined observation."""

    def __init__(self, identity_order, lit_end="bottom"):
        self.identity_order = list(identity_order)
        self.lit_end = lit_end
        self.calls = []

    def __call__(self, image, prompt):
        self.calls.append(prompt)
        return json.dumps(
            {"colors_left_to_right": self.identity_order, "lit_end": self.lit_end}
        )


def _config_path(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"controllers": [], "installation": {"wall_order": list(vc.DEFAULT_WALL_ORDER)}}))
    return str(path)


def _run(tmp_path, *, identity, lit_end="bottom", write_segments=True, write_config=True):
    wall = _make_fleet()
    path = _config_path(tmp_path)

    def load():
        return json.loads(pathlib.Path(path).read_text())

    def save(config):
        pathlib.Path(path).write_text(json.dumps(config, indent=2))

    report = vc.self_configure(
        wall,
        capture=lambda: "iVBORw0KGgo=",
        vision=FakeVision(identity, lit_end),
        write_segments=write_segments,
        write_config=write_config,
        load=load,
        save=save,
        config_path=path,
    )
    return wall, report, path


def _channel_map(wall):
    """{(controller, seg): channel} after self-configuration, read from the report."""
    return {}


# ---------------------------------------------------------------------------
# Identity mapping
# ---------------------------------------------------------------------------

def test_identity_maps_colours_to_wall_positions(tmp_path):
    # Segments are lit in PROBE_COLORS order: left seg0=red, left seg1=green,
    # right seg0=blue, right seg1=yellow. Vision sees them reversed left-to-right,
    # so far-left is the yellow one (right seg1), etc.
    wall, report, _path = _run(tmp_path, identity=["yellow", "blue", "green", "red"])

    assert report["ok"] is True
    assert report["wall_order"] == ["far-left", "middle-left", "middle-right", "far-right"]
    mapping = report["mapping"]
    assert mapping["right seg 1"] == "far-left"     # yellow, seen leftmost
    assert mapping["right seg 0"] == "middle-left"  # blue, seen 2nd
    assert mapping["left seg 1"] == "middle-right"  # green, seen 3rd
    assert mapping["left seg 0"] == "far-right"     # red, seen 4th


def test_identity_preserves_order_when_vision_agrees(tmp_path):
    wall, report, _path = _run(tmp_path, identity=["red", "green", "blue", "yellow"])
    assert report["mapping"] == {
        "left seg 0": "far-left",
        "left seg 1": "middle-left",
        "right seg 0": "middle-right",
        "right seg 1": "far-right",
    }


def test_identity_rejects_unknown_or_wrong_count(tmp_path):
    wall = _make_fleet()
    with pytest.raises(vc.SelfConfigureError):
        vc.self_configure(
            wall, capture=lambda: "img", vision=FakeVision(["purple", "green", "blue", "yellow"]),
            write_segments=False, write_config=False,
            load=lambda: {}, save=lambda c: None, config_path="/tmp/x",
        )
    with pytest.raises(vc.SelfConfigureError):
        vc.self_configure(
            wall, capture=lambda: "img", vision=FakeVision(["red", "green", "blue"]),
            write_segments=False, write_config=False,
            load=lambda: {}, save=lambda c: None, config_path="/tmp/x",
        )


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------

def test_orientation_detects_top_mounted_strips(tmp_path):
    wall, report, _path = _run(tmp_path, identity=["red", "green", "blue", "yellow"], lit_end="top")
    assert report["pixel_zero"] == "top"
    # config persisted orientation down + pixel_zero top
    saved = json.loads(pathlib.Path(_path).read_text())
    assert saved["installation"]["pixel_zero"] == "top"
    assert saved["installation"]["orientation"] == "down"


def test_orientation_missing_falls_back_to_configured_pixel_zero(tmp_path):
    class NoOrientation:
        def __call__(self, image, prompt):
            return json.dumps({"colors_left_to_right": ["red", "green", "blue", "yellow"]})

    wall = _make_fleet()
    report = vc.self_configure(
        wall, capture=lambda: "img", vision=NoOrientation(),
        write_segments=False, write_config=False,
        load=lambda: {}, save=lambda c: None, config_path="/tmp/x",
    )
    assert report["ok"] is True
    assert report["wall_order"] == ["far-left", "middle-left", "middle-right", "far-right"]
    assert report["pixel_zero"] == "bottom"  # InstallationConfig default

def test_garbage_observation_aborts(tmp_path):
    wall = _make_fleet()
    with pytest.raises(vc.SelfConfigureError):
        vc.self_configure(
            wall, capture=lambda: "img", vision=lambda i, p: "not json",
            write_segments=False, write_config=False,
            load=lambda: {}, save=lambda c: None, config_path="/tmp/x",
        )


# ---------------------------------------------------------------------------
# Segment writes + config persistence
# ---------------------------------------------------------------------------

def test_segment_plan_posts_each_controllers_bounds(tmp_path):
    wall, report, _path = _run(tmp_path, identity=["red", "green", "blue", "yellow"])

    right_posts = [p for p in wall.clients["right"].posts if "seg" in p]
    assert right_posts
    assert all("start" not in seg and "stop" not in seg for post in right_posts for seg in post["seg"])
    assert report["segments_written"]["left"]["ok"] is False
    assert report["segments_written"]["right"]["ok"] is False


def test_config_writes_corrected_channels_with_backup(tmp_path):
    wall, report, path = _run(tmp_path, identity=["yellow", "blue", "green", "red"])

    saved = json.loads(pathlib.Path(path).read_text())
    by_channel = {}
    for controller in saved["controllers"]:
        for seg_id, seg in controller["segments"].items():
            by_channel[seg["channel"]] = (controller["name"], int(seg_id))
    assert by_channel["far-left"] == ("right", 1)
    assert by_channel["middle-left"] == ("right", 0)
    assert by_channel["middle-right"] == ("left", 1)
    assert by_channel["far-right"] == ("left", 0)
    assert saved["installation"]["wall_order"] == ["far-left", "middle-left", "middle-right", "far-right"]
    assert report["config_backup"] is not None
    assert pathlib.Path(report["config_backup"]).exists()


def test_offline_controller_aborts_without_writes(tmp_path):
    wall = _make_fleet()
    wall.clients["right"].get_info = lambda: (_ for _ in ()).throw(OSError("offline"))
    with pytest.raises(vc.SelfConfigureError, match="offline"):
        vc.self_configure(
            wall, capture=lambda: "img", vision=FakeVision(["red", "green", "blue", "yellow"]),
            write_segments=True, write_config=True,
            load=lambda: {}, save=lambda c: None, config_path="/tmp/x",
        )


# ---------------------------------------------------------------------------
# State restore
# ---------------------------------------------------------------------------

def test_probes_are_restored_after_self_configure(tmp_path):
    wall, report, _path = _run(tmp_path, identity=["red", "green", "blue", "yellow"])

    # Each controller must receive a restore POST that re-asserts its prior
    # on/bri/seg (segments without an "i" and with the original on flags),
    # unfreezing the probe output.
    for name in ("left", "right"):
        restores = [
            p for p in wall.clients[name].posts
            if p.get("on") is True and p.get("bri") == 128
            and all("i" not in seg and "on" in seg for seg in p.get("seg", []))
        ]
        assert restores, f"{name} never restored its prior state"


def test_two_step_begin_and_conclude_restores_and_writes(tmp_path):
    """The GUI flow: begin_probe lights, caller captures, conclude maps + applies.

    Between begin and conclude the probes must stay lit; conclude restores them.
    """
    wall = _make_fleet()
    path = _config_path(tmp_path)

    def load():
        return json.loads(pathlib.Path(path).read_text())

    def save(config):
        pathlib.Path(path).write_text(json.dumps(config, indent=2))

    handle = vc.begin_probe(wall)

    # Probes are lit now (each controller got an "i" post after begin_probe).
    for name in ("left", "right"):
        assert any("i" in seg for p in wall.clients[name].posts for seg in p.get("seg", []))

    report = vc.conclude(
        wall,
        handle,
        image="iVBORw0KGgo=",
        vision=FakeVision(["red", "green", "blue", "yellow"]),
        load=load,
        save=save,
        config_path=path,
    )
    assert report["ok"] is True
    assert report["wall_order"] == ["far-left", "middle-left", "middle-right", "far-right"]
    # After conclude, each controller's LAST post is the restore (no frozen i).
    for name in ("left", "right"):
        final = wall.clients[name].posts[-1]
        assert all("i" not in seg for seg in final.get("seg", []))
        assert final.get("on") is True


def test_abandoned_handle_can_be_restored_manually(tmp_path):
    wall = _make_fleet()
    handle = vc.begin_probe(wall)
    handle["restore"]()
    for name in ("left", "right"):
        final = wall.clients[name].posts[-1]
        assert all("i" not in seg for seg in final.get("seg", []))
        assert final.get("on") is True
