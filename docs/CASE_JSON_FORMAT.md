# 用例 JSON 导入导出格式（v1）

> 适用版本：`billing-qa/cases` `schema_version = 1`
> 相关代码：`backend/billing_qa/services/io/case_json.py`、`backend/billing_qa/api/v1/cases.py`
> 前端入口：用例资产页 →「导入 JSON」/「导出 JSON」

---

## 1. 设计目标

| 目标 | 做法 |
| --- | --- |
| 换环境可复用 | 不导出数据库自增 `id`，环境 / 模板 / 模型一律导出**业务编码** |
| 不丢字段 | 字段清单与版本快照 `SNAPSHOT_FIELDS` 对齐，新增字段自动进入导出 |
| 人来可读、Git 可比 | 固定字段顺序、`indent=2`、中文不转义 |
| 导错了能撤回 | 导入前先 dry-run 预览，确认后才落库；冲突策略可选 |
| 旧文件不报废 | 兼容旧版扁平数组、单对象、Excel（仅 API 兼容） |

---

## 2. 文件信封

导出的文件永远是**一个对象**，不是裸数组：

```json
{
  "schema": "billing-qa/cases",
  "schema_version": 1,
  "exported_at": "2026-11-16T21:30:00",
  "exported_by": "admin",
  "case_count": 1,
  "filters": { "case_type": null, "ids": [12] },
  "items": [
    {
      "code": "DEMO-CONTRACT-001",
      "name": "账单总额（契约）",
      "case_type": "SINGLE",
      "status": "ACTIVE",
      "priority": "P0",
      "tags": ["答案契约", "账单"],
      "environment_code": "MOCK-ENV",
      "query": "帮我查一下 13800138000 最近一期账单总额是多少？",
      "grading_mode": "ANSWER_CONTRACT",
      "input_type": "CLEAR",
      "as_of": "2026-10",
      "snapshot_id": "SNAP-2026-10",
      "answer_envelope": {
        "must": [
          {
            "id": "amount",
            "kind": "scalar",
            "label": "账单总额",
            "value": 268.5,
            "unit": "CNY",
            "tolerance": 0.01,
            "aliases": ["total_amount", "本期应缴"],
            "require_name": true,
            "verification_status": "VERIFIED",
            "evidence_ref": "bill:2026-10:13800138000"
          }
        ],
        "may": [],
        "must_not": [{ "id": "banned", "kind": "text", "value": "系统错误" }],
        "extras_policy": "lenient"
      },
      "response_contract": {
        "allowed": ["answer_with_assumption"],
        "answer_with_assumption": {
          "must_state_assumption": true,
          "scenarios": [
            { "assumption": "按最近一期账期 2026-10 口径", "must": { "amount": 268.5 } }
          ]
        }
      },
      "plan_spec": { "required_capabilities": ["bill_query"], "max_tool_calls": 3 },
      "risk_level": "LOW",
      "owner": "qa-team",
      "review_status": "NOT_REQUIRED",
      "dataset_version": "v1",
      "dedupe_hash": null
    }
  ]
}
```

- `filters` 记录本次导出条件，仅供人工追溯，导入时忽略。
- `exported_at` / `exported_by` / `case_count` 均为只读元信息。
- `items` 是唯一有业务意义的数组；空数组的文件会被拒绝（提示「文件中没有用例」）。

---

## 3. 字段清单

### 3.1 标识与归类

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | string | **唯一标识**，导入按它判断新增 / 更新；最长 64 |
| `name` | string | 用例名称，必填 |
| `description` | string | 描述 |
| `case_type` | enum | `SINGLE` / `STATIC_MULTI` / `DYNAMIC_MULTI`，必填 |
| `status` | enum | `ACTIVE` / `DISABLED` / `DRAFT`，新建缺省 `ACTIVE` |
| `priority` | string | 新建缺省 `P1` |
| `tags` | string[] | 业务域标签 |
| `owner` | string | 负责人 |
| `risk_level` | enum | `LOW` / `HIGH`；`HIGH` 会要求评审 |

### 3.2 执行配置

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `environment_code` | string | 环境编码，导入时解析为环境 id |
| `query` | string | 单轮固定输入；`SINGLE` 必填 |
| `expected_answer` | string | 4 维评分下的预期口径 |
| `expected_rules` | object | 规则硬校验配置（JSON 对象，不要写成字符串） |
| `turns` | object[] | 静态多轮话术；`STATIC_MULTI` 至少一条 |
| `session_expected` | object | 会话级预期 |
| `fail_fast` | boolean | 静态多轮单轮失败即中断 |
| `user_goal` | string | 动态多轮测试目标 |
| `initial_query` | string | 动态多轮首轮破冰话术 |
| `persona_description` | string | 用户画像 |
| `hidden_context` | object[] | 隐藏信息释放表 `{key, content, release_hint}` |
| `max_turns` | int | 1～20 |
| `terminate_conditions` | object | 终止条件 |
| `request_variables` | object | 用例级请求变量，渲染进环境请求头 / 请求体模板；值支持字符串、数字与 JSON |

