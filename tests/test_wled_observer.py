"""Read-only WLED observation at the socket, clock and lifecycle boundaries."""
from __future__ import annotations

import asyncio
import copy
import queue
import threading
import time

import pytest

from wled_observer import PythonWLEDObserver


RAW = {
    "state": {"on": True, "bri": 80, "customUsermod": {"signal": 4},
              "seg": [{"id": 7, "c1": 19, "o1": True, "i": [0, [5, 6, 7]]}]},
    "info": {"name": "custom firmware", "AudioReactive": {"sample": 13}},
    "effects": ["Solid"], "customRoot": {"retain": True},
}


def eventually(check, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        threading.Event().wait(0.005)
    raise AssertionError("observation did not reach expected state")


class SocketBoundary:
    """Fake only the asynchronous network boundary; the observer loop is real."""
    def __init__(self, host, request_timeout):
        self.host = host
        self.connected = False
        self.raw = copy.deepcopy(RAW)
        self.requests = []
        self.closed = threading.Event()
        self.messages = None
        self.loop = None
        self.error = None
        self.listening_stopped = threading.Event()

    async def update(self):
        return object()  # Typed model must never be our raw JSON contract.

    async def connect(self):
        self.loop = asyncio.get_running_loop()
        self.messages = asyncio.Queue()
        self.connected = True

    async def request(self, path, method="GET"):
        self.requests.append((path, method))
        if self.error:
            raise self.error
        return copy.deepcopy(self.raw)

    async def listen(self, callback):
        try:
            while True:
                item = await self.messages.get()
                if isinstance(item, Exception):
                    if isinstance(item, ConnectionError):
                        self.connected = False
                    raise item
                callback(object())
                if hasattr(self, "notifications_consumed"):
                    self.notifications_consumed.set()
        finally:
            self.listening_stopped.set()

    def emit(self, message=None):
        self.loop.call_soon_threadsafe(self.messages.put_nowait, message)

    async def close(self):
        self.connected = False
        self.closed.set()


@pytest.fixture
def boundary():
    created = queue.Queue()
    def factory(host, request_timeout):
        client = SocketBoundary(host, request_timeout)
        created.put(client)
        return client
    return factory, created


@pytest.fixture
def observed(boundary):
    factory, created = boundary
    observer = PythonWLEDObserver("http://127.0.0.1", timeout=0.2,
                                  client_factory=factory, retry_initial=0.01,
                                  retry_max=0.02, refresh_interval=0.2)
    observer.start()
    socket = created.get(timeout=2)
    eventually(observer.cached_json)
    yield observer, socket, created
    observer.close()


def test_construction_does_not_start_network_io(boundary):
    factory, created = boundary
    observer = PythonWLEDObserver("http://127.0.0.1", client_factory=factory)
    assert observer.cached_json() is None
    assert created.empty()
    observer.close()


def test_cache_preserves_raw_firmware_fields_and_segment_ids(observed):
    observer, socket, _ = observed
    assert observer.cached_json() == RAW
    cached = observer.cached_json()
    cached["state"]["seg"][0]["id"] = 0
    assert observer.cached_json()["state"]["seg"][0]["id"] == 7
    assert all(method == "GET" for _path, method in socket.requests)


def test_socket_event_refreshes_raw_state(observed):
    observer, socket, _ = observed
    socket.raw["state"]["bri"] = 210
    socket.emit()
    eventually(lambda: (observer.cached_json() or {}).get("state", {}).get("bri") == 210)


def test_disconnected_socket_never_serves_cached_state(observed):
    observer, socket, _ = observed
    socket.connected = False
    assert observer.cached_json() is None


def test_listener_failure_reconnects_with_new_session(observed):
    observer, socket, created = observed
    socket.emit(RuntimeError("socket dropped"))
    replacement = created.get(timeout=2)
    eventually(observer.cached_json)
    assert replacement is not socket
    assert socket.closed.wait(1)


def test_invalidate_drops_state_before_next_observation(observed):
    observer, socket, _ = observed
    socket.error = RuntimeError("unavailable")
    observer.invalidate()
    assert observer.cached_json() is None


def test_cache_expires_even_when_socket_claims_connected(boundary):
    clock = [10.0]
    factory, created = boundary
    observer = PythonWLEDObserver("http://127.0.0.1", client_factory=factory,
                                  clock=lambda: clock[0], cache_ttl=1,
                                  refresh_interval=60)
    observer.start()
    created.get(timeout=2)
    try:
        eventually(observer.cached_json)
        clock[0] = 11.0
        assert observer.cached_json() is None
    finally:
        observer.close()


def test_shutdown_closes_socket_and_cannot_restart(observed):
    observer, socket, created = observed
    observer.close()
    assert socket.closed.wait(1)
    assert observer.cached_json() is None
    observer.start()
    assert created.empty()


def test_malformed_refresh_does_not_turn_stale_state_live(observed):
    observer, socket, _ = observed
    socket.raw = {"info": {"name": "missing state"}}
    socket.emit()
    eventually(lambda: len(socket.requests) > 1)
    assert observer.cached_json() is None


def test_shutdown_closes_owned_session_when_socket_close_fails(observed):
    observer, socket, _ = observed
    class SessionBoundary:
        closed = False
        async def close(self):
            self.closed = True
    session = socket.session = SessionBoundary()
    socket._close_session = True
    async def failed_socket_close():
        raise RuntimeError("socket close failed")
    socket.close = failed_socket_close
    observer.close()
    assert session.closed is True


def test_stopped_listener_never_leaves_live_cache(observed):
    observer, socket, _ = observed
    in_flight = threading.Event()
    async def blocked_request(*_args, **_kwargs):
        in_flight.set()
        await asyncio.Future()
    socket.request = blocked_request
    assert in_flight.wait(1)
    # Model decoding can fail while a raw refresh is in flight and WS stays open.
    socket.emit(ValueError("unsupported firmware payload"))
    assert socket.listening_stopped.wait(1)
    assert socket.connected is True
    assert observer.cached_json() is None


def test_socket_event_burst_keeps_fresh_cache_until_rate_limited_refresh(observed):
    observer, socket, _ = observed
    consumed = threading.Event()
    original_request = socket.request
    request_times = []
    async def measured_request(*args, **kwargs):
        request_times.append(time.monotonic())
        return await original_request(*args, **kwargs)
    socket.request = measured_request
    # The actual listener acknowledges callback processing at the network boundary.
    socket.notifications_consumed = consumed
    socket.raw["state"]["bri"] = 211
    for _ in range(50):
        socket.emit()
    assert consumed.wait(1)
    assert observer.cached_json() is not None
    eventually(lambda: (observer.cached_json() or {}).get("state", {}).get("bri") == 211)
    for _ in range(50):
        socket.emit()
    eventually(lambda: len(request_times) >= 2)
    assert request_times[1] - request_times[0] >= 0.45


def test_continuous_socket_changes_do_not_starve_raw_refresh(observed):
    observer, socket, _ = observed
    original_request = socket.request
    socket.raw["state"]["bri"] = 212
    async def changing_request(*args, **kwargs):
        socket.emit()
        await asyncio.sleep(0.01)
        return await original_request(*args, **kwargs)
    socket.request = changing_request
    socket.emit()
    eventually(lambda: (observer.cached_json() or {}).get("state", {}).get("bri") == 212)
