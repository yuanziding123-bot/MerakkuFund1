"""Central configuration, mirroring TradingAgents' ``default_config.py``.

A single ``DEFAULT_CONFIG`` dict is the source of truth. ``POLYAGENTS_*`` env
vars override individual keys, coerced to the type of the existing default so a
plain string in ``.env`` Just Works. ``DEFAULT_CONFIG.copy()`` is what callers
pass to the graph.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional — env vars still work without python-dotenv installed
    from dotenv import load_dotenv

    _PROJECT_ROOT = Path(__file__).resolve().parents[1]
    load_dotenv(_PROJECT_ROOT / ".env")
except Exception:  # pragma: no cover - dotenv is a convenience, not a requirement
    _PROJECT_ROOT = Path(__file__).resolve().parents[1]


_POLYAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".polyagents")

# env-var -> config-key overrides. Add a row to expose a new key.
_ENV_OVERRIDES = {
    "POLYAGENTS_GAMMA_BASE": "gamma_base",
    "POLYAGENTS_CLOB_BASE": "clob_base",
    "POLYAGENTS_DATA_API_BASE": "data_api_base",
    "POLYAGENTS_MARKETS_LIMIT": "markets_limit",
    "POLYAGENTS_PRICE_INTERVAL": "price_interval",
    "POLYAGENTS_PRICE_FIDELITY": "price_fidelity",
    "POLYAGENTS_TRADES_LOOKBACK_HOURS": "trades_lookback_hours",
    "POLYAGENTS_NEWS_MAX_RESULTS": "news_max_results",
    "POLYAGENTS_HTTP_TIMEOUT": "http_timeout",
}


def _coerce(value: str, reference):
    if isinstance(reference, bool):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        config[key] = _coerce(raw, config.get(key))
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_root": str(_PROJECT_ROOT),
    "results_dir": os.getenv("POLYAGENTS_RESULTS_DIR", os.path.join(_POLYAGENTS_HOME, "logs")),

    # Polymarket public endpoints (all read-only — no keys needed for the data layer)
    "gamma_base": "https://gamma-api.polymarket.com",
    "clob_base": "https://clob.polymarket.com",
    "data_api_base": "https://data-api.polymarket.com",

    # Order book via the official py-clob-client SDK (Merakku v3.0 Layer 1 P0).
    # Public L1 reads need no keys; set use_clob_sdk False to force the REST path.
    "polymarket_chain_id": 137,        # Polygon
    "use_clob_sdk": True,

    # Market discovery
    "markets_limit": 500,             # how many active markets to page from Gamma

    # Price history
    "price_interval": "1h",           # bucket size requested from prices-history
    "price_fidelity": 60,             # minutes per point

    # Trade-flow window
    "trades_lookback_hours": 24,      # how far back to read /trades for flow imbalance

    # News
    "news_max_results": 5,            # max Tavily results per market

    # HTTP
    "http_timeout": 20.0,

    # News API key — read from env, never hard-coded
    "tavily_api_key": os.getenv("TAVILY_API_KEY"),

    # MCP servers exposed to runtime agents (mirrors .mcp.json used by Claude
    # Code at dev-time). The Polymarket docs MCP is a documentation search/read
    # server — see polyagents/mcp_tools.py.
    "mcp_servers": {
        "polymarket-docs": {
            "url": "https://docs.polymarket.com/mcp",
            "transport": "streamable_http",
        },
    },
})
