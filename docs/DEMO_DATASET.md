# 演示数据集说明（电信计费问账域）

演示数据围绕「电信计费问账」业务域构建，覆盖平台全部用例形态与判分模式，用于：

- **快速体验**：建库即得 61 条用例、2 个环境、5 条历史/计划任务、2 个数据源、14 条取数能力、12 条取证记录，大盘 / 趋势 / Trace / 评审 / 候选池 / 取数资产全有数据可看；
- **回归验证**：`python scripts/e2e_smoke.py` 依赖其中 5 条固定用例（含预期失败与熔断）；
- **演示培训**：失败、异常、熔断、越权、口径漂移等质量场景均有可复现样本。

## 1. 生成与重置

```powershell
cd backend
python -m billing_qa.db.init_db --demo --reset   # 清空用例/执行/取数资产后重建（幂等）
python scripts/seed_demo_history.py              # 重建 5 条演示任务（3 完成 + 1 中断 + 1 定时）
```

- `seed_demo.py` 幂等：每次执行都会把环境配置、用例绑定、请求变量、数据源、取数能力、取证记录与演示账号对齐当前代码口径；
- `--reset` 清空 `test_case` / `test_case_revision` / `execution_record` / `turn_trace` / `judge_result` / `fact_result` / `gold_query` / `batch_task` / `case_candidate` / `grader_eval` / `capability` / `data_source` / `sys_audit_log`，保留环境、模型、Prompt、内置角色与管理员；
- 在演示库上运行 pytest / 冒烟脚本会产生 `PYTEST-*`、`BATCH20*` 等临时数据，正式演示前重置即可恢复本页所述基线。

## 2. 环境绑定

| 环境 | 报文形态 | 说明 |
| --- | --- | --- |
| `QA-MOCK-JSON` | 非流式 · Agent 报文 `POST /api/v1/agent/chat` | **61 条演示用例默认绑定**。Basic 鉴权 + 前置 `POST /api/v1/conversation` 取 `conversationId` + 首轮空 `sessionid` 回填 + `timestamp_iso` 时间戳，与真实 Agent 对接流程一致，配置指导见 [`AGENT_INTEGRATION_GUIDE.md`](AGENT_INTEGRATION_GUIDE.md) |
| `QA-MOCK` | SSE 流式 · `POST /api/v1/chat` | 保留用于流式能力演示，当前无绑定用例 |

两个环境指向同一内置 Mock（`http://127.0.0.1:8002`），共用同一份电信计费数据，因此切换环境不改变答案口径，只改变报文形态。

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

61 条用例 = 59 条 `ACTIVE`（32 条 4 维评分 + 27 条答案契约）+ 1 条 `DRAFT` + 1 条 `DISABLED`；全部绑定 `QA-MOCK-JSON`，全部带 `userid` / `custid` 请求变量（按话术中的号码自动取值，客户编号 C0001–C0005）。

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
| `DEMO-CONTRACT-LUCKY-PASS` | 单轮 · 答案契约 | 余额答案正确但无取数证据 | PASS（`lucky_pass` 仅诊断） |
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

另有两条仅用于状态 / 审核 / 版本演示、不参与批量回归的用例：

- `DEMO-DRAFT-001`（`DRAFT`）：套餐月费与加装包对比，双账期待补充，演示草稿态与 LATEST 模板引用；
- `DEMO-DISABLED-001`（`DISABLED` + `REJECTED`）：查询任意陌生号码账单，因隐私合规被驳回停用。

## 5. 业务资产与历史数据

| 资产 | 规模 |
| --- | --- |
| 环境 | 2 个（`QA-MOCK-JSON` 默认用于 61 条用例，`QA-MOCK` 流式保留） |
| 模型 | 6 个（`MOCK-SIM` 模拟器 + 双裁判 `MOCK-JUDGE` / `MOCK-JUDGE-2` + `MOCK-AUTHOR` 编写 + 2 个停用的 OpenCode 模型示例） |
| Prompt 模板 | 4 套（模拟器 / 裁判 / 编写 / 自检），其中模拟器、裁判、自检各有 v2 版本，可演示版本 Diff、引用影响面与 LATEST 解析 |
| 请求变量 | 全部用例带 `userid` / `custid`；数字统一按字符串保存 |
| 业务口径 | 14 条（账单、套餐、offer、余额等，含 `offer_contract`、`account_balance`） |
| 候选池 | 8 条（NEW / TRIAGED / PROMOTED / REJECTED 四种状态齐备） |
| 数据源 | 2 个（`MOCK-CYPHER-TOOL` 启用、`MOCK-CYPHER-LEGACY` 停用；页面默认只展示启用项） |
| 取数能力 | 14 条（12 条 CERTIFIED + 1 条 DRAFT + 1 条 DEPRECATED；覆盖账单、明细、用量、offer、余额、欠费、漫游、增值、宽带、发票、积分、合约、客户画像） |
| 取证记录 | 12 条 GoldQuery（覆盖主要合同/单轮场景，含事实 ID、入参、列与结果行） |
| 判分器自检 | 1 条内置变异集结果（5/5 识别、0 误杀） |
| 演示账号 | 5 个（管理员 + 只读 / 编写 / 评审 / 执行各 1 个） |
| 审计日志 | 7 条演示记录（登录、建用户、探活、能力认证、用例更新、Prompt 发布），无测试脚本残留 |
| 历史任务 | 5 条（3 条已完成 + 1 条中断可续做 + 1 条每日定时） |

`seed_demo_history.py` 真实调用 Mock 服务生成执行记录、轮次轨迹与裁判明细，并回填近 3 天历史时间：

| 任务 | 用例范围 | 明细 |
| --- | --- | --- |
| `DEMO-TASK-001` 电信计费全量回归（演示） | 59 条 `ACTIVE` 全量 | 59 条执行明细 |
| `DEMO-TASK-002` 单轮与答案契约专项（演示） | 48 条单轮（4 维 + 契约） | 48 条执行明细，失败项触发追问自检 |
| `DEMO-TASK-003` 动态多轮与熔断专项（演示） | 7 条动态多轮 | 7 条执行明细 |
| `DEMO-TASK-004` 中断与续做演示（演示） | 5 条单轮 | 执行 3 条后终止，剩余 2 条可续做 |
| `DEMO-TASK-005` 每日账单回归（定时演示） | 5 条核心账单用例 | 每日 09:00 触发，初始不执行 |

合计 117 条执行明细与 142 条事实级判分结果（`fact_result`）。

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

运营后台「执行取证」示例：选择 `bill_current`、用例 `DEMO-CONTRACT-001`，参数填 `{"phone":"13800138000","period":"2026-10"}`，即可生成一条新的 GoldQuery。`init_db --demo` 已预置 12 条取证记录，覆盖合同与重点单轮场景，不需要先启动 8003 也能查看。

## 7. 演示账号

| 账号 | 密码 | 角色 | 用途 |
| --- | --- | --- | --- |
| `admin` | `admin123` | 系统管理员 | 全功能演示 |
| `qa_viewer` | `Viewer#12345` | 只读观察员 | 查看大盘 / 用例 / Trace，写操作返回 403 |
| `qa_owner` | `CaseOwner#12345` | 用例编写员 | 写用例、执行调试与批量任务 |
| `qa_reviewer` | `Reviewer#12345` | 用例评审员 | 评审 / 冻结 / 回滚与取数资产 |
| `qa_runner` | `Runner#12345` | 测试执行员 | 执行任务、查看 Trace、导出结果 |

> 演示账号默认不强制改密，便于直接登录体验；正式环境请删除或重置。
