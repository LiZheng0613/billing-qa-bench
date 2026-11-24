# 问账智能客服批量测试平台

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white">
  <img alt="Vue 3" src="https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-15%2B-4169E1?logo=postgresql&logoColor=white">
  <img alt="Redis" src="https://img.shields.io/badge/Redis-6.0%2B-DC382D?logo=redis&logoColor=white">
</p>

> **把 AI 客服的质量评估，从「人工抽检」升级为可批量执行、可复现、可审计的质量流水线。**

问账智能客服批量测试平台是面向对话式 AI 客服的**自动化回归与质量评估平台**，聚焦「精确数值 + 固定口径」类问答场景（演示域：电信计费问账——查话费、账单、套餐、余额）。它把上线与迭代前的**多轮对话回归、数值准确性校验、口径一致性验证**做成一条流水线，并内置 OpenAI 兼容 Mock LLM 与 Mock 被测系统——**本机即可离线走通全链路**，不依赖任何真实大模型与业务系统。

**为谁解决什么问题：**

- **测试 / QA 团队**——人工评测慢、主观、不可复现；平台用批量任务 + 规则硬校验 + LLM 多裁判混合裁决替代人工抽检
- **算法 / Prompt 工程师**——迭代前跑全量回归，用版本快照与全链路 Trace 回答「这次改动到底影响了什么」
- **业务 / 运营**——沉淀业务口径字典与冻结数据集，守住「金额错一位、口径偏一句就是投诉」的质量红线

**产品目标：**

1. **质量可量化**——通过率、时延、Token、事实通过率，用数字回答「这次迭代能不能上」
2. **过程可复现**——用例版本快照与执行绑定，任何一次历史结果都能还原当时的完整输入
3. **问题可定责**——全链路 Trace 与任意两次 Run 对比，把「上次还是对的」变成可定位的证据链

**技术栈**：后端 Python 3.11+ / FastAPI / SQLAlchemy 2.0 Async / Alembic / PostgreSQL 15+ / Redis；前端 Vue 3 `<script setup>` + TypeScript / Vite / Tailwind CSS / Pinia / ECharts；单仓库交付（`backend` + `frontend`）。

---

## 1. 为什么需要这个平台

> **平台能力不绑定演示域**：被测系统通过「环境」模板化对接，任何提供 HTTP(S) JSON 或 SSE 接口的智能客服都可接入；电信计费问账是内置的演示与验证域。

电信计费问账智能客服回答的大多是「精确数值 + 固定口径」类问题——「这个月话费多少」「超套费为什么是 27 元」「赠费什么时候到期」。金额错一位、口径说偏一句就是投诉；编造数字、越权查询更是安全红线。这类系统上线容易，**持续迭代不出回归事故难**，难点集中在三件事上：

| 痛点 | 现实情况 | 平台方案 |
| --- | --- | --- |
| 人工评测慢、主观、不可复现 | 一天人工抽检几十条对话，今天 90 分明天 70 分，说不出差在哪 | 批量任务并发执行；规则硬校验（金额 / 笔数 / 日期 / 必须包含 / 禁止包含）+ LLM 多裁判（`AVG` 均分 / `VETO` 一票否决）混合裁决；同一用例版本可反复回归，结果与证据全程留痕 |
| 多轮对话测不全 | 用户会追问、会指代上文、会「挤牙膏式」一点一点释放信息，固定脚本覆盖不了 | 三类用例：单轮 / 静态多轮 / **动态多轮**（LLM 模拟器按目标驱动，隐藏信息逐轮释放），配三重熔断（轮次上限 / 复读检测 / 模拟器放弃）防止失控 |
| 出了问题说不清 | 「上次还是对的」——是改了 Prompt、换了模型，还是被测接口数据变了？无法定责 | 版本快照绑定执行（`case_version`）+ 全链路 Trace（轮次、裁判明细、原始收发报文留存）+ 历史趋势与任意两次 Run 对比 |

平台把这些能力串成一条质量流水线：

**配置被测环境（支持真实 Agent 报文对接）→ 编写 / 导入用例（可 AI 起草）→ 评审 / 冻结沉淀数据集（答案契约用例）→ 批量执行 → 大盘看通过率与时延 → 点进 Trace 定位根因 → 修订用例或口径后再次回归。**

---

## 2. 产品能力全景

按一次质量保障的完整旅程组织，由被测系统对接与安全协作两大支撑能力托底：

```
用例生产 ──► 质量门禁 ──► 批量执行 ──► 观测分析
(AI 起草)    (评审/冻结)   (并发/熔断)   (Trace/趋势)
     ▲                                        │
     └──────────── 修订用例或口径后再次回归 ◄────┘
```

### 2.1 用例生产

| 能力 | 说明 |
| --- | --- |
| 三类用例 | 单轮问答、静态多轮脚本、**动态多轮**（LLM 模拟器按目标驱动，「挤牙膏式」逐轮释放隐藏信息），覆盖固定脚本测不到的追问、指代与信息不全场景 |
| 动态多轮状态机 | 模拟器按 `CONTINUE / GOAL_ACHIEVED / GIVE_UP` 驱动，轮次上限与硬上限双重保护 |
| AI 帮我填 | 用例抽屉中可手动填写的语义字段均可由 AI 起草回填（不落库，人工确认后应用）；无依据的数值 / 日期不编造，进入「待补充」 |
| 调试工作台 | 编辑任意字段后直接试运行，不生成版本、不动评审状态；草稿记录标记 `SANDBOX`，不进入趋势、通过率与事实统计口径 |

### 2.2 判分与裁决

| 能力 | 说明 |
| --- | --- |
| 混合裁决 | 规则硬校验（金额 / 笔数 / 日期 / 必须包含 / 禁止包含）+ LLM 裁判，`AVG` 均分 / `VETO` 一票否决策略，多裁判可并行；`ANSWER_CONTRACT` 模式由安全 / 行为 / 事实 / 扩展四道闸门判定 |
| 答案契约判分 | Must / May / MustNot 事实包络；名称-数值绑定（数值必须归属正确的业务名称，防「张冠李戴」式通过）；取数取证（`gold_query`）与 `lucky_pass` 侥幸通过诊断 |

### 2.3 质量门禁与数据集治理

| 能力 | 说明 |
| --- | --- |
| 评审与冻结 | 答案契约用例「评审 → 冻结」两步进入数据集，Must 事实强制取证；驳回留痕、契约变更自动退回待评审 |
| 候选池 | 承接生产日志 / 执行轨迹 / 手工录入，分诊转正，持续扩充数据集 |
| 质量运营 | 业务口径字典、判分器自检（变异样本检出 / 误杀统计）、事实通过率与数据集分布统计 |

### 2.4 批量执行

| 能力 | 说明 |
| --- | --- |
| 并发与限流 | 任务级并发（1–20）与 QPS 限流；执行范围支持勾选与全量启用用例（`ALL_ACTIVE`），适合上百条用例的回归 |
| 三重熔断 | 最大轮次 `MAX_TURNS_EXCEEDED`、复读检测 `REPETITION_ABORT`、模拟器放弃 `GIVE_UP`；熔断一律判不通过 |
| 执行异常自动重试 | 环境故障与模型调用异常按指数退避重试；业务性失败（判分不通过）不重试，避免无意义消耗 |
| 可运维的执行 | 任务终止、终止后续做、失败断点重跑、单批次结果 Excel 导出、原始报文留存 |

### 2.5 观测分析

| 能力 | 说明 |
| --- | --- |
| 指标大盘 | 通过率、平均 TTFT、P95 时延、Token 消耗、吞吐与预计剩余时间；WebSocket 推送，断线自动降级轮询 |
| 全链路 Trace | 单笔执行结果：对话轮次、裁判明细、规则校验、用例快照与原始收发报文全程留存 |
| 趋势与 Run 对比 | 单用例历史趋势（得分 / 时延 / 稳定性）、任意两次执行（Run）对比，支撑「改了什么导致结果变化」的定责 |

### 2.6 被测系统对接

