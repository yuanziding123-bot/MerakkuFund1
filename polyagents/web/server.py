"""FastAPI chat server — streams the polyagents trading agent to the browser.

    python -m polyagents.web            # http://127.0.0.1:8000

GET  /             → the chat UI (web/static/index.html)
GET  /api/skills   → registered skills (for the left-panel picker)
GET  /api/portfolio→ current paper portfolio (for the right panel)
POST /api/chat     → SSE: token / tool / tool_result / done / error
                     body: { messages:[...], skills:["polymarket-trading", ...] }

The engine (paper portfolio) persists across requests; the agent is rebuilt per
request from the selected skills. Needs ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from dataclasses import asdict

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from polyagents import mcp_server
from polyagents.default_config import DEFAULT_CONFIG
from polyagents.lab.backtest import BacktestRunner, get_backtest_run, get_report
from polyagents.lab.schemas import BacktestRequest, CreateHypothesisRequest
from polyagents.lab.service import create_hypothesis, default_repository, get_hypothesis, list_hypotheses
from polyagents.storage.db import DataStore

from polyagents.runtime.session import AgentSession

from .agent import build_agent, build_general_agent, list_mcp_servers, list_skills
from .general_backend import chosen_general_backend, stream_devbox_general
from .router import classify
from .uploads import UploadCache, build_message_content, extract, public_view

_CLASSIFIER_LLM = None


def _classifier_llm():
    """Cheap Haiku classifier for ambiguous questions (built once, lazily)."""
    global _CLASSIFIER_LLM
    if _CLASSIFIER_LLM is None:
        try:
            from langchain_anthropic import ChatAnthropic
            _CLASSIFIER_LLM = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0.0)
        except Exception:
            _CLASSIFIER_LLM = False        # unavailable → classify falls back to 'domain'
    return _CLASSIFIER_LLM or None

_REPO = str(Path(__file__).resolve().parents[2])

_STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="polyagents chat")
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


def _qlib_python() -> str:
    configured = DEFAULT_CONFIG.get("qlib_python")
    if configured and Path(str(configured)).exists():
        return str(configured)
    return sys.executable


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(_STATIC / "index.html"))


@app.get("/api/skills")
async def skills() -> JSONResponse:
    return JSONResponse([{"id": s["id"], "name": s["name"], "description": s["description"],
                          "category": s.get("category", "General")}
                         for s in list_skills()])


@app.get("/api/mcp")
async def mcp_servers() -> JSONResponse:
    return JSONResponse(list_mcp_servers())


@app.get("/api/portfolio")
async def portfolio() -> JSONResponse:
    try:
        return JSONResponse(mcp_server.portfolio_status())
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.get("/api/evaluation")
async def evaluation() -> JSONResponse:
    """Calibration / skill report: does p_cal beat the market baseline?"""
    try:
        return JSONResponse({"report": mcp_server.evaluation_report()})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.get("/api/markets")
async def markets(limit: int = 40, min_volume: float = 20000.0) -> JSONResponse:
    """Live Polymarket markets (one row per market, YES+NO prices) for the Market
    tab. Uses polyagents' own data layer — no external market-data MCP needed."""
    try:
        eng = mcp_server.engine()
        raw = eng.client.list_active_markets(limit=eng.config["markets_limit"])
        by_cond: dict[str, dict] = {}
        for m in eng.client.to_markets(raw):
            row = by_cond.setdefault(m.condition_id, {
                "question": m.question, "condition_id": m.condition_id,
                "volume_24h": m.volume_24h, "liquidity": m.liquidity,
                "spread": m.spread, "days_to_expiry": round(m.days_to_expiry, 1),
                "yes_price": None, "no_price": None, "yes_token": None, "no_token": None,
            })
            if m.outcome == "YES":
                row["yes_price"], row["yes_token"] = m.price, m.token_id
            else:
                row["no_price"], row["no_token"] = m.price, m.token_id
        rows = [r for r in by_cond.values()
                if r["volume_24h"] >= min_volume and (r["yes_price"] or 0) > 0.005]
        rows.sort(key=lambda r: r["volume_24h"], reverse=True)
        return JSONResponse(rows[:limit])
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.get("/api/backtest")
async def backtest(forward_bars: int = 5) -> JSONResponse:
    """Run the qlib backtest in the qlib venv (cross-venv) and return metrics +
    an equity curve for the Backtest tab. Factor→model→backtest over the SQLite
    candle history, leakage-safe time split."""
    py = _qlib_python()
    snippet = (
        "import json;from polyagents.mcp_servers.qlib_backtest import run_backtest,data_summary;"
        f"print('@@'+json.dumps({{'summary':data_summary(),'backtest':run_backtest(forward_bars={int(forward_bars)})}}))"
    )
    try:
        env = {**os.environ, "PYTHONPATH": _REPO, "PYTHONUTF8": "1"}
        p = subprocess.run([py, "-c", snippet], capture_output=True, text=True,
                           env=env, cwd=_REPO, timeout=180)
        if p.returncode != 0:
            return JSONResponse({"error": (p.stderr or "backtest failed")[-500:]})
        line = next((l for l in p.stdout.splitlines() if l.startswith("@@")), None)
        if not line:
            return JSONResponse({"error": (p.stdout or p.stderr or "no output")[-500:]})
        return JSONResponse(json.loads(line[2:]))
    except FileNotFoundError:
        return JSONResponse({"error": f"qlib python not found: {py}"})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.get("/api/lab/hypotheses")
