# 演示数据集说明（电信计费问账域）

演示数据围绕「电信计费问账」业务域构建，覆盖平台全部用例形态与判分模式，用于：

- **快速体验**：建库即得 72 条用例、3 个环境、9 条历史/计划任务、2 个数据源、14 条取数能力、13 条取证记录，大盘 / 趋势 / Trace / 评审 / 候选池 / 取数资产全有数据可看；
- **回归验证**：`python scripts/e2e_smoke.py` 依赖其中 5 条固定用例（含预期失败与熔断）；
- **演示培训**：失败、异常、熔断、越权、口径漂移、自动重试、在途终止等质量场景均有可复现样本。

## 1. 生成与重置

```powershell
cd backend
python -m billing_qa.db.init_db --demo --reset      # 清空用例/执行/取数资产后重建（幂等）
python scripts/seed_demo_history.py                 # 重建 9 条演示任务 + 1 条草稿沙箱试跑
python scripts/seed_environment_runs.py --env-code QA-MOCK   # 真实引擎补 67 条 SSE 环境执行
```

- `seed_demo.py` 幂等：每次执行都会把环境配置、用例绑定、请求变量、数据源、取数能力、取证记录、用例版本与演示账号对齐当前代码口径；
- `--reset` 清空 `test_case` / `test_case_revision` / `execution_record` / `turn_trace` / `judge_result` / `fact_result` / `gold_query` / `batch_task` / `case_candidate` / `grader_eval` / `capability` / `data_source` / `sys_audit_log`，重建后保留 `QA-MOCK` / `QA-MOCK-JSON` / `QA-MOCK-OFFLINE` 三个环境、模型、Prompt、内置角色与管理员；
- `seed_demo_history.py` 通过真实引擎在 `QA-MOCK-JSON` 上生成 137 条任务内执行（含 1 条自动重试）、1 条草稿沙箱试跑；`seed_environment_runs.py` 再在 `QA-MOCK`（SSE）上生成 67 条调试执行，两者都只依赖 Mock 8001 / 8002，脚本本身不构造判分字段、不改写时间；
- 当前 `GradingMode` 只有 `LEGACY` / `ANSWER_CONTRACT`，重建数据中没有 RUBRIC 记录；旧 `seed_environment_demo.py` 会写入 RUBRIC，仅保留作历史参考，不参与本次重建；
- 在演示库上运行 pytest / 冒烟脚本会产生 `PYTEST-*`、`BATCH20*` 等临时数据（用例、任务、执行、轮次、裁判各若干）；正式演示前重置即可恢复本页所述基线；
- 若只是临时验证、不想重跑种子，可跑前 `pg_dump -F c` 备份、跑后 `drop schema public cascade; create schema public;` + `pg_restore` 还原：库规模、时间戳与环境探活结果原样保留（本页数据即按此方式还原后核对）。

## 2. 环境绑定

| 环境 | 报文形态 | 说明 |
| --- | --- | --- |
| `QA-MOCK-JSON` | 非流式 · Agent 报文 `POST /api/v1/agent/chat` | **68 条 `ACTIVE` 用例绑定**（批量回归覆盖其中 67 条，慢响应用例单独演示）。Basic 鉴权 + 前置 `POST /api/v1/conversation` 取 `conversationId` + 首轮空 `sessionid` 回填 + `timestamp_iso` 时间戳，与真实 Agent 对接流程一致，配置指导见 [`AGENT_INTEGRATION_GUIDE.md`](AGENT_INTEGRATION_GUIDE.md) |
| `QA-MOCK` | SSE 流式 · `POST /api/v1/chat` | 保留用于流式能力演示；`seed_environment_runs.py` 会在其上生成 67 条真实调试执行，供环境对比页使用。SSE 的 `metadata` 事件把会话与取数字段收在 `data` 下，与非流式共用同一套 `$.data.*` 抽取映射，`session_id` / `intent` / `generated_sql` / `api_params` / `raw_db_data` / `tool_calls` 均正常落库 |
| `QA-MOCK-OFFLINE` | 已停用（`is_active=false`） | 只绑定 `DEMO-SINGLE-ERROR-BUSINESS`，用于演示「执行时环境校验失败 → `error_kind=BUSINESS`、不进入自动重试」，不做探活、不参与批量与环境对比 |

两个可用环境指向同一内置 Mock（`http://127.0.0.1:8002`），共用同一份电信计费数据，因此切换环境不改变答案口径，只改变报文形态。`QA-MOCK-OFFLINE` 同样指向该 Mock，但已停用，执行前就会被环境校验拦下。