| 能力 | 说明 |
| --- | --- |
| 协议与形态 | SSE 流式 / JSON 非流式；任何提供 HTTP(S) JSON 或 SSE 接口的智能客服都可接入 |
| 响应提取 | 字段提取器支持 JSONPath 与**条件规则**两种形态：规则按序命中（if/elif/else）、`occurrence=last` 取最后一次出现、同级 / 上级 / 下层相对导航、SSE 帧序保留，兼容存量字符串配置（见 `docs/RESPONSE_EXTRACTION.md`） |
| 模板化报文 | 请求头 / 体模板 `{{variable}}` 占位 + Jinja2 表达式（拼接前缀、去空格、Base64 / MD5 / URL 编码、时间戳等） |
| 鉴权与钩子 | **Basic 鉴权**自动生成 `Authorization` 头；**前置接口钩子**取 `conversationId` / Token（多字段 JSONPath 提取），供主请求与后续轮次复用 |
| 会话与时间 | 会话由服务端返回回填或客户端生成两种模式；ISO 时间戳每轮刷新 |

### 2.7 资产与版本管理

| 能力 | 说明 |
| --- | --- |
| 版本快照 | 每次保存用例生成 `TestCaseRevision` 快照，`ExecutionRecord.case_version` 精确绑定执行时的用例版本——任何历史结果都能还原当时的用例输入 |
| 版本 Diff / 回滚 | 字段级 Diff（含嵌套 JSON 路径）、历史修改记录、一键回滚并自动生成新版本 |
| Prompt 资产 | 模板版本管理、`LATEST / PINNED` 引用策略、单用例覆盖、引用影响面分析、在线试跑沙盒 |

### 2.8 安全与协作

| 能力 | 说明 |
| --- | --- |
| 多用户 RBAC | JWT 认证；46 个权限码、5 个内置角色（系统管理员 / 用例操作员 / 用例审核员 / 测试执行员 / 只读观察员），模块级 + 动作级双重权限校验，操作按钮按权限渲染 |
| 密钥保护 | 环境变量与模型 Key Fernet 加密落库，默认接口返回掩码；拥有 `environment:secret_read` / `model:secret_read` 的账号在配置页可查看明文原值 |
| 账号安全 | 登录连续 5 次失败锁定 15 分钟（到期自动恢复、可手动解锁）；管理员重置密码后强制改密 |
| 操作审计 | 关键操作写审计日志（操作人、动作、资源、IP、状态码） |

---

## 3. 目录结构

```
batch_test/
├── backend/
│   ├── billing_qa/            # 后端应用包
│   │   ├── api/v1/            # auth / users / roles / audit / notifications / environments / models / prompts / cases / tasks / executions / ws / calibers / candidates / capabilities / grader / stats
│   │   ├── core/              # 配置、日志、安全、异常、加密、任务控制面
│   │   ├── db/                # 引擎/会话、建库初始化、种子数据、演示数据
│   │   ├── models/            # 22 张 ORM 表（18 业务 + 用户 / 角色 / 权限 / 审计）
│   │   ├── schemas/           # Pydantic 请求/响应模型
│   │   └── services/
│   │       ├── adapter/       # 被测系统适配（SSE / JSON、JSONPath 提取、Basic 鉴权、前置接口钩子）
│   │       ├── templating/    # 模板表达式能力（Jinja2 沙箱引擎、过滤器注册表、变量识别）
│   │       ├── author/        # AI 用例草稿生成（数值依据校验、字段白名单、多候选去重）
│   │       ├── capability/    # 取数通道与能力模板（Cypher 工具 / 只读 SQL / HTTP 查询接口）
│   │       ├── engine/        # 执行器、调度器、规则校验、裁判、指标聚合
│   │       ├── grading/       # 答案契约判分（事实 / 行为 / 语义绑定 / 变异自检）
│   │       ├── llm/           # OpenAI 兼容客户端（限流 / 重试 / 严格 JSON）与模型注册解析
│   │       ├── revision/      # 版本快照与 Diff
│   │       └── io/            # 导入导出：用例 JSON、执行结果 Excel、用例 Excel（历史兼容）
│   ├── migrations/            # Alembic 迁移脚本
│   ├── mock_servers/          # 内置 Mock：LLM(8001)、被测客服系统(8002) 与取数执行器(8003)
│   ├── scripts/               # api_smoke.py / e2e_smoke.py / seed_demo_history.py / seed_environment_runs.py / migrate_mysql_to_postgres.py
│   ├── tests/                 # 508 项 pytest
│   ├── alembic.ini
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   ├── e2e/                   # Playwright 端到端（「AI 帮我填」主链路）
│   └── src/
│       ├── api/               # fetch 封装（统一错误、401 登出、鉴权下载）
│       ├── components/        # AppShell / BaseModal / StatusPill / JsonViewer 等
│       ├── composables/       # useEChart
│       ├── stores/            # auth / toast / breadcrumb / debug
│       ├── access/            # 访问管理：用户 / 角色与权限 / 审计日志
│       ├── views/             # 登录 / 测试大盘 / 批量任务与详情 / 用例与详情 / Prompt / 单笔执行结果 / 环境与模型 / 运营后台
│       └── utils/
├── docs/                      # 部署、用例 JSON 格式、Agent 对接、模板表达式语法、答案契约操作手册、演示数据集等
└── README.md
```

---

## 4. 快速开始

> 以下步骤基于**内置 Mock 服务**（OpenAI 兼容 Mock LLM、Mock 被测客服系统、Mock 取数执行器）在本机完成全链路体验，不依赖真实大模型与业务系统；对接真实 Agent 系统见 [`docs/AGENT_INTEGRATION_GUIDE.md`](docs/AGENT_INTEGRATION_GUIDE.md)。

### 4.1 前置要求

| 组件 | 版本 |
| --- | --- |
| Python | 3.11+（开发与验证使用 3.14） |
| Node.js | 18+（验证使用 22） |
| PostgreSQL | 15+（UTF-8 编码） |
| Redis | 6.0+ |

本机默认连接：PostgreSQL `127.0.0.1:5432`（postgres/postgres，库名 `batch_test`）、Redis `redis://127.0.0.1:6379/1`。可通过环境变量覆盖。

### 4.2 启动依赖

```powershell
# PostgreSQL（示例，按本机实际情况调整）
net start postgresql-x64-16

# Redis（示例）
redis-server
```

### 4.3 后端

```powershell
cd backend

# 1) 安装依赖（建议使用虚拟环境）
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2) 生成配置
Copy-Item .env.example .env

# 3) 建库 + 升级表结构 + 内置 Prompt 模板 + 演示数据
python -m billing_qa.db.init_db --demo

# 需要清空现有用例与执行记录后按电信计费域重建
python -m billing_qa.db.init_db --demo --reset

# 4) 启动 API（默认 8000）
python -m uvicorn billing_qa.main:app --host 127.0.0.1 --port 8000
```

- `--reset` 会删除 `test_case` / `test_case_revision` / `execution_record` / `turn_trace` / `judge_result` / `fact_result` / `gold_query` / `batch_task` / `case_candidate` / `grader_eval` / `capability` / `data_source` / `sys_audit_log` 中的全部数据，并清理 `test-*` / `xzc` 等测试模型与 `DEMO-ENV-*` 环境对比演示环境，保留其余环境、模型、Prompt、内置角色与管理员，再重新写入演示用例、业务口径、候选池、取数能力、取证记录与演示审计。
- 需要演示大盘、趋势、Run 对比、续做与定时计划时，再执行 `python scripts/seed_demo_history.py`：脚本会真实调用 Mock 服务生成 9 条演示任务（全量回归 / 单轮与契约专项 / 动态多轮专项 / 中断续做 / 每日定时 / 每周定时 / 一次性定时 / 错误策略与自动重试 / 在途终止），加 1 条草稿沙箱试跑，以及对应的执行记录、轮次轨迹、裁判明细与失败追问自检，并把时间戳回填到近 3 天。脚本幂等，重复执行只重建 `DEMO-TASK-*` 与草稿试跑记录。
- 需要演示「按执行环境对比」时，再执行 `python scripts/seed_environment_runs.py --env-code QA-MOCK`：脚本用真实引擎在 SSE 环境上生成 67 条调试执行，与环境对比页默认的 137 条任务内执行一起构成近 7 天数据；重跑会追加记录，演示前重置即可恢复基线。