async def lab_hypotheses() -> JSONResponse:
    return JSONResponse({"items": list_hypotheses(), "next_cursor": None})


@app.post("/api/lab/hypotheses")
async def lab_create_hypothesis(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        response = create_hypothesis(CreateHypothesisRequest(**payload))
        return JSONResponse(asdict(response))
    except Exception as exc:
        return JSONResponse({"error": {"code": "validation_error", "message": str(exc)}}, status_code=400)


@app.get("/api/lab/hypotheses/{id}")
async def lab_hypothesis_detail(id: str) -> JSONResponse:
    hypothesis = get_hypothesis(id)
    if hypothesis is None:
        return JSONResponse({"error": {"code": "not_found", "message": f"hypothesis not found: {id}"}}, status_code=404)
    return JSONResponse({
        "hypothesis": asdict(hypothesis),
        "reports": default_repository().reports_for_hypothesis(id),
        "audit_tail": [],
    })


@app.post("/api/lab/hypotheses/{id}/backtests")
async def lab_run_backtest(id: str, request: Request) -> JSONResponse:
    store = None
    try:
        payload = await request.json()
        body = {**payload, "hypothesis_id": id}
        store = DataStore(DEFAULT_CONFIG["db_path"])
        result = BacktestRunner(store=store).run(BacktestRequest(**body))
        return JSONResponse({
            "backtest_run_id": result.id,
            "status": result.status,
            "report_id": result.report_id,
        })
    except Exception as exc:
        return JSONResponse({"error": {"code": "evaluation_failed", "message": str(exc)}}, status_code=400)
    finally:
        if store is not None:
            store.close()


@app.get("/api/lab/backtests/{id}")
async def lab_backtest_status(id: str) -> JSONResponse:
    result = get_backtest_run(id)
    if result is None:
        return JSONResponse({"error": {"code": "not_found", "message": f"backtest not found: {id}"}}, status_code=404)
    return JSONResponse(asdict(result))


@app.get("/api/lab/reports/{id}")
async def lab_report(id: str) -> JSONResponse:
    report = get_report(id)
    if report is None:
        return JSONResponse({"error": {"code": "not_found", "message": f"report not found: {id}"}}, status_code=404)
    return JSONResponse(report)


@app.get("/api/lab/system/status")
async def lab_system_status() -> JSONResponse:
    return JSONResponse({
        "tool_manifest_hash": "tm_lab_v1",
        "permission_policy": "lab-v1",
        "data_sources": [{"id": "polymarket", "status": "unknown", "last_checked_at": None}],
        "live_tools_enabled": False,
    })


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ----- strategy run (multi-agent loop, streamed live) -----------------------

def _board_dict(bb) -> dict:
    return {
        "goal": bb.goal,
        "data": bool(bb.data), "signal": bb.signal, "risk": bb.risk,
        "execution": bb.execution,
        "trace": [{"agent": r.agent, "ok": r.ok, "summary": r.summary, "halt": r.halt}
                  for r in bb.trace],
    }


async def _strategy_stream(token_id: str, strategy: str, use_llm: bool) -> AsyncIterator[str]:
    """Run the supervisor in a worker thread; bridge its on_event callback to SSE
    so the browser sees each sub-agent start/finish live."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def emit(ev: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, ev)

    def work() -> None:
        try:
            from polyagents import mcp_server
            from polyagents.orchestration import LLMRouter, run_strategy

            eng = mcp_server.engine()
            market = mcp_server._get_market(token_id) if token_id else eng.most_active_market()
            if market is None:
                emit({"type": "error", "message": f"no market for token_id={token_id!r}"})
                return
            emit({"type": "market", "question": market.question, "token_id": market.token_id})
            if strategy != "research" and not os.getenv("ANTHROPIC_API_KEY"):
                emit({
                    "type": "error",
                    "message": (
                        "Strategy signal/full/trade runs need ANTHROPIC_API_KEY. "
                        "Use Strategy=research for data-only local demos, or add ANTHROPIC_API_KEY to .env."
                    ),
                })
                return
            router = LLMRouter(eng._get_llm()) if use_llm else None
            bb = run_strategy(market, graph=eng, config=eng.config, strategy=strategy,
                              router=router, on_event=emit)
            emit({"type": "final", "summary": bb.summary(), "board": _board_dict(bb)})
        except Exception as exc:
            emit({"type": "error", "message": str(exc)})
        finally:
            emit({"type": "__end__"})

    threading.Thread(target=work, daemon=True).start()
    while True:
        ev = await q.get()
        if ev.get("type") == "__end__":
            break
        yield _sse(ev)


@app.post("/api/strategy")
async def strategy(request: Request) -> StreamingResponse:
    body = await request.json()
    return StreamingResponse(
        _strategy_stream(body.get("token_id", ""), body.get("strategy", "full"),
                         body.get("router") == "llm"),
        media_type="text/event-stream")


# ----- objects (the 5 financial objects + 3-gate state machine) -------------

_OBJECT_STORE = None
_AUDIT_STORE = None


def _object_store():
    global _OBJECT_STORE
    if _OBJECT_STORE is None:
        from polyagents.storage.objects_store import ObjectStore
        db = DEFAULT_CONFIG.get("db_path")
        path = (Path(db).with_name("objects.db") if db
                else Path.home() / ".polyagents" / "objects.db")
        _OBJECT_STORE = ObjectStore(path)
    return _OBJECT_STORE


def _audit():
    global _AUDIT_STORE
    if _AUDIT_STORE is None:
        from polyagents.storage.audit_store import AuditStore
        db = DEFAULT_CONFIG.get("db_path")
        path = (Path(db).with_name("audit.db") if db
                else Path.home() / ".polyagents" / "audit.db")
        _AUDIT_STORE = AuditStore(path)
    return _AUDIT_STORE


@app.get("/api/audit")
async def audit(limit: int = 80, session: str = "") -> JSONResponse:
    try:
        return JSONResponse({"events": _audit().recent(limit=limit, session_id=session or None),
                             "total": _audit().count()})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


_COMMON_FIELDS = {"id", "type", "version", "state", "owner", "snapshot_id",
                  "lineage", "eval_summary", "created_at"}


def _object_view(fo) -> dict:
    """Serialize an object + derive the gate / state-machine info the UI shows."""
    from polyagents.objects import eval_gate_passed, next_states, to_dict
    d = to_dict(fo)
    title = (getattr(fo, "statement", "") or getattr(fo, "question", "")
             or getattr(fo, "hypothesis_id", "") or fo.id)
    return {
        "id": fo.id, "type": fo.type, "version": fo.version, "state": fo.state,
        "snapshot_id": fo.snapshot_id, "created_at": fo.created_at, "title": title,
        "lineage": d["lineage"], "eval_summary": d.get("eval_summary"),
        "next_states": sorted(next_states(fo)),
        "gates": {
            "g1": fo.state in ("lab", "paper", "live", "archived") and fo.state != "draft",
            "g2": bool(fo.eval_summary) and eval_gate_passed(fo.eval_summary),
            "g3": fo.state in ("live",),
        },
        "payload": {k: v for k, v in d.items() if k not in _COMMON_FIELDS},
    }


@app.get("/api/objects")
async def objects(type: str = "", state: str = "") -> JSONResponse:
    try:
        store = _object_store()
        items = [_object_view(o) for o in store.list(type=type or None, state=state or None)]
        return JSONResponse({"objects": items, "counts": store.counts()})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.post("/api/objects")
async def create_object(request: Request) -> JSONResponse:
    """Mint a Hypothesis (the object the Ask→Lab gate produces)."""
    try:
        from polyagents.objects import make

        body = await request.json()
        statement = (body.get("statement") or "").strip()
        if not statement:
            return JSONResponse({"error": "statement is required"}, status_code=400)
        feats = body.get("feature_set") or []
        if isinstance(feats, str):
            feats = [s.strip() for s in feats.split(",") if s.strip()]
        snap = f"snap_{uuid.uuid4().hex[:8]}"
        h = make("hypothesis", snapshot_id=snap, statement=statement,
                 category_filter=(body.get("category") or "").strip(),
                 feature_set=tuple(feats),
                 prompt_version=body.get("prompt_version", ""),
                 model_version=DEFAULT_CONFIG.get("anthropic_model", ""))
        _object_store().save(h)
        _audit().log("web:objects", "object.create",
                     {"id": h.id, "type": h.type, "gate": "ask->hypothesis"}, mode="ask")
        return JSONResponse(_object_view(h))
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.post("/api/backtest_replay")
async def backtest_replay(request: Request) -> JSONResponse:
    """Historical replay alpha test: run a deterministic price signal over
    resolved markets in a slice (strictly PIT) and score vs the market baseline.
    Runs in a worker thread (sequential network fetches)."""
    body = await request.json()
    category = body.get("category") or None
    frac = float(body.get("frac", 0.5))
    signal = body.get("signal", "momentum")
    max_markets = int(body.get("max_markets", 30))

    def work():
        from polyagents import mcp_server
        from polyagents.lab.backtest import BacktestRunner, momentum_signal, naive_signal

        fn = naive_signal if signal == "naive" else momentum_signal
        runner = BacktestRunner(client=mcp_server.engine().client, predict_frac=frac,
                                signal_fn=fn, max_markets=max_markets)
        out = runner.replay(category=category)
        s = out["summary"]
        return {"n_markets": out["n_markets"], "category": category, "predict_frac": frac,
                "signal": out["signal"],
                "summary": {"n": s.n, "brier_model": s.brier_model, "brier_market": s.brier_market,
                            "brier_delta": s.brier_delta, "ci": list(s.brier_delta_ci),
                            "ece": s.ece, "beats_market": s.beats_market,
                            "sample_adequate": s.sample_adequate}}
    try:
        return JSONResponse(await asyncio.to_thread(work))
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.post("/api/objects/alpha_test")
async def alpha_test_object(request: Request) -> JSONResponse:
    """Run the Lab alpha test on a hypothesis: does its p_cal beat the market
    baseline (bootstrap-CI Brier skill) over resolved predictions in its slice?
    Attaches the resulting EvalSummary to the object so gate 2 can read it."""
    try:
        from dataclasses import replace

        from polyagents import mcp_server
        from polyagents.evaluation.alpha import alpha_test

        body = await request.json()
        store = _object_store()
        fo = store.get(body.get("id"))
        if fo is None:
            return JSONResponse({"error": f"object {body.get('id')!r} not found"}, status_code=404)
        category = getattr(fo, "category_filter", "") or None
        records = mcp_server.engine().memory.all()
        summary = alpha_test(records, category=category)
        fo2 = store.save(replace(fo, eval_summary=summary))
        view = _object_view(fo2)
        view["alpha"] = {"n": summary.n, "brier_model": summary.brier_model,
                         "brier_market": summary.brier_market, "brier_delta": summary.brier_delta,
                         "ci": list(summary.brier_delta_ci), "ece": summary.ece,
                         "beats_market": summary.beats_market,
                         "sample_adequate": summary.sample_adequate, "category": category}
        return JSONResponse(view)
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.post("/api/objects/promote")
async def promote_object(request: Request) -> JSONResponse:
    try:
        from polyagents.objects import IllegalTransition

        body = await request.json()
        oid, to_state = body.get("id"), body.get("to_state")
        try:
            moved = _object_store().promote(
                oid, to_state, promoted_by=body.get("by", "user:web"),
                evidence_ref=body.get("evidence"))
        except IllegalTransition as exc:
            return JSONResponse({"error": f"illegal transition: {exc}"}, status_code=400)
        except KeyError:
            return JSONResponse({"error": f"object {oid!r} not found"}, status_code=404)
        _audit().log("web:objects", "promotion",
                     {"object_id": oid, "to_state": to_state,
                      "by": body.get("by", "user:web")}, mode="lab")
        return JSONResponse(_object_view(moved))
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            blk.get("text", "") if isinstance(blk, dict) and blk.get("type") == "text"
            else (blk if isinstance(blk, str) else "")
            for blk in content
        )
    return ""


_UPLOADS = UploadCache()


def _to_lc_messages(history: list[dict]) -> list:
    return [("assistant" if m.get("role") == "assistant" else "user", str(m.get("content", "")))
            for m in history]


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> JSONResponse:
    """Accept dropped/attached files; extract their content for the agent and
    cache it by id. Per-file errors don't fail the batch."""
    out = []
    for f in files:
        try:
            rec = extract(f.filename or "file", await f.read())
            out.append(public_view(_UPLOADS.put(rec), rec))
        except ValueError as exc:
            out.append({"name": f.filename, "error": str(exc)})
        except Exception as exc:
            out.append({"name": f.filename, "error": f"extract failed: {exc}"})
    return JSONResponse({"files": out})


def _kernel_summary(ctx) -> str:
    """Render a kernel Context into a readable answer for the chat bubble."""
    f = ctx.facts
    path = " → ".join(s.capability for s in ctx.trace) or "(no steps)"
    if "answer" in f:                                   # ReAct capability — its text IS the answer
        body = f["answer"]
        if isinstance(body, list):
            body = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in body)
        return str(body)
    if "backtest_report" in f:
        r = f["backtest_report"]
        return (f"**kernel** {path}\n\n"
                f"回测 · event={r.get('event')} · n_markets={r.get('n_markets')} · "
                f"brier_delta={r.get('brier_delta')} · beats_market={r.get('beats_market')} · "
                f"ci={r.get('ci')}")
    if "decision" in f:
        return f"**kernel** {path}\n\ndecision: {f['decision']}"
    if "evaluation" in f:
        return f"**kernel** {path}\n\nevaluation: {f['evaluation']}"
    return f"**kernel** 无法完成目标 {sorted(ctx.goal.targets)}(路径: {path})。"