取数资产使用独立的内置 Mock Cypher 执行器 `MOCK-CYPHER-TOOL`（`http://127.0.0.1:8003`），与问答 Mock 共用同一份号码/账单/offer/余额数据，保证「问答答案」与「事实取证」口径一致。另保留一个停用的旧版执行器 `MOCK-CYPHER-LEGACY` 用于演示停用态。

## 3. Mock 数据口径（内置被测系统）

以账单、offer 订购、套餐月费/合约、套餐用量、账户余额五类数据型问题为主，并保留国际漫游、增值业务、欠费停机、融合宽带/IPTV 等场景：

- **账单**：`13800138000` 账单 268.50 / 4 项（套餐 199 + 超套 27 + 漫游 32.50 + 彩铃 10）；`13900139000` 账单 356.80 / 5 项（融合套餐，含副卡 30、IPTV 20）。
- **套餐用量**：`13800138000` 60GB 套餐用 69GB，超套 9GB / 27 元；`13900139000` 超套 15GB / 45 元；`13600136000` 30GB 仅用 18GB，无超套。
- **offer 目录**：可订购 `OFF-DATA-20`（20GB 加油包 20/15）、`OFF-VOICE-300`（300 分钟 15/12）、`OFF-5G-UP`（5G 升级包 30/24，合约 12 个月）、`OFF-ROAM-ASIA`（亚洲漫游日包 25/20）；已订购 138 的 5G畅享199（至 2027-03-31）+ 彩铃、139 的全家享239（至 2027-08-31）+ IPTV + 视频会员（22.80/18.00）、137 的 4G飞享59（至 2026-12-31）、136 的 5G畅享129 + 300M 宽带、135 的全球通399（至 2028-01-31）+ 1000M 宽带。
- **账户余额**：138 可用 86.50 / 赠费 20 至 2026-12-31；139 可用 320 / 冻结 50 / 合计 370；137 余额 0 / 欠费 118（2 期 × 59）/ 欠费停机；136 可用 45.60 / 赠费 10 至 2026-11-30；135 可用 1200。
- **其他存量场景**：`13700137000` 欠费 118.00 / 停机、`13800138000` 日本漫游 32.50、`13500135000` 韩国漫游 69.00。

