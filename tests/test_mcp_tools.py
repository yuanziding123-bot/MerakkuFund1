"""Tests for runtime MCP tool loading (no network)."""
from __future__ import annotations

from polyagents.default_config import DEFAULT_CONFIG
from polyagents.mcp_tools import default_mcp_servers, load_mcp_tools_sync


def test_default_config_registers_polymarket_docs():
    servers = default_mcp_servers(DEFAULT_CONFIG)
    assert "polymarket-docs" in servers
    assert servers["polymarket-docs"]["url"] == "https://docs.polymarket.com/mcp"
    assert servers["polymarket-docs"]["transport"] == "streamable_http"


def test_no_servers_returns_no_tools_without_network():
    # Empty server map short-circuits — never imports adapters or hits the net.
    assert load_mcp_tools_sync(servers={}) == []