> 表结构只有一条链路：`init_db` 内部调用 `billing_qa.db.migrations.upgrade_schema()`，即 `alembic upgrade head`（空库会按迁移脚本从零建表），不再使用 `create_all` 或 `alembic stamp`。后续结构变更仍是 `alembic revision --autogenerate` + `alembic upgrade head`。

- Swagger：<http://127.0.0.1:8000/api/v1/docs>
- 健康检查：<http://127.0.0.1:8000/health>

### 4.4 内置 Mock 服务（离线自测）

```powershell
cd backend
# Mock LLM：OpenAI 兼容 /v1/chat/completions，支持 MOCK 注入指令
python -m uvicorn mock_servers.llm_api:app --host 127.0.0.1 --port 8001

# Mock 被测客服系统：/api/v1/chat（SSE 流式）与 /api/v1/agent/chat（Agent 报文）
python -m uvicorn mock_servers.target_api:app --host 127.0.0.1 --port 8002

# Mock Cypher 取数执行器：/execute /logs /health /meta，供数据源与取数能力演示
python -m uvicorn mock_servers.capability_api:app --host 127.0.0.1 --port 8003
```

- 演示数据源 `MOCK-CYPHER-TOOL` 指向 `http://127.0.0.1:8003`，内置 14 条取数能力（账单、费用明细、套餐用量、offer 目录/已订购、余额、欠费、漫游、增值业务、宽带、发票、积分、套餐合约、客户画像）。运营后台「数据源 / 取数能力 / 取证记录」可直接探活、执行取证并生成 GoldQuery；执行参数示例：`{"phone":"13800138000","period":"2026-10"}`。执行器只读：含 `SET` / `DELETE` 等写关键字返回 `403 QUERY_FORBIDDEN`，未知能力或缺少 `capability:<key>` 标记返回 `422 INVALID_REQUEST`，`params.__error` 传入 `TIMEOUT` / `INTERNAL` 可注入 `504` / `502` 演示异常链路。

- Mock 被测系统内置电信计费问账数据：账单 / 套餐月费与合约 / 套餐用量 / 账户余额 / offer 订购五类数据型问题为主，外加国际漫游、增值业务、欠费停机、融合宽带/IPTV 等场景，完整口径见 [`docs/DEMO_DATASET.md`](docs/DEMO_DATASET.md)。
- Mock 支持注入指令，用于稳定复现失败 / 熔断 / 异常等质量场景（写在用例 `query` / `expected_answer` / 隐藏上下文中即可触发）：

| 指令 | 效果 |
| --- | --- |
| `[MOCK:FAIL]` | 裁判返回低分与红线命中 |
| `[MOCK:MAXTURNS]` | 模拟器持续追问，触发轮次熔断 |
| `[MOCK:REPEAT]` | 被测系统复读，触发复读熔断 |
| `[MOCK:GIVEUP]` | 模拟器主动放弃 |
| `[MOCK:LLMERROR]` | 裁判模型返回 HTTP 500，执行以 LLM 错误收口，验证错误分类与自动重试 |
| `[MOCK:MULTISTEP]` / `分两步` | 被测系统一次回答回传 2 条取数调用，验证多步调用链 |
| `[MOCK:SLOW]` / `模拟慢响应` | 被测系统延迟 30s 才响应，用于制造在途执行窗口与人工终止 |
| `[MOCK:REQUIRE_SESSION]` | 请求未携带 `session_id` / `sessionId` / `conversationId` 时返回 400，验证会话绑定 |
| `错误金额` | 被测系统返回错误账单金额，验证规则硬校验 |
| `模拟崩溃` / `触发异常` | 被测系统返回 HTTP 500，验证异常分类与重试 |

### 4.5 前端

```powershell
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173
```

Vite 已配置 `/api` 与 `/health` 反向代理到 `http://127.0.0.1:8000`，并开启 WebSocket 透传。

### 4.6 登录

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `admin123` | 系统管理员（`SYSTEM_ADMIN`，拥有全部权限） |

账号密码可用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 覆盖；生产环境务必同时替换 `SECRET_KEY`。

平台为多用户 RBAC 模型：初始化时写入 5 个内置角色（系统管理员 / 用例操作员 / 用例审核员 / 测试执行员 / 只读观察员），「访问管理」中可创建用户、分配角色、维护自定义角色权限、解锁账号与查看审计日志。登录连续 5 次密码错误会锁定账号 15 分钟，锁定期满自动恢复。

### 4.7 演示数据包内容

| 资产 | 内容 |
| --- | --- |
| 环境 | `QA-MOCK-JSON`：Agent 报文（Basic 鉴权 + 前置取 `conversationId` + 首轮空 `sessionid` 回填 + `timestamp_iso`），**68 条 `ACTIVE` 用例绑定**（批量回归 67 条，慢响应用例单独演示）；`QA-MOCK`：SSE 流式演示环境，`seed_environment_runs.py` 在其上生成 67 条调试执行用于环境对比；`QA-MOCK-OFFLINE`：`is_active=false` 的停用环境，只用于演示环境校验失败（BUSINESS 错误） |
| 用例 | 72 条（69 `ACTIVE` + 2 `DRAFT` + 1 `DISABLED`；33 条答案契约 + 39 条 4 维评分），全部带 `userid` / `custid` 请求变量（`userid` 取自话术中的号码，数字按字符串保存） |
| 业务资产 | 4 个模型、4 套 Prompt（7 个版本）、14 条业务口径、8 条候选（NEW / TRIAGED / PROMOTED / REJECTED）、2 个数据源、14 条取数能力、13 条取证记录、1 条判分器自检、7 个演示账号（含锁定 / 停用各 1 个）、12 条演示审计 |
| 历史数据 | 9 条任务（3 完成 + 2 中断 + 3 定时计划 + 1 错误策略）+ 137 条任务内执行明细（含 1 条自动重试）+ 67 条 SSE 调试执行 + 1 条草稿沙箱试跑，合计 205 条执行、227 条轮次轨迹、205 条裁判明细（195 条执行有裁判，其中 10 条双裁判）、237 条事实级判分、53 条追问自检记录（46 `SKIPPED` / 6 `DONE` / 1 `ERROR`） |

用例逐条清单与预期结果、Mock 数据口径、演示任务构成见 [`docs/DEMO_DATASET.md`](docs/DEMO_DATASET.md)。

> 轮次轨迹的 `tool_calls` 由 Mock 目标系统真实回传：取数类回答每轮 1 条 `db_query`（能力码、SQL、入参、行数、时延与结果摘要），多步用例 `DEMO-SINGLE-MULTI-STEP` 一轮 2 条，澄清 / 拒答为空数组 `[]`；两种报文形态（SSE / 非流式）都能抽取。因此「Agent 调用链」「SQL」「取数证据」有真实步骤，`lucky_pass` 为 0，`diagnostics.over_budget` 为 0。227 条轮次里 191 条 1 步、3 条 2 步、33 条为空数组（确实没有取数），没有 `null`（未记录）。

> 在演示库上运行 pytest / 冒烟脚本会产生 `PYTEST-*`、`BATCH20*` 等临时数据；正式演示前依次执行 `python -m billing_qa.db.init_db --demo --reset`、`python scripts/seed_demo_history.py` 与 `python scripts/seed_environment_runs.py --env-code QA-MOCK` 即可恢复基线。
>
> 跑测试前更省事的做法是先备份再还原，库规模、时间戳与环境探活结果都能原样保留，不必重跑种子：
>
> ```powershell
> $env:PGPASSWORD='postgres'
> pg_dump -h 127.0.0.1 -U postgres -d batch_test -F c -f demo_before_test.dump
> # …跑 pytest / 冒烟…
> psql -h 127.0.0.1 -U postgres -d batch_test -c "drop schema public cascade; create schema public;"
> pg_restore -h 127.0.0.1 -U postgres -d batch_test --no-owner --no-privileges demo_before_test.dump
> ```

---

## 5. 首次上手（五分钟走通）

