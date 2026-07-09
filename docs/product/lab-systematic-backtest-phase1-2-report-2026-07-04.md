# Lab Systematic Backtest Phase 1-2 Report

> Date: 2026-07-04  
> Branch: `codex/lab-systematic-backtest`  
> Scope: Phase 1 Historical Collections Ingestion + Phase 1.5 Historical Trade Flow + Phase 2 Strategy-Aware Backtest

## Executive Summary

Lab 回测链路已经从 fixture/demo 数据推进到真实已结算 Polymarket 市场样本。当前系统可以自动拉取 resolved/closed markets，构造 Point-In-Time 安全的 historical collections，写入默认 `DataStore.collections`，并由 `BacktestRunner.run()` 直接消费生成 `Forecasts` 和 `EvaluationReport`。在此基础上，Phase 2 已支持同一批真实 collections 使用不同策略生成不同 EvaluationReport，包括 market baseline、linear factor model 和 momentum model。

当前默认 DataStore 已写入 46 条可用于回测的真实 settled collections。验证结果显示三种策略均可在真实 collections 上完成回测，且不再依赖 fixture fallback。

## Phase 1: Historical Collections Ingestion

### 已完成

- 新增 ingestion pipeline：
  - `polyagents/ingestion/polymarket_ingest.py`
  - `polyagents/ingestion/replay_builder.py`
  - `polyagents/ingestion/feature_builder.py`
- 从 Polymarket Gamma API 拉取 closed/resolved markets。
- 解析 binary Yes/No 市场。
- 提取 market metadata、condition id、YES token id、resolution/end date、outcome price。
- 调用 CLOB prices-history endpoint 获取历史价格序列。
- 选择 deterministic `prediction_time`：
  - MVP 使用价格序列 50% 位置。
  - 保证 `prediction_time < resolution_time`。
  - 少于 `min_history=4` 的样本跳过。
- 构造 PIT-safe collection：
  - 只使用 `prediction_time` 之前的价格数据。
  - 写入 `raw.features`、`raw.lab.outcome`、`raw.lab.available_at_max`、`raw.lab.ingestion_source`、`raw.lab.prediction_policy`、`raw.lab.resolution_time`。
- 使用现有 `(token_id, as_of)` 做去重。
- 新增 ingestion stats：
  - `inserted`
  - `duplicates`
  - `skipped_no_outcome`
  - `skipped_no_price_history`
  - `skipped_pit`
  - `skipped_non_binary`

### 默认 DataStore 运行结果

默认库写入结果：

```json
{
  "fetched_markets": 100,
  "inserted": 46,
  "duplicates": 0,
  "skipped_no_outcome": 0,
  "skipped_no_price_history": 0,
  "skipped_pit": 0,
  "skipped_non_binary": 54
}
```

默认 DataStore 当前状态：

```json
{
  "markets": 50,
  "candles": 171,
  "trades": 160355,
  "orderbook_snapshots": 4,
  "collections": 50
}
```

其中 46 条为可用于回测的真实 settled collections；另外 4 条为之前遗留的 unresolved/无 outcome 样本，`BacktestRunner` 会自动跳过。

## Phase 1.5: Historical Trade Flow Reconstruction

### 已完成

- 新增 historical `trades_flow` 重建：
  - 从 market condition 拉取历史 trades。
  - 只保留目标 YES token 的 trades。
  - 严格过滤 `trade.timestamp < prediction_time`。
  - 生成 `n_trades`、`n_buys`、`n_sells`、`buy_notional`、`sell_notional`、`flow_imbalance`。
- 将 historical trades 写入 `DataStore.trades`，支持复用和去重。
- 将 `flow_imbalance` 接入 feature vector。

### 暂缓内容

- 历史 orderbook 重建暂缓。

### Phase 1.6: PIT-Safe Historical News MVP

已补充 historical news ingestion MVP：

