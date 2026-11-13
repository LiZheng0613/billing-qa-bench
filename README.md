# 问账智能客服批量测试平台

面向「问账智能客服」场景的自动化回归与质量评估平台：以版本快照为核心，覆盖单轮问答、静态多轮、动态多轮三类用例，内置三重熔断、多裁判混合裁决、全链路 Trace、历史趋势与 Run 对比，并提供 WebSocket 实时大盘与 Prompt 沙盒。

- 后端：Python 3.11+ / FastAPI / SQLAlchemy 2.0 Async / Alembic / MySQL 8 / Redis
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
| 三重熔断 | 最大轮次 `MAX_TURNS_EXCEEDED`、复读检测 `REPETITION_DETECTED`、模拟器放弃 `SIMULATOR_GAVE_UP`；熔断一律判不通过 |
| 混合裁决 | 规则硬校验（金额/笔数/日期/必须包含/禁止包含） + LLM 裁判；支持 `AVG` 平均分与 `VETO` 一票否决，多裁判可并行 |
| Prompt 资产 | 模板版本管理、`LATEST / PINNED` 引用策略、单用例覆盖、引用影响面分析、在线试跑沙盒 |
| 执行与观测 | 批量任务并发 / QPS 限流、环境类失败自动重试、暂停 / 恢复 / 终止、失败断点重跑、全链路 Trace、原始报文留存 |
| 指标大盘 | 通过率、平均 TTFT、P95 时延、Token 消耗、吞吐与预计剩余时间；WebSocket 推送，断线自动降级轮询 |
| 视觉分析 | 单用例历史趋势（得分 / 时延 / 稳定性）、两次执行 Run 对比 |
| 安全 | JWT 单管理员登录、环境变量与模型 Key 使用 Fernet 加密落库、敏感值掩码展示 |

---

## 2. 目录结构

```
batch_test/
├── backend/
│   ├── billing_qa/            # 应用包（避免与 site-packages 中的 app 冲突）
│   │   ├── api/v1/            # auth / environments / models / prompts / cases / tasks / executions / ws
│   │   ├── core/              # 配置、日志、安全、异常、加密、任务控制面
│   │   ├── db/                # 引擎/会话、建库初始化、种子数据、演示数据
│   │   ├── models/            # 11 张 ORM 表
│   │   ├── schemas/           # Pydantic 请求/响应模型
│   │   └── services/
│   │       ├── adapter/       # 被测系统适配（SSE / JSON、模板渲染、JSONPath 提取、前置鉴权钩子）
│   │       ├── engine/        # 执行器、调度器、规则校验、裁判、指标聚合
│   │       ├── llm/           # OpenAI 兼容客户端（限流 / 重试 / 严格 JSON）
│   │       ├── prompt/        # 模板渲染与变量提取
│   │       ├── revision/      # 版本快照与 Diff
│   │       └── io/            # Excel / JSON 导入导出
│   ├── migrations/            # Alembic 迁移脚本
│   ├── mock_servers/          # 内置 Mock：LLM(8001) 与被测客服系统(8002)
│   ├── scripts/               # api_smoke.py / e2e_smoke.py
│   ├── tests/                 # 57 项 pytest
│   ├── alembic.ini
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   └── src/
│       ├── api/               # fetch 封装（统一错误、401 登出、鉴权下载）
│       ├── components/        # AppShell / BaseModal / StatusPill / JsonViewer 等
│       ├── composables/       # useEChart
│       ├── stores/            # auth / toast
│       ├── views/             # 大盘 / 用例 / Prompt / Trace / 环境与模型
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
| MySQL | 8.0+（`utf8mb4`） |
| Redis | 6.0+ |

本机默认连接：MySQL `127.0.0.1:3306`（root/root，库名 `batch_test`）、Redis `redis://127.0.0.1:6379/1`。可通过环境变量覆盖。

### 3.2 启动依赖

```powershell
# MySQL（示例，按本机实际情况调整）
net start MySQL80

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

# 3) 建库建表 + 内置 Prompt 模板 + 演示数据
python -m billing_qa.db.init_db --demo

# 4) 启动 API（默认 8000）
python -m uvicorn billing_qa.main:app --host 127.0.0.1 --port 8000
```

- Swagger：<http://127.0.0.1:8000/api/v1/docs>
- 健康检查：<http://127.0.0.1:8000/health>

> 初始化脚本会调用 `Base.metadata.create_all` 建表，并执行 `alembic stamp head` 保持迁移状态一致；后续结构变更使用 `alembic revision --autogenerate` + `alembic upgrade head`。

### 3.4 内置 Mock 服务（离线自测必需）

```powershell
cd backend
# Mock LLM：OpenAI 兼容 /v1/chat/completions，支持 MOCK 注入指令
python -m uvicorn mock_servers.llm_api:app --host 127.0.0.1 --port 8001

# Mock 被测客服系统：/api/v1/chat，支持 SSE 流式
python -m uvicorn mock_servers.target_api:app --host 127.0.0.1 --port 8002
```

