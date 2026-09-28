from __future__ import annotations

import struct
import time

import color_lab
import fleet
import lightctl
import mcp_light
import pytest
import realtime


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(lightctl, "_SCENE_DIR", str(tmp_path))


class FakeFleet:
    def __init__(self):
        self.installation, self.controllers = fleet.load_topology({})
    def channels(self): return {}
    def resolve(self, target): return [(target, None)]


class FakeTransport:
    def __init__(self): self.sent = []; self.closed = False
    def sendto(self, data, address): self.sent.append((data, address)); return len(data)
    def close(self): self.closed = True


class FlakyTransport:
    """Transport that raises OSError for the first `fail_times` sends (None = forever)."""

    def __init__(self, fail_times=None):
        self.sent = []
        self.closed = False
        self.fail_times = fail_times

    def sendto(self, data, address):
        if self.fail_times is None or self.fail_times > 0:
            if self.fail_times is not None:
                self.fail_times -= 1
            raise OSError("network is unreachable")
        self.sent.append((data, address))
        return len(data)

    def close(self):
        self.closed = True


def test_ddp_header_and_push_split():
    packets = realtime.build_ddp_packets(bytes(range(10)), offset=6, seq=7, max_data=4)
    assert len(packets) == 3
    flags, seq, dtype, dest, offset, length = struct.unpack("!BBBBLH", packets[0][:10])
    assert (flags, seq, dtype, dest, offset, length) == (0x40, 7, 0x0B, 1, 6, 4)
    assert packets[-1][0] == 0x41


def test_ddp_topology_mapping_right_offsets_and_wall_order():
    entries = realtime.ddp_topology(FakeFleet())
    assert [e.channel for e in entries] == ["far-left", "middle-left", "middle-right", "far-right"]
    right = {e.channel: e for e in entries if e.controller == "right"}
    assert (right["middle-right"].seg_id, right["middle-right"].ddp_offset, right["middle-right"].ddp_length) == (1, 0, 47)
    assert (right["far-right"].seg_id, right["far-right"].ddp_offset, right["far-right"].ddp_length) == (0, 47, 40)


def test_render_frame_lengths_bounds_and_determinism():
    entries = realtime.ddp_topology(FakeFleet())
    a = realtime.render_frames(entries, shader="aurora_flow", seed=3, t=0.5)
    b = realtime.render_frames(entries, shader="aurora_flow", seed=3, t=0.5)
    assert a == b
    assert len(a["left"]) == 82 * 3
    assert len(a["right"]) == 87 * 3
    assert all(0 <= byte <= 210 for frame in a.values() for byte in frame)


def test_y_orientation_top_is_high_index_for_bottom_zero():
    e = realtime.ddp_topology(FakeFleet())[0]
    low = sum(realtime.pixel_color("vertical_scan", e, 0, 0.0, intensity=0.8))
    high = sum(realtime.pixel_color("vertical_scan", e, e.pixels - 1, 0.0, intensity=0.8))
    assert low > high


def test_runner_start_stop_fake_transport():
    f = FakeFleet(); tx = FakeTransport()
    runner = realtime.RealtimeRunner(f, shader="liquid_gradient", fps=40, duration_s=0.05, transport=tx, seed=1)
    runner.start(); runner.join(timeout=2)
    assert runner.sent_frames >= 1
    assert tx.sent
    assert {addr[1] for _data, addr in tx.sent} == {4048}


def test_global_start_replace_stop_status(monkeypatch):
    f = FakeFleet(); transports = []
    OriginalRunner = realtime.RealtimeRunner
    def make_runner(fleet_, **kwargs):
        kwargs["transport"] = FakeTransport(); kwargs["duration_s"] = 0.2; transports.append(kwargs["transport"])
        return OriginalRunner(fleet_, **kwargs)
    monkeypatch.setattr(realtime, "RealtimeRunner", make_runner)
    assert "Realtime started" in realtime.realtime_start(f, shader="red_rocks", fps=50, duration_s=999)
    assert realtime.realtime_status()["running"] is True
    assert "Realtime started" in realtime.realtime_start(f, shader="aurora_flow")
    assert realtime.realtime_status()["shader"] == "aurora_flow"
    assert "stopped" in realtime.realtime_stop().lower()


