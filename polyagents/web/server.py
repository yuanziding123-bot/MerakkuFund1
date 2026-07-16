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
from polyagents.ingestion.polymarket_ingest import run_polymarket_ingestion
from polyagents.lab.backtest import BacktestRunner, get_backtest_run, get_report
from polyagents.lab.monitor import LabMonitor, MonitorRequest
from polyagents.lab.schemas import BacktestRequest, CreateHypothesisRequest, utc_now
from polyagents.lab.service import create_hypothesis, default_repository, get_hypothesis, list_hypotheses
from polyagents.storage.audit_store import AuditStore
from polyagents.storage.db import DataStore
from polyagents.storage.engine import database_url

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
            from polyagents.llm import build_chat_llm
            _CLASSIFIER_LLM = build_chat_llm(model="claude-haiku-4-5-20251001", temperature=0.0)
        except Exception:
            _CLASSIFIER_LLM = False        # unavailable → classify falls back to 'domain'
    return _CLASSIFIER_LLM or None

_REPO = str(Path(__file__).resolve().parents[2])

_STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="polyagents chat")
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

_LAB_INGEST_JOBS: dict[str, dict] = {}


def _audit_lab(event_type: str, payload: dict | None = None) -> None:
    try:
        store = AuditStore()
        try:
            store.log("lab-api", event_type, payload or {}, mode="lab")
        finally:
            store.close()
    except Exception:
        pass


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


@app.get("/api/capabilities")
async def capabilities() -> JSONResponse:
    """The kernel loop's capabilities, tagged core (always on) vs which vertical pack."""
    try:
        from polyagents.kernel.modes import registry_for
        from polyagents.kernel.packs import CORE, PACKS
        of_pack = {cap: pid for pid, p in PACKS.items() for cap in p["capabilities"]}
        caps = [{"name": c.name, "description": c.description,
                 "needs": sorted(c.preconditions), "gives": sorted(c.effects),
                 "tier": "core" if c.name in CORE else "pack",
                 "pack": of_pack.get(c.name)}
                for c in registry_for("kernel")]   # None packs = all, so every capability shows
        return JSONResponse({"capabilities": caps})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.get("/api/packs")
async def packs() -> JSONResponse:
    """Selectable vertical capability packs (loaded on demand by the kernel)."""
    from polyagents.kernel.packs import PACKS
    return JSONResponse({"packs": [{"id": pid, "name": p["name"],
                                    "description": p["description"],
                                    "capabilities": p["capabilities"]}
                                   for pid, p in PACKS.items()]})


@app.get("/api/library")
async def library() -> JSONResponse:
    """Unified skill manifest — one format for everything the agent can have:
    core capabilities (loading=auto), vertical packs (loading=select), and SKILL.md
    workflows (loading=select). ``kind`` distinguishes the mechanism."""
    try:
        from polyagents.kernel.modes import registry_for
        from polyagents.kernel.packs import CORE, PACKS
        caps = {c.name: c for c in registry_for("kernel")}
        items = []
        for n in CORE:                                   # always-on capabilities
            c = caps.get(n)
            if c:
                items.append({"id": n, "name": n, "description": c.description,
                              "kind": "capability", "loading": "auto",
                              "needs": sorted(c.preconditions), "gives": sorted(c.effects)})
        for pid, p in PACKS.items():                      # selectable vertical packs
            items.append({"id": pid, "name": p["name"], "description": p["description"],
                          "kind": "pack", "loading": "select", "capabilities": p["capabilities"]})
        for s in list_skills():                           # selectable SKILL.md workflows
            items.append({"id": s["id"], "name": s["name"], "description": s["description"],
                          "kind": "workflow", "loading": "select", "category": s.get("category")})
        return JSONResponse({"skills": items})
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


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


@app.get("/api/lab/data/status")
async def lab_data_status() -> JSONResponse:
    store = None
    try:
        store = DataStore(DEFAULT_CONFIG["db_path"])
        counts = store.counts()
        rows = store.fetch_collections(limit=500)
        usable = 0
        fixture_like = 0
        for row in rows:
            raw = row.get("raw") or {}
            lab = raw.get("lab") or {}
            if lab.get("outcome", raw.get("outcome")) is not None:
                usable += 1
            source = lab.get("ingestion_source")
            if source is None:
                fixture_like += 1
        return JSONResponse({
            "db_path": DEFAULT_CONFIG["db_path"],
            "counts": counts,
            "collections": {
                "total": counts.get("collections", 0),
                "usable_settled": usable,
                "unresolved_or_unusable": max(0, counts.get("collections", 0) - usable),
                "unknown_source": fixture_like,
            },
        })
    except Exception as exc:
        return JSONResponse({"error": {"code": "data_status_failed", "message": str(exc)}}, status_code=400)
    finally:
        if store is not None:
            store.close()


