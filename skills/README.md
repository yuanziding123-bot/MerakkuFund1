# polyagents skills

Agent **skills** that teach a host (Alpha DevBox / Claude) how to use polyagents.
A host agent loads these skills + connects to the **`polyagents` MCP server**
(`polyagents/mcp_server.py`), then drives the trading workflow through chat —
the platform is the chat shell, polyagents provides the capabilities.

```
chat (Alpha DevBox)  →  agent (Claude)  →  skills/*  +  polyagents MCP tools
```

## Anatomy of a skill

Each skill is a folder with a `SKILL.md`:

```
skills/
  polymarket-trading/
    SKILL.md          # frontmatter (name, description) + workflow instructions
  <your-next-skill>/
    SKILL.md
    scripts/          # optional helper scripts the skill references
```

`SKILL.md` frontmatter:

```yaml
---
name: kebab-case-name
description: One line — WHAT it does and WHEN the agent should use it. The host
  routes to a skill by matching the user's request against this description.
---
```

The body is plain instructions for the agent: the goal, the step-by-step
workflow (referencing the MCP tools by name), and the discipline/guardrails.

## Adding a new skill (the easy path)

1. **Expose capabilities** as MCP tools in `polyagents/mcp_server.py`
   (`@mcp.tool()` functions — deterministic, JSON-returning, no internal LLM).
2. **Write the skill**: `skills/<name>/SKILL.md` describing when to use it and
   how to compose those tools.
3. That's it — the host picks it up. No change to Alpha DevBox.

Ideas for next skills (same pattern): `mean-reversion`, `event-driven` (news +
sentiment), `portfolio-review`, `backtest` (over the SQLite history),
`market-research` (RAG-heavy).

## Running the MCP server

```bash
python -m polyagents.mcp_server          # stdio (Claude / Alpha DevBox)
python -m polyagents.mcp_server --http    # streamable-http on :8000
```

Register it where the host reads MCP config, e.g.:

```json
{ "mcpServers": { "polyagents": {
    "command": "python", "args": ["-m", "polyagents.mcp_server"] } } }
```

All tools are **paper / read-only** by default — no real orders, no keys needed.