1. **环境与模型**：确认 `QA-MOCK-JSON`（Agent 报文环境）与 `QA-MOCK`（SSE 演示环境）探活成功；确认 `MOCK-SIM`（模拟器）与 `MOCK-JUDGE` / `MOCK-JUDGE-2`（裁判）连通性测试通过。
2. **Prompt 资产**：查看内置模板 `SIM_DEFAULT_BILLING_USER`、`JUDGE_DEFAULT_ZERO_TOLERANCE` 与 `CASE_AUTHOR_DEFAULT`，可在沙盒中按模拟器 / 裁判 / AI 用例编写三种角色试跑。
3. **用例资产**：浏览 69 条 `ACTIVE` 电信计费演示用例；勾选行（或「全选筛选结果」）后点右上角「批量执行（N）」，可直接建任务并跳转大盘；行内「历史」查看该用例全部执行记录，打开「趋势 / 对比 / 版本」体验 Diff 与历史分析。
4. **测试大盘**：也可在「新建批量任务」中勾选用例（支持全选筛选结果，覆盖上百条用例）、设置并发数与 QPS，执行后 WebSocket 实时刷新进度；选中任务即为「批量任务详情」，点击用例行进入单笔执行结果。
5. **执行记录**：列表查看全部批量 / 调试 / 重跑记录，点击任意记录进入单笔结果页（对话轮次、裁判明细、规则校验、用例快照与原始报文）；对失败任务可断点重跑。

---

## 6. 功能详解

### 6.1 LLM 模型选择（统一解析）

所有 LLM 场景共用同一份模型注册数据，统一由 `services/llm/registry` 解析，语义保持一致：

- **用例编写 / 动态模拟器**：不按用例选择模型，两者各自只允许**一个启用项**，由系统配置决定，界面上不提供选择。
  - 模型注册里启用第二个同角色模型会直接 422：「该角色已有启用的模型，请先停用它」。
  - 存量数据出现多个启用项时，调用会明确报错并列出冲突项，要求只保留一个。
  - 要换模型：在设置页停用旧的、启用新的。
- **LLM 裁判**：仍可按用例多选（AVG / VETO 多裁判机制依赖），不受唯一启用约束。
- **Prompt 沙盒**：保留显式指定模型的能力（调试不同模型对同一 Prompt 的输出）。
- **连通性测试**：按注册项直测，不受角色与启用状态限制。
- 调试时的 `case_snapshot_override` 传入与角色不匹配的模型会被拒绝并记录为 `ERROR`，而不是悄悄用错模型执行。

> 模拟器模型已从用例字段中移除（原 `simulator_model_id` 列与 API 字段已删除），
> 执行时统一取全局唯一启用的 `SIMULATOR` 模型。

**模型与环境删除保护**：删除是物理删除，为避免执行链路断链，以下情况返回 409 拒绝——

- 模型：处于启用状态的 `SIMULATOR` / `CASE_AUTHOR` 模型（执行引擎的全局默认，需先停用）；被任意未删除用例的 `judge_model_ids`（多裁判列表）引用的模型。
- 环境：被任意未删除用例（`environment_id`）或批量任务引用的环境；如下线请改用「编辑」调整配置。

### 6.2 AI 帮我填（全字段覆盖）

「AI 帮我填」的目标是：**用例抽屉里能手动填写的语义字段，都能由 AI 生成草稿并回填**。生成结果仍不落库，必须人工确认后应用。

- **基础字段**：名称、描述、优先级、标签、判分方式、输入形态、账期、单轮提问 / 预期答案、静态多轮脚本、动态多轮语义（用户目标 / 初始提问 / 人设 / 隐藏信息 / 最大轮次 / 终止关键词）、Must / May / MustNot 事实、禁用短语、风险等级。
- **请求变量**：`request_variables` 按 key-value 生成；变量名须匹配 `[A-Za-z_][A-Za-z0-9_]*` 且避开运行时保留字（`query` / `history` / `history_text` / `turn` / `timestamp_iso`）。含数字或日期的取值必须有依据，否则留空并进入「待补充」。
- **4 维规则**：`expected_rules`（金额 / 笔数 / 日期 / 必须包含 / 禁止包含），以及静态多轮每轮的 `turns[].expected_rules`；数值与日期同样遵守「无依据不落库」。
- **多轮会话**：静态多轮的 `session_expected` 与 `fail_fast`。
- **响应契约**：非清晰输入时的 `response_contract`（澄清 / 带假设作答 / 部分披露 / 拒绝 及其子配置）。草稿允许只给出一种行为，保存前会提示补齐至少两种。
- **取数计划**：`plan_spec` 的 `required_capabilities` / `max_tool_calls`。能力编码只能来自系统能力清单（能力资产为空时退化为既有用例声明过的编码），模型编造的能力会被丢弃。
- **答案包络与判分配置**：`extras_policy`；`judge_strategy` / `pass_threshold` **仅在用户明确要求时**才生成，避免凭空给出阈值。
- **变更说明**：编辑既有用例时生成 `change_summary`。
- **结果面板**：顶部展示 AI 自评「把握 XX%」徽标（待补项越多置信度越低）；每个字段标注来源（用户 / 口径 / 历史用例 / 模型）。
- **明确不填**：用例编号只在新建且编号为空时给出建议；`environment_id`、模板 ID、`judge_model_ids`、`snapshot_id` 等外键 / 系统 ID 一律不由模型输出；`owner` 与 `status` 不生成，避免编造责任人或状态。

### 6.3 用例评审与冻结

答案契约用例在进入数据集前必须走「评审 → 冻结」两步，入口都在用例资产行内；4 维评分用例一旦进入评审态（待评审 / 已通过 / 已驳回）也保留复审与状态回退入口。

- **评审**：行内「评审 / 复审 / 重新评审」打开评审弹窗，展示当前评审状态、评审人与 Must 事实取证进度；支持「审核通过」「驳回」「撤回评审 / 重置为待评审」。
  - 审核通过会执行事实闸门：仍有 Must 事实处于 `PENDING` / `CONFLICT` / `NO_EVIDENCE` 时直接拒绝，并在弹窗内列出未取证事实。
  - 驳回必须填写原因，原因写入版本变更记录，便于整改追溯；驳回后用例显示「已驳回」，冻结入口隐藏。
  - 已通过的用例修改契约内容（事实、响应契约、取数计划、账期等）会自动退回「待评审」并清空评审人，避免旧审批为新内容背书。
- **冻结**：评审通过后才出现「冻结」按钮，需填写数据集版本；冻结时再次校验 Must 事实已 `VERIFIED` / `WAIVED`。
- 取证状态在运营后台「事实验收」维护；单条事实被撤销或标记冲突时，已通过用例同样自动退回待评审。

### 6.4 名称-数值绑定（判分语义）

事实判分不再只看「答案里出现过这个数字」，而是要求数值归属到正确的业务名称，避免 `账单总额=100 / 税额=10` 被写成 `账单总额=10 / 税额=100` 时仍然通过。三层优先级：

1. **JSON key 绑定**：最终答案是 JSON 对象（或包含 JSON 代码块）时，按字段名直接取值，如 `{"total_amount": 268.5, "item_count": 4}`。
2. **名称绑定**：中文散文里名称与数值需处于同一分句；仅跨行 / 冒号列表等场景允许短窗口兜底。
3. **纯数值兜底**：答案完全没出现业务名称时按数值匹配；若该数值已明确绑定到另一个业务名称，则判「数值归属冲突」而不是复用。

- 名称来源：事实 `label` + `aliases` + 事实 `id` + 包络级 `aliases`；英文 key（`total_amount` / `tax`）与中文别名可混用，比较时忽略大小写、空白、下划线、连字符与标点。
- `aliases` 在用例编辑抽屉中按逗号分隔维护；勾选「必须绑定名称」等价于 `require_name=true`，此时名称缺失或未与数值绑定都判失败。
- 规则硬校验与答案契约判分**互斥**：`LEGACY`（4 维评分）模式才执行 `expected_rules` 硬校验并展示「规则校验」页签；`ANSWER_CONTRACT` 模式由事实 / 行为闸门判定，不再执行规则硬校验，结果页只展示「契约判分」。两者的名称 / 数值绑定逻辑保持一致。
- 判分器版本随语义升级为 `answer-grader@0.2`；旧用例默认保持 `LEGACY`，不设置别名时行为与升级前一致。

### 6.5 批量执行与并发控制

