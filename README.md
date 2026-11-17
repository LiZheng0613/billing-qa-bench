# 问账智能客服批量测试平台

面向「问账智能客服」场景的自动化回归与质量评估平台：以版本快照为核心，覆盖单轮问答、静态多轮、动态多轮三类用例，内置三重熔断、多裁判混合裁决、全链路 Trace、历史趋势与 Run 对比，并提供 WebSocket 实时大盘与 Prompt 沙盒。

- 后端：Python 3.11+ / FastAPI / SQLAlchemy 2.0 Async / Alembic / PostgreSQL 15+ / Redis
- 前端：Vue 3 `<script setup>` + TypeScript / Vite / Tailwind CSS / Pinia / ECharts
- 交付形态：单仓库（`backend` + `frontend`），本机即可完成全链路自测，无需外部大模型

---

## 1. 核心能力

| 能力 | 说明 |
| --- | --- |
| 版本快照 | 每次保存用例生成 `TestCaseRevision` 快照，`ExecutionRecord.case_version` 精确绑定执行时的用例版本 |
| 版本 Diff / 回滚 | 字段级 Diff（含嵌套 JSON 路径）、历史修改记录、一键回滚并自动生成新版本 |
| 三类用例 | 单轮问答、静态多轮脚本、动态多轮（目标驱动 + 隐藏信息挤牙膏式释放） |
| 动态多轮状态机 | 模拟器按 `CONTINUE / GOAL_ACHIEVED / GIVE_UP` 驱动，轮次上限与硬上限双重保护 |
| 三重熔断 | 最大轮次 `MAX_TURNS_EXCEEDED`、复读检测 `REPETITION_ABORT`、模拟器放弃 `GIVE_UP`；熔断一律判不通过 |
| 混合裁决 | 规则硬校验（金额/笔数/日期/必须包含/禁止包含） + LLM 裁判；支持 `AVG` 平均分与 `VETO` 一票否决，多裁判可并行 |
| Prompt 资产 | 模板版本管理、`LATEST / PINNED` 引用策略、单用例覆盖、引用影响面分析、在线试跑沙盒 |
| 执行与观测 | 批量任务并发 / QPS 限流、环境类失败自动重试、任务终止、失败断点重跑、全链路 Trace、原始报文留存 |
| 指标大盘 | 通过率、平均 TTFT、P95 时延、Token 消耗、吞吐与预计剩余时间；WebSocket 推送，断线自动降级轮询 |
| 视觉分析 | 单用例历史趋势（得分 / 时延 / 稳定性）、两次执行 Run 对比 |
| 安全 | JWT 单管理员登录、环境变量与模型 Key 使用 Fernet 加密落库、敏感值掩码展示 |

---

## 2. 目录结构

```
batch_test/
├── backend/
│   ├── billing_qa/            # 应用包（避免与 site-packages 中的 app 冲突）
│   │   ├── api/v1/            # auth / environments / models / prompts / cases / tasks / executions / ws / calibers / candidates / capabilities / grader / stats
│   │   ├── core/              # 配置、日志、安全、异常、加密、任务控制面
│   │   ├── db/                # 引擎/会话、建库初始化、种子数据、演示数据
│   │   ├── models/            # 18 张 ORM 表
│   │   ├── schemas/           # Pydantic 请求/响应模型
│   │   └── services/
│   │       ├── adapter/       # 被测系统适配（SSE / JSON、模板渲染、JSONPath 提取、前置鉴权钩子）
│   │       ├── author/        # AI 用例草稿生成（数值依据校验、字段白名单、多候选去重）
│   │       ├── capability/    # 取数通道与能力模板（Cypher 工具 / 只读 SQL / HTTP 查询接口）
│   │       ├── engine/        # 执行器、调度器、规则校验、裁判、指标聚合
│   │       ├── grading/       # 答案契约判分（事实 / 行为 / 语义绑定 / 变异自检）
│   │       ├── llm/           # OpenAI 兼容客户端（限流 / 重试 / 严格 JSON）
│   │       ├── revision/      # 版本快照与 Diff
│   │       └── io/            # JSON 导入导出（Excel 仅保留兼容）
│   ├── migrations/            # Alembic 迁移脚本
│   ├── mock_servers/          # 内置 Mock：LLM(8001) 与被测客服系统(8002)
│   ├── scripts/               # api_smoke.py / e2e_smoke.py / seed_demo_history.py / migrate_mysql_to_postgres.py
│   ├── tests/                 # 242 项 pytest
│   ├── alembic.ini
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   └── src/
│       ├── api/               # fetch 封装（统一错误、401 登出、鉴权下载）
│       ├── components/        # AppShell / BaseModal / StatusPill / JsonViewer 等
│       ├── composables/       # useEChart
│       ├── stores/            # auth / toast / breadcrumb
│       ├── views/             # 大盘 / 批量任务与详情 / 用例与详情 / Prompt / Trace / 环境与模型 / 运营后台
│       └── utils/
└── README.md
```

---

## 3. 快速开始

### 3.1 前置要求

