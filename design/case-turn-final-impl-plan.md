# 用例执行详情 · 对话轮次 — 原型复刻实现计划

> 目标：把 `design/case-turn-flow-final.html` 定稿方案**逐屏复刻**到
> `frontend/src/views/components/CaseTraceInspector.vue`，并保证在真实数据下
> 不伪造、不空转。原型是“内容与交互规格说明书”，不是可直接拷贝的源码。

---

## 0. 结论先行

1. **界面 100% 复刻原型**：页头 KPI + 页签 + 工具栏（对话/对照）+ 气泡列表 +
   派生自检块 + 右侧 5 段抽屉 + 页面级 5 页签，样式沿用现有 token（`brand #4f46e5`、
   `rounded-tile 20px`、`canvas #f5f5f7`），不做第二套视觉。
2. **数据分三层**：真实有 → 直接用；可推导 → 明确标注推导口径；没有 →
   显示 `—` / 隐藏该块，**绝不编造**。禁止把原型里的 91/88/74 这类样例分数写死。
3. **组件拆分**：现有 1356 行单文件不继续膨胀，拆成
   `case-trace/*` 子组件 + 一个数据适配层，`CaseTraceInspector.vue` 退化为编排壳。
4. **分 7 个里程碑**推进，每个里程碑都能独立验收（跑起来 + 截图 + 回归）。

---

## 1. 设计不变量（验收时必须逐条检查）

| # | 不变量 | 原型证据 |
|---|---|---|
| 1 | 一轮的全部信息只落在一个抽屉，抽屉固定 5 段：本轮 / 判定 / 执行 / 证据 / 派生与上下文，每段只出现一次 | `drawerSection()` L2720 |
| 2 | 「概览」不做独立段，降为抽屉头部摘要（T4 · 状态 · 提问 · 规则/契约/分数） | `.dr-head`、`renderDrawer()` L3040 |
| 3 | 界面文案只用中文「错误归因」，除原始 JSON 外不出现 `error_tags` | `tagChip()`、判分表 |
| 4 | 扩展信息只保留两条归因入口（会话级 / 轮次级），点任意归因 → 打开判定段并高亮 | `tagLink()` L2140、`focus` 参数 |
| 5 | 抽屉宽 `min(860px, 94vw)`；跨段跳转不得把用户从「判定」带出去 | `.drawer` CSS、`data-dj` 就地展开 |
| 6 | 实际发给 Agent 的是自检 prompt，不出现「已发送的自检问题」独立消息 | `selfPromptMini()`、`sw-note` |
| 7 | 几十步调用链：页面级按阶段聚合轨道 + 抽屉内分组折叠，不横向溢出 | `renderChain()` L2468、`chainExecHTML()` L2622 |
| 8 | 通过 / 部分命中 / 未通过三态一眼可辨；高分通过明确显示「通过」 | `statusPill()`、`.st-*` 行首色条 |
| 9 | 请求体 / 响应体 / 头部均可复制，复制按钮在每个内容框右上角；页签栏与页脚无「复制本页」 | `cp()` L2556、`.mhd` |
| 10 | 证据三组 7 项：对话内容（请求/响应/追问/反问）｜执行证据（调用链/SQL）｜判定材料（流程裁决）；无 SQL 时 6 项 | `EVID_TABS` L2540 |
| 11 | 对话 / 对照两种模式常驻工具栏分段控件，可自由切换，状态筛选与搜索共用（**唯一入口**，页头不再放 `查看对照`） | `#viewSwitch` L1315 |
| 12 | 客服回复气泡只保留「判定 + 详情」两个入口 | `ansFoot()` L1814 |
| 13 | 追问自检不混进正常对话：派生轮次 S1 独立容器（琥珀虚线、可折叠、标明不参与判定） | `.selfwrap`、`selfBubble()` L1884 |
| 14 | 长英文提问不得撑破列表（`min-width:0` + 换行 + 行数截断） | `?lang=en`、`qOf()` |

---

## 2. 原型盘点（复刻基准）

### 2.1 文件与状态机

- 文件：`design/case-turn-flow-final.html`（约 260 KB，含内联 CSS/JS）。
- 全局状态：`state = { layout, tab, open:Set, turn, judgeTurn, cols, cmp, selfOpen, q, status }`
  （L1711）。落地时用 `ref`/`reactive` 一一对应。