- 任务可配置 `concurrency`（1–20）与 `qps_limit`；调度器用信号量控制同时在途用例数，用任务级令牌桶保证整体 QPS 不超限。
- 执行明细按用例取最新一次执行做统计，历史尝试记录保留在 `execution_record` 中，`attempt_no` 标记第几次尝试。
- 前端「选择用例」支持按编号 / 名称 / 标签筛选后一键全选筛选结果，用例列表会分页拉取全量数据，适合上百条用例的回归。
- 列表页搜索框统一支持**分号分隔多个关键词**（半角 `;` 或全角 `；`）：命中任意一个关键词即匹配，便于一次性查询多个用例编号、任务编号或标签；用例、任务、Prompt、环境、模型、口径、候选池、用户与权限搜索均适用。
- 批量入口有两处：**用例资产**页勾选行 / 全选筛选结果后点「批量执行（N）」，创建成功自动跳转 `/?task=<id>` 并选中新任务；**测试大盘**的「新建批量任务」适合集中编排环境与重试参数。仅 `ACTIVE` 用例可被执行。
- 执行范围支持两种模式（两个入口均可用）：
  - `SELECTED`：执行当前勾选的用例，支持本页全选与「全选筛选结果（跨页）」。
  - `ALL_ACTIVE`：由后端一次性解析用例库中全部启用用例（跨分页、不受当前筛选影响），前端只传 `case_scope=ALL_ACTIVE`，避免上百条用例逐页勾选或请求体过大。
- **终止后续做**：任务被终止后，可点「继续执行」从断点补跑未完成用例（未执行 / 执行中 / 被终止），已完成或已有最终判定的用例保持原结果、不重复执行；新尝试记为 `source=RESUME`，`attempt_no` 从该用例已有最大尝试次数继续递增。若需要连失败用例一起重跑，用「失败重跑」（`source=RETRY`）。
- **重启自动续做**：服务重启（含实例崩溃）后，执行中的任务会被自动接管续做，不再被标记为「已终止」：启动时先把中断时在途的执行记录收口为 `ABORTED`（保留审计痕迹），随即以 `source=RESUME` 补跑这些用例，最终「用例最新」视图不残留终止态结果。重启前已点「终止」的任务保持终止语义，不会被拉起。多实例部署时通过 Redis 运行锁（`TASK_RUN_LOCK_TTL_SECONDS`）互斥，同一任务只由一个实例执行；实例崩溃后锁自动过期，后台轮询每 `SCHEDULE_POLL_INTERVAL_SECONDS` 秒扫描一次，把无人推进的 RUNNING 任务接管续做。
- **定时计划多实例防重**：到期计划的派发按「计划 + 触发时刻」抢 Redis 触发锁（TTL 60 秒，覆盖读计划 → 克隆入库 → 推进 `next_run_at` 的事务窗口），多个实例同时扫到同一条到期计划时只派发一次，不会克隆出重复批次。
- **孤儿批次自愈**：计划派发在「克隆入库」与「启动执行」之间进程退出时，会留下永远无人拉起的 PENDING 批次（周期计划还会因此一直跳过后续触发）。后台轮询每轮做恢复扫描：计划仍有效的批次自动拉起；计划已停止或控制面带终止意图的批次收口为 `ABORTED`，不自动执行。

### 6.6 批量任务详情与单笔执行结果

- **批量任务详情**（测试大盘选中任务）：展示该任务的通过分布（通过 / 失败 / 异常 / 轮次超限 / 复读熔断 / 执行中 / 等待中）、进度、TTFT/P95/Token 等指标，以及按用例汇总的最新一次执行结果（含尝试次数），支持分页，覆盖上百条用例。
- **结果导出**：批量任务详情与周期计划的每次派生执行均可导出 Excel，包含「执行概览 + 用例结果」两个工作表；导出范围跟随页面当前模式（用例最新 / 全部尝试），用例结果覆盖执行状态、判分、失败原因、最终答案、裁判明细与完整轮次轨迹。
- **单笔执行结果**（`/trace?execution=<id>`）：聚焦一次执行，展示对话轮次、裁判明细、规则校验、用例快照与原始报文；可返回所属批量任务详情，或跳转该用例全部历史。批量详情点击用例行、用例调试完成、趋势面板「查看 Trace」均复用此页面。
- **用例历史**（`/trace?case=<id>`，或用例资产行内「历史」）：列出该用例全部批量 / 调试 / 重跑记录并分页；批量任务的「全部执行记录（含重试）」按任务列出每次尝试。用例级汇总统一取最新一次尝试，避免重试重复计数。

### 6.7 请求变量与多轮会话

环境模板（`headers_template` / `body_template`）用 `{{variable}}` 占位，具体取值按用例注入；模板同样支持 **Jinja2 表达式**（如 `"x-agent-usr": "admin_{{ custCode | replace(' ', '') }}"`），可用于拼接固定前缀、去空格（`trim` 只去首尾；`replace` 去半角 / 全角空格、制表符、换行）、Base64 / MD5 / URL 编码、时间戳等。完整语法、内置过滤器与示例见 [`docs/TEMPLATE_EXPRESSION_SYNTAX.md`](docs/TEMPLATE_EXPRESSION_SYNTAX.md)。

- **请求变量**：用例级 `request_variables`（key-value，值支持字符串 / 数字 / JSON），在用例抽屉「请求变量」中按行维护或直接编辑 JSON；不放敏感信息，明文保存。所有数字（含嵌套对象 / 数组里的数字）统一按原文保存为字符串，避免手机号、订单号等长数字丢精度或变成科学计数法。
- **三层优先级**：运行时变量（`query` / `session_id` / `history` / `history_text` / `turn` / `timestamp_iso`）> 用例 `request_variables` > 环境变量池。其中 `query` / `history` / `history_text` / `turn` / `timestamp_iso` 为保留字，不允许在用例变量中定义；`session_id` 由会话机制注入，用例中自定义会被运行时值覆盖。
- **时间戳**：`timestamp_iso` 为请求时刻的 ISO 8601 字符串（如 `2026-11-17T18:00:00.123`），每轮请求刷新。
- **Basic 鉴权**：环境可配置账号 + 密码（密码加密存储），执行时自动生成 `Authorization: Basic base64(账号:密码)`；请求头模板显式写了 `Authorization` 时以模板为准。
- **前置接口钩子**：执行前调用一次，支持用环境变量池与用例变量渲染 headers / body，并用 `extract`（变量名 → JSONPath）提取 `conversationId`、Token 等字段，供主请求与后续轮次复用；旧的 `token_jsonpath` / `token_var` 配置继续兼容。
- **会话绑定**：会话方式由**执行环境统一决定**，所有用例跟随，不在用例上单独选择：

  | 模式 | 行为 |
  | --- | --- |
  | `SERVER_RETURNED`（默认） | 首轮渲染空会话值，由被测接口返回后回填，后续轮次沿用 |
  | `CLIENT_GENERATED` | 执行开始时生成唯一值（`exec-{execution_id}-xxxxxxxx`），全程各轮一致 |

- **字段名由模板决定**：例如 `"conversationId": "{{session_id}}"` 或 `"sessionid": "{{session_id}}"`，不同接口可各自定义，平台不写死字段名；`SERVER_RETURNED` 模式下首轮渲染为空字符串，由目标接口按首轮对话处理。
- **接口配置页**：环境编辑弹窗内提供「Basic 鉴权」「前置接口钩子」、可用占位符清单与变量来源标注，并可选一条示例用例做**报文预览**（只读，不落库）。
- **探活调试**：环境列表的「调试」可选一条用例，把其提问（可临时修改）经请求模板与用例变量渲染后**直接发送到目标环境**，回显请求/响应报文、耗时、会话与白盒取数；本次发送不写执行记录、不产生任务数据、不更新探活状态，仅用于连通性与功能调试。
- **明文排障口径（有意设计）**：预览与探活调试回显的报文是**真实渲染值**——环境变量池解密后的取值、用例变量、前置接口注入的会话数据都会按模板渲染原样回显，敏感请求头（`Authorization` / `Cookie` / `X-API-Key` 等）除外（统一掩码为 `***`）。这是排障必需：管理员要定位「模板占位符拼错」「变量取值不对」「前置接口没注入」等问题，就必须看到真实渲染产物。因此这两个入口面向持有环境写权限（内置角色中即系统管理员）的账号，不按 `secret_read` 二次设卡——环境写权限本身已包含对环境机密的处置能力。
- **执行结果**：对话轮次页展示每轮会话徽标、会话连续性小结、用例变量来源标记；不一致时会提示「上下文可能未延续」。

