# aihf / polyagents 产品 PRD 与技术架构方案

> 版本: 2026-06-22
> 基于: `架构说明.md`、`项目状态与待决策.md`、当前仓库实现、评估建议、pi 内嵌 Agent Kernel 架构

## 一、产品定位

aihf 不是一个“让 LLM 猜概率然后下单”的交易机器人，而是一个面向 Polymarket / 预测市场的 **7×24 金融投资智能体平台**。其中:

- **aihf** 是金融投资智能体业务层，负责产品体验、多租户、权限、安全、交易风控、评估闭环、自动化任务和审计。
- **pi** 是内嵌式 Agent Kernel，负责大模型推理、标准 Agent 循环、基础文件/命令工具、会话持久化、上下文压缩和工具注入。
- **polyagents** 是 aihf 的预测市场交易与评估引擎，负责 L0-L6 的数据、预测、校准、执行、反馈和研究闭环。
- **Codex / Claude Code / opencode / aider** 等 coding agent 不是主控内核，而是按场景切换的 worker adapter，用于代码修改、实验脚本、测试、回测和工程任务。

核心目标是把交易系统从一次性 prompt 决策，升级为持续发现、验证、校准、执行、复盘 alpha 的闭环系统:

```
用户/自动任务 → pi Agent Session → 假设提出 → point-in-time 数据采集 → 概率预测 → 校准 → sizing → 真实纸面成交模拟
       → 结算 → 评估 → 反事实归因 → 教训沉淀 → 新假设
```

平台默认只读和 paper trading。任何实盘能力必须在校准、前瞻评估、风控和执行质量都达标后再显式开启。

## 二、目标用户

| 用户 | 需求 |
|---|---|
| Alpha 研究员 | 快速提出预测市场 alpha 假设，做分类别评估、基线比较、前瞻实验 |
| 策略工程师 | 构建可审计、可复现、可插拔的交易流水线 |
| 风控 / 运营 | 查看资金曲线、敞口、成交质量、结算风险、资金锁定成本 |
| Agent 平台用户 | 通过聊天界面调用确定性工具，而不是让 LLM 自己暗箱执行交易 |
| 平台管理员 | 管理 pi session、工具权限、沙盒、自动化任务、租户隔离和审计 |

## 三、产品原则

1. **评估优先于交易**  
   没有评估闭环的 alpha 不能进入 paper execution，更不能进入 live execution。

2. **市场价格是强基线**  
   p_true 必须证明自己跑赢“直接相信市场价”。如果跑不赢，edge 默认视为噪声。

3. **概率先校准，再 Kelly**  
   Kelly sizing 的数学前提是概率可信。LLM 原始 p_true 不能直接下注，必须经过校准、收缩和不确定性惩罚。

4. **forward-test 优先于 backtest**  
   回测用于发现问题和加速迭代，前瞻评估用于证明系统是否真的有 edge。

5. **point-in-time 是硬约束**  
   所有特征、新闻、订单簿、相似案例、prompt 上下文都必须有可审计时间戳，禁止任何未来信息泄漏。

6. **paper trading 必须像真实世界**  
   paper executor 必须 walk the book，按可成交深度、滑点、点差、冲击成本和手续费模拟成交。

7. **教训不是胜负标签**  
   L4 反馈要重视校准质量、概率误差、反事实候选和噪声过滤，而不是只把盈利/亏损蒸馏成 prompt 教训。

8. **收益必须时间归一**  
   6% edge 持有 9 天和 9 个月完全不同，门槛、排序和报告必须纳入 APY、资金锁定和机会成本。

9. **Agent Kernel 内嵌优先**  
   aihf 通过 pi SDK 直接 `createAgentSession()`，掌控会话生命周期、工具注入、提示词、鉴权、上下文压缩和审计；外部 coding agent 只作为 worker adapter。

10. **业务控制面高于 worker 能力**  
    Codex、Claude Code、opencode、aider 可以被调用，但不能绕过 aihf 的交易风控、point-in-time 纪律、权限策略和 evaluation ledger。

## 四、产品范围