演示数据中的环境 `QA-MOCK`（SSE）与 `QA-MOCK-JSON`（非流式）均指向 `http://127.0.0.1:8002/api/v1/chat`。

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
3. **用例资产**：浏览 5 条演示用例，打开「版本 / 趋势 / 对比」体验 Diff 与历史分析。
4. **测试大盘**：新建批量任务，勾选用例（支持全选筛选结果，覆盖上百条用例）、设置并发数与 QPS，执行后 WebSocket 实时刷新进度，点击执行明细行跳转 Trace。
5. **执行追踪**：查看对话轮次、裁判明细、规则校验、用例快照与原始报文；对失败任务可断点重跑。

### 批量执行与并发控制

- 任务可配置 `concurrency`（1–20）与 `qps_limit`；调度器用信号量控制同时在途用例数，用任务级令牌桶保证整体 QPS 不超限。
- 执行明细按用例取最新一次执行做统计，历史尝试记录保留在 `execution_record` 中，`attempt_no` 标记第几次尝试。
- 前端「选择用例」支持按编号 / 名称 / 标签筛选后一键全选筛选结果，用例列表会分页拉取全量数据，适合上百条用例的回归。

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

- 重试同样占用并发信号量与 QPS 配额，不会突破任务限制；暂停期间退避计时暂停，终止会立即中断重试等待。
- 每次尝试生成独立执行记录（`source=RETRY`、`attempt_no` 递增），`error_kind` 记录分类，Trace 可逐次回看。
- 任务指标中的 `env_retry_count` 表示触发过自动重试的用例数。

演示用例与预期结果：

| 用例 | 预期 |
| --- | --- |
| `DEMO-SINGLE-001` | PASS |
| `DEMO-SINGLE-FAIL` | FAIL（Mock 注入幻觉） |
| `DEMO-STATIC-001` | PASS |
| `DEMO-DYNAMIC-001` | PASS |
| `DEMO-DYNAMIC-MAXTURNS` | `MAX_TURNS_EXCEEDED`（判不通过） |

Mock 注入指令（写在用例 `query` / `expected_answer` / 隐藏上下文中即可触发）：

- `[MOCK:FAIL]`：裁判返回低分与红线命中
- `[MOCK:MAXTURNS]`：模拟器持续追问，触发轮次熔断
- `[MOCK:REPEAT]`：被测系统复读，触发复读熔断
- `[MOCK:GIVEUP]`：模拟器主动放弃

---

## 5. 配置项

