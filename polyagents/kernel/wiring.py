"""Real wiring for the kernel — a registry backed by the live engine, Lab
BacktestRunner, the Ask LangGraph agent, and the Strategy supervisor.

Lazy + best-effort (imports and the engine are only touched when built), so the
kernel core stays import-light and offline-testable (tests inject fakes; this is
the production wiring). Needs network / ANTHROPIC_API_KEY at run time.
"""
from __future__ import annotations

import math
import re

from .capabilities import (analyze_market_capability, answer_capability,
                           backfill_outcomes_capability,
                           backtest_capability, backtest_matrix_capability,
                           backtest_strategies_capability,
                           batch_backtest_capability, batch_collect_capability,
                           crypto_arb_capability, data_capability,
                           lab_backtest_capability,
                           discover_markets_capability, domain_capability,
                           evaluate_skill_capability, hunt_alpha_capability,
                           microstructure_scan_capability, news_sentiment_capability,
                           paper_trade_capability, portfolio_review_capability,
                           settle_and_reflect_capability,
                           promotion_gate_capability, recommend_markets_capability,
                           resolve_market_capability, scan_capability,
                           scan_opportunities_capability, strategy_capability)


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via erf — for the lognormal crypto-arb probability."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _plain_answer(question: str, emit=None) -> str:
    """No-tools fallback: a plain LLM answer when the ReAct tool-agent errors (e.g. a
    DeepSeek malformed tool call → 400 'bad parameter'). Keeps the loop answering
    instead of surfacing a raw API error."""
    from polyagents.llm import build_chat_llm
    try:
        resp = build_chat_llm(temperature=0.2).invoke([
            ("system", "You are a Polymarket prediction-market research assistant. Answer "
             "concisely and honestly from general knowledge; if you could not fetch live "
             "market data, say so briefly in one line."),
            ("user", question or "")])
        text = _chunk_text(getattr(resp, "content", resp))
    except Exception as exc:
        text = f"(暂时无法完成:{exc})"
    if emit is not None:
        emit({"type": "token", "text": text})
    return text


_CX_ASSETS = {"btc": "BTC", "bitcoin": "BTC", "eth": "ETH", "ethereum": "ETH",
              "sol": "SOL", "solana": "SOL", "xrp": "XRP", "doge": "DOGE",
              "dogecoin": "DOGE", "bnb": "BNB", "ada": "ADA", "cardano": "ADA"}


def parse_crypto_market(question: str) -> dict | None:
    """Parse a crypto threshold market → {asset, strike, direction}, or None.

    Handles 'Will BTC be above $110k …', '$2,500', 'ETH below 3000', etc."""
    q = (question or "").lower()
    asset = next((v for k, v in _CX_ASSETS.items() if re.search(rf"\b{k}\b", q)), None)
    if not asset:
        return None
    m = re.search(r"\$\s*([\d][\d,]*\.?\d*)\s*([kmb])?", q) \
        or re.search(r"\b([\d][\d,]*\.?\d*)\s*([kmb])\b", q)   # $-anchored, else number+suffix
    if not m:
        return None
    strike = float(m.group(1).replace(",", "")) * {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2), 1.0)
    # downward move (dip/drop/fall/below) resolves YES if price goes down to the strike;
    # everything else (reach/hit/above/over/exceed) is an upward threshold.
    down = re.search(r"\b(dip|drop|fall|below|under|less than|down to)\b|<", q)
    direction = "below" if down else "above"
    # terminal ('be above/below X on/by <date>') vs barrier/touch ('reach/hit/dip/… X').
    # The zero-drift terminal lognormal only models terminal markets; barrier markets
    # (touch-any-time) need a barrier prob, so we don't score them.
    barrier = re.search(r"\b(reach|reaches|hit|hits|dip|drop|fall|touch|touches|ever|cross|crosses)\b", q)
    kind = "barrier" if barrier else "terminal"
    return {"asset": asset, "strike": strike, "direction": direction, "kind": kind}


def _chunk_text(content) -> str:
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content if isinstance(content, str) else str(content or "")


def _stream_agent(agent, question: str, emit) -> str:
    """Drive a LangGraph agent's ``astream_events`` in a fresh loop, emitting inner
    tokens/tool-calls via ``emit`` as they happen, and return the full text. Runs in
    the kernel's worker thread (no running loop there), so ``asyncio.run`` is safe."""
    import asyncio

    async def drive() -> str:
        parts: list[str] = []
        async for ev in agent.astream_events(
                {"messages": [("user", question or "")]}, version="v2"):
            kind = ev.get("event")
            if kind == "on_chat_model_stream":
                t = _chunk_text(ev["data"]["chunk"].content)
                if t:
                    parts.append(t)
                    emit({"type": "token", "text": t})
            elif kind == "on_tool_start":
                emit({"type": "tool", "name": ev.get("name")})
            elif kind == "on_tool_end":
                emit({"type": "tool_result", "name": ev.get("name")})
        return "".join(parts)

    return asyncio.run(drive())


