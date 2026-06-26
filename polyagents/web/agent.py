"""The chat agent behind the web UI — Claude + polyagents trading tools.

Skills are discoverable: every ``skills/<id>/SKILL.md`` is registered and shown
in the UI for the user to select. The selected skills' instructions are composed
into the agent's system prompt (the tool surface is shared). Add a skill = drop a
new ``SKILL.md`` folder; it appears in the picker automatically.

Needs ``ANTHROPIC_API_KEY`` (the agent reasons via Claude); the tools themselves
are deterministic / paper-only.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import StructuredTool

from polyagents import mcp_server
from polyagents.default_config import DEFAULT_CONFIG
from polyagents.mcp_servers import compliance as compliance_mcp
from polyagents.mcp_servers import crypto as crypto_mcp
from polyagents.mcp_servers import polydata as polydata_mcp

_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

# Reuse the exact MCP tool functions so the web agent and an Alpha DevBox host
# expose an identical surface (one source of truth). The crypto / polydata /
# compliance tools are bound too, so the cross-market-arb, research and
# risk-check skills work in chat.
def run_trading_strategy(token_id: str = "", strategy: str = "full") -> str:
    """Run the multi-agent strategy loop on one Polymarket market and return the
    agent-loop trace plus the sized decision.

    A supervisor (the main agent) dispatches specialist sub-agents in sequence:
    DataAgent (Layer-1 data) -> SignalAgent (probability read, LLM) -> RiskAgent
    (calibration + Kelly + risk gates). Use this when the user wants to "run the
    strategy / agents" on a market end-to-end, not just one step.

    token_id: a market side's token id from scan_markets (empty = most active market).
    strategy: 'research' (data only), 'signal' (data+signal), or 'full' (data+signal+risk).
    """
    from polyagents.orchestration import run_strategy

    eng = mcp_server.engine()
    market = mcp_server._get_market(token_id) if token_id else eng.most_active_market()
    if market is None:
        return f"No market found for token_id={token_id!r}."
    bb = run_strategy(market, graph=eng, config=eng.config, strategy=strategy)
    lines = [bb.summary()]
    if bb.risk:
        lines.append(f"\nDecision: {bb.risk['action'].upper()} "
                     f"(edge {bb.risk['edge']:+.1%}, APY {bb.risk['apy']:+.0%}, "
                     f"size ${bb.risk['size_usdc']:,.0f})")
    return "\n".join(lines)


def propose_hypothesis(statement: str, category: str = "", feature_set: str = "",
                       success_criteria: str = "") -> str:
    """Surface a testable idea as a Hypothesis the user can Promote to Lab (gate 1).

    READ-ONLY: this only *proposes* — it does NOT create the object. Call it when
    the user has an idea worth validating (e.g. "crypto news updates the LLM's
    probability faster than the market"). The UI renders the proposal as a card
    with a Promote button; promoting (an explicit user click) is what creates the
    Hypothesis and moves it into Lab. Never trade.

    statement: the hypothesis in one sentence.
    category: the market slice it applies to (crypto / politics / macro / …).
    feature_set: comma-separated features it would use (e.g. "news_event, rag_similar").
    success_criteria: what would confirm it (e.g. "brier_delta < -0.01, ece < 0.04, n >= 30").
    """
    return (f"Proposed a Hypothesis for the user to review and Promote:\n"
            f"- statement: {statement}\n- category: {category or 'n/a'}\n"
            f"- features: {feature_set or 'n/a'}\n- success: {success_criteria or 'n/a'}\n"
            "(Shown as a card — the user decides whether to Promote it to Lab.)")


_TOOL_FUNCS = [
    mcp_server.scan_markets,
    mcp_server.market_snapshot,
    mcp_server.find_similar_markets,
    propose_hypothesis,
    run_trading_strategy,
    mcp_server.size_position,
    mcp_server.paper_execute,
    mcp_server.portfolio_status,
    mcp_server.settle_markets,
    mcp_server.pnl_report,
    mcp_server.evaluation_report,
    crypto_mcp.crypto_price,
    crypto_mcp.crypto_24h,
    crypto_mcp.crypto_klines,
    polydata_mcp.list_events,
    polydata_mcp.recent_trades,
    polydata_mcp.price_history,
    compliance_mcp.verify_trade_math,
    compliance_mcp.audit_log,
    compliance_mcp.risk_limits,
]


# Tools that have a side effect (size/execute/settle/run a strategy). The Ask
# mode is READ-ONLY per the v0.2 PRD (§二 / §八-B ToolManifest): these must NOT
# be injected, so a chat in Ask can never place a trade or mutate the portfolio.
WRITE_TOOLS = {"run_trading_strategy", "size_position", "paper_execute", "settle_markets"}


def build_tools(readonly: bool = False) -> list:
    """The chat tool surface. ``readonly=True`` (Ask mode) drops the write tools
    in :data:`WRITE_TOOLS`, leaving only scan / snapshot / data / evaluate reads."""
    funcs = [f for f in _TOOL_FUNCS if not (readonly and f.__name__ in WRITE_TOOLS)]
    return [StructuredTool.from_function(fn) for fn in funcs]


# ----- skills registry -------------------------------------------------------

def list_mcp_servers() -> list[dict]:
    """Registered MCP servers + their tools, for the web UI's MCP panel.

    ``in_chat`` marks the servers whose tools the web chat agent actually binds
    (the rest are registered in .mcp.json for an external host)."""
    from polyagents import mcp_server
    from polyagents.mcp_servers import compliance, crypto, polydata, qlib_backtest

    local = [
        ("polyagents", "core trading engine — scan / size / paper-trade / settle / evaluate", mcp_server.mcp, "in-process"),
        ("crypto", "cross-market crypto prices (Coinbase, no key)", crypto.mcp, "stdio"),
        ("polydata", "Polymarket events / price history / recent trades", polydata.mcp, "stdio"),
        ("compliance", "trade-math verification + audit log + risk limits", compliance.mcp, "stdio"),
        ("qlib-backtest", "factor → model → backtest over the SQLite history (qlib venv)", qlib_backtest.mcp, "qlib-venv"),
    ]
    bound = {"polyagents", "crypto", "polydata", "compliance"}   # what the chat agent binds
    out: list[dict] = []
    for sid, desc, m, transport in local:
        try:
            tools = [t.name for t in m._tool_manager.list_tools()]
        except Exception:
            tools = []
        out.append({"id": sid, "name": m.name, "description": desc,
                    "transport": transport, "tools": tools, "in_chat": sid in bound})
    out.append({"id": "polymarket-docs", "name": "polymarket-docs",
                "description": "official Polymarket documentation search (remote)",
                "transport": "http", "tools": ["search_polymarket_documentation",
                                               "query_docs_filesystem_polymarket_documentation"],
                "in_chat": False})
    return out


def _parse_skill(text: str) -> tuple[str, str, str, str]:
    """Return (name, description, category, body) from a SKILL.md frontmatter."""
    name = desc = category = ""
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            front, body = parts[1], parts[2]
            for line in front.splitlines():
                if line.lower().startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.lower().startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                elif line.lower().startswith("category:"):
                    category = line.split(":", 1)[1].strip()
    return name, desc, category, body.strip()


def list_skills() -> list[dict]:
    """All registered skills: id (folder), name, description, category, body."""
    out: list[dict] = []
    if not _SKILLS_DIR.exists():
        return out
    for d in sorted(p for p in _SKILLS_DIR.iterdir() if p.is_dir()):
        f = d / "SKILL.md"
        if not f.exists():
            continue
        try:
            name, desc, category, body = _parse_skill(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        out.append({"id": d.name, "name": name or d.name, "description": desc,
                    "category": category or "General", "body": body})
    return out


def _compose_prompt(selected_ids: list[str] | None) -> str:
    skills = list_skills()
    if selected_ids:
        chosen = [s for s in skills if s["id"] in selected_ids]
        skills = chosen or skills
    if not skills:
        return "You are a disciplined Polymarket trading assistant. Use the tools provided."
    if len(skills) == 1:
        return skills[0]["body"]
    header = ("You have multiple skills enabled. Use the one that fits the user's "
              "request; respect the most restrictive discipline when they overlap.\n\n")
    return header + "\n\n---\n\n".join(f"## SKILL: {s['name']}\n{s['body']}" for s in skills)


# ----- agent -----------------------------------------------------------------

# Models the Ask composer's selector offers. Keys are what the UI sends; values
# are the real Anthropic model ids. The default falls back to the config model.
ASK_MODELS: dict[str, str] = {
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-opus-4-8": "claude-opus-4-8",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
}


def resolve_model(name: str | None) -> str:
    """Validate a requested model against the allow-list; fall back to default."""
    if name and name in ASK_MODELS:
        return ASK_MODELS[name]
    return DEFAULT_CONFIG["anthropic_model"]


def build_agent(selected_ids: list[str] | None = None, llm=None, model: str | None = None,
                readonly: bool = True):
    """Compile the ReAct agent (Claude + tools + selected skills' prompt).

    ``model`` (from the Ask composer's selector) is validated against
    :data:`ASK_MODELS`; an unknown / missing value uses the configured default.
    ``readonly`` defaults to True — the web chat IS the Ask mode, so it gets the
    read-only tool subset (no trading / portfolio mutation).
    """
    from langgraph.prebuilt import create_react_agent

    if llm is None:
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(
            model=resolve_model(model),
            temperature=DEFAULT_CONFIG.get("anthropic_temperature", 0.0),
        )
    prompt = _compose_prompt(selected_ids)
    if readonly:
        prompt = _ASK_PREAMBLE + "\n\n" + prompt
    return create_react_agent(llm, build_tools(readonly=readonly), prompt=prompt)


_ASK_PREAMBLE = (
    "You are in ASK mode: read-only. You scan, explain, compare and evaluate — you "
    "NEVER place trades or change the portfolio (those tools are not available here). "
    "When the user has an idea worth testing, call `propose_hypothesis` to surface it "
    "as a Hypothesis card the user can Promote to Lab — do not pretend to have created "
    "or backtested it yourself. Ground claims in tool results; cite sample size and "
    "confidence intervals when you report evaluation numbers."
)