真实 Agent 报文（前置取 `conversationId`、Basic 鉴权、首轮回填空 `sessionid`）的完整配置示例见 [`docs/AGENT_INTEGRATION_GUIDE.md`](docs/AGENT_INTEGRATION_GUIDE.md)。

相关接口：`POST /environments/{id}/preview`（body `{"case_id": 123}`，返回渲染后的 `headers` / `body` / `origins` / `session_mode_resolved`）；`POST /environments/{id}/debug-chat`（body `{"case_id": 123, "query": "可选覆盖提问"}`，直发目标环境并回显报文，`persisted=false`）。

### 6.8 执行异常自动重试

任务级配置：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `retry_on_error` | `true` | 是否开启执行异常自动重试，覆盖环境故障与模型调用/裁决异常 |
| `max_error_retries` | `2` | 单个用例最大自动重试次数（0 表示不重试） |
| `retry_backoff_seconds` | `3` | 首次重试的基础退避秒数，之后按 2 倍指数递增，单次封顶 60 秒 |

旧的 `retry_on_env_error` / `max_env_retries` 仍保留兼容：新任务会同步写入两组字段；旧任务升级时自动沿用原环境重试配置。

重试范围：

| 执行结果 | 典型场景 | 是否自动重试 |
| --- | --- | --- |
| `ERROR`（`ENV`） | 连接失败、连接/读取超时、HTTP 429/5xx、SSE 无数据帧、网关 HTML 错误页、鉴权服务不可达 | 是 |
| `ERROR`（`LLM`） | 模拟器 / 裁判 / 追问自检的模型调用失败，或内部重试后仍无法得到合规结构化输出 | 是 |
| `ERROR`（`BUSINESS`） | 模板变量缺失、用例配置非法、4xx 参数错误、平台内部确定性异常 | 否 |
| `FAIL` / `MAX_TURNS_EXCEEDED` / `REPETITION_ABORT` | 规则不通过、裁判低分、轮次超限、复读熔断等业务判定 | 否 |

- 重试同样占用并发信号量与 QPS 配额，不会突破任务限制；终止任务会立即中断重试等待。
- 模型返回 200 但内容不是约定 JSON 时，先由 `LLM_JSON_RETRY_ATTEMPTS`（默认 2，即总调用 3 次）带纠错提示修复；耗尽后按 `LLM` 异常进入任务级重试。配置见 `backend/.env`。
- 每次尝试生成独立执行记录（`source=RETRY`、`attempt_no` 递增），`error_kind` 记录分类，Trace 可逐次回看。
- 任务指标中的 `retry_count` 表示触发过自动重试的用例数；`env_retry_count` 作为兼容字段保留相同取值。

### 6.9 单条用例调试工作台

用例详情页的「调试工作台」把「调试」与「保存版本」解耦：左侧表单分区常驻展开（基础信息 / 请求变量 / 用例类型 / 用例内容 / 判分方式与契约 / 规则硬校验 / Prompt 与模型 / 变更说明），不再折叠；底部浮窗操作栏集中执行环境、失败追问自检开关、保存与试运行，修改任意字段后点「试运行（不保存）」即可看到执行结果。

- **全字段可调**：请求变量、单轮 / 静态多轮 / 动态多轮、隐藏上下文、答案契约（Must / May / MustNot、可接受行为、计划约束）、规则硬校验、模板与裁判模型选择等全部随表单进入试运行快照；`case_snapshot_override` 与当前版本快照浅合并，`code` / `case_type` 不可变。
- **零版本副作用**：试运行不生成 `TestCaseRevision`、不修改 `current_version`、不触发「评审状态退回待评审」；满意后点「保存为新版本」才落库。
- **失败追问自检**：勾选「失败追问自检调试」后，LLM 裁决失败时在同一会话追问 Agent 自检原因；试运行卡片直接展示自检状态与结论，完整追问 Prompt / 请求响应可在 Trace「追问自检」页签查看。
- **结果就地对照**：右侧展示执行状态、得分 / 通过线、耗时、TTFT、轮次、执行环境与模板、轮次摘要、事实判定、裁判理由与自检结论；多次试跑按时间堆叠、最新在前。
- **轨迹往返保留现场**：「查看完整轨迹」在同标签页打开 Trace，浏览器返回键 / 鼠标侧键可直接回到当前工作台；KeepAlive 会保留草稿、试运行结果与展开状态，不会因往返丢失。
- **离页气泡接力**：试运行期间切到其他页面，底部沿用单条调试的浮动胶囊展示进度；完成或失败后胶囊保留结果，点击「查看调试详情」进入该次执行的详情页（含运行中轮询刷新），继续查看 SQL / 取数 / 裁判过程。
- **草稿记录标记**：试跑记录 `source=SANDBOX`，执行记录与 Trace 显示「草稿试跑」徽标；用例趋势、执行通过率（`/executions/pass-rate`）与事实通过率 / 数据集分布统计（`/stats/overview`、`/stats/facts`）均自动排除这类记录；单条调试（`source=DEBUG`）属正式测试执行，保留统计。
- **保存后流转**：保存成功后可直接跳转评审 / 冻结进数据集。

接口：`POST /cases/{id}/debug`（body 示例：`{"draft": true, "environment_id": 1, "case_snapshot_override": {"query": "...", "turns": [...]}}`）。

---

## 7. 配置项

配置来自 `backend/.env`（大小写不敏感，完整示例见 `.env.example`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_ENV` / `DEBUG` | `dev` / `true` | 运行环境与调试开关 |
| `APP_NAME` | `问账智能客服批量测试平台` | 应用显示名（日志与页面标题） |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 监听地址与端口 |
| `API_PREFIX` | `/api/v1` | 路由前缀 |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许的跨域来源（逗号分隔） |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `127.0.0.1` / `5432` | PostgreSQL 连接 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | `postgres` / `postgres` | PostgreSQL 账号 |
| `POSTGRES_DB` | `batch_test` | 库名（初始化脚本会自动创建） |
| `POSTGRES_MAINTENANCE_DB` | `postgres` | 建库时连接的维护库 |
| `POSTGRES_POOL_SIZE` / `POSTGRES_POOL_RECYCLE` | `10` / `1800` | 连接池 |
| `DATABASE_URL` | 空 | 可选，整体覆盖异步连接串（需使用 `postgresql+asyncpg://`） |
| `REDIS_URL` | `redis://127.0.0.1:6379/1` | 控制面与状态存储 |
| `REDIS_KEY_PREFIX` | `batchtest:` | Redis key 前缀 |
| `SECRET_KEY` | 示例值 | JWT 与 Fernet 加密密钥，**生产必须替换** |
| `JWT_ALGORITHM` / `JWT_EXPIRE_MINUTES` | `HS256` / `720` | JWT 配置 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin123` | 初始管理员账号（`SYSTEM_ADMIN` 角色，初始化时写入用户表） |
| `DEFAULT_CONCURRENCY` / `DEFAULT_QPS_LIMIT` | `3` / `5` | 任务默认并发与 QPS |
| `SCHEDULE_POLL_INTERVAL_SECONDS` | `15` | 定时计划轮询间隔（秒） |
| `TASK_RUN_LOCK_TTL_SECONDS` | `60` | 任务运行锁 TTL（秒）：多实例下实例崩溃后锁自动过期，允许其他实例接管续做 |
| `PASS_THRESHOLD` | `60` | 默认通过分数线 |
| `HTTP_TIMEOUT_SECONDS` | `60` | 被测接口默认超时 |
| `MAX_TURNS_HARD_LIMIT` | `20` | 轮次硬上限（超过即熔断） |
| `REPETITION_SIMILARITY_THRESHOLD` | `0.9` | 复读检测相似度阈值 |
| `LLM_JSON_RETRY_ATTEMPTS` | `2` | 模型输出非 JSON 时的额外纠错重试次数（总调用 = 1 + 该值） |
| `LLM_JSON_RETRY_BACKOFF_SECONDS` | `1.0` | JSON 修复重试的基准退避秒数（指数递增，单次上限 8s） |
| `TASK_RETENTION_DAYS` | `30` | 任务数据保留天数 |
| `MOCK_LLM_BASE_URL` / `MOCK_TARGET_BASE_URL` | `http://127.0.0.1:8001/v1` / `http://127.0.0.1:8002` | 内置 Mock 地址 |

