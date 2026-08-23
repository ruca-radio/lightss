"""Tests for wled_ws: push parsing, reconnect/backoff, fallback signalling, shutdown,
and the light_gui integration (cache update feeding /api/state + SSE payloads)."""

from __future__ import annotations

import json
import queue
import threading
import time
import unittest

import light_gui
import wled_ws


class _FakeClosed(Exception):
    pass


class FakeConnection:
    """In-memory stand-in for a websockets.sync.client connection."""

    def __init__(self, messages: list | None = None) -> None:
        self._queue: queue.Queue = queue.Queue()
        for message in messages or []:
            self._queue.put(message)
        self.sent: list[str] = []
        self.closed = False

    def recv(self):
        item = self._queue.get(timeout=5)
        if isinstance(item, BaseException):
            raise item
        return item

    def push(self, message) -> None:
        self._queue.put(message)

    def send(self, data) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True
        self._queue.put(_FakeClosed("closed"))


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def make_observer(conn: FakeConnection, **overrides) -> wled_ws.WledWsObserver:
    updates: list[tuple[str, dict, dict]] = []
    statuses: list[tuple[str, str]] = []
    options = {
        "on_update": lambda host, state, info: updates.append((host, state, info)),
        "on_status": lambda name, status: statuses.append((name, status)),
        "connect_fn": lambda url: conn,
        "sleep_fn": lambda seconds: None,
    }
    options.update(overrides)
    observer = wled_ws.WledWsObserver("10.0.0.1", **options)
    observer.test_updates = updates
    observer.test_statuses = statuses
    return observer


class WsUrlTest(unittest.TestCase):
    def test_bare_host_gets_ws_scheme_and_path(self) -> None:
        self.assertEqual(wled_ws.ws_url("10.0.0.1"), "ws://10.0.0.1/ws")

    def test_http_host_is_rewritten(self) -> None:
        self.assertEqual(wled_ws.ws_url("http://wled.local/"), "ws://wled.local/ws")

    def test_https_host_becomes_wss(self) -> None:
        self.assertEqual(wled_ws.ws_url("https://wled.example.com"), "wss://wled.example.com/ws")


class PushParsingTest(unittest.TestCase):
    def test_push_invokes_callback_with_host_state_info(self) -> None:
        conn = FakeConnection([
            json.dumps({"state": {"on": True, "bri": 42}, "info": {"ws": 1, "name": "lamp"}}),
        ])
        observer = make_observer(conn)
        self.addCleanup(observer.stop)
        observer.start()

        self.assertTrue(wait_for(lambda: len(observer.test_updates) == 1))
        host, state, info = observer.test_updates[0]
        self.assertEqual(host, "http://10.0.0.1")
        self.assertEqual(state, {"on": True, "bri": 42})
        self.assertEqual(info, {"ws": 1, "name": "lamp"})

    def test_bytes_and_partial_pushes_are_handled(self) -> None:
        conn = FakeConnection([
            b'{"state": {"on": false}}',
            json.dumps({"info": {"ws": 2}}),
        ])
        observer = make_observer(conn)
        self.addCleanup(observer.stop)
        observer.start()

        self.assertTrue(wait_for(lambda: len(observer.test_updates) == 2))
        self.assertEqual(observer.test_updates[0][1], {"on": False})
        self.assertEqual(observer.test_updates[0][2], {})
        self.assertEqual(observer.test_updates[1][2], {"ws": 2})

    def test_malformed_messages_are_skipped(self) -> None:
        conn = FakeConnection([
            "not json",
            json.dumps(["a", "list"]),
            json.dumps({"unrelated": True}),
            json.dumps({"state": {"bri": 7}}),
        ])
        observer = make_observer(conn)
        self.addCleanup(observer.stop)
        observer.start()

        self.assertTrue(wait_for(lambda: len(observer.test_updates) == 1))
        self.assertEqual(observer.test_updates[0][1], {"bri": 7})

    def test_requests_state_on_connect(self) -> None:
        conn = FakeConnection()
        observer = make_observer(conn)
        self.addCleanup(observer.stop)
        observer.start()

        self.assertTrue(wait_for(lambda: observer.is_connected))
        self.assertEqual(conn.sent, [json.dumps({"v": True})])

    def test_callback_exception_does_not_kill_observer(self) -> None:
        calls: list[dict] = []

        def bad_callback(host, state, info):
            calls.append(state)
            raise RuntimeError("boom")

        conn = FakeConnection([
            json.dumps({"state": {"bri": 1}}),
            json.dumps({"state": {"bri": 2}}),
        ])
        observer = make_observer(conn, on_update=bad_callback)
        self.addCleanup(observer.stop)
        observer.start()

        self.assertTrue(wait_for(lambda: len(calls) == 2))
        self.assertTrue(observer.is_connected)


