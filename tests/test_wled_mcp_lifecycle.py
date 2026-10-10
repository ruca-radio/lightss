"""MCP owns and closes the device sessions it creates."""

import io
import sys
from unittest.mock import Mock

import pytest

import mcp_light


@pytest.mark.parametrize("explicit_host", [True, False])
def test_mcp_entry_point_closes_sessions_at_eof(monkeypatch, explicit_host):
    client = Mock()
    client.names.return_value = []
    factory = Mock(return_value=client)
    monkeypatch.setattr(mcp_light.lightctl, "LightClient", factory)
    monkeypatch.setattr(mcp_light.fleet.LightFleet, "from_config", factory)
    monkeypatch.setattr(mcp_light, "seed_effect_catalog", Mock())
    monkeypatch.setattr(sys, "argv", ["mcp-light"] + (["--host", "10.0.0.2"] if explicit_host else []))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert mcp_light.main() == 0
    if explicit_host:
        assert factory.call_args.kwargs["use_python_wled"] is True
    client.close.assert_called_once()


def test_mcp_cleanup_does_not_close_a_session_twice():
    client = Mock()
    server = mcp_light.McpServer(client)
    server._cleanup()
    server._cleanup()
    client.close.assert_called_once()