配置来自 `backend/.env`（大小写不敏感，完整示例见 `.env.example`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_ENV` / `DEBUG` | `dev` / `true` | 运行环境与调试开关 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 监听地址与端口 |
| `API_PREFIX` | `/api/v1` | 路由前缀 |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许的跨域来源（逗号分隔） |
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | MySQL 连接 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `root` / `root` | MySQL 账号 |
| `MYSQL_DB` | `batch_test` | 库名（初始化脚本会自动创建） |
| `MYSQL_POOL_SIZE` / `MYSQL_POOL_RECYCLE` | `10` / `1800` | 连接池 |
| `DATABASE_URL` | 空 | 可选，整体覆盖异步连接串 |
| `REDIS_URL` | `redis://127.0.0.1:6379/1` | 控制面与状态存储 |
| `REDIS_KEY_PREFIX` | `batchtest:` | Redis key 前缀 |
| `SECRET_KEY` | 示例值 | JWT 与 Fernet 加密密钥，**生产必须替换** |
| `JWT_ALGORITHM` / `JWT_EXPIRE_MINUTES` | `HS256` / `720` | JWT 配置 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin123` | 单管理员账号 |
| `DEFAULT_CONCURRENCY` / `DEFAULT_QPS_LIMIT` | `3` / `5` | 任务默认并发与 QPS |
| `PASS_THRESHOLD` | `60` | 默认通过分数线 |
| `HTTP_TIMEOUT_SECONDS` | `60` | 被测接口默认超时 |
| `MAX_TURNS_HARD_LIMIT` | `20` | 轮次硬上限（超过即熔断） |
| `REPETITION_SIMILARITY_THRESHOLD` | `0.9` | 复读检测相似度阈值 |
| `TASK_RETENTION_DAYS` | `30` | 任务数据保留天数 |
| `MOCK_LLM_BASE_URL` / `MOCK_TARGET_BASE_URL` | 8001 / 8002 | 内置 Mock 地址 |

---

## 6. 数据模型（11 张表）

| 表 | 作用 |
| --- | --- |
| `environment` | 被测系统环境（Base URL、方法、路径、流式开关、超时、SSL、请求头/体模板、提取器、前置鉴权钩子） |
| `environment_variable` | 环境变量池，敏感值 Fernet 加密、接口返回掩码 |
| `model_config` | 模型注册（模拟器 / 裁判、OpenAI 兼容参数、限流与重试、加密 Key） |
| `prompt_template` | Prompt 模板主表（类型、当前版本、引用计数、是否内置） |
| `prompt_template_version` | 模板版本快照（内容、变量列表、变更说明） |
| `test_case` | 用例主表（三类配置、规则、模板引用策略、模型与裁判策略、当前版本） |
| `test_case_revision` | 用例版本快照（全字段快照、变更类型、变更说明、操作人） |
| `batch_task` | 批量任务（并发、QPS、环境失败重试策略、状态、统计、控制标志） |
| `execution_record` | 单次尝试执行（`case_version` 绑定、尝试次数、错误分类、状态、得分、轮次、时延、Token、终止原因） |
| `turn_trace` | 每轮对话明细（提问、回复、TTFT、时延、Token、原始收/发报文） |
| `judge_result` | 裁判明细（模型、角色、分数、结构化理由、红线命中、Token） |

---

## 7. 引擎工作方式

1. **快照绑定**：调度开始前读取用例当前版本，写入 `ExecutionRecord.case_version`，执行期间不感知后续编辑。
2. **Prompt 解析**：按 `LATEST / PINNED` 策略解析模拟器与裁判模板版本，并叠加用例级覆盖。
3. **对话循环**（静态 / 动态多轮）：
   - 渲染模拟器 Prompt（目标、隐藏信息、轮次、历史）→ 调 LLM → 解析 `thought / status / query`。
   - 调被测系统（SSE 逐块累计首字时延）→ 记录 `turn_trace` → 规则校验。
   - 判定终止：目标达成、模拟器放弃、复读、轮次超限。
4. **混合裁决**：规则硬校验 + 多个 LLM 裁判并行；`VETO` 任一审判红即失败，`AVG` 取均值；最终以最后一轮结论为准。
5. **指标聚合**：通过率、平均 TTFT、P95 时延、Token、吞吐、预计剩余时间；进度事件经 Redis + WebSocket 推送。

---

## 8. API 概览

统一前缀 `/api/v1`，除登录外均需 `Authorization: Bearer <token>`。

| 分组 | 主要接口 |
| --- | --- |
| 认证 | `POST /auth/login`、`GET /auth/me` |
| 环境 | `GET/POST /environments`、`PUT/DELETE /environments/{id}`、`GET/PUT /environments/{id}/variables`、`POST /environments/{id}/health-check`、`GET /environments/{id}/usage` |
| 模型 | `GET/POST /models`、`PUT/DELETE /models/{id}`、`POST /models/{id}/test` |
| Prompt | `GET/POST /prompts`、`GET/PUT/DELETE /prompts/{id}`、`POST /prompts/{id}/versions`、`GET /prompts/{id}/versions/{version}`、`GET /prompts/{id}/usage`、`POST /prompts/{id}/sandbox` |
| 用例 | `GET/POST /cases`、`GET/PUT/DELETE /cases/{id}`、`POST /cases/{id}/debug`、`GET /cases/{id}/revisions`、`GET /cases/{id}/revisions/diff`、`POST /cases/{id}/rollback`、`GET /cases/{id}/trend`、`GET /cases/{id}/executions`、`GET /cases/compare/runs`、`POST /cases/import`、`GET /cases/export` |
| 任务 | `GET/POST /tasks`、`GET /tasks/{id}`、`POST /tasks/{id}/start`、`POST /tasks/{id}/pause|resume|abort|retry-failed`、`GET /tasks/{id}/metrics`、`GET /tasks/{id}/executions` |
| 执行 | `GET /executions`、`GET /executions/{id}`、`GET /executions/{id}/raw` |
| 实时 | `WS /ws/tasks/{task_id}?token=<jwt>` |

---

## 9. 测试与验证

### 9.1 后端单元/集成测试

```powershell
cd backend
python -m pytest -q          # 57 passed（依赖不可用时自动 skip）
python -m ruff check billing_qa scripts tests
```

### 9.2 API 冒烟（需 API 已启动）

```powershell
cd backend
python scripts/api_smoke.py  # 43 项全部通过
```

覆盖：登录、环境变量掩码与探活、模型连通性、模板种子/版本/引用影响、模板发布与版本历史即时刷新、用例 CRUD、版本 Diff 与回滚、单条调试、沙盒、任务全生命周期（暂停/恢复/终止/断点重跑）、指标聚合、Trace 与原始报文、趋势聚合。

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
| 后端启动报数据库连接失败 | 确认 MySQL 已启动、`.env` 中账号密码与库名正确；库不存在时先执行 `python -m billing_qa.db.init_db --demo` |
| `/health` 中 `redis: down` | 确认 Redis 已启动；平台会自动降级为进程内状态，但多进程部署需恢复 Redis |
| 任务一直排队 | 检查任务并发/QPS 配置与 Mock 服务是否可达；查看后端日志与 `/tasks/{id}` 状态 |
| 前端 401 自动退出 | JWT 过期（默认 12 小时），重新登录即可 |
| 用例保存后版本未变 | 仅字段实际变更才会生成新版本；如需强制留痕可在变更说明中记录 |
| 导入失败 | 仅支持 `.json` / `.xlsx`，列名需与导出模板一致；错误行会在返回的 `errors` 中给出 |