### 3.3 模板与模型

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `simulator_template_code` | string | 模拟器 Prompt 模板编码 |
| `simulator_template_strategy` | enum | `LATEST` / `PINNED` |
| `simulator_template_version` | int | `PINNED` 时必填 |
| `judge_template_code` | string | 裁判 Prompt 模板编码 |
| `judge_template_strategy` | enum | `LATEST` / `PINNED` |
| `judge_template_version` | int | `PINNED` 时必填 |
| `prompt_overrides` | object | 局部 Prompt 覆写 `{judge: "...", simulator: "..."}` |
| `judge_model_codes` | string[] | 裁判模型编码列表 |
| `judge_strategy` | enum | `AVG` / `VETO` 等 |
| `pass_threshold` | number | 4 维评分通过线；契约模式不参与通过判定 |

### 3.4 答案契约

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `grading_mode` | enum | `LEGACY`（4 维）或 `ANSWER_CONTRACT`（契约），缺省 `LEGACY` |
| `input_type` | enum | `CLEAR` 与其他 7 种非清晰形态（仅标签不同） |
| `as_of` | string | 账期 / 数据时点；契约模式必填 |
| `snapshot_id` | string | 数据快照标识 |
| `answer_envelope` | object | `{must, may, must_not, extras_policy}`，契约模式至少一条 `must` |
| `response_contract` | object | 非 `CLEAR` 输入必填，`allowed` 至少 2 项，且不能包含 `answer` |
| `plan_spec` | object | 取数计划，如 `{required_capabilities, max_tool_calls}` |

> 事实取证结论（`verification_status` / `evidence_ref` / `evidence_note`）随
> `answer_envelope` 一起导出。跨环境导入时如需保留「已取证」状态，请把这几项一并带上；
> 否则新事实默认 `PENDING`，会导致该用例无法评审通过或冻结。

### 3.5 请求变量与多轮会话绑定

执行器渲染请求报文时按三层优先级合并变量：

```
运行时变量（query / session_id / history / history_text / turn / timestamp_iso）
  > 用例 request_variables
    > 环境变量池
```

- 占位符写法为 `{{variable}}`，可出现在环境 `headers_template` 与 `body_template` 的任意位置；
- `timestamp_iso` 为请求时刻的 ISO 8601 字符串（`2026-11-17T18:00:00.123`），每轮刷新；
- 会话字段名由模板决定（如 `"conversationId": "{{session_id}}"`、`"sessionid": "{{session_id}}"`），
  不同接口可各自定义；`SERVER_RETURNED` 首轮渲染为空字符串，由目标接口按首轮对话处理；
- `request_variables` 的 key 需匹配 `[A-Za-z_][A-Za-z0-9_]*`，且不能使用运行时保留字
  （`query` / `history` / `history_text` / `turn` / `timestamp_iso`），否则校验失败；总量上限 16KB。

> 「AI 帮我填」覆盖用例抽屉里的全部可填语义字段：除基础字段与 Must/May/MustNot 外，
> 还可生成 `request_variables`、`expected_rules`、`turns[].expected_rules`、`session_expected`、
> `fail_fast`、`response_contract`、`plan_spec`、`extras_policy`、`judge_strategy`、
> `pass_threshold`、`change_summary`。含数字或日期的取值必须有用户描述 / 业务口径 / 相似用例作为依据，
> 无依据时进入「待补充」；`plan_spec` 的能力编码只能来自系统能力清单。

会话方式（`SERVER_RETURNED` / `CLIENT_GENERATED`）由**执行环境**统一配置，所有用例跟随，
用例 JSON 不再接受 `session_mode` 字段：

| 模式 | 行为 |
| --- | --- |
| `SERVER_RETURNED` | 首轮渲染空会话值，由响应回填并用于后续轮次（默认） |
| `CLIENT_GENERATED` | 执行开始时生成唯一值（`exec-{execution_id}-xxxxxxxx`），全程各轮一致 |

示例（会话方式由 `QA-MOCK-JSON` 环境决定）：

```json
{
  "code": "NEW-MULTI-001",
  "name": "多轮账单追问",
  "case_type": "STATIC_MULTI",
  "environment_code": "QA-MOCK-JSON",
  "request_variables": { "userid": "13800138000", "custid": "C0001" },
  "turns": [
    { "query": "我本月账单多少钱？" },
    { "query": "其中超套流量费是多少？" }
  ]
}
```

