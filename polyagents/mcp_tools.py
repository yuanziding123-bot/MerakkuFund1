"""Runtime MCP integration — expose configured MCP servers as LangGraph tools.

Two ways the Polymarket MCP is wired into this project:

  * **Dev-time** — ``.mcp.json`` registers the server with Claude Code, so the
    coding agent can look up Polymarket docs while building polyagents.
  * **Run-time (here)** — ``langchain-mcp-adapters`` turns the same server's
    tools into LangChain tools a LangGraph agent can call. The Polymarket docs
    MCP lets a Layer 2 analyst look up exact API/contract/endpoint details on
    demand instead of hard-coding them.

The docs MCP is a *documentation* server (search + read), not a market-data
feed — prices, the order book and trades come from
:class:`~polyagents.dataflows.polymarket_client.PolymarketDataClient`.

``langchain-mcp-adapters`` is an optional dependency and imported lazily, so the
data-collection layer never pays for it unless MCP tools are actually loaded.
"""
from __future__ import annotations

from typing import Any

from polyagents.default_config import DEFAULT_CONFIG


def default_mcp_servers(config: dict | None = None) -> dict[str, Any]:
    """The MCP server map from config (mirrors ``.mcp.json``)."""
    return dict((config or DEFAULT_CONFIG).get("mcp_servers", {}))


async def load_mcp_tools(config: dict | None = None, servers: dict[str, Any] | None = None) -> list:
    """Return LangChain tools for every configured MCP server.

    Empty / no servers → ``[]`` (no network, no import). Used by the later
    decision layer to give agents the Polymarket docs tools.
    """
    servers = servers if servers is not None else default_mcp_servers(config)
    if not servers:
        return []
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(servers)
    return await client.get_tools()


def load_mcp_tools_sync(config: dict | None = None, servers: dict[str, Any] | None = None) -> list:
    """Blocking convenience wrapper around :func:`load_mcp_tools`."""
    import asyncio

    return asyncio.run(load_mcp_tools(config=config, servers=servers))