def test_mcp_realtime_schema_accepts_unique_looks():
    tool = next(item for item in mcp_light.build_tools() if item["name"] == "realtime_start")
    props = tool["inputSchema"]["properties"]
    assert "ember_rise" in props["shader"]["enum"]
    assert "auto" in props["shader"]["enum"]
    assert "colors" in props


def test_mcp_realtime_tools(monkeypatch):
    calls = []
    monkeypatch.setattr(realtime, "realtime_start", lambda client, **kwargs: calls.append(kwargs) or "started")
    assert "started" in mcp_light.call_tool(FakeFleet(), "realtime_start", {"shader": "center_wave", "duration_s": 1}, None)["content"][0]["text"]
    assert calls[0]["shader"] == "center_wave"
    assert "running" in mcp_light.call_tool(FakeFleet(), "realtime_status", {}, None)["content"][0]["text"]


def test_custom_colors_paint_the_frame_instead_of_stock_mood_palette():
    entries = realtime.ddp_topology(FakeFleet())
    teal = realtime.render_frames(entries, shader="liquid_gradient", composition_mode="unison", colors=[[0, 180, 160], [0, 40, 90]], t=0.2)
    magma = realtime.render_frames(entries, shader="liquid_gradient", composition_mode="unison", colors=[[180, 30, 8], [80, 8, 2]], t=0.2)
    assert teal != magma
    left = list(teal["left"][:3])
    assert left[1] >= left[0]
    assert left[1] >= left[2]


def test_unique_shaders_are_visually_distinct_and_safe():
    entries = realtime.ddp_topology(FakeFleet())
    colors = [[180, 40, 12], [40, 8, 4], [220, 90, 30]]
    frames = {
        name: realtime.render_frames(entries, shader=name, colors=colors, seed=4, t=0.8)
        for name in ("ember_rise", "tide_pull", "comet_fall", "twin_helix")
    }
    assert frames["ember_rise"] != frames["tide_pull"]
    assert frames["comet_fall"] != frames["twin_helix"]
    assert all(0 <= byte <= 210 for frame in frames.values() for data in frame.values() for byte in data)


def test_mcp_strips_tool_same_and_per_strip(monkeypatch):
    import columns
    calls = []
    monkeypatch.setattr(columns, "apply_channels", lambda client, channels, fx, pal=None, **opts: calls.append(("same", channels, fx, pal)) or "ok")
    monkeypatch.setattr(columns, "per_strip", lambda client, specs: calls.append(("per", specs)) or "ok")
    text = mcp_light.call_tool(FakeFleet(), "strips", {"channels": ["far-left", "far-right"], "fx": 9}, None)["content"][0]["text"]
    assert "far-left" in text
    assert calls[0][0] == "same"
    mcp_light.call_tool(FakeFleet(), "strips", {"assignments": [{"channel": "middle-left", "fx": 28}]}, None)
    assert calls[1][0] == "per"


def test_mcp_design_look_schema_includes_colors_and_run():
    tool = next(item for item in mcp_light.build_tools() if item["name"] == "design_look")
    props = tool["inputSchema"]["properties"]
    assert "colors" in props
    assert props["colors"]["type"] == "array"
    assert "run" in props
    assert props["run"]["type"] == "boolean"
    assert tool["inputSchema"].get("additionalProperties") is False