### 3.6 评审与数据集

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `review_status` | enum | `NOT_REQUIRED` / `PENDING` / `APPROVED` / `REJECTED` |
| `dataset_version` | string | 数据集版本标签 |
| `dedupe_hash` | string | 去重指纹，一般由系统维护 |

### 3.7 只读字段（出现也不写入）

`id`、`current_version`、`created_by`、`updated_by`、`deleted_at`、`reviewed_by`、
`created_at`、`updated_at`、`exported_at`、`schema`、`schema_version`、`case_count`。

---

## 4. 引用字段与解析

| 文件字段 | 数据库字段 | 解析规则 |
| --- | --- | --- |
| `environment_code` | `environment_id` | 编码必须存在，否则该条失败 |
| `simulator_template_code` | `simulator_template_id` | 同上 |
| `judge_template_code` | `judge_template_id` | 同上 |
| `judge_model_codes` | `judge_model_ids` | 逐个解析，任一不存在则该条失败 |

> 模拟器模型自 2026-11 起改为「全局唯一启用」，不再出现在用例/导出文件中；
> 旧文件里的 `simulator_model_code` 会被当作未知字段忽略（仅告警）。

失败时不会静默置空，错误信息会写明是哪个编码：

```
第 3 条：裁判模型编码不存在: gpt-4o-judge
```

---

## 5. 导入语义

### 5.1 文件形态

导入接受三种顶层结构：

1. 版本化信封（本文第 2 节，推荐）；
2. 旧版扁平数组 `[{...}, {...}]`（会在 `warnings` 中提示已按兼容模式导入）；
3. 单个用例对象 `{ "code": "...", ... }`。

### 5.2 逐条处理

| 场景 | 行为 |
| --- | --- |
| 新 `code` | 新建用例，写一条 `IMPORT / 批量导入创建` 版本记录 |
| 已存在 `code` | 按冲突策略处理；`upsert` 时写 `IMPORT / 批量导入更新` 版本记录 |
| 文件内 `code` 重复 | 第二条及以后判为失败，提示「文件内用例编号重复」 |
| 缺少 `code` | 该条失败 |
| 字段校验失败 | 该条失败，其余记录继续，互不影响 |
| 未知字段 | 忽略并产生文件级告警，不影响该条导入 |

### 5.3 覆盖粒度

`upsert` 是**按出现字段覆盖**，不是整条替换：

- 文件里出现的字段 → 覆盖数据库值；
- 文件里没出现的字段 → 保持数据库原值；
- 文件里显式写 `null` → 覆盖为空。

因此可以只导入 `{"code": "X", "priority": "P0"}` 做局部修改。

### 5.4 评审与闸门

导入不会绕过运营闸门：

- 已 `APPROVED` 的契约用例，若本次触及契约内容字段（`answer_envelope`、`response_contract`、
  `plan_spec`、`query`、`as_of` 等）且文件未显式写 `review_status`，自动退回 `PENDING`；
- 文件要求 `review_status = APPROVED`，但 Must 事实仍有未 `VERIFIED` / `WAIVED` 的，
  该条直接失败（与其他写入口径一致）。

### 5.5 默认值

新建时未提供时取系统默认：`status=ACTIVE`、`priority=P1`、`grading_mode=LEGACY`、
`input_type=CLEAR`、`risk_level=LOW`、`review_status=NOT_REQUIRED`、`dataset_version=v1`。

---

## 6. dry-run 与冲突策略

### 6.1 请求参数

```
POST /api/v1/cases/import?dry_run=true|false&on_conflict=upsert|skip|error
Content-Type: multipart/form-data
file=@cases.json
```

| 参数 | 取值 | 说明 |
| --- | --- | --- |
| `dry_run` | `false`（默认） | 真实写入 |
|  | `true` | 只校验、只回显结果，事务整体回滚，`case_ids` 返回空数组 |
| `on_conflict` | `upsert`（默认） | 已存在则更新 |
|  | `skip` | 已存在则跳过，计入 `skipped` |
|  | `error` | 已存在则判为失败，错误信息「用例编号 X 已存在」 |

dry-run 返回结构与真实导入完全一致，因此前端可以「先预览、再确认」两步走。

### 6.2 响应结构

```json
{
  "total": 3,
  "created": 1,
  "updated": 1,
  "skipped": 0,
  "failed": 1,
  "dry_run": true,
  "on_conflict": "upsert",
  "errors": [{ "row": 3, "message": "环境编码不存在: PROD-X" }],
  "items": [
    { "row": 1, "code": "A-001", "action": "created", "message": null },
    { "row": 2, "code": "A-002", "action": "updated", "message": null },
    { "row": 3, "code": "A-003", "action": "failed", "message": "环境编码不存在: PROD-X" }
  ],
  "warnings": ["文件为旧版扁平数组，已按兼容模式导入"],
  "case_ids": []
}
```