@app.post("/api/lab/data/ingest")
async def lab_data_ingest(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        limit = int(payload.get("limit", 100))
        stats = await asyncio.to_thread(
            run_polymarket_ingestion,
            limit=limit,
            db_path=DEFAULT_CONFIG["db_path"],
        )
        return JSONResponse({"dry_run": False, "stats": asdict(stats)})
    except Exception as exc:
        return JSONResponse({"error": {"code": "ingestion_failed", "message": str(exc)}}, status_code=400)


def _run_lab_ingestion_job(job_id: str, *, limit: int) -> None:
    job = _LAB_INGEST_JOBS[job_id]
    job.update({"status": "running", "started_at": utc_now()})
    try:
        stats = run_polymarket_ingestion(limit=limit, db_path=DEFAULT_CONFIG["db_path"])
        job.update({"status": "completed", "finished_at": utc_now(), "stats": asdict(stats)})
        _audit_lab("lab.ingestion_job.completed", {"job_id": job_id, "stats": asdict(stats)})
    except Exception as exc:
        job.update({"status": "failed", "finished_at": utc_now(), "error": str(exc)})
        _audit_lab("lab.ingestion_job.failed", {"job_id": job_id, "error": str(exc)})


@app.post("/api/lab/data/ingest-jobs")
async def lab_data_ingest_job(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        limit = int(payload.get("limit", 100))
        job_id = f"ing_{uuid.uuid4().hex[:10]}"
        _LAB_INGEST_JOBS[job_id] = {
            "id": job_id,
            "type": "lab_ingestion_job",
            "status": "queued",
            "limit": limit,
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "stats": None,
            "error": None,
        }
        _audit_lab("lab.ingestion_job.create", {"job_id": job_id, "limit": limit})
        asyncio.create_task(asyncio.to_thread(_run_lab_ingestion_job, job_id, limit=limit))
        return JSONResponse(_LAB_INGEST_JOBS[job_id])
    except Exception as exc:
        return JSONResponse({"error": {"code": "ingestion_job_failed", "message": str(exc)}}, status_code=400)


@app.get("/api/lab/data/ingest-jobs/{job_id}")
async def lab_data_ingest_job_status(job_id: str) -> JSONResponse:
    job = _LAB_INGEST_JOBS.get(job_id)
    if job is None:
        return JSONResponse({"error": {"code": "not_found", "message": f"ingestion job not found: {job_id}"}}, status_code=404)
    return JSONResponse(job)


def _run_lab_backtest_sync(id: str, payload: dict) -> dict:
    store = DataStore(DEFAULT_CONFIG["db_path"])
    try:
        body = {**payload, "hypothesis_id": id}
        result = BacktestRunner(store=store).run(BacktestRequest(**body))
        _audit_lab(
            "lab.backtest.completed",
            {"hypothesis_id": id, "run_id": result.id, "report_id": result.report_id, "strategy_id": payload.get("strategy_id")},
        )
        return {
            "backtest_run_id": result.id,
            "status": result.status,
            "report_id": result.report_id,
        }
    finally:
        store.close()


@app.post("/api/lab/hypotheses/{id}/backtests")
async def lab_run_backtest(id: str, request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        return JSONResponse(await asyncio.to_thread(_run_lab_backtest_sync, id, payload))
    except Exception as exc:
        return JSONResponse({"error": {"code": "evaluation_failed", "message": str(exc)}}, status_code=400)


@app.post("/api/lab/monitor/opportunities")
async def lab_monitor_opportunities(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        result = LabMonitor().scan(MonitorRequest(**payload))
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": {"code": "monitor_failed", "message": str(exc)}}, status_code=400)


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
    db_url = database_url()
    db_backend = "postgres" if db_url.startswith(("postgresql://", "postgresql+")) else "sqlite"
    live_enabled = str(DEFAULT_CONFIG.get("execution_mode") or "").lower() == "live"
    tavily_configured = bool(DEFAULT_CONFIG.get("tavily_api_key"))
    issues = []
    if db_backend != "postgres":
        issues.append("production DB is not Postgres; local SQLite is suitable for dev/demo only")
    if live_enabled:
        issues.append("Live execution mode is enabled; keep disabled until Lab gates and operator approvals are enforced")
    if not tavily_configured:
        issues.append("TAVILY_API_KEY is missing; historical news evidence will be unavailable")
    return JSONResponse({
        "tool_manifest_hash": "tm_lab_v1",
        "permission_policy": "lab-v1",
        "data_sources": [
            {"id": "polymarket", "status": "configured", "last_checked_at": None},
            {"id": "tavily", "status": "configured" if tavily_configured else "missing_key", "last_checked_at": None},
        ],
        "database": {
            "backend": db_backend,
            "production_ready": db_backend == "postgres",
            "url_present": bool(db_url),
            "data_store_path": DEFAULT_CONFIG["db_path"],
        },
        "live_tools_enabled": live_enabled,
        "audit_enabled": True,
        "readiness": {
            "production_ready": not issues,
            "issues": issues,
        },
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
        _OBJECT_STORE = ObjectStore()        # shared engine (POLYAGENTS_DATABASE_URL → Postgres in prod)
    return _OBJECT_STORE


def _audit():
    global _AUDIT_STORE
    if _AUDIT_STORE is None:
        from polyagents.storage.audit_store import AuditStore
        _AUDIT_STORE = AuditStore()
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


def _format_market_analysis(a: dict, path: str) -> str:
    """Render the Goal-1 framework result (explore→reason→analyze→backtest→conclude)."""
    if a.get("error"):
        return f"**kernel** {path}\n\n分析失败:{a['error']}"
    m = a.get("market", {})
    r = a.get("reasoning", {})
    bt = a.get("backtest", {})
    c = a.get("conclusion", {})
    ms = a.get("microstructure", {})
    sim = a.get("similar_markets", []) or []
    lines = [f"**市场分析框架** · {path}", ""]
    lines.append(f"**标的**:{m.get('question')}  \n价格 {m.get('price')} · 类别 {m.get('category')} · "
                 f"{m.get('days_to_expiry')} 天到期 · 流动性 {m.get('liquidity')}")
    if "p_true" in r:
        lines.append(f"\n**① 推理(p_true)**:{r['p_true']:.2f} ({r.get('direction')}, "
                     f"{r.get('conviction')})  \n{r.get('rationale', '')}")
    else:
        lines.append(f"\n**① 推理**:{r.get('note', 'n/a')}")
    if ms:
        top = ", ".join(f"{k}={round(v, 3) if isinstance(v, (int, float)) else v}"
                        for k, v in list(ms.items())[:6])
        lines.append(f"\n**② 数据分析(微结构/flow 因子)**:{top}")
    n_bt = bt.get("n_markets") or 0
    if n_bt:
        small = "(样本不足 n<10,仅供参考)" if n_bt < 10 else ""
        lines.append(f"\n**③ 回溯对比**:同类已结算 {n_bt} 个市场上,该信号 "
                     f"brier_delta={bt.get('brier_delta')} · beats_market={bt.get('beats_market')} · "
                     f"ci={bt.get('ci')} {small}")
    else:
        lines.append(f"\n**③ 回溯对比**:{bt.get('note', '无可回测的同类历史')}")
    if sim:
        prec = "; ".join(f"{s.get('question', '')[:48]}→{s.get('resolved_winner')}" for s in sim[:3])
        lines.append(f"\n**④ 相似历史市场(已结算先例)**:{prec}")
    else:
        lines.append("\n**④ 相似历史市场**:无已结算先例(相似市场尚未结算,不作对比)")
    if "action" in c:
        lines.append(f"\n**⑤ 结论**:**{c['action'].upper()}** · edge={c.get('edge')} "
                     f"(p_cal={c.get('p_calibrated')}) · APY={c.get('annualized_edge')} · "
                     f"size=${c.get('size_usdc')}")
        if c.get("reasons"):
            lines.append("　理由:" + "；".join(str(x) for x in c["reasons"][:4]))
        if c.get("risk_flags"):
            lines.append("　风险:" + "；".join(str(x) for x in c["risk_flags"][:4]))
    else:
        lines.append(f"\n**⑤ 结论**:{c.get('note', 'n/a')}")
    return "\n".join(lines)


def _answer_text(f) -> str:
    body = f.get("answer", "")
    if isinstance(body, list):
        body = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in body)
    return str(body)


def _format_settlement(a: dict, path: str) -> str:
    """Render settle + reflect: resolved paper trades booked, with lessons."""
    recs = a.get("settled") or []
    p = a.get("portfolio", {}) or {}
    lines = [f"**结算 & 反思 · settle_and_reflect** · {path}", ""]
    if not recs:
        lines.append("当前没有可结算的交易(无已解决的纸面持仓)。先用 paper_trade 下单,等市场结算后再来。")
        return "\n".join(lines)
    lines.append(f"结算了 **{a.get('n_settled')}** 笔:")
    lines.append("\n| 市场 | 结果 | 已实现 P&L | 收益率 |")
    lines.append("|---|---|---|---|")
    for s in recs:
        won = "✅赢" if s.get("won") else "❌输"
        ret = s.get("realized_return")
        lines.append(f"| {(s.get('question') or '')[:34]} | {won} | {s.get('realized_pnl')} | "
                     f"{(f'{ret:+.1%}' if isinstance(ret, (int, float)) else '—')} |")
    lessons = [s.get("lesson") for s in recs if s.get("lesson")]
    if lessons:
        lines.append("\n**反思(Layer 4 lessons)**:")
        for ls in lessons[:5]:
            lines.append(f"- {ls}")
    lines.append(f"\n**组合(纸面)**:现金 ${p.get('cash')} · 已实现 P&L ${p.get('realized_pnl')} · "
                 f"持仓 {len(p.get('open_positions') or [])} 个")
    lines.append("\n_结算写入决策日志与 lesson,下次同类市场的信号会带上这些教训;整体表现 → evaluate_skill。_")
    return "\n".join(lines)


def _format_paper_trade(a: dict, path: str) -> str:
    """Render the paper-trade outcome (sized decision + circuit-breaker result)."""
    if a.get("error"):
        return f"**纸面交易 · paper_trade** · {path}\n\n失败:{a['error']}"
    m = a.get("market", {})
    act = (a.get("action") or "hold").upper()
    p = a.get("portfolio", {}) or {}
    lines = [f"**纸面交易 · paper_trade** · {path}", "",
             f"**标的**:{m.get('question')}  \n价 {m.get('price')} · p_true={a.get('p_true')} · "
             f"edge={a.get('edge')} · 建议 size ${a.get('size_usdc')}"]
    if a.get("executed"):
        r = a.get("result") or {}
        lines.append(f"\n→ **{act} 已成交(纸面)** · status={r.get('status')} · 已实现 P&L {r.get('realized_pnl')}")
    elif a.get("action") in ("buy", "sell"):
        r = a.get("result") or {}
        lines.append(f"\n→ **{act} 未成交** · status={r.get('status')} · {r.get('reason') or '被风控拦截'}")
    else:
        lines.append(f"\n→ **HOLD**,未达门槛,不下单(edge 不足或风控)。这是常态,不是失败。")
    if a.get("reasons"):
        lines.append("　依据:" + "；".join(str(x) for x in a["reasons"][:3]))
    lines.append(f"\n**组合(纸面)**:现金 ${p.get('cash')} · 敞口 ${p.get('exposure')} · "
                 f"已实现 P&L ${p.get('realized_pnl')} · 持仓 {len(p.get('open_positions') or [])} 个")
    lines.append("\n_纸面交易(paper money),经风控/熔断。想看整体表现 → portfolio_review;结算后 → evaluate_skill。_")
    return "\n".join(lines)


def _format_skill_report(a: dict, path: str) -> str:
    """Render the calibration / skill report (does p_cal beat market)."""
    return f"**技能评估 · evaluate_skill** · {path}\n\n```\n{a.get('report', '(无数据)')}\n```"


def _format_portfolio(a: dict, path: str) -> str:
    """Render the paper portfolio + P&L."""
    p = a.get("portfolio", {}) or {}
    pos = p.get("open_positions") or []
    lines = [f"**纸面组合 & P&L · portfolio_review** · {path}", "",
             f"现金 ${p.get('cash')} · 敞口 ${p.get('exposure')} · 已实现 P&L ${p.get('realized_pnl')} · "
             f"持仓 {len(pos)} 个"]
    if pos:
        lines.append("\n| 市场 | 份额 | 均价 |")
        lines.append("|---|---|---|")
        for x in pos:
            lines.append(f"| {(x.get('market') or '')[:40]} | {x.get('shares')} | {x.get('avg_price')} |")
    else:
        lines.append("\n_当前无持仓(paper)。_")
    lines.append(f"\n**P&L / 归因**\n```\n{a.get('pnl', '(无交易记录)')}\n```")
    return "\n".join(lines)


def _format_news(a: dict, path: str) -> str:
    """Render the news + sentiment signal for a market/topic."""
    lines = [f"**新闻 / 事件情绪 · news_sentiment** · {path}", "", f"_主题:{a.get('query')}_"]
    if not a.get("enabled"):
        lines.append("\n" + (a.get("note") or "新闻检索未启用。"))
        return "\n".join(lines)
    items = a.get("items") or []
    lines.append(f"\n**综合情绪**:{a.get('signal')}(均分 {a.get('mean_sentiment')},共 {a.get('n_items')} 条)")
    if items:
        lines.append("\n| 情绪 | 标题 |")
        lines.append("|---|---|")
        for it in items:
            lines.append(f"| {it.get('sentiment'):+} | [{(it.get('title') or '')[:60]}]({it.get('url')}) |")
    lines.append("\n_情绪分 ∈ [−1,1],词典打分;>0.1 偏多、<−0.1 偏空。事件驱动信号,非确定性。_")
    return "\n".join(lines)


def _format_microstructure(a: dict, path: str) -> str:
    """Render the microstructure / flow scan across markets."""
    mk = a.get("markets") or []
    lines = [f"**微结构 / 资金流扫描 · microstructure_scan** · {path}", "",
             f"_领域:{a.get('category')} · 扫了 {a.get('n_scanned')} 个市场,取 top_"]
    if not mk:
        lines.append("\n未扫到可用市场。")
        return "\n".join(lines)
    lines.append("\n| 市场 | flow | book | micro-mid | 量能x | 动量 | 点差bps | 倾向 | 分 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s in mk:
        lines.append(f"| {(s.get('question') or '')[:22]} | {s.get('flow_imbalance'):+} | "
                     f"{s.get('book_pressure'):+} | {s.get('micro_vs_mid'):+} | {s.get('volume_spike')} | "
                     f"{s.get('price_momentum'):+} | {s.get('spread_bps')} | {s.get('lean')} | {s.get('score')} |")
    lines.append("\n_分越高=资金流/盘口越单边且价格越没跟上(潜在 edge)。点差>300bps 视为难交易(降权)。想深挖某个 → analyze_market。_")
    return "\n".join(lines)


def _format_alpha_hunt(a: dict, path: str) -> str:
    """Render the top-level opportunity hunt: crypto mispricings + microstructure flow."""
    crypto = a.get("crypto") or []
    flow = a.get("flow") or []
    lines = [f"**机会总扫描 · hunt_alpha** · {path}", "", f"_主题:{a.get('query')}_"]
    lines.append(f"\n**① Crypto 现货 vs 隐含错价**({a.get('n_crypto', 0)} 个,终端型)")
    if crypto:
        lines.append("\n| 市场 | 现货 | 行权 | 模型p | 市场价 | gap |")
        lines.append("|---|---|---|---|---|---|")
        for o in crypto:
            lines.append(f"| {(o.get('question') or '')[:28]} | {o.get('spot')} | {o.get('strike')} | "
                         f"{o.get('p_model')} | {o.get('market_price')} | {o.get('gap'):+} |")
    else:
        lines.append("\n　(暂无可评分的终端型 crypto 错价)")
    lines.append(f"\n**② 微结构 / 资金流信号**(扫了 {a.get('n_flow_scanned', 0)} 个活跃市场,取 top)")
    if flow:
        lines.append("\n| 市场 | flow | book | 量能x | 动量 | 点差bps | 倾向 | 分 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for s in flow:
            lines.append(f"| {(s.get('question') or '')[:24]} | {s.get('flow_imbalance'):+} | "
                         f"{s.get('book_pressure'):+} | {s.get('volume_spike')} | {s.get('price_momentum'):+} | "
                         f"{s.get('spread_bps')} | {s.get('lean')} | {s.get('score')} |")
    else:
        lines.append("\n　(暂无明显资金流信号)")
    lines.append("\n_① 终端型 crypto:模型概率 vs 市场价,gap 越负=市场越高估上行。_")
    lines.append("_② 资金流:强单边 flow/book 且价格未跟上=潜在 edge;分越高越值得深挖。点差>300bps 视为难交易(降权)。_")
    lines.append("_均为**信号非确定性**。想深挖某个 → 对它跑 analyze_market;想验证策略 → backtest_strategies / promotion_gate。_")
    return "\n".join(lines)


_CHART_COLORS = ["#7dab7d", "#c9ae62", "#c98276", "#9fb9d6", "#b48ead", "#83b6b6"]


def _svg_esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_svg_chart(spec: dict) -> str:
    """Self-contained inline SVG for a chart spec (line / area / multi / bar).
    Styled for the warm-dark chat bubble; responsive via width:100%."""
    W, H = 680, 300
    L, R, T, B = 48, 16, 24, 52
    pw, ph = W - L - R, H - T - B
    ctype = spec.get("type", "line")
    grid, txt = "rgba(255,255,255,.09)", "#aea69c"
    out = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
           f'role="img" style="width:100%;height:auto;font-family:Inter,system-ui,sans-serif">']
    if spec.get("title"):
        out.append(f'<text x="{L}" y="15" fill="#dad2c1" font-size="12" font-weight="600">'
                   f'{_svg_esc(str(spec["title"])[:64])}</text>')

    def _empty(msg: str) -> str:
        out.append(f'<text x="{L}" y="{T + ph / 2:.0f}" fill="{txt}" font-size="12">{_svg_esc(msg)}</text>')
        out.append("</svg>")
        return "".join(out)

    if ctype == "bar":
        bars = spec.get("bars") or []
        if not bars:
            return _empty(spec.get("error") or "无数据")
        vmax = (max((b["value"] for b in bars), default=1.0) or 1.0) * 1.15
        n, gap = len(bars), 10
        bw = (pw - gap * (n - 1)) / n
        for i in range(4):
            gy = T + ph * i / 3
            out.append(f'<line x1="{L}" y1="{gy:.1f}" x2="{W - R}" y2="{gy:.1f}" stroke="{grid}"/>')
            out.append(f'<text x="{L - 6}" y="{gy + 3:.1f}" fill="{txt}" font-size="9" '
                       f'text-anchor="end">{vmax * (1 - i / 3):.2f}</text>')
        for i, b in enumerate(bars):
            x = L + i * (bw + gap)
            bh = ph * (b["value"] / vmax)
            y = T + ph - bh
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                       f'rx="2" fill="{_CHART_COLORS[i % 6]}"/>')
            out.append(f'<text x="{x + bw / 2:.1f}" y="{y - 3:.1f}" fill="#dad2c1" font-size="9" '
                       f'text-anchor="middle">{b["value"]:.3f}</text>')
            out.append(f'<text x="{x + bw / 2:.1f}" y="{T + ph + 13:.1f}" fill="{txt}" font-size="8" '
                       f'text-anchor="middle">{_svg_esc(str(b["label"])[:12])}</text>')
        out.append("</svg>")
        return "".join(out)

    # ---- time-series: line / area / multi ----
    series = [s for s in (spec.get("series") or []) if s.get("points")]
    if not series:
        return _empty(spec.get("error") or "无价格历史")
    ys = [p[1] for s in series for p in s["points"]]
    ymin, ymax = min(ys), max(ys)
    if ymax - ymin < 1e-6:
        ymax = ymin + 0.01
    padv = (ymax - ymin) * 0.08
    ymin, ymax = ymin - padv, ymax + padv

    def _x(i, n):
        return L + pw * (i / (n - 1) if n > 1 else 0)

    def _y(v):
        return T + ph * (1 - (v - ymin) / (ymax - ymin))

    for i in range(4):
        gy = T + ph * i / 3
        out.append(f'<line x1="{L}" y1="{gy:.1f}" x2="{W - R}" y2="{gy:.1f}" stroke="{grid}"/>')
        out.append(f'<text x="{L - 6}" y="{gy + 3:.1f}" fill="{txt}" font-size="9" '
                   f'text-anchor="end">{ymax - (ymax - ymin) * i / 3:.3f}</text>')
    p_first = str(series[0]["points"][0][0])[:10]
    p_last = str(series[0]["points"][-1][0])[:10]
    out.append(f'<text x="{L}" y="{T + ph + 14:.0f}" fill="{txt}" font-size="9">{p_first}</text>')
    out.append(f'<text x="{W - R}" y="{T + ph + 14:.0f}" fill="{txt}" font-size="9" text-anchor="end">{p_last}</text>')
    for si, s in enumerate(series):
        col = _CHART_COLORS[si % 6]
        pts = s["points"]
        n = len(pts)
        d = " ".join(f'{_x(i, n):.1f},{_y(p[1]):.1f}' for i, p in enumerate(pts))
        if ctype == "area" and len(series) == 1:
            out.append(f'<polygon points="{_x(0, n):.1f},{T + ph:.1f} {d} {_x(n - 1, n):.1f},{T + ph:.1f}" '
                       f'fill="{col}" fill-opacity="0.15"/>')
        out.append(f'<polyline points="{d}" fill="none" stroke="{col}" stroke-width="1.6"/>')
    if len(series) > 1:                                      # legend row for multi
        lx = L
        for si, s in enumerate(series):
            out.append(f'<rect x="{lx:.0f}" y="{H - 14}" width="9" height="9" rx="2" fill="{_CHART_COLORS[si % 6]}"/>')
            lab = str(s["label"])[:16]
            out.append(f'<text x="{lx + 12:.0f}" y="{H - 6}" fill="{txt}" font-size="9">{_svg_esc(lab)}</text>')
            lx += 20 + len(lab) * 5.6
    out.append("</svg>")
    return "".join(out)


def _format_chart(a: dict, path: str) -> str:
    """Render a chart spec as a titled inline SVG (passed through the md renderer)."""
    label = {"line": "价格走势", "area": "价格走势", "multi": "走势对比", "bar": "价格快照"}.get(a.get("type"), "图表")
    lines = [f"**{label} · plot_market** · {path}", ""]
    if a.get("error") and not (a.get("series") or a.get("bars")):
        lines.append(f"画图失败:{a['error']}")
        return "\n".join(lines)
    lines.append("```svg")                                  # svg fence → md renderer emits it raw
    lines.append(_render_svg_chart(a))
    lines.append("```")
    lines.append(f"\n_数据来自价格历史(只读)。图型:{a.get('type')}。想换类型就说'柱状图/面积图/对比走势';想深挖 → analyze_market。_")
    return "\n".join(lines)


_STRAT_LABEL = {"hold": "长期持有", "short": "短线交易", "arb": "套利", "general": "通用"}


def _format_implication(impl: dict) -> list[str]:
    """Same-entity cluster + logical-implication (A⊆B) arbitrage section."""
    if not impl or not impl.get("entity"):
        return []
    out = [f"\n**逻辑蕴含 / 同实体(套利检查)** · 实体:**{impl.get('entity')}**"]
    chain = impl.get("chain") or []
    if len(chain) >= 2:
        out.append("\n| 阶段(强→弱) | 概率 | 应满足 |")
        out.append("|---|---|---|")
        for c in chain:
            out.append(f"| L{c.get('level')} {(c.get('question') or '')[:36]} | {c.get('price')} | P(强)≤P(弱) |")
    vio = impl.get("violations") or []
    if vio:
        out.append("\n🟢 **发现逻辑套利(强于的反而更贵)**:")
        for v in vio:
            out.append(f"- **{(v.get('stronger') or '')[:30]}**({v.get('p_strong')}) > "
                       f"{(v.get('weaker') or '')[:30]}({v.get('p_weak')}) · gap **{v.get('gap'):+}** "
                       f"→ 卖强腿/买弱腿")
    elif len(chain) >= 2:
        out.append("\n　蕴含边界一致(无逻辑套利)。")
    else:
        out.append("　(当前只有一个阶段市场,无蕴含链/路径可查——有'进决赛/进半决赛'类市场时才会亮。)")
    p = impl.get("path")
    if p:
        out.append(f"\n　路径分解:P(进决赛)={p.get('reach_final')} · P(夺冠)={p.get('win')} → "
                   f"隐含 **P(夺冠|进决赛)={p.get('implied_p_win_given_final')}**")
    return out


def _format_relational(a: dict, path: str) -> str:
    """Render the event-relatedness engine: strategy mode + winner-set + lag + what-if
    + same-entity logical-implication arbitrage."""
    if a.get("error"):
        return f"**关联推理 · relational_alpha** · {path}\n\n失败:{a['error']}"
    tgt = a.get("target") or {}
    mode = _STRAT_LABEL.get(a.get("strategy_mode"), a.get("strategy_mode") or "")
    lines = [f"**关联推理引擎 · relational_alpha** · {path}", ""]
    if mode:
        lines.append(f"_策略类型:**{mode}**_")
    if tgt.get("matched_by") == "fallback":
        lines.append(f"\n⚠️ 没匹配到你说的「{tgt.get('unmatched') or a.get('query')}」,退回展示最活跃市场——"
                     f"结果**未必是你要的标的**,请换更贴近市场原名的说法。")
    if a.get("note"):
        lines.append(f"\n**标的**:{tgt.get('question')}\n\n{a['note']}")
        lines += _format_implication(a.get("implication"))
        return "\n".join(lines)
    lines.append(f"\n**标的**:{tgt.get('question')}  \n市场价 {tgt.get('price')} · 冠军集内应有份额 {tgt.get('fair_share')}")
    if a.get("fair_prob") is not None:
        src = a.get("prob_sources") or {}
        edge = a.get("edge_vs_market")
        verdict = "低估→偏买" if isinstance(edge, (int, float)) and edge > 0.005 else \
                  ("高估→偏卖" if isinstance(edge, (int, float)) and edge < -0.005 else "接近公允")
        lines.append(f"\n**合成公允概率(结构性)= {a.get('fair_prob')}** · 市场 {tgt.get('price')} · "
                     f"**edge {edge:+}** · {verdict}")
        lines.append(f"　来源:冠军集隐含 {src.get('field_implied')} + 滞后修正 {src.get('lag_adj'):+}")
    lines.append(f"\n**① 冠军集一致性**:{a.get('n_field')} 个互斥标的,Σ价格 = **{a.get('field_sum')}** · {a.get('consistency')}")
    sig = a.get("signal")
    tag = "🟢 买入" if sig == "buy" else ("🟡 观察" if sig == "watch" else "⚪ 无")
    lines.append(f"\n**② 再分配 + 滞后检测**(别的场次一动、这场跟没跟上)")
    lines.append(f"　对手近期共释放概率 {a.get('field_released')} → 目标**应涨** {a.get('implied_target_rise')};"
                 f"实际涨 {a.get('target_recent_delta')} → **lag_gap = {a.get('lag_gap')}** · 信号 **{tag}**")
    rivals = a.get("top_rivals") or []
    if rivals:
        lines.append("\n| 主要对手 | 价格 | 近期Δ |")
        lines.append("|---|---|---|")
        for r in rivals:
            lines.append(f"| {(r.get('question') or '')[:34]} | {round(r.get('price',0),4)} | {r.get('delta'):+} |")
    wi = a.get("what_if") or []
    if wi:
        lines.append("\n**③ What-if(某对手出局 → 目标应有概率)**")
        lines.append("\n| 若此对手出局 | 目标公允 | Δ |")
        lines.append("|---|---|---|")
        for w in wi:
            lines.append(f"| {(w.get('question') or '')[:34]} | {w.get('target_fair_if_out')} | {w.get('delta'):+} |")
    lines += _format_implication(a.get("implication"))      # ④ same-entity logical-implication arb
    lines.append("\n_lag_gap>0 = 场上事件已动、目标价还没跟上(潜在买点);Σ≈1 为无套利基准,偏离即结构性错价;"
                 "逻辑套利=强于的事件反而更贵。均为**关联信号**,想验证某策略 → research_alpha。_")
    return "\n".join(lines)


def _format_alpha_review(a: dict, path: str) -> str:
    """Render the strategy review: LLM verdict + improvements over the computed evidence."""
    if a.get("error"):
        return f"**策略评审 · research_alpha** · {path}\n\n失败:{a['error']}"
    lines = [f"**策略评审 · research_alpha** · {path}", ""]
    s = a.get("synth")
    if s:
        edge = s.get("edge_vs_market")
        verdict = "低估 → 偏买" if isinstance(edge, (int, float)) and edge > 0.005 else \
                  ("高估 → 偏卖" if isinstance(edge, (int, float)) and edge < -0.005 else "接近公允")
        src = s.get("sources") or {}
        lines.append(f"**合成公允概率 = {s.get('fair_prob')}** · 市场 {s.get('market_price')} · "
                     f"**edge {edge:+}** · {verdict} · 置信 {s.get('confidence')}")
        lines.append(f"　来源:冠军集隐含 {src.get('field_implied')} + 滞后修正 {src.get('lag_adj'):+} "
                     f"+ 新闻调整 {src.get('news_adj'):+}\n")
    if a.get("review"):
        lines.append(a["review"])
    bt = a.get("backtest")
    if bt and bt.get("variants"):
        best = bt.get("best") or {}
        lines.append(f"\n**历史回测(关联估计 vs 市场)+ 变体自测** · {bt.get('n_events')} 个已结算冠军集")
        lines.append("\n| 变体 | n | BrierΔ | 跑赢市场 |")
        lines.append("|---|---|---|---|")
        for v in bt["variants"]:
            bd = v.get("brier_delta")
            star = " ⭐" if v.get("name") == best.get("name") else ""
            lines.append(f"| {v.get('name')}{star} | {v.get('n')} | {bd if bd is not None else '—'} | "
                         f"{'✅' if v.get('beats_market') else '❌'} |")
        if bt.get("note"):
            lines.append(f"\n_{bt['note']}_")
    if a.get("news_signal"):
        lines.append(f"\n_新闻情绪信号:{a['news_signal']}_")
    rel = a.get("relational") or {}
    if rel and not rel.get("error"):
        lines.append("\n---\n\n**关联证据(计算所得,评审依据):**")
        lines.append(_format_relational(rel, path).split("\n", 2)[-1])   # drop the header line
    return "\n".join(lines)


def _format_news_markets(a: dict, path: str) -> str:
    """Render news → affected markets: LLM direction analysis + the matched candidates."""
    lines = [f"**新闻→标的映射 · news_to_markets** · {path}", "",
             f"_新闻:{(a.get('query') or '')[:80]}_"]
    if a.get("note") and not a.get("candidates"):
        lines.append(f"\n{a['note']}")
        if a.get("terms"):
            lines.append(f"\n_抽取到的实体/关键词:{', '.join(a['terms'])}_")
        return "\n".join(lines)
    if a.get("analysis"):
        lines.append("\n**📰 方向研判(利好/利空,值得验证)**\n")
        lines.append(a["analysis"])
    cands = a.get("candidates") or []
    if cands:
        lines.append("\n**匹配到的活跃标的**")
        lines.append("\n| 市场 | 现价 | 关联度 |")
        lines.append("|---|---|---|")
        for c in cands:
            lines.append(f"| {(c.get('question') or '')[:40]} | {c.get('price')} | {c.get('hits')} |")
    lines.append("\n_LLM 把新闻实体链接到活跃标的并研判方向,**是待验证假设**,非确定。想深挖某个 → analyze_market;"
                 "想记下你的判断 → log_prediction。_")
    return "\n".join(lines)


def _format_prediction_logged(a: dict, path: str) -> str:
    if a.get("error"):
        return f"**预测记录 · log_prediction** · {path}\n\n{a['error']}"
    L = a.get("logged") or {}
    edge = L.get("edge_vs_market")
    lean = "你更看多" if isinstance(edge, (int, float)) and edge > 0 else \
           ("你更看空" if isinstance(edge, (int, float)) and edge < 0 else "与市场一致")
    lines = [f"**预测已记录 · log_prediction** · {path}", "",
             f"**已记录到**:{L.get('question')}",
             f"你的概率 **{L.get('user_p')}** · 当时市场价 {L.get('market_p')} · edge **{edge:+}** · {lean}",
             f"\n_匹配方式 {L.get('matched_by')}。**如果不是你要的标的**,请用市场原名/英文名重记。"
             f"市场结算后来 `prediction_journal` 看你 vs 市场的打分。_"]
    return "\n".join(lines)


def _format_prediction_journal(a: dict, path: str) -> str:
    if a.get("error"):
        return f"**预测日志 · prediction_journal** · {path}\n\n{a['error']}"
    lines = [f"**预测日志 · prediction_journal** · {path}", "",
             f"_本次自动结算 {a.get('settled_now')} 笔 · 未结算 {a.get('n_open')} 笔_"]
    agg = a.get("aggregate")
    if agg:
        verdict = "🟢 你在跑赢市场" if agg.get("beats_market") else "🔴 尚未跑赢市场"
        lines.append(f"\n**你的校准(已结算 {agg.get('n_resolved')} 笔)** · {verdict}")
        lines.append(f"　Brier:你 **{agg.get('brier_user')}** vs 市场 {agg.get('brier_market')} · "
                     f"**brierΔ {agg.get('brier_delta'):+}**(>0=你更准)· 命中率 {agg.get('hit_rate')}")
        by = a.get("by_category") or []
        if by:
            lines.append("\n| 类别 | 已结算 | brierΔ(你−市场) |")
            lines.append("|---|---|---|")
            for c in by:
                lines.append(f"| {c.get('category')} | {c.get('n')} | **{c.get('brier_delta'):+}** |")
    else:
        lines.append("\n_还没有已结算的预测——等你记录的市场结算后,这里会出现「你 vs 市场」的打分与按类别的 edge。_")
    openp = a.get("open") or []
    if openp:
        lines.append("\n**未结算(你的活跃判断)**")
        lines.append("\n| 市场 | 你的P | 市场价 | edge | 记录日 |")
        lines.append("|---|---|---|---|---|")
        for o in openp:
            lines.append(f"| {(o.get('question') or '')[:32]} | {o.get('user_p')} | {o.get('market_p')} | "
                         f"**{o.get('edge'):+}** | {o.get('created_at')} |")
    lines.append("\n_前向追踪:市场结算后自动 Brier 打分(你 vs 市场),brierΔ>0 = 你在这类判断有 edge。"
                 "数据落共享/云库,越攒越准。_")
    return "\n".join(lines)


def _format_radar(a: dict, path: str) -> str:
    """Render the market radar: movers / near-resolution / fresh, each with WHY it
    matters, plus an LLM angle + arbitrage-check section."""
    lines = [f"**市场雷达 · market_radar** · {path}", "",
             f"_扫了 {a.get('n_scanned')} 个市场(深检 {a.get('n_deep')} 个)· 每条附「为什么」,末尾给角度/套利建议_"]
    if a.get("insight"):
        lines.append("\n**🎯 角度与套利建议(值得查,非保证)**\n")
        lines.append(a["insight"])
    struct = a.get("structural") or []
    if struct:
        lines.append("\n**📐 结构性一致性检查(可计算)**")
        lines.append("\n| 比赛 | 胜方Σ P(A)+P(B) | 隐含平局/其它 | 精确比分Σ(部分,n) | 超额套利 |")
        lines.append("|---|---|---|---|---|")
        for s in struct[:6]:
            wsum = s.get("winner_sum")
            arb = "🟢 卖出" if (s.get("winner_overround") or s.get("exact_overround")) else "—"
            lines.append(f"| {s.get('match')} | {wsum if wsum is not None else '—'} | "
                         f"{s.get('implied_draw_other', '—')} | {s.get('exact_sum_partial')} (n={s.get('n_scores')}) | {arb} |")
        lines.append("　_胜方Σ>1 或 精确比分Σ>1 = 真·超额,全卖即套利(需扣摩擦);Σ<1 属正常(有平局/未收全)。_")
    movers = a.get("movers") or []
    if movers:
        lines.append("\n**① 异动(近期价格变化最大)**")
        for m in movers:
            lines.append(f"\n- **{(m.get('question') or '')[:44]}** · 现价 {m.get('price')} · "
                         f"Δ**{m.get('change'):+}** · 24h量 {m.get('volume_24h'):,}")
            lines.append(f"  　_{m.get('why', '')}_")
    near = a.get("near_resolution") or []
    if near:
        lines.append("\n**② 临近结算(endgame,波动大)**")
        for m in near:
            lines.append(f"\n- **{(m.get('question') or '')[:44]}** · 现价 {m.get('price')} · "
                         f"{m.get('days')}天到期")
            lines.append(f"  　_{m.get('why', '')}_")
    fresh = a.get("fresh") or []
    if fresh:
        lines.append("\n**③ 短历史(可能新上市 / 低活跃)**")
        for m in fresh:
            lines.append(f"\n- **{(m.get('question') or '')[:44]}** · 现价 {m.get('price')} · "
                         f"{m.get('n_candles')} 个历史点")
            lines.append(f"  　_{m.get('why', '')}_")
    if not (movers or near or fresh):
        lines.append("\n当前没扫到明显线索(市场平静 / 数据不足)。")
    lines.append("\n_挑一个深挖 → analyze_market;想验证想法/找套利 → research_alpha / scan_conditional_arb。_")
    return "\n".join(lines)


def _format_conditional_arb(a: dict, path: str) -> str:
    """Render the cross-market conditional/implication arbitrage scan."""
    chains = a.get("chains") or []
    lines = [f"**跨市场条件套利扫描 · scan_conditional_arb** · {path}", "",
             f"_扫了 {a.get('n_entities')} 个实体,{a.get('n_chains')} 条条件链,"
             f"**真·逻辑蕴含套利 {a.get('n_true_arb')} 个**_"]
    if not chains:
        lines.append("\n未找到可组成条件链的关联标的(需同一实体既有'夺冠'又有'进决赛/晋级/单场'市场;"
                     "当前多数标的只挂了夺冠盘)。")
        return "\n".join(lines)
    arbs = [c for c in chains if c.get("has_arb")]
    if arbs:
        lines.append("\n🟢 **真·无风险套利(强命题反而更贵,买弱腿/卖强腿):**")
        for c in arbs:
            for v in c["violations"]:
                lines.append(f"- **{(v.get('stronger') or '')[:34]}**({v.get('p_strong')}) > "
                             f"{(v.get('weaker') or '')[:34]}({v.get('p_weak')}) · gap **{v.get('gap'):+}**")
    lines.append("\n**条件概率分解**(P(夺冠|晋级) = P(夺冠) / P(晋级)):")
    lines.append("\n| 实体 | P(夺冠) | P(晋级/进决赛) | **P(夺冠\\|晋级)** | 蕴含一致 |")
    lines.append("|---|---|---|---|---|")
    for c in chains:
        ok = "✅" if not c.get("has_arb") else "❌ 套利"
        lines.append(f"| {c.get('entity')} | {c.get('p_champ')} | {c.get('p_advance')} | "
                     f"**{c.get('cond_champ_given_advance')}** | {ok} |")
    conds = [c.get("cond_champ_given_advance") for c in chains
             if isinstance(c.get("cond_champ_given_advance"), (int, float))]
    if len(conds) >= 3:
        mid = sorted(conds)[len(conds) // 2]
        lines.append(f"\n　同档 P(夺冠|晋级) 中位数≈{mid:.3f};显著高于中位=该队'晋级后夺冠'被高估(方向性做空候选),"
                     f"反之偏低=被低估。**这是方向性价值,非无风险。**")
    lines.append("\n_真·套利只在**强命题定价高于弱命题**时出现(有界、近无风险,仍需扣手续费/滑点/流动性)。"
                 "P(单场)×P(夺冠) 那种'链式成本'不是有效套利——条件概率是推导值,市场没单独挂牌,不可执行。_")
    return "\n".join(lines)


def _format_backfill(a: dict, path: str) -> str:
    """Render the outcome-backfill: how many stored snapshots got labelled."""
    if a.get("error"):
        return f"**结果回填 · backfill_outcomes** · {path}\n\n失败:{a['error']}"
    c = a.get("store_counts") or {}
    lines = [f"**结果回填 · backfill_outcomes** · {path}", "",
             f"_库:**{a.get('backend')}**{'(云端)' if a.get('backend')=='postgres' else '(本地)'} · 主题:{a.get('query')}_"]
    lines.append(f"\n扫了 **{a.get('scanned')}** 条已存快照:")
    lines.append("\n| 结果 | 数量 |")
    lines.append("|---|---|")
    lines.append(f"| ✅ 本次新标注(已结算) | **{a.get('newly_labeled')}** |")
    lines.append(f"| 之前已标注 | {a.get('already_labeled')} |")
    lines.append(f"| ⏳ 尚未结算(留待下次) | {a.get('still_unresolved')} |")
    lines.append(f"| **带标签总计(可回测)** | **{a.get('labeled_total')}** |")
    lines.append(f"\n_库存:candles {c.get('candles','?')} · trades {c.get('trades','?')} · "
                 f"collections {c.get('collections','?')}。已结算的快照现在能喂给 **lab_backtest**;"
                 f"未结算的等它们收敛后再跑一次 backfill 即可增量标注。_")
    return "\n".join(lines)


def _format_lab_backtest(a: dict, path: str) -> str:
    """Render the Lab feature-strategy backtest (EvaluationReport metrics + gates)."""
    if a.get("error"):
        return f"**Lab 回测 · lab_backtest** · {path}\n\n失败:{a['error']}"
    g = a.get("gates") or {}
    lines = [f"**Lab 回测 · lab_backtest** · {path}", "",
             f"_策略:**{a.get('strategy_id')}** · 领域:{a.get('category')} · 样本 n={a.get('n')}_"]
    if a.get("uses_fixture"):
        lines.append("\n⚠️ **跑的是 fixture(占位数据)** —— 该领域还没有带标签的真实快照。"
                     "先跑 **backfill_outcomes**(并让 collect 攒够已结算样本)再回来。")
        return "\n".join(lines)
    bd = a.get("brier_delta")
    verdict = "跑赢市场 ✅" if isinstance(bd, (int, float)) and bd > 0 else "未跑赢市场"
    lines.append(f"\n**评估(vs 市场基线)** · {verdict}")
    lines.append("\n| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| Brier Δ(越正越好) | {_fmt(bd)} |")
    lines.append(f"| Brier(模型 / 市场) | {_fmt(a.get('brier_model'))} / {_fmt(a.get('brier_market'))} |")
    lines.append(f"| ECE(校准误差,越低越好) | {_fmt(a.get('ece'))} |")
    lines.append("\n**晋级门(paper-ready 判定)**")
    lines.append("\n| 门 | 通过 |")
    lines.append("|---|---|")
    for k in ("sample_adequate", "beats_market", "ece_pass", "pit_clean", "paper_ready"):
        if k in g:
            lines.append(f"| {k} | {'✅' if g[k] else '❌'} |")
    lines.append(f"\n_Lab 证据回测:{a.get('strategy_id')} 在带标签快照上 vs 市场基线,含 bootstrap CI + 校准 + 晋级门。"
                 f"报告 id `{a.get('report_id')}` 已落 Lab 账本。想扩样本 → backfill_outcomes / 多攒 collect。_")
    return "\n".join(lines)


def _fmt(x) -> str:
    return f"{x:+.4f}" if isinstance(x, (int, float)) else "—"


def _format_opportunities(a: dict, path: str) -> str:
    """Render the Lab opportunity monitor: strategy-scored, ranked dry-run trades."""
    opps = a.get("opportunities") or []
    lines = [f"**机会监控 · scan_opportunities** · {path}", "",
             f"_策略:{a.get('strategy_id', '—')} · 主题:{a.get('query')} · dry-run(不下单)_"]
    if a.get("error"):
        lines.append(f"\n扫描失败:{a['error']}")
        return "\n".join(lines)
    if not opps:
        lines.append(f"\n{a.get('message') or '当前无可执行机会'} —— 没有市场越过 edge/风控门槛。这是常态,不是失败。")
        return "\n".join(lines)
    lines.append(f"\n扫到 **{a.get('n', len(opps))}** 个可执行机会(按 buy 优先 + edge 排序):")
    lines.append("\n| 市场 | 动作 | edge | 建议仓位 | 模型p | 市场价 | APY |")
    lines.append("|---|---|---|---|---|---|---|")
    for o in opps:
        lines.append(f"| {(o.get('question') or '')[:30]} | **{o.get('action')}** | "
                     f"{o.get('edge'):+.4f} | ${o.get('size_usdc'):.0f} | {o.get('p_cal'):.3f} | "
                     f"{o.get('market_price'):.3f} | {o.get('apy'):+.2f} |")
    top = opps[0]
    if top.get("reasons"):
        lines.append(f"\n**Top({(top.get('question') or '')[:40]})依据**:" + "；".join(str(x) for x in top["reasons"][:3]))
    lines.append("\n_LabMonitor 打分:每个市场取 live 特征 → 策略因子模型出 p → 过 Kelly/风控给仓位。**dry-run,信号非确定性**。"
                 "想深挖某个 → analyze_market;想验证该策略历史 → backtest_strategies / promotion_gate。_")
    return "\n".join(lines)


def _format_crypto_arb(a: dict, path: str) -> str:
    """Render the crypto cross-market arbitrage scan (spot vs implied probability)."""
    opps = a.get("opportunities") or []
    barrier = a.get("barrier_markets") or []
    def _px(v):                                          # decimals for sub-$10 assets (DOGE), else whole
        return f"${v:,.4f}" if v < 10 else f"${v:,.0f}"
    lines = [f"**跨市场套利扫描 · crypto 现货 vs 隐含概率** · {path}", ""]
    if not opps and not barrier:
        lines.append("未找到可解析的 crypto 阈值市场(需要类似 'Will BTC be above $X on …' 的市场)。")
        return "\n".join(lines)
    if opps:
        b = a["best"]
        tag = "市场**低估**(现货已支持,可考虑买 YES)" if b["gap"] > 0 else "市场**高估**(偏贵,可看 NO)"
        lines.append(f"**最大错价(终端型 · 模型适用)**:{b['question']}  \n"
                     f"→ {b['asset']} 现货 {_px(b['spot'])} vs 行权 {_px(b["strike"])} ({b['direction']}) · "
                     f"{b['days']} 天 · 模型 p={b['p_model']} vs 市场价 {b['market_price']} · **gap={b['gap']:+}** → {tag}")
        lines.append("\n| 终端型市场 | 现货 | 行权 | 模型p | 市场价 | gap |")
        lines.append("|---|---|---|---|---|---|")
        for o in opps:
            lines.append(f"| {(o.get('question') or '')[:30]} | {_px(o['spot'])} | {_px(o['strike'])} | "
                         f"{o['p_model']} | {o['market_price']} | {o['gap']:+} |")
        lines.append("\n_gap = 模型概率 − 市场价;正=市场低估。仅终端型(到期是否高于/低于 X)才算 gap。_")
    else:
        lines.append("终端型市场(模型适用)里暂无可评分的错价。")
    if barrier:
        lines.append(f"\n**触碰型市场({a.get('n_barrier', len(barrier))} 个 · reach/dip/hit — "
                     "终端模型不适用,仅列出不评 gap)**:")
        lines.append("\n| 触碰型市场 | 现货 | 行权 | 市场价 |")
        lines.append("|---|---|---|---|")
        for o in barrier:
            lines.append(f"| {(o.get('question') or '')[:30]} | {_px(o['spot'])} | {_px(o['strike'])} | {o['market_price']} |")
        lines.append("\n_触碰型='期间内是否曾到过 X'(barrier),需要触碰概率模型,当前终端 lognormal 会系统性低估,故不计 gap。_")
    lines.append("\n_信号非确定性:现货可能反转,注意点差与结算/预言机时点。_")
    return "\n".join(lines)


def _format_promotion(v: dict, path: str) -> str:
    """Render the Lab promotion-gate verdict: is any strategy paper-ready, and why not."""
    strats = v.get("strategies") or []
    lines = [f"**晋级门评估 · 够不够上 paper** · {path}", "",
             f"**领域**:{v.get('domain')} · 已结算 {v.get('n')} 个"]
    if not strats:
        lines.append("\n" + (v.get("note") or "无可评估数据。"))
        return "\n".join(lines)
    ck = lambda b: "✅" if b else "❌"
    lines.append("\n| 策略 | n | brier_delta | ECE | 样本足 | 跑赢市场 | 校准 | 无泄漏 | **paper-ready** |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s in strats:
        g = s.get("gates", {})
        lines.append(f"| {s['signal']} | {s.get('n')} | {s.get('brier_delta', '—')} | {s.get('ece', '—')} | "
                     f"{ck(g.get('sample_adequate'))} | {ck(g.get('beats_market'))} | {ck(g.get('ece_pass'))} | "
                     f"{ck(g.get('pit_clean'))} | {'✅' if s.get('paper_ready') else '❌'} |")
    if v.get("paper_ready"):
        lines.append("\n**结论**:有策略通过全部 4 道门 → **可以上 paper**。")
    else:
        lines.append("\n**结论**:**没有策略够上 paper** —— 全部卡在门上(通常是 *跑赢市场* 那道:没有 alpha)。")
    lines.append("\n_晋级门(Lab 规则):样本足(n≥30)+ 跑赢市场(CI 不含 0)+ 校准 ECE≤0.05 + PIT 无泄漏,"
                 "四门全过才 paper-ready。_")
    return "\n".join(lines)


def _format_backtest_matrix(a: dict, path: str) -> str:
    """Render the strategy × domain matrix (brier_delta per cell, ✅ if beats market)."""
    matrix = a.get("matrix") or {}
    signals = a.get("signals") or []
    lines = [f"**策略 × 领域 回测矩阵 · backtest_matrix** · {path}", ""]
    if not matrix:
        lines.append("没有足够的已结算市场可回测(各领域样本不足)。")
        return "\n".join(lines)
    cats = list(matrix.keys())
    header = "| 策略＼领域 | " + " | ".join(f"{c}(n={matrix[c].get('n')})" for c in cats) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(cats) + 1))
    for sig in signals:
        cells = []
        for c in cats:
            v = matrix[c]["signals"].get(sig, {})
            bd = v.get("brier_delta")
            mark = "✅" if v.get("beats_market") else ""
            cells.append(f"{bd:+}{mark}" if isinstance(bd, (int, float)) else "—")
        lines.append(f"| {sig} | " + " | ".join(cells) + " |")
    winners = a.get("winners") or []
    if winners:
        lines.append("\n**跑赢市场的组合**:" + "、".join(f"{s}@{c}" for c, s in winners))
    else:
        lines.append("\n**结论**:**没有任何(策略,领域)组合稳定跑赢市场** —— 全域无 alpha(诚实,市场大体有效)。")
    lines.append("\n_单元格=brier_delta(正=跑赢市场,✅=CI 不含 0 显著)。这些是价格历史技术信号,真 edge 更可能在 microstructure / crypto 套利。_")
    return "\n".join(lines)


def _format_strategy_comparison(c: dict, path: str) -> str:
    """Render the multi-strategy backtest comparison over a domain's resolved markets."""
    strats = c.get("strategies") or []
    lines = [f"**策略对比回测** · {path}", "",
             f"**领域**:{c.get('domain')} · 已结算市场 {c.get('n_markets')} 个"]
    if not strats:
        lines.append("\n" + (c.get("note") or "该领域没有足够的已结算市场可回测,换个领域再试。"))
        return "\n".join(lines)
    lines.append("\n| 策略 | brier_delta | 跑赢市场 | 95% CI |")
    lines.append("|---|---|---|---|")
    for s in strats:
        win = "✅" if s.get("beats_market") else "❌"
        lo, hi = (s.get("ci") or [0.0, 0.0])[:2]
        lines.append(f"| {s['name']} | {s['brier_delta']:+.4f} | {win} | [{lo:+.4f}, {hi:+.4f}] |")
    best = c.get("best")
    if best:
        tail = "——跑赢市场 ✅" if best.get("beats_market") else "——但仍未跑赢市场,无 alpha。"
        lines.append(f"\n**最优**:{best['name']}(brier_delta={best['brier_delta']:+.4f}){tail}")
    lines.append("\n_注:brier_delta 正=跑赢市场(模型 Brier 更低);naive=直接信市场价的基准(≈0),"
                 "任何策略要证明有效,得稳定地做到正 delta 且 CI 不含 0。_")
    return "\n".join(lines)


def _format_recommendation(r: dict, path: str) -> str:
    """Render the Goal-2 result: topic → ranked candidates → recommended target."""
    ranked = r.get("ranked") or []
    top = r.get("top_pick")
    def _side(s):                                       # show YES/NO so p_true isn't misread
        o = s.get("outcome")
        return f" [{o}]" if o else ""
    lines = [f"**标的推荐** · {path}", "", f"**主题**:{r.get('topic')}"]
    if not ranked:
        lines.append("\n未找到与该主题相关的活跃可交易标的。可换个更具体的主题(球队/资产/事件名),或直接指定一个市场做分析。")
        return "\n".join(lines)
    if not r.get("has_positive_edge", True):            # honest: nothing underpriced
        lines.append("\n⚠ 该主题下**没有被低估(正 edge)的标的**——候选当前都偏贵或接近合理定价。"
                     "下面按相对机会排序,仅供观察,均未达下注门槛。")
    if top:
        act = (top.get("action") or "hold").upper()
        lines.append(f"\n**推荐**:{top.get('question')}{_side(top)}  \n"
                     f"→ **{act}** · p_true={top.get('p_true')} · edge={top.get('edge')} · "
                     f"APY={top.get('annualized_edge')} · 价 {top.get('price')}")
        if top.get("rationale"):
            lines.append(f"　理由:{top['rationale']}")
    if len(ranked) > 1:
        lines.append(f"\n**候选排序(共分析 {r.get('n_scored')} 个 · 正 edge=被低估优先)**:")
        for i, s in enumerate(ranked, 1):
            lines.append(f"{i}. {(s.get('question') or '')[:56]}{_side(s)} — "
                         f"{(s.get('action') or 'hold').upper()} · edge={s.get('edge')} · p_true={s.get('p_true')}")
    lines.append("\n_注:edge<6% 门槛者结论为 HOLD;正 edge=被低估(潜在做多),负 edge=偏贵。"
                 "可对推荐标的再跑 analyze_market 看完整框架。_")
    return "\n".join(lines)


def _kernel_summary(ctx) -> str:
    """Render a kernel Context into a readable answer for the chat bubble.

    Grounded structured deliverables (the analysis framework, batch results) take
    priority over the controller's free-text ``answer`` so the user sees the full,
    numeric result — the controller's takeaway is appended when present."""
    f = ctx.facts
    path = " → ".join(s.capability for s in ctx.trace) or "(no steps)"
    if "paper_trade" in f:                               # the action taken wins — it's terminal
        return _format_paper_trade(f["paper_trade"], path)
    if "settlement" in f:                                # settle + reflect outcome
        return _format_settlement(f["settlement"], path)
    if "recommendation" in f:                           # Goal-2: topic → recommended target
        out = _format_recommendation(f["recommendation"], path)
        if "market_analysis" in f:                      # controller also deep-analyzed the pick
            out += "\n\n---\n\n" + _format_market_analysis(f["market_analysis"], path)
        return out
    # Requested alpha/relational deliverables win over a generic market_analysis the
    # controller may have also run as an intermediate step.
    if "alpha_review" in f:                              # strategy validation + improvement
        return _format_alpha_review(f["alpha_review"], path)
    if "news_markets" in f:                              # news → affected markets + direction
        return _format_news_markets(f["news_markets"], path)
    if "prediction_logged" in f:                         # user's own subjective call recorded
        return _format_prediction_logged(f["prediction_logged"], path)
    if "prediction_journal" in f:                        # journal + personal calibration
        return _format_prediction_journal(f["prediction_journal"], path)
    if "market_radar" in f:                              # 'what changed today' discovery funnel
        return _format_radar(f["market_radar"], path)
    if "conditional_arb" in f:                           # cross-market conditional/implication arb scan
        return _format_conditional_arb(f["conditional_arb"], path)
    if "relational_alpha" in f:                          # event-relatedness engine
        return _format_relational(f["relational_alpha"], path)
    if "market_analysis" in f:                          # Goal-1 framework IS the grounded answer
        return _format_market_analysis(f["market_analysis"], path)  # no free-text append (avoids hallucinated punditry)
    # Final analytical deliverables win over intermediate steps (e.g. collections).
    # Focused scans (the pack the user selected) win over the broad hunt_alpha board.
    if "microstructure" in f:                            # order-flow scan (focused)
        return _format_microstructure(f["microstructure"], path)
    if "chart" in f:                                     # explicit visualization request
        return _format_chart(f["chart"], path)
    if "news_sentiment" in f:                            # news + sentiment signal
        return _format_news(f["news_sentiment"], path)
    if "opportunities" in f:                             # Lab monitor: strategy-scored actionable trades
        return _format_opportunities(f["opportunities"], path)
    if "alpha_hunt" in f:                                # top-level opportunity hunt (broad)
        return _format_alpha_hunt(f["alpha_hunt"], path)
    if "skill_report" in f:                              # calibration / skill report
        return _format_skill_report(f["skill_report"], path)
    if "portfolio_review" in f:                          # paper portfolio + P&L
        return _format_portfolio(f["portfolio_review"], path)
    if "crypto_arb" in f:                                # cross-market crypto arbitrage scan
        return _format_crypto_arb(f["crypto_arb"], path)
    if "promotion_verdict" in f:                         # Lab promotion gates — paper-ready?
        return _format_promotion(f["promotion_verdict"], path)
    if "lab_backtest" in f:                              # Lab feature-strategy evidence backtest
        return _format_lab_backtest(f["lab_backtest"], path)
    if "outcome_backfill" in f:                          # labelled snapshots for the Lab backtest
        return _format_backfill(f["outcome_backfill"], path)
    if "backtest_matrix" in f:                           # strategy × domain matrix
        return _format_backtest_matrix(f["backtest_matrix"], path)
    if "strategy_comparison" in f:                       # multi-strategy backtest comparison
        return _format_strategy_comparison(f["strategy_comparison"], path)
    if "backtest_report" in f:
        r = f["backtest_report"]
        return (f"**kernel** {path}\n\n回测 · event={r.get('event')} · n_markets={r.get('n_markets')} · "
                f"brier_delta={r.get('brier_delta')} · beats_market={r.get('beats_market')} · "
                f"ci={r.get('ci')}")
    if "collections" in f:                              # intermediate: only if no analytical result above
        c = f["collections"]
        return (f"**kernel** {path}\n\n批量采集 · 市场数={c.get('n_markets')} · "
                f"store={c.get('store_counts')}")
    if "answer" in f:                                   # ReAct / Q&A capability — its text IS the answer
        return _answer_text(f)
    if "decision" in f:
        return f"**kernel** {path}\n\ndecision: {f['decision']}"
    if "evaluation" in f:
        return f"**kernel** {path}\n\nevaluation: {f['evaluation']}"
    return f"**kernel** 无法完成目标 {sorted(ctx.goal.targets)}(路径: {path})。"


async def _stream_kernel(history: list[dict], session: "AgentSession",
                         packs: list[str] | None = None,
                         model: str | None = None) -> AsyncIterator[str]:
    """Kernel mode: the request goes through the ONE kernel loop, which recognises
    intent and takes the minimal capability path (Q&A via ReAct, or data→backtest,
    …). The prior turns are passed as cross-turn memory; ``packs`` selects which
    vertical capability packs are loaded. Runs the sync loop in a thread and bridges
    its ``on_event`` to SSE."""
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
            ctx = run_mode("kernel", request=last_text, history=prior, packs=packs,
                           model=model, on_event=on_event)
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
                  mode: str = "auto", packs: list[str] | None = None) -> AsyncIterator[str]:
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
        async for ev in _stream_kernel(history, session, packs, model=model):
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
    packs = body.get("packs")                            # kernel: which vertical packs to load (None = all)
    return StreamingResponse(_stream(history, skills, model, attachments, mode, packs),
                             media_type="text/event-stream")