---

## 8. 数据模型（22 张表：18 业务 + 4 系统权限）

| 表 | 作用 |
| --- | --- |
| `environment` | 被测系统环境（Base URL、方法、路径、流式开关、超时、SSL、请求头/体模板、提取器、Basic 鉴权、前置接口钩子、多轮会话绑定方式） |
| `environment_variable` | 环境变量池，敏感值 Fernet 加密、接口默认返回掩码；有 `environment:secret_read` 时额外返回明文供编辑回显 |
| `model_config` | 模型注册（模拟器 / 裁判、OpenAI 兼容参数、限流与重试、加密 Key） |
| `prompt_template` | Prompt 模板主表（类型、当前版本、引用计数、是否内置） |
| `prompt_template_version` | 模板版本快照（内容、变量列表、变更说明） |
| `test_case` | 用例主表（三类配置、规则、模板引用策略、模型与裁判策略、当前版本、判分方式、答案契约、请求变量） |
| `test_case_revision` | 用例版本快照（全字段快照、变更类型、变更说明、操作人） |
| `batch_task` | 批量任务（并发、QPS、执行异常重试策略、状态、统计、控制标志） |
| `execution_record` | 单次尝试执行（`case_version` 绑定、尝试次数、错误分类、状态、得分、轮次、时延、Token、终止原因、判分模式与事实冗余） |
| `turn_trace` | 每轮对话明细（提问、回复、TTFT、时延、Token、原始收/发报文、工具调用轨迹） |
| `judge_result` | 裁判明细（模型、角色、分数、结构化理由、红线命中、Token） |
| `fact_result` | 事实级判分结果（事实 id / 类型、期望与实际、是否 Must 闸门、失败原因、证据引用） |
| `gold_query` | 取证记录（能力版本、来源、查询语句、返回行数与结果快照） |
| `data_source` | 取数通道（Cypher 执行器工具 / 只读 SQL / HTTP 查询接口，含地址与鉴权配置） |
| `capability` | 取数能力模板（能力编码、查询模板、参数定义、版本与启用状态） |
| `case_candidate` | 候选问题池（生产日志 / 执行轨迹 / 手工录入，分诊、转正、拒绝与去重） |
| `caliber` | 业务口径字典（指标定义、权威来源、负责人） |
| `grader_eval` | 判分器自检（变异样本、检出数、误杀数、版本与详情） |
| `sys_user` | 平台账号（密码哈希、状态、登录失败计数与锁定、强制改密标记） |
| `sys_role` | 角色及角色-权限关联（内置角色不可删除） |
| `sys_permission` | 权限码目录（46 项，模块 + 动作两级） |
| `sys_audit_log` | 审计日志（操作人、动作、资源、IP、状态码） |

---

## 9. 引擎工作方式

1. **快照绑定**：调度开始前读取用例当前版本，写入 `ExecutionRecord.case_version`，执行期间不感知后续编辑。
2. **Prompt 解析**：按 `LATEST / PINNED` 策略解析模拟器与裁判模板版本，并叠加用例级覆盖。
3. **对话循环**（静态 / 动态多轮）：
   - 渲染模拟器 Prompt（目标、隐藏信息、轮次、历史）→ 调 LLM → 解析 `thought / status / query`。
   - 调被测系统（SSE 逐块累计首字时延）→ 记录 `turn_trace` → `LEGACY` 模式下做规则校验。
   - 判定终止：目标达成、模拟器放弃、复读、轮次超限。
4. **混合裁决**：`LEGACY` 模式为规则硬校验 + 多个 LLM 裁判并行，`VETO` 策略下任一裁判命中红线即判失败、`AVG` 取均值；`ANSWER_CONTRACT` 模式由四道闸门（安全 / 行为 / 事实 / 扩展）判定，LLM 裁判只提供质量分参考。以最后一轮结论为准。
5. **指标聚合**：通过率、平均 TTFT、P95 时延、Token、吞吐、预计剩余时间；进度事件经 Redis + WebSocket 推送。

---

## 10. API 概览

统一前缀 `/api/v1`，除登录外均需 `Authorization: Bearer <token>`。每个接口先过**模块级**权限（按 HTTP 方法区分读 / 写集合），写操作再过**动作级**校验（如创建用例 `case:create`、终止任务 `task:stop`、导出结果 `task:export`），权限不足返回 403。

| 分组 | 主要接口 |
| --- | --- |
| 认证 | `POST /auth/login`、`GET /auth/me`、`POST /auth/change-password`、`POST /auth/logout` |
| 用户 | `GET/POST /users`、`GET/PATCH /users/{id}`、`PUT /users/{id}/roles`（分配角色）、`POST /users/{id}/reset-password`、`POST /users/{id}/unlock` |
| 角色与权限 | `GET/POST /roles`、`GET/PATCH /roles/{id}`、`PUT /roles/{id}/permissions`、`DELETE /roles/{id}`、`GET /roles/permissions/catalog`（46 个权限码目录） |
| 审计与通知 | `GET /audit-logs`（操作审计日志）、`GET /notifications` |
| 环境 | `GET/POST /environments`、`PUT/DELETE /environments/{id}`（删除带引用保护，被用例 / 任务引用时 409）、`GET/PUT /environments/{id}/variables`、`POST /environments/{id}/health-check`、`GET /environments/{id}/usage`、`POST /environments/{id}/preview`（选示例用例渲染报文）、`POST /environments/{id}/debug-chat`（探活调试：选用例直发消息，不落库） |
| 模型 | `GET/POST /models`、`PUT/DELETE /models/{id}`（删除带引用保护：启用中的模拟器 / 编写模型、被用例裁判列表引用时 409）、`POST /models/{id}/test`、`POST /models/opencode/catalog`（拉取 OpenCode Serve 可用模型与 Agent） |
| Prompt | `GET/POST /prompts`、`GET/PUT/DELETE /prompts/{id}`、`POST /prompts/{id}/versions`、`GET /prompts/{id}/versions/{version}`、`GET /prompts/{id}/usage`、`POST /prompts/{id}/sandbox` |
| 用例 | `GET/POST /cases`、`GET/PUT/DELETE /cases/{id}`、`POST /cases/{id}/debug`、`POST /cases/ai-draft`（AI 草稿）、`GET /cases/{id}/facts`、`POST /cases/{id}/facts/{fact_id}/verify`、`POST /cases/{id}/review`、`POST /cases/{id}/freeze`、`GET /cases/{id}/revisions`、`GET /cases/{id}/revisions/diff`、`POST /cases/{id}/rollback`、`GET /cases/{id}/trend`、`GET /cases/{id}/executions`、`GET /cases/compare/runs`、`POST /cases/import`、`GET /cases/export` |
| 取数资产 | `GET/POST /data-sources`、`GET/PUT/DELETE /data-sources/{id}`、`POST /data-sources/{id}/probe`（探活）、`GET/POST /capabilities`、`GET/PUT/DELETE /capabilities/{id}`、`POST /capabilities/{id}/evidence`（执行取证生成 GoldQuery） |
| 候选池 | `GET/POST /candidates`、`GET/PATCH /candidates/{id}`、`POST /candidates/{id}/reject`、`POST /candidates/{id}/promote`（转正为测试用例） |
| 业务口径 | `GET/POST /calibers`、`GET/PUT/DELETE /calibers/{id}` |
| 判分器自检 | `GET /grader/mutants`、`POST /grader/evals/run`、`GET /grader/evals` |
| 统计 | `GET /stats/overview`、`GET /stats/facts`、`GET /stats/datasets`（均排除 `SANDBOX` 草稿试跑） |
| 任务 | `GET/POST /tasks`、`GET /tasks/{id}`、`POST /tasks/{id}/start`、`POST /tasks/{id}/abort`、`POST /tasks/{id}/resume`（终止后从未完成处续做）、`POST /tasks/{id}/stop-plan`（停止指定时间/每天/每周/连续多轮计划）、`POST /tasks/{id}/retry-failed`、`POST /tasks/{id}/cases/{case_id}/retry`（批次内单用例重试）、`POST /tasks/{id}/run-now`（立即执行一次定时计划）、`PUT /tasks/{id}/schedule`（编辑定时计划）、`GET /tasks/{id}/runs`（计划执行历史）、`GET /tasks/{id}/metrics`、`GET /tasks/{id}/executions`（`latest_per_case=true` 按用例取最新一次并返回尝试次数）、`GET /tasks/{id}/export`（单批次结果 Excel，`latest_per_case` 控制用例最新或全部尝试） |
| 执行 | `GET /executions`、`GET /executions/pass-rate`（最近 N 小时通过率，排除 `SANDBOX`）、`GET /executions/{id}`、`GET /executions/{id}/raw` |
| 实时 | `WS /ws/tasks/{task_id}?token=<jwt>`（建连时与 REST 同口径校验账号状态与 `token_version`，禁用 / 顶号后的旧 Token 无法建立长连接） |

