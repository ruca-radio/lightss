import pytest
import json
from types import SimpleNamespace
from unittest.mock import Mock

import light_gui
import mcp_light


def test_pixels_are_bounded_real_samples_with_visual_summary():
    from lighting_context import summarize_pixels

    colors = [[255, 0, 0]] * 24 + [[0, 0, 255]] * 8
    summary = summarize_pixels(colors, source="controller_pixel_buffer")
    assert summary["source"] == "controller_pixel_buffer"
    assert summary["sample_colors"][0] == [255, 0, 0]
    assert len(summary["sample_colors"]) <= 8
    assert summary["dominant_colors"][0] == "#ff0000"
    assert summary["lit_fraction"] == 1
    assert summary["color_count"] == 32


def test_observation_maps_live_segment_geometry_to_preview():
    from lighting_context import observe_lighting

    device = SimpleNamespace(
        get_led_preview=lambda: {
            "active": True,
            "age_s": 0.05,
            "source": "controller_pixel_buffer",
            "colors": [[255, 0, 0]] * 4 + [[0, 0, 255]] * 4,
        }
    )
    wall = SimpleNamespace(clients={"left": device})
    snapshot = {
        "topology": {
            "controllers": [
                {
                    "name": "left",
                    "segments": {
                        "7": {"channel": "far-left"},
                        "9": {"channel": "middle-left"},
                    },
                }
            ]
        },
        "devices": {
            "left": {
                "state": {
                    "on": True,
                    "bri": 128,
                    "seg": [
                        {"id": 7, "start": 0, "stop": 4, "fx": 1},
                        {"id": 9, "start": 4, "stop": 8, "fx": 0},
                    ],
                },
                "info": {"live": False, "leds": {"count": 8}},
                "effects": ["Solid", "Audio Effect"],
            }
        },
    }
    result = observe_lighting(wall, snapshot=snapshot)
    segments = result["controllers"]["left"]["segments"]
    assert segments[0]["channel"] == "far-left"
    assert segments[0]["effect_name"] == "Audio Effect"
    assert segments[0]["output"]["dominant_colors"] == ["#ff0000"]
    assert segments[1]["output"]["dominant_colors"] == ["#0000ff"]
    assert result["audio"]["status"] == "unavailable"


def test_stale_preview_never_claims_current_colors():
    from lighting_context import observe_lighting

    device = SimpleNamespace(
        get_led_preview=lambda: {
            "active": False,
            "age_s": 3,
            "colors": [[255, 0, 0]],
            "status": "stale",
        }
    )
    result = observe_lighting(
        device, snapshot={"state": {"seg": [{"id": 0, "start": 0, "stop": 1}]}}
    )
    segment = next(iter(result["controllers"].values()))["segments"][0]
    assert segment["output"]["active"] is False
    assert "sample_colors" not in segment["output"]


def test_gui_context_includes_the_same_controller_observation(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(light_gui, "_device_snapshot", lambda _: {"state": {"seg": []}})
    context = light_gui.ai_context_text(client, playback_clock={})
    assert "Live lighting observation" in context
    assert "physical_output_verified" in context


def test_mcp_observation_is_read_only_and_available_without_a_director():
    client = SimpleNamespace(
        get_device_snapshot=lambda: {"state": {"seg": []}}, post_state=Mock()
    )
    assert "observe_lighting" in {tool["name"] for tool in mcp_light.build_tools()}
    result = mcp_light.call_tool(client, "observe_lighting", {}, modes=Mock())
    data = json.loads(result["content"][0]["text"])
    assert data["physical_output_verified"] is False
    client.post_state.assert_not_called()


def test_real_controller_reads_keep_observation_receipt_time(monkeypatch):
    import lightctl
    import wled_transport

    client = lightctl.LightClient(
        "http://127.0.0.1", use_python_wled=True, min_request_interval=0
    )
    monkeypatch.setattr(
        wled_transport.WledHttpTransport, "get_json", lambda *args: {"on": True}
    )
    try:
        client.get_state()
        assert client._state_observed_at is not None
    finally:
        client.close()


def test_observation_http_endpoint_returns_the_same_read_only_context():
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    state = SimpleNamespace(
        client=SimpleNamespace(get_device_snapshot=lambda: {"state": {"seg": []}})
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), light_gui.make_handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        connection.request("GET", "/api/lighting-observation")
        response = connection.getresponse()
        assert response.status == 200
        data = json.loads(response.read())
        assert data["physical_output_verified"] is False
        assert data["audio"]["status"] == "unavailable"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_downsampled_peek_uses_firmware_stride_for_segment_mapping():
    from lighting_context import observe_lighting

    device = SimpleNamespace(
        get_led_preview=lambda: {
            "active": True,
            "colors": [[255, 0, 0]] * 256 + [[0, 0, 255]] * 256,
        }
    )
    snapshot = {
        "state": {
            "seg": [
                {"id": 0, "start": 0, "stop": 512},
                {"id": 1, "start": 512, "stop": 1025},
            ]
        },
        "info": {"arch": "esp32", "leds": {"count": 1025}},
    }
    segments = observe_lighting(device, snapshot=snapshot)["controllers"]["controller"][
        "segments"
    ]
    assert segments[0]["output"]["sample_step"] == 2
    assert segments[0]["output"]["dominant_colors"] == ["#ff0000"]
    assert segments[1]["output"]["dominant_colors"] == ["#0000ff"]


def test_per_strip_motion_is_measured_from_its_own_pixels():
    from lighting_context import observe_lighting
    from wled_preview import WLEDPreviewObserver
    from tests.test_wled_audio import FakeClock

    clock = FakeClock()
    peek = WLEDPreviewObserver("http://127.0.0.1", clock=clock)
    peek.feed_frame(b"L\x01" + bytes([255, 0, 0, 0, 0, 255]))
    clock.advance(0.04)
    peek.feed_frame(b"L\x01" + bytes([255, 0, 0, 0, 255, 0]))
    client = SimpleNamespace(get_led_preview=peek.snapshot)
    snapshot = {
        "state": {
            "seg": [{"id": 0, "start": 0, "stop": 1}, {"id": 1, "start": 1, "stop": 2}]
        },
        "info": {"leds": {"count": 2}},
    }
    segments = observe_lighting(client, snapshot=snapshot)["controllers"]["controller"][
        "segments"
    ]
    assert segments[0]["output"]["motion"] == "unchanged"
    assert segments[1]["output"]["motion"] == "changing"
    assert segments[1]["output"]["motion_window_s"] == pytest.approx(0.04)
    peek.close()


def test_live_observation_uses_lean_state_read_without_catalog_config_requests(
    monkeypatch,
):
    import lightctl
    from lighting_context import observe_lighting

    client = lightctl.LightClient("http://127.0.0.1", dry_run=True)
    monkeypatch.setattr(
        client,
        "get_json",
        lambda: {
            "state": {"seg": [{"id": 0, "fx": 0}]},
            "info": {"live": False},
            "effects": ["Solid"],
        },
    )
    full = Mock(
        side_effect=AssertionError(
            "full configuration snapshot is too slow for telemetry"
        )
    )
    monkeypatch.setattr(client, "get_device_snapshot", full)
    try:
        observed = observe_lighting(client)
        assert (
            observed["controllers"]["controller"]["segments"][0]["effect_name"]
            == "Solid"
        )
        full.assert_not_called()
    finally:
        client.close()
