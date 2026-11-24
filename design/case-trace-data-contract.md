# 用例执行详情 · 数据契约（对话轮次）

> 目标：把原型 `design/case-turn-flow-final.html` 与 `CaseTraceInspector.vue` 上每个展示项，逐一绑定到真实数据来源；明确「人工填写 / 规则计算 / LLM 产出 / 后端落库」四种生产者，以及缺数据时的降级表现。
>
> 结论先行：**界面框架已经完整，真实数据的缺口集中在「轮次级判分」与「原始报文元信息」两块**。不补这两块，多轮页面只能诚实显示「未判定 / 执行级口径」，无法出现原型里的逐轮分数、逐轮事实和响应头。

---

## 0. 一页结论

| 界面块 | 现在能否用真实数据还原 | 缺口 | 缺口性质 |
| --- | --- | --- | --- |
| 页头（用例 / 执行号 / 时间 / 模型 / 环境 / 任务） | ✅ 可以 | 被测模型名只在请求体里，取不到退裁判模型 | 数据在，字段位置不固定 |
| 页头 KPI（总分 / 轮次 / 规则 / 契约 / 归因） | ✅ 可以 | 契约模式「排序分」用质量分，口径已在界面注明 | 口径问题，不是缺数据 |
| 对话列表 / 气泡 | ✅ 可以 | — | — |
| 对照模式 | ⚠️ 部分 | 多轮缺 `turns[].expected_answer`；逐轮事实缺 | **人工数据 + 后端** |
| 判分页汇总 / 热力表 | ⚠️ 部分 | 多轮无轮次分、四维、轮次归因 | **后端** |
| 判分页行内详情 | ⚠️ 部分 | 同上；reason 只有会话级 | **后端** |
| 抽屉「本轮」 | ✅ 可以 | `finish_reason` 未落库；温度等要看被测请求体 | 字段未抽取 |
| 抽屉「判定」 | ⚠️ 部分 | 契约事实只挂最后一轮；reason 会话级 | **后端** |
| 抽屉「执行」— 性能 / 参数 | ✅ 可以 | 温度 / top_p / max_tokens 取决于被测请求体 | 上游是否发送 |
| 抽屉「执行」— 调用链 | ⚠️ 依赖环境 | `tool_calls` 依赖响应结构与 extractor；真实用例常为 null | **接口契约** |
| 抽屉「证据」— 请求 | ✅ 可以 | 头已脱敏保存 | — |
| 抽屉「证据」— 响应 | ⚠️ 部分 | 无响应头、无状态码；流式只有聚合体 + 帧数 | **后端（易补）** |
| 抽屉「证据」— 追问 / 反问 | ⚠️ 派生 | 追问由前后 `query` 推导；反问靠 `intent=CLARIFY` | 无独立字段，已注明 |
| 抽屉「证据」— 流程裁决 | ⚠️ 部分 | 有 prompt / raw / 会话级 reason；无逐轮 reason | **后端** |
| 抽屉「派生与上下文」— 追问自检 | ⚠️ 部分 | 无 `turn_index`，多轮只能按执行级展示 | **后端（小改）** |
| pass 阈值文案 | ❌ 不一致 | 前端常量 80，后端默认 60，且阈值未随执行落库 | **必须修（P0）** |

---

## 1. 数据链路总览

```
用例编辑器 / AI 草稿
  ├─ case_snapshot.expected_answer           人工 / CASE_AUTHOR prompt
  ├─ case_snapshot.turns[].expected_answer   人工 / CASE_AUTHOR prompt（当前「可选」）
  ├─ case_snapshot.expected_rules            人工 / CASE_AUTHOR prompt
  ├─ case_snapshot.must/may/must_not         FactAtom（人工确认）
  └─ case_snapshot.pass_threshold            人工（可为空 → 后端默认 60）
                │
                ▼  执行时冻结
        ExecutionRecord.case_snapshot
                │
   ┌────────────┴─────────────┐
   ▼                          ▼
TurnTrace（每轮）          JudgeResult（执行级，1~N 个裁判）
 request_payload            total_score / dimension_scores
 response_raw               error_tags / reason / raw_response
 final_answer               judge_prompt
 rule_check（仅 LEGACY）
 tool_calls / generated_sql
                │
                ▼
ExecutionRecord（执行级）
 total_score / pass_flag / grading_mode
 rule_result / judge_result
 answer_grading / fact_results
 diagnostics / self_check
                │
                ▼  build_execution_detail()
        ExecutionDetail（API）
                │
                ▼  buildTraceModel()
         TurnVM × N（前端唯一数据源）
                │
    ┌───────────┼────────────┬─────────────┐
    ▼           ▼            ▼             ▼
 对话/对照    判分页      抽屉 5 段     页头 KPI
```

**关键事实**

1. 执行详情使用**执行时冻结的 `case_snapshot`**，之后改用例不会改变历史执行的对照结果。
2. 契约判分 `grade_answer()` 只判「最后一轮最终答案」，因此事实级结果（`fact_results`）天然只有执行级口径。
3. `TurnTrace` 表**没有** `judge_score / dimension_scores / error_tags / judge_reason / fact_results` 列；前端这些字段目前只有 Mock 提供。
4. `JudgeResult` 的 `reason / error_tags / dimension_scores` 是**会话级**，没有 `turn_index`。
5. 规则校验（`rule_check`）是真正的轮次级门槛，但只在 **LEGACY** 模式、且用例配了 `expected_rules` 时才产出。
6. `turn.evidence` 列存在，但 `executor._save_turn()` 从未写入；真实数据恒为 `null`。
7. 响应**状态码**在适配器里已拿到（`TargetResponse.status_code`），但 `_build_record()` 未落库；响应**头**根本没有写入 `response_payload`。
8. `build_expected_text()` 只拼 `snapshot.expected_answer` / `session_expected`，**不读取 `turns[].expected_answer`**。即使人工补齐逐轮期望，裁判也看不到。

---

## 2. 页头契约

| 展示项 | 前端字段 | 后端来源 | 生产者 | 现状 / 降级 |
| --- | --- | --- | --- | --- |
| 用例名 | `detail.case_name` | `test_case.name`（执行列表 join） | 人工 | 显示实时用例名，不是快照名 |
| 用例编码 | `detail.case_code` | `test_case.code` | 人工 / 生成 | 空则不显示链接 |
| 状态 pill | `detail.status` + `pass_flag` | `ExecutionRecord` | 执行器 | — |
| 草稿试跑标记 | `detail.source` | `ExecutionRecord.source` | 执行器 | 非 `DEBUG` 不显示 |
| 执行号 | `detail.id` | `ExecutionRecord.id` | 自增 | — |
| 时间 | `started_at` / `finished_at` | 同上 | 执行器 | 缺结束时间为 `—` |
| 总耗时 | `latency_ms` | 同上 | 执行器 | — |
| 模型 | `headerAgentModel` ← `request_payload.body.model/model_name/modelName`；回退 `judges[0].model_name` | 被测请求体 / `JudgeResult` | 上游接口 | 回退时明确标「裁判模型」 |
| 会话 | `turns[0].session_id` | `TurnTrace.session_id` | 上游响应 / 适配器生成 | 缺为 `—` |
| 环境 | `environment_name` | `Environment.name` | 人工 | 缺为 `—` |
| 所属任务 | `task_name` | `batch_task.name` | 人工 | 草稿试跑可为空 |
| 记录类型 | `source` | `ExecutionRecord.source` | 执行器 | — |
| 总分 KPI | `totalScoreOf()` | LEGACY：`ExecutionRecord.total_score`；契约：`judge_result.quality.total_score`，回落 `answer_grading.total_score` | 裁判 / 契约判分 | 契约模式标签为「质量分参考 / 排序分」 |
| 轮次 KPI | `turns.length` | `TurnTrace` | — | — |
| 规则 KPI | `rule_result.checks` | `ExecutionRecord.rule_result`（LEGACY） | `evaluate_rules()` | 契约模式为空 → 不展示 |
| 契约 KPI | `answer_grading.passed / gates` | `ExecutionRecord.answer_grading` | `grade_answer()` | 非契约模式为空 |
| 归因 KPI | `judges[].error_tags` | `JudgeResult.error_tags` | 裁判 LLM | 去重后展示，归属会话级 |

---

## 3. 对话 / 对照契约

### 3.1 对话气泡

| 展示项 | 来源 | 现状 |
| --- | --- | --- |
| 用户提问 | `turn.query` | ✅ |
| Agent 回复 | `turn.final_answer` | ✅ |
| 本轮工具 chips | `turn.tool_calls[].name/tool` | ⚠️ 依赖被测响应 |
| 判定 pill / 分数 | `TurnVM.verdict / score` | 多轮 `score` 为 `—` |
| 判定 / 详情入口 | 事件 | ✅ |
| 自检块（仅命中轮） | `detail.self_check` | 单轮可定位；多轮只按执行级 |

### 3.2 对照卡

| 展示项 | 前端计算 | 数据来源 | 缺失时 |
| --- | --- | --- | --- |
| 左列「实际回复」 | `turn.answer` | `TurnTrace.final_answer` | 显示空态 |
| 右列「期望回复」 | `expectedOf()` | `snapshot.turns[i].expected_answer`；单轮回落 `snapshot.expected_answer` | 多轮不回落，显示「本轮没有维护期望答案」 |
| 期望来源标签 | `expectedSource` | 同上 | `—` |
| fact 期望 / 实际 | `mapFacts()` | `detail.fact_results` 或 `answer_grading.facts` | 挂最后一轮，注明执行级 |
| rule 期望 / 实际 | `rulesOf()` | `turn.rule_check.checks`；单轮回落 `detail.rule_result.checks` | 无规则即空 |
| 数值差异 | `valueGaps()` | 前端数值解析，仅同组「期望/实际」都可比时 | 不臆造差异 |
| 结论 / 说明 | `verdict / judgeReason` | 硬门槛 + 裁判 | 多轮无轮次 reason 时不冒充本轮 |
| 缺期望统计 | `summary.noExpect` | 前端统计 | — |

> **对照的空期望是数据缺失，不是前端 bug。** 需要用例编辑器为每一轮维护 `expected_answer`（见 §7.1）。

---

## 4. 判分页契约（`JudgePanel.vue`）

| 展示项 | 来源 | 现状 / 降级 |
| --- | --- | --- |
| 规则通过 / 总数 | `rule_check.checks`（逐轮） | ✅ LEGACY；契约模式无 |
| 契约通过 / 总数 | `answer_grading.gates`（执行级） | ✅ 但无法拆轮；逐轮 `contract_passed` 仅 Mock |
| 通过 / 部分命中 / 未通过 / 未判定 | `pass_flag + 硬门槛 + 轮次分` | ✅ 四态已贯通 |
| 总分 | 同页头总分 | 多轮行内显示 `—` |
| 四维 | `judge_result.quality.dimension_scores` / `judge.dimension_scores` | 执行级，界面注明「不拆轮次」 |
| 错误归因（轮次/会话） | `turn.error_tags`（Mock） / `judge.error_tags` | 真实只有会话级 |
| reason / 行内详情 | `turn.judge_reason`（Mock） / `judge.reason` | 真实只有会话级 |
| facts 明细 | `turn.fact_results`（Mock） / `detail.fact_results` | 真实只有最后一轮 |
| 「定位到对话」 | 前端事件 | ✅ |
| 「复制结论」 | 前端拼接 | ✅ |

---

## 5. 抽屉 5 段契约

### 5.1 本轮（`turn`）

| 展示项 | 来源 | 现状 |
| --- | --- | --- |
| 本轮结果四格（规则 / 契约 / 分数 / 归因） | `TurnVM` 派生 | ✅，缺则 `—` |
| 运行标识（会话 / 用例 / 执行 / 轮次 / 请求 ID / trace / 模型 / 温度 / 来源 / 时间） | `TurnTrace`、`request_payload`、`judge` | 请求 ID 取 `x-request-id` 头；温度取请求体；缺为「未记录」 |
| 输入（问题 / 上文 / 变量） | `turn.query`、上一轮 `query`、`snapshot.request_variables` | ✅ |
| 输出（finish_reason / 意图 / 数据块 / 图标 / SQL） | `response_raw`、`turn.intent`、`raw_db_data` | `finish_reason` 未落库 |
| 回复正文 | `final_answer` | ✅ |
| 本轮取数 | `raw_db_data` | ⚠️ 上游是否返回 |
| 期望答案 | `snapshot.turns[i].expected_answer` | ⚠️ 常缺 |

### 5.2 判定（`judge`）

| 展示项 | 来源 | 现状 |
| --- | --- | --- |
| 规则校验 | `turn.rule_check.checks` | ✅ LEGACY |
| 契约事实 | `fact_results` / `answer_grading.facts` | 执行级，挂最后一轮 |
| 质量分 + 四维 | `judge_result.quality` / `judge.dimension_scores` | 执行级 |
| LLM 综合结论 | `turn.judge_reason`（轮次）；无则显式展示「会话级结论（不归属本轮）」 | ⚠️ 真实只有会话级 |
| 错误归因（轮次 / 会话） | `turn.error_tags`（Mock）/ `judges.error_tags` | 真实只有会话级 |
| 裁决返回校验 | 前端校验四维合计 = 综合分 | ✅ |
| 裁判 Prompt / 原始返回 | `JudgeResult.judge_prompt / raw_response` | ✅ |

### 5.3 执行（`exec`）

| 展示项 | 来源 | 现状 |
| --- | --- | --- |
| 首 Token / 总时延 / 步数 / 最慢一步 | `ttft_ms`、`latency_ms`、`tool_calls` | 步数依赖调用链 |
| tokens | `simulator_*_tokens`（**注意：是被测 Agent 的 token，不是裁判**） | ✅ |
| 模型与参数 | `request_payload.body`（model / temperature / max_tokens / stream / response_format） | 缺则「执行记录未保存」 |
| 环境 / 判分模式 / 会话 | `environment_name`、`grading_mode`、`session_id` | ✅ |
| 调用链（阶段分布 / 筛选 / 折叠） | `turn.tool_calls` | ⚠️ 需被测接口输出 |
| 定位（trace / 轮次 / 最慢工具 / 用例） | 同上 | ✅ |

### 5.4 证据（`evid`，7 子页签）

| 子页签 | 展示项 | 来源 | 现状 |
| --- | --- | --- | --- |
| 请求 | 输入原文、请求头、请求体、URL / method | `request_payload.{headers,body,url,method}` | ✅ 头已脱敏 |
| 响应 | 状态码、响应头、响应体、流式帧数 | `response_raw.{mode,body,frames,frame_count,status_code,headers}`、`responseHeaders` | ✅ 新执行已落库并展示；历史执行按空态降级 |
| 追问 | 上一轮 / 本轮 / 输入来源、自检 | 相邻 `query`、`input_source`、`self_check` | ✅ 派生，已注明 |
| 反问 | 是否反问、反问内容、用户回复 | `intent === 'CLARIFY'`、`final_answer`、`error_tags` | 无独立字段，已注明 |
| Agent 调用链 | 步骤、工具、耗时、args/result | `tool_calls` | ⚠️ 依赖接口 |
| SQL | 生成 SQL | `generated_sql` → `response_body.data.generated_sql` | ✅ 两处兜底 |
| 流程裁决 | 输入 / 调用 / 返回 / 校验、裁判 Prompt、原始返回 | `judges` | ✅ 会话级 |

### 5.5 派生与上下文（`context`）

| 展示项 | 来源 | 现状 |
| --- | --- | --- |
| 追问自检（状态 / 触发原因 / 结论 / prompt / raw） | `detail.self_check` | ⚠️ 无 `turn_index` |
| 会话级判定上下文（综合分 / 通过轮 / 最低轮 / 归因数 / 会话一致性） | 前端聚合 | ✅ |
| 执行快照（版本 / 尝试 / 环境 / 起止 / 模型 / 阈值） | `ExecutionDetail` | ✅，阈值口径待修 |
| 请求变量 | `case_snapshot.request_variables` | ✅ |

---

## 6. 判定语义（前端不得自行改写）

1. **pass 公式**：
   - LEGACY / RUBRIC：`硬门槛全过 且 综合分 ≥ 阈值`。
   - ANSWER_CONTRACT：只由 `answer_grading.gates` 四项全过决定（`answer_grader.py:130 passed = all(gates)`），`MAX_TURNS_EXCEEDED` / `REPETITION_ABORT` 由 `executor.py:591-595` 强制 false；质量分 `judge_result.quality` 仅作参考，不参与 pass，也不是「契约分」。
   - 前端界面文案必须按模式分支，不能对契约执行写「pass = 闸门 + 分数 ≥ 阈值」。
2. **四态**：
   - `pass`：后端 `pass_flag=true`（单轮）或门槛 + 分数都满足；
   - `warn`：达线但硬门槛未全过（部分命中）；契约模式下只是展示态，pass 仍以闸门为准；
   - `fail`：未达线或门槛未过；
   - `unknown`：多轮下既无轮次分、也无规则/契约门槛 → **不臆造通过**。
3. **分数来源**：轮次分优先 `turn.judge_score`；单轮回退执行级总分；多轮回退 `—`，绝不拿会话总分伪装轮次分。
4. **事实归属**：`fact_results` 永远只有执行级；只挂最后一轮并注明。`factsFromTurn` 只能由轮次级事实字段为真。
5. **期望归属**：单轮才允许会话级 `expected_answer` 回落；多轮只用 `turns[].expected_answer`。
6. **归因归属**：`turn.error_tags` 才算轮次级；`judge.error_tags` 是会话级，只作上下文。
7. **自检**：`self_check` 不改变 pass / 分数；单轮可归属唯一一轮，多轮只有后端提供 `turn_index` 才能归属。
8. **阈值**：必须来自执行记录，不能用前端常量。当前前端 `PASS_LINE = 80`、后端默认 `60`，是 P0 缺陷（见 §8.1）。
9. **过程偏向与最终判定（已确认口径）**：采用 4 维评分 LLM 裁决（LEGACY）时，**通过与否只认整通的最终目标结果**；对话过程中的轮次偏差（表达、中间数字、完整性等）允许存在，由裁判在整体维度上消化，不构成逐轮否决。
   - 代码依据：裁判一次看完整会话（`build_judge_variables` 拼整通 chat_history），产出**会话级** `pass / total_score`；`MAX_TURNS_EXCEEDED / REPETITION_ABORT` 直接否决。
   - 界面含义：多轮下中间轮显示「未判定」**不是缺陷，是业务正确**——过程轮本就不判 pass；轮次级只展示过程信息（规则、异常、性能），判定只认执行级。
   - 边界（保持现状）：该容忍只作用于 **LLM 评分维度**；规则硬门槛仍是全轮生效——`merge_rule_results` 汇总所有轮的 checks，任一轮失败则整通不通过（`rule_evaluator.py:332`）。若业务希望「中间轮规则失误不否决整通」，需另行确认，目前不改。

---

## 7. Prompt 改动评估

### 7.1 用例生成 Prompt：逐轮 `expected_answer`

**现状**

- `CASE_AUTHOR_DEFAULT_CONTENT`（`backend/billing_qa/db/seed.py:104`）的 Output JSON 里 `turns[].expected_answer` 没有出现；
- `services/author/service.py:504/530` 的追加强调「每轮 query 必填，expected_answer/notes **可选**」；
- `schemas/case_ai.py:49` 允许为 `null`；
- 归一化 `service.py:1188` 保留但允许为空；
- `schemas/case.py:297` 的 `TurnItem.expected_answer` 可选。

