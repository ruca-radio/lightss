"""Observe WLED's single-subscriber Peek stream, never write lighting state.

Protocol: https://kno.wled.ge/interfaces/websocket/#live-led-stream-peek
A browser may take over Peek. Expire our data rather than repeatedly reclaiming
its subscription. Pixel buffers describe rendered RGB, not physical light output.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


class WLEDPreviewObserver:
    """One explicitly started preview socket per persistent controller."""

    def __init__(self, host, *, clock=time.monotonic):
        self.host = host.rstrip("/")
        parsed = urlsplit(self.host)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username
            or parsed.path
        ):
            raise ValueError("Peek requires a plain HTTP controller URL")
        self._clock = clock
        self._lock = threading.RLock()
        self._closed = False
        self._thread = self._loop = self._task = None
        self._colors = None
        self._previous_colors = None
        self._previous_at = None
        self._at = None
        self._frames = 0
        self._change = None
        self._error = None

    def feed_frame(self, data):
        """Accept bounded 1D RGB frames; reject unknown versions/partial pixels."""
        if not isinstance(data, bytes) or len(data) < 5 or data[:2] != b"L\x01":
            return False
        if (len(data) - 2) % 3 or len(data) > 3074:
            return False
        colors = tuple(tuple(data[i : i + 3]) for i in range(2, len(data), 3))
        now = self._clock()
        with self._lock:
            if self._closed:
                return False
            self._change = None
            self._previous_colors = self._previous_at = None
            if (
                self._colors
                and len(self._colors) == len(colors)
                and now - self._at <= 2
            ):
                self._previous_colors, self._previous_at = self._colors, self._at
                self._change = round(
                    sum(
                        abs(a - b)
                        for old, new in zip(self._colors, colors)
                        for a, b in zip(old, new)
                    )
                    / (len(colors) * 3 * 255),
                    4,
                )
            self._colors, self._at = colors, now
            self._frames += 1
            self._error = None
        return True

    def snapshot(self):
        """Return isolated fresh frames; stale buffers never masquerade as output."""
        with self._lock:
            age = max(0, self._clock() - self._at) if self._at is not None else None
            active = not self._closed and age is not None and age <= 2
            status = (
                "live"
                if active
                else "stopped"
                if self._closed
                else "stale"
                if age is not None
                else "waiting_for_frames"
            )
            result = {
                "active": active,
                "status": status,
                "age_s": age,
                "source": "controller_pixel_buffer",
                "received_at_monotonic_s": self._at,
                "frame_count": self._frames,
                "frame_change": self._change if active else None,
            }
            if active:
                result["colors"] = [list(color) for color in self._colors]
                if self._previous_colors:
                    result["previous_colors"] = [
                        list(color) for color in self._previous_colors
                    ]
                    result["frame_interval_s"] = self._at - self._previous_at
            if self._error:
                result["error"] = self._error
            return result

    def start(self):
        """Opt in once; constructing a controller performs no network requests."""
        with self._lock:
            if self._closed or self._thread:
                return
            self._thread = threading.Thread(
                target=self._thread_main, name="wled-peek", daemon=True
            )
            self._thread.start()

    def close(self):
        """Cancel reconnects and close sockets even while reception is idle."""
        with self._lock:
            self._closed = True
            loop, task, thread = self._loop, self._task, self._thread
        if loop and task:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
            if thread.is_alive():
                logger.warning("Peek observer did not stop promptly for %s", self.host)

    def _thread_main(self):
        asyncio.run(self._run())

    async def _run(self):
        import aiohttp

        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
        retry = 0.5
        try:
            while not self._closed:
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=3)
                    ) as session:
                        async with session.ws_connect(
                            self.host + "/ws", heartbeat=15, max_msg_size=16384
                        ) as ws:
                            await ws.send_json({"lv": True})
                            subscription_attempts = 1
                            saw_frame = False
                            retry = 0.5
                            async for message in ws:
                                if message.type == aiohttp.WSMsgType.BINARY:
                                    saw_frame = (
                                        self.feed_frame(message.data) or saw_frame
                                    )
                                elif message.type == aiohttp.WSMsgType.TEXT:
                                    try:
                                        reply = message.json()
                                    except ValueError:
                                        continue
                                    # Retry only an explicitly rejected request,
                                    # never silence after another Peek client takes over.
                                    if (
                                        isinstance(reply, dict)
                                        and reply.get("error") == 3
                                        and not saw_frame
                                        and subscription_attempts < 3
                                    ):
                                        await asyncio.sleep(0.2 * subscription_attempts)
                                        await ws.send_json({"lv": True})
                                        subscription_attempts += 1
                                elif message.type == aiohttp.WSMsgType.ERROR:
                                    raise ConnectionError("Peek socket disconnected")
                            with self._lock:
                                self._colors = None
                                self._at = None
                            # A live socket without frames can mean another Peek
                            # client owns the stream. Never resubscribe on a timer.
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    with self._lock:
                        self._colors = None
                        self._at = None
                        self._error = type(exc).__name__
                if not self._closed:
                    await asyncio.sleep(retry)
                    retry = min(retry * 2, 15)
        except asyncio.CancelledError:
            pass
        finally:
            with self._lock:
                self._colors = None
                self._at = None
                self._task = None
                self._loop = None
