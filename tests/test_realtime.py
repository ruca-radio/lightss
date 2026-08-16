from __future__ import annotations

import struct
import time

import fleet
import lightctl
import mcp_light
import realtime


class FakeFleet:
    def __init__(self):
        self.installation, self.controllers = fleet.load_topology({})
    def channels(self): return {}
    def resolve(self, target): return [(target, None)]


class FakeTransport:
    def __init__(self): self.sent = []; self.closed = False
    def sendto(self, data, address): self.sent.append((data, address)); return len(data)
    def close(self): self.closed = True


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


def test_mcp_realtime_tools(monkeypatch):
    calls = []
    monkeypatch.setattr(realtime, "realtime_start", lambda client, **kwargs: calls.append(kwargs) or "started")
    assert "started" in mcp_light.call_tool(FakeFleet(), "realtime_start", {"shader": "center_wave", "duration_s": 1}, None)["content"][0]["text"]
    assert calls[0]["shader"] == "center_wave"
    assert "running" in mcp_light.call_tool(FakeFleet(), "realtime_status", {}, None)["content"][0]["text"]
