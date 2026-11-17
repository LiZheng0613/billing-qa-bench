# 演示数据集说明（电信计费问账域）

演示数据围绕「电信计费问账」业务域构建，覆盖平台全部用例形态与判分模式，用于：

- **快速体验**：建库即得 57 条用例、2 个环境、3 条历史任务，大盘 / 趋势 / Trace / 评审 / 候选池全有数据可看；
- **回归验证**：`python scripts/e2e_smoke.py` 依赖其中 5 条固定用例（含预期失败与熔断）；
- **演示培训**：失败、异常、熔断、越权、口径漂移等质量场景均有可复现样本。

## 1. 生成与重置

```powershell
cd backend
python -m billing_qa.db.init_db --demo --reset   # 清空用例/执行数据后重建（幂等）
python scripts/seed_demo_history.py              # 重建 3 条演示批量任务（幂等）
```

- `seed_demo.py` 幂等：每次执行都会把环境配置、用例绑定与请求变量对齐当前代码口径；
- `--reset` 清空 `test_case` / `test_case_revision` / `execution_record` / `turn_trace` / `judge_result` / `fact_result` / `gold_query` / `batch_task` / `case_candidate`，保留环境、模型、Prompt 等基础资产；
- 在演示库上运行 pytest / 冒烟脚本会产生 `PYTEST-*`、`BATCH20*` 等临时数据，正式演示前重置即可恢复本页所述基线。

## 2. 环境绑定

| 环境 | 报文形态 | 说明 |
| --- | --- | --- |
| `QA-MOCK-JSON` | 非流式 · Agent 报文 `POST /api/v1/agent/chat` | **57 条演示用例默认绑定**。Basic 鉴权 + 前置 `POST /api/v1/conversation` 取 `conversationId` + 首轮空 `sessionid` 回填 + `timestamp_iso` 时间戳，与真实 Agent 对接流程一致，配置指导见 [`AGENT_INTEGRATION_GUIDE.md`](AGENT_INTEGRATION_GUIDE.md) |
| `QA-MOCK` | SSE 流式 · `POST /api/v1/chat` | 保留用于流式能力演示，当前无绑定用例 |

两个环境指向同一内置 Mock（`http://127.0.0.1:8002`），共用同一份电信计费数据，因此切换环境不改变答案口径，只改变报文形态。

## 3. Mock 数据口径（内置被测系统）

以账单、offer 订购、套餐月费/合约、套餐用量、账户余额五类数据型问题为主，并保留国际漫游、增值业务、欠费停机、融合宽带/IPTV 等场景：

- **账单**：`13800138000` 账单 268.50 / 4 项（套餐 199 + 超套 27 + 漫游 32.50 + 彩铃 10）；`13900139000` 账单 356.80 / 5 项（融合套餐，含副卡 30、IPTV 20）。
- **套餐用量**：`13800138000` 60GB 套餐用 69GB，超套 9GB / 27 元；`13900139000` 超套 15GB / 45 元；`13600136000` 30GB 仅用 18GB，无超套。
- **offer 目录**：可订购 `OFF-DATA-20`（20GB 加油包 20/15）、`OFF-VOICE-300`（300 分钟 15/12）、`OFF-5G-UP`（5G 升级包 30/24，合约 12 个月）、`OFF-ROAM-ASIA`（亚洲漫游日包 25/20）；已订购 138 的 5G畅享199（至 2027-03-31）+ 彩铃、139 的全家享239（至 2027-08-31）+ IPTV + 视频会员（22.80/18.00）、137 的 4G飞享59（至 2026-12-31）、136 的 5G畅享129 + 300M 宽带、135 的全球通399（至 2028-01-31）+ 1000M 宽带。
- **账户余额**：138 可用 86.50 / 赠费 20 至 2026-12-31；139 可用 320 / 冻结 50 / 合计 370；137 余额 0 / 欠费 118（2 期 × 59）/ 欠费停机；136 可用 45.60 / 赠费 10 至 2026-11-30；135 可用 1200。
- **其他存量场景**：`13700137000` 欠费 118.00 / 停机、`13800138000` 日本漫游 32.50、`13500135000` 韩国漫游 69.00。

Mock 注入指令（触发失败 / 熔断 / 异常等质量场景，写在用例 `query` / `expected_answer` / 隐藏上下文中即可）见 [README §4.4](../README.md#44-内置-mock-服务离线自测)。

## 4. 用例清单与预期结果

57 条用例 = 55 条 `ACTIVE`（30 条 4 维评分 + 25 条答案契约）+ 1 条 `DRAFT` + 1 条 `DISABLED`；全部绑定 `QA-MOCK-JSON`，全部带 `userid` / `custid` 请求变量（按话术中的号码自动取值，客户编号 C0001–C0005）。

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

另有两条仅用于状态 / 审核 / 版本演示、不参与批量回归的用例：

- `DEMO-DRAFT-001`（`DRAFT`）：套餐月费与加装包对比，双账期待补充，演示草稿态与 LATEST 模板引用；
- `DEMO-DISABLED-001`（`DISABLED` + `REJECTED`）：查询任意陌生号码账单，因隐私合规被驳回停用。

## 5. 业务资产与历史数据

| 资产 | 规模 |
| --- | --- |
| 模型 | 9 个（`MOCK-SIM` 模拟器 + 双裁判 `MOCK-JUDGE` / `MOCK-JUDGE-2` 等，全部指向内置 Mock LLM） |
| Prompt 模板 | 3 套（`SIM_DEFAULT_BILLING_USER` 模拟器、`JUDGE_DEFAULT_ZERO_TOLERANCE` 裁判等，内置不可删） |
| 业务口径 | 14 条（账单、套餐、offer、余额等，含 `offer_contract`、`account_balance`） |
| 候选池 | 8 条（NEW / TRIAGED / PROMOTED / REJECTED 四种状态齐备） |
| 历史任务 | 3 条（见下表） |

`seed_demo_history.py` 真实调用 Mock 服务生成执行记录、轮次轨迹与裁判明细，并回填近 3 天历史时间：

| 任务 | 用例范围 | 明细 |
| --- | --- | --- |
| `DEMO-TASK-001` 电信计费全量回归（演示） | 55 条 `ACTIVE` 全量 | 55 条执行明细 |
| `DEMO-TASK-002` 单轮与答案契约专项（演示） | 44 条单轮（4 维 + 契约） | 44 条执行明细 |
| `DEMO-TASK-003` 动态多轮与熔断专项（演示） | 7 条动态多轮 | 7 条执行明细 |

合计 106 条执行明细与 136 条事实级判分结果（`fact_result`）。
