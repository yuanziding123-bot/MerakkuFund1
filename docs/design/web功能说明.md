# polyagents Web — 功能说明

> 更新: 2026-06-09 · 仓库: github.com/yuanziding123-bot/polyagent
> 配套: [架构说明.md](架构说明.md) · [项目状态与待决策.md](项目状态与待决策.md)

一个自托管的 AI 交易聊天平台(类似 aiusd.ai/chat):**Claude 负责推理,polyagents 工具负责取数/算数**,全程**纸面交易**。

## 启动 / 访问

```bash
cd C:\polyagents
C:\polymarket\.venv\Scripts\python.exe -m polyagents.web
```
- 地址:**http://127.0.0.1:8000/**(本机)
- 需要 `.env` 里的 `ANTHROPIC_API_KEY`
- 模型:`claude-sonnet-4-6`(可用 `ANTHROPIC_MODEL` 改成 Haiku 省钱 / Opus 提质)

---

## 界面总览

```
┌ 顶栏: ◆ polyagents | [Chat] [Market] [Backtest]        ● paper mode ┐
├ 左侧 ─────────────┬──────── 中间 ────────┬─ 右侧 ──────────────────┤
│ ＋ New chat        │  Chat / Market /      │ Paper equity            │
│ SKILLS(可勾选)    │  Backtest 视图        │  Cash / Exposure / P&L  │
│ MCP SERVERS(只读) │                       │  Open positions(自动刷新)│
│ Chat history       │                       │                         │
└────────────────────┴───────────────────────┴─────────────────────────┘
```

---

## 一、顶部三个标签页

### 💬 Chat(对话交易)
- 和 Claude 对话,它会**流式回复** + 实时显示**工具调用**(绿色 ⚙ chip)。
- Claude 推理 + 调用 18 个确定性工具(见下);**纸面下单**会更新右侧组合。
- 4 个快捷提示按钮(扫市场 / 分析市场 / 看组合 / 校准如何)。

### 📈 Market(实时行情)
- **实时行情表**(`/api/markets`):每个市场一行,显示 **YES/NO 价格、24h 成交量、流动性、点差、到期**,按成交量排序。
- **每 20 秒自动刷新** + 手动 ⟳。
- **点某一行 → 自动切到 Chat 并预填"分析这个市场"**,行情和分析打通。

### 🧪 Backtest(qlib 回测可视化)
- **"▶ run backtest"** → 调 qlib venv 跑 因子→模型→回测(防泄漏时间切分)。
- 显示:**判定徽章**(✅ edge / ❌ noise)、**指标卡**(Accuracy / IC / Sharpe / Total return / Trades)、**权益曲线图**(SVG)。

---

## 二、左侧栏

### ＋ New chat
清空当前对话,重新开始。

### SKILLS(可勾选,驱动 agent)
勾选哪个,哪个的工作流就成为 agent 的系统提示。**加新 skill = 丢一个 `skills/<名>/SKILL.md`,自动出现。**

| Skill | 作用 |
|---|---|
| **polymarket-trading** | 追踪资金/微结构 → 估概率 → Kelly 风控 → 纸面交易 |
| **market-research** | 只研究不交易:数据 + RAG 相似市场 + 解读 |
| **cross-market-arb** | 对比交易所现价 vs Polymarket 隐含概率,找滞后套利 |

### MCP SERVERS(只读,查接了哪些)
列出所有注册的 MCP server,点击展开工具名。绿色 `in chat` = 当前 chat agent 在用。

| MCP | 工具数 | 在 chat | 传输 |
|---|---|---|---|
| polyagents | 9 | ✅ | in-process |
| crypto | 3 | ✅ | stdio |
| polydata | 3 | ✅ | stdio |
| compliance | 3 | ✅ | stdio |
| qlib-backtest | 2 | ⬜(走 Backtest 标签) | qlib venv |
| polymarket-docs | 2 | ⬜(远程) | http |