class ReconnectTest(unittest.TestCase):
    def test_reconnects_with_capped_exponential_backoff(self) -> None:
        delays: list[float] = []
        attempts = {"count": 0}
        conn = FakeConnection([json.dumps({"state": {"on": True}})])

        def flaky_connect(url: str):
            attempts["count"] += 1
            if attempts["count"] <= 3:
                raise OSError("connection refused")
            return conn

        observer = make_observer(
            conn,
            connect_fn=flaky_connect,
            sleep_fn=delays.append,
            backoff_base=1.0,
            backoff_max=3.0,
            fallback_after=100,
        )
        self.addCleanup(observer.stop)
        observer.start()

        self.assertTrue(wait_for(lambda: len(observer.test_updates) == 1))
        self.assertEqual(delays, [1.0, 2.0, 3.0])
        self.assertTrue(observer.is_connected)
        self.assertIn((observer.name, wled_ws.STATUS_CONNECTED), observer.test_statuses)

    def test_fallback_status_after_repeated_failures(self) -> None:
        def always_fail(url: str):
            raise OSError("no route to host")

        observer = make_observer(
            FakeConnection(),
            connect_fn=always_fail,
            fallback_after=2,
        )
        self.addCleanup(observer.stop)
        observer.start()

        self.assertTrue(wait_for(
            lambda: any(status == wled_ws.STATUS_FALLBACK for _name, status in observer.test_statuses)
        ))
        # Fallback is signalled once per outage, not on every retry.
        self.assertEqual(
            [s for _n, s in observer.test_statuses].count(wled_ws.STATUS_FALLBACK),
            1,
        )

    def test_recovers_after_fallback_when_connection_returns(self) -> None:
        conn = FakeConnection([json.dumps({"state": {"bri": 9}})])
        attempts = {"count": 0}

        def recovering_connect(url: str):
            attempts["count"] += 1
            if attempts["count"] <= 2:
                raise OSError("down")
            return conn

        observer = make_observer(conn, connect_fn=recovering_connect, fallback_after=1)
        self.addCleanup(observer.stop)
        observer.start()

        self.assertTrue(wait_for(lambda: len(observer.test_updates) == 1))
        statuses = [s for _n, s in observer.test_statuses]
        self.assertIn(wled_ws.STATUS_FALLBACK, statuses)
        self.assertEqual(statuses[-1], wled_ws.STATUS_CONNECTED)


class UnsupportedDeviceTest(unittest.TestCase):
    def test_info_ws_minus_one_signals_polling_and_stops(self) -> None:
        conn = FakeConnection([
            json.dumps({"state": {"on": True}, "info": {"ws": -1, "name": "old-build"}}),
        ])
        observer = make_observer(conn)
        observer.start()

        self.assertTrue(wait_for(
            lambda: any(s == wled_ws.STATUS_UNSUPPORTED for _n, s in observer.test_statuses)
        ))
        self.assertTrue(wait_for(lambda: not (observer._thread and observer._thread.is_alive())))
        self.assertFalse(observer.is_connected)
        observer.stop()


class ShutdownTest(unittest.TestCase):
    def test_stop_unblocks_recv_and_joins_thread(self) -> None:
        conn = FakeConnection()  # no messages: recv blocks
        observer = make_observer(conn)
        observer.start()
        self.assertTrue(wait_for(lambda: observer.is_connected))

        started = time.monotonic()
        observer.stop(timeout=2.0)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2.0)
        self.assertTrue(conn.closed)
        self.assertFalse(observer.is_connected)
        self.assertIsNotNone(observer._thread)
        self.assertFalse(observer._thread.is_alive())