- URL 协议：`?cmp=1`、`?status=pass|warn|all`、`?q=`、`?tab=turns|judge|selfcheck|snapshot|raw`、
  `?drawer=<turn>&sec=turn|judge|exec|evid|context&text=<tab>&dj=jprompt|jres`、
  `?lang=en`。落地需保留兼容（至少 `tab`，见 §6.3）。
- 渲染入口：`render()` L2099 按 `state.cmp` → `renderCompare()`，否则按
  `state.layout` → `renderBubble()`（默认）/`renderSummary()`/`renderWork()`/`renderRail()`/`renderMaster()`。
  **只有 `bubble` 是定稿**，其余 4 种是历史备选，不落地。

### 2.2 页面级结构（自上而下）

| 区块 | 实现函数 / 选择器 | 说明 |
|---|---|---|
| 面包屑顶栏 | `.topbar` L1240 | 现有 `TraceView.vue` 已有等价物，不重复 |
| 页头 | `.page-head` L1256 | 标题、编号 chip、执行元信息、`导出报告`/`重新执行`（**`查看对照` 落地时移除**，模式切换只保留工具栏分段控件，见 §10.D6） |
| 5 个 KPI | `.stats` L1275 | 通过轮次 / 裁判综合分 / 平均时延 / 总 Tokens / 会话一致性 |
| 页签栏 | `#tabsBar` L1299 | 对话轮次 / 判分 / 追问自检 / 执行快照 / 原始报文 · 调用链 + 跳转图例 |
| 工具栏 | `#turnTools` L1314 | 对话/对照分段、状态筛选、搜索、显示计数、全部展开 |
| 内容区 | `#stage` L1331 | 气泡 / 对照卡片 / 自检块 |
| 说明段 | L1335 | 交互约定说明（可保留为一次性折叠说明） |
| 抽屉 | `.drawer` L1341 | 头部 / 轮次切换条 / 5 段 / 正文 / 页脚 |
| Toast | `#toast` L1369 | 复制反馈，复用现有 toast store |

### 2.3 抽屉 5 段内容清单（复刻核心）

`drawerSection(t, sec)` L2720：

**本轮 `turn`**
1. 本轮结果：规则 x/y、契约 x/y、裁判 n/100、错误归因 n 条 + 两个快捷入口（判定明细 / 原始证据）+ pass 公式说明。
2. 运行标识：会话 / 用例 / 执行 / 轮次 / 请求 ID / trace / span / 模型 / 温度上限 / 输入来源 / 发生时间。
3. 输入：用户问题、上文摘要、变量 chips、变量来源说明。
4. 输出：finish_reason / 意图 / 数据块 / 内联图标数 / SQL 行数 / 附件。
5. 回复正文（纯文本 300+ 字，可折行）。
6. 结构化块（按轮存在才渲染）：数据卡表 / 环比表 / 排行表 / 导出附件 / 差异对照表 / 归因说明 / 建议追问。
7. 数据来源：Agent 产物 vs 评测产物双行，末尾结论“数字来自工具结果，不是模型生成”。

**判定 `judge`**
1. 规则校验逐条（未通过带 `why`）。
2. 契约命中：facts / behavior / safety 三格 + facts 逐条。
3. 期望答案（用例快照）。
4. 裁判评分：大分 + 三态 pill + 四维进度条 + LLM 综合结论。
5. 错误归因：轮次级一行、会话级一行、空态「无」、点击高亮 + `focus` 说明。
6. 裁决返回校验：维度合计 = total_score、按分数/按硬门槛 pass、标签枚举校验。
7. 裁决记录：`jprompt` / `jres` 就地展开（带 `cp`、`data-dj`、`aria-expanded`）+ 裁判模型参数 KV + trace 注脚。

**执行 `exec`**
1. 执行性能 8 指标（首 Token / 总时延 / 步数 / 最慢步 / 输入输出 tokens / 工具种类）。
2. 模型与参数（模型、温度、stream、response_format、来源、版本、系统提示、评测集）。
3. 调用链：`chainExecHTML()`（阶段分组折叠、异常过滤、arguments/result 复制）+ 口径异常提示。
4. 定位：trace / span / 父 span / 最慢工具。

