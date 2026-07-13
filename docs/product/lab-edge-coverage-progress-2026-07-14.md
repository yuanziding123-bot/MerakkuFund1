# Lab Edge Coverage Progress Report

> Date: 2026-07-14  
> Branch target: `codex/lab-edge-coverage-snapshots`  
> Scope: Polymarket-aligned collection coverage, edge explainability, news cache, multi-snapshot replay

## Brief Summary

本轮更新把 Lab 历史回测从“少量 binary midpoint 样本”推进到“Polymarket token-level、多 prediction_time、可解释 edge evidence”的版本。现在多 outcome/range 市场不再整体跳过，而是按每个 `clobTokenId` 拆成独立 settled collection；每个 token 默认生成 25% / 50% / 75% 三个 PIT snapshot。Report 同时区分 research edge 和 executable edge availability，避免把 `p_cal - p_market` 误说成真实可成交收益。

## What Changed

### 1. Polymarket Token-Level Ingestion

- 新增 outcome token expansion：
  - 一个 Polymarket market 可以生成多条 token-level collections。
  - 每条 collection 对齐 `condition_id`、`clobTokenId`、`outcome_label`、`outcome_index`。
  - `outcomePrices` 用作 final settled outcome source。
- 价格历史继续来自 CLOB `/prices-history`。
- 新 collection 会记录：
  - `polymarket_price_source = clob_prices_history`
  - `polymarket_outcome_source = gamma_outcomePrices`

### 2. Multi Prediction-Time Snapshots

- Ingestion 默认 `prediction_policy = multi`。
- 每个 token 默认生成三个 PIT snapshots：
  - 25% of available price history
  - 50% of available price history
  - 75% of available price history
- 每个 snapshot 都严格满足：
  - price candles before `prediction_time`
  - trades before `prediction_time`
  - news `available_at <= prediction_time`
  - `prediction_time < resolution_time`

### 3. News Query Cache

- 多 outcome 市场现在复用原始 Polymarket question 查询 historical news。
- 同一 ingestion run 内按 `query + start_date + end_date + max_results` 缓存 Tavily 结果。
- 精确 PIT 过滤仍在本地按 timestamp 执行。
- Ingestion stats 新增：
  - `news_cache_hits`
  - `news_cache_misses`

### 4. Edge Explainability

- EvaluationReport 每条 market sample 增加 `edge_evidence`：
  - `research_edge = p_cal - p_market`
  - `entry_price_proxy`
  - `executable_edge`
  - `executable_edge_available`
  - `polymarket_alignment`
  - `market_microstructure`
- 如果没有历史 best ask/orderbook，不会声称有 executable edge。
- 前端 report review 显示 research edge、exec edge 是否可用、price/outcome source、token match。

## Validation

真实小样本 ingestion：

```json
{
  "fetched_markets": 3,
  "inserted": 12,
  "duplicates": 6,
  "updated_duplicates": 6,
  "skipped_non_binary": 0,
  "news_items_used": 90,
  "news_cache_hits": 10,
  "news_cache_misses": 8
}
```

默认 DataStore snapshot：

```json
{
  "markets": 42,
  "collections": 54,
  "multi_snapshot_collections": 18,
  "unique_tokens": 42,
  "polymarket_price_source_aligned": 48
}
```

Regression tests:

```text
36 passed, 1 warning
```

## Message For Management

这次 Lab 的重点不是简单增加 UI，而是把回测样本和 Polymarket 的真实数据结构进一步对齐。之前很多 Polymarket closed markets 因为是多 outcome/range 结构会被跳过，现在系统会按每个 `clobTokenId` 拆成可回测的 token-level collection，并且每个 token 生成多个历史预测时点，显著提高样本覆盖。报告里也明确区分了 research edge 和可成交 edge：当前没有历史 orderbook/best ask 的地方不会被包装成真实可交易收益，而是诚实标注为 research probability gap。整体上，这让 Lab 更接近“系统化历史回放和证据报告”，而不是单次 demo 链路。

## Remaining Risks

- 多 snapshot 会提高样本量，但同一 market 的多个时间点不是完全独立样本；下一步需要 cluster-aware evaluation。
- executable edge 仍依赖历史 orderbook / best bid ask，目前只有 research edge 可以稳定展示。
- Tavily historical coverage 受新闻源质量影响，仍需要保留 skipped / unavailable 标记。

## Recommended Next Step

下一步建议做 cluster-aware EvaluationReport：

- 按 `condition_id` / market cluster 汇总样本。
- 同一 market 多 snapshot 可展示趋势，但统计显著性不能简单当作独立样本。
- Report 增加 cluster count、token count、snapshot count，帮助上级判断样本质量。
