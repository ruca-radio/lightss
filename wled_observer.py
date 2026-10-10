"""Read-only python-wled WebSocket observations for the synchronous controller.

python-wled's models intentionally represent a subset of firmware fields. Its
WebSocket callbacks therefore schedule a bounded raw GET /json, retaining
usermod data and the device's actual segment IDs. Events
are coalesced; idle sockets refresh periodically. This observer never POSTs or
owns the native/UDP lighting writers.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

logger = logging.getLogger("lightss.wled_observer")


class PythonWLEDObserver:
    """Explicitly started, independently closed observer with a fresh-only cache."""

    def __init__(
        self,
        host: str,
        timeout: float = 2.5,
        *,
        client_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        cache_ttl: float = 5.0,
        refresh_interval: float = 4.0,
        retry_initial: float = 0.5,
        retry_max: float = 15.0,
    ) -> None:
        parsed = urlsplit(host)
        if parsed.scheme != "http" or parsed.port not in (None, 80) or not parsed.hostname:
            raise ValueError("python-wled observation requires an HTTP controller on port 80")
        self.host = parsed.hostname
        self.timeout = timeout
        self._factory = client_factory
        self._clock = clock
        self._cache_ttl = cache_ttl
        self._refresh_interval = refresh_interval
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._listener: asyncio.Task | None = None
        self._refresh_event: asyncio.Event | None = None
        self._client: Any = None
        self._connected = False
        self._closed = False
        self._cache: dict[str, Any] | None = None
        self._fetched_at = 0.0
        self._generation = 0

    def start(self) -> None:
        """Start observation once; construction alone performs no network IO."""
        with self._lock:
            if self._closed or self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._thread_main, name=f"wled-observer-{self.host}", daemon=True
            )
            self._thread.start()

    def cached_json(self) -> dict[str, Any] | None:
        """Return an isolated raw snapshot only while it is demonstrably fresh."""
        with self._lock:
            if (
                self._closed or not self._connected or self._client is None
                or self._listener is None or self._listener.done()
                or not self._client.connected or self._cache is None
                or self._clock() - self._fetched_at >= self._cache_ttl
            ):
                return None
            return copy.deepcopy(self._cache)

    def invalidate(self) -> None:
        """Drop observations immediately, including while a request is in flight."""
        self._notify_change(clear=True)

    def _notify_change(self, *, clear: bool = False) -> None:
        """Coalesce device notifications without causing extra fallback GETs."""
        with self._lock:
            if clear:
                self._cache = None
                # Only local writes invalidate in-flight reads. Device events
                # keep another refresh queued without starving cache commits.
                self._generation += 1
            loop, event = self._loop, self._refresh_event
        if loop is not None and event is not None:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # The loop completed between lookup and scheduling.
                pass

    def close(self) -> None:
        """Cancel network activity and release the socket/session promptly."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cache = None
            self._connected = False
            loop, task, thread = self._loop, self._task, self._thread
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
            if thread.is_alive():
                logger.warning("WLED observer for %s did not stop promptly", self.host)

    def _thread_main(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
        factory = self._factory
        if factory is None:
            from wled import WLED
            factory = WLED
        retry = self._retry_initial
        try:
            while not self._closed:
                client = factory(self.host, request_timeout=self.timeout)
                listener = None
                try:
                    await asyncio.wait_for(client.update(), timeout=self.timeout)
                    await asyncio.wait_for(client.connect(), timeout=self.timeout)
                    if not client.connected:
                        raise RuntimeError("WLED WebSocket did not connect")
                    with self._lock:
                        self._client = client
                        self._connected = True
                        self._refresh_event = asyncio.Event()
                    listener = asyncio.create_task(client.listen(lambda _device: self._notify_change()))
                    with self._lock:
                        self._listener = listener
                    retry = self._retry_initial
                    await self._observe(client, listener)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("WLED observer reconnecting to %s", self.host, exc_info=True)
                finally:
                    with self._lock:
                        self._connected = False
                        self._cache = None
                        self._client = None
                        self._listener = None
                        self._refresh_event = None
                        self._generation += 1
                    if listener is not None:
                        listener.cancel()
                        await asyncio.gather(listener, return_exceptions=True)
                    try:
                        await asyncio.wait_for(client.close(), timeout=1.0)
                    except Exception:
                        logger.debug("WLED observer session close failed", exc_info=True)
                    finally:
                        # Upstream closes the session after its socket. A failed
                        # or stalled socket close must still release owned HTTP
                        # resources; externally supplied sessions remain owned by
                        # their caller.
                        session = getattr(client, "session", None)
                        if (
                            session is not None and getattr(client, "_close_session", False)
                            and not getattr(session, "closed", False)
                        ):
                            try:
                                await asyncio.wait_for(session.close(), timeout=1.0)
                            except Exception:
                                logger.debug("WLED owned session cleanup failed", exc_info=True)
                if not self._closed:
                    await asyncio.sleep(retry)
                    retry = min(retry * 2, self._retry_max)
        except asyncio.CancelledError:
            pass
        finally:
            with self._lock:
                self._loop = None
                self._task = None

    async def _observe(self, client: Any, listener: asyncio.Task) -> None:
        """Coalesce socket notifications into raw reads, bounded by timeout."""
        while not self._closed:
            if listener.done():
                await listener
                raise RuntimeError("WLED WebSocket listener stopped")
            with self._lock:
                generation = self._generation
                event = self._refresh_event
                event.clear()
            try:
                raw = await asyncio.wait_for(client.request("/json", method="GET"), self.timeout)
                if not isinstance(raw, dict) or not isinstance(raw.get("state"), dict):
                    raise ValueError("WLED raw observation did not contain a state object")
                with self._lock:
                    if not self._closed and client.connected and generation == self._generation:
                        self._cache = copy.deepcopy(raw)
                        self._fetched_at = self._clock()
            except asyncio.CancelledError:
                raise
            except Exception:
                with self._lock:
                    self._cache = None
                logger.debug("WLED raw observation failed for %s", self.host, exc_info=True)
            # Avoid a hot loop when fast producers emit changes continuously.
            # Controllers have limited networking resources. Keep notification
            # storms below two raw refreshes per second, retaining a still-fresh
            # snapshot between them. Local writes use invalidate() to clear it.
            await asyncio.sleep(0.5)
            waiter = asyncio.create_task(event.wait())
            try:
                await asyncio.wait(
                    {waiter, listener}, timeout=self._refresh_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