**证据 `evid`**
- 子页签条（三组，组间分隔符 `.tbsep`）+ 当前 pane + 底部“证据分三组”说明。
- 7 个 pane 的内容规格见 §4.7。

**派生与上下文 `context`**
1. 追问自检：已触发（结论 + 4 指标 + 触发原因/参与判定/模板/模型）+ 完整 prompt + 自检返回；未触发（指向触发轮的入口）。
2. 会话级判定上下文：会话均分 / 通过数 / 最低轮次 / 归因总数 + 结论/总分/四维均分/会话 ID + 跳会话级归因。
3. 执行快照：执行/用例/版本/评测集/数据源/快照时间/模型/系统提示/裁判模型/pass 阈值。

### 2.4 页面级 5 页签

| 页签 | 实现 | 关键内容 |
|---|---|---|
| 对话轮次 | `renderBubble/Compare` | 见 §4.3 / §4.4 |
| 判分 | `renderJudge()` L2145 | 汇总 KPI + 判分表（12 列）+ 行首三态色条 + 点击行就地展开明细 + 归因聚合 + legend |
| 追问自检 | `renderSelfCheck()` L2399 | 触发原因 / 自检配置 / Prompt 卡 / 自检返回气泡 |
| 执行快照 | `renderSnapshot()` L2417 | 快照头 + 请求变量 / 期望答案 / 判分口径 / 与当前用例差异 |
| 原始报文 · 调用链 | `renderChain()` L2468 | 4 KPI + 阶段图例 + 每轮聚合轨道 + 工具 Top6 折叠 + 慢步骤/异常 + 原始报文入口 |

---

## 3. 现有实现盘点（改造起点）

`frontend/src/views/components/CaseTraceInspector.vue`：1467 行，7 页签
`turns | judges | contract | rules | selfcheck | snapshot | raw`，
已有 `turnsView: 'flow' | 'diff'`（对话流 / 对照模式）。

已具备、可保留的能力：

- 头部 KPI（总分/轮次/时延/TTFT/Tokens/终止状态/开始时间）、用例跳转、trace 复制。
- `ruleChecks`（`rule_result.checks`）、`dims`（`judge_result.dimension_scores`）、
  `contractGates/Scorecard/Facts/Behavior/Extras`（`answer_grading` + `fact_results`）、
  `diagnostics`、`selfCheck`、`policyNotes`、`rootCauseText`。
- 轮次折叠、`diff` 视图、`ExpectedDiffView` 组件、`copyText`、`JsonViewer`。
- 轮次输入来源标记（SIMULATOR/STATIC）。

缺口（本计划要补的）：

1. 没有抽屉，所有信息平铺在页签里；「一轮的全部信息」无法在一个落点看完。
2. 没有状态筛选 / 搜索 / 显示计数 / 全部展开工具栏。
3. 没有三态结论（通过 / 部分命中 / 未通过）与 pass 公式；只有执行级 `pass_flag`。
4. 没有错误归因的会话级 / 轮次级分层，也没有点击归因 → 判定段高亮。
5. 没有请求头 / 响应头渲染（`request_payload.headers` 存在但未展示）。
6. 没有调用链（但后端 `TurnTraceOut.tool_calls` / `evidence` **已返回**，前端 `TurnTrace` 类型未声明）。
7. 没有追问自检的派生轮次隔离块（现在只是普通页签）。
8. 没有 `?drawer=` 深链协议。

---

## 4. 数据可用性矩阵（落地前必须知道）