**改 Prompt 能解决什么**

- 让 AI 草稿在生成多轮用例时主动补「每轮预期答案」，提高覆盖率；
- 让用例编辑器在草稿回填时把该字段亮出来，提示人工确认。

**改 Prompt 不能解决什么**

- `build_expected_text()`（`services/engine/judge.py:61`）根本不把 `turns[].expected_answer` 喂给裁判，裁判仍无法按轮判分；
- LLM 可能编造期望值。不能用「必填」硬卡 Schema，否则生成失败率会上升。

**建议改法（Prompt + 校验，不改 Schema 必填）**

1. `CASE_AUTHOR_DEFAULT_CONTENT` 的 Hard Rules 增加：
   > 多轮用例必须为每一轮给出 `expected_answer`，写「该轮可核对的结论口径」（金额 + 口径 + 是否应澄清），不得只写「回答正确」；没有权威依据时留空并写入 `missing`。
2. Output JSON 的 `turns` 示例补 `expected_answer`。
3. `service.py` 的追加强调把「可选」改为「建议必填」，并在生成结果里对缺 `expected_answer` 的多轮草稿追加 `warnings`。
4. 编辑器（`TestCaseDrawer.vue`）对多轮缺期望显示黄色提示，不阻断保存。

> 这一项是**数据生产**改进，不能替代后端把字段送进裁判。

### 7.2 裁判 Prompt：轮次级输出

**现状**

- `JUDGE_DEFAULT_CONTENT`（`seed.py:41`）要求输出 `pass / total_score / dimension_scores / error_tags / reason`，全部是会话级；
- `coerce_judgement()` 只解析 `JUDGE_SCORE_KEYS` 这几个固定键；
- 裁判一次调用看完整 `chat_history`，天然具备按轮评价的能力，只是没有结构化输出。

**可选方案**

| 方案 | 改动 | 成本 | 风险 |
| --- | --- | --- | --- |
| A. Prompt 让裁判多输出 `turn_scores[] / turn_error_tags[]` | 改 `JUDGE_DEFAULT_CONTENT` + `coerce_judgement()` + 落库 | 1 次裁判调用 | 输出稳定性下降；需要 Schema 容错 |
| B. 每轮单独跑裁判 | 执行器 N 次调用 | N 倍 token / 延迟 | 轮次间口径可能不一致 |
| C. 不用 LLM，后端做逐轮确定性诊断 | `rule_check` 已存在；契约事实按轮跑 `grade_answer` 诊断 | 代码 + 每轮 claim 抽取 | 多轮语义边界要定义清楚 |

**推荐**：**A 用于「轮次评分 / 归因」，C 用于「事实 expected/actual」**。

- 事实级 expected/actual 必须由 `envelope.grade_envelope()` 代码判定，**不要让裁判编 expected/actual**；
- 裁判只负责它擅长的「这轮哪里不对」和分数；
- `turn_scores[]` 必须带 `turn_index`，且后端在 `coerce_judgement()` 中做范围 / 总和校验，异常时整段丢弃并降级回会话级，避免污染。

### 7.3 按轮事实抽取

`CLAIM_EXTRACT` prompt 已存在（`services/grading/prompts.py`）。要按轮产出 `turn.fact_results`，需要：

1. 对每一轮的 `final_answer` 调一次 `extract_claims()`；
2. 用同一个 `case_snapshot.must/may/must_not` 调 `grade_envelope()`；
3. 把结果写入轮次级字段（见 §8.2）。

成本：每轮 1 次 claim 抽取 LLM 调用（可缓存、可降级为规则抽取）。语义风险：中间轮可能本来就不该给出最终事实，需在界面注明「轮次诊断，不代表整通通过」。

### 7.4 不建议改的

- **不要把生成的 SQL 写进回复正文**：SQL 只做证据。
- **不要为了「有数据」让裁判编 `expected`**：事实判定必须可复核。
- **不要为了凑轮次分改 `pass_flag` 语义**：整通 pass 仍由最终答案 / 契约决定，轮次分只做诊断。
- **不要强制 `expected_answer` Schema 必填**：会推高 AI 草稿失败率，改为「提示 + missing」。

---

## 8. 后端改动清单

### 8.1 P0：必须在真实数据上修的四项

| # | 缺口 | 影响 | 改动点 | 迁移 |
| --- | --- | --- | --- | --- |
| 1 | 前端写死 80；阈值仅在 LEGACY 的 `judge_result.threshold` 里，契约模式完全没存 | 页头 / 抽屉 / 判分页口径错误，可能把真实通过显示成未通过 | ①前端改读 `judge_result.threshold` → `case_snapshot.pass_threshold` → 常量兜底；②`_complete_contract` 把 `ctx.pass_threshold` 写进 `answer_grading.pass_threshold` 与 `judge_result.threshold` | 无 |
| 2 | 响应头 / 状态码未保存 | 证据「响应」页签无法还原 | `target_api.py` 的 `response_payload` 增加 `headers`（脱敏）与 `status_code`（SSE + JSON 两条路径） | 无（JSON） |
| 3 | 多轮缺 `turns[].expected_answer` | 对照右列为空 | 改用例生成 prompt + 编辑器提示（§7.1） | 无 |
| 4 | 裁判看不到逐轮期望 | 裁判无法按轮判 | `build_expected_text()` 增加 `turns[].expected_answer` 列表 | 无 |

### 8.2 P1：轮次诊断（推荐方案 B'）

新增 1 个 JSON 列，避免 5 列散落：

```text
turn_trace.turn_grade JSON
{
  "judge_score": 82.0,
  "dimension_scores": {...},
  "error_tags": [...],
  "judge_reason": "...",
  "fact_results": [...],
  "contract": {"passed": 7, "total": 8},
  "source": "TURN_DIAGNOSTIC"
}
```

- 执行器在每轮结束后做**诊断判分**，不参与整通 `pass_flag`；
- 前端 `TurnTrace` 类型把可选的 `judge_score / dimension_scores / error_tags / judge_reason / fact_results / contract_*` 改为读这个对象（保持向后兼容）；
- serializer 把 `turn_grade` 展开或原样透出；
- 界面继续注明「轮次诊断分，不改变整通 pass」。

> 这样既能让多轮判分页 / 对照有真实逐轮数据，又不破坏「契约只看最终答案」的既有语义。是否解禁此前「后端不加 `turn_scores`」的决策，需要确认。

### 8.3 P1：其他小改

| 缺口 | 改动 |
| --- | --- |
| `self_check` 无触发轮次 | `SelfCheckResult` 增加 `turn_index`（= 最后一轮），落库；前端不再靠正则解析 `trigger` |
| `finish_reason` 未落库 | 若上游返回则抽到 `response_raw.finish_reason`；抽不到保持「未保存」 |
| `evidence` 列从不写入 | 若要做「事实账本」，在 `_save_turn` 写入 `fact → value → 调用引用`；否则从契约中删除该展示项 |
| `pass_threshold` 展示 | 前端统一读 `judge_result.pass_threshold` / `answer_grading.pass_threshold` / `case_snapshot.pass_threshold`，最后才回退常量 |

### 8.4 环境 / 接口契约（非后端代码）

| 界面项 | 需要被测接口提供 | 配置位置 |
| --- | --- | --- |
| 调用链 | `data.tool_calls[]`（`name/tool/kind/status/latency_ms/args/result`） | 环境 extractor，默认 `$.data.tool_calls` |
| SQL | `data.generated_sql` | 默认 extractor |
| 意图 | `data.intent` | 默认 extractor |
| 取数明细 | `data.raw_db_data` | 默认 extractor |
| 模型 / 温度 / max_tokens | 请求体里有对应字段 | 环境 body template |

> 这些字段不存在时，界面已按「未记录 / 空」降级，不应伪造。

---

## 9. 验收清单

### 9.1 真实数据（单轮 LEGACY）

- [ ] 页头模型来自请求体；取不到时标「裁判模型」。
- [ ] 「阈值」显示后端实际阈值，不是 80。
- [ ] 证据 → 响应：有状态码、有响应头（或明确「响应头未保存」）。
- [ ] 对照：单轮顶层 `expected_answer` 能显示；空时提示补哪个字段。
- [ ] 判分：规则 / 总分 / 归因与后端一致。

### 9.2 真实数据（多轮）

- [ ] 无轮次分时显示「未判定」或「—」，不出现假分数。
- [ ] 只有最后一轮挂执行级 `fact_results`，并注明口径。
- [ ] 无轮次 reason 时显示「会话级结论（不归属本轮）」。
- [ ] 对照缺 `turns[].expected_answer` 时提示具体轮次与字段。
- [ ] 自检不硬塞到某一轮；有 `turn_index` 后才归属。

### 9.3 契约模式（ANSWER_CONTRACT）

- [ ] 页头 KPI 标签为「质量分参考 / 排序分」。
- [ ] pass 由 `answer_grading.passed` 与硬门槛决定，不由分数决定。
- [ ] 规则区不展示（契约与规则互斥）。

---

## 10. 本次已收口的前端口径（代码）

- `model.ts` 新增 `expectedScope / factsScope / judgeReasonScope / sessionJudgeReason`：
  - 多轮不再把会话级裁判结论当本轮结论；
  - 多轮不再把会话级期望当本轮期望；
  - 执行级事实只在最后一轮展示并注明。
- `TurnDrawer.vue`：「期望答案」注明来源；「LLM 综合结论」区分本轮 / 会话级；复制流程裁决时带口径。
- `JudgePanel.vue`：行内 reason 区分本轮 / 会话级。
- `TurnCompare.vue`：缺期望时说明「多轮未维护本轮 `expected_answer`」，不再笼统写「用例快照未提供」。

> 这些改动只让界面**更诚实**，不会凭空产生数据；真实逐轮展示仍取决于 §8 的改动。

---

## 11. 业务对齐与分阶段方案

### 11.1 功能块 ↔ 业务问题 ↔ 数据依赖

| 功能块 | 回答的业务问题 | 关键数据 | 真实数据可用性 |
| --- | --- | --- | --- |
| 页头摘要 | 这是哪条用例的哪次执行，谁判的，多久 | `ExecutionRecord` + `judge_result.threshold` + 请求体模型名 | ✅ 可用；阈值口径待修 |
| 对话轮次 | 双方实际说了什么 | `TurnTrace.query / final_answer` | ✅ 可用 |
| 对照 | 每轮「说的」与「该说的」差在哪 | `turns[].expected_answer` + `fact_results` | ⚠️ 人工数据缺失 + 事实仅执行级 |
| 判分页 | 为什么通过 / 不通过，哪轮拖后腿 | 轮次门槛 + 轮次分 + 轮次归因 | ⚠️ 只有会话级结论 |
| 抽屉「本轮 / 执行」 | 这一轮怎么跑出来的 | `request_payload`、`tool_calls`、`latency_ms` | ✅ / ⚠️ 取决于被测接口 |
| 抽屉「证据」 | 原始请求响应能否复核 | `request_payload`、`response_raw` | ⚠️ 缺响应头 / 状态码 |
| 抽屉「派生与上下文」 | 自检做了什么，快照是什么 | `self_check`、`case_snapshot` | ⚠️ 自检无轮次归属 |

**结论**：页面骨架与交互已 1:1 还原，真实数据缺口集中在 **轮次级判分** 与 **响应元信息** 两块；其余是「人工没填」或「上游没返回」，界面已诚实降级。

### 11.2 分三期落地

**P0 口径修正（无迁移，建议先做）**

1. 前端阈值：`judge_result.threshold` → `case_snapshot.pass_threshold` → 常量兜底；「阈值」标签同步显示来源。
2. `_complete_contract` 补 `answer_grading.pass_threshold`、`judge_result.threshold`。
3. `response_payload` 增加脱敏 `headers` 与 `status_code`（SSE / JSON 两条路径）。
4. `_create_execution` 写入 `max_turns`；`SelfCheckResult` 增加 `turn_index`（= 最后一轮）。

产出：页头 / 抽屉 / 判分页阈值与后端一致；证据「响应」可还原状态码与响应头；自检可归属到轮。

**P1 轮次诊断（1 个 JSON 列，不改 pass 语义）**

- 新增 `turn_trace.turn_grade`，由执行器在每轮结束后写入：
  `judge_score / dimension_scores / error_tags / judge_reason / facts / contract{passed,total} / source`。
- 来源优先级：`rule_check`（确定性）> 裁判 `turn_scores[]`（若 prompt 已支持）> 不写。
- 校验：`turn_index` 必须落在本次轮次范围，四维合计与综合分偏差 > 5 分则整段丢弃。
- 整通 `pass_flag` 仍由最终答案 / 契约决定，轮次分只做诊断，界面显式标注。
- 前端把可选轮次字段切到 `turn_grade`，缺省继续降级。

产出：判分页 / 抽屉 / 对照出现真实逐轮分数、四维、归因；不再出现「未判定」假阴性。

**P2 数据生产（人工 + prompt）**

1. `CASE_AUTHOR_DEFAULT_CONTENT` 要求多轮写「每轮可核对的结论口径」，缺失进 `warnings` / `missing`，**不改 Schema 必填**。
2. `build_expected_text()` 增加逐轮 `turns[].expected_answer` 段，裁判才拿得到。
3. 编辑器对多轮缺期望显示黄色提示，不阻断保存。
4. 逐轮事实抽取（可选、默认关）：每轮 `CLAIM_EXTRACT` + `grade_envelope()`，成本 N 次 LLM 调用。

产出：对照右列有内容；裁判可按轮评价；事实级 expected/actual 仍由代码判定，不让 LLM 编。

### 11.3 需要拍板的三件事

| 决策 | 选项 | 结论 |
| --- | --- | --- |
| 是否新增 `turn_grade` | 加 / 不加（后端继续只给会话级） | **不做（已确认）**：轮次判分非必选，界面诚实降级为「未判定 / 会话级口径」即可；后续如需失败归因增强，再评估「只判失败轮」的按需方案 |
| 逐轮事实是否真跑 | 默认开 / 默认关（按执行开关） | **默认关**，先靠规则 + 裁判会话级归因，成本可控后再开 |
| 阈值来源 | 执行快照 / 后端常量 | **执行快照**，历史执行不随配置漂移 |

### 11.4 明确不做的

- 不为「有数据」让裁判生成 `expected` / `actual`；
- 不把轮次分写进 `pass_flag`；
- 不强制 `expected_answer` Schema 必填；
- 不把生成的 SQL 放进回复正文。

---

## 12. 原型逐项扫描复核（增量发现）

> 扫描方法：读取 `design/case-turn-flow-final.html` 的 mock 数据模型（`turns` / `EV` / `JUDGE` / `selfTurn` / `buildRequest·buildResponse·headersFor·headersForRes`）与全部渲染函数，把每个展示项还原为「目标字段」，再对照 §1 的真实链路。

### 12.1 本轮扫描新增的断链点（§0–§11 未覆盖）

| # | 原型目标 | 现有链路 | 定性 | 最短变更 |
| --- | --- | --- | --- | --- |
| N1 | 每轮被测 tokens（列表分隔条 / 执行段「输入/输出/合计 tokens」） | 前端只读 `turn.simulator_prompt/completion_tokens`——这是**模拟器** tokens，静态多轮恒为 0 | **前端可解** | 回落读 `response_raw.body.data.usage`（`$.data.usage` / `$.usage`），再退 0 并标「未记录」 |
| N2 | `finish_reason`（本轮段「输出」） | 未抽取未落库 | **前端可解** | 回落读 `response_raw.body.data.finish_reason` 或 SSE `metadata.finish_reason`；后端 extractor 可选补 `$.data.finish_reason` |
| N3 | 执行快照页「与当前用例的差异」（新增规则 / 期望值变更） | 当前为诚实空态「无法计算差异」 | **需新数据** | 后端在 `ExecutionDetail` 增加 `case_current`（当前用例最新快照），前端 diff；或前端调用现有用例详情 API 自行 diff（注意权限/404） |
| N4 | 逐轮 trace / span / 请求 ID | 已由 `trace_uid + turn_index` 派生、请求 ID 取 `x-request-id` 请求头 | ✅ 已闭环 | 无 |
| N5 | 响应头含 `X-Request-Id / Server-Timing / X-Ttft-Ms` 等 | `response_payload` 无 headers | 后端（易补） | 见 §8.1-2 |
| N6 | 原型自不一致 | 执行快照页写「裁判阈值 4.0」（旧 5 分制残留），判分页为 0-100 / 80 | 以判分页为准 | 前端已按 0-100 实现，不搬运 4.0 |

### 12.2 扫描确认已闭环（或已诚实降级）的目标

- 对话四布局（summary/work/bubble/rail/master）全部字段：`query / summary / final_answer / ttft / latency / intent / 变量 / 状态` ✅；
- 对照卡：实际←`final_answer`、期望←`turns[].expected_answer`、diff 三元组←fact 级 expected/actual（§3.2）；
- 判分页热力表列（轮次/结论/提问/规则/契约/四维/归因/总分）与行内详情：字段依赖与 §4 一致；
- 抽屉「本轮」运行标识 11 项：请求 ID / trace / 模型 / 温度等全部带回落 ✅；
- 「判定」段裁决返回校验（维度合计 = total、按分数/按门槛 pass、标签枚举）：纯前端校验 ✅；
- 「执行」段调用链（阶段聚合 / 异常筛选 / 慢步骤 / 组内滚动）与页面级轨道、工具统计：全部由 `tool_calls` 派生 ✅（数据本身依赖被测接口）;
- 证据 7 子页签的复制、JSON 高亮、组结构 ✅；
- 「派生与上下文」：会话级判定上下文 / 执行快照 / 请求变量 ✅；追问自检按执行级展示并注明 ⚠️（`turn_index` 待补）。

### 12.3 变更归属汇总

| 归属 | 项 |
| --- | --- |
| 仅前端（无后端改动） | N1 逐轮 tokens 回落、N2 finish_reason 回落、阈值读 `judge_result.threshold`（LEGACY 已落库） |
| 后端小改（无迁移） | 响应头 + 状态码落 `response_payload`；契约模式补 `pass_threshold`；`max_turns` 写入；`self_check.turn_index` |
| 结构变更（**已确认不做**） | ~~`turn_trace.turn_grade`~~：轮次判分非必选，界面诚实降级；后续如需，仅评估「失败轮按需诊断」 |
| 需新数据源 | `case_current`（快照差异）；逐轮 `expected_answer`（prompt + 人工） |
| 上游接口契约 | `tool_calls` / `generated_sql` / `intent` / `usage` / `raw_db_data`（环境 extractor） |

### 12.4 落地记录（2026-11-23，A + B 组已实现）

**前端（`frontend/src`）**

- `case-trace/model.ts`：`PASS_LINE = 80` 常量废除，改为 `passLineOf(detail)` 读取链（`judge_result.threshold` → `answer_grading.pass_threshold` → `case_snapshot.pass_threshold` → `DEFAULT_PASS_LINE` 兜底），`TraceModel` 新增 `passLine / passLineSource`；逐轮 tokens 回落读响应体 `data.usage / usage / SSE metadata.usage`，再退模拟器 tokens；新增 `finishReasonOf()`；`triggerTurnIndex` 优先后端 `turn_index` 字段。
- `TurnDrawer.vue` / `JudgePanel.vue` / `CaseTraceInspector.vue`：全部 `PASS_LINE` 引用替换为 `model.passLine`，阈值展示附来源标注（执行口径 / 用例快照 / 前端默认）；`finish_reason` 改为真实读取，缺失仍显示「执行记录未保存」。
- `types.ts`：`SelfCheckResult` 增加可选 `turn_index`。