- 使用 news API 的历史日期窗口搜索。
- 每条新闻必须有可解析发布时间。
- 严格要求 conservative `available_at <= prediction_time`。
- 没有发布时间的新闻、发布时间晚于 prediction time 的新闻不会进入真实 backtest。
- 只有日期、没有具体时间的新闻按当天结束才可用，避免同日未来信息泄漏。
- `EvaluationReport.market_sample` 现在会展示 `news_evidence`：
  - 使用了几条 PIT-clean news。
  - mean sentiment / label。
  - skipped_future / skipped_no_published。
  - 样本首条 headline。

这使前端 report review 不只看到 `sentiment=0.35` 这样的数字，也能看到该 sentiment 来自哪些历史新闻，以及哪些新闻因为 PIT 风险被剔除。

## Phase 2: Strategy-Aware Backtest

### 已完成

- 新增 strategy registry：
  - `polyagents/lab/strategies.py`
- MVP 策略：
  - `market-naive-v1`
  - `linear-factor-v1`
  - `momentum-v1`
- `BacktestRequest` 新增可选字段：

```json
{
  "strategy_id": "linear-factor-v1"
}
```

- 默认策略仍为 `linear-factor-v1`，兼容旧请求。
- `BacktestRunner.run()` 根据 `strategy_id` 选择 scoring logic。
- `EvaluationReport` 记录：
  - `strategy_id`
  - strategy description
  - baseline
  - available strategies
  - 每个 sample 的 `signal_model.feature_vector`
  - 每个 sample 的 `signal_model.feature_contributions`
- 修复同一秒内连续回测可能覆盖 run/report id 的问题。

### 策略说明

`market-naive-v1`：

- 直接信任历史市场价格。
- 用作 baseline。

`linear-factor-v1`：

- 使用 sentiment、flow imbalance、book pressure、spread、price momentum 等因素。
- 作为当前默认 deterministic factor model。

`momentum-v1`：

- 更偏重 price momentum，并使用 flow imbalance 做轻量确认。

## Verification

合并最新 `amber/main` 后，相关测试通过：

```text
33 passed, 1 warning
```

覆盖范围：

- Lab strategy registry
- BacktestRequest contract
- Lab repository persistence
- Historical ingestion
- Lab API contract
- Storage
- 新 main 的 storage engine
- 新 main 的 kernel backtest strategies

默认 DataStore 真实 collections smoke test：

```text
market-naive-v1   n=46 source=collections fixture=false
linear-factor-v1  n=46 source=collections fixture=false
momentum-v1       n=46 source=collections fixture=false
```

这说明同一批真实 settled collections 可以被不同策略消费，并生成不同的 EvaluationReport。

## Known Limitations

- 当前 ingestion MVP 只构造 YES-token 样本。
- 当前 prediction policy 使用价格序列 50% 位置，后续可增加多 prediction_time replay。
- 历史 orderbook 尚未重建。
- historical news 已有 PIT-safe MVP，但依赖新闻 API 的 historical coverage 和 published timestamp 质量；未带时间戳或晚于 prediction_time 的新闻会被跳过。
- 样本量目前为 46 条可用 settled collections，已能 demo 真实链路，但仍需要扩大数据覆盖。
- `tests/test_web_kernel_mode.py` 在当前 Codex 沙盒中仍会失败，因为新 main 的 SQLAlchemy audit/storage 默认路径指向 `/Users/haoyingwang/.polyagents/cache/aihf.db`，沙盒无法打开该路径。该问题不影响 Lab ingestion 或 strategy-aware backtest，但建议后续将测试环境 DB path 显式指向 `tmp_path` 或加入 fallback。

## Next Recommended Work

下一阶段建议进入 Phase 3: Dry-Run Monitor。

重点目标：

- 扫描 active markets。
- 用指定 strategy 计算 `p_raw`、`p_cal`、edge、APY。
- 通过 deterministic risk gate 生成 candidate。
- 所有结果保持 `dry_run=true`。
- 不触发真实下单。

同时建议继续扩展 historical collections：

- 增加更多 resolved markets。
- 支持多 prediction_time。
- 后续补历史 orderbook，并继续提高 historical news 数据源覆盖、去重和 query quality。
