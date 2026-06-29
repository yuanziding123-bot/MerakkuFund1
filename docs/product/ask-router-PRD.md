# Ask · 问题路由 + 多回答模式 PRD

> 版本 v0.1 · 2026-06-25 · 分支 `feat/ask-router`
> 上位: `ask-module-PRD.md` · 设计讨论见对话(2026-06-25)
> 已定: P1 通用 agent=Claude(无领域工具)+ Tavily 搜索;分类=规则 + Haiku 兜底;P2 通用后端换 pi.dev coding agent

---

## 一、问题

Ask 问题发散度高,**一个 ReAct 图通吃不合适**:确定性领域问题该用 LangGraph 编排领域工具/skill;泛/开放/编码问题该交给更通用的 agent。需要**先分类、再路由到对应回答模式**。

## 二、架构:Router → Handler(Ask 内部一层)

```
问题 → [Router 分类] → Domain / General(/ Skill)Handler → 统一在 AgentSession("ask"):只读 + 审计
```
- **Router 与 Handler 解耦**,加问题类型 = 加 Handler,不改主干。
- 全程仍只读(General 也不给交易/写工具),Router 决定也落审计。

## 三、回答模式(P1)

| 模式 | 处理 | 实现 |
|---|---|---|
| **Domain** | 扫市场/估概率/评估/相似市场/提假设 | 现有 `build_agent`(只读领域工具 + 选中 skill) |
| **General** | 解释概念/写代码/查外部信息/泛研究 | **新** `build_general_agent`:Claude(无领域工具)+ **web_search(Tavily)**,只读 |

> Skill 路由 P1 先并入 Domain(skill 选择已存在);独立 Skill Handler 留后续。

## 四、分类(便宜优先)

1. **显式**:用户在 composer 选了 `Domain/General`(手动)→ 直接用。
2. **规则(零 token)**:领域关键词(market/概率/brier/evaluate/假设/校准…)→ Domain;泛关键词(写代码/解释/什么是/翻译/总结…)→ General。**重叠时 Domain 优先**(领域是专长能力)。
3. **Haiku 兜底**:规则判不准 → 一次 Haiku 轻量分类(`domain|general`);解析失败 **默认 Domain**(本应用主业是市场研究)。

## 五、后端契约

- `POST /api/chat` body 增 `mode`:`auto`(默认)/`domain`/`general`。
- `_stream`:分类 → 选 Handler → 建 agent → 流式;新增 SSE `route` 事件 `{route, by:rule|llm|manual}`;审计 `route.decided`。
- `web_search(query)` 工具:Tavily(`TAVILY_API_KEY`),**无 key 优雅降级**(提示用通用知识答),只读。

## 六、UX

composer 加**模式胶囊**:`Auto`(默认,自动分类)/`Domain`/`General`。气泡上小标 `· routed: general`(透明可审计)。

## 七、范围外 / 分期
- **P2**:General 后端换 pi.dev coding agent(MCP 集成,单独 PR)。
- Skill 独立 Handler;web_search 结果引用渲染;分类结果缓存。

## 八、验收
1. 领域问题 → Domain(能调市场工具);泛问题 → General(只有 web_search,无领域工具)。
2. 手动选模式覆盖自动;规则命中不烧 token;模糊才调 Haiku。
3. General 仍只读,无交易/写工具。
4. SSE 回 route、审计落 route.decided。
5. 本地 pytest 全绿 + 目测;无 Tavily key 时 General 仍可答(降级)。