### Chat history
当前会话(后续可扩展成多会话历史)。

---

## 三、右侧栏 — 纸面组合

- **Paper equity**(现金 + 持仓市值)
- **Cash / Exposure / Realised P&L**(盈亏绿涨红跌)
- **Open positions**(每个持仓:市场 + 股数 + 均价)
- **每轮对话后自动刷新** + 手动 ⟳。

---

## 四、Chat 里能用的 18 个工具

| 来源 | 工具 | 干什么 |
|---|---|---|
| **polyagents** | `scan_markets` | 扫活跃市场 |
| | `market_snapshot` | L1 全量数据 + 因子 |
| | `find_similar_markets` | RAG 检索相似历史市场 |
| | `size_position` | 校准 + Kelly + 风控/APY 闸门 → 仓位 |
| | `paper_execute` | 纸面下单(走订单簿滑点 + 熔断) |
| | `portfolio_status` / `settle_markets` / `pnl_report` / `evaluation_report` | 组合 / 结算 / 战绩 / 校准评估 |
| **crypto** | `crypto_price` / `crypto_24h` / `crypto_klines` | 交易所现价/24h/K线(Coinbase) |
| **polydata** | `list_events` / `recent_trades` / `price_history` | 事件 / 成交流向 / 历史价格 |
| **compliance** | `verify_trade_math` / `audit_log` / `risk_limits` | 交易数学校验 / 审计 / 限额 |

---

## 五、示例提问

| 想做 | 这样说 |
|---|---|
| 扫机会 | "Scan the most active markets" |
| 深入分析 | "Analyse a liquid market and estimate a fair probability" |
| 跨市场套利 | "找一个被错误定价的加密市场,对比交易所现价"(勾选 cross-market-arb) |
| 看事件 | "列出当前热门 event" |
| 资金流向 | "这个市场最近 24h 的买卖流向?" |
| 数学校验 | "校验这笔交易的 edge/Kelly 对不对" |
| 看组合 | "Show my paper portfolio" |
| 评估有没有 alpha | "How well-calibrated are my predictions? 有没有跑赢市场?" |

---

## 六、API 端点(后端)

| 端点 | 作用 |
|---|---|
| `GET /` | 聊天 UI |
| `POST /api/chat` | SSE 流式:token / tool / done(body 带 messages + 选中的 skills) |
| `GET /api/skills` | 技能列表(左侧选择器) |
| `GET /api/mcp` | MCP server 列表(左侧面板) |
| `GET /api/portfolio` | 纸面组合(右侧面板) |
| `GET /api/markets` | 实时行情(Market 标签) |
| `GET /api/backtest` | qlib 回测(Backtest 标签,跨 venv 子进程) |

---

## 七、重要说明

- **纸面交易**:右上角 `● paper mode`,不下真单(实盘要显式开 `execution_mode=live` + 私钥)。
- **Claude 计费**:走 `.env` 的 `ANTHROPIC_API_KEY`,**按 token 扣 Anthropic API 费**;和写代码的 Claude Code 是两个独立账户。省钱可换 Haiku。
- **本机访问**:`127.0.0.1` 只有本机能开;要局域网/公网访问需改监听地址或部署。
- **数字不编造**:价格、现价、仓位都是工具算的喂给 Claude,skill 明令禁止编造。
- **不骗自己**:回测/评估用真实结算 + 防泄漏切分,没 edge 就如实判"噪声"。

---

## 八、文件位置(都在 polyagents 内)

```
polyagents/web/
  agent.py        Claude ReAct agent + 18 工具 + skills 系统提示 + MCP 注册表
  server.py       FastAPI + SSE + 7 个 API 端点
  static/index.html  全部 UI(单文件,零构建)
skills/<id>/SKILL.md   技能(polymarket-trading / market-research / cross-market-arb)
polyagents/mcp_servers/  MCP server(crypto / polydata / compliance / qlib_backtest)
```