### V1 范围

| 模块 | 功能 |
|---|---|
| 市场扫描 | 扫描高流动性 / 高成交 / 即将结算 / 指定类别市场 |
| 市场快照 | 拉取价格、订单簿、成交流、新闻、因子、相似市场 |
| LLM 概率预测 | 输出 p_raw、方向、置信度、证据、反证、类别 |
| 概率校准 | 将 p_raw 校准为 p_calibrated，并记录校准版本 |
| 基线比较 | 与 market-implied probability 比较 Brier / log loss / ECE |
| 风控 sizing | edge、年化 edge、1/4 Kelly、仓位上限、流动性闸门 |
| realistic paper execution | walk order book，记录平均成交价、滑点、未成交量 |
| 结算与记账 | 按 token_id 结算，计算 realised P&L、APY、持有期 |
| 评估报告 | 按类别、时间窗、模型版本、市场类型统计预测质量和交易质量 |
| 反事实日志 | 记录未下单候选，评估“如果当时下单会怎样” |
| Agent Runtime | 通过 pi 内嵌 Agent Kernel 创建和恢复金融智能体会话 |
| skills + MCP | 作为显性能力层暴露交易纪律、工具集、MCP server 和权限 |
| Coding Harness | 通过 worker adapter 调用 Codex / Claude Code / opencode / aider 等执行工程任务 |
| 自动化任务 | 7×24 定时扫描、forward-test、结算、评估报告和异常告警 |

### 暂不做

| 项 | 原因 |
|---|---|
| 默认实盘交易 | 评估、校准、风控尚未证明稳定 edge 前不开放默认路径 |
| 完整自动化资金管理 | 需要更成熟的成交质量、结算风险和组合风险模型 |
| 只靠 backtest 宣称收益 | 回测有天然泄漏风险，不能作为上线依据 |
| 未校准 LLM 概率直接 Kelly | 会把概率偏差放大成资金风险 |
| 让外部 coding agent 作为系统主控 | 会削弱会话、权限、审计、PIT 纪律和多租户控制 |
| 绕过 pi session 的临时 prompt 调用 | 难以复现、难以审计、难以恢复长期任务 |

## 五、核心用户流程

### 1. Alpha 研究流程

```
创建假设 → 定义市场类别 / 特征集 / prompt 版本 → 跑历史重放
      → 与市场价基线比较 → 查看类别分层指标 → 进入 forward-test
```

输出:
- 假设名称、版本、适用类别
- p_raw vs p_calibrated vs market price
- Brier / log loss / ECE / calibration curve
- 相对市场价的增量收益和置信区间
- 泄漏风险检查结果

### 2. 前瞻评估流程

```
定时扫描 → timestamp-locked snapshot → 生成预测 → 只记录或纸面下单
      → 等待结算 → 评估 forecast quality 与 trading quality
```

关键要求:
- 预测生成后不可修改输入快照
- 所有新闻、RAG、订单簿、价格只允许使用 prediction_time 之前的数据
- 同时记录 actioned trades 和 rejected candidates
- settled 后统一进入 evaluation ledger

### 3. Paper trading 流程

```
market_snapshot → p_raw → p_calibrated → risk gates → paper_execute
      → walk book fill → portfolio ledger → settle → evaluation
```

成交输出必须包含:
- requested size
- filled size
- average fill price
- market mid / best ask / best bid
- slippage bps
- book depth consumed
- unfilled quantity
- execution quality score

### 4. 反馈学习流程

```
结算结果 → 预测质量评估 → 交易质量评估 → 噪声过滤
      → 重要性加权 → lesson 入库 → prompt / RAG 受控注入
```

Lesson 不能只写“这次赢了/输了”。必须包含:
- 概率是否校准
- 错误来自信息缺失、推理错误、市场已定价、结算歧义还是纯噪声
- 是否适用于同类市场
- 重要性权重
- 过期时间或衰减策略

## 六、成功指标

### Forecast quality