| 原型概念 | 真实字段 | 状态 | 落地策略 |
|---|---|---|---|
| 轮次提问 | `turn.query` | ✅ | 直接用 |
| 轮次回复 | `turn.final_answer` | ✅ | 直接用 |
| 意图 | `turn.intent` | ✅ | 空则 `—` |
| 生成 SQL | `turn.generated_sql` | ✅ | 空则不渲染 SQL 页签（6 项） |
| 请求报文 | `turn.request_payload`（含 `headers`、`body`） | ✅ | 请求头/体分块渲染 |
| 响应报文 | `turn.response_raw`（`mode=json|sse`、`body`/`frames`） | ✅ | 响应头需合成（见 §5.3） |
| 首 Token / 总时延 | `ttft_ms` / `latency_ms` | ✅ | |
| tokens | `simulator_prompt/completion_tokens` + 执行级 `judge_tokens` | ✅ | 轮次合计 = 两者之和 |
| 输入来源 | `input_source` | ✅ | SIMULATOR/STATIC/INITIAL |
| 模拟器思考/状态 | `simulator_thought` / `simulator_status` | ✅ | 抽屉「派生与上下文」可选展示 |
| 隐藏上下文释放 | `hidden_context_released` | ✅ | 抽屉「派生与上下文」 |
| 渲染后 Prompt | `rendered_prompt` | ✅ | 证据「请求」的输入原文；自检规则场景复用 |
| 轮次规则校验 | `turn.rule_check`（`checks[]`） | ✅ | 判定段规则列 |
| 调用链 | **`turn.tool_calls` / `turn.evidence`** | ✅ 后端有、前端类型缺 | **补类型 + 适配器**，按阶段归并 |
| 轮次标量取数 | `turn.raw_db_data` / `api_params` | ✅ | 执行段 / 数据卡来源 |
| 执行级总分 | `detail.total_score` / `judge_result` | ✅ | 判分页汇总 + 抽屉「本轮结果」标注“执行级” |
| 四维评分 | `judge_result.dimension_scores` | ✅ 执行级 | 判定段只展示执行级，注明口径 |
| 错误归因 | `judge_result.error_tags` | ✅ 执行级、无 scope | 前端按语义分 scope（§5.4），文案仍写「错误归因」 |
| 契约 gates/facts/behavior | `answer_grading` / `fact_results` | ✅ 执行级、fact 无 turn_index | 判定段作为“执行级契约”块；轮次列表不硬塞 |
| 期望答案（整通） | `case_snapshot.expected_answer` | ✅ | 对照模式右列兜底 |
| 期望答案（逐轮） | `case_snapshot.turns[k].expected_answer`（`k` 按 `turn.index ?? 位置` 与 `turn_index` 对齐，不按数组下标硬取） | ✅ | 对照模式右列**首选** |
| 逐轮期望规则 | `case_snapshot.turns[i].expected_rules` | ✅ | 轮次结论推导 |
| 裁判 Prompt / 原始返回 | `judges[i].judge_prompt` / `raw_response` | ✅ | 证据「流程裁决」+ 判定“裁决记录” |
| 追问自检 | `detail.self_check`（status/trigger/template/prompt/conclusion/latency/request/response） | ✅ 单条执行级 | 派生轮次 S1；`trigger` 指向轮次 |
| 会话级汇总 | 前端由 `turns` 聚合 | ✅ 可算 | 会话均分/通过数/最低轮/归因数 |
| **轮次总分 / 轮次四维** | 无 | ❌ | 不编造：轮次结论用规则+对照推导，分数仅执行级展示 |
| **逐轮契约命中** | 无（fact 无 turn_index） | ❌ | 契约作为执行级块，轮次列显示 `—` 并加说明 |
| **轮次 tokens 拆分** | 只有 `simulator_*`（模拟器侧） | ⚠️ | 标注“模拟器 tokens”，不冒充“输入/输出” |

---

## 5. 目标结构

### 5.1 页面骨架