def test_mcp_design_look_tool(monkeypatch):
    import look_agents

    designed = []
    applied = []
    fake_look = {
        "shader": "ember_rise",
        "colors": [(196, 48, 12), (80, 8, 2)],
        "composition_mode": "unison",
        "intensity": 0.6,
        "seed": 7,
        "mood": "ember",
        "agents": {"colorist": "specialist", "motion": "color_lab", "critic": None},
    }

    def fake_design(prompt, **kwargs):
        designed.append((prompt, kwargs))
        return fake_look

    def fake_apply(client, look, **kwargs):
        applied.append((look, kwargs))
        return "Realtime started: ember_rise"

    monkeypatch.setattr(look_agents, "design_look", fake_design)
    monkeypatch.setattr(look_agents, "apply_look", fake_apply)

    result = mcp_light.call_tool(
        FakeFleet(),
        "design_look",
        {
            "prompt": "ember canyon dusk",
            "mood": "ember",
            "colors": ["#c8320c"],
            "run": True,
            "fps": 24,
            "duration_s": 8,
            "target": "all",
        },
        None,
    )
    text = result["content"][0]["text"]
    assert "ember_rise" in text
    assert "colorist" in text
    assert "realtime" in text.lower()
    assert designed
    assert designed[0][0] == "ember canyon dusk"
    assert "target" not in designed[0][1]
    assert designed[0][1].get("mood") == "ember"
    assert "settings" in designed[0][1]
    assert applied
    assert applied[0][0]["shader"] == "ember_rise"
    assert applied[0][1].get("fps") == 24
    assert applied[0][1].get("duration_s") == 8

    designed.clear()
    applied.clear()
    skipped = mcp_light.call_tool(
        FakeFleet(),
        "design_look",
        {"prompt": "quiet tide", "run": False},
        None,
    )
    assert "ember_rise" in skipped["content"][0]["text"]
    assert "not started" in skipped["content"][0]["text"].lower()
    assert designed
    assert not applied


def test_auto_shader_and_status_include_built_look():
    f = FakeFleet()
    tx = FakeTransport()
    runner = realtime.RealtimeRunner(
        f,
        shader="auto",
        mood="ember rise",
        colors=["#c8320c", "#501004"],
        fps=30,
        duration_s=0.05,
        transport=tx,
        seed="look-9",
    )
    assert runner.shader == "ember_rise"
    assert runner.colors[0][0] > runner.colors[0][2]
    runner.start()
    runner.join(timeout=2)
    realtime._runner = runner
    try:
        payload = realtime.realtime_status()
        assert payload["shader"] == "ember_rise"
        assert payload["colors"]
        assert payload["mood"] == "ember rise"
    finally:
        realtime._runner = None


def test_runner_survives_transient_send_errors():
    f = FakeFleet()
    tx = FlakyTransport(fail_times=3)
    runner = realtime.RealtimeRunner(f, fps=40, duration_s=0.3, transport=tx, seed=1)
    runner.start()
    runner.join(timeout=5)
    assert tx.sent  # recovered and kept painting
    assert runner.sent_frames >= 1


def test_runner_aborts_after_sustained_send_errors_without_raising():
    f = FakeFleet()
    tx = FlakyTransport(fail_times=None)  # always fails
    runner = realtime.RealtimeRunner(f, fps=40, duration_s=600, transport=tx, seed=1)
    runner.start()
    runner.join(timeout=15)  # must exit on its own after the error cap
    assert not runner.is_alive()
    assert runner.sent_frames == 0


def test_realtime_start_stops_running_show(monkeypatch):
    import shows

    calls = []
    monkeypatch.setattr(shows, "stop_show", lambda: calls.append("stop") or "stopped")
    f = FakeFleet()
    OriginalRunner = realtime.RealtimeRunner

    def make_runner(fleet_, **kwargs):
        kwargs["transport"] = FakeTransport()
        kwargs["duration_s"] = 0.05
        return OriginalRunner(fleet_, **kwargs)

    monkeypatch.setattr(realtime, "RealtimeRunner", make_runner)
    realtime.realtime_start(f, shader="aurora_flow")
    assert calls == ["stop"]
    realtime.realtime_stop()


def test_runner_unspecified_composition_rotates_per_seed():
    modes = set()
    for seed in range(12):
        runner = realtime.RealtimeRunner(
            FakeFleet(), mood="moody lounge", seed=seed,
            transport=FakeTransport(), duration_s=0.1,
        )
        assert runner.composition_mode in color_lab.COMPOSITION_MODES
        modes.add(runner.composition_mode)
    assert len(modes) >= 3


# ---------------------------------------------------------------------------
# Static per-LED frame streaming (set_leds / dynamic_scene persistence on
# WLED 0.15+, where JSON seg.i frames no longer stay on screen)
# ---------------------------------------------------------------------------


