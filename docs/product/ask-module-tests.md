# Ask 模块 · 测试用例

> 配套: `ask-module-PRD.md` · 分支 `feature/ask-module`
> 自动化: `tests/test_ask_module.py`(后端 model 选择)+ 现有 `tests/test_web.py`(skills/路由)
> 手动验收: 下表(对照 PRD §六验收标准)

## 一、自动化(pytest,本地必须全绿)

| 用例 | 断言 | 文件 |
|---|---|---|
| 已知模型透传 | `resolve_model("claude-opus-4-8")` == 该 id | test_ask_module |
| 未知/空/None 回退默认 | `resolve_model(...)` == `DEFAULT_CONFIG["anthropic_model"]` | test_ask_module |
| 模型白名单合法 | `ASK_MODELS` 非空,值均 `claude-` 前缀且能 resolve | test_ask_module |
| chat 端点读 model 字段 | `chat` 源码含 `body.get("model")` | test_ask_module |
| skills 列表/选择仍工作 | `/api/skills` 含 category;`_compose_prompt` 选中生效 | test_web |
| 路由齐全 | `/`,`/api/chat`,`/api/skills`,`/api/objects` 存在 | test_web |

## 二、手动验收(UI,逐条对照 PRD)

| # | 步骤 | 期望 |
|---|---|---|
| A1 | 进 Ask、无消息 | 居中 Hero:大标题 + 副标题 + 大输入框 + "Try asking" chips |
| A2 | 点一个建议 chip | 文案填入输入框(可直接发送) |
| B1 | 看 composer 工具条 | `＋` · `Chat` 胶囊 · `模型 ▾` · `📎` · `圆形发送` 齐全 |
| B2 | 点 `＋` | 弹出菜单:Upload files(soon 灰显)/ Skills ›/ Projects(soon 灰显) |
| B3 | `＋` → Skills › | 右侧飞出 skill 列表,可勾选;勾选与左栏 chips/Skills 视图同步 |
| B4 | 点 `模型 ▾` | 下拉 Sonnet/Opus/Haiku,选中打勾,胶囊文案更新 |
| B5 | 选 Opus 后发一条消息 | 请求 body 带 `model:"claude-opus-4-8"`,后端用该模型 |
| C1 | 发送一条消息 | 进入对话态:气泡流式输出 + 工具调用 chip;输入框下沉到底 |
| C2 | 触发假设涌现 | 出现 Hypothesis 卡片(id/snapshot/success + Promote/Edit/Dismiss) |
| C3 | 点 Promote to Lab | 真实建对象(`/api/objects`,落 SQLite),Objects/Lab 可见 |
| D1 | 全程 | Ask 内**无任何下单/纸面入口**(只读边界) |
| D2 | soon 项 | Upload files / Projects 灰显不可点(不放失效假按钮) |
| E1 | 会话历史 | 新建 / 载回 / 搜索可用 |

## 三、回归
- 切到 Lab/Markets/Objects/Evaluation 各视图正常(IA 不被破坏)。
- 右侧持仓栏:Ask 隐藏、其它视图显示。
- 全量 `pytest` 通过(本次新增不减少既有 148)。