```
TraceView.vue
└─ CaseTraceInspector.vue                 ← 编排壳（状态 + 布局）
   ├─ TraceHeaderKpis.vue                 ← 页头 KPI（5 卡）
   ├─ TraceTabs.vue                       ← 5 页签 + 跳转图例
   ├─ TraceToolbar.vue                    ← 对话/对照 + 筛选 + 搜索 + 计数
   ├─ turns/TurnBubbleList.vue            ← 对话模式
   │  ├─ TurnBubble.vue                   ← 单轮：分隔行 / 用户气泡 / AI 气泡
   │  ├─ TurnAnswerBody.vue               ← 回复正文 + 结构化块（kpis/cmps/table/file/diff/callout/tail）
   │  └─ SelfCheckDerivedBlock.vue        ← 派生轮次 S1 隔离容器
   ├─ turns/TurnCompareList.vue           ← 对照模式（卡片 + 差异 + 底部 S1 说明）
   ├─ TraceDrawer.vue                     ← 抽屉壳（宽/头/轮次条/5 段/页脚）
   │  ├─ sections/SectionTurn.vue
   │  ├─ sections/SectionJudge.vue
   │  ├─ sections/SectionExec.vue
   │  ├─ sections/SectionEvidence.vue     ← 子页签 + pane 路由
   │  └─ sections/SectionContext.vue
   ├─ evidence/EvidencePanes.vue          ← 7 个 pane（req/res/follow/clarify/chain/sql/judge）
   ├─ pages/JudgePage.vue                 ← 判分表 + 行内展开
   ├─ pages/SelfCheckPage.vue
   ├─ pages/SnapshotPage.vue
   └─ pages/ChainPage.vue                 ← 阶段聚合轨道 + 工具统计
```

新增共享层：

```
case-trace/model.ts        ← 归一化：TraceTurn[] / TraceTag[] / 派生状态 / pass 公式
case-trace/format.ts       ← bytes / 时长 / 分数 / 头信息合成 / JSON 高亮
case-trace/useTraceView.ts ← 状态机 + URL 同步（tab/cmp/status/q/drawer/sec/text/dj）
```

### 5.2 状态设计

```ts
interface TraceViewState {
  tab: 'turns' | 'judge' | 'selfcheck' | 'snapshot' | 'raw'
  mode: 'chat' | 'compare'          // 对应原型 state.cmp 取反
  status: 'all' | 'pass' | 'warn'   // warn = 关注（非通过）
  q: string
  expanded: Set<number>             // 对话模式“展开全部”辅助
  selfOpen: boolean                 // S1 块折叠
  drawer: {
    open: boolean
    turnIndex: number | null
    section: 'turn' | 'judge' | 'exec' | 'evid' | 'context'
    evidTab: 'req' | 'res' | 'follow' | 'clarify' | 'chain' | 'sql' | 'judge'
    focusTag: string | null
    openRows: Set<string>           // jprompt/jres/step ids/加入对比
  }
}
```

- 关闭抽屉不清空 `section`（再次打开回到上次落点，符合原型“不用关掉重开”）。
- 切轮次时若当前 `evidTab === 'sql'` 而新轮无 SQL → 回落 `req`（原型 L3042）。
- `Esc` 关闭抽屉；打开时焦点进抽屉，关闭后回到触发按钮。

### 5.3 URL 协议（与原型对齐，兼容旧链接）

| 参数 | 含义 | 兼容处理 |
|---|---|---|
| `tab` | 页面页签 | 旧 `judges→judge`、`contract/rules→judge`、`raw→raw` |
| `cmp=1` | 对照模式 | 新 |
| `status` / `q` | 筛选 / 搜索 | 新 |
| `drawer` + `sec` | 打开抽屉 + 段落 | 旧 `sec=overview/content→turn`、`text→evid`、`chain→exec`、`selfcheck→context` |
| `text` | 证据子页签 | 旧 `prompt→req`、`payload→req`、`jprompt/jres→judge` |
| `dj` | 就地展开裁决记录 | 新 |

写入用 `router.replace`（不污染历史），已有 `TraceView` 的 `route.query.tab` 读取逻辑保留。

### 5.4 归因 scope（前端展示规则）

真实 `error_tags` 无 scope。落地用**语义映射**，并在 UI 注明“按整通会话判定 / 归属具体轮次”：

```ts
const SESSION_SCOPED_TAGS = new Set(['未主动反问', '超出最大轮次'])
// 其余标签视为轮次级；未知标签按轮次级处理，不丢弃
```

若某标签在两处都出现，只按 scope 归一次；点击任一标签 → 打开 `judge` 段并 `focusTag` 高亮，滚动到对应行。

---

## 6. 逐屏复刻规格（验收口径）

### 6.1 页头 KPI