---

## 11. 测试与验证

### 11.1 后端单元/集成测试

```powershell
cd backend
python -m pytest -q          # 508 passed（Mock / PostgreSQL 依赖不可用时自动 skip）
python -m ruff check billing_qa scripts tests
```

### 11.2 API 冒烟（需 API 已启动）

```powershell
cd backend
python scripts/api_smoke.py  # 46 项全部通过
```

覆盖：登录、环境变量掩码与探活、Agent 报文环境（接口 / Basic / 前置钩子）、模型连通性、模板种子/版本/引用影响、模板发布与版本历史即时刷新、用例 CRUD、版本 Diff 与回滚、单条调试、沙盒、批量任务全生命周期（创建 / 启动 / 终止 / 断点重跑）、指标聚合、Trace 与原始报文、趋势聚合。

> 冒烟脚本使用独立临时模板与用例验证流程并在结束时删除，不会污染内置模板与演示用例基线。

### 11.3 引擎端到端冒烟

```powershell
cd backend
python scripts/e2e_smoke.py  # 需 Mock 8001/8002 已启动，且已 init_db --demo
```

预期命中：`PASS ×3`、`FAIL ×1`、`MAX_TURNS_EXCEEDED ×1`；另附「请求变量 + 客户端生成会话」独立校验（headers/body 注入、用例变量覆盖环境变量池、会话值贯穿第二轮、Mock `received_params` 回显）。

### 11.4 前端

```powershell
cd frontend
npm run typecheck   # vue-tsc，noUnusedLocals / noUnusedParameters 已开启
npm run build       # 产物在 dist/
```

### 11.5 前端 E2E（Playwright）

```powershell
cd frontend
npm run test:e2e    # 「AI 帮我填」主链路回归
```

- 依赖：后端 `127.0.0.1:8000`、Mock LLM `8001` / 被测 Mock `8002`、前端 `5173` 都在运行，且已初始化演示数据。
- 默认用本机 Edge（`channel: 'msedge'`）驱动，不下载 Chromium；其他浏览器可改 `playwright.config.ts`。
- 用例会真实创建临时用例并在结束时删除，保持演示库 72 条基线；失败时可看 `test-results/` 下的截图与 trace。

---

## 12. 部署

生产部署（Linux + systemd + Nginx，或容器化）与安全加固清单见 [`docs/deployment.md`](docs/deployment.md)。

对接真实 Agent 系统（Basic 鉴权、前置取 `conversationId`、会话回填、时间戳）的配置指导见 [`docs/AGENT_INTEGRATION_GUIDE.md`](docs/AGENT_INTEGRATION_GUIDE.md)。

模板表达式（固定前缀拼接、去空格、Base64 / MD5、时间戳等）的完整语法与过滤器清单见 [`docs/TEMPLATE_EXPRESSION_SYNTAX.md`](docs/TEMPLATE_EXPRESSION_SYNTAX.md)。

---

## 13. 已知限制

- RBAC 为单租户模型：支持多用户、多角色与 46 项权限码的完整权限体系，但不做多租户（组织 / 空间）隔离；数据不分归属，所有有读权限的账号看到同一份数据。
- 任务执行器为进程内 asyncio 调度；服务重启后执行中的任务会自动续做（在途尝试收口为 `ABORTED` 留审计，未完成用例以 `RESUME` 来源补跑）。多实例部署依赖 Redis 互斥（任务运行锁 + 计划触发锁）；Redis 不可用且多实例同时运行时存在重复执行的可能（单实例不受影响）。
- 动态多轮的「目标达成」判定依赖模拟器输出与裁判结论，Mock 场景下由注入指令确定。
- 前端 ECharts 全量包约 1.0 MB（gzip 343 KB），已拆分为独立 vendor chunk；如需进一步瘦身可改为按需引入。
- 测试与冒烟脚本会在当前连接的数据库中产生临时数据（脚本会自清理创建的用例与模板，但任务与执行记录会留存）；演示库与开发库建议分开，或演示前按 §4.3 重置。

---

## 14. 常见问题

| 现象 | 处理 |
| --- | --- |
| 后端启动报数据库连接失败 | 确认 PostgreSQL 已启动、`.env` 中账号密码与库名正确；库不存在时先执行 `python -m billing_qa.db.init_db --demo` |
| 登录提示「账号暂时锁定」 | 连续 5 次密码错误锁定 15 分钟，锁定期满自动恢复；或由拥有 `user:write` 权限的账号在「访问管理 → 用户」中立即解锁 |
| 管理员重置密码后无法沿用旧密码登录 | 重置会生成临时密码并标记强制改密，首次登录需立即修改密码（`must_change_password`） |
| 接口返回 403 | 当前角色缺少对应动作权限（如 `case:create`、`task:stop`）；在「访问管理 → 角色」中调整权限，或联系系统管理员 |
| `/health` 中 `redis: down` | 确认 Redis 已启动；平台会自动降级为进程内状态，但多进程部署需恢复 Redis |
| 任务一直排队 | 检查任务并发/QPS 配置与 Mock 服务是否可达；查看后端日志与 `/tasks/{id}` 状态 |
| 前端 401 自动退出 | JWT 过期（默认 12 小时），重新登录即可 |
| 用例保存后版本未变 | 仅字段实际变更才会生成新版本；如需强制留痕可在变更说明中记录 |
| 导入失败 | 页面入口仅支持 `.json`；先用页面「导入 JSON」走 dry-run 预览，逐条错误会列在弹窗中（格式见 [`docs/CASE_JSON_FORMAT.md`](docs/CASE_JSON_FORMAT.md)） |
| 删除环境 / 模型返回 409 | 资产仍被用例或任务引用（模型另受「启用中的模拟器 / 编写模型不可删」保护）；先解除引用或停用后再删除，详见 §6.1 |
| 演示库出现 `PYTEST-*` / `BATCH20*` 用例或任务 | 运行测试脚本留下的临时数据；执行 `python -m billing_qa.db.init_db --demo --reset`、`python scripts/seed_demo_history.py` 与 `python scripts/seed_environment_runs.py --env-code QA-MOCK` 恢复演示基线 |

---

## 15. 文档索引

| 文档 | 内容 |
| --- | --- |
| [`docs/AGENT_INTEGRATION_GUIDE.md`](docs/AGENT_INTEGRATION_GUIDE.md) | 真实 Agent 报文对接：Basic 鉴权、前置取 `conversationId`、会话回填、时间戳，含替换 Mock 的完整步骤与验证命令 |
| [`docs/DEMO_DATASET.md`](docs/DEMO_DATASET.md) | 演示数据集：环境绑定、Mock 数据口径、72 条用例清单与预期结果、取数能力与演示历史任务构成 |
| [`docs/CASE_JSON_FORMAT.md`](docs/CASE_JSON_FORMAT.md) | 用例 JSON 导入导出格式、字段清单与 dry-run 说明 |
| [`docs/ANSWER_CONTRACT_OPERATIONS.md`](docs/ANSWER_CONTRACT_OPERATIONS.md) | 答案契约判分的通俗操作手册 |
| [`docs/deployment.md`](docs/deployment.md) | 生产部署（systemd / Nginx / 容器化）、安全加固与 MySQL 迁移 |
| [`docs/EXTERNAL_INTEGRATION_QUESTIONS.md`](docs/EXTERNAL_INTEGRATION_QUESTIONS.md) | 外部系统对接待确认事项（Cypher 执行器、数据源、鉴权、快照口径等） |