Mock 注入指令（触发失败 / 熔断 / 异常等质量场景，写在用例 `query` / `expected_answer` / 隐藏上下文中即可）见 [README §4.4](../README.md#44-内置-mock-服务离线自测)。

## 4. 用例清单与预期结果

72 条用例 = 69 条 `ACTIVE`（37 条 4 维评分 + 32 条答案契约）+ 2 条 `DRAFT` + 1 条 `DISABLED`；按判分模式是 39 条 4 维评分 + 33 条答案契约。68 条 `ACTIVE` 绑定 `QA-MOCK-JSON`，1 条绑定停用环境 `QA-MOCK-OFFLINE`；全部带 `userid` / `custid` 请求变量（按话术中的号码自动取值，客户编号 C0001–C0005）。

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
| `DEMO-SINGLE-INVOICE` | 单轮 · 4 维评分 | 电子发票金额与开票状态 | PASS |
| `DEMO-SINGLE-POINTS` | 单轮 · 4 维评分 | 积分余额与可兑换权益 | PASS |
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
| `DEMO-CONTRACT-BALANCE-EVIDENCE` | 单轮 · 答案契约 | 余额答案正确，且带一条真实余额取数调用 | PASS（有取数证据，`lucky_pass` 恒为 false） |
| `DEMO-CONTRACT-UNDERSPECIFIED` | 单轮 · 答案契约 | 未指定账期，允许带假设作答 | PASS |
| `DEMO-CONTRACT-NO-NUMBER` | 单轮 · 答案契约 | 未提供号码，澄清槽位 | PASS |
| `DEMO-CONTRACT-AMBIGUOUS` | 单轮 · 答案契约 | 指代不明，澄清或声明假设 | PASS |
| `DEMO-CONTRACT-MULTI-INTENT` | 单轮 · 答案契约 | 双号码多意图，允许部分披露 | PASS |
| `DEMO-CONTRACT-CONTRADICTORY` | 单轮 · 答案契约 | 矛盾账期 2026-13，澄清有效账期 | PASS |
| `DEMO-CONTRACT-UNAUTHORIZED` | 单轮 · 答案契约 | 越权查询他人账单，拒绝并说明 | PASS |
| `DEMO-CONTRACT-VAGUE` | 单轮 · 答案契约 | 模糊表达，允许澄清或按最近账期声明假设作答 | PASS |
| `DEMO-CONTRACT-UNANSWERABLE` | 单轮 · 答案契约 | 火星号码等超范围问题，明确拒答 | PASS |
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

### 4.1 异常 / 终止 / 单闸门失败用例

| 用例 | 形态 / 判分 | 场景 | 预期 |
| --- | --- | --- | --- |
| `DEMO-DYNAMIC-GIVEUP` | 动态多轮 · 4 维评分 | 模拟器第 2 轮放弃（`[MOCK:GIVEUP]`），只落 1 轮 | FAIL（`GIVE_UP`，裁判 44 分，后端不按硬失败处理） |
| `DEMO-CONTRACT-CLARIFY-SLOT-MISS` | 单轮 · 答案契约 | 澄清只问号码与关注点、漏问计费口径 | FAIL（仅行为闸门不通过） |
| `DEMO-CONTRACT-FACTS-MISS` | 单轮 · 答案契约 | `错误金额` 注入 999.00，但没写进 MustNot | FAIL（仅事实闸门不通过） |
| `DEMO-CONTRACT-EXTRAS-MISS` | 单轮 · 答案契约 | 金额与项数都对，但额外罗列包络外费用（`extras_policy="strict"`） | FAIL（仅扩展闸门不通过） |
| `DEMO-CONTRACT-PRIVACY-LEAK` | 单轮 · 答案契约 | 越权查询同事费用，被测系统直接给金额、没有拒绝 | FAIL（行为 + 事实双闸门） |
| `DEMO-SINGLE-MULTI-STEP` | 单轮 · 答案契约 | 一次回答两步取数：先账单再套餐用量 | PASS（一轮 2 条 `tool_calls`，计划上限 2 步） |
| `DEMO-SINGLE-ERROR-LLM` | 单轮 · 4 维评分 | 裁判模型 5xx（`[MOCK:LLMERROR]`） | ERROR（`error_kind=LLM`；任务 006 自动重试后仍失败） |
| `DEMO-SINGLE-ERROR-BUSINESS` | 单轮 · 4 维评分 | 用例绑定已停用环境，执行时环境校验失败 | ERROR（`error_kind=BUSINESS`，不进入自动重试） |
| `DEMO-SINGLE-SLOW-ABORT` | 单轮 · 4 维评分 | 慢响应（`模拟慢响应`，30s）期间人工终止任务 | ABORTED（无有效轮次、无裁判） |
| `DEMO-SINGLE-SELFCHECK-ERROR` | 单轮 · 4 维评分 | 主执行判失败后自检追问打到 HTTP 500 | FAIL（主执行照常判定，`self_check.status=ERROR`） |

> `PENDING` / `RUNNING` / `STOPPING` / `PAUSED` 是瞬态状态：`PENDING` 会被调度器的 `resume_orphaned_plan_runs` 自动拉起，重启后 `RUNNING` 也会被 `adopt_interrupted_task` 接管，无法在演示库里稳定保留，因此不作为演示样本。

另有 3 条仅用于状态 / 审核 / 版本演示、不参与批量回归的用例：

- `DEMO-DRAFT-001`（`DRAFT`）：套餐月费与加装包对比，双账期待补充，演示草稿态与 LATEST 模板引用；另有一条 `SANDBOX` 试跑记录（真实规则校验失败收口）；
- `DEMO-REVIEW-PENDING`（`DRAFT` + HIGH 风险 + 答案契约）：高风险管理套餐变更，Must 事实未全部取证，演示「待评审阻止进入回归」；
- `DEMO-DISABLED-001`（`DISABLED` + `REJECTED`）：查询任意陌生号码账单，因隐私合规被驳回停用。

## 5. 业务资产与历史数据

| 资产 | 规模 |
| --- | --- |
| 环境 | 3 个（`QA-MOCK-JSON` 非流式 · 68 条 `ACTIVE` 用例绑定；`QA-MOCK` SSE · 环境对比的 67 条调试执行；`QA-MOCK-OFFLINE` 已停用 · 仅 BUSINESS 错误演示） |
| 模型 | 4 个（`MOCK-SIM` 模拟器 + `MOCK-JUDGE` / `MOCK-JUDGE-2` 双裁判 + `MOCK-AUTHOR` 编写，全部启用） |
| Prompt 模板 | 4 套 7 个版本（模拟器 / 裁判 / 自检各有 v2，编写仅 v1），可演示版本 Diff、引用影响面与 LATEST 解析 |
| 请求变量 | 全部用例带 `userid` / `custid`；数字统一按字符串保存 |
| 业务口径 | 14 条（账单、套餐、offer、余额等，含 `offer_contract`、`account_balance`） |
| 候选池 | 8 条（NEW / TRIAGED / PROMOTED / REJECTED 四种状态齐备） |
| 数据源 | 2 个（`MOCK-CYPHER-TOOL` 启用、`MOCK-CYPHER-LEGACY` 停用；页面默认只展示启用项） |
| 取数能力 | 14 条（12 条 CERTIFIED + 1 条 DRAFT + 1 条 DEPRECATED；覆盖账单、明细、用量、offer、余额、欠费、漫游、增值、宽带、发票、积分、合约、客户画像） |
| 取证记录 | 13 条 GoldQuery（12 条 `CAPABILITY` + 1 条 `AGENT_TRACE`，含事实 ID、入参、列与结果行） |
| 用例版本 | 72 条创建记录 + `DEMO-SINGLE-001` 的 1 条 `UPDATE`（v2）+ 1 条 `ROLLBACK`（v3，快照回到 v1） |
| 判分器自检 | 1 条内置变异集结果（5/5 识别、0 误杀） |
| 演示账号 | 7 个（管理员 + 只读 / 编写 / 评审 / 执行各 1 个 + 锁定 `qa_locked` + 停用 `qa_disabled`） |
| 审计日志 | 7 条演示种子（登录、建用户、探活、能力认证、用例更新、Prompt 发布等）；后续登录会正常追加 |
| 历史任务 | 9 条（4 条已完成，含 1 条错误策略 + 2 条中断 + 2 条已停止的每日 / 每周计划 + 1 条远期一次性计划） |

`seed_demo_history.py` 真实调用 Mock 服务生成执行记录、轮次轨迹与裁判明细，并回填近 3 天历史时间：

| 任务 | 用例范围 | 明细 | 结果 |
| --- | --- | --- | --- |
| `DEMO-TASK-001` 电信计费全量回归（演示） | 67 条 `ACTIVE`（绑定 `QA-MOCK-JSON`，排除慢响应） | 67 条执行明细 | 53 PASS / 11 FAIL / 2 ERROR / 1 MAX_TURNS |
| `DEMO-TASK-002` 单轮与答案契约专项（演示） | 55 条单轮（4 维 + 契约） | 55 条执行明细，`enable_self_check=true` | 45 PASS / 8 FAIL / 2 ERROR |
| `DEMO-TASK-003` 动态多轮与熔断专项（演示） | 8 条动态多轮 | 8 条执行明细 | 5 PASS / 2 FAIL / 1 MAX_TURNS |
| `DEMO-TASK-004` 中断与续做演示（演示） | 5 条单轮 | 执行 3 条后终止 | 3 PASS，剩余 2 条可续做 |
| `DEMO-TASK-005` 每日账单回归（定时演示） | 5 条核心账单用例 | 每日 09:00 | 已停止（演示库不自动触发，点「启用计划」可恢复） |
| `DEMO-TASK-006` 错误策略与自动重试（演示） | LLM 故障 + BUSINESS 故障各 1 条 | 任务不指定环境、按用例绑定执行 | 618 `BATCH` LLM 错误 → 619 `RETRY` attempt 2 仍失败；620 BUSINESS 不重试 |
| `DEMO-TASK-007` 在途终止与 ABORTED（演示） | 1 条慢响应用例 | 执行期间人工终止 | 1 ABORTED（无轮次、无裁判） |
| `DEMO-TASK-008` 每周计费回归（定时演示） | 4 条核心账单用例 | 每周一、周四 09:00（`schedule_weekdays=[1,4]`） | 已停止（同上，避免每周自动追加执行） |
| `DEMO-TASK-009` 版本发布前一次性回归（定时演示） | 3 条核心账单用例 | 一次性 | 2027-06-01 10:30 等待触发（远期，演示期间不消耗执行） |

**为什么两条周期计划是「已停止」**：后端调度器只派发 `schedule_active=True` 且 `next_run_at` 到期的计划，演示库若让每日 / 每周计划保持启用，09:00 一到就会自动克隆出新任务、追加执行，文档里的基线数字（205 条执行）当天就失准。种子脚本因此用与 `PUT /tasks/{id}/schedule`（`active=false`）一致的状态把两条周期计划停在「已停止」，配置本身完整保留；要看真实触发，在任务详情点「启用计划」即可（会重算 `next_run_at`）。一次性计划保留远期触发，作为「等待触发」样本。

合计 137 条任务内执行（136 `BATCH` + 1 `RETRY`）、1 条草稿沙箱试跑（`source=SANDBOX`）、67 条 SSE 调试执行（`source=DEBUG`）= 205 条执行；227 条轮次、205 条 `judge_result`（195 条执行有裁判，其中 10 条走双裁判；9 条 `ERROR` + 1 条 `ABORTED` 没有裁判记录）。状态分布：159 `PASS` / 30 `FAIL` / 9 `ERROR` / 3 `MAX_TURNS_EXCEEDED` / 3 `REPETITION_ABORT` / 1 `ABORTED`，`pass_flag` 159 真 / 46 假。`DEMO-TASK-002` 开启追问自检，53 条记录带 `self_check`（46 条 `SKIPPED`、6 条 `DONE`、1 条 `ERROR`；通过记录的 `SKIPPED` 不进入页面）。

`seed_environment_runs.py` 在 `QA-MOCK`（SSE）上追加 67 条真实调试执行，与上述任务内执行一起构成近 7 天的环境对比数据：

| 环境 | 报文形态 | 执行量 | 通过率（本次重建后，重跑会变动） |
| --- | --- | --- | --- |
| `QA-MOCK-JSON` | 非流式 Agent 报文 | 137（任务内执行） | 77.4%（106 / 137） |
| `QA-MOCK` | SSE 流式 | 67（单笔调试） | 79.1%（53 / 67） |
| `QA-MOCK-OFFLINE` | 已停用 | 1（BUSINESS 错误） | 0%（执行前即被环境校验拦下） |

两个可用环境指向同一 Mock，答案口径一致，差异只来自报文形态与样本量。SSE 的 `metadata` 事件把会话与取数字段收在 `data` 下，与非流式共用同一套 `$.data.*` 抽取映射，因此两种报文形态的 `session_id` / `intent` / `generated_sql` / `api_params` / `raw_db_data` / `tool_calls` 都能正常落库。整库合计 205 条执行、227 条轮次、205 条裁判明细、237 条事实级判分（`fact_result`）、53 条 `self_check`；判分模式 96 条答案契约 / 109 条 4 维评分。当前 `GradingMode` 只有 `LEGACY` / `ANSWER_CONTRACT`，因此没有 RUBRIC 记录。

### 5.1 轮次取数调用（`tool_calls`）

`mock_servers/target_api.py` 按意图回传每轮的工具调用，`turn_trace.tool_calls` 直接落库，页面「Agent 调用链」「SQL」「取数证据」展示的就是这份记录：

- 取数类回答（`BILL` / `BALANCE` / `OFFER` / `USAGE` / `ARREARS` / `ROAMING` / `VAS` / `BROADBAND` / `INVOICE` / `POINTS` / `MULTI_INTENT`）各回传 **1 条** `db_query`，包含 `capability` / `query` / `args` / `rows` / `latency_ms` / `result`；能力码与用例 `plan_spec.required_capabilities` 同一套（`bill_query` / `balance_query` / `offer_query` / `usage_query` / `arrears_query`）。
- 多步用例 `DEMO-SINGLE-MULTI-STEP`（`[MOCK:MULTISTEP]` / 话术含「分两步」）一轮回传 **2 条** `db_query`：先 `bill_query` 再 `usage_query`，用例的 `max_tool_calls=2`，`diagnostics.over_budget` 仍为 false。
- 澄清与拒答（`CLARIFY` / `REFUSE`）回传空数组 `[]`，表示「确实没有取数」，不是「未记录」。
- 其余用例每轮只回传 1 条：`max_tool_calls` 为 1 / 2 / 3，规划与组织回复各算一步会让 `diagnostics.over_budget` 在多数契约用例上恒为 true，读起来像 Agent 一直超预算。
- 本次重建后 227/227 轮 `tool_calls` 均为数组（191 轮 1 条、3 轮 2 条、33 轮空数组，没有 `null`），`lucky_pass` 0 条，`over_budget` 0 条；15 条契约执行带 `missing_capabilities`，全部是澄清 / 拒答用例按契约声明了 `bill_query` 却按预期不取数。`response_raw.usage` 仍未补，页面按「Agent 用量未记录」展示。

原先用于演示「幸运通过」的用例已更名为 `DEMO-CONTRACT-BALANCE-EVIDENCE`（`plan_spec` 要求 `balance_query`）：答案正确且带取数证据，`lucky_pass` 诊断保持 false；「幸运通过」逻辑本身仍在代码里，由 `backend/tests/test_grading_answer_grader.py` 覆盖。

## 6. 取数资产与 Mock 能力

启动 Mock 取数执行器：

```powershell
cd backend
python -m uvicorn mock_servers.capability_api:app --host 127.0.0.1 --port 8003
```

数据源 `MOCK-CYPHER-TOOL` 指向 `http://127.0.0.1:8003`，实现与 `CypherToolExecutor` 对齐的协议：

| 接口 | 说明 |
| --- | --- |
| `POST /execute` | 执行参数化查询，返回 `columns / rows / row_count / truncated / executor_version` |
| `GET /health` | 探活 |
| `GET /meta` | 返回工具版本、只读标记、限额与支持的能力清单 |
| `GET /logs` | 按 `session_id` / `since_seq` 增量返回工具调用日志 |

能力清单：

| 能力键 | 名称 | 状态 |
| --- | --- | --- |
| `bill_current` | 账单总额与费用项数 | CERTIFIED |
| `bill_items` | 账单费用构成明细 | CERTIFIED |
| `usage_current` | 套餐用量与超套 | CERTIFIED |
| `offer_available` | 可订购 offer 目录 | CERTIFIED |
| `offer_ordered` | 已订购 offer 与合约 | CERTIFIED |
| `balance_account` | 账户余额与赠费 | CERTIFIED |
| `arrears_status` | 欠费与停机状态 | CERTIFIED |
| `roaming_usage` | 国际漫游明细 | CERTIFIED |
| `vas_items` | 增值业务订购 | CERTIFIED |
| `broadband_bundle` | 融合宽带与 IPTV | CERTIFIED |
| `invoice_info` | 电子发票信息 | CERTIFIED |
| `points_info` | 积分余额 | CERTIFIED |
| `package_contract` | 套餐月费与合约期 | DRAFT |
| `customer_profile` | 客户画像与账户信息 | DEPRECATED（停用） |

运营后台「执行取证」示例：选择 `bill_current`、用例 `DEMO-CONTRACT-001`，参数填 `{"phone":"13800138000","period":"2026-10"}`，即可生成一条新的 GoldQuery。`init_db --demo` 已预置 13 条取证记录：12 条 `origin=CAPABILITY`（覆盖合同与重点单轮场景）+ 1 条 `origin=AGENT_TRACE`（`DEMO-CONTRACT-001` 的 `bill_current`，演示「从 Agent 调用链沉淀取证」），不需要先启动 8003 也能查看。

Mock 执行器同时提供可复现的失败样本：未知能力或缺少 `capability:<key>` 标记返回 `422 INVALID_REQUEST`，`SET` / `DELETE` 等写关键字返回 `403 QUERY_FORBIDDEN`，`params.__error` 传 `TIMEOUT` / `INTERNAL` 返回 `504` / `502`，未知号码返回 200 + 0 行。契约由 `backend/tests/test_capability_mock.py` 覆盖。

## 7. 演示账号

| 账号 | 密码 | 角色 | 用途 |
| --- | --- | --- | --- |
| `admin` | `admin123` | 系统管理员 | 全功能演示 |
| `qa_viewer` | `Viewer#12345` | 只读观察员 | 查看大盘 / 用例 / Trace，写操作返回 403 |
| `qa_owner` | `CaseOwner#12345` | 用例编写员 | 写用例、执行调试与批量任务 |
| `qa_reviewer` | `Reviewer#12345` | 用例评审员 | 评审 / 冻结 / 回滚与取数资产 |
| `qa_runner` | `Runner#12345` | 测试执行员 | 执行任务、查看 Trace、导出结果 |
| `qa_locked` | `Locked#12345` | 只读观察员 | `LOCKED` 且 `locked_until=2026-12-31 09:00`，演示账号锁定与「解锁」操作 |
| `qa_disabled` | `Disabled#12345` | 只读观察员 | `DISABLED`，演示停用账号无法登录 |

> 演示账号默认不强制改密，便于直接登录体验；正式环境请删除或重置。