- 通过轮次：`通过 X / N 轮`，副标 `Y 轮达标 · Z 轮待改进`，图标随全通过/部分切换绿/琥珀。
- 裁判综合分：按 `grading_mode` 取数 ——
  LEGACY 取 `judge_result.total_score`；`ANSWER_CONTRACT` 取 `judge_result.quality.total_score`
  （契约模式顶层**没有** `dimension_scores/policy_notes/threshold`，不能直接读）。无值显示 `—`。
  副标固定写 `LLM 裁判综合评分`，**不要**写“总分”，因为 `detail.total_score` 在契约模式下是
  scorecard 加权排序分（0–100 但语义不同），与裁判分不可混用。
- 平均时延 + P95；总 Tokens；会话一致性（`session_id` 是否各轮一致 → `PERSIST` / `不一致`）。
- 无数据时显示 `—`，不显示 0。

### 6.2 工具栏

- 分段控件：`对话` / `对照`，`aria-pressed` 同步。**唯一的模式入口**（页头 `查看对照`
  按 D6 移除）；`?cmp=1` 直达时只同步这里的状态，不额外渲染按钮。
- 状态筛选：`全部 N` / `通过 N` / `关注 N`（关注 = 非通过）。
- 搜索：匹配 `query` + `final_answer`，`toLowerCase`，防抖 150ms。
- 右侧：`显示 x / N 轮`、`全部展开/收起`（仅对话模式）。
- 筛选结果为空 → 空态 + 清除筛选按钮；搜索时隐藏 S1 块（原型 `showSelf` 规则）。

### 6.3 对话模式

- 每轮：分隔行 `T{n} · {ttft}ms · {latency}s · {tokens} tokens` → 右对齐用户气泡 → 左对齐 AI 气泡（头像 + 模型名 + 状态 pill + 图标数）。
- AI 气泡结构：正文（`final_answer`，保留 `:icon:` 标记渲染）→ 结构化块 → 底部入口 `判定 {score}` + `详情`。
- 长英文提问：`word-break` + `min-width:0` + 提问在分隔行下方单独成行；列表宽度 100% 内换行。
- 无结构化块时不渲染空表格，只留正文 + 入口。

### 6.4 对照模式

- 顶部汇总：与期望一致 / 存在差异 / 判定未通过 三计数。
- 卡片：`T{n}` + 状态 pill + 提问 + 分数按钮 + `详情`；两列 `Agent 实际回复 | 期望回复 · 用例快照`。
- 有差异：差异条（按 `diff` 项）+ 归因说明；无差异：`与期望一致` 或 `数值与期望一致 · 判定未通过`。
- 底部固定 `.cmp-self`：S1 不进入对照，只给“查看完整自检”入口。

### 6.5 派生自检块（对话模式）

- 容器：琥珀虚线边框 + 淡底 + 圆角，与 6 轮物理分隔，`margin-top:26px`。
- 头部：`派生轮次 S1` 徽标 + `追问自检` + `T4 未通过后自动触发 · 不计入 6 轮 · 不参与通过判定` + `定位 T4` / `完整自检` / `收起`。
- 头下说明条：系统生成 Prompt ≠ 用户消息；回答单独归档；不改变本轮分数与结论。
- 主体：Prompt 卡（`系统生成 · 非用户消息` 角标 + 全文/复制）→ `自检返回` 分隔条 → 自检回复气泡（结论 callout + 复制 + 看 T4 判定）。
- 折叠：点头部或按钮均可；`state.selfOpen` 记住；折叠后仅剩头部 39px。
- 筛选/搜索时整块隐藏。

### 6.6 抽屉

- 宽度 `min(860px, 94vw)`；右侧滑入 + `dimmer`；`Escape` 关闭；头部 `T{n}` + 状态 + 钉住 + 关闭。
- 头部摘要：提问（可折行）+ `规则 x/y · 契约 x/y · 总分 n · 归因 k 条`。
- 轮次切换条：`T1..Tn` 胶囊，颜色按状态，当前高亮，点击**只换轮不换段**。
- 5 段：见 §2.3；每段内容按 §5 数据矩阵决定“显示 / 显示—/ 隐藏”。
- 证据段：三组 7 项（无 SQL 6 项）；`sql` 不存在时从 tab 列表过滤；切换子页签不重置滚动位置过多。
- 复制：`.mhd` 右侧统一按钮，`data-copy` → `navigator.clipboard` → `execCommand` 兜底 → toast。
- 跨段跳转只做 `section` 切换 + 行高亮，不打开新页面。

