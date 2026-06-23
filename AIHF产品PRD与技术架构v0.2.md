# AIHF 产品 PRD 与技术架构 v0.2

> 版本: v0.2 · 2026-06-22
> 替代: `产品PRD与技术架构方案.md`(v0.1)
> 基于: Claude 式极简产品哲学 + pi.dev 调研结论 + 现有 polyagents 代码
> 配套: `架构说明.md` · `项目状态与待决策.md` · `feedback1.md`

---

## 〇、v0.2 相对 v0.1 的根本变化(给读过 v0.1 的人)

| 维度 | v0.1 | v0.2 |
|---|---|---|
| 架构形态 | aihf app + pi Kernel + worker adapters,三层新东西 | **一条流水线 + 三种副作用模式**,无新增架构层 |
| 底层引擎 | pi 作为"既成地基"(未验证依赖) | **polyagents/LangGraph 是地基;pi 是可选 chat 外壳** |
| 产品形态 | 8 个 milestone,6-12 个月 | **3 道晋升门 + 2 周 MVP** |
| 复杂度位置 | 架构层(多模块、多接口、多租户) | **对象状态机层(5 个对象,3 种状态)** |
| 语言栈 | Python 后端 + TS 接口混搭 | **纯 Python 单进程**(pi 外壳经 MCP 接入,不混栈) |
| Coding agent | 4 个平级 worker adapter | **Lab 内一个 code_exec 工具,backend 可换** |
| 实盘路径 | Live 是独立大模块 | **Live = 加一个 ExecutionAdapter,不改核心** |

**一句话**: v0.1 是"造一个金融版操作系统",v0.2 是"造一个金融版 Claude —— 少量原语 + 渐进式副作用 + 一个统一入口"。

---

## 一、产品定位

AIHF 是一个 **金融预测市场智能体**,设计哲学对标 Claude.ai:

- **一个入口**(一个对话框),模式由**副作用范围**决定,不由 UI 决定。
- **对象作为 artifact 涌现**: 聊着聊着,agent 生成一个假设/策略,它就"浮"成可命名、可引用、可 fork 的对象。
- **晋升即信任闸门**: 从"对话里的草稿"到"实盘里的资产",中间是显式的、带证据的 promote,不是无缝的。

目标市场: Polymarket 等预测市场(首发),架构预留扩展到其他二元结果市场(选项市场、体育、事件合约)。

核心问题(整个系统存在的理由):

> **我们的 p_calibrated 是否在某些类别、某些时间窗、扣除滑点和资金锁定后,稳定跑赢市场价格?**

只有这个问题长期答"是",AIHF 才有资格从研究平台进入实盘。

---

## 二、三种副作用模式(不是三个产品)

借鉴 Claude 的 chat / artifacts / projects,但用金融语义:

| Claude | AIHF 模式 | 副作用范围 | LLM 自由度 | 产出 |
|---|---|---|---|---|
| Chat | **Ask**(问) | 只读: 查市场、解释事件、比较假设 | 高(只读安全) | 解释 + 建议 |
| Artifacts/Code | **Lab**(研) | 沙盒可造: 建假设、跑回测、训校准器 | 中(沙盒内) | Hypothesis / Strategy 对象 + EvaluationReport |
| Projects | **Live**(盘) | 真金白银: 资金、风控、审计、结算 | 低(执行者) | Position / Portfolio |

**关键设计判断**: 这三者**不是三个独立模块**,而是**同一个 agent 在三种信任等级下的运行模式**。三种模式共享同一套确定性金融工具,区别只在:
1. **注入的工具子集**(ToolManifest)
2. **权限策略**(PermissionPolicy)
3. **审计强度**

### 模式切换的物理实现

```python
session = AgentSession(
    mode="ask",           # ← 这一个字段决定下面三件事
    tools=tool_registry.for_mode("ask"),
    permissions=policy_registry.for_mode("ask"),
    audit=AuditSink(enabled=True),
)
```

**没有"三个引擎",只有一个 AgentSession 类 + 三套配置。** 这是 v0.2 最核心的简化。