**后端（`backend/billing_qa`）**

- `adapter/target_api.py`：SSE 与 JSON 两条路径的 `response_payload` 均落 `status_code` + `headers`（`_sanitize_headers` 脱敏，`set-cookie` 加入敏感头）；此前响应头完全不落库。
- `engine/executor.py`：契约模式 `answer_grading.pass_threshold` 与 `judge_result.threshold` 随执行落库（无记录早退分支同样补齐）；`_create_execution` 写入钳制后的 `max_turns`。
- `engine/self_check.py`：`SelfCheckResult` 增加 `turn_index`（= 原对话最后一轮），to_dict 自动透出。
- 未做 B5（extractor 增加 `usage` / `finish_reason`）：前端已直接读 `response_raw`，extractor 路径冗余。

**验证**：`npm run typecheck` ✅、`npm run build` ✅；后端 `compileall` ✅、`pytest test_self_check`（18）✅、`test_engine_e2e / e2e_contract / environment_debug_chat / regression_fixes`（28）✅（1 例超时抖动单独重跑通过）。

### 12.5 复扫记录（同日二次扫描）

对 A + B 落地后的链路重新按原型目标核对，发现并修复 3 处残留：

| # | 发现 | 处理 |
| --- | --- | --- |
| R1 | 后端已落库 `status_code`，但前端**没有任何位置读取展示**（原型为 `HTTP/1.1 200 OK` 徽标） | `TurnVM` 新增 `responseStatus`（读 `response_raw.status_code`），证据「响应」页签头部新增「状态码 / 响应形态」两行 |
| R2 | 响应头空态文案「执行记录只落库响应体，未保存响应头与 status_code」已过时（对新执行不成立） | 改为「本执行未保存响应头（历史执行记录；新执行已随报文落库并脱敏）」 |
| R3 | `selfTriggerValue` 注释仍写「后端只落库触发原因，没有触发轮次字段」 | 逻辑本身已通过 `turn_index` 闭环（归属轮次 → `selfCheckTurn` → 显示 T{n}），仅更正注释 |
| R4 | SSE 轮次响应体显示 `null`：SSE 落库只有 `frames/content` 没有 `body`，前端未回落，且下方文案写着「展示聚合后的内容」自相矛盾（演示数据实测发现） | `responseBody: response?.body ?? response?.content ?? null`，流式轮次响应体回落到聚合内容 |

复扫确认已闭环：阈值读数链贯通 13 处展示、`finish_reason` 真实读取、逐轮 tokens usage 回落、`requestId` 请求头→响应头双回落（原有实现，无需改）、自检轮次归属、`max_turns` 新执行落库。其余「未保存」类文案逐条核对均为真实空态，保留。`npm run typecheck`、`npm run build` 复跑通过。

### 12.6 演示数据生产可达性核验（同日三轮，mock 数据逐项溯源）

演示 mock（`design/mock-trace-server.cjs`）逐字段对照真实生成链路核验，确保「界面展示 ⇄ 生产可生成」一一对应。前三轮结论见 12.6.1/12.6.2 摘要，第三轮（序列化层 + 枚举盲区）修正 8 处：

| # | 发现 | 生产真实形态（file:line） |
| --- | --- | --- |
| V1 | mock 用 `DYNAMIC_MULTI` + 「部分轮未判定」 | 动态多轮规则逐轮全跑（executor.py:405），该组合不可达；改 `STATIC_MULTI`，轮级规则逐轮可配（executor.py:266-270）是唯一可达组合 |
| V2 | SSE 轮带 intent/tool_calls 白盒字段 | `$.data.*` 提取不到 → 恒 null（extractor.py:14-22）；SSE 帧改问账风格 `{data:{delta}}`（sse.py:50-56 支持） |
| V3 | 规则检查自定义 label / actual / 缺 extracted | `must_contain` label 自动生成、actual 恒 None、to_dict 全键含 `mode/bound_name/reason/extracted`（rule_evaluator.py:253-263, 46-64）；执行级 merge 带 `T{n} ·` 前缀、extracted 空dict（executor.py:288 + rule_evaluator.py:317） |
| V4 | `input_source:'CASE_INPUT'`、`strategy:'SINGLE'`、trigger 带分数、`hidden_context` 用 `{title}` | 枚举 `INITIAL/SIMULATOR/STATIC/MANUAL`（executor.py:272,330,388）；`AVG/VETO`（enums.py:45-47）；trigger 固定「LLM 裁决不通过」（self_check.py:107）；HiddenContextItem `{key,content,release_hint}`（schemas/case.py:274-277） |
| V5 | `trace_uid` 用 `trc-` 前缀 | `str(uuid.uuid4())` 裸 UUID（executor.py:654） |
| V6 | 轮次带 `judge_score` 等 5 个序列化层不存在的键；`raw_request_response` 多 `judge_prompt`；缺 `attempt_count` | TurnTraceOut 无这些键（schemas/task.py:256-280）；raw 三键形态（serializers.py:200-207，`judge_prompt` 仅 `/raw` 端点 executions.py:172）；ExecutionListItem 恒返回 attempt_count（schemas/task.py:310） |
| V7 | JSON 轮给独立 TTFT（812/755） | JSON 轮 `ttft_ms=latency` 恒等（target_api.py:429），仅 SSE 轮有独立 TTFT；执行级 = 各轮均值 round2（executor.py:461-466）→ 修正为 avg(2210,640,3050)=1966.67 |
| V8 | 快照只给部分键；任务/通知枚举失真 | `build_snapshot` 全字段恒写入含 None（snapshot.py:105-119）+ `verified_facts:[]`（facts.py:87-90）；TaskStatus 无 `FINISHED`→`COMPLETED`（enums.py:53）、control_state 完成态 `FINISHED`（scheduler.py:374）、schedule_type 无 `NONE`→`IMMEDIATE`（enums.py:62）、repeat_rounds/round_index 默认 1（schemas/task.py:124-125）、TaskOut 恒含 summary/error_message/environment_name/run_count/unfinished_cases/retrying_cases（schemas/task.py:130-138）；通知仅投影用户/角色管理审计事件（notifications.py:11-21），键形 `message` + level ∈ info/success/warning/error（schemas/notification.py:11-19） |

核验后确认本就真实：B1 报文键形（SSE: mode/status_code/headers/frame_count/metadata/frames[-50:]/content，target_api.py:399-407；JSON: +body，:438-443）、judge_result aggregate 键形（judge.py:326-340）、judges 表（executor.py:746-761）、policy_notes 固定文案（executor.py:511）、self_check dataclass 全键与丢 None 行为（self_check.py:41-79）、`/raw` 端点（executions.py:150-180）、ExecutionDetail 全键（schemas/task.py:337-358）、P95/会话一致性为前端对真实字段的确定性推导（CaseTraceInspector.vue:204-213）。保留的编造项仅剩剧本数值与上游自由文本（回复措辞/裁判理由/tool_calls 内部结构/raw_response 内部键）。浏览器实测复验通过（T1/T3 JSON 轮 TTFT==时延、T2 SSE 独立 TTFT、快照页 3 变量/3 轮逐轮期望/阈值 75 执行口径、通知端点新形态）。

### 12.7 第四轮核验（模式可达性 / 内部一致性，同日）

换角度做模式与一致性审计，发现并修正 4 处（其中 1 处推翻 12.6 的 V2 结论）：

| # | 发现 | 生产依据与处理 |
| --- | --- | --- |
| V9 | 同一执行内「T1 JSON + T2 SSE」混排 | SSE/JSON 分支由环境级 `is_stream` 标志或响应 content-type 决定（target_api.py:369），环境固定 → 单执行内不可混排。**改为全 SSE 环境**：白盒字段经问账式首帧 `{data:{白盒键}}` 深合并进 metadata 后仍可被 `$.data.*` 命中（extractor.py:14-22 + sse.py:66），流式与白盒演示能力都保留；V2「SSE 轮白盒恒 null」结论修正为「取决于上游是否在 data 帧携带白盒键」 |
| V10 | dimension_labels 用简写文案 | 生产原文为「数据准确性与防幻觉 / 取数口径与逻辑一致性 / 目标达成与主动澄清 / 服务规范与话术清晰度」（judge.py:26-29）；界面展示用前端 DIMENSIONS 常量（原型口径），数据侧已对齐生产 |
| V11 | 轮次 created_at 与 latency 有 1.6~2s 无解释缝隙 | created_at 是落库时刻（≈响应完成 + 落库开销），重排时间线：33.210 → 34.880 → 38.050 → 裁判 49.006（~11s 裁判 LLM 时延，3410 tokens）→ 自检 10:12:50 → 完成 51.845，与墙钟 20845.3ms 自洽 |
| V12 | mock 的 metadata 用浅合并 | 真实是全帧深合并（sse.py:63-66），白盒帧 data 键会被浅合并整体覆盖丢失 → mock 实现深合并与后端一致 |

顺带确认：红线策略（judge.py:164-193）本剧本均不触发（data_accuracy=20>0、termination=COMPLETED、裁判原始 pass=false）；reason 拼装格式「【模型】{总分}分：{理由}」（judge.py:320-322）；脱敏头集合 {authorization, cookie, x-api-key, token, x-token, set-cookie}（target_api.py:476）；SSE 轮 ttft 独立回落链（target_api.py:390）。浏览器实测：三轮均「流式 · N 帧」、TTFT 812/640/755ms（执行级均值 735.67）、T2 意图/调用链白盒正常提取、自检同 SSE 形态。

四轮累计修正 26 处。终极验证建议（未做）：等生产一条新执行落库后，用真实 `/executions/{id}` 响应反向 diff mock 结构，做一次端到端对照。

### 12.8 通过用例不展示自检内容（用户需求，同日落地）

**口径**：自检只在「LLM 裁决不通过」时触发（self_check.py:106-107）。通过的用例后端只有两种落库形态——`self_check=null`（任务未开自检）或 `{status:'SKIPPED', skip_reason:'LLM 裁决通过，未触发追问自检'}`（self_check.py:128，skipped() 只落两键）。界面据此收口：**自检内容仅在真实发生（DONE，或自检尝试本身失败的 ERROR）时呈现；SKIPPED/null 一律不显示**。

| 改动 | 位置 | 说明 |
| --- | --- | --- |
| 新增 `selfCheckPerformed(record)` | case-trace/model.ts | `status !== 'SKIPPED'` 才算真实发生；`useTraceView` 的 selfCheck/selfCheckTurn 推导由此收口（S1 派生块、页签条 pill、抽屉上下文全部联动） |
| 独立「追问自检」页签同口径 | CaseTraceInspector.vue | 该页签读的是 `detail.self_check` 原文，单独用 helper 门控 |
| TraceModel 新增 `executionPassed` | case-trace/model.ts | `pass_flag === true`；抽屉「本轮/派生与上下文」两处追问自检小节改为 `v-if="selfRecord \|\| !executionPassed"`——未通过但无记录的执行保留诚实空态，通过的执行整段隐藏 |

mock 新增通过用例执行 **#6581**（BILL-FEE-0422 · 2 轮全通过 · self_check=SKIPPED）与失败例 #6580 对照演示。浏览器双向实测：#6581 全页（页签条/对话页/抽屉/筛选条）无任何「自检」字样；#6580 的 S1 派生块与「追问自检」页签完整保留。`npm run typecheck`、`npm run build` 通过。

**自检页签按原型重做**：原实现是三张 Tailwind 信息卡，不符合 `design/case-turn-flow-final.html` 的 renderSelfCheck 结构。新建 `case-trace/SelfCheckPanel.vue` 1:1 还原：`selfdiv` 分隔条（由 T{n} 自动触发 · 同一会话追加 1 轮 · 不参与通过判定）→ `snapgrid` 两张 `sbox` 卡（触发原因 = trigger 固定文案 + turn_index 补充；自检配置 = varlist chips template/v{n}/触发轮/追加 1 轮/不影响判定）→ `promptcard` 长卡（【】标题加粗、104px 折叠渐变、字符/行数、复制/展开全文）→ `selfAnswerBubble` 琥珀气泡（bmeta 模板标识 + stat-pill 自检轮次 + SSE 聚合正文 + callout 自检结论 + ev-chips 定位触发轮/复制结论）。数据映射全部真实：正文 = `response_raw.content`（target_api.py:384-385 聚合链）、tokens = `response_raw.metadata.usage.completion_tokens`（sse.py:63-66 深合并链）、时延 = `latency_ms`、结论 = `conclusion`/`error`/`skip_reason` 回落；原型中「5 个上下文片段」「max_turns=1」「affects_score=false」等生产无对应字段的演示值改为诚实等价物（追加 1 轮/不影响判定为业务事实）。CaseTraceInspector 相应删除旧版 selfCheckStatus*/selfCheckBody/selfCheckPromptOpen。

**判分矩阵轮次折叠图标方向修正**（用户反馈）：原为「收起↓ / 展开左转」方向反了，改为 chevron-r 收起向右、`.sel` rotate(90°) 展开向下（与原型 .go 系列折叠约定一致）。同轮反馈的展开行视觉重复确认为**轮次档案头与表格行复述**（T 号/状态/总分上下两行各出现一遍）：档案头精简为仅保留表格行没有的「意图 · TTFT · 时延」轻量 meta 行（9px mono 弱视觉），T 号、状态 pill、总分交由表格行承载，不再复述；jd-gate 硬门槛链与轮次级归因空态保留原型结构。（此版本已被下一条取代：档案头整行随后移除，jd-gate 移入右栏。）

**判分展开行改为「Agent 回复 → 各项打分 → 裁判理由」，长文本折叠 + 双栏收口**（用户反馈两轮合并）：档案头那行「意图 / TTFT / 时延」属于「对话轮次」分隔条与本轮详情的口径，放在判分展开区头部既无评分信息量又占首屏，整行移除（`jx-h`/`jx-meta`/`tnum`/`jx-sc` 样式一并删除）。**界面统一称「Agent」**：气泡头原「被测客服 Agent」改为「Agent」，同时把 `TurnBubble` 的 `aname`、自检分隔条与设置页说明里的「被测 Agent」、用例期望时间轴的「客服」一并替换，界面不再出现原称谓（数据里的模型名、自检 Prompt 原文不改写）。展开区首位为 **Agent 回复气泡**（`jx-ai`：26px 渐变 bot 头像 + 白色气泡 `border-radius: 5px 14px 14px 14px` + `#e8e8ed` 描边 + 微投影，直接复用「对话轮次」`bbubble-ai` 的气泡语言；气泡头为「Agent」+「本轮回复」胶囊 + 字数；正文 `turn.answer` 去掉 `:icon:` 标记后 `white-space: pre-wrap` 以 12.5px/1.9 深色渲染；无正文时显示「本轮没有回复正文（可能执行未完成或调用失败）」诚实空态）。**长文本不再撑高布局**：回复正文默认截断 143px（6 行）、裁判理由默认截断 137px（6 行），底部渐变遮罩 + 「展开全文 / 收起」（chevron 随状态翻转）；是否溢出用真实 `scrollHeight` 与 `ANSWER_CLAMP`/`REASON_CLAMP` 比较判定，不用字数猜，切换轮次自动收起并重算；提问超过 28 字时在首位展示全文（表格行只截断两行，这里补全完整上下文，不参与折叠）。其后依次是：**各项打分**（`jd-dims` 浅底卡：四条比例条，或一行「后端只落库执行级四维」口径注，标口径「四维 · 轮次级 / 执行级口径」）→ **REASON 裁判理由**（`jx-judge` 独立判定卡：`--brand` 3px 左边框 + `--brand-edge` 描边 + 浅靛底，标题带 scale 图标徽章与「裁判模型输出原文 · N 字」，正文 12px/1.9；会话级回落注明「会话级结论，不归属本轮」）。**布局从三栏收为两栏**：`.jx-inner` 为 `minmax(0, 1.7fr) minmax(0, 1fr)`（≤1100px 右栏固定 250px，≤820px 单列堆叠），左栏 `.jx-main` 只承载阅读流（回复 → 打分 → 理由），右栏 `.jx-side` 收口「门槛检查 / 错误归因 / 操作按钮（2×2）」，长文本只影响阅读列，不把门槛与操作推走；无长提问时回复气泡与右栏卡片顶边对齐（1310px 视口：主栏 544px + 右栏 320px）。**门槛检查全部收口到右栏**「门槛检查」卡（规则逐条 + 答案契约 facts + 已通过/未通过结论），左侧不再出现 `jd-gate`「硬门槛 规则 x/y · 契约 x/y → 结论」重复条（样式同步删除）；矩阵的规则/契约列仍保留为对比用计数。数据来源与对话页同一字段 `TurnVM.answer`（target_api.py 聚合 `response_raw.content` → executor 落库），不新增字段。同时修正 `.jx > td` 未重置 `text-align` 导致展开区正文继承表格 `center` 的问题（Agent 回复、裁判理由、空态注全部改为左对齐）；并收紧首屏留白：`.jx-inner` 上内边距 12→6px，展开行顶部不再出现一条空白带。**原型已同步**：`design/case-turn-flow-final.html` 的 `detailRow` 与 CSS 按同一结构重写（`syncJudgeClamp` 用真实 `scrollHeight` 决定「展开全文」是否出现，T4 为长文本压力用例（溢出显示按钮），其余 5 轮内容未溢出故不显示按钮；长文本注入压测 137 → 182 → 137px、文案与 chevron 同步翻转），原型与实现保持一致。**演示数据覆盖该形态**：#6580 第 3 轮回复 512 字（1440px：143 → 238px；1920px：143 → 190px）、裁判理由 522 字（137 → 228px / 137 → 182px）；原型 T4 回复 550 字（1440px：143 → 190px）、理由 531 字（137 → 182px）。两处均出现渐变遮罩与「展开全文」，用于验证超长文本不撑坏布局；短文（#6580 T1 回复 24px、原型 T6）不出现按钮，用于验证诚实降级。

**修正（用户反馈「最后一行虚化却无法展开」）**：此前 `clamp` 无条件挂在「未展开」状态上，而它同时带 `max-height` 与渐变遮罩——文字正好放得下（如 1500px 宽时回复恰好 143px、理由恰好 137px）时没有任何内容被截断，遮罩却仍把最后一行虚化，溢出判定为 false 又不显示「展开全文」，于是形成「虚化却展不开」。现改为 **`clamp` 只在「真实溢出 且 未展开」时挂载**：Vue 侧 `:class="{ clamp: answerOverflow && !answerOpen }"`（理由同理）；原型侧 `syncJudgeClamp()` 先移除 `clamp` 量出全文高度，再按 `scrollHeight > limit + 4` 决定是否回挂，展开态用 `data-jopen` 记录、点击后统一重测。未溢出时既不截断也不虚化、不显示按钮。实测：1500px 宽回复/理由均为 `h === scrollHeight`（143/137）且无遮罩无按钮；收窄到 985px 视口（主栏 444px）时回复 143→333px、理由 137→342px，遮罩与按钮同时出现，展开/收起可正常往返。