def default_registry() -> list:
    """Wire data → backtest (over resolved markets), plus answer + strategy.

    ``event`` is treated as a free-text handle; we slice resolved markets by its
    keyword category, then replay a deterministic backtest over them."""
    from polyagents import mcp_server
    from polyagents.evaluation.evaluate import categorize
    from polyagents.lab.backtest import BacktestRunner
    from polyagents.strategies import SIGNALS

    eng = mcp_server.engine()

    def _resolved_yes(query):
        """Resolved YES-side markets, sliced to the request's keyword category."""
        cat = categorize(query or "")
        raw = eng.client.list_resolved_markets(limit=80)
        yes = [m for m in eng.client.to_markets(raw) if m.outcome == "YES"]
        if cat != "other":
            yes = [m for m in yes if categorize(m.question) == cat]
        return cat, yes

    def fetch(event):
        cat, yes = _resolved_yes(event)
        return {"event": event, "category": cat, "markets": yes}

    def _replay(markets, event=None):
        out = BacktestRunner(client=eng.client, max_markets=20, store=getattr(eng, "store", None)).replay(
            category=None, markets=markets)
        s = out["summary"]
        return {"event": event, "n_markets": out["n_markets"],
                "brier_delta": s.brier_delta, "beats_market": s.beats_market,
                "ci": list(s.brier_delta_ci)}

    def backtest(history):
        return _replay(history["markets"], event=history.get("event"))

    # ----- batch data pipeline (scan -> collect / backtest) ------------------

    def scan(query):
        rows = mcp_server.scan_markets(limit=8, min_volume_24h=20000.0)
        return {"query": query, "count": len(rows), "markets": rows}

    def batch_collect(market_batch, cap=5):
        rows = (market_batch or {}).get("markets", [])[:cap]
        collected = []
        for row in rows:
            m = mcp_server._get_market(row.get("token_id", ""))
            if m is None:
                continue
            eng.collect(m)
            collected.append(row.get("question") or row.get("token_id"))
        counts = eng.store.counts() if getattr(eng, "store", None) else {}
        return {"n_markets": len(collected), "collected": collected,
                "store_counts": counts}

    def batch_backtest(query):
        _cat, yes = _resolved_yes(query)
        return _replay(yes, event=query)

    # ----- cross-market crypto arbitrage (spot vs implied probability) -------

    _cx_vol: dict = {}                                   # per-request daily-vol cache

    def _daily_vol(cx, asset):
        if asset in _cx_vol:
            return _cx_vol[asset]
        closes = (cx.crypto_klines(asset, interval="1d", limit=30) or {}).get("closes")
        if closes and len(closes) > 2:
            rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
            mean = sum(rets) / len(rets)
            sig = math.sqrt(sum((x - mean) ** 2 for x in rets) / len(rets))
        else:
            sig = 0.03                                   # ~3%/day fallback for crypto
        _cx_vol[asset] = sig
        return sig

    def find_crypto_arb(query, cap=8, universe=400):
        """Scan crypto threshold markets, estimate YES prob from spot+vol, rank mispricings.

        Pulls a large active-market universe directly (crypto threshold markets are
        often not top-by-volume, so the volume-capped scan would miss them)."""
        from polyagents.mcp_servers import crypto as cx
        by_cond = {}                                     # dedup by market, keep the YES side
        for m in eng.client.to_markets(eng.client.list_active_markets(limit=universe)):
            parsed = parse_crypto_market(m.question)
            if not parsed:
                continue
            if m.condition_id not in by_cond or m.outcome == "YES":
                by_cond[m.condition_id] = (m, parsed)
        spot: dict = {}
        opps, barrier = [], []                           # terminal (scored) vs touch (listed only)
        for m, parsed in by_cond.values():
            asset, K = parsed["asset"], parsed["strike"]
            if asset not in spot:
                sp = cx.crypto_price(asset)
                spot[asset] = sp.get("price") if isinstance(sp, dict) and "error" not in sp else None
            S = spot[asset]
            if not S or K <= 0:
                continue
            T = max(float(m.days_to_expiry or 0.0), 0.0)
            price = float(m.price or 0.0)
            row = {"question": m.question, "token_id": m.token_id, "asset": asset,
                   "strike": K, "direction": parsed["direction"], "spot": round(float(S), 2),
                   "days": round(T, 1), "market_price": round(price, 3)}
            if parsed.get("kind") == "barrier":          # touch event — terminal model invalid, don't score
                barrier.append(row)
                continue
            sig_h = _daily_vol(cx, asset) * math.sqrt(T) if T > 0 else 0.0
            if sig_h <= 0:
                p_above = 1.0 if S > K else 0.0
            else:
                p_above = _norm_cdf(math.log(S / K) / sig_h)   # zero-drift lognormal
            p_yes = min(0.99, max(0.01, p_above if parsed["direction"] == "above" else 1.0 - p_above))
            opps.append({**row, "p_model": round(p_yes, 3), "gap": round(p_yes - price, 3)})
        opps.sort(key=lambda o: abs(o["gap"]), reverse=True)
        opps = opps[:cap]
        return {"query": query, "n": len(opps), "opportunities": opps,
                "best": opps[0] if opps else None,
                "barrier_markets": barrier[:cap], "n_barrier": len(barrier)}

    def _flow_signal(m, factors):
        """Score one market's microstructure/flow: strong one-sided flow+book with an
        un-moved price = potential edge; wide spread = penalised (untradeable)."""
        fi = float(factors.get("flow_imbalance", 0.0))      # buy vs sell flow
        bp = float(factors.get("book_pressure", 0.0))       # bid vs ask depth
        vs = float(factors.get("volume_spike_ratio", 0.0))  # unusual activity
        pm = float(factors.get("price_momentum", 0.0))      # has price moved yet
        spread = float(factors.get("spread_bps", 0.0))
        mvm = float(factors.get("micro_vs_mid", 0.0))
        conviction = 0.5 * abs(fi) + 0.3 * abs(bp) + 0.2 * min(vs / 3.0, 1.0)
        unpriced = 1.0 + max(0.0, 0.15 - abs(pm))           # flow strong but price flat = edge
        tradeable = spread < 300.0
        return {"question": m.question, "token_id": m.token_id,
                "flow_imbalance": round(fi, 3), "book_pressure": round(bp, 3),
                "volume_spike": round(vs, 2), "price_momentum": round(pm, 3),
                "spread_bps": round(spread, 0), "micro_vs_mid": round(mvm, 4),
                "score": round(conviction * unpriced * (1.0 if tradeable else 0.3), 3),
                "tradeable": tradeable,
                "lean": "YES(资金买盘占优)" if (fi + bp) > 0 else "NO/谨慎(卖盘占优)"}

    def _scan_flow(rows, cap):
        out = []
        for row in rows[:cap]:
            m = mcp_server._get_market(row.get("token_id", ""))
            if m is None:
                continue
            try:
                factors = ((eng.collect(m).get("raw", {}) or {}).get("features", {}) or {}).get("factors", {})
            except Exception:
                continue
            out.append(_flow_signal(m, factors))
        out.sort(key=lambda x: x["score"], reverse=True)
        return out

    def hunt_alpha(query, n_micro=6):
        """Top-level opportunity hunt: crypto spot-vs-implied mispricings + microstructure
        smart-money flow, consolidated and ranked. Deterministic (no LLM), honest."""
        crypto = (find_crypto_arb(query).get("opportunities") or [])[:5]   # reuse crypto detector
        flow = _scan_flow(mcp_server.scan_markets(limit=n_micro, min_volume_24h=20000.0), n_micro)
        return {"query": query, "crypto": crypto, "n_crypto": len(crypto),
                "flow": flow[:5], "n_flow_scanned": len(flow)}

    def scan_opportunities(query, limit=12):
        """Dry-run opportunity monitor (colleague's Lab LabMonitor), driven from Ask:
        score live active markets with a Lab strategy and rank actionable trades."""
        from polyagents.lab.monitor import LabMonitor, MonitorRequest
        from polyagents.lab.strategies import STRATEGIES
        q = (query or "").lower()
        # match a named Lab strategy by keyword vs its versioned id (momentum→momentum-v1),
        # else fall back to the default factor model
        chosen = next((sid for sid in STRATEGIES
                       if any(len(w) >= 4 and w in sid for w in q.split())), None)
        req_kw = {"limit": limit, "include_holds": False}   # Ask wants actionable ideas
        if chosen:
            req_kw["strategy_id"] = chosen
        try:
            monitor = LabMonitor(client=eng.client, config=eng.config)
            out = monitor.scan(MonitorRequest(**req_kw))
        except Exception as exc:                            # degrade honestly, never fabricate
            return {"query": query, "n": 0, "opportunities": [],
                    "error": f"{type(exc).__name__}: {exc}"}
        return {"query": query, **out}

    # ----- pack: lab-backtest (label snapshots -> Lab feature-strategy backtest) --

    def backfill_outcomes(query, limit=1000):
        """Label stored collection snapshots with realised outcomes, so the Lab
        feature-strategies can backtest on real data. Writes to eng.store — the
        shared cloud Postgres when POLYAGENTS_DATABASE_URL is set, else local SQLite."""
        from datetime import datetime, timezone
        store = getattr(eng, "store", None)
        if store is None:
            return {"error": "no data store configured (persist_enabled=False?)"}
        raw = eng.client.list_resolved_markets(limit=limit)      # token -> realised outcome
        resolved = {m.token_id: (1 if m.price >= 0.5 else 0)
                    for m in eng.client.to_markets(raw) if m.outcome == "YES"}
        cols = store.fetch_collections(limit=limit)
        now = datetime.now(timezone.utc).isoformat()
        already = newly = unresolved = 0
        for row in cols:
            bundle = row.get("raw") or {}
            lab = bundle.get("lab") or {}
            if lab.get("outcome") is not None:
                already += 1; continue
            tok = row["token_id"]
            if tok in resolved:
                bundle = {**bundle, "lab": {**lab, "outcome": resolved[tok],
                                            "outcome_source": "resolved_market", "labeled_at": now}}
                store.record_collection(tok, row["as_of"], row.get("question") or "",
                                        row.get("market_price") or 0.5, bundle)
                newly += 1
            else:
                unresolved += 1
        backend = "postgres" if getattr(store.engine, "dialect", None) and \
            store.engine.dialect.name == "postgresql" else "sqlite"
        return {"query": query, "backend": backend, "scanned": len(cols),
                "already_labeled": already, "newly_labeled": newly,
                "still_unresolved": unresolved, "labeled_total": already + newly,
                "store_counts": store.counts()}

    def lab_backtest(query):
        """Run the colleague's Lab evidence backtest: a Lab feature-strategy scored over
        the labelled collection snapshots -> EvaluationReport metrics + promotion gates."""
        from datetime import datetime, timezone
        from polyagents.lab.backtest import BacktestRunner, get_report
        from polyagents.lab.schemas import BacktestRequest
        from polyagents.lab.strategies import DEFAULT_STRATEGY_ID, STRATEGIES
        q = (query or "").lower()
        sid = next((s for s in STRATEGIES if any(len(w) >= 4 and w in s for w in q.split())),
                   DEFAULT_STRATEGY_ID)
        cat = categorize(query or "")
        req = BacktestRequest(
            hypothesis_id=f"ask_{abs(hash(query)) % 10**8}",
            time_window={"start": "2000-01-01T00:00:00+00:00",
                         "end": datetime.now(timezone.utc).isoformat()},
            market_filter={"category": cat if cat != "other" else "all", "settled_only": True},
            model_version="ask", prompt_version="ask", calibrator_id="market",
            strategy_id=sid, pit_strict=False, max_markets=200)
        try:
            result = BacktestRunner(client=eng.client, store=getattr(eng, "store", None)).run(req)
        except Exception as exc:
            return {"query": query, "strategy_id": sid, "error": f"{type(exc).__name__}: {exc}"}
        report = get_report(result.report_id) or {}
        m = report.get("metrics", {}) or {}
        dq = report.get("data_quality", {}) or {}
        return {"query": query, "strategy_id": sid, "category": cat,
                "n": result.forecast_count, "uses_fixture": bool(dq.get("uses_fixture_data")),
                "brier_delta": m.get("brier_delta"), "brier_model": m.get("brier_model"),
                "brier_market": m.get("brier_market"), "ece": m.get("ece"),
                "gates": report.get("gates"), "report_id": result.report_id}

    # ----- vertical pack capabilities: news-events / microstructure ----------

    def settle_and_reflect(query):
        """Settle resolved paper trades (book P&L) + Layer-4 reflection (write lessons)."""
        settled = eng.settle(reflect=True)
        recs = [{"question": s.get("question"), "won": s.get("won"),
                 "resolved_winner": s.get("resolved_winner"),
                 "realized_pnl": s.get("realized_pnl"), "realized_return": s.get("realized_return"),
                 "lesson": s.get("lesson")} for s in settled]
        return {"n_settled": len(recs), "settled": recs,
                "portfolio": mcp_server.portfolio_status()}

    def paper_trade(market_ref):
        """Analyse a market, take the deterministic sized decision, and paper-execute if
        it's actionable (buy/sell). Paper money, through the circuit breaker."""
        ref = market_ref or {}
        m = mcp_server._get_market(ref.get("token_id", "")) if ref.get("token_id") else None
        if m is None:
            return {"error": f"market not found: {ref}"}
        core = _analysis_core(m)
        sig, dec = core["signal"], core["decision"]
        if dec is None:
            return {"market": {"question": m.question, "price": m.price},
                    "action": "hold", "executed": False, "note": "no decision",
                    "portfolio": mcp_server.portfolio_status()}
        result, executed = None, False
        if dec.action in ("buy", "sell") and dec.size_usdc > 0:
            result = mcp_server.paper_execute(m.token_id, dec.action, round(dec.size_usdc, 2))
            executed = result.get("status") == "filled"
        return {
            "market": {"question": m.question, "token_id": m.token_id, "price": round(m.price, 4)},
            "action": dec.action, "p_true": round(sig.p_true, 3) if sig is not None else None,
            "edge": round(dec.edge, 4), "size_usdc": round(dec.size_usdc, 2),
            "reasons": dec.reasons, "executed": executed, "result": result,
            "portfolio": mcp_server.portfolio_status(),
        }

    def evaluate_skill(query):
        return {"report": mcp_server.evaluation_report()}

    def portfolio_review(query):
        return {"portfolio": mcp_server.portfolio_status(), "pnl": mcp_server.pnl_report()}

    def news_sentiment(query):
        nc = eng.news_client
        if not getattr(nc, "enabled", False):
            return {"query": query, "enabled": False,
                    "note": "新闻/情绪需要 TAVILY_API_KEY(.env),当前未配置"}
        items = nc.search(query or "", max_results=6)
        scored = []
        for it in items:
            s = eng.scorer.score(f"{getattr(it, 'title', '')} {getattr(it, 'snippet', '')}")
            scored.append({"title": getattr(it, "title", ""), "url": getattr(it, "url", ""),
                           "sentiment": round(float(s), 3)})
        mean = round(sum(x["sentiment"] for x in scored) / len(scored), 3) if scored else 0.0
        signal = "偏多" if mean > 0.1 else ("偏空" if mean < -0.1 else "中性")
        return {"query": query, "enabled": True, "n_items": len(scored),
                "mean_sentiment": mean, "signal": signal, "items": scored}

    def microstructure_scan(query, n=10):
        cat = categorize(query or "")
        rows = mcp_server.scan_markets(limit=30, min_volume_24h=10000.0)
        if cat != "other":                              # narrow to the request's domain if any
            rows = [r for r in rows if categorize(r.get("question", "")) == cat] or rows
        ranked = _scan_flow(rows, n)
        return {"query": query, "category": cat, "n_scanned": len(ranked), "markets": ranked[:8]}

    def promotion_gate(query):
        """loop→Lab bridge: backtest each strategy over the domain, then run Lab's
        promotion gates (sample / beats-market / ECE / PIT) → is any PAPER-READY?"""
        from polyagents.evaluation.report import build_evaluation_summary, promotion_gates
        cat, yes = _resolved_yes(query)
        if not yes:                                     # domain empty → all resolved
            raw = eng.client.list_resolved_markets(limit=80)
            yes = [m for m in eng.client.to_markets(raw) if m.outcome == "YES"]
            cat = f"{cat}→all"
        if not yes:
            return {"domain": cat, "n": 0, "strategies": [], "paper_ready": False,
                    "note": "no resolved markets to evaluate"}
        strategies = []
        for name, fn in SIGNALS.items():
            recs = BacktestRunner(client=eng.client, max_markets=20, signal_fn=fn, store=getattr(eng, "store", None)).replay(
                markets=yes)["records"]
            if not recs:
                strategies.append({"signal": name, "n": 0, "gates": {}, "paper_ready": False})
                continue
            summary = build_evaluation_summary(
                p_cal=[r["p_true"] for r in recs],
                p_market=[r["market_price"] for r in recs],
                outcomes=[1.0 if r["won"] else 0.0 for r in recs],
                pit_clean=True)                          # replay is strictly point-in-time
            gates = promotion_gates(summary)
            strategies.append({"signal": name, "n": summary.n,
                               "brier_delta": round(summary.brier_delta, 4),
                               "ece": round(summary.ece, 4),
                               "gates": gates, "paper_ready": gates["paper_ready"]})
        return {"domain": cat, "n": max((s["n"] for s in strategies), default=0),
                "strategies": strategies,
                "paper_ready": any(s.get("paper_ready") for s in strategies)}

    def _multi_score(markets, cap=12):
        """Score EVERY signal on the same PIT candle slice per market (one price-history
        fetch per market, not one per signal) → per-signal alpha summaries. Mirrors
        BacktestRunner._score_market's point-in-time setup, and reads candles
        store-first (live only as fallback) via a shared runner."""
        from polyagents.evaluation.alpha import alpha_test
        runner = BacktestRunner(client=eng.client, store=getattr(eng, "store", None))
        per = {name: [] for name in SIGNALS}
        scored = 0
        for m in markets:
            if scored >= cap:
                break
            if not (m.price <= 0.05 or m.price >= 0.95):        # same extreme-price filter
                continue
            won = m.price >= 0.5
            candles = runner.candles_for(m)
            if len(candles) < 5:
                continue
            idx = min(max(int(0.5 * len(candles)), 4), len(candles) - 1)
            pit = [c for c in candles[:idx] if c.ts < candles[idx].ts]
            if len(pit) < 4:
                continue
            market_p = pit[-1].close
            if not (0.02 < market_p < 0.98):
                continue
            for name, fn in SIGNALS.items():
                per[name].append({"status": "resolved", "won": won,
                                  "p_true": float(fn(pit, market_p)),
                                  "market_price": market_p, "question": m.question})
            scored += 1
        return {name: alpha_test(recs) for name, recs in per.items()}, scored

    def backtest_matrix(query, cap=12):
        """Strategy × domain matrix: every signal over every category's resolved markets,
        one consolidated board of which (if any) combos beat the market."""
        raw = eng.client.list_resolved_markets(limit=120)
        all_yes = [m for m in eng.client.to_markets(raw) if m.outcome == "YES"]
        matrix = {}
        for cat in ("crypto", "sports", "politics", "economy", "other"):
            yes = [m for m in all_yes if categorize(m.question) == cat]
            if not yes:
                continue
            summaries, n = _multi_score(yes, cap)
            if n == 0:
                continue
            matrix[cat] = {"n": n, "signals": {
                name: {"brier_delta": round(s.brier_delta, 4), "beats_market": s.beats_market}
                for name, s in summaries.items()}}
        winners = [(cat, sig) for cat, row in matrix.items()
                   for sig, v in row["signals"].items() if v["beats_market"]]
        return {"query": query, "signals": list(SIGNALS), "matrix": matrix, "winners": winners}

    def backtest_strategies(query):
        """Run every built-in strategy signal over the domain's resolved markets and
        compare — which (if any) beats the market."""
        cat, yes = _resolved_yes(query)
        if not yes:                                     # domain misdetected/empty → all resolved
            raw = eng.client.list_resolved_markets(limit=80)
            yes = [m for m in eng.client.to_markets(raw) if m.outcome == "YES"]
            cat = f"{cat}→all(no per-domain data)"
        if not yes:
            return {"domain": cat, "n_markets": 0, "strategies": [], "best": None,
                    "note": "no resolved markets to backtest"}
        strategies = []
        for name, fn in SIGNALS.items():
            out = BacktestRunner(client=eng.client, max_markets=20, signal_fn=fn, store=getattr(eng, "store", None)).replay(
                category=None, markets=yes)
            s = out["summary"]
            strategies.append({"name": name, "n_markets": out["n_markets"],
                               "brier_delta": s.brier_delta, "beats_market": s.beats_market,
                               "ci": list(s.brier_delta_ci)})
        strategies.sort(key=lambda x: x["brier_delta"], reverse=True)   # best (highest delta) first
        n = max((s["n_markets"] for s in strategies), default=0)
        return {"domain": cat, "n_markets": n, "strategies": strategies,
                "best": strategies[0] if strategies else None}

    # ----- Goal 1: single-target analysis framework --------------------------
    #   resolve_market -> analyze_market
    #   explore -> reason -> analyze -> backtest (historical comparison) -> conclude

    def resolve(query):
        """Pick ONE concrete market for the request: explicit token id, else best
        keyword match among live markets, else the most active."""
        q = (query or "").strip()
        m = mcp_server._get_market(q) if q else None      # exact token id?
        if m is None:
            rows = mcp_server.scan_markets(limit=25, min_volume_24h=0.0)
            words = {w for w in q.lower().split() if len(w) > 2}
            best, best_hits = None, 0
            for row in rows:
                hits = sum(1 for w in words if w in str(row.get("question", "")).lower())
                if hits > best_hits:
                    best, best_hits = row, hits
            if best is not None:
                return {"token_id": best["token_id"], "question": best["question"],
                        "price": best["price"], "matched_by": f"keywords({best_hits})"}
            m = eng.most_active_market()                   # nothing matched
        if m is None:
            return {"error": "no market found", "query": query}
        return {"token_id": m.token_id, "question": m.question, "price": m.price,
                "matched_by": "token_id" if q == m.token_id else "most_active"}

    _analysis_cache: dict = {}                              # per-request memo, keyed by token_id

    def _analysis_core(m):
        """Shared L1+L2 analysis for one market — the scoring core reused by both
        analyze_market (full framework) and recommend_markets (rank candidates).

        Memoized by token for this request, so a market recommended AND then
        deep-analyzed is only run through the LLM once — the recommendation and the
        framework then agree (same p_true / narrative) instead of two stochastic runs."""
        cached = _analysis_cache.get(m.token_id)
        if cached is not None:
            return cached
        state = eng.analyze(m)                              # L1 collect + L2 signal/decision/reflect (LLM)
        core = {"state": state, "signal": state.get("signal"),
                "decision": state.get("trade_decision"), "reflection": state.get("reflection")}
        _analysis_cache[m.token_id] = core
        return core

    def analyze_market(market_ref):
        """Run the whole Goal-1 framework on one market and return a structured result."""
        ref = market_ref or {}
        token = ref.get("token_id")
        m = mcp_server._get_market(token) if token else None
        if m is None:
            return {"error": f"market not found: {ref}", "market_ref": ref}

        core = _analysis_core(m)
        state, sig, dec, refl = core["state"], core["signal"], core["decision"], core["reflection"]
        factors = ((state.get("raw", {}) or {}).get("features", {}) or {}).get("factors", {})

        cat, yes = _resolved_yes(m.question)               # 回溯对比: backtest the signal on comparable history
        backtest = _replay(yes, event=m.question) if yes else {
            "n_markets": 0, "note": f"no resolved '{cat}' markets to backtest against"}

        try:                                            # 相似历史:只保留已结算(有 winner)的先例
            similar = [s for s in mcp_server.find_similar_markets(m.question, n=8)
                       if s.get("resolved_winner")][:3]
        except Exception:
            similar = []

        return {
            "market": {"token_id": m.token_id, "question": m.question, "price": m.price,
                       "category": cat, "days_to_expiry": round(m.days_to_expiry, 1),
                       "liquidity": m.liquidity},
            "explore": {"price_report": state.get("price_report"),
                        "orderbook_report": state.get("orderbook_report"),
                        "trades_flow_report": state.get("trades_flow_report")},
            "microstructure": factors,
            "reasoning": ({"p_true": sig.p_true, "direction": sig.direction,
                           "conviction": sig.conviction, "rationale": sig.rationale}
                          if sig is not None else {"note": "no signal (LLM unavailable)"}),
            "backtest": backtest,
            "similar_markets": similar,
            "conclusion": ({"action": dec.action, "edge": round(dec.edge, 4),
                            "p_calibrated": round(dec.p_true, 4),
                            "annualized_edge": round(dec.annualized_edge, 4),
                            "size_usdc": round(dec.size_usdc, 2), "reasons": dec.reasons,
                            "risk_flags": (refl.risk_flags if refl is not None else [])}
                           if dec is not None else {"note": "no decision"}),
        }

    # ----- Goal 2: topic → recommend a trading target ------------------------
    #   discover_markets -> recommend_markets (reuses the analysis core to score)

    def _topic_terms(topic):
        """Search terms for a topic: raw words + LLM-extracted English keywords, so a
        Chinese / free-text topic still matches English market questions."""
        terms = {w for w in (topic or "").lower().split() if len(w) > 2}
        try:
            resp = eng._get_llm().invoke([
                ("system", "Extract 3-8 short English search keywords/entities (people, "
                 "teams, places, assets, events) from the user's topic, for matching "
                 "prediction-market questions. Reply with ONLY a comma-separated list."),
                ("user", topic or "")])
            text = getattr(resp, "content", resp)
            if isinstance(text, list):
                text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
            terms |= {t.strip().lower() for t in str(text).split(",") if len(t.strip()) > 2}
        except Exception:
            pass
        return terms

    def _words(s):                                      # alpha words >2 chars; drops digits/years
        return {w for w in re.findall(r"[a-z]+", str(s).lower()) if len(w) > 2}

    def discover(topic):
        term_words = set()                              # word-overlap match is robust to phrasing
        for t in _topic_terms(topic):                   # ("2026 world cup" still contributes world/cup)
            term_words |= _words(t)
        rows = mcp_server.scan_markets(limit=40, min_volume_24h=5000.0)
        by_cond = {}                                    # dedup by market, keep the YES side
        for row in rows:
            if (row.get("days_to_expiry") or 0) < 1:    # skip settling / same-day markets
                continue
            cid = row.get("condition_id")
            if cid not in by_cond or row.get("outcome") == "YES":
                by_cond[cid] = row
        scored = []
        for row in by_cond.values():
            hits = len(term_words & _words(row.get("question", "")))
            if hits:
                scored.append((hits, row))
        scored.sort(key=lambda t: (t[0], t[1].get("volume_24h", 0.0)), reverse=True)
        markets = [{**r, "relevance": h} for h, r in scored[:6]]
        return {"topic": topic, "count": len(markets), "markets": markets,
                "terms": sorted(term_words)}

    def recommend(candidates, top_n=3):
        cands = (candidates or {}).get("markets", [])[:top_n]
        scored = []
        for row in cands:
            m = mcp_server._get_market(row.get("token_id", ""))
            if m is None:
                continue
            try:
                core = _analysis_core(m)
            except Exception:
                continue
            sig, dec = core["signal"], core["decision"]
            scored.append({
                "token_id": m.token_id, "question": m.question, "outcome": m.outcome,
                "price": round(m.price, 4),
                "p_true": round(sig.p_true, 3) if sig is not None else None,
                "edge": round(dec.edge, 4) if dec is not None else None,
                "action": dec.action if dec is not None else None,
                "annualized_edge": round(dec.annualized_edge, 4) if dec is not None else None,
                "rationale": sig.rationale if sig is not None else None,
            })
        # rank: actionable (buy/sell) first, then by SIGNED edge — a positive edge means
        # underpriced (an attractive long); a negative edge means overpriced, not a pick.
        scored.sort(key=lambda s: (1 if s.get("action") in ("buy", "sell") else 0,
                                   s.get("edge") or 0.0), reverse=True)
        has_positive_edge = any((s.get("edge") or 0.0) > 0 for s in scored)
        return {"topic": (candidates or {}).get("topic"), "n_scored": len(scored),
                "ranked": scored, "top_pick": scored[0] if scored else None,
                "has_positive_edge": has_positive_edge}

    def _last_content(res):
        msgs = res.get("messages", []) if isinstance(res, dict) else []
        last = msgs[-1] if msgs else None
        return getattr(last, "content", "") if last is not None else ""

    def answer(question):                              # general / web-search agent
        from polyagents.web.agent import build_general_agent
        try:
            return _last_content(build_general_agent().invoke(
                {"messages": [("user", question or "")]}))
        except Exception:                              # tool-call/API error → no-tools fallback
            return _plain_answer(question)

    def answer_stream(question, emit):
        from polyagents.web.agent import build_general_agent
        try:
            return _stream_agent(build_general_agent(), question, emit)
        except Exception:
            return _plain_answer(question, emit)

    def domain_answer(question):                       # read-only market-tools agent
        from polyagents.web.agent import build_agent
        try:
            return _last_content(build_agent(readonly=True).invoke(
                {"messages": [("user", question or "")]}))
        except Exception:
            return _plain_answer(question)

    def domain_stream(question, emit):
        from polyagents.web.agent import build_agent
        try:
            return _stream_agent(build_agent(readonly=True), question, emit)
        except Exception:                              # DeepSeek bad tool-call → graceful text
            return _plain_answer(question, emit)

    def run_strategy(market):
        from polyagents.orchestration import run_strategy as _rs
        bb = _rs(market, graph=eng, config=eng.config, strategy="full")
        return bb.risk

    return [
        data_capability(fetch),
        backtest_capability(backtest),
        scan_capability(scan),
        batch_collect_capability(batch_collect),
        batch_backtest_capability(batch_backtest),
        backtest_strategies_capability(backtest_strategies),
        backtest_matrix_capability(backtest_matrix),
        promotion_gate_capability(promotion_gate),
        crypto_arb_capability(find_crypto_arb),
        hunt_alpha_capability(hunt_alpha),
        scan_opportunities_capability(scan_opportunities),
        backfill_outcomes_capability(backfill_outcomes),
        lab_backtest_capability(lab_backtest),
        evaluate_skill_capability(evaluate_skill),
        portfolio_review_capability(portfolio_review),
        paper_trade_capability(paper_trade),
        settle_and_reflect_capability(settle_and_reflect),
        news_sentiment_capability(news_sentiment),
        microstructure_scan_capability(microstructure_scan),
        resolve_market_capability(resolve),
        analyze_market_capability(analyze_market),
        discover_markets_capability(discover),
        recommend_markets_capability(recommend),
        answer_capability(answer, stream_fn=answer_stream),
        domain_capability(domain_answer, stream_fn=domain_stream),
        strategy_capability(run_strategy),
    ]
