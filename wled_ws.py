#!/usr/bin/env python3
"""Realtime observation of WLED devices over their websocket interface.

WLED serves a websocket at ws://<host>/ws: on connect it pushes the full
{state, info} JSON and pushes again on every state change, whatever the
source (web UI, HTTP API, MQTT, sync, button). Max 4 simultaneous clients;
info.ws reports the connected client count (-1 = build without websocket
support). See https://kno.wled.ge/interfaces/websocket/ .

One WledWsObserver thread per device lets the platform see external changes
as they happen instead of only when something polls /json. Observers are
read-only here: the only frame ever sent is the optional {"v": true} state
request on connect. Polling remains the fallback whenever the socket is
unavailable or unsupported.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

import lightctl

logger = logging.getLogger("lightss.wled_ws")

WS_PATH = "/ws"

# Status values reported via the on_status callback.
STATUS_CONNECTED = "connected"
STATUS_DISCONNECTED = "disconnected"
STATUS_FALLBACK = "fallback"        # reconnecting repeatedly; caller should rely on polling
STATUS_UNSUPPORTED = "unsupported"  # info.ws == -1: this build has no websocket support


def ws_url(host: str) -> str:
    """ws:// URL for a device's /ws endpoint from any accepted host spelling."""
    normalized = lightctl.normalize_host(host)
    scheme, _, rest = normalized.partition("://")
    ws_scheme = "wss" if scheme == "https" else "ws"
    return f"{ws_scheme}://{rest}{WS_PATH}"


def _default_connect(url: str) -> Any:
    from websockets.sync.client import connect  # lazy: optional dependency

    return connect(url)


class WledWsObserver:
    """One daemon thread watching one device's /ws socket and forwarding pushes.

    on_update(host, state, info) fires for every {state, info} push (either
    key may be missing from a given push; both arrive as {} then). on_status
    (optional) receives STATUS_* values so the caller can stand polling back
    up when the socket is unusable. Callback exceptions are logged, never
    propagated, and reconnects use capped exponential backoff.
    """

    def __init__(
        self,
        host: str,
        on_update: Callable[[str, dict, dict], None],
        *,
        name: str | None = None,
        on_status: Callable[[str, str], None] | None = None,
        connect_fn: Callable[[str], Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        fallback_after: int = 5,
        request_state: bool = True,
    ) -> None:
        self.host = lightctl.normalize_host(host)
        self.name = name or self.host
        self.on_update = on_update
        self.on_status = on_status
        self._connect_fn = connect_fn or _default_connect
        self._sleep_fn = sleep_fn
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._fallback_after = max(1, fallback_after)
        self._request_state = request_state
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn: Any = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"wled-ws-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        conn = self._conn
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _emit_status(self, status: str) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status(self.name, status)
        except Exception:
            logger.exception("on_status callback failed for %s", self.name)

    def _handle_message(self, raw: Any) -> bool:
        """Parse one push; returns False when the device lacks ws support."""
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = bytes(raw).decode("utf-8", errors="replace")
            message = json.loads(raw)
        except (ValueError, TypeError):
            logger.debug("ignoring non-JSON ws message from %s", self.name)
            return True
        if not isinstance(message, dict):
            return True
        info = message.get("info")
        if isinstance(info, dict) and info.get("ws") == -1:
            logger.info("%s reports no websocket support (info.ws=-1); staying on polling", self.name)
            self._emit_status(STATUS_UNSUPPORTED)
            return False
        state = message.get("state")
        if not isinstance(state, dict) and not isinstance(info, dict):
            return True
        try:
            self.on_update(
                self.host,
                state if isinstance(state, dict) else {},
                info if isinstance(info, dict) else {},
            )
        except Exception:
            logger.exception("on_update callback failed for %s", self.name)
        return True

    def _run(self) -> None:
        failures = 0
        fallback_signalled = False
        while not self._stop.is_set():
            conn = None
            try:
                conn = self._connect_fn(ws_url(self.host))
                self._conn = conn
                failures = 0
                fallback_signalled = False
                self._connected.set()
                self._emit_status(STATUS_CONNECTED)
                if self._request_state:
                    try:
                        conn.send(json.dumps({"v": True}))
                    except Exception:
                        logger.debug("initial state request failed for %s", self.name, exc_info=True)
                while not self._stop.is_set():
                    if not self._handle_message(conn.recv()):
                        return  # unsupported build: polling stays the channel
            except Exception as exc:
                if self._stop.is_set():
                    break
                failures += 1
                if failures == 1:
                    logger.warning("ws observer for %s lost connection: %s", self.name, exc)
                else:
                    logger.debug("ws reconnect #%d for %s failed: %s", failures, self.name, exc)
            finally:
                self._connected.clear()
                self._conn = None
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            if self._stop.is_set():
                break
            self._emit_status(STATUS_DISCONNECTED)
            if failures >= self._fallback_after and not fallback_signalled:
                fallback_signalled = True
                self._emit_status(STATUS_FALLBACK)
            delay = min(self._backoff_max, self._backoff_base * (2 ** min(failures - 1, 16)))
            self._sleep_fn(delay)
        self._connected.clear()