**页签条去掉计数（用户反馈）**：`对话轮次 3`、`判分 1`、`追问自检 1` 这类数字对页签导航没有增益，且与各页内部的统计重复，容易让人误以为是「待处理数量」。页签条只保留图标 + 名称：Vue 侧删除 `turns`/`judge`/`selfcheck` 三处计数以及 `ANSWER_CONTRACT` 模式下的契约门槛 `x/y` 摘要（该信息仍可在判分页右栏「门槛检查」卡查看），并移除因此不再被引用的 `gateSummary`、`failedGates` 计算属性；原型删除 `<span class="n">` 徽标与 `.tab-btn .n` 样式。实测应用与原型页签文案均为「对话轮次 / 判分 / 追问自检 / 执行快照 / 原始报文 · 调用链」，DOM 内无附加数字（判分页签的琥珀色提示点属于状态指示，非计数，保留）。

**对话轮次「判定」入口按结论着色（用户反馈）**：该入口原先恒为中性灰，通过、部分命中、未通过三种轮次看起来完全一样，扫读时无法区分。现按 `turn.verdict` 着色：通过 = 绿（文字 `#047857`，分数胶囊 `#ecfdf5` 底 + `#a7f3d0` 内描边）、部分命中 = 琥珀（`#b45309` / `#fffbeb` / `#fde68a`）、未通过 = 玫红（`#be123c` / `#fff1f2` / `#fecdd3`）、未判定 = 中性灰（分数胶囊 `#f8fafc`），hover 用同色系深一档而非统一靛蓝。只改颜色与胶囊底色，文案、图标与入口行为不变，色值与相邻状态胶囊、判分页判定卡同一来源。原型同步：`evStrip` 从「规则是否全过」改为按 `t.status` 选 `ok / warn / fail` 三档（新增 `.ev-chip.fail`），实测判定 91/88/96 为绿（T1–T3）、74 为玫红（T4）、88 为琥珀（T5）。

**追问自检页签重排：自检返回置顶 → 再与配置块互换（用户两轮反馈）**：第一轮反馈「用户首先关注 Agent 的自检返回」，于是把「自检回答」从最底部提到首位，顺序为「分隔条 → 自检回答 → 触发原因 / 自检配置 → 自检 Prompt」。第二轮反馈要求把「自检回答」与「触发原因 / 自检配置」两块互换，最终顺序为 **分隔条 → 触发原因 / 自检配置 → 自检回答 → 自检 Prompt**：先交代为什么触发、按什么模板触发，再给 Agent 的自检返回（正文 + 自检结论），最后以 Prompt 长卡作为输入凭据（其折叠与复制能力不变）。气泡右上角的说明保持「按下文 Prompt 生成」，与 Prompt 位于下方的最终布局一致。间距由 `.judge` 的 flex `gap: 12px` 统一承担（删除 `.snapgrid`、`.promptcard` 的 `margin-bottom` 与 `.selfans-label` 的 `margin-top`，避免旧顺序遗留的外边距把回答块推低或页尾多出空白）。原型 `renderSelfCheck` 同步最终顺序（自检回答包在 `margin-bottom:12px` 的 `.stage-bub` 内），`.promptcard` 去掉 `margin-bottom`。实测 1310px 视口下应用与原型顺序均为 `selfdiv → snapgrid → stage-bub(自检回答) → promptcard`（应用自检回答块 250px、配置块 89px），无控制台报错。抽屉内的「追问自检」折叠块不属本页签，保持原顺序未动。

**自检 Prompt 卡改为中性色（用户反馈「一大坨黄色」）**：原先 Prompt 卡整套沿用琥珀语义（`--amber-edge` 描边、`#fffbeb` 卡头、`#fffdf5` 卡身与卡尾、琥珀色按钮/标题），与上方琥珀的「追问自检」分隔条、自检回答气泡叠加后，整页大面积为黄色，视觉上很重。现把 Prompt 卡降为中性的「原始输入」面板：卡身白底、`#e5e7eb` 描边；卡头与卡尾 `#f8f9fb` 浅灰底 + `#eef0f3` 实线分隔（原来 1px 虚线改实线）；`pc-tag` 灰字灰边白底、`pc-meta` 与字符数改 `#9ca3af`；正文 `#374151`、`【】` 加粗改 `#111827`（不再是琥珀）；遮罩渐变终点由 `#fffdf5` 改为 `#fff`；底部按钮灰字灰边，hover 为 `#f3f4f6`。琥珀色只保留在语义位置（「追问自检」分隔条、自检回答气泡与结论 callout、状态胶囊），一眼能看出「哪段是系统的自检输入」。原型 `.promptcard / .pc-head / .pc-tag / .pc-body / .pc-foot` 同步同一套中性色。实测应用与原型：卡片 `#fff` + `#e5e7eb`、卡头/卡尾 `rgb(248,249,251)`、正文 `rgb(55,65,81)`、按钮 `rgb(107,114,128)` + `#e5e7eb`，卡片高度 175px 不变。

**自检 Prompt 卡同样改为「真实溢出才截断」（顺带修同类缺陷）**：原实现 `.pc-body` 恒定 `max-height:104px` + 未展开即挂渐变遮罩，Prompt 只有一行时也会把唯一一行虚化，且按钮恒显示「展开全文」——与判分页「虚化却展不开」是同一类缺陷。现改为 `scrollHeight > 104 + 4` 判定溢出，只有溢出才挂 `.clamp`（`max-height` 与遮罩一起加）并显示「展开全文 / 收起」；短 Prompt 完整展示、无遮罩、无按钮。实现用 `promptBody` ref + `ResizeObserver`（宽度变化导致重排时重测），`PROMPT_CLAMP = 104` 与 `.pc-body.clamp` 的 `max-height` 显式对齐。演示数据同步：mock #6580 的自检 `prompt` 由单句改为后端渲染后的完整模板（多段【】：失败轮次 / 用户问题 / 本轮回答 / 期望口径 / 判分结论 / 差异明细 / 历史轮次摘要 / 上下文 / 请按顺序回答 / 输出格式），`request_payload.query` 复用同一常量以免两处文案不一致。实测 1310px：`sh=670`、收起态 `104px` + 遮罩 + 「展开全文」，展开 `670px` + 「收起」、遮罩消失，可正常往返；`#6580` 之外的无自检执行（#6581）不渲染本页签。

**「对话轮次」内联自检 Prompt 卡去掉右上角说明（用户反馈）**：该卡头部原为「系统生成 · 非用户消息」标签 + 模板名 + 右侧「发送给 Agent 的就是这段 Prompt」。用户指出这句说明没有必要——卡片本身就位于「追问自检」块内、并有「系统生成 · 非用户消息」标签与「复制 Prompt / 展开全文」操作，语义已足够清楚，重复一句话反而占位。现删除该 `pc-meta` 及其后的空白占位 `.sp`（连带清理已无引用的 `.pc-head .sp` 规则），头部只留标签与模板名。原型的内联自检块本就没有这句说明（`:pc-meta` 仅渲染模板名），因此无需同步。实测 1310px：#6580 的 `.pc-head` 文本为「系统生成 · 非用户消息失败追问自检 · 账单口径 v3」，全页不再出现「发送给 Agent 的就是这段 Prompt」，控制台无报错。

**自检结论按原文换行渲染（用户反馈）**：后端 `self_check.conclusion` 是多行文本（首行 `【自检结论】…`，随后 `1./2./3.` 编号列表），而「追问自检」页签的结论 callout 用普通 `<p>` 渲染，HTML 默认折叠连续空白与换行，界面上挤成一整段，复制出来的文本却带换行——展示与复制不一致。现给 `.callout p` 加 `white-space: pre-wrap; word-break: break-word`，与抽屉 `TurnDrawer` 的 `.dverdict span`、调试台 `CaseDebugWorkbench` 的 `whitespace-pre-wrap` 对齐；复制逻辑不变（`conclusionText` 原样复制，仍是后端原文）。原型 `.callout p` 同步加同一属性。演示数据：mock #6580 的 `conclusion` 改为四行形态（结论行 + 出错位置 / 直接原因 / 修正方式），实测 1310px 下结论段落 `18.7px × 4 行 = 75px`、callout 整块 112px，换行与编号列表正常分条显示。

**抽屉「判定」页重排重点：裁判结论置顶、期望答案强调、分数降级（用户反馈「这部分应该也是强调项，现在打开这个界面侧重点不对」）**：原布局打开「判定」先看到「规则校验 / 契约命中」两张表（无记录时是一整块空态），裁判最重要的判断依据——LLM 综合结论——被缩在「裁判评分」卡底部一条 10px 灰字（`.judgeray`），而 26px 大分数 + 绿/琥珀/玫红底色反而成了视觉主角，重点与信息价值倒挂。现调整为：①裁判结论独立成卡并移到首位，靛蓝左条 + 12px 正文（`.dconcl`，图标 + `pre-wrap`），多轮无轮次级 `reason` 时用后端会话级结论并保留口径说明；②「期望答案」提到第二位并用 `.dexpect.hl` 强调（靛蓝左条 + 12px），它是判断对错的基准；③「裁判评分」降为佐证：数字改 22px 中性灰 `#374151`，去掉分数块的 pass/warn/fail 底色与数字变色，结论色只留给状态胶囊，`pass = … · 阈值 …` 一行保留；④后面依次是规则校验、契约命中、错误归因、裁决返回校验、裁决记录。原型 `renderJudge` 同步同一顺序与同一套样式（`dexpect.hl` / `dconcl`，分数 24px 中性）。实测 1310px 应用 #6580：T1 顺序为「会话级综合结论 → 裁判评分(空态) → 规则校验 → 契约命中 → 错误归因 → 裁决记录」，T3 为「会话级综合结论 → 期望答案 → 裁判评分 → 规则校验(1/2) → 契约命中 → 错误归因 → 裁决记录」；结论卡 199px、期望答案 44px、两者左条 `rgb(99,102,241)`；原型 T4 顺序与样式一致（分数 `rgb(17,24,39)` / 24px），控制台无报错。

**判分页裁判理由头部去掉英文前缀**：展开区里裁判结论的头部原为 `reason · 裁判理由`（英文在前、9.5px 大写 + 中文），与页面其他中文标题不一致，也把重点落在装饰性英文上。现只留「裁判理由」+ 右侧「裁判模型输出原文 · N 字」，顺序与结构（Agent 回复 → 各项打分 → 裁判理由）不变。原型 `.jx-judge-h` 同步。实测应用判分页头部文本为 `裁判理由裁判模型输出原文 · 522 字`，全页不再出现 `reason · 裁判理由`。

**内联自检 Prompt 卡把「来源 / 去向」角标移到右侧并改写（用户反馈）**：用户指出这张卡的关键语义是「系统生成、发送给了 Agent」，但原角标「系统生成 · 非用户消息」挤在模板名左侧，与模板名连成一串，一眼看不出这是系统投喂给 Agent 的输入。现头部改为：左侧只留模板名（`失败追问自检 · 账单口径 v3`），右侧放白底 + `#fbbf24` 描边胶囊「系统生成 · 已发送给 Agent」（bot 图标、600 字重、`#92400e`），中间用 `.sp`（`flex:1`）把两者拉开。文案保留「系统生成」，把「非用户消息」换成「已发送给 Agent」：前者已在隔离说明与「自检返回 · 由 S1 自动发起 · 非用户提问」里反复出现，后者才是这张卡要交代的归属。原型 `selfPromptMini()` 同步：标题 + 字符数在左，`.gen` 角标由 `.sp` 推到最右，样式改为同一颗白底琥珀边胶囊。实测 1310px 头部 39px、模板名距左 12px、角标距右 12px（间隙 630px）；985px 下头部仍单行（39px / 768px 宽）、无横向滚动；全页不再出现「系统生成 · 非用户消息」，控制台无报错。

**「判定」页把 LLM 打分内容与裁决结论合并成一张裁决卡（用户反馈「这部分应该是卡在一起，对最终结果要清晰」）**：上一版虽然把结论提到首位，但「结论 / 期望答案 / 评分」仍是三张独立的卡，读者要在三处之间来回拼：结论卡只有文字没有分数，评分卡只有一个大数字，期望答案又另起一块，最终结果并不「一眼可读」。现合并为一张 `.djudgecard` 裁决卡，卡体左侧 3px 靛蓝条，自上而下四段：①**卡头**（scale 图标 + 「LLM 裁判结论」+ 裁判模型名 + 右端状态胶囊 + 分数），卡头底色按 `jv-pass/jv-warn/jv-fail/jv-unknown` 取极淡的绿/琥珀/玫红/灰，结论色只出现在胶囊与这层淡底上；②**结论正文**（12px/1.9 `pre-wrap`，无原文时输出「执行记录没有给出这一轮的裁判结论原文，不代裁判补写」而不是留白）；③`.djv-meta`（`pass = true/false/—`、`阈值 N（执行口径）`、多轮无轮次分时补「本轮无独立分 · 数字为执行级汇总」）；④**判定基准 · 期望答案**（虚线分隔的浅底区，9px 大写小标题 + 右侧来源「用例快照」，正文 11.5px），有 `dimensions` 时再接一段四维分条与「四维合计 N，是执行级评分，不是本轮独立得分」。分数口径：有 `turn.score` 时显示本轮分并标注「本轮综合分」；多轮没有轮次分时用 `model.totalScore`（与 pass 判定同源的执行级综合分）并标注「执行级综合分」，不再显示原来那个虚线空态大盒。数字保持 19px 中性灰 `#374151`，不随结论变色，避免一个大数字抢走「通过 / 未通过」的第一眼。为支撑这一口径，`TraceModel` 新增 `totalScore`（取自 `totalScoreOf`，`ANSWER_CONTRACT` 走 `judge_result.quality.total_score` / `answer_grading.total_score`，其余走 `detail.total_score`）。多轮中间轮常只有会话级结论：此时正文上方加一枚琥珀小标签「会话级结论 · 未拆分到本轮」，先交代来源再给原文，避免读者把它当成这一轮的独立判定（原来这层含义只藏在 9px 灰字 meta 里）。原型 `renderJudge` 与 `.djudgecard/.djv-*` 系列 CSS 同步为同一结构（原型数据每轮都有轮次分，故无「执行级综合分」与来源标签分支）。实测 1310px 应用 #6580：T1 `jv-unknown` + 「未判定」胶囊 + 58.5「执行级综合分」+ 会话级标签，卡高 294px；T3 `jv-fail` + 「未通过」+ 58.5 + 会话级标签 + 期望答案，卡高 352px；#6581 T2 `jv-pass` + 「通过」+ 92 + 期望答案，卡高 163px；三张卡 `scrollWidth - clientWidth = 0`，卡后仍是规则校验 / 契约命中 / 错误归因 / 裁决返回校验 / 裁决记录。985px：抽屉 860px、卡 823px、卡头 38px 单行（图标 12 + 标题 72 + 模型 59 + 弹性 428 + 胶囊 60 + 分数 125）、无横向滚动、控制台无报错。原型 `?drawer=1|2|3&sec=judge` 三张卡分别为 91 / 88 / 96 分、四维分条 4 条、卡头无溢出；`npm run typecheck`、`npm run build` 通过。

### 12.9 mock 演示数据（独立 ID 段 900001–900009，覆盖判定全形态）

> 2026-11-24 起演示数据迁到**独立 ID 段**：任务 `900101` / `900102`，执行 `900001`–`900009`，用例 `900211`–`900227`。原 `6580`–`6588` / `5061` / `5062` 与真实库同号记录重名，已全部迁出，旧 ID 在 mock 上返回 404。§12.1–§12.8 是迁移前的实测记录，其中的旧 ID 保留原样；下表及 §12.10–§12.11 用新 ID。旧→新对照见本文件末尾与 `design/mock-demo-cases.md` §6。

真实库无法同时演示判定页的所有分支：RUBRIC 1226 条无轮次、ANSWER_CONTRACT 只有 74 条、LEGACY 的 `tool_calls / evidence` 全为空。为此新增 9 条 mock 执行，逐条按后端真实键形构造，**不引入生产不可能出现的字段**：`judge_result` 取 `judge.py` 的 aggregate 键形（`pass / total_score / dimension_scores / error_tags / reason / threshold / judge_count / policy_notes / errors / quality`），`answer_grading` 取契约键形（`total_score / pass_threshold / gates / scorecard / facts`），`turn_trace` 严格只用 `TurnTraceOut` 声明的 20 个字段——`turn_trace` 表没有 `judge_score / dimension_scores / fact_results / judge_reason / contract_*` 列，mock 也不造这些字段（`frontend/src/types.ts` 里它们本就是可选，缺失时前端按空值降级）。

| 执行 | 任务 | 覆盖形态 | 判定页预期 |
| --- | --- | --- | --- |
| 900001 | 900101（开自检） | LEGACY · 3 轮 · 失败 · 自检 DONE | T1/T2 规则未过、T3 未通过 58.5；「追问自检」页签完整 |
| 900002 | 900101（开自检） | LEGACY · 2 轮 · 通过 · 自检 SKIPPED | 通过 92；全页无自检页签与自检字样 |
| 900003 | 900102（关自检） | LEGACY · 单轮 · 分数够但规则未过 | 部分命中 82；四维合计 82「与本轮综合分一致」 |
| 900004 | 900102 | LEGACY · 3 轮 · 全轮无规则 / 无轮次分 · 执行级 71 < 阈值 80 | 三轮均未判定；正文带「会话级结论 · 未拆分到本轮」 |
| 900005 | 900102 | ANSWER_CONTRACT · 单轮 · 契约全过 | 通过 94；facts 2/2、四枚门槛全绿 |
| 900006 | 900102 | ANSWER_CONTRACT · 单轮 · facts / safety 未过 | 部分命中 94；事实实际值显示 999（不再整段 JSON） |
| 900007 | 900102 | LEGACY · 多轮 · T1 未判定 / T2 规则通过 | T1 未判定 92、T2 通过；期望答案只挂 T2 |
| 900008 | 900102 | 历史 RUBRIC · 无轮次 / 无裁判 JSON | 通过 89.7；综合结论说明旧模式只保留执行级分；无 0 轮矩阵 |
| 900009 | 900102 | LEGACY · 单轮 · 48 < 阈值 60 | 未通过 48；卡头极淡玫红；四维合计 48 |

任务 900102 统计：7 个用例，通过 3（900005 / 900007 / 900008）、失败 4（900003 / 900004 / 900006 / 900009），`enable_self_check=false`，故这批 `self_check` 恒为 `null`；自检形态由 900101 的 900001 / 900002 覆盖。访问方式与逐条数据依据见 `design/mock-demo-cases.md`（含启动命令、字段自洽约束、ID 对照）。演示通道独立：mock 后端 `127.0.0.1:8877` + 演示 vite `127.0.0.1:5174`，不触碰真实后端 8000 与常规前端 5173。

### 12.10 历史数据影响评估（真实库 1580 条，2026-11-23）

按 `grading_mode` 分组统计，评估本次视觉改动的数据可得性。结论先行：**没有字段需要回填或迁移**；所有缺口都能落成「诚实降级」，唯二属于产品能力缺口（轮次级判分字段、`tool_calls / evidence` 落库）的，不在本次回填范围。

**(1) 模式分布**

| grading_mode | 执行数 | 有轮次 | 有 judge_result | 有 answer_grading | 有 self_check | turns_used > 1 |
| --- | --- | --- | --- | --- | --- | --- |
| LEGACY | 280 | 278 | 278 | 0 | 50 | 88 |
| ANSWER_CONTRACT | 74 | 74 | 74 | 74 | 47 | 0 |
| RUBRIC | 1226 | 0 | 0 | 0 | 0 | 272 |