---

## 三、五个核心对象(系统的全部复杂度在这)

所有 UI、所有 agent 行为都是对这 5 个对象的 CRUD + 状态流转:

```
Market ──► Hypothesis ──► Strategy ──► Position ──► Portfolio
(市场)     (假设)         (策略)       (持仓)       (组合)
              │               │
              │ promote       │ promote
              │ (过评估门)    │ (过风控门)
              ▼               ▼
         Lab artifact     Live object
```

每个对象共享同一套契约(这是"通用性"的来源):

```python
@dataclass(frozen=True)
class FO:  # FinancialObject — 所有 5 个对象的基类
    id: str
    type: Literal["market", "hypothesis", "strategy", "position", "portfolio"]
    version: int                    # 不可变;改参数 = 新 version
    snapshot_id: str                # 创建时的 PIT 快照 hash
    state: Literal["draft", "lab", "paper", "live", "archived"]
    owner: str                      # tenant / user(MVP 单租户,字段预留)
    lineage: Lineage                # 来源链:从哪个 parent promote 来
    eval_summary: EvalSummary | None  # 该对象最近的评估快照
    created_at: str
```

### 为什么这让系统通用(多市场扩展的关键)

- 政治类、加密类、体育类、甚至非 Polymarket 的事件合约——**都是 `Market`**,差异藏在 `metadata.tags`,评估时分层。
- 新市场类型 = 新的 `MarketFetcher` 适配器(实现同一个接口),**不改对象模型、不改评估管道、不改 UI**。
- "挖策略"和"问答"用**同一个 agent + 同一组工具**,区别只在"产出挂到 Market(只读)还是 promote 成 Hypothesis(可回测)"。

---

## 四、底层引擎选型(基于 pi.dev 调研)

### 调研结论摘要(完整评估见对话记录)

