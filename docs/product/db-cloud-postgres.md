# 数据库上云 · SQLAlchemy 双后端(SQLite dev / Postgres prod)

> 2026-06-25 · 分支 `feat/aihf-db-postgres`
> 目标(mentor): 数据库存云端,所有人用 web 时**调用同一份数据**。
> 约定: 表名集中配置、统一 `aihf_` 前缀、项目隔离、表名不写死。

---

## 一、为什么

之前每台机器各自一堆**本地 SQLite**(`~/.polyagents/`),互不相通——换机/多人看不到同一份数据。改成**一个共享数据库引擎**:连同一个云 Postgres,所有 web 请求读写同一份数据。

用 **SQLAlchemy Core 双后端**:同一套代码,本地/CI 跑 **SQLite**(快、零外部依赖),线上跑 **Postgres**;方言差异(AUTOINCREMENT/IDENTITY、连接池等)由 SQLAlchemy 抹平。

---

## 二、三个核心件

| 文件 | 作用 |
|---|---|
| `polyagents/storage/tables.py` | **表名配置中心**:所有表用 `aihf_` 前缀集中定义(SQLAlchemy `Table`),代码只引用这些对象/`table_name()`,**绝不写死字符串**。env 可配 `AIHF_TABLE_PREFIX` / `AIHF_DB_SCHEMA`。 |
| `polyagents/storage/engine.py` | **引擎工厂**:从 `POLYAGENTS_DATABASE_URL`(或 `DATABASE_URL`)建一个进程级共享引擎;没设则回退本地 SQLite(`~/.polyagents/cache/aihf.db`)。 |
| `storage/objects_store.py` · `audit_store.py` | 用 SQLAlchemy Core 重写,**公共 API 不变**(`save/get/list/promote/...`),调用方零改动。 |

---

## 三、配置(env)

```bash
# 线上(云端共享):指向你们服务器的 Postgres
POLYAGENTS_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/aihf
# 可选:项目隔离用独立 schema(默认 public)
AIHF_DB_SCHEMA=aihf
# 可选:改表前缀(默认 aihf_)
AIHF_TABLE_PREFIX=aihf_

# 不设 → 本地 SQLite(开发/测试默认),无需任何外部 DB
```
- **密码只在 env / .env,绝不进代码**(.env 已 gitignore)。
- 依赖:`sqlalchemy>=2.0` + `psycopg[binary]>=3.1`(prod 装;本地测试只用到 sqlalchemy + 内置 sqlite)。

---

## 四、表清单(`aihf_` 前缀)

| 逻辑名 | 物理表 | 内容 | 状态 |
|---|---|---|---|
| objects | `aihf_objects` | 5 类对象(假设/策略/…) | ✅ 已迁 |
| promotion_events | `aihf_promotion_events` | 晋升审计 | ✅ 已迁 |
| audit_events | `aihf_audit_events` | 会话/工具/晋升审计 | ✅ 已迁 |
| forecasts / evaluations | `aihf_*` | Lab 评估证据(LabRepository) | ⬜ 待迁(同模式) |
| markets/candles/trades/… | `aihf_*` | L1 行情缓存(DataStore) | ⬜ 待迁 |
| RAG 向量 | — | ChromaDB | ⬜ 另议(可上 pgvector) |

本 PR 先迁**共享知识最关键**的 objects + audit,跑通模式;其余按同样的 `tables.py`+`engine.py` 模式后续迁(独立 PR)。

---

## 五、上线步骤(服务器)

1. 在 Postgres 建库(可建独立 schema):`CREATE DATABASE aihf;`(可选 `CREATE SCHEMA aihf;`)
2. 服务器 env 设 `POLYAGENTS_DATABASE_URL=postgresql+psycopg://…`(+ 可选 `AIHF_DB_SCHEMA=aihf`)。
3. `pip install -r requirements.txt`(含 sqlalchemy + psycopg)。
4. 起 web:首次会 `create_all` 自动建 `aihf_*` 表。所有用户访问同一 URL = 同一份数据。

---

## 六、测试策略

- 本地/CI:`make_engine("sqlite://")` 内存库,每个 store 实例独立,快且无外部依赖。
- 契约测试 `tests/test_storage_engine.py`:`aihf_` 前缀、URL 归一化、**多 store 共享一个引擎互相读到写入**(上云共享的核心语义)。
- 注:SQLite 宽松,方言 bug 可能漏;上线前建议在一个真 Postgres 上跑一轮 smoke(待拿到 DSN)。