| 组件 | 版本 |
| --- | --- |
| Python | 3.11+（开发与验证使用 3.14） |
| Node.js | 18+（验证使用 22） |
| PostgreSQL | 15+（UTF-8 编码） |
| Redis | 6.0+ |

本机默认连接：PostgreSQL `127.0.0.1:5432`（postgres/postgres，库名 `batch_test`）、Redis `redis://127.0.0.1:6379/1`。可通过环境变量覆盖。

### 3.2 启动依赖

```powershell
# PostgreSQL（示例，按本机实际情况调整）
net start postgresql-x64-16

# Redis（示例）
redis-server
```

### 3.3 后端

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

- `--reset` 会删除 `test_case` / `test_case_revision` / `execution_record` / `turn_trace` / `judge_result` / `fact_result` / `gold_query` / `batch_task` / `case_candidate` 中的全部数据，保留环境、模型、Prompt 等基础资产，再重新写入演示用例、业务口径与候选池。
- 需要演示大盘、趋势与 Run 对比时，再执行 `python scripts/seed_demo_history.py`：脚本会真实调用 Mock 服务生成 3 条已完成批量任务（全量回归 / 单轮与契约专项 / 动态多轮专项），以及对应的执行记录、轮次轨迹与裁判明细，并回填近 3 天历史时间。脚本幂等，重复执行只重建 `DEMO-TASK-*`。

- Swagger：<http://127.0.0.1:8000/api/v1/docs>
- 健康检查：<http://127.0.0.1:8000/health>

> 表结构只有一条链路：`init_db` 内部调用 `billing_qa.db.migrations.upgrade_schema()`，即 `alembic upgrade head`（空库会按迁移脚本从零建表），不再使用 `create_all` 或 `alembic stamp`。后续结构变更仍是 `alembic revision --autogenerate` + `alembic upgrade head`。

### 3.4 内置 Mock 服务（离线自测必需）

```powershell
cd backend
# Mock LLM：OpenAI 兼容 /v1/chat/completions，支持 MOCK 注入指令
python -m uvicorn mock_servers.llm_api:app --host 127.0.0.1 --port 8001

# Mock 被测客服系统：/api/v1/chat，支持 SSE 流式
python -m uvicorn mock_servers.target_api:app --host 127.0.0.1 --port 8002
```

- 演示数据中的环境 `QA-MOCK`（SSE）与 `QA-MOCK-JSON`（非流式）均指向 `http://127.0.0.1:8002/api/v1/chat`。
- Mock 被测系统内置电信计费问账数据，以账单、offer 订购、套餐月费/合约、套餐用量、账户余额五类数据型问题为主，并保留国际漫游、增值业务、欠费停机、融合宽带/IPTV 等场景：
  - **账单**：`13800138000` 账单 268.50 / 4 项（套餐 199 + 超套 27 + 漫游 32.50 + 彩铃 10）；`13900139000` 账单 356.80 / 5 项（融合套餐，含副卡 30、IPTV 20）。
  - **套餐用量**：`13800138000` 60GB 套餐用 69GB，超套 9GB / 27 元；`13900139000` 超套 15GB / 45 元；`13600136000` 30GB 仅用 18GB，无超套。
  - **offer 目录**：可订购 `OFF-DATA-20`（20GB 加油包 20/15）、`OFF-VOICE-300`（300 分钟 15/12）、`OFF-5G-UP`（5G 升级包 30/24，合约 12 个月）、`OFF-ROAM-ASIA`（亚洲漫游日包 25/20）；已订购 138 的 5G畅享199（至 2027-03-31）+ 彩铃、139 的全家享239（至 2027-08-31）+ IPTV + 视频会员（22.80/18.00）、137 的 4G飞享59（至 2026-12-31）、136 的 5G畅享129 + 300M 宽带、135 的全球通399（至 2028-01-31）+ 1000M 宽带。
  - **账户余额**：138 可用 86.50 / 赠费 20 至 2026-12-31；139 可用 320 / 冻结 50 / 合计 370；137 余额 0 / 欠费 118（2 期 × 59）/ 欠费停机；136 可用 45.60 / 赠费 10 至 2026-11-30；135 可用 1200。
  - **其他存量场景**：`13700137000` 欠费 118.00 / 停机、`13800138000` 日本漫游 32.50、`13500135000` 韩国漫游 69.00。

### 3.5 前端

```powershell
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173
```

Vite 已配置 `/api` 与 `/health` 反向代理到 `http://127.0.0.1:8000`，并开启 WebSocket 透传。

### 3.6 登录

| 账号 | 密码 |
| --- | --- |
| `admin` | `admin123` |

账号密码可用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 覆盖；生产环境务必同时替换 `SECRET_KEY`。

---

## 4. 首次使用路径