# ---------------------------------------------------------------------------
# light_gui integration
# ---------------------------------------------------------------------------

class FakeClient:
    """Single-controller client whose HTTP polling fails if it is used while ws is live."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.polls = 0
        self.poll_result: dict = {}

    def get_state(self) -> dict:
        self.polls += 1
        return dict(self.poll_result)

    def get_info(self) -> dict:
        self.polls += 1
        return dict(self.poll_result)


def bare_gui_state(client, *, is_fleet: bool = False) -> light_gui.GuiState:
    state = light_gui.GuiState.__new__(light_gui.GuiState)
    state.client = client
    state._info_client = client
    state.is_fleet = is_fleet
    state.dry_run = False
    state.cached_state = None
    state.cached_info = None
    state._offline = {}
    state._offline_checked_at = {}
    state.state_lock = threading.Lock()
    state._ws_observers = []
    state._ws_statuses = {}
    state._ws_names = {}
    return state


class GuiIntegrationTest(unittest.TestCase):
    def test_ws_push_updates_cache_and_api_state_without_polling(self) -> None:
        client = FakeClient("http://10.0.0.1")
        state = bare_gui_state(client)
        conn = FakeConnection([
            json.dumps({"state": {"on": True, "bri": 77}, "info": {"ws": 1, "name": "lamp"}}),
        ])
        self.addCleanup(state.stop_ws_observers)
        state.start_ws_observers(connect_fn=lambda url: conn, sleep_fn=lambda s: None)

        self.assertTrue(wait_for(lambda: state.cached_state == {"on": True, "bri": 77}))
        self.assertEqual(state.api_state(), {"on": True, "bri": 77})
        self.assertEqual(state.get_info_throttled(), {"ws": 1, "name": "lamp"})
        self.assertEqual(client.polls, 0, "live ws cache should stand in for HTTP polling")

    def test_polling_resumes_when_ws_drops(self) -> None:
        client = FakeClient("http://10.0.0.1")
        client.poll_result = {"on": False, "bri": 5}
        state = bare_gui_state(client)
        conn = FakeConnection([
            json.dumps({"state": {"on": True}, "info": {"ws": 1}}),
        ])
        self.addCleanup(state.stop_ws_observers)
        state.start_ws_observers(connect_fn=lambda url: conn, sleep_fn=lambda s: None)
        self.assertTrue(wait_for(lambda: state.cached_state == {"on": True}))

        state.stop_ws_observers()

        self.assertEqual(state.api_state(), {"on": False, "bri": 5})
        self.assertGreaterEqual(client.polls, 1)

    def test_fleet_push_updates_named_controller_entry(self) -> None:
        import fleet

        controllers = [
            fleet.ControllerConfig(name="left", host="http://10.0.0.1"),
            fleet.ControllerConfig(name="right", host="http://10.0.0.2"),
        ]
        conns = {
            "ws://10.0.0.1/ws": FakeConnection([
                json.dumps({"state": {"bri": 11}, "info": {"ws": 1}}),
            ]),
            "ws://10.0.0.2/ws": FakeConnection([
                json.dumps({"state": {"bri": 22}, "info": {"ws": 1}}),
            ]),
        }
        client = FakeClient("http://10.0.0.1")
        client.controllers = controllers
        state = bare_gui_state(client, is_fleet=True)
        self.addCleanup(state.stop_ws_observers)
        state.start_ws_observers(connect_fn=conns.__getitem__, sleep_fn=lambda s: None)

        self.assertTrue(wait_for(
            lambda: state.cached_state == {"left": {"bri": 11}, "right": {"bri": 22}}
        ))
        self.assertEqual(state.api_state(), {"left": {"bri": 11}, "right": {"bri": 22}})
        self.assertEqual(client.polls, 0)

    def test_dry_run_starts_no_observers(self) -> None:
        state = bare_gui_state(FakeClient("http://10.0.0.1"))
        state.dry_run = True
        state.start_ws_observers(connect_fn=lambda url: FakeConnection())
        self.assertEqual(state._ws_observers, [])


if __name__ == "__main__":
    unittest.main()
