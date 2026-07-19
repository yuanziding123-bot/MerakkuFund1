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
                           hedge_scan_capability,
                           log_prediction_capability, market_radar_capability,
                           news_to_markets_capability,
                           plot_market_capability, prediction_journal_capability,
                           relational_alpha_capability,
                           research_alpha_capability, scan_conditional_arb_capability,
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

    # ----- pack: alpha-research (event-relatedness engine + strategy review) -----

    def _recent_change(token_id, bars=24):
        """Recent price move for a token (last close vs ~`bars` bars ago)."""
        c = eng.client.fetch_price_history(token_id, interval="max") or []
        if len(c) < 2:
            return 0.0
        ref = c[-min(bars, len(c))].close
        return round(float(c[-1].close) - float(ref), 4)

    def _winner_set(query, scan_limit=90):
        """Resolve the target and find its mutually-exclusive winner set — the YES side
        of every 'Will <X> win the <same event>?' market (e.g. all WC champions)."""
        tgt = resolve(query)
        if tgt.get("error"):
            return None, tgt
        tq = str(tgt.get("question", "")).lower()
        if " win " not in tq:                               # not a 'win the X' target
            return {"target": tgt, "event": None, "siblings": []}, None
        event_key = tq.split(" win ", 1)[1].strip(" ?.")    # e.g. "the 2026 fifa world cup"
        rows = mcp_server.scan_markets(limit=scan_limit, min_volume_24h=0.0)
        sibs, seen = [], set()
        for r in rows:
            q = str(r.get("question", "")).lower()
            if r.get("outcome") != "YES" or " win " not in q or event_key not in q:
                continue
            if r.get("question") in seen:
                continue
            seen.add(r.get("question"))
            sibs.append({"question": r.get("question"), "token_id": r.get("token_id"),
                         "price": float(r.get("price") or 0.0)})
        return {"target": tgt, "event": event_key, "siblings": sibs}, None

    # stage keywords → level; a STRONGER claim (higher level) logically implies the weaker
    # ones, so its probability must be ≤ theirs (win ⊆ reach final ⊆ reach semi ⊆ advance).
    _STAGE_KW = [
        (("win the", "wins the", "champion", "to win", "winner of"), 4),
        (("reach the final", "make the final", "in the final", "the final", "final"), 3),
        (("semifinal", "semi-final", "semi final"), 2),
        (("quarterfinal", "quarter-final", "quarter final"), 1),
        # advance-this-round / single-match: the weakest (nearest) claim in the chain
        (("advance", "group stage", "qualify", "round of", "knockout",
          "beat", "defeat", " vs ", " vs.", "to advance"), 0),
    ]

    def _stage_level(q):
        ql = str(q).lower()
        for kws, lvl in _STAGE_KW:
            if any(k in ql for k in kws):
                return lvl
        return None

    def _classify_strategy(query):
        """Route the user's intent to a strategy mode → which relations/signals matter."""
        q = (query or "").lower()
        if any(w in q for w in ("套利", "arbitrage", "arb", "无风险", "risk-free", "risk free",
                                "mispric", "inconsist", "平价", "两腿", "multi-leg")):
            return "arb"
        if any(w in q for w in ("短线", "日内", "short-term", "short term", "intraday", "快进",
                                "scalp", "momentum", "快速", "波段")):
            return "short"
        if any(w in q for w in ("hold", "持有", "长期", "long-term", "long term", "到期", "持仓")):
            return "hold"
        return "general"

    def _entity_implication(target_question):
        """Same-entity cluster + logical-implication check: find the target entity's other
        markets, order them by stage, and flag where a STRONGER claim is priced above a
        weaker one (P(win) > P(reach final) is a risk-free inconsistency)."""
        m = re.match(r"will\s+(.+?)\s+(win|reach|advance|make|beat|qualify|to win)",
                     str(target_question or "").lower())
        ent = m.group(1).strip() if m else None
        if not ent or len(ent) < 2:
            return {"entity": None, "cluster": [], "chain": [], "violations": [], "path": None}
        rows = mcp_server.scan_markets(limit=100, min_volume_24h=0.0)
        cluster, seen = [], set()
        for r in rows:
            if r.get("outcome") != "YES":
                continue
            q = str(r.get("question", ""))
            if ent in q.lower() and q not in seen:
                seen.add(q)
                cluster.append({"question": q, "price": round(float(r.get("price") or 0), 4),
                                "level": _stage_level(q)})
        staged = sorted([c for c in cluster if c["level"] is not None], key=lambda c: -c["level"])
        violations = []
        for i in range(len(staged) - 1):
            hi, lo = staged[i], staged[i + 1]
            if hi["level"] > lo["level"] and hi["price"] > lo["price"] + 0.01:
                violations.append({"stronger": hi["question"], "p_strong": hi["price"],
                                   "weaker": lo["question"], "p_weak": lo["price"],
                                   "gap": round(hi["price"] - lo["price"], 4)})
        path = None                                         # path decomposition: win vs reach-final
        win_m = next((c for c in staged if c["level"] == 4), None)
        fin_m = next((c for c in staged if c["level"] == 3), None)
        if win_m and fin_m and fin_m["price"] > 0:
            path = {"reach_final": fin_m["price"], "win": win_m["price"],
                    "implied_p_win_given_final": round(win_m["price"] / fin_m["price"], 4)}
        return {"entity": ent, "cluster": cluster, "chain": staged,
                "violations": violations, "path": path}

    def _winner_set_backtest(predict_frac=0.5, normalize=True, max_events=15):
        """Replay the relational estimate over RESOLVED winner-sets: at a point in time,
        does the field-implied (vig-free) fair probability beat the raw market price at
        calling the eventual winner? Returns per-member (market, model, outcome) records."""
        from polyagents.lab.backtest import BacktestRunner
        runner = BacktestRunner(client=eng.client, store=getattr(eng, "store", None))
        raw = eng.client.list_resolved_markets(limit=500)
        yes = [m for m in eng.client.to_markets(raw) if m.outcome == "YES"]
        groups: dict = {}
        for m in yes:
            q = (m.question or "").lower()
            key = q.split(" win ", 1)[1].strip(" ?.") if " win " in q else None
            # only a real competition suffix — reject date/scoreline groupings ("on 2026-..","2-0")
            if key and not key.startswith("on ") and not re.match(r"^[\d\- ]+$", key):
                groups.setdefault(key, []).append(m)
        records, n_events = [], 0
        for members in groups.values():
            # a genuine mutually-exclusive winner-set has EXACTLY ONE resolved winner
            if len(members) < 4 or sum(1 for m in members if m.price >= 0.5) != 1:
                continue
            pit = {}                                        # token -> (PIT price, resolved outcome)
            for m in members:
                candles = runner.candles_for(m)
                if len(candles) < 5:
                    continue
                idx = min(max(int(predict_frac * len(candles)), 4), len(candles) - 1)
                pit[m.token_id] = (float(candles[idx].close), 1.0 if m.price >= 0.5 else 0.0)
            if len(pit) < 3:
                continue
            field_sum = sum(p for p, _ in pit.values())
            if field_sum <= 0:
                continue
            n_events += 1
            for price, won in pit.values():
                fair = price / field_sum if normalize else price
                records.append({"market_price": price, "p_model": max(0.01, min(0.99, fair)), "won": won})
            if n_events >= max_events:
                break
        return records, n_events

    def _brier_delta(records):
        """Mean Brier(market) − Brier(model): positive = model beats the raw market."""
        if not records:
            return None
        bm = sum((r["market_price"] - r["won"]) ** 2 for r in records) / len(records)
        bd = sum((r["p_model"] - r["won"]) ** 2 for r in records) / len(records)
        return round(bm - bd, 5)

    def relational_backtest(query=None):
        """Validate the relational estimate on history + self-test variants (which config
        beats the market most). This is the evidence behind 'does the signal have alpha'."""
        variants = [
            {"name": "field-normalized @50%", "normalize": True, "predict_frac": 0.5},
            {"name": "raw market @50% (baseline)", "normalize": False, "predict_frac": 0.5},
            {"name": "field-normalized @40%", "normalize": True, "predict_frac": 0.4},
            {"name": "field-normalized @60%", "normalize": True, "predict_frac": 0.6},
        ]
        results, n_events, n_records = [], 0, 0
        for v in variants:
            recs, ne = _winner_set_backtest(predict_frac=v["predict_frac"], normalize=v["normalize"])
            n_events = max(n_events, ne)
            n_records = max(n_records, len(recs))
            results.append({"name": v["name"], "n": len(recs), "brier_delta": _brier_delta(recs),
                            "beats_market": (_brier_delta(recs) or 0) > 0})
        ranked = sorted([r for r in results if r["brier_delta"] is not None],
                        key=lambda r: r["brier_delta"], reverse=True)
        if n_events == 0:
            note = ("目前**没有已结算的互斥冠军集**可回测(真冠军集需恰好一个赢家;Polymarket 历史里极少,"
                    "唯一活跃的 2026 世界杯尚未结算)。历史回放这条路暂时喂不饱——正确做法是**前向追踪**:"
                    "把每次算出的 fair_prob 落库,等市场结算后打分(Tier2 预测追踪)。")
        elif n_events < 5:
            note = f"样本偏少(仅 {n_events} 个已结算冠军集),结论仅供参考;随赛事结算/预测追踪累积会变准。"
        else:
            note = "样本充足。"
        return {"query": query, "n_events": n_events, "n_records": n_records,
                "variants": results, "best": ranked[0] if ranked else None, "note": note}

    def relational_alpha(query, top_k=8):
        """Event-relatedness engine across relation types + strategy mode: winner-set
        consistency + redistribution + lag + what-if, PLUS same-entity cluster and
        logical-implication (A⊆B) arbitrage. Deterministic, from live prices + candles."""
        strategy_mode = _classify_strategy(query)
        ws, err = _winner_set(query)
        if err:
            return {"query": query, "strategy_mode": strategy_mode, "error": err["error"]}
        tgt, sibs, event = ws["target"], ws["siblings"], ws["event"]
        impl = _entity_implication(tgt.get("question"))     # same-entity cluster + implication
        implication = {"entity": impl["entity"], "chain": impl["chain"],
                       "violations": impl["violations"], "path": impl["path"]}
        tgt_sib = next((s for s in sibs if s["question"] == tgt.get("question")), None)
        if event is None or tgt_sib is None or len(sibs) < 3:
            return {"query": query, "strategy_mode": strategy_mode, "target": tgt, "event": event,
                    "siblings_n": len(sibs), "implication": implication,
                    "note": ("未找到清晰的互斥冠军集(目标非 'win the X' 型),但已按同实体簇/逻辑蕴含分析。"
                             if impl["chain"] else
                             "未找到清晰的互斥冠军集,也没有可用的同实体关联市场,关联推理不适用。")}
        tgt_price = tgt_sib["price"]
        field_sum = sum(s["price"] for s in sibs)
        rest = field_sum - tgt_price
        w = (tgt_price / rest) if rest > 0 else 0.0         # target's share of "the rest of the field"

        rivals = sorted((s for s in sibs if s["token_id"] != tgt_sib["token_id"]),
                        key=lambda s: -s["price"])[:top_k]
        tgt_delta = _recent_change(tgt_sib["token_id"])
        rival_moves, released = [], 0.0
        for r in rivals:
            d = _recent_change(r["token_id"])
            rival_moves.append({**r, "delta": d})
            if d < 0:
                released += -d                              # probability "released" by a rival crashing
        implied_rise = round(w * released, 4)              # what the target SHOULD have gained
        lag_gap = round(implied_rise - max(0.0, tgt_delta), 4)   # …minus what it actually gained
        signal = "buy" if lag_gap > 0.01 else ("watch" if lag_gap > 0.003 else "none")

        # ---- fair-probability synthesizer (structural): field-implied + lag correction ----
        p_field = tgt_price / field_sum if field_sum else tgt_price     # vig-free field consensus
        lag_adj = round(0.5 * lag_gap, 4)                               # half the un-repriced field move
        fair_p = round(max(0.01, min(0.99, p_field + lag_adj)), 4)
        edge_structural = round(fair_p - tgt_price, 4)

        whatif = []
        for r in rivals[:5]:
            newrest = field_sum - r["price"]
            tgt_new = tgt_price + r["price"] * (tgt_price / newrest) if newrest > 0 else tgt_price
            whatif.append({"question": r["question"], "rival_price": round(r["price"], 4),
                           "target_fair_if_out": round(tgt_new, 4),
                           "delta": round(tgt_new - tgt_price, 4)})
        return {"query": query, "strategy_mode": strategy_mode, "implication": implication,
                "target": {**tgt, "price": round(tgt_price, 4),
                           "fair_share": round(tgt_price / field_sum, 4) if field_sum else 0},
                "event": event, "n_field": len(sibs), "field_sum": round(field_sum, 3),
                "consistency": ("overround(市场加价)" if field_sum > 1.03
                                else "underround(反常低估)" if field_sum < 0.97 else "tight(接近无套利)"),
                "target_recent_delta": tgt_delta, "field_released": round(released, 4),
                "implied_target_rise": implied_rise, "lag_gap": lag_gap, "signal": signal,
                "fair_prob": fair_p, "edge_vs_market": edge_structural,
                "prob_sources": {"field_implied": round(p_field, 4), "lag_adj": lag_adj},
                "top_rivals": rival_moves[:6], "what_if": whatif}

    def research_alpha(query):
        """Strategy review: run the relational engine + news, then have the LLM judge
        whether the user's thesis has alpha and propose concrete improvements — grounded
        strictly in the computed numbers (no fabricated data)."""
        import json as _json
        rel = relational_alpha(query)
        news = news_sentiment(query)
        bt = relational_backtest(query)                     # historical validation + variant self-test
        news_sig = news.get("signal") if isinstance(news, dict) else None
        news_mean = news.get("mean_sentiment") if isinstance(news, dict) else None

        # ---- synthesize ONE fair probability: structural (field+lag) + news adjustment ----
        synth = None
        if not rel.get("error") and rel.get("fair_prob") is not None:
            news_adj = round(max(-0.03, min(0.03, (news_mean or 0.0) * 0.03)), 4)
            base = rel["fair_prob"]                              # field-implied + lag
            fair = round(max(0.01, min(0.99, base + news_adj)), 4)
            market = (rel.get("target") or {}).get("price") or 0.0
            tight = 0.97 <= (rel.get("field_sum") or 0) <= 1.03
            conf = ("高" if tight and (rel.get("n_field") or 0) >= 4 and news_mean is not None
                    else "中" if tight else "低")
            synth = {"fair_prob": fair, "market_price": market,
                     "edge_vs_market": round(fair - market, 4), "confidence": conf,
                     "sources": {**rel.get("prob_sources", {}), "news_adj": news_adj}}
        evidence = _json.dumps({"synthesized_fair_prob": synth,
                                "historical_backtest": {"n_events": bt.get("n_events"),
                                                        "best": bt.get("best"), "variants": bt.get("variants"),
                                                        "note": bt.get("note")},
                                "relational": rel, "news_signal": news_sig, "news_mean": news_mean},
                               ensure_ascii=False, default=str)[:2900]
        review = None
        try:
            mode = (rel.get("strategy_mode") if isinstance(rel, dict) else None) or "general"
            mode_hint = {
                "hold": "This is a HOLD/long-term thesis: emphasize structural fair value (winner-set "
                        "consistency + logical-implication bounds) and whether the mispricing should "
                        "converge by resolution.",
                "short": "This is a SHORT-TERM thesis: emphasize the lag signal, recent related-market "
                         "moves, and news — repricing speed matters more than structural value.",
                "arb": "This is an ARBITRAGE thesis: emphasize logical-implication violations "
                       "(P(stronger)>P(weaker)), winner-set Σ≠1, and any risk-free multi-leg. If no "
                       "inconsistency is found, say so plainly.",
                "general": "Weigh structural value, lag, and any inconsistency together.",
            }[mode]
            sys = ("You are a prediction-market quant reviewer. The user proposes a trading "
                   f"thesis/strategy. Strategy mode = {mode}. {mode_hint} Using ONLY the computed "
                   "evidence (synthesized fair prob, historical backtest w/ variant self-test, "
                   "winner-set analysis, same-entity implication chain + violations, news), judge "
                   "whether the thesis has alpha and propose 2-3 CONCRETE improvements tailored to the "
                   "strategy mode. Anchor on synthesized_fair_prob vs market AND the backtest; for arb, "
                   "cite implication violations. Prefer the best self-test variant for improvements. "
                   "Cite real numbers; never invent data; if evidence is thin, say so. Answer in the "
                   "user's language, <190 words, as: 1) 复述策略(含策略类型) 2) alpha 判定(据数) 3) 改进建议.")
            user = f"User thesis / request:\n{query}\n\nComputed evidence (JSON):\n{evidence}"
            resp = eng._get_llm().invoke([("system", sys), ("user", user)])
            text = getattr(resp, "content", resp)
            if isinstance(text, list):
                text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
            review = str(text).strip()
        except Exception as exc:
            review = f"(评审生成失败:{type(exc).__name__};以下为可计算的关联证据。)"
        return {"query": query, "synth": synth, "backtest": bt, "relational": rel,
                "news_signal": news_sig, "review": review}

    # ----- pack: conditional-arb (cross-market conditional / implication arb scanner) --

    def scan_conditional_arb(query=None, max_show=10):
        """Scan the market for CONDITIONAL cross-market structures: an entity with a
        championship market linked to lower-stage (reach-final / advance / single-match)
        markets. Report the implied P(champ|advance)=P(champ)/P(advance), flag GENUINE
        logical-implication arbitrage (a stronger claim priced above a weaker one — that
        is risk-free & bounded), and separate it from directional conditional value."""
        rows = mcp_server.scan_markets(limit=500, min_volume_24h=0.0)   # wide net: stage markets rank lower
        ents: dict = {}
        for r in rows:
            if r.get("outcome") != "YES":
                continue
            q = str(r.get("question", ""))
            m = re.match(r"will\s+(.+?)\s+(win|reach|advance|make|beat|defeat|qualify|to win|to advance)",
                         q.lower())
            if not m:
                continue
            ent, lvl = m.group(1).strip(), _stage_level(q)
            if lvl is None or len(ent) < 2:
                continue
            ents.setdefault(ent, []).append({"question": q, "price": round(float(r.get("price") or 0), 4),
                                              "level": lvl})
        chains = []
        for ent, mk in ents.items():
            staged, seen = [], set()
            for c in sorted(mk, key=lambda c: -c["level"]):          # keep one market per stage level
                if c["level"] not in seen:
                    seen.add(c["level"]); staged.append(c)
            if len({c["level"] for c in staged}) < 2:                # need ≥2 stages for a conditional
                continue
            violations = []                                          # genuine implication arb (risk-free)
            for i in range(len(staged) - 1):
                hi, lo = staged[i], staged[i + 1]
                if hi["price"] > lo["price"] + 0.01:
                    violations.append({"stronger": hi["question"], "p_strong": hi["price"],
                                       "weaker": lo["question"], "p_weak": lo["price"],
                                       "gap": round(hi["price"] - lo["price"], 4)})
            champ, nxt = staged[0], staged[1]
            cond = round(champ["price"] / nxt["price"], 4) if nxt["price"] > 0 else None
            chains.append({"entity": ent, "chain": staged, "violations": violations,
                           "champ_q": champ["question"], "p_champ": champ["price"],
                           "advance_q": nxt["question"], "p_advance": nxt["price"],
                           "cond_champ_given_advance": cond, "has_arb": bool(violations)})
        chains.sort(key=lambda c: (c["has_arb"], max((v["gap"] for v in c["violations"]), default=0.0)),
                    reverse=True)
        return {"query": query, "n_entities": len(ents), "n_chains": len(chains),
                "n_true_arb": sum(1 for c in chains if c["has_arb"]), "chains": chains[:max_show]}

    # ----- pack: market-radar ("what changed today" — movers / near-resolution / fresh) --

    def _radar_move(token_id, bars=24):
        """Recent price move + history length (age proxy) for a market token."""
        c = eng.client.fetch_price_history(token_id, interval="max") or []
        if len(c) < 2:
            return {"change": 0.0, "n": len(c), "last": (c[-1].close if c else None)}
        ref = c[-min(bars, len(c))].close
        return {"change": round(float(c[-1].close) - float(ref), 4), "n": len(c),
                "last": round(float(c[-1].close), 4)}

    _WIN_ON_RE = re.compile(r"will\s+(.+?)\s+win\s+on\s+([\d-]+)", re.I)
    _SCORE_RE = re.compile(r"exact score:\s*(.+?)\s+(\d+)\s*-\s*(\d+)\s+(.+?)\?*$", re.I)

    def _match_consistency(rows):
        """Computable structural checks on match markets: winner Σ + implied draw/other,
        and the (partial) exact-score Σ. Σ>1 = genuine overround (sell all → arb)."""
        winners, matches = {}, {}
        for r in rows:
            if r.get("outcome") != "YES":
                continue
            q = str(r.get("question", "")); p = float(r.get("price") or 0.0)
            mw = _WIN_ON_RE.search(q)
            if mw:
                winners[mw.group(1).strip().lower()] = {"team": mw.group(1).strip(), "price": round(p, 4)}
                continue
            ms = _SCORE_RE.search(q)
            if ms:
                a, sa, sb, b = ms.group(1).strip(), int(ms.group(2)), int(ms.group(3)), ms.group(4).strip()
                key = frozenset((a.lower(), b.lower()))
                matches.setdefault(key, {"teams": (a, b), "scores": []})["scores"].append(
                    {"a": sa, "b": sb, "price": round(p, 4)})
        out = []
        for mt in matches.values():
            a, b = mt["teams"]
            wa, wb = winners.get(a.lower()), winners.get(b.lower())
            row = {"match": f"{a} vs {b}", "n_scores": len(mt["scores"]),
                   "exact_sum_partial": round(sum(s["price"] for s in mt["scores"]), 4)}
            row["exact_overround"] = row["exact_sum_partial"] > 1.01     # subset already >1 → sell all
            if wa and wb:
                pa, pb = wa["price"], wb["price"]
                row.update({"p_a": pa, "p_b": pb, "winner_sum": round(pa + pb, 4),
                            "implied_draw_other": round(1 - pa - pb, 4),
                            "winner_overround": (pa + pb) > 1.01})       # sell both winners → arb
            out.append(row)
        out.sort(key=lambda r: (r.get("exact_overround") or r.get("winner_overround", False),
                                r.get("n_scores", 0)), reverse=True)
        return out

    def market_radar(query=None, scan=100, deep=28, expiry_days=5.0):
        """Surface leads for a human to dig into: biggest recent movers, markets near
        resolution, and short-history (possibly newly-listed / thin) markets. Computed
        from live prices + candle history; no verdicts, just where to look."""
        rows = mcp_server.scan_markets(limit=scan, min_volume_24h=1000.0)
        seen, mkts = set(), []
        for r in rows:                                      # dedup by market, keep the YES side
            cid = r.get("condition_id")
            if r.get("outcome") != "YES" or cid in seen:
                continue
            seen.add(cid); mkts.append(r)
        near = sorted([m for m in mkts if 0 < float(m.get("days_to_expiry") or 999) <= expiry_days],
                      key=lambda m: float(m.get("days_to_expiry") or 999))[:8]
        near_out = [{"question": m.get("question"), "price": round(float(m.get("price") or 0), 4),
                     "days": round(float(m.get("days_to_expiry") or 0), 1),
                     "liquidity": round(float(m.get("liquidity") or 0)),
                     "volume_24h": round(float(m.get("volume_24h") or 0))} for m in near]
        scored = []                                         # movers + freshness need candle history
        for m in mkts[:deep]:
            mv = _radar_move(m.get("token_id"))
            scored.append({"question": m.get("question"), "price": round(float(m.get("price") or 0), 4),
                           "change": mv["change"], "n_candles": mv["n"],
                           "volume_24h": round(float(m.get("volume_24h") or 0)),
                           "days": round(float(m.get("days_to_expiry") or 0), 1)})
        # independent copies so a market appearing in both lists gets its own section 'why'
        movers = [dict(s) for s in sorted([s for s in scored if s["n_candles"] >= 2],
                                          key=lambda s: -abs(s["change"]))[:8]]
        fresh = [dict(s) for s in sorted([s for s in scored if 2 <= s["n_candles"] <= 30],
                                         key=lambda s: s["n_candles"])[:6]]

        for m in movers:                                    # deterministic 'why it's worth watching'
            ch = m["change"]
            mag = "大幅" if abs(ch) >= 0.05 else "中幅" if abs(ch) >= 0.02 else "小幅"
            m["why"] = f"近期{mag}{'上涨' if ch > 0 else '下跌'} {ch:+.1%},有新信息在推价 → 查催化剂 / 是否过度反应"
        for m in near_out:
            liq = m.get("liquidity") or 0
            m["why"] = (f"{m.get('days')}天到期,单一事件即定生死;流动性 {liq:,}"
                        + ("(充足)" if liq > 500000 else "(偏薄,注意滑点)"))
        for m in fresh:
            m["why"] = f"仅 {m.get('n_candles')} 个历史点,可能新挂/低活跃 → 定价未必充分,先建立观点"

        structural = _match_consistency(rows)               # computable match Σ / draw / exact-score checks

        insight = None                                      # LLM angle + arbitrage suggestions (grounded)
        try:
            import json as _json
            board = _json.dumps({"movers": movers, "near_resolution": near_out, "fresh": fresh,
                                 "structural_checks": structural[:6]},
                                ensure_ascii=False, default=str)[:2600]
            sys = ("You are a prediction-market scout. Given this radar board (movers / "
                   "near-resolution / fresh + COMPUTED structural_checks with real numbers), for the "
                   "3-4 MOST interesting leads say in one line WHY it's worth attention + 1-2 concrete "
                   "ANGLES to verify. USE the structural_checks numbers: winner_sum (P(A)+P(B)) and "
                   "implied_draw_other, exact_sum_partial. If winner_overround or exact_overround is "
                   "true, that IS a genuine sell-all arbitrage — state it with the numbers. Otherwise "
                   "frame cross-market checks as '值得查'. Only reference markets/prices in the board; "
                   "never invent numbers. Answer in the user's language, <180 words, concise bullets.")
            resp = eng._get_llm().invoke([("system", sys), ("user", f"Radar board:\n{board}")])
            text = getattr(resp, "content", resp)
            if isinstance(text, list):
                text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
            insight = str(text).strip()
        except Exception as exc:
            insight = f"(角度生成失败:{type(exc).__name__};以下为线索表。)"

        return {"query": query, "n_scanned": len(mkts), "n_deep": min(deep, len(mkts)),
                "movers": movers, "near_resolution": near_out, "fresh": fresh,
                "structural": structural, "insight": insight}

    # ----- pack: range-hedge (measure swing + lockable hedge profit) ------------

    def hedge_scan(query):
        """Resolve a market, pull its full price series, and measure the peak-trough swing
        + the max profit lockable by legging into both sides over time (buy YES on a dip,
        equal NO on a later pop → cost<$1, pays $1 either way)."""
        ref = resolve(query)
        if ref.get("error"):
            return {"query": query, "error": ref["error"]}
        candles = eng.client.fetch_price_history(ref.get("token_id", ""), interval="max")
        ps = [c.close for c in candles if c.close is not None]
        if len(ps) < 3:
            return {"query": query, "market": ref.get("question"), "matched_by": ref.get("matched_by"),
                    "n": len(ps), "note": "价格点不足,无法评估摆幅(市场太新/太冷或无历史)。"}
        lo, hi = min(ps), max(ps)
        run_min, lock = ps[0], 0.0                          # buy YES at running-min, hedge at a later pop
        for p in ps[1:]:
            run_min = min(run_min, p); lock = max(lock, p - run_min)
        run_max, lock_rev = ps[0], 0.0                      # or buy NO at a peak, hedge on a later dip
        for p in ps[1:]:
            run_max = max(run_max, p); lock_rev = max(lock_rev, run_max - p)
        best = round(max(lock, lock_rev), 4)
        verdict = ("波动大,适合区间对冲锁利" if best >= 0.10
                   else "摆动一般,能锁但薄" if best >= 0.04 else "太平,难锁利")
        return {"query": query, "market": ref.get("question"), "matched_by": ref.get("matched_by"),
                "n": len(ps), "current": round(ps[-1], 4), "low": round(lo, 4), "high": round(hi, 4),
                "range": round(hi - lo, 4), "lockable": best, "verdict": verdict}

    # ----- pack: prediction-journal (log your own P, score it after resolution) --

    def log_prediction(query):
        """Record the user's OWN subjective probability for a market (+ the market price
        now). Parse the % / decimal from the request, resolve the market, persist to the
        shared journal (cloud DB when configured)."""
        q = query or ""
        pm = re.search(r"(\d+(?:\.\d+)?)\s*%", q)
        if pm:
            user_p = float(pm.group(1)) / 100.0
        else:
            dm = re.search(r"\b0?\.\d+\b", q)
            user_p = float(dm.group(0)) if dm else None
        if user_p is None:
            return {"error": "没解析到你的概率——请说明,例如「记录我对法国夺冠的预测:30%」或「…0.30」。"}
        user_p = max(0.01, min(0.99, user_p))
        clean = re.sub(r"\d+(?:\.\d+)?\s*%|\b0?\.\d+\b", " ", q)      # drop the probability
        clean = re.sub(r"记录|我对|的预测|我觉得|我认为|我预测|概率(是|为|大概)?|预测|押注?|大概|"
                       r"log( my)?|prediction|probability|estimate|[:：]", " ", clean, flags=re.I)
        ref = resolve(clean.strip() or q)                             # resolve the market name only
        if ref.get("error"):
            return {"error": ref["error"]}
        if ref.get("matched_by") == "fallback":                       # don't log a wrong market
            return {"error": f"没准确匹配到你说的标的(退回到「{ref.get('question')}」),没记录。"
                             f"请用更贴近市场原名的说法,如「记录我对 France win World Cup 的预测:30%」。"}
        mk = mcp_server._get_market(ref.get("token_id", ""))
        market_p = float(mk.price) if mk else float(ref.get("price") or 0.0)
        try:
            from polyagents.storage.predictions import PredictionStore
            store = PredictionStore()
            store.log(token_id=ref["token_id"], condition_id=(mk.condition_id if mk else ""),
                      question=ref["question"], category=categorize(ref["question"]),
                      user_p=user_p, market_p=market_p, note=q[:140])
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"logged": {"question": ref["question"], "user_p": round(user_p, 4),
                           "market_p": round(market_p, 4), "edge_vs_market": round(user_p - market_p, 4),
                           "matched_by": ref.get("matched_by")}}

    def prediction_journal(query=None):
        """Show the journal: auto-settle any open calls whose market has resolved (Brier
        you vs market), list open calls with current edge, and aggregate where your
        subjective read beats the market (overall + by category)."""
        try:
            from polyagents.storage.predictions import PredictionStore
            store = PredictionStore()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        settled_now = 0
        for p in store.open():                              # auto-settle resolved markets
            cid = p.get("condition_id")
            raw = eng.client.fetch_market_by_condition(cid) if cid else None
            if not raw or not (raw.get("closed") or raw.get("archived")):
                continue
            yes = next((mm for mm in eng.client.to_markets([raw]) if mm.token_id == p["token_id"]), None)
            if yes is None:
                continue
            outcome = 1 if yes.price >= 0.5 else 0
            store.mark_resolved(p["id"], outcome, round((p["user_p"] - outcome) ** 2, 4),
                                round((p["market_p"] - outcome) ** 2, 4))
            settled_now += 1
        allp = store.all()
        openp = [{"question": p["question"], "user_p": p["user_p"], "market_p": p["market_p"],
                  "edge": round((p["user_p"] or 0) - (p["market_p"] or 0), 4),
                  "created_at": (p["created_at"] or "")[:10]} for p in allp if not p["resolved"]]
        resolved = [p for p in allp if p["resolved"]]
        agg, by_cat = None, []
        if resolved:
            mbu = sum(p["brier_user"] for p in resolved) / len(resolved)
            mbm = sum(p["brier_market"] for p in resolved) / len(resolved)
            hit = sum(1 for p in resolved if (p["user_p"] >= 0.5) == (p["outcome"] == 1)) / len(resolved)
            agg = {"n_resolved": len(resolved), "brier_user": round(mbu, 4), "brier_market": round(mbm, 4),
                   "brier_delta": round(mbm - mbu, 4), "beats_market": mbm > mbu, "hit_rate": round(hit, 3)}
            cats = {}
            for p in resolved:
                c = cats.setdefault(p["category"] or "other", {"n": 0, "bu": 0.0, "bm": 0.0})
                c["n"] += 1; c["bu"] += p["brier_user"]; c["bm"] += p["brier_market"]
            by_cat = sorted([{"category": k, "n": v["n"],
                              "brier_delta": round(v["bm"] / v["n"] - v["bu"] / v["n"], 4)}
                             for k, v in cats.items()], key=lambda x: -x["brier_delta"])
        return {"query": query, "settled_now": settled_now, "n_open": len(openp),
                "open": openp[:12], "aggregate": agg, "by_category": by_cat}

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

    def news_to_markets(query, top=8):
        """Reverse of news_sentiment: given a NEWS item, find which live Polymarket markets
        it affects and the likely direction. LLM entity-links the news → candidate markets
        (deterministic overlap) → LLM rates each market's direction. Event-driven scouting."""
        term_words = set()
        for t in _topic_terms(query):                       # LLM entities/keywords from the news
            term_words |= _words(t)
        rows = mcp_server.scan_markets(limit=120, min_volume_24h=0.0)
        scored, seen = [], set()
        for r in rows:
            if r.get("outcome") != "YES":
                continue
            cid = r.get("condition_id")
            hits = len(term_words & _words(r.get("question", "")))
            if hits <= 0 or cid in seen:
                continue
            seen.add(cid)
            scored.append({"question": r.get("question"), "price": round(float(r.get("price") or 0), 4),
                           "hits": hits})
        scored.sort(key=lambda s: -s["hits"])
        candidates = scored[:top]
        if not candidates:
            return {"query": query, "terms": sorted(term_words)[:10], "candidates": [],
                    "note": "没找到明显相关的活跃市场(新闻里的实体没匹配到标的)。"}
        analysis = None                                     # LLM direction per candidate (grounded)
        try:
            import json as _json
            cand = _json.dumps([{"q": c["question"], "price": c["price"]} for c in candidates],
                               ensure_ascii=False)
            sys = ("You map a NEWS item to the prediction markets it affects. Given the news and a "
                   "list of CANDIDATE markets (with current YES price), for each RELEVANT one give "
                   "the likely direction for YES — 📈 利好(涨) / 📉 利空(跌) / ❓不确定 — and a one-line "
                   "why tying the news to that market. Skip clearly irrelevant candidates. Use ONLY "
                   "the given markets; never invent markets or prices. These are hypotheses to verify, "
                   "not certainties. Answer in the user's language, concise bullets, <190 words.")
            resp = eng._get_llm().invoke([("system", sys), ("user", f"News:\n{query}\n\nCandidates:\n{cand}")])
            text = getattr(resp, "content", resp)
            if isinstance(text, list):
                text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
            analysis = str(text).strip()
        except Exception as exc:
            analysis = f"(方向分析失败:{type(exc).__name__};以下为匹配到的候选市场。)"
        return {"query": query, "terms": sorted(term_words)[:10],
                "candidates": candidates, "analysis": analysis}

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

    def _best_match(rows, words):
        """The scanned row whose English question shares the most query words."""
        best, best_hits = None, 0
        for row in rows:
            q = str(row.get("question", "")).lower()
            hits = sum(1 for w in words if w in q)
            if hits > best_hits:
                best, best_hits = row, hits
        return best, best_hits

    def resolve(query):
        """Pick ONE concrete market for the request: explicit token id, else best
        keyword match among live markets, else the most active."""
        q = (query or "").strip()
        m = mcp_server._get_market(q) if q else None      # exact token id?
        if m is None:
            rows = mcp_server.scan_markets(limit=120, min_volume_24h=0.0)   # wide net (WC dominates top)
            best, best_hits = _best_match(rows, {w for w in q.lower().split() if len(w) > 2})
            match_by = f"keywords({best_hits})"
            if best_hits < 2:                             # weak/no scan match → LLM-translate
                terms = {w for t in _topic_terms(q) for w in _words(t)}
                if terms:
                    b2, h2 = _best_match(rows, terms)     # re-match the scan with English terms first
                    if h2 >= 2:                           # scan has a solid match → keep it (avoid drift)
                        best, best_hits, match_by = b2, h2, f"keywords({h2})"
                    else:                                 # still weak → full-text search for niche markets
                        cands = list(rows)
                        for mm in eng.client.to_markets(eng.client.search_markets(" ".join(sorted(terms)), limit=25)):
                            if mm.outcome == "YES":
                                cands.append({"token_id": mm.token_id, "question": mm.question, "price": mm.price})
                        b3, h3 = _best_match(cands, terms)
                        if h3 > best_hits:
                            best, best_hits, match_by = b3, h3, f"search({h3})"
            if best is not None and best_hits >= 1:
                return {"token_id": best["token_id"], "question": best["question"],
                        "price": best["price"], "matched_by": match_by}
            m = eng.most_active_market()                   # nothing matched
            generic = (not q) or any(w in q.lower() for w in
                                     ("最活跃", "活跃", "most active", "liquid", "一个市场", "any market"))
            if m is not None and not generic:              # a specific target we couldn't find — be honest
                return {"token_id": m.token_id, "question": m.question, "price": m.price,
                        "matched_by": f"fallback", "unmatched": q[:40]}
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

    # ----- visualization: build a chart spec (rendered to SVG by the web layer) --

    def _price_series(token_id, label, cap=140):
        """A downsampled (ts, close) price series for one market token."""
        candles = eng.client.fetch_price_history(token_id, interval="max") or []
        pts = [[c.ts.isoformat(), round(float(c.close), 4)] for c in candles]
        if len(pts) > cap:                                  # even downsample to keep the SVG light
            step = len(pts) / cap
            pts = [pts[int(i * step)] for i in range(cap)]
        return {"label": label, "points": pts}

    def plot_market(query):
        """Pick chart type + target from the request and return a chart spec:
        line/area (one market's price trend), multi (compare several), or bar
        (snapshot of current prices)."""
        q = (query or "").lower()
        if any(w in q for w in ("对比", "比较", "compare", "versus", " vs ")):
            ctype = "multi"
        elif any(w in q for w in ("柱", "bar", "直方")):
            ctype = "bar"
        elif any(w in q for w in ("面积", "area")):
            ctype = "area"
        else:
            ctype = "line"

        if ctype in ("line", "area"):
            r = resolve(query)
            if r.get("error"):
                return {"type": ctype, "query": query, "error": r["error"], "series": []}
            s = _price_series(r["token_id"], r.get("question") or "market")
            if not s["points"]:
                return {"type": ctype, "query": query, "title": r.get("question"),
                        "series": [], "error": "该市场无价格历史"}
            return {"type": ctype, "query": query, "title": r.get("question"),
                    "y_label": "price", "series": [s]}

        cands = (discover(query).get("markets") or [])
        if ctype == "bar":
            bars = [{"label": (c.get("question") or "")[:22],
                     "value": round(float(c.get("price") or 0.0), 4)} for c in cands[:8]]
            return {"type": "bar", "query": query, "title": f"当前价格快照:{query}",
                    "y_label": "price", "bars": bars,
                    **({"error": "没找到相关市场"} if not bars else {})}
        # multi: compare several markets' price trends
        series = []
        for c in cands[:4]:
            if not c.get("token_id"):
                continue
            s = _price_series(c["token_id"], (c.get("question") or "")[:26])
            if s["points"]:
                series.append(s)
        return {"type": "multi", "query": query, "title": f"价格走势对比:{query}",
                "y_label": "price", "series": series,
                **({"error": "没找到可对比的市场"} if not series else {})}

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
        plot_market_capability(plot_market),
        relational_alpha_capability(relational_alpha),
        research_alpha_capability(research_alpha),
        scan_conditional_arb_capability(scan_conditional_arb),
        market_radar_capability(market_radar),
        hedge_scan_capability(hedge_scan),
        log_prediction_capability(log_prediction),
        prediction_journal_capability(prediction_journal),
        backfill_outcomes_capability(backfill_outcomes),
        lab_backtest_capability(lab_backtest),
        evaluate_skill_capability(evaluate_skill),
        portfolio_review_capability(portfolio_review),
        paper_trade_capability(paper_trade),
        settle_and_reflect_capability(settle_and_reflect),
        news_sentiment_capability(news_sentiment),
        news_to_markets_capability(news_to_markets),
        microstructure_scan_capability(microstructure_scan),
        resolve_market_capability(resolve),
        analyze_market_capability(analyze_market),
        discover_markets_capability(discover),
        recommend_markets_capability(recommend),
        answer_capability(answer, stream_fn=answer_stream),
        domain_capability(domain_answer, stream_fn=domain_stream),
        strategy_capability(run_strategy),
    ]
