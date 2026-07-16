# Lab Edge Coverage Progress Report

> Date: 2026-07-14  
> Branch target: `codex/lab-edge-coverage-snapshots`  
> Scope: Polymarket-aligned collection coverage, edge explainability, news cache, multi-snapshot replay, launch readiness UI

## Brief Summary

本轮更新把 Lab 历史回测从“少量 binary midpoint 样本”推进到“Polymarket token-level、多 prediction_time、可解释 edge evidence、上线前 readiness 可检查”的版本。现在多 outcome/range 市场不再整体跳过，而是按每个 `clobTokenId` 拆成独立 settled collection；每个 token 默认生成 25% / 50% / 75% 三个 PIT snapshot。Report 同时区分 research edge 和 executable edge availability，避免把 `p_cal - p_market` 误说成真实可成交收益。最新补充的上线前改动还增加了 cluster-aware sample structure、异步 ingestion job、system readiness 面板和 report review checklist，让 demo 时用户能按顺序检查数据、运行 backtest、解释结果。

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

### 5. Launch Readiness And Report Review

- EvaluationReport 增加 `sample_structure`：
  - `sample_count`
  - `token_count`
  - `condition_cluster_count`
  - `max_snapshots_per_cluster`
  - `cluster_adjusted_sample_adequate`
- Report 的 `data_quality` 现在同步记录 condition cluster 数量，避免把同一 market 的多 snapshot 误解释成完全独立样本。
- Data ingestion 新增 job/status API：
  - `POST /api/lab/data/ingest-jobs`
  - `GET /api/lab/data/ingest-jobs/{job_id}`
- 前端 ingestion 改为创建 job 后轮询状态，避免长任务让 Backtest 页看起来卡死。
- Lab system status 增加 readiness 信息：
  - DB backend / production readiness
  - Tavily news key 是否配置
  - live execution 是否开启
  - audit 是否启用
- Backtest 页面新增 `Launch readiness · system checks` 面板。
- Report review 新增摘要卡和 checklist：
  - data source
  - sample independence
  - PIT integrity
  - promotion gate
- Monitor 空结果增加解释：`no opportunity` 是有效扫描结果，不代表 backtest 失败。

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
37 passed, 1 warning
```

## Message For Management

这次 Lab 的重点不是简单增加 UI，而是把回测样本和 Polymarket 的真实数据结构进一步对齐，并补齐上线前解释和检查能力。之前很多 Polymarket closed markets 因为是多 outcome/range 结构会被跳过，现在系统会按每个 `clobTokenId` 拆成可回测的 token-level collection，并且每个 token 生成多个历史预测时点，显著提高样本覆盖。报告里也明确区分了 research edge 和可成交 edge：当前没有历史 orderbook/best ask 的地方不会被包装成真实可交易收益，而是诚实标注为 research probability gap。最新版本还增加了 cluster-aware sample structure、异步 ingestion job 和 readiness 面板，demo 时可以清楚说明数据是否来自真实 collections、样本是否独立、PIT 是否干净，以及为什么当前结果是否能进入 promotion。

## Remaining Risks

- 多 snapshot 会提高样本量，但同一 market 的多个时间点不是完全独立样本；现在已展示 condition cluster 数量，后续还需要 cluster-level bootstrap / confidence interval。
- executable edge 仍依赖历史 orderbook / best bid ask，目前只有 research edge 可以稳定展示。
- Tavily historical coverage 受新闻源质量影响，仍需要保留 skipped / unavailable 标记。
- 当前 readiness job 状态保存在 web 进程内存中，适合 demo / single-process；生产环境建议落 DB 或任务队列。

## Recommended Next Step

下一步建议进入中优先级的第二批产品化工作：

- Report 增加更直观的 calibration / reliability 图形展示。
- Monitor 增加 opportunity reason breakdown 和策略对比。
- System readiness 从只读状态扩展到 demo checklist。
- Ingestion job 状态从内存迁移到持久化表或轻量 task store。