### 6.7 页面 5 页签

- 判分：列序 `轮次 | 结论 | 提问 | 规则 | 契约 | 数据/40 | 逻辑/30 | 目标/20 | 语气/10 | 错误归因 | 总分`；
  `pass = 硬门槛全过 且 total_score ≥ 80`；三态与行首色条；行展开显示硬门槛、reason、四维、规则/契约逐条、操作。
  **真实数据下**：规则列 = `turn.rule_check`；契约列 = 执行级（行内显示 `—`，表头注“契约按整通判定”）；四维/总分 = 执行级（仅在汇总行/首行展示，其余轮 `—`）。
- 自检：触发原因 / 配置 / Prompt / 返回；`status=SKIPPED` 显示跳过原因；`ERROR` 显示错误。
- 快照：快照头 + 变量 / 期望答案 / 判分口径 / 与当前用例差异（差异无接口时隐藏该卡）。
- 原始报文 · 调用链：4 KPI + 图例 + 每轮阶段轨道 + 工具统计（Top 6 + 展开）+ 慢步骤/异常 + 入口。
  无 `tool_calls` 的轮次显示“本轮无工具调用记录”，轨道退化为时延条。

---

## 7. 需要配合的后端 / 类型改动（最小集）

1. `frontend/src/types.ts`：`TurnTrace` 补 `tool_calls?: unknown[] | null`、`evidence?: unknown[] | null`
   （后端 `TurnTraceOut` 已返回，属纯类型补齐）。
2. 可选（建议但非阻塞）：`judge_result` 增加 `turn_scores`（每轮 total/四维）与
   `error_tag_scopes`，才能让判分表与抽屉“本轮结果”显示真实轮次分数。
   没有它时按 §6.7 降级，不影响复刻验收。
3. 可选：`fact_results[i].turn_index`，用于把契约命中归到轮次。
4. 以上均不改接口形态，只加字段，保持向后兼容。

---

## 8. 里程碑与验收

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M0 数据底座** | `model.ts` 归一化 + `useTraceView` 状态 + 类型补齐 | 单测/手测：归一化后轮次、标签、会话统计与原型数值口径一致；`node --check`/`vue-tsc` 通过 |
| **M1 页面骨架** | 页头 KPI、5 页签、工具栏（筛选/搜索/计数/展开） | 与 `case-turn-final-bubble.png` 顶部/工具栏一致；筛选搜索可用；空态正常 |
| **M2 对话模式** | 气泡列表 + 回复正文 + 结构化块 + 两个入口 | 与截图逐轮一致；长英文不溢出；`判定/详情` 可点 |
| **M3 抽屉** | 5 段 + 轮次切换 + 深链 + 复制 + 就地展开 | 与 `-panel-*.png` 一致；跨段不跳段；`Esc` 关闭；宽度自适应 94vw |
| **M4 证据** | 三组 7 项 + 请求/响应头 + 调用链 + 流程裁决 | 与 `-panel-evid-*.png` 一致；复制全部可用；无 SQL 6 项 |
| **M5 对照模式** | 卡片并排 + 差异 + 底部 S1 说明 | 与 `-cmp.png` 一致；两种模式切换状态不丢 |
| **M6 自检隔离** | 派生块 + 折叠 + 定位 + 页面自检页 | 与 `-selfcheck-bubble.png` 一致；筛选隐藏；不参与判定文案齐全 |
| **M7 判分/快照/调用链页** | 3 个页面级视图 | 与 `-judge.png`、`-chain.png` 一致；阶段轨道几十步不溢出 |

每个里程碑完成后：`npm run build`（或项目对应 typecheck）、控制台 0 error、
1440 / 1040 两档宽度无横向滚动。

---

## 9. 回归清单（沿用原型验收 12 条直达链接）

