# 通用 Agent Loop 内核 PRD(P1 MVP)

> 2026-06-25 · 分支 `feat/agent-loop-kernel`
> 来自 mentor:借鉴 coding agent 框架,自建**通用 agent loop**,根据目标自动判断调哪个 agent、走什么流程,而不只靠 LangGraph 编排;"一个 mode 对应一种 loop 形式,所有问题靠 loop 解决,关键是框架自动识别并调用相应策略,无需穷举"。

---

## 一、目标

把"该调哪个 agent、走哪条路"从**写死的流水线 / LangGraph 静态图**,变成**运行时按目标自动推导**。例:用户说"拉某事件历史数据做 backtest" → 自动 `data agent → backtest agent`,**不走 signal/risk 等多余路径**。

## 二、核心机制:目标驱动的反向链规划(避免穷举的关键)

每个能力声明 **前置条件(preconditions)+ 产出(effects)**;从**目标**反向链式推导出**最短能力序列**:

```
Capability = { name, 描述, preconditions, effects, cost, run(ctx)->新facts }

data_agent      precond:{event}    effect:{history}
backtest_agent  precond:{history}  effect:{backtest_report}
signal_agent    precond:{history}  effect:{signal}      ← 存在但与 backtest 目标无关
risk_agent      precond:{signal}   effect:{decision}
```

目标 = 产出 `backtest_report` → 反向链:`backtest_report ← backtest_agent(需 history) ← data_agent(需 event,已给)` → 路径 = **data_agent → backtest_agent**。signal/risk 因不在目标可达链上,**不会被选**。
→ 这就是"自动调 data agent、不走多余路径",且**加新能力 = 声明 precond/effect,planner 自动纳入,无需改主干、无需枚举问题**。

## 三、内核组件(自建,不绑 LangGraph)

```
请求 → [intent.recognize → Goal] → AgentLoop:
        每步 → Planner.next(ctx) 选下一个能力(或 STOP) → 执行 → 写黑板 → 判目标达成
```

| 组件 | 文件 | 职责 |
|---|---|---|
| `Capability` | `kernel/core.py` | 能力单元(precond/effect/run) |
| `Context` / `Goal` | `kernel/core.py` | 黑板(facts)+ 目标(target effects + 初始 facts)+ trace |
| **Planner**(`next_capability`) | `kernel/core.py` | 反向可达 + 前置满足 → 选最便宜的可运行能力;无则 STOP(P2 接 LLM 兜底) |
| `AgentLoop` | `kernel/core.py` | perceive→plan→act→observe,有最大步数 + 审计 + on_event |
| `intent.recognize` | `kernel/intent.py` | 请求 → Goal(规则版;P2 接 LLM) |
| 能力注册表 | `kernel/capabilities.py` | data/backtest/signal/risk 能力(依赖注入,可接真实组件) |

## 四、"一个 mode 一种 loop 形式"

同一内核 + 不同**能力子集**:Ask(只读问答)/ Research(+data/backtest/eval)/ Trade(+signal/risk/exec 过风控)。换注册表 = 换 loop 形式,**不为每类问题写流水线**。

## 五、与现有代码 / LangGraph 关系

- **演进 `orchestration/`**:`SubAgent`→`Capability`(加 precond/effect),`Router`/`LLMRouter`→`Planner`。
- **LangGraph 退化为一个 Capability**(通用问答),pi/devbox 同理 —— 内核是总编排,LangGraph 只是其中一种能力。

## 六、P1 范围(本 PR)

- ✅ `kernel/core.py`:Capability + Context/Goal + 反向链 Planner + AgentLoop(审计/事件/步数上限)。
- ✅ `kernel/capabilities.py`:data/backtest/signal/risk 能力(DI)+ 注册表构造器。
- ✅ `kernel/intent.py`:规则版 recognize(backtest/trade/evaluate/ask)。
- ✅ 契约测试:backtest 目标 → 仅 data+backtest(跳过 signal/risk)、目标已达成不动、卡死即停、步数上限、**加能力自动纳入**、审计落点。
- ⬜ 不在 P1:LLM Planner 兜底、真实 event→市场端到端、把 LangGraph/Strategy 接成 Capability(见 P2)、trade/live mode 风控门。

## 六·P2(已实现)

- **LLM Planner 兜底** `kernel/llm_planner.py`:确定性 planner 卡死(目标模糊)时,把"目标 + 已知 facts + 当前可运行能力"给模型选下一个(或 stop);`AgentLoop(fallback_planner=...)` 接入,确定性优先、LLM 只兜底。
- **LangGraph/Strategy → Capability**:`answer_capability`(把 Ask ReAct agent 当"问答"能力)、`strategy_capability`(把 data→signal→risk Supervisor 当一个能力)——**LangGraph 退化为内核里的一种能力**。
- **真实端到端** `kernel/wiring.py::default_registry()`:data(取已结算市场)+ backtest(BacktestRunner.replay)+ answer + strategy。实测:backtest 目标 → **仅走 data→backtest**(注册表里的 answer/strategy 未被调),产出真实报告(6 市场,NO EDGE,诚实)。
- 测试 `tests/test_kernel_p2.py`(5):LLM planner 选择/停止/未知、loop 用 LLM 兜底、answer/strategy 能力在 loop 内跑通。

## 七、验收

1. `recognize("拉事件X历史数据做backtest")` → Goal(targets={backtest_report}, facts={event:X})。
2. AgentLoop 跑出 trace = `[data_agent, backtest_agent]`,facts 含 `backtest_report`;signal/risk **未执行**。
3. 给注册表加一个新能力(声明 precond/effect)→ 若在目标可达链上,planner 自动调用,无需改 loop。
4. 本地 `pytest` 全绿。
