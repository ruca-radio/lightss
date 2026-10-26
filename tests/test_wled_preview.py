import pytest
from tests.test_wled_audio import FakeClock


def test_peek_observer_reports_real_frames_and_expires_them():
    from wled_preview import WLEDPreviewObserver

    clock = FakeClock()
    observer = WLEDPreviewObserver("http://127.0.0.1", clock=clock)
    assert observer.snapshot()["status"] == "waiting_for_frames"
    assert observer.feed_frame(b"L\x01\xff\x00\x00\x00\x00\xff")
    snapshot = observer.snapshot()
    assert snapshot["colors"] == [[255, 0, 0], [0, 0, 255]]
    assert snapshot["source"] == "controller_pixel_buffer"
    snapshot["colors"][0][0] = 0
    assert observer.snapshot()["colors"][0][0] == 255
    clock.advance(3)
    assert observer.snapshot()["active"] is False
    assert "colors" not in observer.snapshot()
    observer.close()
    assert not observer.feed_frame(b"L\x01\xff\x00\x00")


def test_peek_rejects_malformed_or_unsupported_frames():
    from wled_preview import WLEDPreviewObserver

    observer = WLEDPreviewObserver("http://127.0.0.1")
    for frame in (
        b"",
        b"L\x01",
        b"X\x01\x00\x00\x00",
        b"L\x01\x00",
        b"L\x02\x01\x01\x00\x00\x00",
        b"L\x01" + b"\x00" * 3075,
    ):
        assert not observer.feed_frame(frame)
    assert observer.snapshot()["active"] is False
    observer.close()


@pytest.mark.parametrize("busy", [False, True])
def test_socket_subscribes_once_without_reclaiming_idle_peek(monkeypatch, busy):
    import asyncio
    import queue
    import threading
    import aiohttp
    from wled_preview import WLEDPreviewObserver
    from tests.test_wled_observer import eventually

    created = queue.Queue()

    class Boundary:
        def __init__(self):
            self.messages = None
            self.closed = threading.Event()
            self.sent = []

        async def __aenter__(self):
            self.loop = asyncio.get_running_loop()
            self.messages = asyncio.Queue()
            created.put(self)
            return self

        async def __aexit__(self, *args):
            self.closed.set()

        async def send_json(self, value):
            self.sent.append(value)

        def __aiter__(self):
            return self

        async def __anext__(self):
            message = await self.messages.get()
            if message is None:
                raise StopAsyncIteration
            return message

        def emit(self, data):
            self.loop.call_soon_threadsafe(
                self.messages.put_nowait,
                type("Message", (), {"type": aiohttp.WSMsgType.BINARY, "data": data})(),
            )

        def busy(self):
            self.loop.call_soon_threadsafe(
                self.messages.put_nowait,
                type(
                    "Message",
                    (),
                    {"type": aiohttp.WSMsgType.TEXT, "json": lambda self: {"error": 3}},
                )(),
            )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def ws_connect(self, *args, **kwargs):
            return Boundary()

    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kwargs: Session())
    clock = FakeClock()
    observer = WLEDPreviewObserver("http://127.0.0.1", clock=clock)
    observer.start()
    socket = created.get(timeout=2)
    eventually(lambda: bool(socket.sent))
    try:
        if busy:
            socket.busy()
            eventually(lambda: len(socket.sent) == 2)
        socket.emit(b"L\x01\xff\x00\x00")
        eventually(lambda: observer.snapshot()["active"])
        clock.advance(3)
        assert observer.snapshot()["active"] is False
        assert socket.sent == [{"lv": True}] * (2 if busy else 1)
    finally:
        observer.close()
    assert socket.closed.is_set()