1. 默认打开 → 对话模式、T1 起、无抽屉
2. `?cmp=1` → 对照模式、6 卡 + 底部 S1 说明
3. `?tab=judge` → 判分表 12 列、三态、行展开
4. `?tab=selfcheck` → 自检页（无记录时页签隐藏）
5. `?tab=snapshot` → 快照 4 卡
6. `?tab=raw` → 调用链轨道 + 工具统计
7. `?drawer=4&sec=turn` → 抽屉本轮段
8. `?drawer=4&sec=judge` → 判定段 + 归因高亮
9. `?drawer=4&sec=exec` → 执行段（38 步折叠）
10. `?drawer=4&sec=evid&text=req` / `res` → 报文头 + 体
11. `?drawer=4&sec=evid&text=chain` → 阶段分组
12. `?drawer=4&sec=evid&text=judge&dj=jprompt` → 就地展开裁决 Prompt
13. `?status=warn` / `?q=杭州` → 筛选/搜索，S1 块隐藏
14. 清空数据 / 执行中（轮次为空）→ 骨架不崩，显示空态

---

## 10. 风险与待确认

| # | 风险 | 影响 | 应对 |
|---|---|---|---|
| R1 | 轮次无总分/四维 | 原型“每轮 74/88”无法 1:1 复现 | **已定：降级**（轮次列 `—`，分数只留执行级并注明口径），不加后端字段 |
| R2 | `tool_calls` 结构由环境决定 | 调用链渲染可能字段对不上 | 写防御式适配器，支持 `name/tool/tool_name`、`arguments/args/input`、`result/output`、`duration_ms/elapsed_ms/ms`，未知字段进 payload 折叠区 |
| R3 | SSE 响应头无原始值 | 只能合成头信息 | 合成时标注“按响应重建”，字段值不伪造（`Content-Length` 用实际字节） |
| R4 | 单条自检 vs 多轮 | 只能挂一轮 | 用 `self_check.trigger` 解析轮次；解析不到挂最后失败轮并注明 |
| R5 | 单文件拆分回归面大 | 可能漏功能 | 先建 `model.ts` 与只读渲染，再逐段迁；保留旧 7 页签在 M1–M3 期间可回退 |

**评审结论（已定）**

1. **R1 → 采纳「诚实降级」**：轮次分数列显示 `—`，结论由 `rule_check` + 逐轮期望推导；
   总分 / 四维只在执行级展示并注明口径。**不加后端 `turn_scores`**。
2. **R5 补充**：轮次契约命中同样降级 —— `facts[]` 无 `turn_index`，因此「判定」段标注为
   **执行级（最终答案）契约**，轮次列显示 `—`，不假装每轮都有。
3. **页头动作 → 保留 `导出报告` / `重新执行`**；后端无导出接口时 `导出报告` 走前端
   生成（JSON 下载），不阻塞。
4. **页头 `查看对照` 按钮 → 移除**（见 D6）。
5. **`?tab=contract|rules|judges` → 纯映射到 `judge`**，不做页面级锚点。
   依据：全站无收藏功能、无任何代码生成这类链接（仅 `stores/debug.ts` 生成
   `?tab=selfcheck`，且该页签仍存在）。

**已知会「不像原型」的 7 处，需你接受**

| # | 原型 | 落地 | 原因 |
|---|---|---|---|
| D1 | 每轮 74/88/91 分 + 四维 | `—` | 后端无轮次级评分 |
| D2 | 每轮「契约 2/3」 | `—`（执行级另标） | `fact` 无 `turn_index` |
| D3 | 响应头 10 项 | 读 `response_raw.headers`；缺失时显示空态并注明「只落库响应体」 | 后端不存响应头与 status |
| D4 | 「流程裁决」页签 | 由 `judge_result` / `answer_grading` / `judges[]` / `self_check` 组装「输入 → 返回 → 校验」 | 无独立 `flow_judge` 对象 |
| D5 | 每轮输入/输出 tokens | 只有模拟器侧 tokens，标签写「模拟器 tokens」 | 后端不拆被测侧 token |
| D6 | 页头 `查看对照` 按钮 | **移除**，只保留工具栏「对话 / 对照」分段控件 | 单一入口，避免两处切换状态不同步 |
| D7 | 轮次结论只有通过 / 部分命中 / 未通过 | 多轮且无规则、无轮次分时显示**未判定**（slate），并从 KPI 达标数中剔除 | 无轮次级分数与门槛时无法判定，不能默认「通过」 |