状态分布：`PASS 1176 / FAIL 306 / MAX_TURNS_EXCEEDED 65 / REPETITION_ABORT 20 / ERROR 13`。`pass_flag` 1580 条全部非空；`case_snapshot` 1580 条全部存在，其中 `pass_threshold` 可用 LEGACY 251、AC 74、RUBRIC 1226。

**(2) 轮次级字段（472 条 turn_trace）**

| 字段 / 键 | 可得量 | 说明 |
| --- | --- | --- |
| `rule_check` | 472 | 全部非空；`passed` 键仅 LEGACY 398（353 通过 / 45 失败），AC 74 无 `passed` 键；`checks` 为空 214 条 |
| `tool_calls` / `evidence` | 0 | 472/472 均为 JSON `null`（不是空数组），调用链页只能空态 |
| `judge_score` / `dimension_scores` / `fact_results` / `judge_reason` / `contract_*` | 0 | `turn_trace` 表与 `TurnTraceOut` 都没有这些列 / 字段 |
| 有轮次的执行 | 278 LEGACY + 74 AC | RUBRIC 1226 条一条轮次都没有 |

**(3) 缺失字段 → 界面降级**

| 缺口 | 涉及数量 | 界面现状 | 是否回填 |
| --- | --- | --- | --- |
| RUBRIC 无轮次 / 裁判 JSON / 契约 / 自检 | 1226 | 对话轮次空态；判分页不渲染 0 轮矩阵，综合结论说明「旧评分模式只保留执行级综合分与通过结论」；无判定卡入口 | 不回填（旧模式本无此数据） |
| RUBRIC 无 total_score | 11 | 分数显示 — | 不回填 |
| LEGACY 无轮次 / judge_result / total_score | 2 | 全页空态 | 不回填 |
| LEGACY 无 answer_grading | 280 | 契约命中显示「当前为 LEGACY 判分模式，没有契约事实」 | 已降级 |
| 轮次级分数 / 四维 / 事实 / 理由缺失 | 472 轮 | 单轮回落执行级分与四维；多轮无分→未判定；理由用会话级结论或注明未提供 | 不回填（无字段） |
| 多轮无轮级规则（checks 为空） | 214 轮 | 判分矩阵判「未判定」，不臆造通过 | 已降级 |
| 通过线来源缺失 | AC 54/74 无 `judge_result.threshold`；`answer_grading.pass_threshold` 仅 20/74 | 四级回落：`judge_result.threshold` → `answer_grading.pass_threshold` → `case_snapshot.pass_threshold` → 默认 60 | 已降级 |
| 自检缺失或 `turn_index` 缺失 | DONE 11（8 有 idx）/ SKIPPED 86（40 有 idx） | 仅 DONE / ERROR 呈现；多轮无 `turn_index` 不认领到具体轮次 | 已降级 |
| `tool_calls` / `evidence` 未落库 | 472 轮 | 调用链 / 证据页签空态 | 需引擎侧埋点，另立需求 |

**(4) 结论**

- 视觉改动不依赖任何新字段：判定卡、判分矩阵、自检页签全部建立在上述可用字段之上，缺字段时走空态 / 未判定分支，已在 mock 900003–900009 与真实库同类 LEGACY / ANSWER_CONTRACT / RUBRIC 记录上验证。
- **不回填、不迁移**：RUBRIC 1226 条若补造轮次或裁判理由，等于编造历史，违反「每一项展示数据都必须有迹可循」；保留空态更能说明旧记录的真实能力边界。
- 两处产品能力缺口需另立需求：①轮次级判分（`turn_trace` 增加 `judge_score / dimension_scores / judge_reason` 等列并补写入链路）；②`tool_calls / evidence` 生产落库（当前真实执行全为空，演示只能走 mock）。

### 12.11 本轮实测修复（三处缺陷 + 空态收口）

- **单轮四维 note 与卡头口径打架**：`TurnDrawer` 的 `.djv-note` 原固定写「是执行级评分，不是本轮独立得分」，但单轮卡头显示的是「本轮综合分」。现由 `dimsFromExec`（是否回落到执行级四维）分流：轮次级四维 →「与本轮综合分一致。」，执行级回落 → 保留执行级说明。实测 900003 / 900006 / 900009 均为「四维合计 N，与本轮综合分一致。」；多轮 900004 / 900001 为执行级说明。
- **0 轮时 verdict 落到「部分通过」**：`JudgePanel` 的 `verdictLabel / verdictTone` 在 `turns.length === 0` 时走默认分支（多轮 + 非全过 → 部分通过），导致 900008（PASS 89.7）显示琥珀「部分通过」。现 0 轮按 `detail.pass_flag === true ? '通过' : '未通过'` 取值。实测 900008 页头为绿色「通过」。
- **契约事实实际值输出整段 JSON**：`model.ts` 的 `factText` 对 `{mode, numbers, bound_name, bound_value}` 既无 `value` 也无单键，退回 `JSON.stringify`，900006 显示「实际{"mode":"name_mismatch",…}」。现优先取 `bound_value`，再回落 `value` / 单键。实测 900006 事实行显示「实际 999」；真实 #4729 的 `fact_results` 同形，属既有缺陷而非 mock 造错。
- **900008 判分页 0 轮空表格**：0 轮时不再渲染 `.jmatrix`（连同空的「轮次结论 / 门槛 + 维度得分」表头与「判定口径」图例），同时隐藏四张「— / 40 后端未提供」维度卡、`硬门槛 规则 0/0` 流程条、`轮次通过 0/0 · 评分来源 单裁判` 计数胶囊与空的「错误归因」；LLM 综合结论改为说明 RUBRIC 旧模式保留了哪些数据。实测 900008 只剩「通过 89.7 / 100 + 一句综合结论」，无空表 / 空卡 / 空计数、控制台无报错。
- **多轮判定卡不显示四维得分（用户反馈「这个判分没有按照 4 维展示」）**：原 `model.ts` 只在单轮时把执行级四维回落给 `TurnVM.dimensions`，多轮因 `turn_trace` 没有轮次级四维列而 `dimensions` 为空，卡里只剩结论与分数。现改为**不动轮次级字段**：`TraceModel` 新增执行级 `dimensions`（`judge_result` 里真实存在的那一份），`TurnDrawer` 用 `drawerDims = turn.dimensions.length ? turn.dimensions : model.dimensions` 回落展示，并用 `dimsFromExec` 标明这份四维来自执行级。卡内加 `.djv-dims-h` 头「四维得分 + 执行级口径 · 不拆轮次」（有轮次级四维时显示「轮次级口径」），底部 note 继续说明「四维合计 N，是执行级评分，不是本轮独立得分」。裁决返回校验的「合计 = 综合分」改为与 `turn.score ?? model.totalScore` 比对，避免多轮误判为否；该区块仍只在有轮次分或轮次级四维时渲染，多轮不伪造轮次级 schema 校验。原型 `.djv-dims-h` 同步。实测 900004 T1 显示 26/40、22/30、15/20、8/10，合计 71 = 执行级综合分，头与 note 文案正确、卡内无溢出；单轮 900003 / 900005 / 900006 / 900009 仍为「轮次级口径 + 与本轮综合分一致」；多轮 900001 / 900007 同样按执行级口径展示；原型 `?drawer=1&sec=judge` 头与 note 一致。
- 回归：`npm run typecheck`、`npm run build` 通过；1310px 下 900003 / 900006 / 900009 裁决卡宽 814px、抽屉 859px、`docOverflow = 0`、`drOverflow = 0`；985px / 800px 下 900004 裁决卡 814 / 706px、四维 4 条、`cardOverflow = headOverflow = docOverflow = drawerOverflow = 0`；900001 / 900002 保持未通过 58.5 / 通过 92，自检页签仅 900001 出现。

### 12.12 演示 ID 迁移对照（旧 mock → 独立 ID 段）

迁移原因：旧 mock 直接占用真实库同号执行（6580–6588）、同号任务（5061 / 5062），在真实后端 8000 与 mock 8877 之间切换时无法分辨数据来源，且会让人误以为真实库里有这批记录。现全部改为独立 ID 段（任务 `900101` / `900102`、执行 `900001`–`900009`、用例 `900211`–`900227`），真实库同号记录不再被覆盖；mock 上请求旧 ID 返回 404。

| 旧执行 | 新执行 | 新任务 | 场景 |
| --- | --- | --- | --- |
| 6580 | 900001 | 900101 | 多轮失败 + 自检 DONE |
| 6581 | 900002 | 900101 | 多轮通过 + 自检 SKIPPED |
| 6582 | 900003 | 900102 | 单轮分数够但规则硬门槛未过 |
| 6583 | 900004 | 900102 | 多轮全轮未判定 + 执行级四维回落 |
| 6584 | 900005 | 900102 | ANSWER_CONTRACT 全过 |
| 6585 | 900006 | 900102 | ANSWER_CONTRACT facts / safety 未过 |
| 6586 | 900007 | 900102 | 多轮混合结论 |
| 6587 | 900008 | 900102 | 历史 RUBRIC 0 轮 |
| 6588 | 900009 | 900102 | 单轮低于通过线 |

配套改动：`design/mock-trace-server.cjs` 的常量名、`EXECUTIONS` 映射、任务对象与 `/api/v1/tasks/:id` 路由、启动日志均同步；任务编号改为 `TRACE-DEMO-01` / `TRACE-DEMO-02`，任务名统一加「【Trace 演示】」前缀，避免和平台演示任务混淆。

### 12.13 执行级契约事实的判分页口径修正（2026-11-24）

- **问题**：`answer_grading.facts` 没有 `turn_index`，`model.ts` 把这份执行级事实挂在最后一轮（`factsScope = 'execution'`），但 `JudgePanel` 的契约统计只认 `turn.contract`。900006 这类「facts 未过、但 `turn_trace` 没有轮次级 `contract_passed/total`」的执行，判分页出现三处自相矛盾：矩阵契约列显示「—」、流程条写「契约 —」、计数胶囊写「契约未命中 —」，而结论与 `pass_flag` 都是「未通过」，右栏「门槛检查」也只写「未通过」却不列任何未命中项。
- **修正**：判分页统一把执行级 facts 计入契约口径——①矩阵契约列在无 `turn.contract` 但有执行级 facts 时显示 `facts 命中/总数`（hover 注明「按整通执行判定」）；②流程条与「契约未命中」胶囊改用 `execFacts` + 轮次级契约汇总；③「门槛检查」卡新增「执行级答案契约 · facts x/y」分组并逐条列出事实；④门槛未命中列表增加 `执行级 facts` 行，可点击展开该轮。数据仍只来自 `answer_grading.facts`，不新增字段、不回填轮次级契约。
- **演示任务名**：`900102` 的任务名原为「【Trace 演示】判定页分支覆盖（自检关闭）」，会在通过执行 900005 的页头带出「自检」字样，与「通过的用例不得出现任何自检相关内容」冲突；改为「【Trace 演示】判定页分支覆盖 · 契约与历史」，任务描述里的「自检关闭」说明保留。
- **验证**：900006 判分页现在显示「契约 1/2 → 未通过」、胶囊「契约未命中 1 项」、矩阵契约列 `1/2`、门槛检查列出「账单总额」未命中；900005 全过显示 `2/2` 且「已通过」，全页无「自检」；900008（0 轮 RUBRIC）与 LEGACY 执行保持「—」。`npm run typecheck` / `npm run build` 通过。

### 12.14 判分结论与链接兜底（2026-11-24）

- **判分页结论必须跟 `pass_flag`**：`verdictLabel / verdictTone` 原来按「每轮都通过」汇总，多轮执行里中间轮没有轮次级规则时会落「未判定」，于是 900002 / 900007 这种 `pass_flag = true`、`status = PASS` 的执行，页头显示「通过」而判分卡与流程条显示「部分通过」。现统一以 `detail.pass_flag` 为准（缺失时回落「综合分 ≥ 通过线」），「部分通过 / 混合结论」只保留在对话页「轮次结论」卡与判分矩阵的逐轮列里；`硬门槛` 流程条的 `bad` 态、LLM 综合结论、`未通过` 文案同步改口径。
- **页签兜底**：自检页签只在 `self_check` 真发生过时出现。旧链接 `?tab=selfcheck` 指向 900002 这类无自检执行时，原来会出现「没有任何页签高亮 + 显示『未开启自检』空态」，通过用例页还会漏出「自检」字样。现 `visibleTabs` 变化后把不可用的 `activeTab` 收回 `turns` 并同步 URL；详情未返回前不判空，避免把 900001 的合法 `?tab=selfcheck` 误清掉。
- **抽屉兜底**：`?drawer=99` 这类越界轮次原来会打开「T— 本轮没有提问记录」的空抽屉。现详情到达后校验 `turn_index`，不存在则关闭抽屉并清掉 `drawer / sec / text` 参数，合法 drawer 与切执行场景不受影响。
- **任务详情回跳**：mock 补齐 `GET /tasks/:id/metrics`、`/executions`、`/retry-candidates`，从执行页面包屑进入任务详情不再 404。指标只从执行记录派生（进度 / 通过率 / 均分 / P95 / tokens / 状态分布），`900101` 的用例数由 5 校正为实际的 2 条（900001 / 900002），与执行列表一致。
- **验证**：900001 / 900002 / 900007 / 900004 的判分卡分别为「未通过 / 通过 / 通过 / 未判定」，与页头状态、流程条一致；`?tab=selfcheck` 在 900001 正常打开、900002 自动落回对话轮次且全页无「自检」；`?drawer=99` 关闭抽屉，`?drawer=2` 与 `drawer=1&text=sql` 正常；任务 900101 / 900102 详情分别显示 2 / 7 条用例，行内「查看结果」可回到对应执行。

### 12.15 判分与抽屉口径复核（2026-11-24）

- **会话级归因不伪造轮次**：`judge_result.error_tags` 是整通执行的归因，原 `JudgePanel.aggregateTags` 把它复制到每一轮再按轮累计，界面出现 `×2`、`T1`，等于声称该归因在每轮各命中一次。现 `TagAgg` 增加 `perTurn`：轮次级标签保留次数与最早轮次，会话级只去重、不累计，渲染为不可点击的 `span` 并附 `.etag-note`「未拆分到轮次」。900001 的「数值错误/数据幻觉、口径遗漏」与 900004 的「未主动反问」只出现在会话级行，裁决记录里的 `错误归因 2 条` 也标注「会话级，不归属本轮」。
- **执行级结论跟 `pass_flag`**：`verdictLabel / verdictTone` 去掉 `allUnknown` 覆盖，统一以 `detail.pass_flag` 为准（缺失时回落「总分 ≥ 通过线」）。900004（3 轮全未判定、执行记录 FAIL）原判分页显示「未判定」，现为「未通过 71.0」，与页头「失败」、`pass_flag = false` 一致；会话级结论文案补「整通执行按记录判『通过 / 未通过』」。
- **轮次分布胶囊**：判定页在「轮次通过 N/M」后补「部分命中 N」「未通过 N」胶囊（`.warnchip` / `.failchip`），未判定既有中性胶囊。900001 显示 0/3 通过 · 未通过 1 · 未判定 2；900003 / 900006 显示部分命中 1；900004 显示未判定 3。
- **矩阵汇总行按真实口径**：原「均分 / 执行级」只看 `allUnknown`，900001 有 1 轮判定但没有任何轮次级四维，汇总行仍标「均分」却填执行级四维。现由 `perTurnDims`（每轮都有 4 维）决定：有则「均分」，否则「执行级」；900001 与多轮 900002 / 900007 的汇总行均改标「执行级」，与单元格来源一致。
- **单轮「轮次结论」保留部分命中**：`CaseTraceInspector.passHeadline` 单轮非通过时原固定「未通过」，而 900003 / 900006 的轮次实际是「部分命中」（达线但硬门槛未过）。现单轮按 `turns[0].verdict === 'warn'` 显示「部分命中」；KPI 副标题按 通过 / 部分命中 / 未通过 / 未判定 四态分布，全轮未判定显示「N 轮均未判定 · 无轮次级分数与门槛」。
- **执行级契约闸门进入门槛检查**：此前只把执行级 facts 归入契约口径，`gates.safety = false`（900006）在门槛检查与「未通过门槛」列表里看不到。现补「执行级契约闸门」分组与 `安全闸门` 未通过行，与 `pass_flag` 的失败原因对齐；900006 未通过门槛由 1 项变为 2 项。
- **抽屉判定卡口径**：多轮无轮次级分数时，标题分数标「执行级综合分」，meta 由「本轮无独立分 · 数字为执行级汇总」改为「pass 仅依据硬门槛」；「本轮」段的判定说明改为「多轮执行未落库轮次级分数，本轮 pass 按已落库的硬门槛判定为『通过 / 未通过』，不参与分数比较」，不再声称「综合分 ≥ 阈值，本轮满足」。契约模式 meta 补「契约门槛 x/y · 全过 / 未全过 + 质量分阈值（仅参考）」。
- **规则命中证据**：`must_contain` 类规则通过时后端常只落 `evidence`、`actual = null`，原界面在「实际」后显示「—」，看起来像没命中。现改为「命中片段」并展示 `evidence`；既无 actual 也无 evidence 时显示「已命中（后端未记录取值）」；空 `checks` 文案改为「没有配置规则检查项（`rule_check.checks` 为空）」，不再把「没配规则」说成「rule_check 为空」。
- **最低轮次不再兜底到 T1**：`session.lowest` 原来在没有轮次级分数时返回第一轮，900001 因此显示「最低轮次 T1」（T1 实为未判定）。现只从有分数的轮次里取最低，否则显示「—」，段末注明「未落库轮次级分数，轮次高低无法比较」；900009 单轮 48 仍正常显示「T1 · 48」。
- **单轮未跑自检却标「已触发」**：`TurnVM.selfCheck` 原为 `single || turn_index 命中`，900009 这类单轮失败但 `self_check = null` 的执行，追问页签标题显示「已触发」而正文是「本次执行没有追问自检记录」。现要求自检记录存在才认领，900009 显示「未触发 / 未记录」，通过用例仍不出现任何自检入口。
- **验证**：逐段复测 900001 T1–T3、900002 T1–T2、900003、900004 T1、900005、900006、900007 T1–T2、900009：900004 判分页「未通过 71.0」、900006 门槛检查列出 `facts 1/2` + 安全闸门、900002 T2 判定卡「通过 92 执行级综合分 · pass 仅依据硬门槛」、900009 追问自检「未触发」、900001 最低轮次「—」、900003 / 900006 轮次结论「部分命中」。`npm run typecheck` / `npm run build` 通过；807×786 下 900001 / 900002 / 900009 抽屉 `drOverflow = 0`、`docOverflow = 0`。

### 12.16 归因作用域与分数口径收口（2026-11-24）

