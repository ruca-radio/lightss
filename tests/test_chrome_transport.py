import json
import os
import stat
import threading
import time

import pytest

from chrome_transport import ChromeTransport


def _frame(message):
    return json.dumps(message, separators=(",", ":")).encode() + b"\0"


def _transport_from_pipes():
    command_r, command_w = os.pipe()
    reply_r, reply_w = os.pipe()
    transport = ChromeTransport.from_fds(command_w, reply_r)
    return transport, command_r, reply_w


def _read_frame(fd):
    data = b""
    while b"\0" not in data:
        data += os.read(fd, 65536)
    return json.loads(data.split(b"\0", 1)[0])


def test_call_handles_split_and_coalesced_frames_and_dispatches_events():
    transport, command_r, reply_w = _transport_from_pipes()
    events = []
    transport.on_event = events.append

    def peer():
        request = _read_frame(command_r)
        payload = _frame({"method": "Page.ready", "params": {"token": "not logged"}})
        payload += _frame({"id": request["id"], "result": {"ok": True}})
        payload += _frame({"method": "Page.after", "params": {}})
        os.write(reply_w, payload[:7])
        os.write(reply_w, payload[7:])

    thread = threading.Thread(target=peer)
    thread.start()
    try:
        assert transport.call("Page.enable", {"enabled": True}) == {"ok": True}
        assert events == [
            {"method": "Page.ready", "params": {"token": "not logged"}},
            {"method": "Page.after", "params": {}},
        ]
    finally:
        transport.close()
        os.close(command_r)
        os.close(reply_w)
        thread.join()


def test_send_returns_id_without_waiting_and_call_ignores_its_response():
    transport, command_r, reply_w = _transport_from_pipes()

    def peer():
        sent = _read_frame(command_r)
        called = _read_frame(command_r)
        os.write(reply_w, _frame({"id": sent["id"], "result": {"old": True}}))
        os.write(reply_w, _frame({"id": called["id"], "result": {"new": True}}))

    thread = threading.Thread(target=peer)
    thread.start()
    try:
        assert transport.send("Runtime.runIfWaitingForDebugger", session_id="session") == 1
        assert transport.call("Browser.getVersion") == {"new": True}
    finally:
        transport.close()
        os.close(command_r)
        os.close(reply_w)
        thread.join()


def test_send_rejects_oversized_frames_before_writing():
    transport, command_r, reply_w = _transport_from_pipes()
    try:
        with pytest.raises(ValueError, match="frame too large"):
            transport.send("Runtime.evaluate", {"expression": "x" * (4 * 1024 * 1024)})
        os.set_blocking(command_r, False)
        with pytest.raises(BlockingIOError):
            os.read(command_r, 1)
    finally:
        transport.close()
        os.close(command_r)
        os.close(reply_w)


def test_send_times_out_when_real_pipe_has_no_reader():
    transport, command_r, reply_w = _transport_from_pipes()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="CDP command timed out"):
            transport.send("Runtime.evaluate", {"expression": "x" * 1_000_000}, timeout=0.03)
        assert time.monotonic() - started < 0.5
    finally:
        transport.close()
        os.close(command_r)
        os.close(reply_w)


def test_call_timeout_includes_time_spent_writing_request():
    transport, command_r, reply_w = _transport_from_pipes()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="CDP command timed out"):
            transport.call(
                "Runtime.evaluate",
                {"expression": "x" * 1_000_000},
                timeout=0.03,
            )
        assert time.monotonic() - started < 0.5
    finally:
        transport.close()
        os.close(command_r)
        os.close(reply_w)


def test_close_interrupts_send_blocked_on_real_pipe():
    transport, command_r, reply_w = _transport_from_pipes()
    errors = []
    sender = threading.Thread(
        target=lambda: _capture_error(
            errors,
            transport.send,
            "Runtime.evaluate",
            {"expression": "x" * 1_000_000},
            timeout=30,
        )
    )
    sender.start()
    time.sleep(0.03)
    transport.close()
    sender.join(timeout=1)
    os.close(command_r)
    os.close(reply_w)
    assert not sender.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ConnectionError)