def test_frame_runner_restreams_until_stopped():
    f = FakeFleet()
    entries = realtime.ddp_topology(f)
    frames = realtime.render_frames(entries, shader="liquid_gradient", seed=1, t=0.3)
    tx = FakeTransport()
    runner = realtime.StaticFrameRunner(f, frames, fps=40, duration_s=0.4, transport=tx)
    runner.start(); runner.join(timeout=2)
    assert runner.sent_frames >= 2
    assert tx.sent
    assert {addr[1] for _data, addr in tx.sent} == {4048}


def test_frame_runner_rejects_misaligned_frames_before_opening_socket():
    f = FakeFleet()
    with pytest.raises(ValueError, match="Frame for 'left'"):
        realtime.StaticFrameRunner(f, {"left": b"\x00" * 3}, transport=FakeTransport())
    with pytest.raises(ValueError, match="Unknown controller"):
        realtime.StaticFrameRunner(f, {"nope": b""}, transport=FakeTransport())


def test_frame_start_status_replace_and_stop(monkeypatch):
    f = FakeFleet()
    entries = realtime.ddp_topology(f)
    frames = realtime.render_frames(entries, shader="aurora_flow", seed=2, t=0.1)
    transports = []
    OriginalRunner = realtime.StaticFrameRunner

    def make_runner(fleet_, frames, fps=6, duration_s=300, transport=None):
        tx = FakeTransport()
        transports.append(tx)
        return OriginalRunner(fleet_, frames, fps=fps, duration_s=duration_s, transport=tx)

    monkeypatch.setattr(realtime, "StaticFrameRunner", make_runner)
    try:
        assert "streaming" in realtime.frame_start(f, frames, fps=30, duration_s=999)
        assert realtime.frame_status()["running"] is True
        assert realtime.frame_status()["controllers"] == ["left", "right"]
        assert "streaming" in realtime.frame_start(f, frames, fps=12, duration_s=60)  # replaces previous
        assert len(transports) == 2
        assert "stopped" in realtime.frame_stop()
        assert realtime.frame_status()["running"] is False
        assert realtime.frame_stop() == "Static frame not running."
    finally:
        realtime.frame_stop()


def test_realtime_stop_also_stops_frame_runner(monkeypatch):
    f = FakeFleet()
    entries = realtime.ddp_topology(f)
    frames = realtime.render_frames(entries, shader="bass_bloom", seed=3, t=0.0)
    OriginalRunner = realtime.StaticFrameRunner

    def make_runner(fleet_, frames, fps=6, duration_s=300, transport=None):
        return OriginalRunner(fleet_, frames, fps=fps, duration_s=duration_s, transport=FakeTransport())

    monkeypatch.setattr(realtime, "StaticFrameRunner", make_runner)
    try:
        realtime.frame_start(f, frames)
        assert realtime.frame_status()["running"] is True
        result = realtime.realtime_stop()
        assert "stopped" in result
        assert realtime.frame_status()["running"] is False
        assert realtime._frame_runner is None
    finally:
        realtime.realtime_stop()


def test_realtime_start_replaces_frame_runner(monkeypatch):
    f = FakeFleet()
    entries = realtime.ddp_topology(f)
    frames = realtime.render_frames(entries, shader="ember_rise", seed=5, t=0.5)
    OriginalFrame = realtime.StaticFrameRunner

    def make_frame(fleet_, frames, fps=6, duration_s=300, transport=None):
        return OriginalFrame(fleet_, frames, fps=fps, duration_s=duration_s, transport=FakeTransport())

    monkeypatch.setattr(realtime, "StaticFrameRunner", make_frame)
    OriginalRT = realtime.RealtimeRunner

    def make_rt(fleet_, **kwargs):
        kwargs["transport"] = FakeTransport()
        kwargs["duration_s"] = 0.05
        return OriginalRT(fleet_, **kwargs)

    monkeypatch.setattr(realtime, "RealtimeRunner", make_rt)
    try:
        realtime.frame_start(f, frames)
        assert realtime.frame_status()["running"] is True
        realtime.realtime_start(f, shader="aurora_flow")
        assert realtime.frame_status()["running"] is False
        assert realtime.realtime_status()["running"] is True
    finally:
        realtime.realtime_stop()
