"""GET /api/led-preview must feed the GUI live visualizer actual controller pixels.

The backend already keeps one read-only Peek subscriber per controller
(`LightClient.get_led_preview`). The GUI strip preview never received those
pixels, so it could only approximate colors from polled state JSON. This
endpoint exposes bounded, sanitized live buffers plus the channel map.
"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import light_gui


class StubController:
    def __init__(self, snapshot=None, error=None):
        self._snapshot = snapshot or {"active": False, "status": "waiting_for_frames"}
        self._error = error

    def get_led_preview(self):
        if self._error:
            raise self._error
        return self._snapshot


def make_state(left=None, right=None, fleet=True):
    left = left if left is not None else StubController()
    right = right if right is not None else StubController()
    clients = {"left": left, "right": right} if fleet else {"controller": left}
    channel_map = (
        {
            "far-left": ["left", 0],
            "middle-left": ["left", 1],
            "middle-right": ["right", 0],
            "far-right": ["right", 1],
        }
        if fleet
        else {}
    )
    client = SimpleNamespace(clients=clients)
    return SimpleNamespace(
        client=client,
        is_fleet=fleet,
        channel_map=lambda: channel_map,
    )


@pytest.fixture
def server_factory():
    servers = []

    def start(state):
        server = ThreadingHTTPServer(("127.0.0.1", 0), light_gui.make_handler(state))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def get_json(server, path):
    with urllib.request.urlopen(
        f"http://127.0.0.1:{server.server_port}{path}", timeout=5
    ) as response:
        return response.status, json.loads(response.read().decode())


def test_led_preview_path_is_a_registered_get_route():
    assert "/api/led-preview" in light_gui.API_GET_PATHS


def test_led_preview_serves_active_pixels_sanitized_with_channel_map(server_factory):
    left = StubController(
        {
            "active": True,
            "status": "live",
            "age_s": 0.12,
            "colors": [[255, 0, 0], [0, 255, 0], [300, -5, "x"], [1, 2], None, "junk"],
            "frame_count": 42,
            "source": "controller_pixel_buffer",
        }
    )
    right = StubController(
        {"active": True, "status": "live", "age_s": 0.05, "colors": [[9, 8, 7]]}
    )
    server = server_factory(make_state(left=left, right=right))
    status, payload = get_json(server, "/api/led-preview")
    assert status == 200 and payload["ok"] is True
    controllers = payload["controllers"]
    assert controllers["left"]["active"] is True
    assert controllers["left"]["status"] == "live"
    # Malformed rows are dropped; valid pixels pass through verbatim.
    assert controllers["left"]["colors"] == [[255, 0, 0], [0, 255, 0]]
    assert controllers["right"]["colors"] == [[9, 8, 7]]
    assert payload["fleet"] is True
    assert payload["channels"]["far-left"] == ["left", 0]
    assert payload["channels"]["far-right"] == ["right", 1]


def test_led_preview_inactive_controller_reports_reason_without_colors(server_factory):
    right = StubController({"active": False, "status": "stale", "age_s": 4.2})
    server = server_factory(make_state(right=right))
    _, payload = get_json(server, "/api/led-preview")
    entry = payload["controllers"]["right"]
    assert entry["active"] is False
    assert entry["status"] == "stale"
    assert "colors" not in entry


def test_led_preview_observer_error_is_reported_never_fatal(server_factory):
    left = StubController(error=RuntimeError("peek socket died"))
    server = server_factory(make_state(left=left))
    status, payload = get_json(server, "/api/led-preview")
    assert status == 200 and payload["ok"] is True
    entry = payload["controllers"]["left"]
    assert entry["active"] is False
    assert entry["error"] == "RuntimeError"
    assert "colors" not in entry


def test_led_preview_single_controller_still_serves_pixels(server_factory):
    solo = StubController({"active": True, "status": "live", "colors": [[1, 2, 3]]})
    server = server_factory(make_state(left=solo, fleet=False))
    _, payload = get_json(server, "/api/led-preview")
    assert payload["fleet"] is False
    assert payload["controllers"]["controller"]["colors"] == [[1, 2, 3]]


def test_led_preview_bounds_oversized_buffers(server_factory):
    many = [[1, 2, 3]] * 5000
    left = StubController({"active": True, "status": "live", "colors": many})
    server = server_factory(make_state(left=left))
    _, payload = get_json(server, "/api/led-preview")
    assert len(payload["controllers"]["left"]["colors"]) <= 1024