| 指标 | 含义 |
|---|---|
| Brier score | 概率预测均方误差 |
| Log loss | 对高置信错误更敏感 |
| ECE | Expected Calibration Error，衡量校准程度 |
| Calibration curve | 分桶查看 60% 概率事件是否约 60% 发生 |
| Market baseline delta | p_calibrated 是否跑赢 market-implied probability |

所有指标必须支持按以下维度切分:
- 类别: 政治、经济、体育、公司事件、加密、宏观等
- 市场状态: 新开、临近结算、高流动性、低流动性
- 时间窗: 7d / 30d / 90d / all
- 模型版本、prompt 版本、校准器版本
- 持有期 bucket

### Trading quality

| 指标 | 含义 |
|---|---|
| realised P&L | 已实现盈亏 |
| APY | 年化收益，处理资金锁定时间 |
| Sharpe | 风险调整后收益 |
| max drawdown | 最大回撤 |
| hit rate | 命中率，只做辅助指标 |
| avg slippage bps | 平均滑点 |
| fill rate | 成交率 |
| opportunity cost | 资金锁定造成的机会成本 |

### 产品门槛

| 阶段 | 进入条件 |
|---|---|
| 记录模式 | 任意假设均可进入，只记录不交易 |
| paper trading | 至少有校准器、market baseline 对比、point-in-time 快照 |
| limited live | forward-test 样本充足，p_calibrated 在目标类别显著跑赢市场价，paper execution 质量稳定 |
| scaled live | 有连续多个时间窗的稳定 APY / Sharpe，且结算与执行风险可控 |

## 七、技术架构总览

原 L1-L4 保留，但将评估、校准、实验治理提升为平级系统，并在上层引入 aihf + pi 的内嵌 Agent Runtime:

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                                aihf app layer                                 │
│  Web / Chat / Dashboard / Automations / Multi-tenant / Audit / Permissions     │
└───────────────────────────────────────┬───────────────────────────────────────┘
                                        │ createAgentSession()
┌───────────────────────────────────────▼───────────────────────────────────────┐
│                         pi embedded Agent Kernel                              │
│  model inference / agent loop / file+command tools / session persistence       │
│  memory / compaction / tool injection / auth context / sandbox policy          │
└───────────────┬───────────────────────────────┬───────────────────────────────┘
                │                               │
                │                               └── Worker adapters
                │                                   Codex / Claude Code / opencode / aider
                │                                   local sandbox / remote RPC / harness
                │
┌───────────────▼───────────────────────────────────────────────────────────────┐
│                         polyagents domain engine                              │
│                                                                               │
│  L0 Data Governance  ──►  L1 Market Data  ──►  L2 Signal / Decision           │
│  时间戳锁定              价格/订单簿/新闻        p_raw / p_cal / sizing        │
│  PIT snapshot             因子/RAG/相似市场       reflection / risk gates       │
│                                                                               │
│             ┌────────────── L5 Evaluation & Calibration ◄──────────────┐       │
│             │ baseline / Brier / log loss / ECE / APY / Sharpe          │       │
│             │ calibration model registry / forward-test ledger          │       │
│             └───────────────────────┬──────────────────────────────────┘       │
│                                     │                                          │
│  L3 Execution  ───────────────►  L4 Feedback  ───────────────►  L6 Research    │
│  paper/live adapters             settlement / lessons             hypothesis   │
│  walk-the-book fills              counterfactuals / memory         experiments  │
└───────────────────────────────────────────────────────────────────────────────┘
```

## 八、模块设计

### A0: aihf App Layer

职责:
- 提供 Web、Chat、Dashboard、Automation、Admin Console
- 管理用户、租户、角色、API key、交易权限和审计
- 管理 7×24 定时任务: 市场扫描、forward-test、结算、评估报告、异常告警
- 将金融业务上下文、权限上下文和风险策略注入 pi session
- 为 skills、MCP、polyagents engine、coding worker 提供统一控制面

建议模块:

```
aihf/app/
  web/                  # chat, dashboard, admin
  auth/                 # user, tenant, role, API key policy
  automations/          # scheduled runs, monitors, alerts
  audit/                # tool call, session, trade, data lineage audit
  permissions/          # tool permission and execution policy
  runtime/              # pi session lifecycle wrapper