- **会话级归因在所有入口都不可点**：§12.15 只改了结论区与抽屉的会话级行，`TurnDrawer` 的「会话级判定上下文」还保留「会话级归因 → T4 判定」按钮，`JudgePanel` 的矩阵展开区、`TurnDrawer` 判定段的会话级标签也仍是 `button`，点开就把整通会话的归因挂到某一轮。现三处全部改为静态 `span` + `.etag-note`「未拆分到轮次」，抽屉的会话级上下文改用去重后的 `sessionTagsAll` 静态行；轮次级标签仍可点。
- **「最低项」只列轮次级归因**：`JudgePanel` 的 LLM 综合结论把 `low.tags.concat(low.sessionTags)` 拼进最低轮次说明，等于说该轮命中了会话级归因。现只拼 `low.tags`；原型 `case-turn-flow-final.html` 同步。多轮无轮次级归因时只显示轮次与分数。
- **抽屉分数标签按模式**：契约模式抽屉判定卡原写「本轮综合分 94」，实际是 `judge_result.quality.total_score`（质量分参考，执行级 `total_score` 为契约分 100/70）。现标「质量分参考」；会话级上下文块的「执行级综合分」同改。LEGACY 仍为「本轮综合分 / 执行级综合分」。
- **分数达标但未通过的说明**：900003 这类「82 ≥ 60 但规则 1/2 未过」的执行，抽屉 meta 只写「阈值 60」会让人误以为阈值判断失败。现补「分数已达标 · 硬门槛未全过」；契约模式不显示该提示（质量分仅参考，由契约门槛决定）。
- **验证**：900001 T1/T3、900002 T2、900003、900004 T1、900005、900006 抽屉与矩阵展开区均无会话级 `button`；900003 显示「分数已达标 · 硬门槛未全过」；900005 / 900006 显示「质量分参考 94」，900006 不再出现分数达标提示。`npm run typecheck` / `npm run build` 通过；`/trace?execution=14875`（真实 MAX_TURNS 执行）会话级上下文显示「未通过（执行级）」、会话级标签静态，18 个 mock 页面无 `undefined` / `NaN`、无横向溢出。

### 12.17 轮次结论与零轮/空态口径（2026-11-24）

- **多轮 0 轮通过不再说「部分通过」**：`passHeadline` 原对多轮非全过一律返回「部分通过」，900001（0 通过 / 1 未通过 / 2 未判定）与真实 `14875` 都被说成「部分通过」，与页头「失败」矛盾。现 0 通过且存在未通过轮次 → 「未通过」，0 通过且全部为部分命中 → 「部分命中」，有任一轮通过才叫「部分通过」。原型 `syncHeader`、会话级上下文「判定结论」与综合结论句同步。
- **轮次结论着色跟结论走**：KPI 图标原只分「全过绿 / 其他琥珀」，900001 的「未通过」仍显示琥珀告警。现通过绿 + `check`、部分通过/部分命中琥珀 + `alert`、未通过玫红 + `x`、未判定灰 + `info`；原型补 `.stat .si.rose` 并同步 `syncHeader`。
- **零轮执行的页头**：RUBRIC 历史执行（900008）有 `turns_used` 但没有 `turn_trace`，页头原显示「轮次结论 — / 0 轮」「0 轮通过」「会话一致性 PERSIST 各轮会话一致」。现改为「—」（不再拼 `/ 0 轮`）+「执行记录只保留执行级结论，没有逐轮明细」、会话一致性「— / 无逐轮记录，无法判断」，并隐藏 0 轮时的筛选条与「全部 0」计数；对话页空态按模式说明「旧评分模式（RUBRIC）只保留执行级分数与结论」。
- **原始报文页空态**：0 轮执行不再写「所有轮次的 `tool_calls` 都为空」与「原始报文按轮次留存」，改为「本次执行没有逐轮记录，页面不补造调用步骤」与「旧评分模式（RUBRIC）没有留存原始报文」。
- **单轮四维卡片**：判分页四维卡在单轮时原写「最低 T1 · 22/40」，既与原型「占满分 55%」不一致，也只是重复卡片上的分数；现按原型显示占满分比例，多轮仍显示最低轮次。
- **追问自检页签**：`visibleTabs` 原只在 `self_check` 存在时显示页签，于是 900003 / 900004 / 900006 / 900009 这类失败执行没有入口，而抽屉「派生与上下文」还写着「未记录」，两处口径不一致。现失败执行保留页签，无记录时渲染既有空态「本次执行没有 self_check 记录（任务未开启或本次未触发）；页面不补造自检内容」；通过用例仍完全不出现自检页签与字样（900002 / 900005 / 900007 / 900008 保持 4 个页签）。
- **验证**：900001 轮次结论「未通过」玫红叉号图标；900003 / 900004 / 900006 / 900009 五页签 + 空态、`?tab=selfcheck` 不再回落；900002 / 900005 / 900008 四页签且全页「自检」0 次；900008 页头与原始报文页口径一致；900009 四维显示「占满分」；原型 8899 控制台无报错、无横向溢出。

### 12.18 失败但裁决通过的自检、无分数异常执行与单轮结论（2026-11-24）

- **FAIL + `self_check.status=SKIPPED` 不再说「没有记录」**：生产上 LEGACY 的 `judge_result.pass` 为 true、规则硬校验未过时，`ExecutionRecord.pass_flag` 被合成为 false，但 `self_check_trigger_reason` 只看 LLM 裁决（`self_check.py:90-108`）→ 落 `SKIPPED` + `skip_reason="LLM 裁决通过，未触发追问自检"`；契约模式也会在契约通过但 `MAX_TURNS_EXCEEDED` / `REPETITION_ABORT` 时出现同样形态。原 `model.selfCheck` 只在 `DONE` / `ERROR` 时非空，页面统一说「没有 self_check 记录 / self_check 为空」，与落库记录矛盾。现新增 `model.selfCheckSkipReason`：失败执行渲染「未触发追问自检 · SKIPPED」+ 后端 `skip_reason`；通过用例仍完全不展示自检（任务开启时的 `SKIPPED` 属于正常静默形态）。
- **无轮次且无分数的异常执行不判「未通过」**：`_mark_error` / `_mark_aborted` 会写 `pass_flag=false`，但从未产出裁判结论。真实 `14899`（LEGACY，`turns_used=0`）与 `15525`（RUBRIC，`turns_used=2`）都是 `total_score=null`，原页头写「执行记录只保留执行级结论」、判分页写「旧评分模式只保留执行级综合分 — 与通过结论」，等于把系统写的 false 当成裁判结论。现统一为「未判定」裁决；有分数但零轮的 RUBRIC（900008）保留「只保留执行级分数与结论」话术。
- **单轮「部分命中」的结论句与矩阵一致**：900003 / 900006 的轮次 `verdict=warn`（总分达线、硬门槛未全过），矩阵行与页头都写「部分命中」，但 `JudgePanel` 的单轮结论仍按非通过一律写「本轮未通过」。现按 `verdict` 输出「本轮通过 / 本轮部分命中 / 本轮未通过 / 本轮未判定」；`warn` 补「达线但硬门槛未全过」，`unknown` 不再拼「总分 —」。原型 `case-turn-flow-final.html` 的 `concl` 同步（`selWarn` 分支）。
- **mock 的 `judge_result.pass` 只保留 LLM 裁决**：`legacyAggregate` 原用 `score >= threshold && policyNotes.length === 0`，把规则否决写进了 `judge_result.pass`，与 `executor.py:507-513` 只在 `pass_flag` 上合成不一致。现改为 `pass: score >= threshold`；900003 的原始报文因此是 `pass_flag=false` + `judge_result.pass=true`，正是「82 分、LLM 判过、规则未过」的真实形态，也解释了它若开启自检为何会落 `SKIPPED`。
- **真实库存在 FAIL + SKIPPED**：340 条 `pass_flag=false` 中恰有 1 条 `self_check.status='SKIPPED'`（`14898`，LEGACY 单轮 91 分、规则 1/2 未过，`skip_reason="LLM 裁决通过，未触发追问自检"`），另有 2 条 `DONE`、其余 `null`；本文早期「未找到样本」的结论已按全量复核订正。演示 900001–900009 保持既有形态（900001 DONE、900002 SKIPPED 通过态、其余 `null`），该形态生产可达（LEGACY 规则否决、契约 `MAX_TURNS_EXCEEDED` / `REPETITION_ABORT`），`14898` 已实测渲染为「未触发追问自检 · SKIPPED」+ 后端原因。
- **验证**：临时把 900003 的 `self_check` 置为 `SKIPPED`（验证后已还原）→ 追问自检页签显示「执行记录：LLM 裁决通过，未触发追问自检（self_check = SKIPPED）」，抽屉「派生与上下文」显示「未触发 · SKIPPED」；恢复后 900003 仍为 `null`；真实 `14898` 同样渲染「未触发追问自检 · SKIPPED」+ 后端 `skip_reason`。真实 `14899` / `15525` 判分页均显示「未判定」且无「只保留执行级分数」误述；900003 判分页结论改为「本轮部分命中：总分 82.0 / 100；达线但硬门槛未全过…」，900006 同步；900005 / 900002 / 900007 全页「自检」仍为 0 次。`npm run typecheck` 通过；9 执行 × 5 页签复测无 `undefined` / `NaN` / `[object Object]`、无横向溢出、无控制台报错；`?drawer=99` 越界自动关闭并清理 URL，`?drawer=0/-2` 不打开抽屉。

### 12.19 `turns=[]` 不等于 0 轮、RUBRIC 判分口径与无裁判记录时的标签（2026-11-24）

- **`turns=[]` 不代表没跑过轮次**：执行表同时落 `turns_used` 与 `turn_trace`；错误 / 终止的记录可能 `turns=[]` 而 `turns_used>0`（真实 `15525` 走了 2 轮，`raw_request_response=[]`）。原对话页空态与判分页结论一律写「本次执行没有产生对话轮次」「没有对话轮次」，与 `turns_used` 矛盾。现按 `turns_used` 分档：`0` → 保持「本次执行没有产生对话轮次，也没有执行级评分与结论」；`>0` → 标题「没有逐轮明细」+「本次执行共 N 轮，但没有留存逐轮明细，也没有执行级评分与结论」。有分数的 RUBRIC（`900008` / 真实 `16152`）写「本次执行共 N 轮，旧评分模式（RUBRIC）只保留执行级综合分 X 与最终结论，没有逐轮明细」。措辞用「最终结论」而不是「通过结论」，避免失败记录被读成「有一条通过的结论」。
- **执行快照的「判分口径」补 RUBRIC 分支**：原三目只分 `ANSWER_CONTRACT` / 其余，`grading_mode=RUBRIC`（11-24 复核 1214 条）被写进「LEGACY 模式：规则 + LLM 裁判四维加权…」，但这些记录没有裁判四维、没有 `judge_result`。现单独写「旧评分模式（RUBRIC）：规则 + LLM 评分，落库时只保留执行级综合分与最终结论，没有逐轮四维与裁判理由」；LEGACY / 契约文案不变。
- **原始报文页的空态同样按 `turns_used` 分档**：该页原写死「本次执行没有逐轮记录，页面不补造调用步骤」，`15525`（2 轮）/ `16143`（1 轮）/ `900008` 都被读成没跑过轮次。现分三档：有 `turn_trace` → 保留「所有轮次的 `tool_calls` 都为空」；`turns_used>0` 但无明细 →「本次执行共 N 轮，但没有留存逐轮调用记录，页面不补造调用步骤」；`turns_used=0` →「本次执行没有产生对话轮次，页面不补造调用步骤」。
- **页头综合分标签跟 `grading_mode` 与 `judge_result` 走**：原非契约模式一律标「裁判综合分 / LLM 裁判综合评分」，但 RUBRIC 只落 `total_score`、异常执行可能根本没有 `judge_result`（真实 `14899`）。现分三档：契约 →「排序分 / 质量分参考」；RUBRIC →「综合评分 / 旧评分模式（RUBRIC）执行级综合分」；LEGACY 且有 `judge_result` →「裁判综合分 / LLM 裁判综合评分」；LEGACY 但没有 `judge_result` →「综合评分 / 执行记录未留存裁判评分」。
- **没有裁判 JSON 时不再把界面结论标成「LLM 综合结论」**：判分页结论块的标题原写死「LLM 综合结论」，而 `14899` / `15525` / RUBRIC 记录的结论句是本页根据 `turns_used` / `total_score` / `pass_flag` 生成的说明，并非裁判产出。现 `judge_result` 存在才写「LLM 综合结论」，否则写「综合结论」。
- **多轮全未判定的执行级分数措辞**：900004 原文「执行级综合分 71.0 / 100 仅作参考，整通执行按记录判「未通过」」。71.0 就是执行级权威分，「仅作参考」容易被读成分数不可信；改为「只代表整通执行，整通执行按记录判「未通过」」，既说明不拆分到轮次，也不否定执行级结论。
- **RUBRIC 总分高于快照阈值但判未通过时说明差异**：真实库 1203 条有分数的 RUBRIC 中有 137 条 `pass_flag=false` 且 `total_score ≥ case_snapshot.pass_threshold`（如 `16143`：64.1 / 阈值 60，`termination_status=GIVE_UP`），而快照页直接展示「pass 阈值 60」，判分页只写「未通过 64.1」会让人以为前后矛盾。现仅在该形态追加一句：「落库总分不低于该执行快照的阈值 X，但历史记录没有保存当时的判定依据（规则 / 终止原因），页面按落库结论记为「未通过」，不反推原因」。阈值来源不是用例快照（前端默认兜底或执行口径）时不输出该句。
- **数据体量复核**：§12.10 的 11-23 快照为 1580 条（LEGACY 280 / 契约 74 / RUBRIC 1226），11-24 全量复核为 1336 条（LEGACY 68 / 契约 54 / RUBRIC 1214），说明库内记录在两次审计之间发生了变化；后续引用体量以复核日期为准。形态结论不受影响，关键样本 `14898` / `14899` / `15525` / `16143` 均仍在库。
- **验证**：真实 `15525` 对话页显示「没有逐轮明细 / 本次执行共 2 轮…」，判分页标题「综合结论」、页头「综合评分 / 旧评分模式（RUBRIC）执行级综合分」；真实 `14899` 仍显示「没有对话轮次」（`turns_used=0`）、页头「执行记录未留存裁判评分」；真实 `16152`（RUBRIC PASS，`turns_used=1`）与 mock `900008` 显示「共 1 轮，旧评分模式…」；真实 `16143` / `16120`（RUBRIC FAIL 且总分 ≥ 阈值）显示差异说明，`16152`（PASS）不显示；`15525` / `900008` 快照页判分口径为 RUBRIC 文案，`900001`（LEGACY）与 `900005`（契约）文案不变；`900004` 结论改为「只代表整通执行…」；原始报文页 `15525` / `16143` / `900008` 显示「共 N 轮，但没有留存逐轮调用记录」，`14899` 显示「没有产生对话轮次」。`npm run typecheck` / `npm run build` 通过；9 执行 × 5 页签无 `undefined` / `NaN` / `[object Object]`、无横向溢出、控制台无报错。

### 12.20 契约模式 pass 只看闸门；没有执行级总分时的结论兜底（2026-11-24）

- **契约模式的 pass 口径被界面写成了「闸门 + 分数」**：`answer_grader.grade_answer` 里 `passed = all(gates.values())`，`executor` 只对 `MAX_TURNS_EXCEEDED` / `REPETITION_ABORT` 强制置 false，质量分 `judge_result.quality` 明确「仅作质量参考，不参与 pass」。但判分页的门槛图例、矩阵表头、`判定口径` 卡、抽屉「本轮结果」与「流程裁决 · 校验」仍写「pass = 硬门槛全过 且 total_score ≥ 阈值」，文案还把契约质量分叫作「契约分」。真实库 54 条契约执行中有 `14917` / `14858` 两条反例：质量分 94 ≥ 存储阈值 60，`pass_flag=false`，`gates.facts=false`、`gates.safety=false`。现按模式分支：契约 →「pass = 安全 / 行为 / 事实 / 附加 四项契约闸门全过；质量分仅参考，不参与 pass」，LEGACY / RUBRIC 保持原式；`execPassed` 在 `pass_flag` 缺失时也只按契约闸门推断，不再拿质量分与阈值兜底。
- **契约执行的分数量词跟口径走**：同一个 94 在页头标「排序分 / 质量分参考」，在单轮结论句里却写成「总分 94 / 100」，容易被读成契约排序分。现契约模式的单轮结论写「质量分 94.0 / 100」，未通过 / 部分命中时补「质量分不参与契约模式的 pass，执行级判定为『未通过』（契约闸门未全过或终止状态否决）」，不再用「达线」描述质量分。
- **页头「排序分」标签与数值不符**：页头大数字取 `judge_result.quality.total_score`（94），标签却写「排序分」；真正参与任务列表排序的是 `ExecutionRecord.total_score`（契约评分卡，mock `900005` 为 100、`900006` 为 70，真实 `14917` / `14858` 均为 70）。现标签改为「质量分参考」，hint 写「仅参考 · 排序分 70.0 · 四项闸门决定 pass」（质量分缺失时才退回「契约排序分 / 评分卡总分」），big number 与标签不再互相矛盾。
- **同一页内的「总分」列名统一跟模式走**：判分流程条、矩阵总分列、判定口径卡的总分行、快照页阈值胶囊、抽屉会话级「总分 / pass 阈值」都把契约质量分叫「总分 / pass 阈值」。现契约模式分别改为「质量分（参考）」「质量分」「质量分参考线」，矩阵列头补 title「仅作参考，不参与 pass」；LEGACY / RUBRIC 文案不变。抽屉里键名叫「判定口径」但值是执行级结论（通过 / 未通过），一并改为「执行级结论」。
- **未通过门槛提示按模式**：契约模式写「契约闸门未全过，质量分再高也判『未通过』」，不再沿用「硬门槛不过，总分再高……」。
- **没有执行级总分的结论不再拼 `— / 100`**：`overall` 在轮次无分且 `total_score` 为空时返回 null（异常 / 裁判未产出），原判定卡头仍渲染「— / 100」，单轮结论、全未判定结论与多轮结论还会拼出「总分 — / 100」「执行级综合分 — / 100」。真实库当前没有该形态，但字段允许。现分数位在 null 时只写「执行记录未留存执行级综合分」，卡头与流程条不渲染 `/ 100`，复制结论写「执行记录未留存轮次级分数」；抽屉裁决卡在 `turn.score` 与 `model.totalScore` 都为空时也不再显示 `/ 100`。
- **验证**：mock `900005` / `900006` 的图例与判定口径为契约文案（页头「质量分参考 / 仅参考 · 排序分 100.0 / 70.0 · 四项闸门决定 pass」，流程条「质量分 94.0 / 100（参考）→ 通过 / 未通过」，矩阵列头「质量分」，快照「质量分参考线 60（用例快照）」，抽屉「执行级结论 / 质量分合计 / 质量分参考线」），`900001` / `900003` / `900009` 仍为「硬门槛全过 且 total_score ≥ 阈值」「总分 / pass 阈值」；真实 `14917` / `14858` 结论显示「质量分 94.0 / 100；质量分不参与契约模式的 pass……」、页头排序分 70.0、判定仍为未通过；`900006` 抽屉「本轮结果」与流程裁决校验均为契约口径；9 执行 × 5 页签无 `undefined` / `NaN` / `[object Object]`、无横向溢出、控制台无报错，通过用例（900002 / 900005 / 900007）全页「自检」0 次；`npm run typecheck` / `npm run build` 通过。

### 12.21 契约主分不借评分卡总分、抽屉校验不再把「没分」写成 false（2026-11-24）

