"""Tests must never send lighting commands to external hosts."""
import socket

import pytest


@pytest.fixture(autouse=True)
def local_network_only(monkeypatch):
    original = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        if host not in (None, "localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1"):
            raise OSError("External network access is disabled in tests")
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