def test_call_serializes_concurrent_callers():
    transport, command_r, reply_w = _transport_from_pipes()
    seen = []

    def peer():
        first = _read_frame(command_r)
        seen.append(first["method"])
        time.sleep(0.05)
        os.write(reply_w, _frame({"id": first["id"], "result": {}}))
        second = _read_frame(command_r)
        seen.append(second["method"])
        os.write(reply_w, _frame({"id": second["id"], "result": {}}))

    peer_thread = threading.Thread(target=peer)
    callers = [threading.Thread(target=transport.call, args=(method,)) for method in ("one", "two")]
    peer_thread.start()
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()
    try:
        assert sorted(seen) == ["one", "two"]
    finally:
        transport.close()
        os.close(command_r)
        os.close(reply_w)
        peer_thread.join()


def test_call_raises_bounded_errors_for_timeout_eof_protocol_error_and_oversize():
    transport, command_r, reply_w = _transport_from_pipes()
    with pytest.raises(TimeoutError, match="CDP command timed out"):
        transport.call("Secret.method", timeout=0.02)
    os.close(reply_w)
    with pytest.raises(ConnectionError, match="Chrome debugging pipe closed"):
        transport.call("After.eof")
    transport.close()
    os.close(command_r)

    for payload, exception in [
        (b"not-json\0", ValueError),
        (b"x" * (4 * 1024 * 1024 + 1), ValueError),
    ]:
        transport, command_r, reply_w = _transport_from_pipes()
        os.write(reply_w, payload[:65536])
        if len(payload) > 65536:
            def flood():
                view = memoryview(payload)[65536:]
                while view:
                    written = os.write(reply_w, view[:65536])
                    view = view[written:]
            writer = threading.Thread(target=flood)
            writer.start()
        with pytest.raises(exception):
            transport.call("Bounded")
        transport.close()
        os.close(command_r)
        os.close(reply_w)
        if len(payload) > 65536:
            writer.join()


def test_close_from_another_thread_interrupts_pending_call_and_is_idempotent():
    transport, command_r, reply_w = _transport_from_pipes()
    errors = []
    caller = threading.Thread(target=lambda: _capture_error(errors, transport.call, "Wait.forever", timeout=30))
    caller.start()
    _read_frame(command_r)
    transport.close()
    transport.close()
    caller.join(timeout=1)
    os.close(command_r)
    os.close(reply_w)
    assert not caller.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ConnectionError)


def _capture_error(errors, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except Exception as error:
        errors.append(error)


def test_launch_uses_private_pipe_shim_isolated_profile_and_owned_process_group(tmp_path):
    browser = tmp_path / "fake-browser"
    browser.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os\n"
        "data=b''\n"
        "while b'\\0' not in data: data += os.read(3,65536)\n"
        "req=json.loads(data.split(b'\\0',1)[0])\n"
        "os.write(4,json.dumps({'id':req['id'],'result':{'argv':__import__('sys').argv[1:]}}).encode()+b'\\0')\n"
        "while os.read(3,65536): pass\n"
    )
    browser.chmod(browser.stat().st_mode | stat.S_IXUSR)
    profile = tmp_path / "owned-profile"
    transport = ChromeTransport(str(browser), profile, "https://example.test/path")
    try:
        assert transport.process.pid > 0
        argv = transport.call("Browser.getVersion")["argv"]
        assert "--remote-debugging-pipe" in argv
        assert f"--user-data-dir={profile}" in argv
        assert "--no-first-run" in argv
        assert "--no-default-browser-check" in argv
        assert "--ozone-platform=x11" in argv
        assert "--app=https://example.test/path" in argv
        assert not any("remote-debugging-port" in arg or "no-sandbox" in arg for arg in argv)
        assert stat.S_IMODE(profile.stat().st_mode) == 0o700
        assert os.getpgid(transport.process.pid) == transport.process.pid
    finally:
        process = transport.process
        transport.close()
        assert process.poll() is not None


def test_immediate_close_reaps_owned_subprocess(tmp_path):
    browser = tmp_path / "sleep-browser"
    browser.write_text("#!/bin/sh\nsleep 30\n")
    browser.chmod(0o700)
    transport = ChromeTransport(str(browser), tmp_path / "profile")
    process = transport.process
    transport.close()
    assert process.poll() is not None