```

硬规则:
- 任何交易相关 action 必须经过 aihf permission policy
- live execution 默认关闭，且必须由 aihf 层显式授权
- 所有长期任务必须可恢复、可审计、可停止

### A1: pi Embedded Agent Kernel

职责:
- 通过 SDK 内嵌在 aihf 源码中，而不是作为外部黑盒进程
- 提供标准 Agent loop、大模型推理、工具调用、文件/命令工具、会话持久化
- 支持上下文压缩、记忆策略、工具注入、鉴权上下文、沙盒策略
- 为每个金融任务创建可审计的 agent session

核心接口示意:

```ts
const session = await createAgentSession({
  tenantId,
  userId,
  agentType: "financial-research",
  systemPrompt,
  tools,
  skills,
  authContext,
  memoryPolicy,
  compactionPolicy,
  sandboxPolicy,
  auditSink,
});
```

建议模块:

```
aihf/runtime/
  pi_client.ts              # createAgentSession wrapper
  session_registry.ts       # session create/resume/stop/archive
  tool_injection.ts         # inject trading/evaluation/harness tools
  prompt_policies.ts        # system prompts and discipline
  compaction_policies.ts    # context compression for long-running agents
  memory_policies.ts        # what can be remembered, decayed, or forgotten
  sandbox_policies.ts       # file/command/network boundaries
```

pi session 必须记录:
- `session_id`
- `tenant_id`
- `agent_type`
- `model`
- `system_prompt_version`
- `tool_manifest_hash`
- `auth_scope`
- `memory_policy_version`
- `compaction_policy_version`
- `created_at / resumed_at / stopped_at`

### A2: Worker Adapters / Coding Harness

职责:
- 将 Codex、Claude Code、opencode、aider 等能力包装成可替换 worker
- 执行代码修改、测试、数据导出、回测脚本、校准器实验、文档生成
- 不拥有交易决策主控权，不得绕过 aihf 权限和 polyagents evaluation ledger

推荐接口:

```ts
interface WorkerBackend {
  createWorkspace(input): Promise<WorkspaceRef>;
  runCommand(workspace, command, policy): Promise<CommandResult>;
  editFile(workspace, patch, policy): Promise<FileDiff>;
  runTests(workspace, selector): Promise<TestReport>;
  createArtifact(workspace, spec): Promise<ArtifactRef>;
  reportDiff(workspace): Promise<DiffSummary>;
  stop(workspace): Promise<void>;
}
```

适配器:

```
aihf/workers/
  codex_worker.ts
  claude_code_worker.ts
  opencode_worker.ts
  aider_worker.ts
  local_sandbox_worker.ts
  remote_rpc_worker.ts
```

使用场景:
- Research Agent 生成一个新特征或校准器实验
- pi session 调用 `worker.runCommand()` 跑测试或 backtest
- worker 产出 diff、artifact、report
- aihf 审核后将实验结果写入 evaluation ledger
- 满足条件后才允许 promote to forward-test

硬规则:
- worker 不能直接调用 live execution
- worker 不能直接写入 settled outcome 到 signal 输入
- worker 产物必须带 workspace、commit/diff、命令、依赖、数据快照版本
- worker 输出进入 Research / Evaluation，不能自动变成交易 lesson

### L0: Data Governance

职责:
- 创建 timestamp-locked snapshot
- 管理 point-in-time 数据读取策略
- 防止 RAG、新闻、SQLite 历史、结算结果泄漏到预测时刻之前
- 记录数据 lineage 和 feature manifest

建议模块:

```
polyagents/data_governance/
  snapshot.py          # SnapshotBuilder / SnapshotManifest
  time_guard.py        # assert_point_in_time(...)
  lineage.py           # data source, fetched_at, effective_at, version
  leak_checks.py       # future-info 检查
