"""Small, private Chrome DevTools Protocol transport."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


_MAX_FRAME = 4 * 1024 * 1024
_READ_SIZE = 64 * 1024
_FD_SHIM = (
    "import fcntl,os,sys;"
    "a=fcntl.fcntl(int(sys.argv[1]),fcntl.F_DUPFD_CLOEXEC,5);"
    "b=fcntl.fcntl(int(sys.argv[2]),fcntl.F_DUPFD_CLOEXEC,5);"
    "os.dup2(a,3);os.dup2(b,4);os.close(a);os.close(b);"
    "os.set_inheritable(3,True);os.set_inheritable(4,True);"
    "os.execv(sys.argv[3],sys.argv[3:])"
)


class ChromeTransport:
    """Synchronous, serialized CDP client for a Chrome debugging pipe."""

    def __init__(
        self,
        binary: str,
        profile: Path,
        initial_url: str = "about:blank",
    ) -> None:
        self._initialize()
        command_r = command_w = reply_r = reply_w = None
        try:
            profile = Path(profile)
            profile.mkdir(mode=0o700, parents=True, exist_ok=True)
            profile.chmod(0o700)
            command_r, command_w = os.pipe()
            reply_r, reply_w = os.pipe()
            argv = [
                sys.executable,
                "-I",
                "-c",
                _FD_SHIM,
                str(command_r),
                str(reply_w),
                binary,
                "--remote-debugging-pipe",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--ozone-platform=x11",
                f"--app={initial_url}",
            ]
            self.process = subprocess.Popen(
                argv,
                pass_fds=(command_r, reply_w),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            self._owns_process = True
            self._write_fd = command_w
            self._read_fd = reply_r
            os.set_blocking(command_w, False)
            command_w = reply_r = None
        except BaseException:
            self.close()
            raise
        finally:
            self._close_raw_fds(command_r, command_w, reply_r, reply_w)

    @classmethod
    def from_fds(cls, write_fd: int, read_fd: int) -> ChromeTransport:
        """Construct around owned pipe ends, primarily for boundary tests."""
        self = cls.__new__(cls)
        self._initialize()
        self._write_fd = write_fd
        self._read_fd = read_fd
        os.set_blocking(write_fd, False)
        return self

    def _initialize(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.on_event: Callable[[dict[str, Any]], None] | None = None
        self._owns_process = False
        self._write_fd: int | None = None
        self._read_fd: int | None = None
        self._buffer = bytearray()
        self._next_id = 0
        self._closed = False
        self._call_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)

    @staticmethod
    def _close_raw_fds(*fds: int | None) -> None:
        for fd in fds:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float = 5,
    ) -> int:
        deadline = time.monotonic() + max(0, timeout)
        return self._send_until(method, params, session_id, deadline)

    def _send_until(
        self,
        method: str,
        params: dict[str, Any] | None,
        session_id: str | None,
        deadline: float,
    ) -> int:
        with self._write_lock:
            with self._state_lock:
                if self._closed or self._write_fd is None:
                    raise ConnectionError("Chrome debugging pipe closed")
                self._next_id += 1
                request_id = self._next_id
                fd = self._write_fd
            message: dict[str, Any] = {
                "id": request_id,
                "method": method,
                "params": params if params is not None else {},
            }
            if session_id is not None:
                message["sessionId"] = session_id
            frame = json.dumps(message, separators=(",", ":")).encode("utf-8")
            if len(frame) > _MAX_FRAME:
                raise ValueError("Chrome debugging frame too large")
            payload = frame + b"\0"
            view = memoryview(payload)
            while view:
                with self._state_lock:
                    if self._closed:
                        raise ConnectionError("Chrome debugging pipe closed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("CDP command timed out")
                try:
                    written = os.write(fd, view)
                except BlockingIOError:
                    try:
                        readable, writable, _ = select.select(
                            [self._wake_r], [fd], [], remaining
                        )
                    except (OSError, ValueError) as error:
                        raise ConnectionError("Chrome debugging pipe closed") from error
                    if self._wake_r in readable:
                        raise ConnectionError("Chrome debugging pipe closed")
                    if not writable:
                        raise TimeoutError("CDP command timed out")
                    continue
                except OSError as error:
                    raise ConnectionError("Chrome debugging pipe closed") from error
                if written <= 0:
                    raise ConnectionError("Chrome debugging pipe closed")
                view = view[written:]
            return request_id

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float = 5,
    ) -> dict[str, Any]:
        with self._call_lock:
            deadline = time.monotonic() + max(0, timeout)
            request_id = self._send_until(method, params, session_id, deadline)
            while True:
                response: dict[str, Any] | None = None
                for message in self._take_messages():
                    if message.get("id") == request_id:
                        response = message
                    elif "method" in message:
                        callback = self.on_event
                        if callback is not None:
                            callback(message)
                if response is not None:
                    if "error" in response:
                        raise RuntimeError("Chrome command failed")
                    result = response.get("result", {})
                    if not isinstance(result, dict):
                        raise ValueError("Invalid Chrome response")
                    return result

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("CDP command timed out")
                with self._state_lock:
                    read_fd = self._read_fd
                    closed = self._closed
                if closed or read_fd is None:
                    raise ConnectionError("Chrome debugging pipe closed")
                try:
                    ready, _, _ = select.select([read_fd, self._wake_r], [], [], remaining)
                except (OSError, ValueError) as error:
                    raise ConnectionError("Chrome debugging pipe closed") from error
                if self._wake_r in ready:
                    try:
                        os.read(self._wake_r, _READ_SIZE)
                    except OSError:
                        pass
                    raise ConnectionError("Chrome debugging pipe closed")
                if not ready:
                    raise TimeoutError("CDP command timed out")
                try:
                    chunk = os.read(read_fd, _READ_SIZE)
                except OSError as error:
                    raise ConnectionError("Chrome debugging pipe closed") from error
                if not chunk:
                    raise ConnectionError("Chrome debugging pipe closed")
                self._buffer.extend(chunk)
                if b"\0" not in self._buffer and len(self._buffer) > _MAX_FRAME:
                    raise ValueError("Chrome debugging frame too large")

    def _take_messages(self) -> list[dict[str, Any]]:
        messages = []
        while True:
            try:
                boundary = self._buffer.index(0)
            except ValueError:
                break
            frame = bytes(self._buffer[:boundary])
            del self._buffer[: boundary + 1]
            if not frame:
                continue
            if len(frame) > _MAX_FRAME:
                raise ValueError("Chrome debugging frame too large")
            try:
                message = json.loads(frame)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("Invalid Chrome debugging frame") from None
            if not isinstance(message, dict):
                raise ValueError("Invalid Chrome debugging frame")
            messages.append(message)
        return messages

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            process = self.process if self._owns_process else None
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass
        with self._write_lock:
            with self._state_lock:
                write_fd, read_fd = self._write_fd, self._read_fd
                self._write_fd = self._read_fd = None
        self._close_raw_fds(write_fd, read_fd)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        self._close_raw_fds(self._wake_r, self._wake_w)

    def __enter__(self) -> ChromeTransport:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