Pi(pi.dev, [earendil-works/pi](https://github.com/earendil-works/pi))是 OpenClaw 的底层引擎,TypeScript,MIT,64k stars,提供 SDK 内嵌模式。它的强项: agent loop、session 持久化、context compaction。它的边界: **LLM 全程主导范式**(适合 coding,不适合金融确定性优先)、**TypeScript 栈**(与 Python 量化生态冲突)、**无金融风控/策略引擎**。

### v0.2 的分层决策

```
┌──────────────────────────────────────────────────────────┐
│  Ask 模式 chat 外壳(可选,pi 或任何 MCP client)           │
│  交互、compaction、session(MVP 用简单 web chat)          │
├──────────────────────────────────────────────────────────┤
│  MCP 边界(polyagents 暴露成 MCP server)                  │
├──────────────────────────────────────────────────────────┤
│  AgentSession 薄层(Python,自建,~300 行)                 │
│  生命周期 / 工具注入 / 权限 / 审计 / 模式切换             │
├──────────────────────────────────────────────────────────┤
│  LangGraph StateGraph(借) + Anthropic SDK(借)           │
│  确定性节点 + LLM 节点混合图                              │
├──────────────────────────────────────────────────────────┤
│  polyagents 引擎(L0-L6,你的护城河)                      │
│  数据 / 信号 / 校准 / 执行 / 反馈 / 评估 / 研究           │
└──────────────────────────────────────────────────────────┘
```

**核心判断**: 引擎 5 层职责里,**3 层借、1 层薄自建、1 层是护城河**。

| 职责 | 方案 | 理由 |
|---|---|---|
| 模型推理 | Anthropic SDK | 已有 key,commodity |
| 图编排 | LangGraph StateGraph | 已在用;天生支持"确定性节点 + LLM 节点" |
| Agent loop | AgentSession 内 ~100 行 | 不需要通用 loop,只需要"LLM 节点调 SDK,确定性节点直接调函数" |
| 会话/权限/审计 | **自建 AgentSession** | 金融特有,无现成方案 |
| 工具集 | **自建 polyagents 工具** | 产品价值,非 commodity |

### 关于 pi 的定位(明确回答 v0.1 的 open question #6)

- **pi 不作为地基**。它未提供金融需要的确定性工作流原语和策略引擎,且 TS 栈与 Python 量化生态冲突。
- **pi 作为可选的 Ask 模式 chat 外壳**。它的 compaction / session / chat 体验是强项,作为 MCP client 接入 polyagents 即可享受这些能力,且**可替换**(换成 Claude Desktop、Cursor、任何 MCP client 都行)。
- **MVP 不依赖 pi**。先用简单 web chat 跑通闭环,pi 接入列为 Milestone 之后的优化项。
- **关注变量**: 若 pi 推出官方 Python SDK,或增加确定性工作流原语,重新评估其作为地基的可能。

---

## 五、三道晋升门(整个工作流引擎就这么简单)

v0.1 用 8 个 milestone 描述流程,v0.2 把整个工作流压成 **3 道晋升门**:

```
Ask 里的建议 ──[用户点"验证"]──► Hypothesis(Lab/draft)
Hypothesis    ──[过评估门]────► Strategy(Lab/paper)
Strategy      ──[过风控门]────► Strategy(Live)
```

每道门是**纯函数 + 显式标准**,不是 LLM 判断:

| 门 | 判断逻辑(确定性) | 类型 |
|---|---|---|
| Ask → Hypothesis | 用户显式点击 | 人工门 |
| Hypothesis → paper Strategy | `forward_test_n >= N` AND `brier_vs_market CI 下界 > 0` AND `ece < 阈值` | 自动门(可配置) |
| paper → Live | `paper_apy > min_apy` AND `max_dd < 限额` AND `slippage 保守` AND **人工批准** | 混合门 |

晋升是**单向、版本化、带证据的**:

- Strategy promote 到 Live 后,Lab 版本仍保留,可对比"上线后 vs 上线前",发现 decay 即回滚。
- 每次 promote 写一条 `Lineage` 记录: from / promoted_at / promoted_by / evidence_ref(挂对应的 EvaluationReport)。
- 这套机制吸收了 v0.1 的"7×24 自动化、session resume、worker promote"——它们都是"对象在状态机里流转",不需要单独的 automation framework。

---

## 六、产品原则(继承 v0.1 的 10 条,精简为 7 条)

1. **评估优先于交易**。没评估闭环的 alpha 不进 paper,更不进 live。
2. **市场价格是强基线**。p_calibrated 必须证明跑赢"直接信市场价",否则 edge 视为噪声。
3. **概率先校准再 Kelly**。LLM 原始 p_true 不直接下注,经校准 + 收缩 + 不确定性惩罚。
4. **forward-test 优先于 backtest**。回测发现问题,前瞻评估证明 edge。
5. **point-in-time 是硬约束**。所有特征/新闻/订单簿/相似案例带可审计时间戳,禁止未来信息泄漏。
6. **paper trading 必须像真实世界**。walk the book + 滑点 + 冲击成本 + 资金锁定成本。
7. **收益必须时间归一**。门槛、排序、报告纳入 APY、资金锁定、机会成本。

(v0.1 的 8-10 条关于 pi/worker 的原则被 v0.2 的引擎选型节吸收,不再单列。)

---

## 七、两周 MVP(最小闭环)

### 目标

**两周内,零新外部依赖,跑通**: 聊天 → 提假设 → 回测 → 看评估报告。

### MVP 范围

| 做 | 不做 |
|---|---|
| Ask 模式(简单 web chat) | pi 外壳接入 |
| Hypothesis 对象 CRUD | Live 模式(只留接口) |
| 历史回放(用现有 SQLite 数据) | 实时数据流 |
| p_raw / p_cal / p_market 落库 | 复杂校准器(isotonic 留接口) |
| Brier / ECE / baseline delta 报告 | Sharpe / drawdown(无 paper 执行) |
| 一道晋升门(Ask → Hypothesis) | 后两道门(留 stub) |
| 单租户 | 多租户 |

### MVP 技术栈

- 后端: 现有 `polyagents/` Python + LangGraph + FastMCP
- 前端: 极简 web chat(可基于现有 `polyagents/web/`)
- 存储: 现有 SQLite + 新增 3 张表(见下)
- 模型: 现有 Anthropic key

### MVP 新增代码量估算

| 模块 | 行数 | 说明 |
|---|---|---|
| `polyagents/objects.py` | ~200 | 5 个对象的数据类 + 状态机 |
| `polyagents/runtime/session.py` | ~300 | AgentSession + ToolManifest + PermissionPolicy |
| `polyagents/evaluation/ledger.py` | ~150 | forecasts/evaluations 表 + 落库 |
| `polyagents/evaluation/report.py` | ~100 | 生成 EvaluationReport 挂到 Hypothesis |
| `polyagents/lab/backtest.py` | ~150 | 历史回放(复用现有 evaluate.py) |
| MCP 工具新增 | ~100 | create_hypothesis / run_backtest / get_report |
| 前端 chat | ~200 | 极简 UI |
| **合计** | **~1200 行** | 两周可达 |

### MVP 用户故事(验证标准)

```
用户: "最近政治类市场,我们的模型表现得怎么样?"
Agent: [调 evaluate_forecast] "过去 30 天政治类 n=42,model Brier 0.14 vs market 0.16,
       ECE 0.03,跑赢市场但样本不足。加密类 n=18,表现不明显。"
用户: "我觉得加密类在新闻事件后 LLM 更新快,帮我验证一下。"
Agent: [只读分析] "这是个值得验证的假设。要我开个 Hypothesis 去回测吗?"
       [按钮: 创建 Hypothesis]
用户: [点击]
       → Hypothesis 对象创建(state=draft, snapshot_id=...)
Agent: "已创建 Hypothesis #H001。要我用过去 90 天数据回测吗?"
用户: "跑一下。"
Agent: [调 run_backtest]
       → 回测跑完,EvaluationReport 挂到 H001
       "回测完成。加密类新闻后 2 小时窗口:
        - n=28, model Brier 0.15 vs market 0.19
        - brier_skill +21%(bootstrap 95% CI: [+8%, +33%])
        - ECE 0.04
        建议: 样本量仍偏小,但方向正向。继续 forward-test 积累样本后再评估是否 promote。"
```

**这个闭环跑通,就证明了 v0.2 架构的可行性。**

### MVP 的成功标准

1. 能从聊天自然涌现出 Hypothesis 对象(不靠菜单)。
2. Hypothesis 带 version + snapshot_id + lineage。
3. EvaluationReport 包含 baseline delta + bootstrap CI + 样本量标注。
4. 整个流程单进程 Python 跑通,无跨语言。

---

## 八、模块设计(MVP + 后续)

### A. 对象层(MVP 核心)

```python
# polyagents/objects.py
@dataclass(frozen=True)
class Market(FO):
    token_id: str
    question: str
    category: str
    outcome: str
    price: float
    metadata: dict

@dataclass(frozen=True)
class Hypothesis(FO):
    statement: str          # "加密类新闻后 LLM 更新快于市场"
    category_filter: str
    feature_set: list[str]
    prompt_version: str
    model_version: str

@dataclass(frozen=True)
class Strategy(FO):
    hypothesis_id: str      # lineage
    calibrator_id: str
    sizing_rule: dict       # {kelly_fraction: 0.25, max_edge_apy: 0.15, ...}
    risk_gates: dict

@dataclass(frozen=True)
class Position(FO):
    strategy_id: str
    market_token_id: str
    side: str
    size_usdc: float
    entry_snapshot_id: str

@dataclass(frozen=True)
class Portfolio(FO):
    positions: list[str]    # position ids
    nav_usdc: float
```

状态机:

```
draft ──► lab ──► paper ──► live ──► archived
                                   │
                                   └─► archived(回滚)
```

### B. Runtime 层(MVP 核心)

```python
# polyagents/runtime/session.py
class AgentSession:
    def __init__(self, mode, tenant="default"):
        self.id = uuid()
        self.mode = mode
        self.tools = TOOL_REGISTRY.for_mode(mode)      # 工具子集
        self.permissions = POLICY_REGISTRY.for_mode(mode)
        self.audit = AuditSink()
        self.graph = build_graph(mode)                 # LangGraph

    async def run(self, input) -> Result:
        self.audit.log("session.start", mode=self.mode)
        result = await self.graph.ainvoke(input)
        self.audit.log("session.end", result_hash=hash(result))
        return result

class ToolManifest:
    """按模式注入工具子集,并算 manifest hash(用于审计/复现)。"""
    @classmethod
    def for_mode(cls, mode) -> "ToolManifest": ...
```

三模式的工具集:

| 工具 | ask | lab | live |
|---|---|---|---|
| scan_markets | ✅ | ✅ | ❌ |
| market_snapshot | ✅ | ✅ | ✅(只读) |
| forecast_market(只读) | ✅ | ✅ | ❌ |
| evaluate_forecast | ✅ | ✅ | ✅(只读) |
| create_hypothesis | ❌ | ✅ | ❌ |
| run_backtest | ❌ | ✅ | ❌ |
| calibrate | ❌ | ✅ | ❌ |
| code_exec | ❌ | ✅(沙盒) | ❌ |
| size_position | ❌ | ✅(paper) | ✅(live,过风控) |
| paper_execute | ❌ | ✅ | ❌ |
| submit_order | ❌ | ❌ | ✅(过风控+人工确认) |
| halt | ❌ | ❌ | ✅ |

### C. 评估账本(MVP 核心,回应 feedback1 最核心批评)

```python
# polyagents/evaluation/ledger.py
# 新增 3 张表(其余沿用现有 SQLite):
#   forecasts(id, hypothesis_id, market_token_id, snapshot_id,
#             p_raw, p_cal, p_market, model_version, prompt_version,
#             calibrator_id, prediction_time)
#   evaluations(id, forecast_id_set, metric_set, ci_set,
#               baseline_delta, n_samples, generated_at)
#   promotion_events(id, object_id, from_state, to_state,
#                    evidence_eval_id, decided_by, decided_at)
```

**指标必须带统计显著性**(v0.1 缺的):

```python
@dataclass
class EvalSummary:
    n: int
    brier_model: float
    brier_market: float
    brier_delta: float
    brier_delta_ci: tuple[float, float]   # bootstrap 95% CI
    ece: float
    beats_market: bool                     # CI 下界 > 0 才为 True
    sample_adequate: bool                  # n >= min_samples 才为 True
```

### D. Lab 子系统(MVP 核心)

```python
# polyagents/lab/backtest.py
class BacktestRunner:
    """历史回放,严格 PIT。"""
    def run(self, hypothesis: Hypothesis,
            time_window: tuple[datetime, datetime]) -> EvalSummary:
        # 1. 取 window 内的 settled markets(按 hypothesis.category_filter)
        # 2. 对每个 market,重建 prediction_time 的 snapshot
        # 3. 跑 signal -> 得 p_raw
        # 4. 用当前 registry 的 calibrator -> p_cal
        # 5. 对比 p_market
        # 6. bootstrap CI
        # 7. 生成 EvalSummary 挂到 hypothesis
```

### E. pi 外壳接入(MVP 之后)

```python
# 未来:pi 作为 MCP client 接入
# polyagents/mcp_server.py 已有,只需确认这些工具暴露正确:
#   - scan_markets / market_snapshot / forecast_market / evaluate_forecast
# pi 端: configure MCP server url, 即可在 pi chat 里调用
# 不需要改 polyagents 任何核心代码
```

---

## 九、数据与存储

### MVP 新增表(其余沿用现有)

```sql
-- 对象注册表(所有 5 类对象共用)
CREATE TABLE objects (
    id TEXT PRIMARY KEY,
    type TEXT, version INTEGER, state TEXT, owner TEXT,
    snapshot_id TEXT, lineage_json TEXT, eval_summary_json TEXT,
    payload_json TEXT,                       -- 类型特定字段
    created_at TEXT, updated_at TEXT
);

-- 评估账本
CREATE TABLE forecasts (
    id TEXT PRIMARY KEY,
    hypothesis_id TEXT, market_token_id TEXT, snapshot_id TEXT,
    p_raw REAL, p_cal REAL, p_market REAL,
    model_version TEXT, prompt_version TEXT, calibrator_id TEXT,
    prediction_time TEXT,
    FOREIGN KEY (hypothesis_id) REFERENCES objects(id)
);

CREATE TABLE evaluations (
    id TEXT PRIMARY KEY,
    scope TEXT,                              -- "hypothesis:H001" / "category:crypto"
    metrics_json TEXT, ci_json TEXT,
    baseline_delta REAL, n_samples INTEGER,
    generated_at TEXT
);

CREATE TABLE promotion_events (
    id TEXT PRIMARY KEY,
    object_id TEXT, from_state TEXT, to_state TEXT,
    evidence_eval_id TEXT,
    decided_by TEXT, decided_at TEXT,        -- "user:alice" / "policy:auto-eval-gate-v1"
    FOREIGN KEY (object_id) REFERENCES objects(id)
);

-- 审计(MVP 最小:工具调用级)
CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    session_id TEXT, ts TEXT,
    event_type TEXT,                         -- "tool.call" / "promotion" / "session.start"
    payload_json TEXT
);
```

共 4 张新表(v0.1 是 17 张)。

### PIT 不变量(v0.2 的硬约束)

所有 signal 输入必须满足 `available_at <= prediction_time`。MVP 实现方式:

- 每条 news / feature / orderbook 记录带 `available_at`。
- BacktestRunner 在重建 snapshot 时过滤 `available_at <= prediction_time`。
- **加一个 `assert_point_in_time()` 断言函数,signal 节点入口强制调用**。
- MVP 写一个 PIT invariant 测试套件(人工构造未来信息,断言被拒绝),进 CI。

---

## 十、Live 模式如何无重构加入(v0.2 的关键承诺)

v0.2 承诺: **从 MVP 到 Live,不改核心架构,只加适配器**。

### Live 加入的路径

1. **新增 `LiveCLOBExecutionAdapter`**(实现现有 `ExecutionClient` 接口)。
2. **扩展 `TOOL_REGISTRY.for_mode("live")`**,加 `submit_order` / `halt`。
3. **扩展 `POLICY_REGISTRY.for_mode("live")`**,加策略白名单 + 熔断 + 人工确认。
4. **新增第三道晋升门**(paper → Live)的判断逻辑。
5. **对象状态机已有 `live` 状态**,无需改。

### 不需要改的

- 5 个对象的 schema(已有 `live` 状态)。
- AgentSession 类(只是多一种 mode 配置)。
- LangGraph 图结构(执行节点换 adapter)。
- 评估账本(Live 和 paper 共用,只是多记 execution_quality)。
- MCP 工具接口(只是多几个工具)。

**这就是 Claude 式设计的回报**: 复杂度在对象状态机里,不在架构层。加 Live = 加 adapter + 加配置,不是加架构。

---

## 十一、多市场扩展如何无重构加入

v0.2 承诺: **从 Polymarket 到其他市场,不改对象模型,只加 fetcher 适配器**。

### 扩展点

```python
class MarketFetcher(Protocol):
    """所有市场数据源的统一接口。"""
    def scan(self, criteria) -> list[Market]: ...
    def snapshot(self, token_id) -> MarketSnapshot: ...
    def orderbook(self, token_id) -> OrderBook: ...

# 现有: PolymarketFetcher
# 未来: KalshiFetcher / PredictItFetcher / SportsbookFetcher
```

新市场类型 = 新增一个 Fetcher 实现 + 注册到 `FETCHER_REGISTRY`。**对象模型、评估管道、UI、状态机全部不动。** 评估时按 `market.metadata.source` 分层。

---

## 十二、路线图

### Milestone 0: MVP 闭环(2 周)

- 5 个对象 schema + 状态机
- AgentSession + ToolManifest + PermissionPolicy(只实现 ask/lab)
- forecasts / evaluations 表 + 落库
- BacktestRunner(历史回放)
- 1 道晋升门(Ask → Hypothesis)
- 极简 web chat
- **验收**: 跑通第七节的用户故事

### Milestone 1: 评估闭环加固(1 周)

- bootstrap CI 实现
- PIT invariant 测试套件 + CI
- EvaluationReport markdown 渲染
- 类别分层报告
- **验收**: 人工构造未来信息,系统拒绝;所有指标带 CI

### Milestone 2: 校准器体系(1-2 周)

- isotonic calibrator 实现
- calibrator registry(version + fit_window + holdout_ece)
- 第二道晋升门(Hypothesis → paper Strategy)
- calibration report
- **验收**: isotonic 在 holdout 上 ECE 优于 raw;决策使用 p_cal

### Milestone 3: Paper Execution(1-2 周)

- PaperExecutor walk-the-book
- post-fill adverse selection 指标
- capital cost(资金锁定成本)
- Strategy → Position 流转
- **验收**: 薄簿市场产生明显滑点;P&L 扣除资金成本

### Milestone 4: Forward-test + 自动化(2 周)

- forward-test ledger(append-only)
- 定时扫描 + 预测 + 记录(pi 或 cron)
- session resume
- 第三道晋升门(paper → Live,Live 仍未开)
- **验收**: 30 天 politics forward-test 报告

### Milestone 5: pi 外壳接入(可选,1 周)

- 确认 polyagents MCP server 工具集
- pi 配置 MCP server url
- 验证 compaction / session 体验
- **验收**: pi chat 里能完成 Ask 模式全流程

### Milestone 6: Limited Live Gate(2 周)

- LiveCLOBExecutionAdapter
- live permission policy(白名单 + 限额 + 人工确认)
- 熔断器
- Live 审计加强
- **验收**: live 前置检查失败时无法下单

### 累计: ~10-12 周到 Limited Live

(vs v0.1 的 8 个 milestone、6-12 个月、且依赖未验证的 pi 地基)

---

## 十三、关键开放问题(v0.1 的 8 个 + v0.2 新增)

### 继承自 v0.1(给出推荐答案)

| # | 问题 | v0.2 推荐 |
|---|---|---|
| 1 | 新闻源 | Claude 当打分器 + Tavily 补原文(MVP 先不接,用现有数据) |
| 2 | taxonomy | 先用 Polymarket 原生 category,alpha 层加自定义 tag |
| 3 | qlib | 仅作外部 benchmark,不接(二元市场 ≠ 日频股票) |
| 4 | 校准窗口 | 全局滚动 isotonic,先 90 天窗口 |
| 5 | lesson 注入 | 按类别 top-k + 衰减,先不进 prompt,只进研究报告 |
| 6 | pi SDK 能力边界 | **已回答**: 不作地基,作 chat 外壳 |
| 7 | Worker backend | LocalSandboxBackend 默认,其他留接口 |
| 8 | 多租户 | 单租户先跑,字段预留 |

### v0.2 新增

| # | 问题 | 状态 |
|---|---|---|
| 9 | MVP 用现有 web chat 还是直接接 pi? | **推荐**: 现有 web chat(pi 接入是 Milestone 5,不阻塞 MVP) |
| 10 | code_exec 工具的沙盒边界? | MVP 用 subprocess + cwd 限制 + 超时;后续上 Docker |
| 11 | 对象存储用 SQLite JSON 列还是分表? | MVP 用单表 JSON(v0.1 的 17 表是 over-engineering);量大再分 |
| 12 | bootstrap CI 的 resample 次数? | 1000 次(MVP),可配置 |

---

## 十四、最终形态

AIHF 最终应当是一个**金融版 Claude**:

- **一个入口**: 用户在 chat 里问任何金融问题,agent 调用确定性工具回答。
- **对象涌现**: 聊出的好想法能"浮"成 Hypothesis,带版本、带快照、带评估。
- **晋升闸门**: 从想法到实盘,经过显式的、带证据的、可审计的 promote。
- **底层引擎**: LangGraph + Anthropic SDK 是地基,pi 是可选 chat 外壳,AgentSession 是薄层纪律官。
- **确定性工具**: 是护城河,不是 commodity。Kelly/ECE/walk-the-book/calibration/PIT 全是自己造。
- **多市场通用**: 5 个对象 + 一套工具,新市场 = 新 fetcher。
- **Live 无重构**: 加 adapter + 加配置,不改架构。
- **系统持续回答同一个问题**: 我们的 p_calibrated 是否稳定跑赢市场?只有答"是",才进实盘。

---

## 附录 A: v0.1 → v0.2 的每条改进对应关系

| v0.1 的问题 | v0.2 的改进 |
|---|---|
| pi 作为未验证地基 | pi 降级为可选 chat 外壳,地基是 LangGraph |
| 8 个 milestone 过重 | 3 道晋升门 + 2 周 MVP |
| aihf/pi/workers 三层新架构 | 一条流水线 + 三种模式,无新增架构层 |
| 4 个 coding agent adapter | Lab 内一个 code_exec,backend 可换 |
| 17 张表 | 4 张新表(对象/forecast/evaluation/promotion) |
| TS/Python 混搭 | 纯 Python,pi 经 MCP 接入 |
| 缺统计显著性 | 所有 baseline delta 带 bootstrap CI |
| 缺 PIT 可证伪测试 | PIT invariant 测试套件进 CI |
| 缺校准器训练协议 | registry 存 fit_window + holdout_ece + manifest_hash |
| paper 缺 adverse selection | 加 post-fill adverse selection + capital cost |
| Live 是独立大模块 | Live = 加 adapter,不改核心 |
| 多市场需重写 | 5 对象 + fetcher 接口,新市场 = 新 fetcher |
| lesson 过滤未定义 | 确定性规则(分位 + 类别校准达标),非 LLM 判断 |
| milestone 缺 kill criteria | 每阶段加停止条件(见下) |

## 附录 B: 各 Milestone 的 kill criteria

| Milestone | 停止条件 |
|---|---|
| M0 | 若 2 周内连 Ask→Hypothesis 闭环都跑不通,说明对象模型设计有误,回到设计 |
| M1 | 若 PIT 测试发现现有数据已大面积泄漏,停止后续,回去修数据采集 |
| M2 | 若所有校准器在 holdout 上都跑输 market baseline,**承认当前 LLM signal 没 edge**,回到 prompt/特征设计,不硬上 M3 |
| M3 | 若 paper P&L 扣除资金成本和滑点后为负,停止 forward-test |
| M4 | 若 30 天 forward-test Brier 输市场 > 0.02,不进 M5 |
| M6 | Live 永远默认关闭;任何 gate 失败立即 halt |

## 附录 C: 与现有 polyagents 代码的对接

现有代码**大部分保留**,v0.2 是"在外面包一层对象 + 评估账本":

| 现有模块 | v0.2 角色 | 改动 |
|---|---|---|
| `polyagents/graph/` | Lab 模式的回测引擎核心 | 不改,被 BacktestRunner 调用 |
| `polyagents/agents/` | signal/decision/reflection | 不改,被图节点调用 |
| `polyagents/agents/calibration.py` | 校准器实现 | 加 isotonic + registry |
| `polyagents/evaluation/` | 评估指标 | 加 ledger + CI + 落库 |
| `polyagents/execution/` | paper/live 执行 | 加 walk-the-book 细节 |
| `polyagents/storage/db.py` | SQLite | 加 4 张新表 |
| `polyagents/mcp_server.py` | MCP 暴露 | 加新工具(create_hypothesis 等) |
| `polyagents/web/` | Ask 模式 UI | 改造为对象感知的 chat |
| **新增** `polyagents/objects.py` | 5 对象 + 状态机 | 新建 |
| **新增** `polyagents/runtime/session.py` | AgentSession 薄层 | 新建 |
| **新增** `polyagents/lab/backtest.py` | 回测 runner | 新建 |

**核心判断**: v0.2 不是重写,是"给现有引擎加一个对象/评估/晋升的外壳"。现有 LangGraph 四层流水线是地基,不动。
