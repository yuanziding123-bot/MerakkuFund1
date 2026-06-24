# Ask 模块 · 产品 PRD

> 版本 v0.1 · 2026-06-24 · 分支 `feature/ask-module`
> 上位文档: `docs/product/AIHF产品PRD与技术架构v0.2.md`(Ask = 三种副作用模式之"只读")
> 设计参考: **demo.openalice.ai** 的 Ask 界面 + `docs/prototypes/prototype-v0.2.html`(暖色主题)
> 流程约定: PRD → 高保真 → 测试用例 → fork→本地通过→merge(不直推 main)

---

## 一、定位与边界

**Ask 是平台的默认入口与只读对话模式。** 用户在一个对话框里问任何金融/市场问题,agent 调用确定性工具(取数/算数/检索)回答、解释、对比;**永不下单、不写实盘、不改组合**。

副作用边界(硬约束,来自 v0.2 §二):
- **只读工具子集**:scan / snapshot / forecast(只读)/ evaluate / 检索相似市场 / 行情解释。
- **唯一的"写"动作 = 把一个想法提升为 Hypothesis**(进 Lab,带 PIT 快照),且必须**用户显式点击**(gate 1)。Ask 自己不会自动建对象、不下注。
- 任何交易/纸面执行入口在 Ask 里**不可见**(那是 Lab/Live 的事)。

一句话:**Ask = 金融版 Claude 的聊天首页——问、查、解释、提假设,到此为止。**

---

## 二、目标用户与核心场景

| 用户 | 场景 | 期望 |
|---|---|---|
| 研究者 / 交易员 | "最近政治类市场我们的 p_cal 表现怎样?" | 调 evaluate,给带 CI 的解读 |
| 同上 | "我觉得 crypto 新闻后 LLM 更新快,能验证吗?" | 起草一个可回测的 Hypothesis 卡片,等用户点 Promote |
| 新用户 | 打开就不知道能干嘛 | 空状态 hero + 建议 chips 引导第一步 |
| 任意 | 连续追问、回看历史会话 | 会话历史可检索、可载回 |

---

## 三、信息架构与布局

延续现有三栏 IA(rail / list / workspace),Ask 模式下:

```
┌ rail(modes/library)┬ list: Ask 会话历史 ┬ workspace: 对话区 ────────────┐
│ ● Ask(高亮)        │ 搜索 + Today 分组   │ 顶: 面包屑 Ask·read-only      │
│   Lab / Live        │ ＋ New chat         │ 中: 空状态 hero / 消息流       │
│   Objects/Markets/  │ <会话卡片>          │ 底: 大输入框 + 工具条          │
│   Evaluation        │                     │                               │
└─────────────────────┴─────────────────────┴───────────────────────────────┘
```

- Ask 模式**隐藏右侧持仓栏**(只读,不需要组合)。
- 主题:对齐 openalice/prototype-v0.2 的**暖色 + 细网格底**(当前为深色,换肤在第 2 步高保真里定)。

---

## 四、组件清单(对齐 openalice,标注一期/二期)

### A. 空状态 Hero(首屏,无消息时)
- 居中大标题(例:**"What do you want to look into?"** / 中文"今天想研究什么?")
- 副标题(例:"Ask Alice to research, analyze, or trade — your market data and tools are on tap.")
- 下方紧接输入框 + **建议 chips**("Try asking …",3–4 个,点击填入并发送)。
- 一期:✅(我们已有简化版空状态,需重做成 hero 样式)

### B. 输入框 Composer(核心)
大圆角输入区 + 底部工具条:

