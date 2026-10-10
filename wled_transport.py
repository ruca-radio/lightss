"""Synchronous, raw-JSON read adapter for python-wled's async HTTP client.

Writes stay in LightClient so upstream payload rewriting and retries never
change the application's validated command contract.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from typing import Any, Callable
from urllib.parse import urlsplit

logger = logging.getLogger("lightsctl")


class WledHttpTransport:
    """Own one asyncio loop and reusable upstream session, created on demand."""

    def __init__(
        self,
        host: str,
        timeout: float,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not self.supports_host(host):
            raise ValueError(f"Unsupported python-wled host: {host}")
        self.host = host
        self.timeout = timeout
        self._client_factory = client_factory
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._closed = False
        # Upstream has at most three attempts with exponential backoff (1+2s).
        # Bound the entire operation too, without nesting a second retry loop.
        self._request_budget = timeout * 3 + 3

    @staticmethod
    def supports_host(host: str) -> bool:
        try:
            parts = urlsplit(host)
            return bool(
                parts.scheme == "http"
                and parts.hostname
                and ":" not in parts.hostname
                and parts.port in (None, 80)
                and parts.username is None
                and parts.password is None
                and parts.path in ("", "/")
                and not parts.query
                and not parts.fragment
            )
        except ValueError:
            return False

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            tasks = asyncio.all_tasks(self._loop)
            for task in tasks:
                task.cancel()
            if tasks:
                self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def _ensure_worker(self) -> None:
        if self._closed:
            raise RuntimeError("python-wled HTTP transport is closed")
        if self._thread is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="lightss-wled-http", daemon=True
            )
            self._thread.start()

    async def _read(self, path: str) -> Any:
        if self._client is None:
            factory = self._client_factory
            if factory is None:
                from wled import WLED

                factory = WLED
            self._client = factory(
                urlsplit(self.host).hostname, request_timeout=self.timeout
            )
        return await asyncio.wait_for(
            self._client.request(path, "GET"), timeout=self._request_budget
        )

    def get_json(self, path: str) -> dict[str, Any] | list[Any]:
        url = f"{self.host}{path}"
        with self._lock:
            self._ensure_worker()
            assert self._loop is not None
            future = asyncio.run_coroutine_threadsafe(self._read(path), self._loop)
            try:
                data = future.result(timeout=self._request_budget + 0.5)
            except (TimeoutError, concurrent.futures.TimeoutError) as exc:
                future.cancel()
                raise RuntimeError(f"Timeout reading WLED at {url}") from exc
            except Exception as exc:
                # Preserve the legacy truncation-recovery signals for fxdata.
                name = type(exc).__name__
                if name == "WLEDInvalidResponseError" or isinstance(exc, json.JSONDecodeError):
                    raise RuntimeError(f"Invalid JSON from {url}: {exc}") from exc
                if name == "WLEDEmptyResponseError":
                    raise RuntimeError(f"Empty response from {url}: {exc}") from exc
                raise RuntimeError(f"Could not read light controller at {url}: {exc}") from exc
        if not isinstance(data, (dict, list)):
            raise RuntimeError(f"Unexpected JSON root from {url}: {type(data).__name__}.")
        return data

    async def _close_client(self) -> None:
        if self._client is not None:
            await self._client.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._loop is None or self._thread is None:
                return
            future = asyncio.run_coroutine_threadsafe(self._close_client(), self._loop)
            try:
                future.result(timeout=max(1.0, self.timeout))
            except Exception:
                future.cancel()
                logger.debug("Failed to close python-wled HTTP session", exc_info=True)
            finally:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=max(1.0, self.timeout) + 0.5)
