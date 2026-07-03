# Ask 回答架构与流程

> 2026-06-25 · 现状汇总(分支 `feat/ask-router`)
> 配套: `ask-module-PRD.md` · `ask-router-PRD.md` · `ask-upload-files-PRD.md`
> 一句话: **Ask = 只读金融研究对话台**——问题先**分类路由**到合适的回答模式,Domain 用 LangGraph 编排领域工具,General 交给通用 agent(Claude/pi),全程**只读 + 审计**,好想法可**涌现成 Hypothesis** 一键进 Lab。

---

## 一、端到端流程图

```
浏览器 (static/index.html)
  │  POST /api/chat  { messages, skills[], model, mode(auto|domain|general), attachments[] }
  ▼
server._stream
  │  ① AgentSession("ask")  ← mode 决定:只读工具子集 + 权限 + 审计
  │  ② classify(最后一条用户消息)        [router.py]
  │       手动 mode > 关键词规则 > Haiku 兜底(默认 domain)
  │       → (route, by)；SSE 回 {type:"route"}；审计 route.decided
  ▼
  路由分发
  ├─ route=domain ───────► build_agent(只读领域工具 + 选中 skill)   [agent.py]
  │                          = LangGraph create_react_agent(ReAct 图)
  │
  └─ route=general ──┬─ backend=claude(默认)► build_general_agent(Claude + web_search)
                     └─ backend=devbox ────► 中继 Alpha DevBox/pi 的 AI SDK SSE  [general_backend.py]
  ▼
  附件注入(若有):把上传文件文本/图片块并进最后一条用户消息  [uploads.py]
  ▼
  流式执行(LangGraph: agent.astream_events / devbox: HTTP SSE 中继)
  ▼
  SSE 回吐 → 浏览器逐字渲染 + 工具 chip + 路由小标 + Hypothesis 卡
```

---

## 二、为什么先路由(核心设计)

Ask 问题发散度高,**一个图通吃不合适**。所以 Ask 内部加一层 **Router → Handler**:
- **确定性领域问题**(扫市场/估概率/评估)→ Domain(LangGraph + 领域工具)。
- **泛/开放/编码问题**(解释概念/写代码/查外部)→ General(通用 agent)。
- Router 与 Handler 解耦:**加问题类型 = 加 Handler,不改主干**。

### 分类(便宜优先)[router.py]
1. **手动**:composer 模式胶囊选了 `Domain/General` → 直接用(`by=manual`)。
2. **规则(零 token)**:领域关键词(market/概率/brier/evaluate/假设/校准…)→ domain;泛关键词(写代码/解释/什么是/翻译…)→ general;**重叠时 domain 优先**。
3. **Haiku 兜底**:规则判不准 → 一次便宜 Haiku 分类;失败默认 `domain`(本应用主业)。

---

## 三、两种回答模式(Handlers)

| 模式 | 处理 | 实现 | 工具 |
|---|---|---|---|
| **Domain** | 市场/数据/评估/相似市场/提假设 | `build_agent(readonly=True)` = LangGraph ReAct 图 | 15 个**只读**领域工具 + propose_hypothesis |
| **General** | 概念/编码/翻译/外部信息 | `build_general_agent`(Claude)**或** `stream_devbox_general`(pi) | 仅 `web_search`(Tavily,无 key 降级) |

- **Domain 图的循环**:agent 节点(Claude 决定直接答 / 调工具)⇄ tools 节点,直到无需工具 → END。
- **General(claude)**:同样是 ReAct 图,但只挂 web_search,无领域工具。
- **General(devbox/pi)**:不走我们的 LangGraph——POST 到 `Alpha DevBox /api/devbox/chat`,**中继它的 AI SDK SSE**;env `ASK_GENERAL_BACKEND=devbox` + `DEVBOX_BASE_URL` 激活,不可达自动降级回 Claude。

---

## 四、贯穿全程的纪律:AgentSession("ask")

每次对话跑成一个 **mode-scoped 会话** [runtime/session.py]:一个 `mode` 字段决定三件事——
- **工具范围**:`readonly=True` → 去掉所有写/交易工具(`WRITE_TOOLS`),**Ask 内绝不下单/改组合**(硬保证,不靠提示词)。
- **权限**:`PermissionPolicy`(ask 不可交易、可 promote)。
- **审计强度**:每步落 `audit_events`。

### 审计事件(可在 Live 视图 Audit 面板看)
`session.start` → `route.decided` →(general 时)`general.backend` →（每次工具）`tool.call` → `session.end`;晋升另有 `object.create`(gate1)/`promotion`。

---

## 五、几个增强能力

- **模型选择**:composer 选 Sonnet/Opus/Haiku → `body.model` → `resolve_model()` 白名单校验(乱传回退默认),逐条可换。
- **文件上传**[uploads.py]:拖拽/附件 文本/PDF/图片 → 抽取(文本/pypdf/base64)→ `/api/upload` 缓存 → 发送时按 `attachments[]` 注入最后一条用户消息(文本块 / 多模态图片块)。
- **涌现 Hypothesis**(Domain 专属):agent 调只读 `propose_hypothesis` → 前端渲染 **Hypothesis 卡片** → 用户点 **Promote to Lab**(= gate 1)→ `POST /api/objects` 真建对象进 Lab。
- **会话历史**:中间列 Today 分组卡片(内存版,刷新即清)。

---

## 六、SSE 事件类型(server → 浏览器)

| 事件 | 含义 |
|---|---|
| `route` | 本次分类结果 `{route, by}`(气泡上小标) |
| `token` | 流式文本增量 |
| `tool` / `tool_result` | 工具调用开始/结束(绿色 chip);`propose_hypothesis` 特判渲染成卡片 |
| `done` / `error` | 结束 / 出错 |

---

## 七、文件地图

```
polyagents/web/
  server.py          /api/chat → _stream:会话 + 路由 + 附件 + 流式 + 审计
  router.py          classify(规则 + Haiku 兜底)
  agent.py           build_agent(Domain,只读工具+skill) · build_general_agent · web_search · propose_hypothesis · ASK_MODELS/resolve_model
  general_backend.py General 后端选择 + Alpha DevBox/pi 的 SSE 中继
  uploads.py         文件抽取 + 多模态消息组装
  static/index.html  composer(＋菜单/模式胶囊/模型选择/拖拽上传)+ 流式渲染 + Hypothesis 卡
polyagents/runtime/session.py   AgentSession + PermissionPolicy(mode→工具/权限/审计)
polyagents/storage/audit_store  audit_events 落库
```

---

## 八、设计要点回顾(对齐 v0.2 PRD)
- §二 Ask 只读:工具子集 + AgentSession 双重保证。
- §三/§五 对象涌现 + gate 1:propose_hypothesis → Promote。
- §八-B mode-scoped session;§九 audit_events。
- 路由 + 多回答模式:本轮新增(`ask-router-PRD.md`),P2 通用后端可换 pi。