async def _stream_kernel(history: list[dict], session: "AgentSession") -> AsyncIterator[str]:
    """Kernel mode: the request goes through the ONE kernel loop, which recognises
    intent and takes the minimal capability path (Q&A via ReAct, or data→backtest,
    …). The prior turns are passed as cross-turn memory. Runs the sync loop in a
    thread and bridges its ``on_event`` to SSE."""
    from polyagents.kernel import run_mode

    # split the conversation into the current request + the prior turns (memory)
    last_idx = next((i for i in range(len(history) - 1, -1, -1)
                     if history[i].get("role") == "user"), None)
    last_text = str(history[last_idx].get("content", "")) if last_idx is not None else ""
    prior = [(m.get("role"), str(m.get("content", "")))
             for m in history[:last_idx]] if last_idx is not None else []

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def on_event(ev: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, ev)

    def work() -> None:
        try:
            ctx = run_mode("kernel", request=last_text, history=prior, on_event=on_event)
            loop.call_soon_threadsafe(q.put_nowait, {"type": "_result", "ctx": ctx})
        except Exception as exc:                        # surface, don't crash the stream
            loop.call_soon_threadsafe(q.put_nowait, {"type": "_error", "message": str(exc)})

    fut = loop.run_in_executor(None, work)
    session.log("route.decided", route="kernel", by="manual")
    yield _sse({"type": "route", "route": "kernel", "by": "manual"})
    streamed = False                                    # did any real token flow out?
    while True:
        ev = await q.get()
        t = ev.get("type")
        if t == "token":                                # inner capability tokens — real streaming
            streamed = True
            yield _sse({"type": "token", "text": ev.get("text", "")})
        elif t in ("tool", "tool_result"):              # inner tool-calls (e.g. web_search)
            yield _sse(ev)
        elif t == "capability.start":
            session.log("kernel.capability", name=ev.get("name"))
            yield _sse({"type": "tool", "name": ev.get("name")})
        elif t == "capability.done":
            yield _sse({"type": "tool_result", "name": ev.get("name")})
        elif t == "capability.error":
            yield _sse({"type": "error", "message": f"{ev.get('name')}: {ev.get('error')}"})
        elif t == "_result":
            if not streamed:                            # nothing streamed → emit the summary/answer
                for chunk in _chunk_text(_kernel_summary(ev["ctx"])):
                    yield _sse({"type": "token", "text": chunk})
            break
        elif t == "_error":
            yield _sse({"type": "error", "message": ev["message"]})
            break
        # loop.start / loop.end are informational — path shows in the summary
    await fut
    session.log("session.end")
    yield _sse({"type": "done"})