| 元素 | openalice | 我们的映射 | 期 |
|---|---|---|---|
| 多行 textarea | "Ask Alice…" | "Ask…" 占位,Enter 发送/Shift+Enter 换行 | 一期 ✅ |
| **＋ 菜单** | Upload files / Skills › / Projects › | 见 C | 一期(Skills)/ 二期 |
| **模式胶囊** | 💬 Chat | Ask 内固定 Chat;预留扩展 | 一期(展示) |
| **模型/agent 选择** | ⚙ Claude Code ▾ | 模型选择 ▾(Sonnet/Opus/Haiku,经 `ANTHROPIC_MODEL`) | 一期 ✅ |
| 📎 附件 | paperclip | = Upload files | 二期 |
| ↑ 发送 | 圆形按钮 | Send | 一期 ✅ |

### C. "＋" 菜单(二级)
- **Upload files** — 上传文件给 agent 参考。**二期**(后端无文件管线)。先灰显/隐藏。
- **Skills ›** — 二级展开 skill 选择(勾选驱动 agent 系统提示)。**一期 ✅**(已有 in-composer 选择器,改成二级菜单形态)。
- **Projects ›** — 会话/工作区分组。**二期**(或一期映射为"会话历史"分组,先只读)。

### D. 对话区(有消息时)
- 消息气泡(user / ai),ai 流式输出。
- **工具调用 chip**(绿色 ⚙ tool):实时显示 agent 调了哪个只读工具。
- **涌现 artifact 卡片**:agent 起草的 **Hypothesis 卡**(类型标签 + id + snapshot + 成功标准 + `Promote to Lab` / `Edit` / `Dismiss`)。点 Promote = gate 1。**一期目标**(对接已有 `/api/objects` 创建)。

### E. 会话历史(中间 list 列)
- 顶部标题 "Ask" + 搜索框;＋ New chat;按 Today 分组的会话卡片;点击载回;搜索过滤。**一期 ✅**(已实现,内存版)。

---

## 五、与后端对接

| 能力 | 端点 | 状态 |
|---|---|---|
| 流式对话 + 工具事件 | `POST /api/chat`(SSE token/tool) | 已有 ✅ |
| skills 列表 / 选择 | `GET /api/skills` + body.skills | 已有 ✅ |
| 模型选择 | body 增加 `model` 字段;后端按需覆盖 `ANTHROPIC_MODEL` | **一期新增**(小改) |
| 涌现 Hypothesis | `POST /api/objects`(已有) | 接线 |
| Upload files | — | 二期(需文件管线) |
| Projects | — | 二期(或复用会话历史) |

只读保证:Ask agent 绑定的工具集**不含** `paper_execute`/`submit_order`;`run_trading_strategy` 等写动作不在 Ask 暴露。

---

## 六、验收标准(一期)

1. 空状态 hero(标题+副标题+建议 chips)按设计稿呈现;点 chip 能发起对话。
2. Composer 工具条:＋ 菜单(Skills 二级可选)、模型选择 ▾、发送齐全且可用。
3. 选中的 skill 真正改变 agent 行为(系统提示),与会话历史/Skills 视图状态同步。
4. 对话流式输出 + 工具调用 chip 实时显示;**Ask 内无任何下单/纸面入口**。
5. agent 能在对话中起草 Hypothesis 卡片,点 `Promote to Lab` 真实建对象(落 SQLite)。
6. 会话历史:新建/载回/搜索可用。
7. 全量 `pytest` 在分支本地通过;新增 Ask 相关测试通过(见测试用例文档)。

---

## 七、范围外 / 分期

- **二期**:Upload files(文件管线)、Projects(工作区/会话持久化跨刷新)、附件预览。
- **二期**:暖色换肤若工作量大,可先在一期保留现有主题、仅落 hero+composer 结构,主题单独排。
- 多租户、登录、协作 — 不在本模块。

---

## 八、下一步(本模块流程)

1. ✅ 本 PRD。
2. ⬜ 高保真设计(暖色 hero + composer + ＋二级菜单 + 模型选择;静态 HTML 原型或在 `web/static` 直接迭代)。
3. ⬜ 测试用例文档 + 自动化测试。
4. ⬜ 在 `feature/ask-module` 实现 → 本地 `pytest` 全绿 → 提 PR merge。