- **契约质量分缺失时不能拿评分卡总分充数**：`model.ts` 的 `totalScoreOf` 契约分支原本在 `judge_result.quality.total_score` 缺失时回退 `answer_grading.total_score`。后者是契约评分卡总分（`executor.py` 的 `base.total_score = grading.total_score`，mock `900005`=100 / `900006`=70，任务列表按它排序），把它渲染成「质量分」会张冠李戴；质量分裁判不可用时 `executor` 会落一个只有 `error` 的 `quality`，正好走到这个回退。现契约分支只认 `quality.total_score`，缺失时显示「未留存质量分」；页头仍按 `scoreKpi` 的「契约排序分」分支展示评分卡总分，两处标签与数值各自自洽。当前库 54 条契约执行全部有质量分，此改动只影响理论形态。
- **「按分数 pass」在没有轮次分时被写成 false**：抽屉「裁决返回校验」与「流程裁决 · 校验」都渲染 `turn.score !== null && turn.score >= passLine`，多轮执行没有轮次级分数时直接输出 `false`，读起来像「按分数判未通过」。现分数缺失时显示「未提供轮次分」，并补一句「本轮没有轮次级综合分，pass 只按已落库的硬门槛判定」，不再暗示分数比较发生过。
- **契约模式的「裁决返回校验」仍按 LEGACY 口径列项**：原固定展示「按分数 pass / 按硬门槛 pass」，契约执行没有规则硬校验，`gateOk` 会因规则为空而恒为 true。现契约分支改为「质量分达参考线（仅参考，不参与 pass）」与「按契约闸门 pass（取 `pass_flag`，执行级判定，不由质量分推出）」。
- **裁判输入描述与实际 Prompt 变量不符**：抽屉写「用户问题 + 候选回答 + 规则结果 + 期望答案」，但 `build_judge_variables` 只拼 `chat_history`（用户问题 + Agent 最终回答）、`raw_db_data`、`expected_answer`、轮次与终止状态，没有「规则结果」。现改为「用户问题 + 候选回答 + 白盒取数明细 + 期望答案」；契约模式补「契约闸门由后端规则计算，不交给模型判断；模型只给质量分」，说明闸门与质量分是两条链路。
- **契约模式下残留的「硬门槛 / 综合分」措辞**：未判定 tooltip、抽屉「本轮无独立分 · pass 仅依据硬门槛」、四维合计说明、派生轮次说明、「返回」行与单轮最低项都沿用 LEGACY 词。现契约模式分别改为「缺少轮次级质量分与契约事实」「pass 仅依据契约闸门」「本轮质量分」「执行级质量分」，多轮最低项也写「质量分」；LEGACY / RUBRIC 文案不变。
- **页头 KPI 在没有分时仍拼 `/ 100`**：`judgeScore` 为空时大数字显示「—」，但后面固定挂着「/ 100」，而且 RUBRIC 分支的 hint 仍写「旧评分模式（RUBRIC）执行级综合分」。真实 `15525`（RUBRIC ERROR，无 `total_score`）会同时出现「— / 100」和「执行级综合分」。现 `/ 100` 只在有分时渲染；RUBRIC 无分写「旧评分模式（RUBRIC）；本次执行未留存综合分」，有裁判记录但无分写「有裁判记录，但本次执行未留存评分」。
- **「复制本轮结论」复制出去的文字没有跟着模式走**：`conclusionText` 固定写「得分：…」「硬门槛：规则 0/0」，契约执行的复制结果会把质量分叫「得分」、把契约事实漏在「硬门槛」里，且没有契约事实时只写「规则 0/0」。现契约模式输出「质量分：94 / 100（仅参考，不参与 pass）」「闸门：规则 0/0 · 契约 未记录」，无轮次分写「未留存轮次级质量分（pass 只看闸门）」；LEGACY / RUBRIC 仍为「得分 / 硬门槛」。
- **判分页主分与页头大数字共用一个 `judgeScore`**：契约分支在质量分缺失时回退 `detail.total_score`（评分卡总分），而 `JudgePanel` 的 `:score` 就是它 —— 排序分会被判分页当成主分展示；页头此时标签已是「契约排序分」，同一数字两处两个名字。现 `judgeScore` 契约分支只返回质量分或 `null`，页头另设 `headerScore`（标签为「契约排序分」时展示 `detail.total_score`）；判分页在质量分缺失时主分为「—」、结论写「执行记录未留存质量分」。另补契约模式的无轮次明细文案「执行记录只有契约质量分与执行级结论」。
- **验证**：临时把两处质量分判定强制为缺失后，mock `900006` 页头显示「契约排序分 70.0 / 100 · 评分卡总分；本次执行未留存质量分」，判分页主分「—」、结论「执行记录未留存质量分，结论按落库的判定结果展示」，轮次结论按闸门记为「未通过」；恢复后 `900005` / `900006` / 真实 `14917` 仍为「质量分参考 94.0 / 100 · 排序分 100.0 / 70.0」，`15525` / `14899` 页头仍无 `/ 100`、`14875` 仍为「未判定 / 3 轮」。当前库 54 条契约执行全部有质量分，该回退形态暂无真实样本。
- **验证**：mock `900006` 抽屉「本轮结果」显示「契约事实 1 / 2」「质量分 94 / 100」，校验行为「质量分达参考线 true（仅参考）/ 按契约闸门 pass false（执行级判定）」，证据页「返回 pass = false · 质量分 94」与「输入 … 白盒取数明细 …」均正确；`900002` 抽屉校验行显示「按分数 pass 未提供轮次分 / 按硬门槛 pass true」并补「pass 只按已落库的硬门槛判定」；`900003` 判定口径与结论仍为 LEGACY 文案；真实 `14917` / `14858` 页头仍为「质量分参考 / 排序分 70.0」、结论仍为「质量分 94.0 / 100；质量分不参与契约模式的 pass……」，`16143` RUBRIC 差异说明不变，`14875` / `15525` 多轮与无明细文案不变，`15525` / `14899` 页头不再出现「— / 100」；9 执行 × 5 页签无 `undefined` / `NaN` / `[object Object]`、无横向溢出、控制台无报错；`npm run typecheck` 通过。

### 12.22 调用链与快照不补结构、自检与对照只写落库字段、抽屉口径收紧（2026-11-24）

- **调用链时延缺失被补成 0ms**：`stepsOf` 原用 `num(...) ?? 0` 兜底，`TurnStepVM.ms` 类型是 `number`，于是「后端没记时延」会被渲染成「0ms，很快」，`ChainList` 的总耗时 / 慢步骤筛选 / 阶段条、页头「调用链总耗时 / 占比 / 最慢一步」、工具均分、抽屉「合计」全部按 0 计算。现 `ms: number | null`，缺失显示「—」：总耗时只在有值时相加，工具均分按有值步骤数计算（全无时 `avg=null` → 「—」），慢步骤筛选取 `-1` 参与排序但不显示假值；阶段条只有在整条链路每一步都落了时延（`fullyTimed` / `chainFullyTimed`）时才按时间轴着色，否则整轮按步数比例并注明「部分步骤未记录时延，不按 0ms 计」，避免未记录阶段被画成 0ms。真实库 `turn_trace.latency_ms` / `ttft_ms` 无 NULL，但字段允许缺失（模拟器 / 异常链路），页面不能再自造数值。
- **调用链步骤补造 arguments**：`stepArgs` / `stepResult` 在 `arguments` / `result` 缺失时拼了 `{ tool, step, input, step_index }` 和 `raw`，让人以为后端真有这些字段。现只渲染真实落库的 `arguments` / `result`；任一缺失时改展示「原始条目」并标注「后端未拆分 arguments / result」，展示层不替执行记录补结构。
- **快照 `turns` 不是数组时当轮次渲染**：`case_snapshot` 是历史自由 JSON，真实库 1326 条 `turns` 为 `null`，也可能被写成标量；原 `snapshot.value?.turns ?? []` 只挡住缺省。现只接受数组。`expected_answer` 为空字符串时，逐轮期望也原会以「—」占一行并计入轮数；现 `snapshotTurnExpects` 只统计真正有值的轮次，并说明「快照共 N 轮，其中 M 轮有逐轮期望，其余为空」。
- **规则型期望被说成「没写期望」**：真实库 1409 条快照中 `expected_answer` 有 1319 条非空，但 `turns[].expected_answer` 0 条、`expected_rules` 仅 689 条非空。快照页空态原写「快照未保存期望答案」，对照模式原写「补上期望答案后即可比对」，把以规则表达期望的用例读成没写期望。现快照页在有 `expected_rules`（执行级或轮次级，且非空对象）时写「快照没有文本期望答案……本用例的期望以 expected_rules 规则表达，见下方原始快照 JSON」；对照模式写「无文本期望答案」，有规则时补「期望若以 expected_rules 表达，请以该轮的规则命中为准」。
- **快照页缺快照时仍渲染字段**：原 `snapshot` 为空仍进入正文，卡片标题与「用例当前版本 v…」照常显示。现补「没有执行快照」空态（`case_snapshot` 未保存，不补造请求变量、期望答案与判分口径）；有快照时版本文案改为「用例版本 v{{ case_version }}（执行时）」，避免与用例当前版本混淆。
- **快照「判分口径」chip 把执行结果写成用例配置**：规则结果为空时原写「规则结果 0 条」，会被读成用例没配规则；事实 / 闸门为空同样没有说明。现按模式分档：契约「规则结果未参与契约判定」、RUBRIC「规则结果未记录（RUBRIC）」、LEGACY 有快照规则写「规则结果未留存」、确实没配才写「未配置规则」；事实 / 闸门为空写「契约事实未留存 / 契约闸门未留存」。
- **契约模式的阈值被叫成「质量分参考线」**：`executor.py:593` 的 pass 是 `grading.passed`（四项闸门），`threshold` 只随执行落库，质量分 94 从未与 60 比较。快照 chip 与抽屉仍写「质量分参考线 60」，暗示参考线参与判定。现统一写「阈值 60（执行口径）· 不参与契约判定」，抽屉写「阈值（不参与契约判定）」；LEGACY / RUBRIC 的 chip 仍为「pass 阈值 / 质量分参考线」原口径。
- **`rulesOf` 默认「没有 passed 就算通过」**：原 `pass: item.passed !== false`，字段缺失时页面会替后端判通过。真实库 146 条 `rule_result` 全部带 `passed`，但缺失形态下不能默认成功。现 `pass = typeof passed === 'boolean' ? passed : false`，缺字段时 `why` 写「后端未记录 passed，页面按未通过展示」。
- **请求地址硬写 POST**：`requestOf.method` 原 `|| 'POST'`，抽屉「请求地址」行因此在 `request_payload.method` 缺失时仍显示 POST。现去掉默认值，method 为空时只显示 URL；行文案按 `turn.request.method ? \`${method} \` : ''` 拼接，不再冒充落库事实。
- **期望答案按位置兜底会张冠李戴**：`expectedOf` 原 `turns.find(index) ?? turns[index - 1]`，`index` 存在但对不上时会把别的轮次期望挂到本轮。真实快照 `turns[]` 182 个元素 `index` 100% 存在，故只在结构里确实没有 `index` 字段时才按位置兜底，且在来源标「（按位置匹配）」。
- **取数只展示 8 项时没有说明还有多少**：对话页 `dataRows` 直接 `slice(0, 8)`，读起来像全部取数。现保留前 8 项并补「另有 N 项 · 见「证据」」，样式为虚线条目。
- **自检触发原因被补写成「LLM 裁决不通过」**：`SelfCheckPanel` 原 `record.trigger || 'LLM 裁决不通过'`，`SelfCheckBlock` 原 `triggerIndex ? \`T${index} 未通过\` : '本次执行判失败'`，契约执行的自检会被误标成 LLM 裁决。现优先用后端 `trigger`；缺失时写「后端未记录触发原因（self_check.trigger 为空）」，触发轮缺失时写「评测器在同一会话…；执行记录未保存触发轮次」/「由本次执行的失败判定自动触发（触发轮未记录）」。
- **自检触发轮字段被说成「后端没有」**：抽屉原写「后端只保存了触发原因，没有触发轮次字段」，但 `self_check.turn_index` 真实存在（`self_check.py:312-325`）。现改为「执行记录保存了这条自检，但没有触发轮次（self_check.turn_index 为空）」「self_check 未保存触发轮次（turn_index 为空）」，只陈述本次记录的空值。
- **自检 Prompt 折叠按固定高度判断**：原 CSS `max-height: 104px` 常驻，按钮 `v-if="prompt"` 只要内容非空就显示「展开全文」。现用 `scrollHeight > 104 + 4` 实测溢出，`ResizeObserver` 重测；不溢出时既不加遮罩也不显示按钮，开合两态都按完整高度测量。
- **对照模式没有交代派生轮次**：追问自检是同一会话追加的派生轮（`S1`），没有期望答案、不进入对照，但对照页只展示 N 轮，用户会以为漏了一轮。现存在 `self_check` 时补说明条「派生轮次 S1 · 由 T{n} 的失败判定触发（触发轮未记录时写「本次执行的失败判定」）· 只有实际回复、没有期望答案，因此不进入对照」+「查看完整自检」跳页签；由 `CaseTraceInspector` 传入 `grading-mode` 与 `self-check`。
- **`turns_used>0` 但无明细时页头仍写「没有」**：页头副文案原写「执行记录只保留执行级结论，没有逐轮明细」「本次执行没有产出判定结论，也没有逐轮明细」，与 `turns_used` 矛盾（真实 `15525`）。现按 `turnsUsed` 分档：「执行记录只保留执行级结论，N 轮的逐轮明细未留存」「本次执行共 N 轮，但未留存逐轮明细，也没有执行级评分与结论」；对话页空态标题由「没有逐轮明细」改为「逐轮明细未留存」，头部标签补「共 N 轮 · 逐轮明细未留存」。
- **判分矩阵与抽屉的契约措辞残留**：矩阵标题 / 维度分组原固定「门槛 + 维度得分」「维度得分 · 40/30/20/10」，契约模式改「闸门 + 质量分维度」「质量分维度 · 40 / 30 / 20 / 10」；抽屉「质量分达参考线 true/false」改为只列「质量分 94.0 仅参考，不参与 pass（后端没有把它和阈值比较）」；契约轮次结论不再输出「pass = …」，改为「质量分 … + pass 不由裁判给出，按契约闸门计算」；本轮无硬门槛时写「本轮无硬门槛」而不是 `false`；无四维分时维度合计写「无四维分」，不再显示「0.00 / 100」；判分模式显示全称（`ANSWER_CONTRACT · 契约模式` / `RUBRIC · 旧评分模式（历史数据）` / `LEGACY · 规则 + LLM 裁判`）。
- **抽屉判定卡标题与来源不符**：原固定「LLM 裁判结论」，契约模式的闸门结论与没有 `judge_result` 的执行都被标成 LLM 产出。现 `judgeCardTitle`：契约 →「判定结论」；LEGACY 且有 `judge_result` 对象 →「LLM 裁判结论」；否则「判定结论」。
- **验证**：临时把 mock `900009` 的 planner / db_query 时延置 `null`（验证后已还原并重启）：原始报文页显示「按阶段聚合并按步数比例着色（部分步骤未记录时延，不按 0ms 计）」，无 `.cdist`、无 `0ms` / `NaN`；抽屉显示「3 步 · 3 种工具 · 126ms」与「1 步 · —」，工具统计 `planner` / `db_query` 平均耗时为「—」。真实 `14866` 快照页显示「快照没有文本期望答案…本用例的期望以 expected_rules 规则表达」，chip「规则结果 4 条 / pass 阈值 60（执行口径）」；`16143` chip「规则结果未记录（RUBRIC）」；`14917` 快照 chip「规则结果未参与契约判定 / 契约事实 2 项 / 契约闸门 4 项 / 阈值 60（执行口径）· 不参与契约判定」、抽屉「阈值 60（随执行落库，不参与契约判定）」与「质量分 94.0 仅参考…」；`15525` 页头与文档流为「共 2 轮 · 逐轮明细未留存」，`14899` 仍为「没有对话轮次」；三页均无 `NaN` / `undefined` / `[object Object]`。`npm run typecheck` / `npm run build` 通过。

### 12.23 Tokens 按角色拆分、抽屉裁决与响应形态按落库事实降级（2026-11-24）

- **模拟器 tokens 被当成 Agent 用量**：`TurnVM` 原来只有一组 `promptTokens / completionTokens / tokens`，取值是 `usage.prompt || turn.simulator_prompt_tokens || 0`。真实库 `turn_trace` 267 轮的响应体全部没有 `usage`（`response_raw` 中 0 条），这个 `||` 实际永远落到模拟器列，界面上却是「Agent 本轮 tokens」；另有 213 轮模拟器两列都是 0，会被写成「0 tokens」，把「没记录」冒充成「零消耗」。现拆成 `agentPromptTokens / agentCompletionTokens / agentTokens` 与 `simulatorPromptTokens / simulatorCompletionTokens / simulatorTokens`，全部 `number | null`：`usageOf` 缺失返回 `null`，模拟器两列全 0 视为未记录；`agentTokens` 只在输入与输出都有值时求和，避免只把半边计成「合计」。`SessionStats` 的 `totalTokens` 换成 `agentTokens` / `simulatorTokens`，用 `sumOrNull` 只累加有值项，一项都没有时为 `null`。
- **页头「总 Tokens」把模拟器 + 裁判说成总量**：大数字是 `simulator_tokens + judge_tokens`，标签却是「总 Tokens」，容易被读成全部用量（包含 Agent）。现标签改为「模拟器 + 裁判 Tokens」，hint 补「· 不含 Agent 自身用量」；两位列为 0 时不再显示 0，而是「—」+「执行记录未留存模拟器 / 裁判用量，不按 0 计」（真实 `14899`、mock `900008`）。对话页汇总行同样按 `agentTokens` / `simulatorTokens` 分档，不再累加模拟器列冒充 Agent 用量。
- **抽屉「执行」段把缺失 usage 写成数值**：原「输入 / 输出 / 合计 tokens」直接渲染 0。现缺失显示「—」并补说明条「Agent 用量未记录（响应体里没有 usage 字段），不按 0 计」；有模拟器用量时单独说明「模拟器（生成用户话术）另用 N tokens，不计入 Agent 用量」。
- **抽屉裁判 tokens 只认 `judge_result` 明细**：RUBRIC 执行没有 `judge_result` 行（真实库 1214 条全无），但 `execution_record.judge_tokens` 有值，原实现一律显示「未记录」。现优先累加裁判明细，缺失时回退执行级 `judge_tokens` 并标注「执行级合计（judge_result 明细未落库）」；复制「流程裁决」时也带上同口径。
- **多裁判只取第 1 位的原始返回却不说**：`judgeRaw` 取 `judges[0].raw_response`，两位裁判交叉裁决时（真实 `17425` 为 94 / 88 → 执行级 AVG 91）页面展示的只是第一位裁判的报文。现展开行标注「第 1 / N 位裁判 · 已记录」，并补说明「执行级结论按 N 位合并（综合分 91）」；复制文本同步写「裁判返回（第 1 / N 位裁判）」。
- **抽屉会话级分数取单个裁判分**：原 `model.judges[0].total_score` 被标成「执行级综合分」，与执行级 AVG 不符。现 `sessionScoreInfo` 优先取 `model.totalScore`（契约标签「质量分参考」，LEGACY「执行级综合分」），缺失时才退轮次均分并注明「轮次均分（执行级未记录）」；轮次分合计补「（N/M 轮有分）」。
- **四维均值按索引对齐**：`sessionDimText` 用 `item.dimensions[index] ?? 0`，某轮缺某一维时会按 0 计入，凭空拉低均分。现按维度 key 取值，缺该维度的轮次不参与；完全没有轮次级四维时回退执行级四维，标签改「四维得分（执行级）」（真实 `14875` 显示「30 / 22 / 0 / 9」）。
- **多轮抽屉把执行级裁决挂到本轮**：多轮执行只有一次整通裁决，抽屉「流程裁决 → 返回」原写「pass = … · 综合分 …」，读起来像本轮得分；契约模式还会显示轮次质量分（实际只有执行级）。现多轮统一写「执行级 pass = … · 执行级综合分 / 质量分 …」并注明「整通一次裁决，不归属本轮」；`pass_flag` 缺失时写「未记录」。多轮未判定轮不再输出 `pass = false`，改写「pass 未判定」。
- **无硬门槛时仍输出 pass true/false**：校验行原固定「按硬门槛 pass {{ gateOk }}」，没有规则也没有契约事实时 `gateOk` 恒 true。现写「本轮无硬门槛」，既无分数也无门槛时补「不给出本轮 pass」。
- **响应形态缺失被默认成「非流式 JSON」**：`responseMode` 为空时页面照样写「非流式 JSON」，等于替执行记录断言报文形态。现三态：`sse` →「流式 · N 帧」、`json` →「非流式 JSON」、其余 →「执行记录未保存响应形态」；无报文时说明条改「本轮执行记录里没有响应报文，因此没有响应头 / 响应体可展示」。真实库 267 轮 `response_raw` 都带 `mode`（257 json / 10 sse），该分支只服务异常 / 历史记录。
- **SQL 来源一律写 `response_raw`**：`generated_sql` 列真实库 267 行全空，SQL 实际来自响应体 `body.data.generated_sql`；但页面固定标 `response_raw`，列里真有值时反而说错。现按 `turn_trace.generated_sql` 是否有值切换 `note` 与说明条文案（「本轮落库的 generated_sql」/「本轮响应体（generated_sql 列为空）」）。
- **验证**：mock `5175`：`900002` 页头「模拟器 + 裁判 TOKENS 2,980 · 模拟器 0 · 裁判 2,980 · 不含 Agent 自身用量」，`900008` 为「— / 执行记录未留存模拟器 / 裁判用量，不按 0 计」；`900001` 抽屉 T1「输入 3842 / 输出 615 / 合计 4457」+「取自本轮响应体的 usage」；`900002` 抽屉判定段为「执行级综合分 92 / 会话级结论 · 未拆分到本轮」。真实 `5173`：`14899` 页头「—」且无「/ 100」；`15525` 页头「155 · 模拟器 155 · 裁判 0」、文档流「共 2 轮 · 逐轮明细未留存」；`17425` 抽屉「第 1 / 2 位裁判 · 已记录」+「执行级结论按 2 位合并（综合分 91）」；`14875` 「四维得分（执行级）30 / 22 / 0 / 9」。页面无 `NaN` / `undefined` / `[object Object]`。`npm run typecheck` / `npm run build`（12.68s）通过。