```

核心对象:
- `prediction_time`
- `available_at`
- `source_event_time`
- `fetched_at`
- `snapshot_id`
- `feature_manifest_hash`

硬规则:
- 任何 `available_at > prediction_time` 的数据不得进入 signal
- RAG 检索只能返回在 prediction_time 前已经存在的历史市场状态
- 已结算标签只允许在 evaluation / feedback 阶段出现

### L1: Market Data

职责:
- Polymarket market data、订单簿、成交流、K 线
- 新闻 / 事件上下文
- 微结构特征
- 相似市场检索

升级重点:
- 新闻数据必须保存原文摘要、URL、发布时间、抓取时间
- 订单簿快照必须可重放
- collections 表从“缓存”升级为训练与评估资产

### L2: Signal / Decision

职责:
- LLM 或模型输出 p_raw
- 校准器输出 p_calibrated
- 与市场价格比较 edge
- 用时间年化门槛、Kelly、风控约束生成 trade decision

关键改造:
- `Signal` schema 增加 `model_version`、`prompt_version`、`evidence_refs`、`uncertainty`
- `TradeDecision` 同时保存 `p_market`、`p_raw`、`p_calibrated`
- sizing 使用 p_calibrated，不使用 p_raw
- edge floor 改为 `min_apy` + `min_absolute_edge` 双门槛

### L3: Execution

职责:
- Paper / Live 执行端口
- 熔断器
- Portfolio ledger

paper executor 必须:
- walk the book
- 支持部分成交
- 记录滑点和冲击成本
- 支持 marketable limit order 的可成交判断
- 禁止用 mid price 作为默认成交价

### L4: Feedback

职责:
- 结算
- P&L 归因
- lesson 生成
- 反事实候选追踪

升级重点:
- lessons 加入重要性权重、置信度、适用类别、过期时间
- 未下单候选也写入 `counterfactuals`
- outcome reflection 不能直接污染 prompt，必须经过评估质量过滤

### L5: Evaluation & Calibration

这是新增的一等子系统，和 L1-L4 平级。

职责:
- 预测质量评估
- 市场基线对比
- 校准曲线与 ECE
- 类别分层报告
- forward-test ledger
- 校准器训练、版本管理、回放
- 交易质量评估: APY / Sharpe / drawdown / slippage

建议模块:

```
polyagents/evaluation/
  metrics.py             # Brier / log_loss / ECE / calibration bins
  baseline.py            # market price baseline
  stratify.py            # category / horizon / liquidity bucketing
  ledger.py              # forecast, trade, settlement, counterfactual records
  reports.py             # markdown/json/html reports
  forward_test.py        # live append-only evaluation protocol
  backtest.py            # historical replay, PIT only

polyagents/calibration/
  calibrators.py         # shrinkage / isotonic / platt / beta calibration
  registry.py            # versioned calibrator artifacts
  train.py               # train on settled historical records
```

核心表:

| 表 | 用途 |
|---|---|
| `snapshots` | timestamp-locked 输入快照 |
| `forecasts` | p_raw / p_calibrated / p_market / versions |
| `decisions` | action / size / risk gates / reason |
| `executions` | fill detail / slippage / order book consumption |
| `settlements` | outcome / resolved_at / dispute status |
| `evaluations` | Brier / log loss / ECE bucket / baseline delta |
| `counterfactuals` | 未下单候选的预测、价格和后验表现 |
| `lessons` | 加权、可遗忘的教训 |

### L6: Research / Alpha Lab

职责:
- 管理 alpha 假设
- 管理实验配置
- 产出研究报告
- 对比不同 prompt、模型、特征集、校准器

建议对象:
- `Hypothesis`
- `Experiment`
- `DatasetSlice`
- `ModelRun`
- `EvaluationReport`

示例:

```yaml
hypothesis: "政治类市场中，新闻事件后的 LLM 更新速度快于市场"
category: politics
features:
  - orderbook_microstructure
  - recent_news
  - similar_resolved_markets
baseline:
  - market_price
  - no_news_llm
success_metric:
  - brier_delta_vs_market > 0.01
  - ece < 0.04
  - forward_test_apy > 15%
