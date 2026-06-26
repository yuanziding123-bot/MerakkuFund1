"""LLM-backed routing — let the main agent *decide* the next sub-agent.

Where :class:`SequentialRouter` walks a fixed plan, :class:`LLMRouter` asks the
model, given the current blackboard, which specialist to call next (or to stop).
This turns the loop from a pipeline into genuine supervision while staying safe:

  * the model only ever picks from the registered sub-agents (or ``stop``);
  * preconditions are stated in the prompt (risk needs a signal, etc.);
  * anything unparseable, unknown, or looping resolves to *stop*;
  * the Supervisor's ``max_iters`` is still the hard backstop.

The model is injected, so this is unit-testable with a scripted fake.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .blackboard import Blackboard

_SYSTEM = (
    "You are the supervisor of a prediction-market research loop. Each turn you "
    "pick the SINGLE next specialist sub-agent to run, or stop.\n"
    "Rules:\n"
    "- 'signal' needs 'data' to have run; 'risk' needs 'signal'; 'execution' needs "
    "a non-hold 'risk' decision.\n"
    "- Do not re-run a sub-agent whose result is already on the board unless it failed.\n"
    "- Stop once the goal is answered (e.g. risk produced a decision, or a step halted).\n"
    'Reply with ONLY a JSON object: {"next": "<agent-name|stop>", "why": "<short>"}'
)


def _board_state(bb: Blackboard) -> str:
    filled = [k for k in ("data", "signal", "risk", "execution")
              if getattr(bb, k) not in (None, {}, [])]
    ran = [r.agent for r in bb.trace]
    last = bb.trace[-1].summary if bb.trace else "(nothing yet)"
    return (f"goal: {bb.goal}\n"
            f"board filled: {', '.join(filled) or '(empty)'}\n"
            f"already ran: {', '.join(ran) or '(none)'}\n"
            f"last result: {last}")


class LLMRouter:
    """Choose the next sub-agent via the model. Safe-by-default (stops on doubt)."""

    def __init__(self, llm: Any, *, max_repeat: int = 1) -> None:
        self.llm = llm
        self.max_repeat = max_repeat        # how often the same agent may repeat

    def next_agent(self, bb: Blackboard, agents: dict) -> str | None:
        menu = "\n".join(f"- {n}: {a.description}" for n, a in agents.items())
        user = (f"{_board_state(bb)}\n\navailable sub-agents:\n{menu}\n\n"
                "Which next? JSON only.")
        try:
            resp = self.llm.invoke([("system", _SYSTEM), ("user", user)])
            text = getattr(resp, "content", resp)
            if isinstance(text, list):      # some providers return content blocks
                text = "".join(b.get("text", "") if isinstance(b, dict) else str(b)
                               for b in text)
            choice = self._parse(str(text), agents)
        except Exception:
            return None                      # never let routing crash the loop
        if choice in (None, "stop"):
            return None
        # loop guard: don't let the model spin on one agent
        if [r.agent for r in bb.trace].count(choice) >= self.max_repeat:
            return None
        return choice

    @staticmethod
    def _parse(text: str, agents: dict) -> str | None:
        names = set(agents) | {"stop"}
        m = re.search(r'"next"\s*:\s*"([^"]+)"', text)
        if m and m.group(1).strip().lower() in names:
            return m.group(1).strip().lower()
        try:                                 # tolerate bare JSON / extra prose
            obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
            cand = str(obj.get("next", "")).strip().lower()
            if cand in names:
                return cand
        except Exception:
            pass
        for n in names:                      # last resort: name mentioned in text
            if re.search(rf"\b{re.escape(n)}\b", text.lower()):
                return n
        return None