`items[].action` 四态：`created` / `updated` / `skipped` / `failed`。

---

## 7. 前端操作流程

1. 点击「导入 JSON」，选择文件；
2. 弹窗自动发起 dry-run，展示 **文件条数 / 新增 / 更新 / 跳过 / 失败** 与逐条动作；
3. 可切换冲突策略（覆盖更新 / 跳过 / 报错），切换后重新 dry-run；
4. 若有文件级提示（旧格式、未知字段），在黄色区块展示；
5. 点击「确认导入（N 新增 / M 更新）」才真正写库；
6. 成功后自动刷新列表。

「导出 JSON」导出**当前筛选结果**（未设置筛选时导出全部未删除用例，含全部字段），
文件名形如 `test_cases_20261116_2130.json`。支持的筛选参数与列表接口一致：
`case_type` / `status` / `tag` / `environment_id` / `grading_mode` / `review_status` / `keyword`，
也可用 `ids` 指定具体用例。

---

## 8. 兼容性说明

| 格式 | 现状 |
| --- | --- |
| JSON 信封 | **主推**，字段最全 |
| 旧版扁平 JSON 数组 | 可导入，给出告警；`tags` 支持逗号字符串 |
| 单个 JSON 对象 | 可导入 |
| Excel `.xlsx` | 仅后端兼容：`GET /cases/export?format=xlsx` 与 `.xlsx` 上传仍可用；前端不再提供入口 |

Excel 兼容路径的列集合已与 JSON 字段对齐：包含 `description`、`status`、`request_variables`、
`prompt_overrides`、`judge_model_codes` 等最终字段；嵌套对象按 JSON 文本读写，
`environment_code` / 模板编码 / 裁判模型编码同样会在导入时解析为数据库引用。
会话方式 `session_mode` 由执行环境统一决定，JSON 与 Excel 均不再收该字段。

---

## 9. 常见失败信息

| 错误信息 | 原因 | 处理 |
| --- | --- | --- |
| `无法解析 JSON 文件: ...` | 语法错误 / 非 UTF-8 | 用编辑器校验后另存为 UTF-8 |
| `不支持的 schema: xxx` | 信封 `schema` 不是 `billing-qa/cases` | 改回标准信封，或直接传裸数组 |
| `不支持的 schema_version: 99` | 版本过高 | 升级后端，或导出为 v1 |
| `JSON 缺少 items 数组，也不是单个用例对象` | 顶层对象既无 `items` 也无 `code` | 使用标准信封 |
| `文件中没有用例` | `items` 为空数组 | 至少放一条用例 |
| `缺少用例编号 code` | 记录无 `code` | 补 `code` |
| `文件内用例编号重复: X` | 同一文件两条同 `code` | 合并或改名 |
| `环境编码不存在: X` / `模板编码不存在` / `模型编码不存在` | 目标库没有对应编码 | 先建引用对象，或改成目标环境已有编码 |
| `第 N 条：忽略未知字段 ...`（warning） | 文件含当前版本不认识的字段 | 一般可忽略；如为必要字段请升级后端 |

---

## 10. 最小可用示例

只改优先级，其余保持库内不变：

```json
{
  "schema": "billing-qa/cases",
  "schema_version": 1,
  "items": [{ "code": "DEMO-SINGLE-001", "priority": "P0" }]
}
```

新建一条 4 维单轮用例：

```json
{
  "schema": "billing-qa/cases",
  "schema_version": 1,
  "items": [
    {
      "code": "NEW-SINGLE-001",
      "name": "余额查询",
      "case_type": "SINGLE",
      "query": "我的账户余额是多少？",
      "expected_answer": "余额 128.50 元"
    }
  ]
}
```

新建一条契约用例（必须带 `as_of` 与至少一条 `must`）：

```json
{
  "schema": "billing-qa/cases",
  "schema_version": 1,
  "items": [
    {
      "code": "NEW-CONTRACT-001",
      "name": "余额查询（契约）",
      "case_type": "SINGLE",
      "query": "我的账户余额是多少？",
      "grading_mode": "ANSWER_CONTRACT",
      "input_type": "CLEAR",
      "as_of": "2026-11",
      "answer_envelope": {
        "must": [
          { "id": "balance", "kind": "scalar", "label": "账户余额", "value": 128.5, "unit": "CNY" }
        ],
        "may": [],
        "must_not": [],
        "extras_policy": "lenient"
      }
    }
  ]
}
```