```

## 九、数据与存储方案

### SQLite

SQLite 继续作为默认本地资产库，但要从缓存库升级为事件账本:

```
markets
candles
trades
orderbook_snapshots
collections
snapshots
forecasts
decisions
executions
settlements
evaluations
counterfactuals
lessons
calibrator_versions
experiment_runs
agent_sessions
tool_manifests
tool_runs
worker_workspaces
worker_artifacts
automation_runs
audit_events
```

要求:
- append-only 优先
- 所有模型输出带版本号
- 所有快照带 hash
- 结算标签与预测输入物理隔离
- 支持导出为 parquet / JSONL，供外部 ML 或 qlib 适配
- pi session、工具调用、worker 执行、自动化任务均进入 audit ledger
- worker 产物必须能回溯到 session、workspace、命令、diff 和数据快照

### Chroma / RAG

RAG 检索必须 point-in-time:
- 市场 embedding 写入时记录 `available_at`
- 查询时过滤 `available_at <= prediction_time`
- resolved outcome 默认不进入 signal prompt，除非它在历史时点已经可知

### JSONL Memory

JSONL 继续保留，但定位调整:
- 不作为唯一真相源
- 用作可读 audit log 和 prompt lesson source
- 真正评估口径以 SQLite evaluation ledger 为准

### Agent Runtime Store

Agent Runtime 需要独立记录 pi session 与 worker lifecycle:

| 表 | 用途 |
|---|---|
| `agent_sessions` | pi 会话生命周期、模型、prompt、tool manifest、租户和权限上下文 |
| `tool_manifests` | 每次 session 注入的 tools / skills / MCP 版本与 hash |
| `tool_runs` | 每次工具调用的输入、输出摘要、权限判断、耗时、错误 |
| `worker_workspaces` | coding harness 工作区、backend 类型、状态 |
| `worker_artifacts` | 代码 diff、测试报告、回测报告、导出数据、图表 |
| `automation_runs` | 定时任务的触发、恢复、停止、结果 |
| `audit_events` | 用户、Agent、工具、交易和权限事件的统一审计流 |

## 十、Agent Runtime / Skills / MCP 产品接口

### 产品入口

skills + MCP 不应只藏在 Settings。建议产品上提供显性 **Tools / Runtime** 入口:

| 页面 | 作用 |
|---|---|
| `Tools` | 查看和启用 skills、MCP servers、tool permissions、最近 tool runs |
| `Runtime` | 查看 pi sessions、上下文压缩、memory policy、sandbox policy、自动化状态 |
| `Harness` | 查看 coding worker 工作区、命令、diff、测试、artifact、promote to forward-test |
| `Settings` | 管理 API keys、server URL、租户配置、默认模型、执行模式 |

交互上:
- `Ask` 是用户主入口
- `Tools` 是 Agent 能力入口
- `Runtime` 是 Agent 内核与会话入口
- `Harness` 是工程执行入口
- `Settings` 只承载配置，不承载核心工作流

新增或强化 MCP tools:

| Tool | 功能 |
|---|---|
| `scan_markets` | 扫描候选市场 |
| `market_snapshot` | 创建 PIT 快照并返回摘要 |
| `forecast_market` | 生成 p_raw / p_calibrated，不交易 |
| `evaluate_forecast` | 查询预测质量和基线对比 |
| `size_position` | 用 p_calibrated + 风控 sizing |
| `paper_execute` | realistic paper fill |
| `record_counterfactual` | 记录未交易候选 |
| `settle_markets` | 结算并写入 ledger |
| `evaluation_report` | 产出分类别指标报告 |
| `calibration_report` | 查看校准曲线 / ECE |
| `experiment_run` | 运行指定 alpha 假设 |

新增 pi runtime tools:

| Tool | 功能 |
|---|---|
| `create_agent_session` | 创建金融研究 / 交易 / 评估 / 工程 session |
| `resume_agent_session` | 恢复 7×24 长期任务 |
| `list_agent_sessions` | 查看活跃、暂停、失败、已归档 session |
| `inspect_tool_manifest` | 查看当前 session 注入的 skills / MCP / tools |
| `update_tool_permissions` | 调整工具权限，需要管理员或策略授权 |
| `compact_session_context` | 触发或预览上下文压缩 |
| `inspect_memory_policy` | 查看 lesson、RAG、chat memory 的保留和遗忘策略 |

新增 worker / harness tools:

| Tool | 功能 |
|---|---|
| `create_worker_workspace` | 为某个 hypothesis 或 bug 创建工程工作区 |
| `run_worker_command` | 在受控 sandbox 中运行测试、脚本、回放 |
| `apply_worker_patch` | 让 worker 产出代码修改，但由 aihf 记录 diff |
| `run_worker_tests` | 跑单元测试、评估测试、PIT 泄漏测试 |
| `collect_worker_artifacts` | 收集报告、图表、parquet、JSONL、日志 |
| `promote_experiment` | 将通过评估的实验提升到 forward-test |

Skill 纪律:
- 不允许让 LLM 直接绕过 `size_position`
- 不允许用未校准概率下单
- 不允许把结算后信息放进预测 prompt
- 不允许只看 P&L，不看 Brier / log loss / ECE / baseline delta
- 不允许外部 worker 绕过 pi session 和 aihf permission policy
- 不允许 worker 产物未经 evaluation ledger 就影响 live 或 paper trading

## 十一、风控与上线门槛

### 风控门槛

| 风险 | 控制 |
|---|---|
| 概率偏差 | 校准器 + market shrinkage + ECE 监控 |
| 市场已定价 | market baseline 必须跑赢 |
| 滑点高估收益 | walk-the-book paper execution |
| 资金锁定 | APY / opportunity cost |
| 结算歧义 | UMA dispute / ambiguous resolution 标记 |
| 回测泄漏 | PIT snapshot + leak checks |
| prompt 过拟合 | forward-test 优先 + 版本隔离 |
| Agent 行为不可控 | pi 内嵌 session + tool manifest + permission policy + audit |
| Worker 越权 | worker adapter 权限收敛，禁止直接交易和读取未来标签 |
| 长期任务漂移 | session resume、context compaction、tool/version pinning |

### 实盘开启条件

必须同时满足:
- forward-test 样本达到预设数量
- 目标类别中 Brier / log loss 稳定优于市场基线
- ECE 低于阈值
- paper APY / Sharpe 在多个时间窗稳定
- paper slippage 模型保守且可解释
- 最大回撤和敞口在限额内
- 结算风险流程明确
- pi session、tools、worker、automation 的审计链完整
- live tool permission 经过显式授权，不允许由 worker 直接触发

## 十二、路线图

### Milestone 0: pi 内嵌 Agent Runtime

- 封装 `createAgentSession()`，在 aihf 源码内直接创建 pi session
- 建立 session registry、tool manifest、memory policy、compaction policy
- 所有 Ask / Automation / Harness 任务都进入 `agent_sessions`
- 建立 tool permission policy 与 audit sink

验收:
- 任意 Agent 会话可创建、恢复、停止、归档
- 每次工具注入都有 manifest hash
- 每次 tool run 都能回溯 session、tenant、permission 和输入输出摘要

### Milestone 1: 评估账本最小闭环

- 固化 `forecasts / settlements / evaluations` 表
- 每次 `analyze()` 都记录 p_raw、p_market、p_calibrated
- 实现 market baseline 对比
- 输出 Brier / log loss / ECE 报告
- 按类别分层

验收:
- 对任意 settled forecasts 生成 evaluation report
- 能回答“p_true 有没有跑赢市场价”

### Milestone 2: Point-in-time 快照与泄漏防线

- 实现 snapshot manifest
- L1 所有输入记录 available_at
- RAG 增加时间过滤
- backtest 只能读取 prediction_time 前数据
- 增加 leak check 测试

验收:
- 人工构造未来新闻 / 结算标签，系统能拒绝进入 signal

### Milestone 3: 校准器体系

- 实现 shrinkage calibrator
- 支持 isotonic / Platt / beta calibration seam
- 校准器版本注册
- decision 只使用 p_calibrated

验收:
- calibration report 展示 p_raw、p_calibrated、market baseline 的 ECE 对比

### Milestone 4: 真实 paper execution

- paper executor walk book
- 记录 partial fill、slippage、depth consumed
- P&L 报告加入 execution quality

验收:
- 同一订单在薄簿市场中产生明显滑点，而不是 mid fill

### Milestone 5: Forward-test Alpha Lab

- experiment config
- append-only forward-test ledger
- 反事实候选记录
- 类别/假设/prompt/模型版本对比
- 由 pi automation session 定时执行扫描、预测、记录和评估

验收:
- 能运行一个 30 天 politics forward-test，并输出与市场价基线的完整对比

### Milestone 6: Coding Harness / Worker Adapters

- 定义 `WorkerBackend` 接口
- 接入至少一个 worker backend，用于实验脚本、测试和报告
- worker workspace、命令、diff、artifact 全部入库
- worker 产物可 promote 到 forward-test，但不能直接进入 live

验收:
- Research Agent 能创建一个校准器实验工作区、运行测试、生成报告
- 报告能被写入 evaluation ledger
- 权限测试证明 worker 无法直接调用 live execution

### Milestone 7: 7×24 Automation

- 支持定时 market scan、forward-test、settlement、evaluation report
- 支持 session resume 和失败重试
- 支持异常告警: 泄漏检查失败、ECE 飙升、slippage 异常、任务失败

验收:
- 一个自动化任务能跨进程恢复并继续执行
- 每次自动化 run 都能审计到对应 pi session 和 tool runs

### Milestone 8: Limited Live Gate

- live 仍默认关闭
- 增加只允许白名单市场 / 小额限额 / 强制人工确认
- live 与 paper 共用 evaluation ledger
- live permission 由 aihf policy 控制，pi session 和 worker 均不可绕过

验收:
- live 前置检查失败时无法下单

## 十三、关键开放问题

1. 新闻源选择: Tavily、Polyseer、Perplexity、RSS、自建爬虫，还是先用 Claude 对人工摘要打分。
2. 类别 taxonomy: 使用 Polymarket 原生 category，还是自建更适合 alpha 分析的 taxonomy。
3. qlib 定位: 用于因子/模型训练，还是仅作为外部 benchmark，不强行套官方股票回测范式。
4. 校准器训练窗口: 全历史、滚动窗口、按类别训练，还是层级贝叶斯式混合。
5. lesson 注入策略: top-k、按类别、按相似市场、按重要性衰减，还是只用于研究报告不进 prompt。
6. pi SDK 能力边界: 是否已稳定支持会话持久化、上下文压缩、工具注入、文件/命令工具和 sandbox policy。
7. Worker backend 优先级: 先接 Codex、Claude Code、opencode、aider，还是先做 LocalSandboxBackend。
8. 多租户策略: 租户级模型、key、数据、memory、worker workspace 是否完全隔离。

## 十四、最终形态

最终产品应当是一个构建在 pi 内嵌 Agent Kernel 之上的 **金融投资智能体操作系统**:

- Agent 可以聊天式扫描、分析、解释市场，也可以 7×24 主动运行
- aihf 完全掌控 pi session 生命周期、工具注入、权限、记忆、压缩和审计
- skills + MCP 是可见能力层，用户和管理员能看到 Agent 到底会什么、能调用什么
- coding worker 是可替换执行后端，服务于研究和工程，不拥有交易主控权
- 工具层保持确定性、可审计、可复现
- 每一个预测都有时间戳锁定输入和后验评估
- 每一个交易都有真实可成交模拟和风控轨迹
- 每一个 lesson 都有权重、适用范围和遗忘机制
- 系统持续回答同一个核心问题:

**我们的 p_calibrated 是否在某些类别、某些时间窗、扣除滑点和资金锁定后，稳定跑赢市场价格？**

只有这个问题的答案长期为“是”，且 pi session、tool manifest、worker artifacts、evaluation ledger 和 live permission 全部可审计，aihf 才有资格从研究平台进入小额实盘。