1. **环境与模型**：确认 `QA-MOCK` 环境探活成功；确认 `MOCK-SIM`（模拟器）与 `MOCK-JUDGE` / `MOCK-JUDGE-2`（裁判）连通性测试通过。
2. **Prompt 资产**：查看内置模板 `SIM_DEFAULT_BILLING_USER` 与 `JUDGE_DEFAULT_ZERO_TOLERANCE`，可在沙盒中试跑。
3. **用例资产**：浏览 57 条电信计费演示用例（55 条 `ACTIVE` + 1 条 `DRAFT` + 1 条 `DISABLED`；其中 25 条答案契约、32 条 4 维评分）；勾选行（或「全选筛选结果」）后点右上角「批量执行（N）」，可直接建任务并跳转大盘；行内「历史」查看该用例全部执行记录，打开「趋势 / 对比 / 版本」体验 Diff 与历史分析。
4. **测试大盘**：也可在「新建批量任务」中勾选用例（支持全选筛选结果，覆盖上百条用例）、设置并发数与 QPS，执行后 WebSocket 实时刷新进度；选中任务即为「批量任务详情」，点击用例行进入单笔执行结果。
5. **执行记录**：列表查看全部批量 / 调试 / 重跑记录，点击任意记录进入单笔结果页（对话轮次、裁判明细、规则校验、用例快照与原始报文）；对失败任务可断点重跑。

### 用例评审与冻结

答案契约用例在进入数据集前必须走「评审 → 冻结」两步，入口都在用例资产行内；4 维评分用例一旦进入评审态（待评审 / 已通过 / 已驳回）也保留复审与状态回退入口。

- **评审**：行内「评审 / 复审 / 重新评审」打开评审弹窗，展示当前评审状态、评审人与 Must 事实取证进度；支持「审核通过」「驳回」「撤回评审 / 重置为待评审」。
  - 审核通过会执行事实闸门：仍有 Must 事实处于 `PENDING` / `CONFLICT` / `NO_EVIDENCE` 时直接拒绝，并在弹窗内列出未取证事实。
  - 驳回必须填写原因，原因写入版本变更记录，便于整改追溯；驳回后用例显示「已驳回」，冻结入口隐藏。
  - 已通过的用例修改契约内容（事实、响应契约、取数计划、账期等）会自动退回「待评审」并清空评审人，避免旧审批为新内容背书。
- **冻结**：评审通过后才出现「冻结」按钮，需填写数据集版本；冻结时再次校验 Must 事实已 `VERIFIED` / `WAIVED`。
- 取证状态在运营后台「事实验收」维护；单条事实被撤销或标记冲突时，已通过用例同样自动退回待评审。

### 名称-数值绑定（判分语义）

事实判分不再只看「答案里出现过这个数字」，而是要求数值归属到正确的业务名称，避免 `账单总额=100 / 税额=10` 被写成 `账单总额=10 / 税额=100` 时仍然通过。三层优先级：