def _chunk_text(text: str, size: int = 24):
    for i in range(0, len(text), size):
        yield text[i:i + size]


async def _stream(history: list[dict], skills: list[str], model: str | None = None,
                  attachments: list[str] | None = None,
                  mode: str = "auto") -> AsyncIterator[str]:
    # The web chat IS the Ask mode: one session decides tools (read-only),
    # permissions and audit. mode → readonly tool subset + audit trail (§八-B/§九).
    session = AgentSession("ask", audit=_audit())
    # route the question to a Domain (tools) or General (web_search) handler
    last_text = next((str(m.get("content", "")) for m in reversed(history)
                      if m.get("role") == "user"), "")
    # Kernel mode: one goal-directed loop auto-recognises intent + picks the path,
    # with the prior turns as cross-turn memory.
    if mode == "kernel":
        session.log("session.start", model=model, skills=skills,
                    attachments=len(attachments or []))
        async for ev in _stream_kernel(history, session):
            yield ev
        return
    route, by = classify(last_text, manual=(mode if mode in ("domain", "general") else None),
                         llm=_classifier_llm())
    session.log("session.start", model=model, skills=skills,
                attachments=len(attachments or []))
    session.log("route.decided", route=route, by=by)
    yield _sse({"type": "route", "route": route, "by": by})
    # General mode may delegate to an external coding agent (Alpha DevBox / pi);
    # it streams its own SSE which we relay. Falls back to Claude when unconfigured.
    if route == "general" and chosen_general_backend() == "devbox":
        session.log("general.backend", backend="devbox")
        try:
            async for ev in stream_devbox_general(last_text):
                yield _sse(ev)
            session.log("session.end")
            yield _sse({"type": "done"})
        except Exception as exc:
            session.log("session.error", message=str(exc))
            yield _sse({"type": "error", "message": str(exc)})
        return
    try:
        if route == "general":
            agent = build_general_agent(model=model)
        else:
            agent = build_agent(skills or None, model=model, readonly=session.readonly)
    except Exception as exc:
        session.log("session.error", message=str(exc))
        yield _sse({"type": "error", "message": f"agent init failed: {exc}"})
        return
    messages = _to_lc_messages(history)
    records = [r for r in (_UPLOADS.get(a) for a in (attachments or [])) if r]
    if records and messages and messages[-1][0] == "user":
        last_text = messages[-1][1] if isinstance(messages[-1][1], str) else ""
        messages[-1] = ("user", build_message_content(last_text, records))
    try:
        async for ev in agent.astream_events({"messages": messages}, version="v2"):
            kind = ev.get("event")
            if kind == "on_chat_model_stream":
                text = _text_of(ev["data"]["chunk"].content)
                if text:
                    yield _sse({"type": "token", "text": text})
            elif kind == "on_tool_start":
                session.log("tool.call", name=ev.get("name"))
                yield _sse({"type": "tool", "name": ev.get("name"), "args": ev["data"].get("input")})
            elif kind == "on_tool_end":
                yield _sse({"type": "tool_result", "name": ev.get("name")})
        session.log("session.end")
        yield _sse({"type": "done"})
    except Exception as exc:
        session.log("session.error", message=str(exc))
        yield _sse({"type": "error", "message": str(exc)})


@app.post("/api/chat")
async def chat(request: Request) -> StreamingResponse:
    body = await request.json()
    history = body.get("messages", [])
    skills = body.get("skills", [])
    model = body.get("model")
    attachments = body.get("attachments", [])
    mode = body.get("mode", "auto")
    return StreamingResponse(_stream(history, skills, model, attachments, mode),
                             media_type="text/event-stream")