### 12.24 `turns_used>0` 但无明细时不再写「没有逐轮明细」，抽屉裁判 tokens 只在无 judge 行时回退（2026-11-24）

- **空明细句与统一口径不一致**：`turns_used>0` 且没有 `turn_trace` 时（真实 `15525` 2 轮、`16143` / `16152` 1 轮，mock `900008` 1 轮），判分页综合结论句仍写「没有逐轮明细」「没有轮次明细」，与页头、会话行、原始报文页的「逐轮明细未留存」互相矛盾，且读起来像「这轮没跑」。现 `JudgePanel` 的 RUBRIC / 其余模式分支、`CaseTraceInspector.turnEmptyCopy` 的 RUBRIC / 契约 / LEGACY 分支统一改为「逐轮明细未留存」；`turns_used === 0` 的分支保留「没有逐轮明细」（确实没有产生轮次）。
- **页头「会话一致性」hint 同问题**：无逐轮明细时 fixed 写「无逐轮记录，无法判断」，`turns_used>0` 时与事实冲突。现按 `turnsUsed` 分档：有轮次写「逐轮明细未留存，无法判断」，零轮才保留「无逐轮记录，无法判断」（真实 `15525`、mock `900008`）。
- **快照页 RUBRIC 口径句**：「没有逐轮四维与裁判理由」改为「逐轮四维与裁判理由未留存」，避免与 `turns_used` 事实冲突，语义仍是「未留存」而非「未执行」。
- **抽屉裁判 tokens 回退条件过宽**：原逻辑只要明细合计为 0 就回退 `execution_record.judge_tokens` 并标「judge_result 明细未落库」；若确实存在 judge 行但 tokens 落库为 0，就会把整通用量说成这一轮的裁判用量，还误称明细没落库。现收窄为「没有 judge 行才回退」：有 judge 行时明细为 0 就按「未记录」展示（`value: null`），不拿执行级合计顶替；`judgeTokensText` 统一渲染，不再用 `|| '未记录'` 让真实 0 落到「未记录」分支。
- **多裁判 tokens 合计没有标注**：`17425`（双裁判 814 + 103）抽屉显示「917」，未说明是两位裁判的加总，容易读成第 1 位裁判的消耗。现补 `judgeTokensNote`：「（2 位裁判合计）」；回退执行级时仍标「（执行级合计 · judge_result 明细未落库）」，复制「流程裁决」同步带出口径。
- **契约执行页头「部分命中」与判定页「执行级未通过」并存（保留）**：`14917` / `14858` / `900006` 质量分高于阈值但闸门未过，页头轮次结论按既定规则显示「部分命中」，判定页按 `pass_flag` 显示「未通过」。§6.2 已把契约 `warn` 定义为展示态、pass 只看四项闸门，二者口径不同但不矛盾，本轮不改。
- **验证**：mock `5175` `900008` 页头「执行记录只保留执行级结论，1 轮的逐轮明细未留存」+「会话一致性 — / 逐轮明细未留存，无法判断」；真实 `5173` `16152` / `16143` 判分页写「逐轮明细未留存」，`15525` 写「共 2 轮 … 逐轮明细未留存」；抽屉 `17425` 为「917（2 位裁判合计）」、`14866` 为「425」（单裁判不标注）、`14917` 为「346」。全页仍无 `NaN` / `undefined` / `[object Object]`。`npm run typecheck` / `npm run build`（12.50s / 12.75s）通过。

### 12.25 契约单轮结论句补闸门口径，矩阵质量分列已标注（2026-11-24）

- **审计对象**：契约单轮 `14917` / `14858`（真实）、`900005`（通过）/ `900006`（未通过）（mock），检查判分矩阵最后一列与综合结论是否会把执行级质量分读成轮次分或 pass 依据。结论：`overall` 只作展示主分（有轮次分时取有分轮均值，无则回落到执行级参考分），契约模式的 `execPassed` 只读 `pass_flag`（缺失时按四项闸门全过兜底），不拿 `overall` 与阈值比较；矩阵列名在契约模式为「质量分」，表头 `scoreHeadingHint` 写明「仅作参考，不参与 pass」，单元格 `title` 由 `verdictTitle` 写「质量分不参与 pass，执行级判定为…」，不存在误读路径。
- **单轮结论句措辞漏洞**：单轮契约通过时结论只写「本轮通过：质量分 94.0 / 100。」，分数与结论并列却没说 pass 来自闸门；同一函数的多轮契约分支已有「契约闸门全部通过…质量分只作参考」。现单轮 `pass` 补「；契约闸门全部通过，质量分只作参考。」，`fail` 补「；契约闸门未全过或终止状态否决，质量分不参与 pass。」；`warn` 分支保留原有「质量分不参与契约模式的 pass，执行级判定为『未通过』…」；LEGACY 分支措辞不变。
- **注释里的过时条数**：`JudgePanel.vue` 原注释写「当前库 1226 条」，实际 `grading_mode='RUBRIC'` 为 1214 条且会随执行增加变化，注释不适合固化条数，已删去具体数字。
- **验证**：mock `900005` 结论为「本轮通过：质量分 94.0 / 100；契约闸门全部通过，质量分只作参考。」，真实 `14917` 保持「本轮部分命中：质量分 94.0 / 100；质量分不参与契约模式的 pass…」，LEGACY `900003` 仍为「本轮部分命中：总分 82.0 / 100；达线但硬门槛未全过…」；全页无 `NaN` / `undefined` / `[object Object]`。`npm run typecheck` / `npm run build`（12.10s）通过。

### 12.26 终止类硬失败（REPETITION_ABORT / MAX_TURNS_EXCEEDED）不得写成「硬门槛未全过」（2026-11-24）

- **后端口径**：`backend/billing_qa/services/engine/judge.py:313-318` 对 `REPETITION_ABORT` / `MAX_TURNS_EXCEEDED` 直接强制 `passed=False`，这是执行终止状态带来的否决，与规则 / 契约闸门无关；`judge_result.pass` 与 `execution_record.pass_flag` 都会落成 `false`，但 `total_score` 与裁判理由可以全为正面。
- **两个字段都要看**：`execution_record.termination_status` 与 `status` 并非一一对应——`MAX_TURNS_EXCEEDED` 有 15 条写 `MAX_TURNS_EXCEEDED`、37 条写 `MAX_TURNS`；`REPETITION_ABORT` 有 2 条写 `REPETITION_ABORT`、8 条写 `GIVE_UP`。只用 `termination_status ?? status` 取值会让后两类漏判，因此 `terminationLabel` 同时匹配：`REPETITION_ABORT`（任一字段）→「复读熔断」，`MAX_TURNS_EXCEEDED` / `MAX_TURNS`（任一字段）→「轮次超限」。
- **发现的矛盾**：真实 `14876` / `14935`（LEGACY、单轮、`REPETITION_ABORT`）总分 94.0 ≥ 阈值 60、`rule_check` 0/0、`judge_result.reason` 正面，判定页却写「达线但硬门槛未全过」；门槛检查空态与抽屉「分数已达标」flag 同样把原因归到门槛，等于让页面解释一个并不存在的门槛失败。
- **判分页（`JudgePanel.vue`）**：新增 `terminationLabel`（`REPETITION_ABORT` →「复读熔断」，`MAX_TURNS_EXCEEDED` / `MAX_TURNS` →「轮次超限」）与 `hasFailedGate`；`passRuleBody` 仅在终止执行时补「，且执行未因终止状态否决」；单轮 `warn` 分支、多轮 `execPassed=false` 分支、`verdictTitle` 都改成三态——终止执行写「执行因…按硬失败判『未通过』」，有失败门槛仍写「硬门槛未全过」，两者都没有则写「未记录…页面不推断」；判定口径 `em` 补「本次执行终止于{{终止}}，按硬失败否决 pass」（契约模式不追加，闸门口径已覆盖）。
- **门槛检查空态只分两态**：右栏只陈列门槛事实，终止执行写「本轮没有规则或契约门槛；执行因…按硬失败判『未通过』。」，其余仍写「本轮没有规则或契约门槛，无法据此判定。」，不在门槛栏解释门槛以外的原因。
- **抽屉（`TurnDrawer.vue`）**：新增 `terminationLabel` / `overLineFailNote`；`sec=turn` 的 pass 公式补「，且执行未因终止状态否决」；分数达标 flag 由「分数已达标 · 硬门槛未全过」改为「分数已达标 · 执行因复读熔断按硬失败判未通过」；`裁决返回校验` 增加「按终止状态 pass false」行，并在「证据 · 流程裁决 · 校验」写「按分数 pass true / 执行因复读熔断按硬失败否决 pass」+「分数与门槛都不能覆盖」。
- **RUBRIC 分支不再把终止原因说成未保存**：`16081`（RUBRIC、12 轮、总分 61.7 ≥ 快照阈值 60、`status=MAX_TURNS_EXCEEDED`）原写「没有保存当时的判定依据（规则 / 终止原因）」，但 `termination_status` 就在记录里。现终止执行改写「落库总分不低于该执行快照的阈值 60；本次执行以轮次超限结束，后端按终止类硬失败否决 pass，规则明细未留存。」；`16143`（RUBRIC、总分 64.1 ≥ 阈值、非终止）仍保留「判定依据（规则）未保存」的原句。
- **未知轮次不套终止措辞**：`turn.verdict === 'unknown'` 的轮次（如真实 `14875` 三档全未判定）仍按「无法据此判定」展示，终止否决只落在有明确结论的轮次 / 执行级，避免把「没判」说成「判了未通过」。
- **验证**：真实 `5173` `14876` 判分页为「本轮部分命中：总分 94.0 / 100；达线，但执行因复读熔断按硬失败判『未通过』。」+ 门槛检查「本轮没有规则或契约门槛；执行因复读熔断按硬失败判『未通过』。」，抽屉「本轮」段公式为「pass = 硬门槛全过 且综合分 ≥ 60，且执行未因终止状态否决；本轮不满足。」、判定段「分数已达标 · 执行因复读熔断按硬失败判未通过」「按终止状态 pass false」，证据校验行同上；`14875`（`MAX_TURNS_EXCEEDED`）判定口径写「本次执行终止于轮次超限」；`16081` 走新的终止说明、`14944`（RUBRIC 总分 53.4 低于阈值）只陈述分数与结论、`16143` 保留规则未保存句；`14898`（真有失败门槛）仍写「达线但硬门槛未全过」，mock `900003` / `900001` / `900006` / `900009` 无回归；全页无 `NaN` / `undefined` / `[object Object]`。`npm run typecheck` / `npm run build`（7.95s / 8.03s）通过。

### 12.27 复制出的结论 / 流程裁决要能自证 pass 来源（2026-11-24）

- **问题**：`14876` 抽屉「复制流程裁决」原文只有「`pass = false · 综合分 94 · 错误归因 1 条`」，`14898` 只有「`pass = false · 综合分 91 · 错误归因 0 条`」——分数都不低、门槛条数没抄进去，复制出去后无法解释为什么未通过，终止类记录更是完全看不出熔断。判分页「复制结论」同样缺终止状态。
- **判分页**：`conclusionText` 在终止执行时补一行「终止状态：复读熔断（后端按终止类硬失败否决 pass）」，插入在「硬门槛」与裁判理由之间；非终止记录输出不变。
- **抽屉**：`copyJudgeFlow` 的「返回」行在终止执行时追加「· 复读熔断（终止类硬失败否决 pass）」（多轮同样追加在「整通一次裁决，不归属本轮」之后）；另新增一行「校验：按分数 pass … / 按硬门槛 pass …」，口径与模板里的「校验」行一致——契约写「本轮 pass … · 质量分 …（仅参考）」，LEGACY 写分数是否达线与门槛结论，终止执行写「执行因…按硬失败否决 pass」。
- **验证**：`14876` 复制文本为「返回：pass = false · 综合分 94 · 错误归因 1 条 · 复读熔断（终止类硬失败否决 pass）」+「校验：按分数 pass true / 执行因复读熔断按硬失败否决 pass」；`14898` 为「校验：按分数 pass true / 按硬门槛 pass false」；`14875` 为「返回：执行级 pass = false · 执行级综合分 61 … · 轮次超限（终止类硬失败否决 pass）」+「校验：按分数 pass 未提供轮次分 / 本轮无硬门槛」；mock `900006` 为「返回：质量分 94 …（pass 不由裁判给出，按契约闸门计算）」+「校验：本轮 pass false · 质量分 94（仅参考）」、`900001` 多轮带「执行级」与「整通一次裁决」；`npm run typecheck` / `npm run build`（7.95s）通过。

### 12.28 结论句必须写出结论、自检空态必须写出结束方式（2026-11-24）

- **RUBRIC 执行级结论句缺结论**：`14876` 一类终止修好后，RUBRIC 只剩「只保留执行级综合分 53.4 与最终结论……」这半句，正文里既没有「未通过」也没有终止状态——`14944`（`MAX_TURNS_EXCEEDED`、12 轮、53.4 < 阈值 60）、`16152`（`PASS`、89.7）都只说了「与最终结论」，读者要自己回页头看结论。现 `JudgePanel.vue` 在 RUBRIC 分支补 `verdictTail`：分数与阈值无矛盾时写「落库最终结论为「通过 / 未通过」」，终止执行再接「，本次执行以轮次超限结束」；`16143`（64.1 ≥ 阈值 60、非终止）已有「页面按落库结论记为「未通过」」，`divergence` 非空时不再追加，避免同句两遍结论。`pass_flag` 缺失（`execPassed === null`）时不补结论，保持「只剩分数」的诚实形态。
- **终止执行的 RUBRIC 差异句补结论**：总分 ≥ 快照阈值且因终止否决时，原句只写「后端按终止类硬失败否决 pass」，未写落库结论；现补「落库最终结论为「未通过」」，与 `16143` 的非终止分支对齐。
- **全未判定 + 执行级分 + 终止**：`14875`（LEGACY、`MAX_TURNS_EXCEEDED`、3 轮全未判定、61.0 ≥ 60）原写「执行级综合分 61.0 / 100 只代表整通执行，整通执行按记录判「未通过」。」——分数达标却突然未通过，页面上看不到否决来源。现 `allUnknown` 分支抽出 `execClause`，终止执行写「因轮次超限按硬失败判「未通过」」，无终止记录仍写「按记录判「未通过」」，`pass_flag` 缺失则写「不据此推断整通执行是否通过」。
- **自检空态不能一律说「任务未开启」**：`14944`、`16152` 的「追问自检」空态原写「本次执行没有 self_check 记录（任务未开启或本次未触发）」——但 `termination_status` 就在记录里，执行是跑满 12 轮被轮次超限终止的，说「未触发」掩盖了事实。现 `CaseTraceInspector.vue` 抽出 `selfCheckEmptyText`：有 `skip_reason` 仍用后端原因；`ERROR` / `ABORTED` 保持「执行以异常 / 终止结束」；有终止标签写「执行以{终止}结束，本次没有 self_check 记录」；其余才回退「任务未开启或本次未触发」。
- **终止标签集中**：`JudgePanel.vue` / `TurnDrawer.vue` 两份 `terminationLabel` 与新增的自检空态三处需要同一口径，统一收敛为 `model.ts` 的 `terminationLabelOf(detail)`（同时看 `termination_status` 与 `status`），组件内只保留 computed 包装。
- **抽屉裁判理由拼接**：契约模式判定段把 `judgeConclusion` 与「pass 不由裁判给出，按契约闸门计算」用「；」硬拼，裁判理由本身以「。」结尾时渲染成「。；」。新增 `appendClause()` 去掉句末标点再拼（`14917` 现为「……话术清晰；pass 不由裁判给出，按契约闸门计算」）。
- **抽屉头部分数 title**：页头 `scoreInfo.label` 在无轮次级分数时显示「—」，悬停补「本轮没有轮次级分数；执行级综合分见「判定」段」，与多轮口径一致。
- **验证**：真实 `5173` `14944` 判分页为「……落库最终结论为「未通过」，本次执行以轮次超限结束。」、自检页签为「执行以轮次超限结束，本次没有 self_check 记录；页面不补造自检内容。」；`16152` 为「……落库最终结论为「通过」。」；`16143` 只保留原有「页面按落库结论记为「未通过」」不重复；`14875` 为「……因轮次超限按硬失败判「未通过」。」；`15525`（`ERROR`、2 轮无分）仍写「共 2 轮，但没有留存逐轮明细，也没有执行级评分与结论。」；`14917` 抽屉「证据 · 流程裁决」返回行为「……话术清晰；pass 不由裁判给出，按契约闸门计算」。mock `5175` `900001` / `900003` / `900004` / `900006` 无回归（`900004` 无终止仍写「按记录判「未通过」」）。全页仍无 `NaN` / `undefined` / `[object Object]`。`npm run typecheck` / `npm run build` 通过。
