# Ask · Upload files 功能 PRD

> 版本 v0.1 · 2026-06-24 · 分支 `feature/ask-module`
> 上位: `ask-module-PRD.md`(本功能为其中 ＋菜单 → Upload files,由 soon 升级为可用)
> 一期范围(已定): **文本 + 图片 + PDF**;拖拽到聊天框 + 点击附件 + ＋菜单三入口
> 流程: PRD → 高保真 → 测试 → 开分支本地通过后停手(用户自行 push/merge)

---

## 一、目标

让用户把文件**拖拽到聊天框**(或点击附件 / ＋→Upload files)带进对话,agent 能**真正读到内容**来回答——丢一份 CSV/笔记/研报 PDF/图表截图,然后问它。仍是 **Ask 只读**:文件只作上下文,不触发任何写动作。

---

## 二、支持的文件与处理方式

| 类型 | 扩展名 | 后端处理 | 喂给 agent 的形式 |
|---|---|---|---|
| 文本 | .txt .md .csv .json .log .py .ts .yaml .tsv | UTF-8 解码(忽略坏字节),截断到上限 | 作为文本块拼进用户消息:`[file: name]\n<内容>` |
| PDF | .pdf | `pypdf` 逐页 extract_text,截断 | 同上(文本块) |
| 图片 | .png .jpg .jpeg .webp .gif | 读 bytes → base64 + media type | Claude **多模态 image 块** |

**依赖**:venv 需装 `pypdf`(纯 Python,轻量);图片无需额外库;`python-multipart` 已具备。

---

## 三、限额(防滥用 / 控 token)

- 单文件 ≤ **10MB**;单条消息最多 **5 个文件**;图片单消息最多 **3 张**(Claude 限制)。
- 文本/PDF 抽取后截断到 **~20k 字符/文件**(超出尾部标注"…(截断)")。
- 非白名单扩展名 / 超限 → 前端拒收并提示,不上传。

---

## 四、交互流程

1. **三个入口**:
   - **拖拽**:文件拖到聊天区/输入框 → 高亮 drop zone → 松手即上传。
   - **附件按钮**(composer 工具条 📎,从 disabled 改为可用)→ 系统文件选择。
   - **＋ → Upload files**(从 soon 改为可用)→ 同附件。
2. 上传中:输入框上方出现**附件 chip**(文件名 + 类型图标 + 大小 + 转圈),完成后转圈变为可移除 `×`。
3. 发送消息:把待发附件随消息一起带上;消息气泡顶部显示已带文件 chip。
4. 失败(类型/超限/抽取失败):chip 显示红色错误,可移除;不阻塞发送其它内容。

---

## 五、后端设计

### 端点 `POST /api/upload`(multipart,可多文件)
逐文件:校验扩展名 + 大小 → 按类型抽取 → 返回:
```json
{ "files": [
  { "id":"f_ab12", "name":"notes.md", "kind":"text", "size":1234, "chars":980 },
  { "id":"f_cd34", "name":"chart.png", "kind":"image", "size":88012, "media_type":"image/png" },
  { "id":"f_ef56", "name":"report.pdf", "kind":"pdf", "size":456789, "chars":18000, "pages":12 }
]}
```
- 抽取出的**文本**与图片 **base64** 暂存在**进程内会话缓存**(`{id: {...}}`,带 TTL/容量上限),用 id 引用,避免把大 base64 再回传一轮。
- 单文件错误项:`{ "name":..., "error":"unsupported type" }`,不影响其它文件。

### `/api/chat` 扩展
- body 增加 `attachments: ["f_ab12", ...]`(id 列表)。
- 组装用户消息(改 `_to_lc_messages` 支持**结构化 content**):
  - 文本/PDF id → 追加文本块 `[file: name]\n<抽取内容>`。
  - 图片 id → 追加 `{type:"image", source:{base64, media_type}}` 多模态块。
- 找不到的 id(缓存过期)→ 忽略 + 在回复前提示"附件已过期,请重传"。

### 只读边界
附件只进入 **prompt 上下文**,不落交易、不建对象;Ask 工具集不变。

---

## 六、前端设计(高保真见 `docs/design/ask-upload-hifi.html`)

- **Drop zone**:dragover 时聊天区出现虚线高亮 + "松开以添加文件";dragleave/drop 还原。
- **附件 chip 区**:composer 顶部,横向可滚动;每个 chip = 图标 + 名 + 大小 + 状态(上传中/✓/✗) + `×`。
- **附件按钮**📎 与 **＋→Upload files** 改为可用(去掉 `soon`)。
- 隐藏 `<input type="file" multiple>` 承接点击选择。

---

## 七、验收标准

1. 拖拽一个 .md/.csv 到聊天框 → 出现 chip → 发送后 agent 能引用其内容回答。
2. 拖拽一张 .png → agent 能描述/分析图片(多模态生效)。
3. 上传一个 PDF → agent 能回答其文本内容(pypdf 抽取)。
4. 三入口(拖拽 / 📎 / ＋→Upload files)都能添加;chip 可移除。
5. 超类型/超大小被拒并提示;单文件失败不连累其它。
6. Ask 仍只读,无下单入口;附件不建对象。
7. 本地 `pytest` 全绿;新增 upload 抽取/组装单测通过(见测试文档)。

---

## 八、范围外 / 二期

- 文件**持久化**与跨会话引用(当前进程内缓存,刷新/重启即失效)。
- Office(docx/xlsx)、大 PDF OCR、图片预处理/压缩(PIL)。
- 把上传文件做成 agent 可主动 `read_file` 的工具(当前是一次性注入上下文)。

---

## 九、下一步(本功能流程)

1. ✅ 本 PRD。
2. ⬜ 高保真(drop zone + chip 区,深色)。
3. ⬜ 测试用例 + 自动化(抽取/组装/限额)。
4. ⬜ 实现 → 本地 `pytest` 全绿 → **停手交给你 push**。