1. **JSON key 绑定**：最终答案是 JSON（或带 ```json 代码块）时，按字段名直接取值，如 `{"total_amount": 268.5, "item_count": 4}`。
2. **名称绑定**：中文散文里名称与数值需处于同一分句；仅跨行 / 冒号列表等场景允许短窗口兜底。
3. **纯数值兜底**：答案完全没出现业务名称时按数值匹配；若该数值已明确绑定到另一个业务名称，则判「数值归属冲突」而不是复用。

- 名称来源：事实 `label` + `aliases` + 事实 `id` + 包络级 `aliases`；英文 key（`total_amount` / `tax`）与中文别名可混用，比较时忽略大小写、空白、下划线、连字符与标点。
- `aliases` 在用例编辑抽屉中按逗号分隔维护；勾选「必须绑定名称」等价于 `require_name=true`，此时名称缺失或未与数值绑定都判失败。
- 规则硬校验与答案契约判分**互斥**：`LEGACY`（4 维评分）模式才执行 `expected_rules` 硬校验并展示「规则校验」页签；`ANSWER_CONTRACT` 模式由事实 / 行为闸门判定，不再执行规则硬校验，结果页只展示「契约判分」。两者的名称 / 数值绑定逻辑保持一致。
- 判分器版本随语义升级为 `answer-grader@0.2`；旧用例默认保持 `LEGACY`，不设置别名时行为与升级前一致。

### 批量执行与并发控制

- 任务可配置 `concurrency`（1–20）与 `qps_limit`；调度器用信号量控制同时在途用例数，用任务级令牌桶保证整体 QPS 不超限。
- 执行明细按用例取最新一次执行做统计，历史尝试记录保留在 `execution_record` 中，`attempt_no` 标记第几次尝试。
- 前端「选择用例」支持按编号 / 名称 / 标签筛选后一键全选筛选结果，用例列表会分页拉取全量数据，适合上百条用例的回归。
- 批量入口有两处：**用例资产**页勾选行 / 全选筛选结果后点「批量执行（N）」，创建成功自动跳转 `/?task=<id>` 并选中新任务；**测试大盘**的「新建批量任务」适合集中编排环境与重试参数。仅 `ACTIVE` 用例可被执行。
- 执行范围支持两种模式（两个入口均可用）：
  - `SELECTED`：执行当前勾选的用例，支持本页全选与「全选筛选结果（跨页）」。
  - `ALL_ACTIVE`：由后端一次性解析用例库中全部启用用例（跨分页、不受当前筛选影响），前端只传 `case_scope=ALL_ACTIVE`，避免上百条用例逐页勾选或请求体过大。

### 批量任务详情与单笔执行结果

- **批量任务详情**（测试大盘选中任务）：展示该任务的通过分布（通过 / 失败 / 异常 / 轮次超限 / 复读熔断 / 执行中 / 等待中）、进度、TTFT/P95/Token 等指标，以及按用例汇总的最新一次执行结果（含尝试次数），支持分页，覆盖上百条用例。
- **单笔执行结果**（`/trace?execution=<id>`）：聚焦一次执行，展示对话轮次、裁判明细、规则校验、用例快照与原始报文；可返回所属批量任务详情，或跳转该用例全部历史。批量详情点击用例行、用例调试完成、趋势面板「查看 Trace」均复用此页面。
- **用例历史**（`/trace?case=<id>`，或用例资产行内「历史」）：列出该用例全部批量 / 调试 / 重跑记录并分页；批量任务的「全部执行记录（含重试）」按任务列出每次尝试。用例级汇总统一取最新一次尝试，避免重试重复计数。

### 请求变量与多轮会话绑定

环境模板（`headers_template` / `body_template`）用 `{{variable}}` 占位，具体取值按用例注入：

- **请求变量**：用例级 `request_variables`（key-value，值支持字符串 / 数字 / JSON），在用例抽屉「请求变量」中按行维护或直接编辑 JSON；不放敏感信息，明文保存。
- **三层优先级**：运行时变量（`query` / `session_id` / `history` / `history_text` / `turn` / `timestamp_iso`）> 用例 `request_variables` > 环境变量池。运行时保留字不允许在用例变量中重复定义。
- **时间戳**：`timestamp_iso` 为请求时刻的 ISO 8601 字符串（如 `2026-11-17T18:00:00.123`），每轮请求刷新。
- **Basic 鉴权**：环境可配置账号 + 密码（密码加密存储），执行时自动生成 `Authorization: Basic base64(账号:密码)`；请求头模板显式写了 `Authorization` 时以模板为准。
- **前置接口钩子**：执行前调用一次，支持用环境变量池与用例变量渲染 headers / body，并用 `extract`（变量名 → JSONPath）提取 `conversationId`、Token 等字段，供主请求与后续轮次复用；旧的 `token_jsonpath` / `token_var` 配置继续兼容。
- **会话绑定**：会话方式由**执行环境统一决定**，所有用例跟随，不在用例上单独选择：

  | 模式 | 行为 |
  | --- | --- |
  | `SERVER_RETURNED`（默认） | 首轮渲染空会话值，由被测接口返回后回填，后续轮次沿用 |
  | `CLIENT_GENERATED` | 执行开始时生成唯一值（`exec-{execution_id}-xxxxxxxx`），全程各轮一致 |

- **字段名由模板决定**：例如 `"conversationId": "{{session_id}}"` 或 `"sessionid": "{{session_id}}"`，不同接口可各自定义，平台不写死字段名；首轮渲染为空字符串，由目标接口按「首轮对话」处理。
- **接口配置页**：环境编辑弹窗内提供「Basic 鉴权」「前置接口钩子」「多轮会话绑定」、可用占位符清单与变量来源标注，并可选一条示例用例做**报文预览**（只读，不落库）。
- **执行结果**：对话轮次页展示每轮会话徽标、会话连续性小结、用例变量来源标记；不一致时会提示「上下文可能未延续」。

真实 Agent 报文（前置取 `conversationId`、Basic 鉴权、首轮回填空 `sessionid`）的完整配置示例见
[`docs/AGENT_INTEGRATION_GUIDE.md`](docs/AGENT_INTEGRATION_GUIDE.md)。

相关接口：`POST /environments/{id}/preview`（body `{"case_id": 123}`，返回渲染后的 `headers` / `body` / `origins` / `session_mode_resolved`）。

### 环境类失败自动重试

任务级配置：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `retry_on_env_error` | `true` | 是否开启环境类失败自动重试 |
| `max_env_retries` | `2` | 单个用例最大自动重试次数（0 表示不重试） |
| `retry_backoff_seconds` | `3` | 首次重试的基础退避秒数，之后按 2 倍指数递增，单次封顶 60 秒 |

错误分类规则：

| 分类 | 典型场景 | 是否自动重试 |
| --- | --- | --- |
| `ENV` | 连接失败、连接/读取超时、HTTP 429/5xx、SSE 无数据帧、网关 HTML 错误页、鉴权服务不可达 | 是 |
| `BUSINESS` | 规则硬校验不通过、裁判低分、轮次超限、复读熔断、模板变量缺失、用例配置非法、4xx 参数错误 | 否 |

- 重试同样占用并发信号量与 QPS 配额，不会突破任务限制；终止任务会立即中断重试等待。
- 每次尝试生成独立执行记录（`source=RETRY`、`attempt_no` 递增），`error_kind` 记录分类，Trace 可逐次回看。
- 任务指标中的 `env_retry_count` 表示触发过自动重试的用例数。

演示用例与预期结果（55 条 `ACTIVE`：30 条 4 维评分 + 25 条答案契约，聚焦账单 / offer 订购 / 套餐 / 用量 / 余额）：

| 用例 | 形态 / 判分 | 场景 | 预期 |
| --- | --- | --- | --- |
| `DEMO-SINGLE-001` | 单轮 · 4 维评分 | 5G 套餐账单总额与费用项数 | PASS |
| `DEMO-SINGLE-BILL-DETAIL` | 单轮 · 4 维评分 | 账单四项费用构成明细 | PASS |
| `DEMO-SINGLE-PACKAGE-FEE` | 单轮 · 4 维评分 | 套餐固定月费 199 元 | PASS |
| `DEMO-SINGLE-SUBCARD` | 单轮 · 4 维评分 | 融合套餐副卡功能费 30 元 | PASS |
| `DEMO-SINGLE-USAGE` | 单轮 · 4 维评分 | 套餐余量与 9GB 流量超套费 | PASS |
| `DEMO-SINGLE-VOICE-SMS` | 单轮 · 4 维评分 | 流量 12GB / 语音 290 分钟 / 短信 88 条余量 | PASS |
| `DEMO-SINGLE-OFFER-AVAILABLE` | 单轮 · 4 维评分 | 可订购 4 个 offer 与优惠价 | PASS |
| `DEMO-SINGLE-OFFER-ORDERED` | 单轮 · 4 维评分 | 已订购 offer 与合约到期日 | PASS |
| `DEMO-SINGLE-OFFER-CONTRACT` | 单轮 · 4 维评分 | 融合套餐 3 个 offer 与视频会员优惠价 | PASS |
| `DEMO-SINGLE-BALANCE` | 单轮 · 4 维评分 | 可用余额 86.50 / 赠费到期日 | PASS |
| `DEMO-SINGLE-BALANCE-FROZEN` | 单轮 · 4 维评分 | 可用 320 + 冻结 50 = 合计 370 | PASS |
| `DEMO-SINGLE-BALANCE-ZERO` | 单轮 · 4 维评分 | 零余额与欠费停机联动 | PASS |
| `DEMO-SINGLE-ARREARS` | 单轮 · 4 维评分 | 欠费 118 元、2 期账期与停机状态 | PASS |
| `DEMO-SINGLE-ROAMING` | 单轮 · 4 维评分 | 日本漫游日期、流量与费用 | PASS |
| `DEMO-SINGLE-BROADBAND` | 单轮 · 4 维评分 | 融合宽带与 IPTV 费用 | PASS |
| `DEMO-SINGLE-VAS` | 单轮 · 4 维评分 | 增值业务订购与逐项扣费 | PASS |
| `DEMO-SINGLE-FAIL` | 单轮 · 4 维评分 | 数据幻觉红线（`[MOCK:FAIL]`） | FAIL（VETO 一票否决） |
| `DEMO-SINGLE-VETO` | 单轮 · 4 维评分 | 错误金额注入（`错误金额`） | FAIL（规则硬校验拦截） |
| `DEMO-SINGLE-ERROR` | 单轮 · 4 维评分 | 被测系统异常注入（`模拟崩溃`） | ERROR |
| `DEMO-CONTRACT-001` | 单轮 · 答案契约 | 账单金额 + 费用项数 + 手机号 | PASS |
| `DEMO-CONTRACT-ALIAS-JSON` | 单轮 · 答案契约 | 英文 JSON key 名称绑定（`total_amount` / `item_count`） | PASS |
| `DEMO-CONTRACT-BILL-ITEMS` | 单轮 · 答案契约 | 四项费用名称集合 | PASS |
| `DEMO-CONTRACT-PACKAGE` | 单轮 · 答案契约 | 套餐月费 199 元 + 套餐费用项 | PASS |
| `DEMO-CONTRACT-SUBCARD` | 单轮 · 答案契约 | 副卡功能费 30 元与账单总额 | PASS |
| `DEMO-CONTRACT-USAGE` | 单轮 · 答案契约 | 超套 9GB、超套费与套餐名称 | PASS |
| `DEMO-CONTRACT-USAGE-NO-OVERAGE` | 单轮 · 答案契约 | 未超套（零超套费 + 剩余 12GB） | PASS |
| `DEMO-CONTRACT-OFFER-AVAILABLE` | 单轮 · 答案契约 | 可订购 offer 集合与加油包优惠价 | PASS |
| `DEMO-CONTRACT-OFFER-ORDERED` | 单轮 · 答案契约 | 已订购数量、主套餐月费与合约到期日 | PASS |
| `DEMO-CONTRACT-OFFER-CONTRACT` | 单轮 · 答案契约 | 融合套餐合约与视频会员 22.80/18.00 | PASS |
| `DEMO-CONTRACT-OFFER-COUNT` | 单轮 · 答案契约 | 全球通可订购 offer 数量与漫游日包 | PASS |
| `DEMO-CONTRACT-OFFER-UPGRADE` | 单轮 · 答案契约 | 5G 升级包 30/24 与 12 个月承诺期 | PASS |
| `DEMO-CONTRACT-BALANCE` | 单轮 · 答案契约 | 可用余额 + 赠费金额 + 到期日 | PASS |
| `DEMO-CONTRACT-BALANCE-FROZEN` | 单轮 · 答案契约 | 可用 / 冻结 / 合计三项余额拆分 | PASS |
| `DEMO-CONTRACT-BALANCE-GRANT` | 单轮 · 答案契约 | 小额余额 45.60 + 赠费 10 到期日 | PASS |
| `DEMO-CONTRACT-BALANCE-ZERO` | 单轮 · 答案契约 | 零余额 + 欠费 118 + 停机状态 | PASS |
| `DEMO-CONTRACT-ARREARS` | 单轮 · 答案契约 | 欠费金额、账期集合与停机状态（禁止复机话术） | PASS |
| `DEMO-CONTRACT-WRONG-AMOUNT` | 单轮 · 答案契约 | 错误金额 999.00 命中 MustNot | FAIL（事实闸门 + 安全红线） |
| `DEMO-CONTRACT-LUCKY-PASS` | 单轮 · 答案契约 | 余额答案正确但无取数证据 | PASS（`lucky_pass` 仅诊断） |
| `DEMO-CONTRACT-UNDERSPECIFIED` | 单轮 · 答案契约 | 未指定账期，允许带假设作答 | PASS |
| `DEMO-CONTRACT-NO-NUMBER` | 单轮 · 答案契约 | 未提供号码，澄清槽位 | PASS |
| `DEMO-CONTRACT-AMBIGUOUS` | 单轮 · 答案契约 | 指代不明，澄清或声明假设 | PASS |
| `DEMO-CONTRACT-MULTI-INTENT` | 单轮 · 答案契约 | 双号码多意图，允许部分披露 | PASS |
| `DEMO-CONTRACT-CONTRADICTORY` | 单轮 · 答案契约 | 矛盾账期 2026-13，澄清有效账期 | PASS |
| `DEMO-CONTRACT-UNAUTHORIZED` | 单轮 · 答案契约 | 越权查询他人账单，拒绝并说明 | PASS |
| `DEMO-STATIC-001` | 静态多轮 · 4 维评分 | 指代继承与超套费追问 | PASS |
| `DEMO-STATIC-OFFER` | 静态多轮 · 4 维评分 | 可订购 offer 列表到加油包价格 | PASS |
| `DEMO-STATIC-BALANCE` | 静态多轮 · 4 维评分 | 可用余额到合计余额追问 | PASS |
| `DEMO-STATIC-FAILFAST` | 静态多轮 · 4 维评分 | 首轮失败立即中断 | FAIL（fail_fast） |
| `DEMO-DYNAMIC-001` | 动态多轮 · 4 维评分 | 隐藏手机号与账期挤牙膏式释放 | PASS |
| `DEMO-DYNAMIC-OFFER` | 动态多轮 · 4 维评分 | 隐藏号码的可订购 offer 咨询 | PASS |
| `DEMO-DYNAMIC-BALANCE` | 动态多轮 · 4 维评分 | 隐藏号码的余额拆分咨询 | PASS |
| `DEMO-DYNAMIC-BALANCE-GRANT` | 动态多轮 · 4 维评分 | 隐藏号码的赠费到期咨询 | PASS |
| `DEMO-DYNAMIC-USAGE` | 动态多轮 · 4 维评分 | 套餐余量与超套费追问 | PASS |
| `DEMO-DYNAMIC-MAXTURNS` | 动态多轮 · 4 维评分 | 最大轮次熔断 | `MAX_TURNS_EXCEEDED`（判不通过） |
| `DEMO-DYNAMIC-REPEAT` | 动态多轮 · 4 维评分 | 复读机熔断 | `REPETITION_ABORT`（判不通过） |

另有 `DEMO-DRAFT-001`（`DRAFT`，双账期对比待补充）与 `DEMO-DISABLED-001`（`DISABLED` + `REJECTED`，隐私越权）仅用于状态/审核/版本演示，不参与批量回归。

数据集规模：57 条用例 / 57 条版本快照 / 8 条候选（NEW/TRIAGED/PROMOTED/REJECTED）/ 14 条业务口径（含 `offer_contract`、`account_balance`）；`seed_demo_history.py` 生成 `DEMO-TASK-001`（55 例全量）、`DEMO-TASK-002`（44 例单轮与契约）、`DEMO-TASK-003`（7 例动态多轮），共 106 条执行记录与 136 条事实结果（`fact_result`）。

Mock 注入指令（写在用例 `query` / `expected_answer` / 隐藏上下文中即可触发）：

- `[MOCK:FAIL]`：裁判返回低分与红线命中
- `[MOCK:MAXTURNS]`：模拟器持续追问，触发轮次熔断
- `[MOCK:REPEAT]`：被测系统复读，触发复读熔断
- `[MOCK:GIVEUP]`：模拟器主动放弃
- `[MOCK:REQUIRE_SESSION]`：请求未携带 `session_id` / `sessionId` / `conversationId` 时返回 400，用于验证会话绑定
- `错误金额`：Mock 被测系统返回错误账单金额，验证规则硬校验
- `模拟崩溃` / `触发异常`：Mock 被测系统返回 HTTP 500，验证异常分类与重试

`python scripts/e2e_smoke.py` 除校验演示用例状态分布外，还会额外跑一轮「请求变量 + 客户端生成会话」检查：
校验 headers/body 注入、用例变量覆盖环境变量池、会话值贯穿第二轮，以及 Mock 的 `received_params` 回显。

---

## 5. 配置项

配置来自 `backend/.env`（大小写不敏感，完整示例见 `.env.example`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_ENV` / `DEBUG` | `dev` / `true` | 运行环境与调试开关 |
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
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin123` | 单管理员账号 |
| `DEFAULT_CONCURRENCY` / `DEFAULT_QPS_LIMIT` | `3` / `5` | 任务默认并发与 QPS |
| `SCHEDULE_POLL_INTERVAL_SECONDS` | `15` | 定时计划轮询间隔（秒） |
| `PASS_THRESHOLD` | `60` | 默认通过分数线 |
| `HTTP_TIMEOUT_SECONDS` | `60` | 被测接口默认超时 |
| `MAX_TURNS_HARD_LIMIT` | `20` | 轮次硬上限（超过即熔断） |
| `REPETITION_SIMILARITY_THRESHOLD` | `0.9` | 复读检测相似度阈值 |
| `TASK_RETENTION_DAYS` | `30` | 任务数据保留天数 |
| `MOCK_LLM_BASE_URL` / `MOCK_TARGET_BASE_URL` | 8001 / 8002 | 内置 Mock 地址 |

---

## 6. 数据模型（18 张业务表）

| 表 | 作用 |
| --- | --- |
| `environment` | 被测系统环境（Base URL、方法、路径、流式开关、超时、SSL、请求头/体模板、提取器、前置鉴权钩子、多轮会话绑定方式） |
| `environment_variable` | 环境变量池，敏感值 Fernet 加密、接口返回掩码 |
| `model_config` | 模型注册（模拟器 / 裁判、OpenAI 兼容参数、限流与重试、加密 Key） |
| `prompt_template` | Prompt 模板主表（类型、当前版本、引用计数、是否内置） |
| `prompt_template_version` | 模板版本快照（内容、变量列表、变更说明） |
| `test_case` | 用例主表（三类配置、规则、模板引用策略、模型与裁判策略、当前版本、判分方式、答案契约、请求变量、会话绑定方式） |
| `test_case_revision` | 用例版本快照（全字段快照、变更类型、变更说明、操作人） |
| `batch_task` | 批量任务（并发、QPS、环境失败重试策略、状态、统计、控制标志） |
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

---

## 7. 引擎工作方式

1. **快照绑定**：调度开始前读取用例当前版本，写入 `ExecutionRecord.case_version`，执行期间不感知后续编辑。
2. **Prompt 解析**：按 `LATEST / PINNED` 策略解析模拟器与裁判模板版本，并叠加用例级覆盖。
3. **对话循环**（静态 / 动态多轮）：
   - 渲染模拟器 Prompt（目标、隐藏信息、轮次、历史）→ 调 LLM → 解析 `thought / status / query`。
   - 调被测系统（SSE 逐块累计首字时延）→ 记录 `turn_trace` → `LEGACY` 模式下做规则校验。
   - 判定终止：目标达成、模拟器放弃、复读、轮次超限。
4. **混合裁决**：`LEGACY` 模式为规则硬校验 + 多个 LLM 裁判并行，`VETO` 任一审判红即失败、`AVG` 取均值；`ANSWER_CONTRACT` 模式由四道闸门（安全 / 行为 / 事实 / 扩展）判定，LLM 裁判只提供质量分参考。最终以最后一轮结论为准。
5. **指标聚合**：通过率、平均 TTFT、P95 时延、Token、吞吐、预计剩余时间；进度事件经 Redis + WebSocket 推送。

---

## 8. API 概览

统一前缀 `/api/v1`，除登录外均需 `Authorization: Bearer <token>`。

| 分组 | 主要接口 |
| --- | --- |
| 认证 | `POST /auth/login`、`GET /auth/me` |
| 环境 | `GET/POST /environments`、`PUT/DELETE /environments/{id}`、`GET/PUT /environments/{id}/variables`、`POST /environments/{id}/health-check`、`GET /environments/{id}/usage`、`POST /environments/{id}/preview`（选示例用例渲染报文） |
| 模型 | `GET/POST /models`、`PUT/DELETE /models/{id}`、`POST /models/{id}/test`、`POST /models/opencode/catalog`（拉取 OpenCode Serve 可用模型与 Agent） |
| Prompt | `GET/POST /prompts`、`GET/PUT/DELETE /prompts/{id}`、`POST /prompts/{id}/versions`、`GET /prompts/{id}/versions/{version}`、`GET /prompts/{id}/usage`、`POST /prompts/{id}/sandbox` |
| 用例 | `GET/POST /cases`、`GET/PUT/DELETE /cases/{id}`、`POST /cases/{id}/debug`、`POST /cases/ai-draft`（AI 草稿）、`GET /cases/{id}/facts`、`POST /cases/{id}/facts/{fact_id}/verify`、`POST /cases/{id}/review`、`POST /cases/{id}/freeze`、`GET /cases/{id}/revisions`、`GET /cases/{id}/revisions/diff`、`POST /cases/{id}/rollback`、`GET /cases/{id}/trend`、`GET /cases/{id}/executions`、`GET /cases/compare/runs`、`POST /cases/import`、`GET /cases/export` |
| 取数资产 | `GET/POST /data-sources`、`GET/PUT /data-sources/{id}`、`GET/POST /capabilities`、`GET/PUT /capabilities/{id}` |
| 候选池 | `GET/POST /candidates`、`GET/PATCH /candidates/{id}`、`POST /candidates/{id}/reject` |
| 业务口径 | `GET/POST /calibers`、`GET/PUT/DELETE /calibers/{id}` |
| 判分器自检 | `GET /grader/mutants`、`POST /grader/evals/run`、`GET /grader/evals` |
| 统计 | `GET /stats/overview`、`GET /stats/facts`、`GET /stats/datasets` |
| 任务 | `GET/POST /tasks`、`GET /tasks/{id}`、`POST /tasks/{id}/start`、`POST /tasks/{id}/abort`、`POST /tasks/{id}/retry-failed`、`POST /tasks/{id}/run-now`（立即执行一次定时计划）、`PUT /tasks/{id}/schedule`（编辑定时计划）、`GET /tasks/{id}/runs`（计划执行历史）、`GET /tasks/{id}/metrics`、`GET /tasks/{id}/executions`（`latest_per_case=true` 按用例取最新一次并返回尝试次数） |
| 执行 | `GET /executions`、`GET /executions/{id}`、`GET /executions/{id}/raw` |
| 实时 | `WS /ws/tasks/{task_id}?token=<jwt>` |

---

## 9. 测试与验证

### 9.1 后端单元/集成测试

```powershell
cd backend
python -m pytest -q          # 242 passed（Mock / PostgreSQL 依赖不可用时自动 skip）
python -m ruff check billing_qa scripts tests
```

### 9.2 API 冒烟（需 API 已启动）

```powershell
cd backend
python scripts/api_smoke.py  # 43 项全部通过
```

覆盖：登录、环境变量掩码与探活、模型连通性、模板种子/版本/引用影响、模板发布与版本历史即时刷新、用例 CRUD、版本 Diff 与回滚、单条调试、沙盒、任务全生命周期（启动/终止/断点重跑/定时计划）、指标聚合、Trace 与原始报文、趋势聚合。

> 冒烟脚本使用独立临时模板验证发布流程并在结束时删除，不会污染内置模板内容。

### 9.3 引擎端到端冒烟

```powershell
cd backend
python scripts/e2e_smoke.py  # 需 Mock 8001/8002 已启动，且已 init_db --demo
```

预期命中：`PASS ×3`、`FAIL ×1`、`MAX_TURNS_EXCEEDED ×1`。

### 9.4 前端

```powershell
cd frontend
npm run typecheck   # vue-tsc，noUnusedLocals / noUnusedParameters 已开启
npm run build       # 产物在 dist/
```

---

## 10. 部署

生产部署（Linux + systemd + Nginx，或容器化）与安全加固清单见 [`docs/deployment.md`](docs/deployment.md)。

用例 JSON 导入导出格式、字段清单与 dry-run 说明见 [`docs/CASE_JSON_FORMAT.md`](docs/CASE_JSON_FORMAT.md)。
答案契约判分的通俗操作手册见 [`docs/ANSWER_CONTRACT_OPERATIONS.md`](docs/ANSWER_CONTRACT_OPERATIONS.md)。
外部系统对接待确认事项（Cypher 执行器、数据源、鉴权、快照口径等）见 [`docs/EXTERNAL_INTEGRATION_QUESTIONS.md`](docs/EXTERNAL_INTEGRATION_QUESTIONS.md)。

---

## 11. 已知限制

- 单管理员模型，无多租户与 RBAC；如需扩展可在 `core/security.py` 与 `api/v1/auth.py` 上叠加。
- 任务执行器为进程内 asyncio 调度，重启进程会中断进行中的任务（状态保留，可重跑）。
- 动态多轮的「目标达成」判定依赖模拟器输出与裁判结论，Mock 场景下由注入指令确定。
- 前端 ECharts 全量包约 1 MB（gzip 343 KB），已拆分为独立 vendor chunk；如需进一步瘦身可改为按需引入。

---

## 12. 常见问题

| 现象 | 处理 |
| --- | --- |
| 后端启动报数据库连接失败 | 确认 PostgreSQL 已启动、`.env` 中账号密码与库名正确；库不存在时先执行 `python -m billing_qa.db.init_db --demo` |
| `/health` 中 `redis: down` | 确认 Redis 已启动；平台会自动降级为进程内状态，但多进程部署需恢复 Redis |
| 任务一直排队 | 检查任务并发/QPS 配置与 Mock 服务是否可达；查看后端日志与 `/tasks/{id}` 状态 |
| 前端 401 自动退出 | JWT 过期（默认 12 小时），重新登录即可 |
| 用例保存后版本未变 | 仅字段实际变更才会生成新版本；如需强制留痕可在变更说明中记录 |
| 导入失败 | 仅支持 `.json`；先用页面「导入 JSON」走 dry-run 预览，逐条错误会列在弹窗中（格式见 [`docs/CASE_JSON_FORMAT.md`](docs/CASE_JSON_FORMAT.md)） |
