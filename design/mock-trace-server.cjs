/**
 * 演示用 mock 后端：逐字段对照真实生成链路构造（2026-11-23 四轮全量核验）。
 *
 * 生成路径对照（所有数据均为生产可达形态）：
 * - 用例类型 STATIC_MULTI：静态多轮逐轮跑 turns[i].expected_rules（executor.py:266-270），
 *   「T1/T2 无规则（未判定）+ T3 规则失败」是该类型可达的唯一组合；
 * - input_source='STATIC'（executor.py:272）；静态多轮无模拟器 → simulator_* 全空/0；
 * - termination 恒 COMPLETED（executor.py:287）；status=FAIL（executor.py:524）；
 * - 执行级 rule_result = merge_rule_results（executor.py:288，label 带 T{index} 前缀，
 *   rule_evaluator.py:317），extracted 为空 dict（RuleResult 默认值）；
 * - must_contain 检查项形态（rule_evaluator.py:253-263）：label 自动生成「必须包含「{kw}」」、
 *   actual 恒 None、未命中 evidence=''（_evidence 返回空串）；
 * - 每轮 rule_check 顶层含 extracted（rule_evaluator.py:46-64 to_dict 全键）；
 * - 响应形态：SSE/JSON 分支由环境级 is_stream 标志或响应 content-type 决定
 *   （target_api.py:369），同一执行内不可混排 → 本演示环境为全 SSE（is_stream=true），
 *   请求头恒 Content-Type: application/json + Accept: text/event-stream（target_api.py:332-335），
 *   请求体 stream=true 为环境模板常量；
 * - SSE 帧：问账风格 {data:{delta}} 增量（sse.py:50-56 支持）+ 首帧 data 白盒元数据
 *   （经 $.data.* 全命中，extractor.py:14-22）+ 末帧顶层 usage/finish_reason；
 *   metadata 为全帧深合并（sse.py:66），frames 落库取末 50 帧（target_api.py:405），
 *   final_answer 提不到 $.data.answer 时回落 sse_result.content（target_api.py:384-385）；
 *   SSE 轮有独立 TTFT（target_api.py:390）；
 * - 敏感头掩码集合 {authorization, cookie, x-api-key, token, x-token, set-cookie}
 *   （target_api.py:476），请求/响应头共用；
 * - judge_result 键形 = judge.py:326-340 aggregate 原文；dimension_labels 为生产原文
 *   （judge.py:26-29）；reason 格式「【模型】{总分}分：{理由}」（judge.py:320-322）；
 *   strategy='AVG'（enums.py:45-47，默认 context.py:242）；红线策略（judge.py:164-193）
 *   本剧本均不触发（data_accuracy=20>0、COMPLETED、裁判原始 pass=false）；
 *   规则失败时 policy_notes 追加固定文案（executor.py:511）；
 * - judges 表字段（executor.py:746-761）；latency_ms=墙钟（executor.py:157）、
 *   ttft_ms=各轮均值 round2（executor.py:461-466）；
 * - self_check：trigger 固定文案「LLM 裁决不通过」（self_check.py:107）、
 *   performed_at 秒级（self_check.py:392）、turn_index=最后一轮、notes 为空不落库；
 *   自检同走 adapter.chat → 响应同为 SSE 形态；
 * - 快照：build_snapshot 全字段恒写入含 None（snapshot.py:105-119），
 *   LEGACY 的 verified_facts 为 []（facts.py:87-90）；hidden_context/HiddenContextItem
 *   为 {key, content, release_hint}（schemas/case.py:274-277）；TurnItem 为
 *   {index, query, expected_answer?, expected_rules?}（schemas/case.py:120-127）；
 * - trace_uid = str(uuid.uuid4()) 裸 UUID（executor.py:654）；
 * - 时间戳：turn/execution 毫秒级（now_iso），self_check.performed_at 秒级；
 *   轮次 created_at 与 latency 时序自洽（响应完成时刻 + 落库开销）；
 * - 任务枚举：status=COMPLETED（enums.py:53）、control_state=FINISHED（scheduler.py:374）、
 *   schedule_type=IMMEDIATE（enums.py:62）；TaskOut 恒含 summary/error_message/
 *   environment_name/run_count/unfinished_cases/retrying_cases（schemas/task.py:130-138）；
 * - 通知仅投影用户/角色管理审计事件（notifications.py:11-21），键形 message、
 *   level ∈ info/success/warning/error（schemas/notification.py:11-19）。
 *
 * 用法：node design/mock-trace-server.cjs   （监听 127.0.0.1:8877）
 * 仅本地演示用，不入库。
 *
 * 演示数据索引（独立 ID 段：任务 900101/900102，执行 900001-900009）
 * 900101 / 900001  LEGACY 3 轮失败 · self_check=DONE（长文本折叠 + 自检 Prompt）
 * 900101 / 900002  LEGACY 2 轮通过 · self_check=SKIPPED（通过用例不得出现自检）
 * 900102 / 900003  单轮 82 · 分够但规则硬门槛未过（jv-warn）
 * 900102 / 900004  3 轮全轮无规则/无轮次分 · 执行级 71（未判定 + 执行级四维回落）
 * 900102 / 900005  ANSWER_CONTRACT 单轮全过 94（jv-pass）
 * 900102 / 900006  ANSWER_CONTRACT facts/safety 未过（jv-warn + 实际值 999）
 * 900102 / 900007  多轮 T1 未判定 / T2 规则通过（同一执行内混合结论）
 * 900102 / 900008  历史 RUBRIC 0 轮（无轮次/无裁判 JSON，执行级 89.7 通过）
 * 900102 / 900009  单轮 48 < 阈值 60（jv-fail）
 */
const http = require('node:http')

const EXECUTION_ID = 900001
const TASK_ID = 900101
const SESSION_ID = 'sess-20261123-8e21c4'
const AGENT_MODEL = 'wanjuan-agent-v2.3'

/** 与后端 now_iso() 一致：datetime.isoformat(timespec="milliseconds") */
function msIso(sec, ms) {
  return `2026-11-23T10:12:${String(sec).padStart(2, '0')}.${String(ms).padStart(3, '0')}`
}
/** 与 self_check performed_at 一致：isoformat(timespec="seconds") */
function secIso(sec) {
  return `2026-11-23T10:12:${String(sec).padStart(2, '0')}`
}

// ---------------------------------------------------------------- 请求报文（target_api.py:342-347）
/** 环境模板固定：全 SSE（is_stream=true），stream 为 body 模板常量 */
function requestPayload(query, requestId, session = SESSION_ID) {
  return {
    method: 'POST',
    url: 'https://agent-gw.internal/billing-qa/v2/chat',
    headers: {
      // 固定头按代码原样大小写（target_api.py:332-335），模板头保留配置原样
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      Authorization: '***', // _sanitize_headers 掩码后落库形态
      'x-request-id': requestId,
    },
    body: {
      query,
      session_id: session,
      stream: true, // 环境模板常量，逐轮一致
      variables: { account_id: 'A10086', billing_month: '2026-10' },
    },
  }
}

// ---------------------------------------------------------------- 响应报文头（B1 落库 + _sanitize_headers 脱敏）
function sseHeaders(requestId) {
  return {
    'content-type': 'text/event-stream; charset=utf-8',
    'x-request-id': requestId,
    'x-upstream': 'billing-agent-gw-02',
    'set-cookie': '***',
    server: 'nginx/1.24',
    connection: 'keep-alive',
  }
}

// ---------------------------------------------------------------- SSE（consume_sse 产物形态）
/** 与 sse.py:66 _deep_merge 一致的全帧深合并 */
function deepMerge(target, source) {
  for (const [key, value] of Object.entries(source)) {
    if (
      value && typeof value === 'object' && !Array.isArray(value) &&
      target[key] && typeof target[key] === 'object' && !Array.isArray(target[key])
    ) {
      deepMerge(target[key], value)
    } else {
      target[key] = value
    }
  }
  return target
}

/**
 * 问账风格流式响应：首帧 data 白盒元数据（$.data.* 全命中）+ 增量帧 + 末帧 usage/finish_reason。
 * - 白盒帧只贡献 metadata，不贡献增量（sse.py:50-56：data 内无 delta/content/text 键）；
 * - final_answer 提不到 $.data.answer → 回落 content = 增量拼接（target_api.py:384-385）。
 */
function sseResponse(requestId, { deltas, usage, meta }) {
  const frames = []
  if (meta) frames.push({ data: meta })
  for (const delta of deltas) frames.push({ data: { delta } })
  frames.push({
    data: { delta: '' },
    usage,
    finish_reason: 'stop',
    model: AGENT_MODEL,
  })
  const metadata = {}
  for (const frame of frames) deepMerge(metadata, frame)
  return {
    mode: 'sse',
    status_code: 200,
    headers: sseHeaders(requestId),
    frame_count: frames.length,
    metadata,
    frames, // frames[-50:]（target_api.py:405）
    content: deltas.join(''),
  }
}

// ---------------------------------------------------------------- 工具调用（上游自由形态，前端按 phase 聚合）
function call(tool, name, phase, ms, extra) {
  return { tool, name, phase, latency_ms: ms, status: 'OK', ...extra }
}

const T1_STEPS = [
  call('planner', '意图规划', '规划', 118, { args: { query: '查一下这个月账单里的总流量是多少' }, result: { intent: '流量总额查询', need_db: true } }),
  call('sql_writer', '生成取数 SQL', '生成 SQL', 226, { query: 'SELECT SUM(gprs_total) AS total FROM flow_daily WHERE account_id = :account_id AND month = :month', result: { sql_valid: true } }),
  call('db_query', '账单库查询', '执行查询', 342, { capability: 'billing_flow_sum', args: { account_id: 'A10086', month: '2026-10' }, result: { rows: [{ total_flow: 2652.1 }], row_count: 1 } }),
  call('caliber_check', '口径校验', '口径校验', 74, { result: { caliber: 'gprs_total（不含定向）', ambiguous: true } }),
  call('composer', '组织回复', '组织回复', 152, { result: { answer_chars: 46 } }),
]

const T2_STEPS = [
  call('planner', '意图规划', '规划', 96, { args: { query: '需要含定向流量的口径' }, result: { intent: '流量口径重算', need_db: true } }),
  call('sql_writer', '生成取数 SQL', '生成 SQL', 204, { query: 'SELECT SUM(gprs_total + directed_flow) AS total FROM flow_daily WHERE account_id = :account_id AND month = :month', result: { sql_valid: true } }),
  call('db_query', '账单库查询', '执行查询', 318, { capability: 'billing_flow_sum_incl_directed', args: { account_id: 'A10086', month: '2026-10' }, result: { rows: [{ total_flow: 2741.3 }], row_count: 1 } }),
  call('composer', '组织回复', '组织回复', 141, { result: { answer_chars: 30 } }),
]

const T3_STEPS = [
  call('planner', '意图规划', '规划', 104, { args: { query: '和上月比呢？' }, result: { intent: '环比对比', need_db: true } }),
  call('sql_writer', '生成取数 SQL', '生成 SQL', 218, { query: 'SELECT month, SUM(gprs_total) FROM flow_daily GROUP BY month', result: { sql_valid: true } }),
  call('db_query', '账单库查询', '执行查询', 398, { capability: 'billing_flow_monthly', args: { account_id: 'A10086', metric: 'gprs_total' }, result: { rows: [{ month: '2026-09', flow: 2360.4 }, { month: '2026-10', flow: 2652.1 }], row_count: 2 } }),
  call('caliber_check', '口径校验', '口径校验', 69, { status: 'WARN', result: { caliber: 'gprs_total（不含定向）', warn: '对话中已确认含定向口径，但查询仍取 gprs_total' } }),
  call('composer', '组织回复', '组织回复', 166, { result: { answer_chars: 31 } }),
]

// ---------------------------------------------------------------- 规则检查（rule_evaluator.py 真实输出形态）
/** 空配置时 evaluate_rules 仍落 rule_check（executor.py:266-270 逐轮恒调用） */
const RULE_CHECK_EMPTY = {
  passed: true,
  checks: [],
  extracted: { amounts: [], counts: [], dates: [] },
}
/** T3 文本提不出金额/笔数/完整日期（金额正则只认 ¥/元/万，rule_evaluator.py:13-23） */
const T3_RULE_CHECK = {
  passed: false,
  checks: [
    {
      type: 'must_contain',
      label: '必须包含「2,741.3」', // label 自动生成（rule_evaluator.py:257）
      expected: '2,741.3',
      actual: null, // must_contain 的 actual 恒 None（rule_evaluator.py:259）
      passed: false,
      evidence: '', // 未命中时 _evidence 返回空串（rule_evaluator.py:302-303）
      mode: '',
      bound_name: null,
      reason: '',
    },
    {
      type: 'must_contain',
      label: '必须包含「环比」',
      expected: '环比',
      actual: null,
      passed: true,
      evidence: '本月总流量 2,652.1 GB，环比上升 12.4%。', // _evidence 40 字符窗
      mode: '',
      bound_name: null,
      reason: '',
    },
  ],
  extracted: { amounts: [], counts: [], dates: [] },
}

// ---------------------------------------------------------------- 轮次
/*
 * 时序自洽（created_at = 响应完成时刻 + 落库开销）：
 * 10:12:31.000 执行开始 → T1 +2.21s → 33.210 落库
 * → T2 +1.58s → 34.880 落库 → T3 +3.05s → 38.050 落库
 * → 裁判 LLM（3410 tokens，~11s）→ 49.006 → 自检 +1.46s → performed_at 10:12:50
 * → 落库收尾 → 51.845（墙钟 20845.3ms = 51.845 - 31.0）
 */
const TURNS = [
  {
    id: 91,
    turn_index: 1,
    query: '查一下这个月账单里的总流量是多少',
    input_source: 'STATIC', // executor.py:272
    simulator_thought: null, // 静态多轮无模拟器（_build_record 不含 simulator 字段）
    simulator_status: null,
    hidden_context_released: null,
    rendered_prompt: null,
    request_payload: requestPayload('查一下这个月账单里的总流量是多少', 'req-7f3ac2e1'),
    response_raw: sseResponse('req-7f3ac2e1', {
      deltas: [
        '您本月（2026-10）的总流量为 2,652.1 GB。',
        '请问需要区分套餐内与套餐外口径吗？',
      ],
      usage: { prompt_tokens: 3842, completion_tokens: 615 },
      // 首帧白盒元数据：$.data.* 全命中（extractor.py:14-22）
      meta: {
        session_id: SESSION_ID,
        intent: '流量总额查询',
        generated_sql: 'SELECT SUM(gprs_total) AS total FROM flow_daily WHERE account_id = :account_id AND month = :month',
        api_params: { account_id: 'A10086', month: '2026-10', metric: 'gprs_total' },
        raw_db_data: { rows: [{ total_flow: 2652.1 }], row_count: 1 },
        tool_calls: T1_STEPS,
      },
    }),
    final_answer: '您本月（2026-10）的总流量为 2,652.1 GB。请问需要区分套餐内与套餐外口径吗？',
    session_id: SESSION_ID,
    intent: '流量总额查询',
    generated_sql: 'SELECT SUM(gprs_total) AS total FROM flow_daily WHERE account_id = :account_id AND month = :month',
    api_params: { account_id: 'A10086', month: '2026-10', metric: 'gprs_total' },
    raw_db_data: { rows: [{ total_flow: 2652.1 }], row_count: 1 },
    tool_calls: T1_STEPS,
    evidence: null, // evidence 列从不写入（已知现状）
    ttft_ms: 812, // SSE 轮有独立 TTFT（target_api.py:390）
    latency_ms: 2210,
    rule_check: RULE_CHECK_EMPTY, // 本轮未配 expected_rules → checks 空 → 界面「未判定」
    simulator_prompt_tokens: 0,
    simulator_completion_tokens: 0,
    created_at: msIso(33, 210),
    // TurnTraceOut（schemas/task.py:256-280）到此为止：API 序列化层不存在轮次级判分键
  },
  {
    id: 92,
    turn_index: 2,
    query: '需要含定向流量的口径',
    input_source: 'STATIC',
    simulator_thought: null,
    simulator_status: null,
    hidden_context_released: null,
    rendered_prompt: null,
    request_payload: requestPayload('需要含定向流量的口径', 'req-9d20b8a4'),
    response_raw: sseResponse('req-9d20b8a4', {
      deltas: [
        '好的，已按含定向流量口径',
        '为您统计：本月总流量 2,741.3 GB。',
      ],
      usage: { prompt_tokens: 4126, completion_tokens: 388 },
      meta: {
        session_id: SESSION_ID,
        intent: '流量口径重算',
        generated_sql: 'SELECT SUM(gprs_total + directed_flow) AS total FROM flow_daily WHERE account_id = :account_id AND month = :month',
        api_params: { account_id: 'A10086', month: '2026-10', metric: 'gprs_total+directed' },
        raw_db_data: { rows: [{ total_flow: 2741.3 }], row_count: 1 },
        tool_calls: T2_STEPS,
      },
    }),
    final_answer: '好的，已按含定向流量口径为您统计：本月总流量 2,741.3 GB。',
    session_id: SESSION_ID,
    intent: '流量口径重算',
    generated_sql: 'SELECT SUM(gprs_total + directed_flow) AS total FROM flow_daily WHERE account_id = :account_id AND month = :month',
    api_params: { account_id: 'A10086', month: '2026-10', metric: 'gprs_total+directed' },
    raw_db_data: { rows: [{ total_flow: 2741.3 }], row_count: 1 },
    tool_calls: T2_STEPS,
    evidence: null,
    ttft_ms: 640,
    latency_ms: 1580,
    rule_check: RULE_CHECK_EMPTY,
    simulator_prompt_tokens: 0,
    simulator_completion_tokens: 0,
    created_at: msIso(34, 880),
  },
  {
    id: 93,
    turn_index: 3,
    query: '和上月比呢？',
    input_source: 'STATIC',
    simulator_thought: null,
    simulator_status: null,
    hidden_context_released: null,
    rendered_prompt: null,
    request_payload: requestPayload('和上月比呢？', 'req-b41d09f7'),
    response_raw: sseResponse('req-b41d09f7', {
      deltas: [
        '本月（2026-10）总流量 2,652.1 GB，较 2026 年 9 月的 2,360.4 GB 上升 291.7 GB，环比 +12.4%，日均约 85.6 GB（9 月日均 78.7 GB）。',
        '其中套餐内流量 1,984.6 GB、套餐外流量 667.5 GB，套餐外占比 25.2%；按网络类型拆分，4G 1,120.3 GB、5G 1,531.8 GB，5G 占比 57.8%。',
        '统计口径为 flow_daily 表按 account_id 聚合的 gprs_total 字段，已剔除测试卡与物联网卡，数据更新至 2026-10-31 23:59，与账单出账口径（自然月、按天累加）一致。',
        '需要说明的是，本次统计沿用了上一轮的套餐口径，定向流量（视频类免流包）未计入；9 月基数同样为套餐口径，因此环比可比，但若用于对外账单核对，数值会低于账单总量，差额即定向流量部分。',
        '若你需要对外出具账单口径，我可以按含定向流量的方式重新统计后再给你一版，并同步给出两种口径的差异说明。',
        '另外如需按日拆分，可以再拉近 30 天的日维度曲线，标注峰值日与套餐外超额日，便于定位异常；若还需按套餐档位或家庭成员卡分摊，也可以给出对应拆分。',
      ],
      usage: { prompt_tokens: 4480, completion_tokens: 720 },
      meta: {
        session_id: SESSION_ID,
        intent: '环比对比',
        generated_sql: 'SELECT month, SUM(gprs_total) AS flow FROM flow_daily WHERE account_id = :account_id AND month IN (:m1, :m2) GROUP BY month',
        api_params: { account_id: 'A10086', months: ['2026-09', '2026-10'], metric: 'gprs_total' },
        raw_db_data: { rows: [{ month: '2026-09', flow: 2360.4 }, { month: '2026-10', flow: 2652.1 }], row_count: 2 },
        tool_calls: T3_STEPS,
      },
    }),
    final_answer: '本月（2026-10）总流量 2,652.1 GB，较 2026 年 9 月的 2,360.4 GB 上升 291.7 GB，环比 +12.4%，日均约 85.6 GB（9 月日均 78.7 GB）。其中套餐内流量 1,984.6 GB、套餐外流量 667.5 GB，套餐外占比 25.2%；按网络类型拆分，4G 1,120.3 GB、5G 1,531.8 GB，5G 占比 57.8%。统计口径为 flow_daily 表按 account_id 聚合的 gprs_total 字段，已剔除测试卡与物联网卡，数据更新至 2026-10-31 23:59，与账单出账口径（自然月、按天累加）一致。需要说明的是，本次统计沿用了上一轮的套餐口径，定向流量（视频类免流包）未计入；9 月基数同样为套餐口径，因此环比可比，但若用于对外账单核对，数值会低于账单总量，差额即定向流量部分。若你需要对外出具账单口径，我可以按含定向流量的方式重新统计后再给你一版，并同步给出两种口径的差异说明。另外如需按日拆分，可以再拉近 30 天的日维度曲线，标注峰值日与套餐外超额日，便于定位异常；若还需按套餐档位或家庭成员卡分摊，也可以给出对应拆分。',
    session_id: SESSION_ID,
    intent: '环比对比',
    generated_sql: 'SELECT month, SUM(gprs_total) AS flow FROM flow_daily WHERE account_id = :account_id AND month IN (:m1, :m2) GROUP BY month',
    api_params: { account_id: 'A10086', months: ['2026-09', '2026-10'], metric: 'gprs_total' },
    raw_db_data: { rows: [{ month: '2026-09', flow: 2360.4 }, { month: '2026-10', flow: 2652.1 }], row_count: 2 },
    tool_calls: T3_STEPS,
    evidence: null,
    ttft_ms: 755,
    latency_ms: 3050,
    rule_check: T3_RULE_CHECK, // 本轮配了 expected_rules（快照 turns[2]）
    simulator_prompt_tokens: 0,
    simulator_completion_tokens: 0,
    created_at: msIso(38, 50),
  },
]

// ---------------------------------------------------------------- 裁判（judge.py:33-58 / executor.py:746-761）
const DIMENSION_SCORES = { data_accuracy: 20, logic_consistency: 16, goal_completion: 12.5, tone_clarity: 10 }
// 生产原文维度标签（judge.py:26-29 DIMENSION_LABELS）
const DIMENSION_LABELS = {
  data_accuracy: '数据准确性与防幻觉',
  logic_consistency: '取数口径与逻辑一致性',
  goal_completion: '目标达成与主动澄清',
  tone_clarity: '服务规范与话术清晰度',
}
const JUDGE_REASON =
  '第三轮在口径已被明确为「含定向流量」后，仍使用不含定向口径的 2,652.1 GB 作答，与期望 2,741.3 GB 不符，差额 89.2 GB 恰为本账单定向流量合计，说明模型沿用了上一轮的旧口径而未重新计算；环比数值由该错误基数推导而来，因此同样偏小 2.4%。取数工具在口径校验步骤已返回「total_scope=mixed」告警，模型未采纳也未在回复中说明数值的统计范围，用户无法判断该数字是否包含定向流量。前两轮的澄清与口径确认处理正确，追问响应完整，语气清晰度无问题。本轮问题集中在数据准确度与逻辑一致性，目标完成度因最终数值错误而扣分，建议在工具返回告警时先复核口径再作答。从证据链看，工具返回的 row_count 与明细行数一致，SQL 本身无误，问题出在口径选择而非取数能力。若按含定向流量重算，2,652.1 GB 会修正为 2,741.3 GB，环比也应同步修正为 +16.1%。建议在工具返回 total_scope 告警时先复核口径再作答，避免同类问题在后续轮次重复出现。此外，前两轮已建立的口径共识应在后续轮次持续生效，模型在多轮对话中的上下文保持仍需加强；建议把口径确认结果写入会话级备注，取数前自动带出校验。'

const JUDGES = [
  {
    id: 1,
    model_config_id: 3,
    model_name: 'glm-4.7-0414',
    pass_flag: false,
    total_score: 58.5,
    dimension_scores: DIMENSION_SCORES,
    error_tags: ['数值错误/数据幻觉', '口径遗漏'],
    reason: JUDGE_REASON,
    raw_response: {
      // 裁判 LLM 原始返回（judge.py:197 校验 total_score / dimension_scores / pass 至少一项）
      pass: false,
      total_score: 58.5,
      dimension_scores: DIMENSION_SCORES,
      error_tags: ['数值错误/数据幻觉', '口径遗漏'],
      reason: JUDGE_REASON,
    },
    judge_prompt:
      '你是话费账单问答的裁决裁判。根据用户目标、对话记录与期望答案，按数据准确度(40)/逻辑一致性(30)/目标完成度(20)/语气清晰度(10)评分，给出 error_tags 与理由。通过线：75 分且规则硬校验全部通过。',
    prompt_tokens: 1980,
    completion_tokens: 1430,
    created_at: msIso(49, 6),
  },
]

// ---------------------------------------------------------------- 执行详情
/**
 * 自检 Prompt：后端 self_check.py 渲染后的完整模板（多段【】，整段作为一次模型调用发出），
 * prompt 与 request_payload.query 共用同一份文本，避免两处口径不一致。
 */
const SELF_CHECK_PROMPT = [
  '你是 Agent。请在当前会话内完成一次失败追问自检，不要引入新的外部数据。',
  '',
  '【失败轮次】T3 · 流量口径',
  '【用户问题】这个月的流量都算上了吧？含不含定向？',
  '【本轮回答】本月总流量 2,652.1 GB，其中套餐内 2,102.4 GB、套餐外 549.7 GB。',
  '【期望口径】统计需包含定向流量 89.2 GB，总计 2,741.3 GB。',
  '【判分结论】规则「统计口径必须包含定向流量」未通过；裁判 3.0 / 5.0，口径一致性 2.4 / 5.0。',
  '【差异明细】总量 -89.2 GB（-3.3%）；套餐内、套餐外明细无差异。',
  '',
  '【历史轮次摘要】',
  '· T1 账单汇总：通过，规则 4/4；',
  '· T2 套餐外明细：通过，规则 3/3；',
  '· T3 流量口径：本轮失败，规则 3/4。',
  '',
  '【上下文】',
  '· 会话 sess-20261123-8e21c4，执行 #900001，模型 wanjuan-agent-v2.3；',
  '· 上轮 SQL 过滤：account_id IN (…) AND month=2026-10；',
  '· 数据源 billing.usage 更新于 2026-11-23 10:38，包含 directional_flag 字段；',
  '· 同一会话内已追加 1 轮，不参与用例通过判定。',
  '',
  '【请按顺序回答】',
  '1. 差异产生的直接原因，以及属于取数口径、SQL 条件还是表达问题；',
  '2. 给出可执行的修正建议（例如 directional_flag 应该取什么值）；',
  '3. 说明后续遇到同类问题时如何避免；',
  '4. 结论控制在 200 字以内，先给结论再给依据；',
  '5. 不要重复道歉，不要编造未提供的数据，不要输出与问题无关的寒暄。',
  '',
  '【输出格式】',
  '结论：……',
  '依据：……',
  '修正建议：……',
].join('\n')

const EXECUTION = {
  id: EXECUTION_ID,
  task_id: TASK_ID,
  task_name: '【Trace 演示】10 月账单口径回归（夜间批）',
  case_id: 900211,
  case_code: 'BILL-FLOW-0421',
  case_name: '10 月流量口径多轮查询',
  case_version: 7,
  source: 'BATCH', // enums.py:106
  status: 'FAIL', // enums.py:70；GOAL/COMPLETED 类终止且未通过时（executor.py:524）
  attempt_no: 1,
  attempt_count: 1, // ExecutionListItem 恒返回（Pydantic 默认 1）
  error_kind: null,
  pass_flag: false,
  total_score: 58.5,
  termination_status: 'COMPLETED', // 静态多轮恒 COMPLETED（executor.py:287）
  turns_used: 3,
  latency_ms: 20845.3, // 整通墙钟（executor.py:157,473），含被测调用+裁判+自检：51.845-31.0
  ttft_ms: 735.67, // 各轮 TTFT 均值（executor.py:461-466）：avg(812,640,755)，SSE 轮有独立 TTFT
  simulator_tokens: 0, // 静态多轮无模拟器
  judge_tokens: 3410, // 1980+1430
  grading_mode: 'LEGACY',
  lucky_pass: null,
  root_cause: null,
  started_at: msIso(31, 0),
  finished_at: msIso(51, 845),
  environment_id: 12,
  environment_name: '联调环境 · 账单中台',
  max_turns: 5, // _create_execution 从快照落库（B3）
  simulator_template_version: null,
  judge_template_version: 4,
  rule_result: {
    // 静态多轮执行级 = merge_rule_results（executor.py:288）：
    // label 带 T{index} 前缀（rule_evaluator.py:317），extracted 为空 dict
    passed: false,
    checks: T3_RULE_CHECK.checks.map((check) => ({ ...check, label: `T3 · ${check.label}` })),
    extracted: {},
  },
  judge_result: {
    // judge.py:326-340 aggregate 键形（LEGACY 无 mode 键）
    pass: false,
    total_score: 58.5,
    dimension_scores: DIMENSION_SCORES,
    dimension_labels: DIMENSION_LABELS,
    error_tags: ['数值错误/数据幻觉', '口径遗漏'],
    reason: `【glm-4.7-0414】58.5分：${JUDGE_REASON}`, // judge.py:320-322 拼装格式
    strategy: 'AVG', // JudgeStrategy 枚举 AVG/VETO，默认 AVG（context.py:242）
    threshold: 75, // 通过线随执行落库（前端 passLineOf 第一优先级）
    judge_count: 1,
    policy_notes: ['规则硬校验未通过，最终判定为不通过'], // executor.py:511 固定文案
    errors: [],
    simulator_tokens: 0,
    judge_tokens: 3410,
  },
  error_message: null,
  case_snapshot: {
    // build_snapshot（snapshot.py:105-119）：SNAPSHOT_FIELDS 全字段恒写入（None 也落键），
    // 末尾追加 id / request_variables（字符串化）/ verified_facts（LEGACY 空账本为 []）
    code: 'BILL-FLOW-0421',
    name: '10 月流量口径多轮查询',
    description: '静态多轮核对 10 月账单总流量：口径澄清后追问环比，验证含定向口径的最终答复。',
    case_type: 'STATIC_MULTI',
    status: 'ACTIVE',
    priority: 'P1',
    tags: ['口径', '多轮', '流量'],
    environment_id: 12,
    request_variables: { account_id: 'A10086', billing_month: '2026-10', province: '浙江' },
    query: null, // 固定输入仅 SINGLE 使用；多轮走 turns
    expected_answer: '总流量 2,741.3 GB（含定向 89.2 GB），环比上升约 9%',
    expected_rules: null, // 会话级规则不配；轮级规则在 turns 内逐轮配置
    turns: [
      // TurnItem 键形（schemas/case.py:120-127）；expected_answer / expected_rules 逐轮可配
      { index: 1, query: '查一下这个月账单里的总流量是多少' },
      { index: 2, query: '需要含定向流量的口径' },
      {
        index: 3,
        query: '和上月比呢？',
        expected_answer: '含定向口径总流量 2,741.3 GB；环比上升约 9%',
        expected_rules: { must_contain: ['2,741.3', '环比'] },
      },
    ],
    session_expected: {
      expected_answer: '整通对话结束时，用户应得到含定向口径的总流量与环比结论',
    },
    fail_fast: false, // 列默认 False（test_case.py:68）
    user_goal: '核实 10 月账单在含定向流量口径下的总流量与环比',
    initial_query: null, // 动态多轮专用，静态为空
    persona_description: null, // 同上
    hidden_context: [
      // HiddenContextItem 键形（schemas/case.py:274-277）
      { key: 'caliber_hint', content: '该用户 10 月账单含定向流量 89.2 GB，问「总流量」时默认应答含定向流量的总口径。', release_hint: '口径' },
    ],
    max_turns: 5,
    terminate_conditions: null, // 动态多轮专用，静态为空
    simulator_template_id: null, // 静态多轮无模拟器
    simulator_template_strategy: null,
    simulator_template_version: null,
    judge_template_id: 7,
    judge_template_strategy: 'LATEST', // 列默认（test_case.py:89）
    judge_template_version: null, // LATEST 策略不锁定版本，执行时解析为 4
    prompt_overrides: null, // 无单用例覆写
    judge_model_ids: [3], // 裁判模型 id 列表（test_case.py:94）
    judge_strategy: 'AVG',
    pass_threshold: 75,
    grading_mode: 'LEGACY',
    input_type: 'CLEAR', // 列默认（test_case.py:108-110）
    as_of: '2026-10-31',
    snapshot_id: null,
    answer_envelope: null, // LEGACY 模式不配包络
    response_contract: null,
    plan_spec: null,
    risk_level: 'LOW', // 列默认（test_case.py:122）
    owner: 'qa_wang',
    review_status: 'APPROVED', // ReviewStatus 枚举（enums.py:205）
    dataset_version: 'v1', // 列默认（test_case.py:128）
    dedupe_hash: null,
    id: 900211,
    verified_facts: [], // build_verified_ledger(None) → []（facts.py:87-90）
  },
  trace_uid: 'e2740e5a-9b31-4c8a-a1f4-0d83c2b7e602', // str(uuid.uuid4()) 裸 UUID（executor.py:654）
  turns: TURNS,
  judges: JUDGES,
  answer_grading: null, // LEGACY 模式无契约判分
  fact_results: null,
  diagnostics: null,
  grader_version: null,
  as_of: '2026-10-31',
  self_check: {
    status: 'DONE',
    trigger: 'LLM 裁决不通过', // self_check.py:107 固定文案
    turn_index: 3, // 归属到原对话最后一轮（B4）
    session_id: SESSION_ID,
    template_code: 'SELF_CHECK_DEFAULT',
    template_name: '失败追问自检 · 账单口径',
    template_version: 3,
    prompt: SELF_CHECK_PROMPT,
    conclusion: [
      '【自检结论】对照评测裁判理由与我上一轮的回答，我确认本轮存在错误：',
      '1. 出错位置：上一轮回答中的总流量 2,652.1 GB 与底层用量明细不一致，属数据准确性问题；',
      '2. 直接原因：生成回答时未严格回查系统返回的取数结果，未把定向流量 89.2 GB 并入合计数；',
      '3. 修正方式：重新回答时会先以底层数据为准，逐项核对套餐内、套餐外与定向流量后再组织话术，并主动标注口径来源。',
    ].join('\n'),
    request_payload: requestPayload(SELF_CHECK_PROMPT, 'req-c7f4e221'),
    response_raw: sseResponse('req-c7f4e221', {
      // 自检同走 adapter.chat → 同为 SSE 形态（环境 is_stream=true）
      deltas: [
        '2,652.1 GB 是套餐内与套餐外流量的合计，未包含定向流量。',
        '若需要含定向口径，总数应为 2,741.3 GB。',
      ],
      usage: { prompt_tokens: 2140, completion_tokens: 305 },
      meta: { session_id: SESSION_ID },
    }),
    latency_ms: 1460,
    performed_at: secIso(50), // isoformat(timespec="seconds")
    // notes 为空时不落库（self_check.py:74-75 + to_dict 丢 None/空）
  },
  raw_request_response: TURNS.map((turn) => ({
    // serializers.py:200-207：三键形态（judge_prompt 仅在 /raw 端点）
    turn: turn.turn_index,
    request: turn.request_payload,
    response: turn.response_raw,
  })),
}

// ---------------------------------------------------------------- 通过用例（self_check=SKIPPED 形态）
/*
 * 执行 #900002：同一任务的通过用例。对照生产链路：
 * - 判定链：judge pass（92 ≥ 75）且规则全过 → pass_flag=true / status=PASS（executor.py:513,524）；
 * - self_check：任务开了 enable_self_check，但「LLM 裁决通过，未触发追问自检」
 *   （self_check.py:128），skipped() 只落 {status, skip_reason} 两键（to_dict 丢 None）；
 * - 时序：31.400 开始 → T1 +1.42s → 32.820 → T2 +1.98s → 34.800 → 裁判 ~3.5s → 38.300
 *   → 收尾 38.900（墙钟 7500.0ms）。
 */
const P_DIMENSION_SCORES = { data_accuracy: 38, logic_consistency: 27, goal_completion: 17, tone_clarity: 10 }
const P_JUDGE_REASON = '两轮对话完整覆盖话费总额与费用项数，数值与账单一致，口径清晰，用户目标达成。'

const P_RULE_CHECK_T2 = {
  passed: true,
  checks: [
    {
      type: 'must_contain',
      label: '必须包含「86.40 元」',
      expected: '86.40 元',
      actual: null,
      passed: true,
      evidence: '已为您复核：本月话费总额 86.40 元，4 项费用与账单一致。',
      mode: '',
      bound_name: null,
      reason: '',
    },
    {
      type: 'must_contain',
      label: '必须包含「4 项」',
      expected: '4 项',
      actual: null,
      passed: true,
      evidence: '本月话费总额 86.40 元，4 项费用与账单一致。',
      mode: '',
      bound_name: null,
      reason: '',
    },
  ],
  // 文本实际提取结果（rule_evaluator.py:165-172）：金额 86.40 元、笔数 4 项
  extracted: { amounts: ['86.40'], counts: [4], dates: [] },
}

const P_TURNS = [
  {
    id: 94,
    turn_index: 1,
    query: '查一下这个月话费总共多少',
    input_source: 'STATIC',
    simulator_thought: null,
    simulator_status: null,
    hidden_context_released: null,
    rendered_prompt: null,
    request_payload: requestPayload('查一下这个月话费总共多少', 'req-p1-a3f82c01', 'sess-20261123-9d40f7'),
    response_raw: sseResponse('req-p1-a3f82c01', {
      deltas: ['您本月（2026-10）的话费总额为 86.40 元，', '共 4 项费用。'],
      usage: { prompt_tokens: 3620, completion_tokens: 412 },
      meta: {
        session_id: 'sess-20261123-9d40f7',
        intent: '话费总额查询',
        generated_sql: 'SELECT SUM(fee_amount) AS total FROM bill_detail WHERE account_id = :account_id AND month = :month',
        api_params: { account_id: 'A10086', month: '2026-10' },
        raw_db_data: { rows: [{ total_fee: 86.4 }], row_count: 1 },
        tool_calls: [
          call('planner', '意图规划', '规划', 88, { args: { query: '查一下这个月话费总共多少' }, result: { intent: '话费总额查询', need_db: true } }),
          call('sql_writer', '生成取数 SQL', '生成 SQL', 196, { query: 'SELECT SUM(fee_amount) AS total FROM bill_detail WHERE account_id = :account_id AND month = :month', result: { sql_valid: true } }),
          call('db_query', '账单库查询', '执行查询', 301, { capability: 'billing_fee_sum', args: { account_id: 'A10086', month: '2026-10' }, result: { rows: [{ total_fee: 86.4 }], row_count: 1 } }),
          call('composer', '组织回复', '组织回复', 128, { result: { answer_chars: 28 } }),
        ],
      },
    }),
    final_answer: '您本月（2026-10）的话费总额为 86.40 元，共 4 项费用。',
    session_id: 'sess-20261123-9d40f7',
    intent: '话费总额查询',
    generated_sql: 'SELECT SUM(fee_amount) AS total FROM bill_detail WHERE account_id = :account_id AND month = :month',
    api_params: { account_id: 'A10086', month: '2026-10' },
    raw_db_data: { rows: [{ total_fee: 86.4 }], row_count: 1 },
    evidence: null,
    ttft_ms: 590,
    latency_ms: 1420,
    rule_check: {
      passed: true,
      checks: [],
      extracted: { amounts: ['86.40'], counts: [4], dates: [] },
    },
    simulator_prompt_tokens: 0,
    simulator_completion_tokens: 0,
    created_at: msIso(32, 820),
  },
  {
    id: 95,
    turn_index: 2,
    query: '帮我对一下都有哪几项费用',
    input_source: 'STATIC',
    simulator_thought: null,
    simulator_status: null,
    hidden_context_released: null,
    rendered_prompt: null,
    request_payload: requestPayload('帮我对一下都有哪几项费用', 'req-p2-c58e1b64', 'sess-20261123-9d40f7'),
    response_raw: sseResponse('req-p2-c58e1b64', {
      deltas: ['已为您复核：本月话费总额 86.40 元，', '4 项费用与账单一致。'],
      usage: { prompt_tokens: 3856, completion_tokens: 447 },
      meta: {
        session_id: 'sess-20261123-9d40f7',
        intent: '话费费用复核',
        generated_sql: 'SELECT fee_name, fee_amount FROM bill_detail WHERE account_id = :account_id AND month = :month',
        api_params: { account_id: 'A10086', month: '2026-10' },
        raw_db_data: { rows: [{ fee_name: '套餐月费', fee_amount: 39.0 }, { fee_name: '流量叠加包', fee_amount: 19.9 }, { fee_name: '增值业务', fee_amount: 15.0 }, { fee_name: '短信包', fee_amount: 12.5 }], row_count: 4 },
        tool_calls: [
          call('planner', '意图规划', '规划', 92, { args: { query: '帮我对一下都有哪几项费用' }, result: { intent: '话费费用复核', need_db: true } }),
          call('db_query', '账单库查询', '执行查询', 336, { capability: 'billing_fee_items', args: { account_id: 'A10086', month: '2026-10' }, result: { row_count: 4 } }),
          call('composer', '组织回复', '组织回复', 142, { result: { answer_chars: 26 } }),
        ],
      },
    }),
    final_answer: '已为您复核：本月话费总额 86.40 元，4 项费用与账单一致。',
    session_id: 'sess-20261123-9d40f7',
    intent: '话费费用复核',
    generated_sql: 'SELECT fee_name, fee_amount FROM bill_detail WHERE account_id = :account_id AND month = :month',
    api_params: { account_id: 'A10086', month: '2026-10' },
    raw_db_data: { rows: [{ fee_name: '套餐月费', fee_amount: 39.0 }, { fee_name: '流量叠加包', fee_amount: 19.9 }, { fee_name: '增值业务', fee_amount: 15.0 }, { fee_name: '短信包', fee_amount: 12.5 }], row_count: 4 },
    evidence: null,
    ttft_ms: 610,
    latency_ms: 1980,
    rule_check: P_RULE_CHECK_T2,
    simulator_prompt_tokens: 0,
    simulator_completion_tokens: 0,
    created_at: msIso(34, 800),
  },
]

// 白盒字段经 $.data.* 从首帧提取（extractor.py:14-22）：turn 顶层与帧内数据一致
for (const turn of P_TURNS) {
  turn.tool_calls = turn.response_raw.frames[0].data.tool_calls
}

const EXECUTION_900002 = {
  id: 900002,
  task_id: TASK_ID,
  task_name: '【Trace 演示】10 月账单口径回归（夜间批）',
  case_id: 900212,
  case_code: 'BILL-FEE-0422',
  case_name: '10 月话费总额多轮查询',
  case_version: 5,
  source: 'BATCH',
  status: 'PASS',
  attempt_no: 1,
  attempt_count: 1,
  error_kind: null,
  pass_flag: true,
  total_score: 92,
  termination_status: 'COMPLETED',
  turns_used: 2,
  latency_ms: 7500.0,
  ttft_ms: 600.0, // avg(590,610)
  simulator_tokens: 0,
  judge_tokens: 2980, // 1720+1260
  grading_mode: 'LEGACY',
  lucky_pass: null,
  root_cause: null,
  started_at: msIso(31, 400),
  finished_at: msIso(38, 900),
  environment_id: 12,
  environment_name: '联调环境 · 账单中台',
  max_turns: 5,
  simulator_template_version: null,
  judge_template_version: 4,
  rule_result: {
    passed: true,
    checks: P_RULE_CHECK_T2.checks.map((check) => ({ ...check, label: `T2 · ${check.label}` })),
    extracted: {},
  },
  judge_result: {
    pass: true,
    total_score: 92,
    dimension_scores: P_DIMENSION_SCORES,
    dimension_labels: DIMENSION_LABELS,
    error_tags: [],
    reason: `【glm-4.7-0414】92分：${P_JUDGE_REASON}`,
    strategy: 'AVG',
    threshold: 75,
    judge_count: 1,
    policy_notes: [],
    errors: [],
    simulator_tokens: 0,
    judge_tokens: 2980,
  },
  error_message: null,
  case_snapshot: {
    code: 'BILL-FEE-0422',
    name: '10 月话费总额多轮查询',
    description: '静态多轮核对 10 月话费总额与费用项数。',
    case_type: 'STATIC_MULTI',
    status: 'ACTIVE',
    priority: 'P1',
    tags: ['话费', '多轮'],
    environment_id: 12,
    request_variables: { account_id: 'A10086', billing_month: '2026-10', province: '浙江' },
    query: null,
    expected_answer: '话费总额 86.40 元，共 4 项费用',
    expected_rules: null,
    turns: [
      { index: 1, query: '查一下这个月话费总共多少' },
      {
        index: 2,
        query: '帮我对一下都有哪几项费用',
        expected_answer: '总额 86.40 元；4 项费用',
        expected_rules: { must_contain: ['86.40 元', '4 项'] },
      },
    ],
    session_expected: {
      expected_answer: '整通对话结束时，用户应得到话费总额与费用项数的一致结论',
    },
    fail_fast: false,
    user_goal: '核实 10 月账单话费总额与费用构成',
    initial_query: null,
    persona_description: null,
    hidden_context: [],
    max_turns: 5,
    terminate_conditions: null,
    simulator_template_id: null,
    simulator_template_strategy: null,
    simulator_template_version: null,
    judge_template_id: 7,
    judge_template_strategy: 'LATEST',
    judge_template_version: null,
    prompt_overrides: null,
    judge_model_ids: [3],
    judge_strategy: 'AVG',
    pass_threshold: 75,
    grading_mode: 'LEGACY',
    input_type: 'CLEAR',
    as_of: '2026-10-31',
    snapshot_id: null,
    answer_envelope: null,
    response_contract: null,
    plan_spec: null,
    risk_level: 'LOW',
    owner: 'qa_wang',
    review_status: 'APPROVED',
    dataset_version: 'v1',
    dedupe_hash: null,
    id: 900212,
    verified_facts: [],
  },
  trace_uid: '4a1b9c72-58d3-4e6f-b0a1-2c9e8d7f5134',
  turns: P_TURNS,
  judges: [
    {
      id: 2,
      model_config_id: 3,
      model_name: 'glm-4.7-0414',
      pass_flag: true,
      total_score: 92,
      dimension_scores: P_DIMENSION_SCORES,
      error_tags: [],
      reason: P_JUDGE_REASON,
      raw_response: {
        pass: true,
        total_score: 92,
        dimension_scores: P_DIMENSION_SCORES,
        error_tags: [],
        reason: P_JUDGE_REASON,
      },
      judge_prompt:
        '你是话费账单问答的裁决裁判。根据用户目标、对话记录与期望答案，按数据准确度(40)/逻辑一致性(30)/目标完成度(20)/语气清晰度(10)评分，给出 error_tags 与理由。通过线：75 分且规则硬校验全部通过。',
      prompt_tokens: 1720,
      completion_tokens: 1260,
      created_at: msIso(38, 300),
    },
  ],
  answer_grading: null,
  fact_results: null,
  diagnostics: null,
  grader_version: null,
  as_of: '2026-10-31',
  // 通过的用例：任务开了自检但未触发，只落 {status, skip_reason}（self_check.py:128 + to_dict 丢 None）
  self_check: {
    status: 'SKIPPED',
    skip_reason: 'LLM 裁决通过，未触发追问自检',
  },
  raw_request_response: P_TURNS.map((turn) => ({
    turn: turn.turn_index,
    request: turn.request_payload,
    response: turn.response_raw,
  })),
}

// ================================================================ 扩展演示数据（900003-900009）
/*
 * 判定页分支覆盖用执行记录。全部按后端真实落库键形构造（字段来源见 design/mock-demo-cases.md）：
 * - LEGACY：judge_result.threshold / reason / dimension_scores（judge.py:297-340）
 * - ANSWER_CONTRACT：judge_result.{mode,gates,quality,scorecard} + answer_grading.total_score
 * - 历史 RUBRIC：无 turn_trace、无 judge_result、无 answer_grading（当前库里 1226 条这种形态）
 * 任务 900102 未开启追问自检，因此这批记录 self_check 恒为 null（executor.py:159-190、723）。
 */
const DEMO_TASK_ID = 900102
const DEMO_TASK_NAME = '【Trace 演示】判定页分支覆盖 · 契约与历史'
const DEMO_SESSION = 'sess-20261123-b705d1'
const DEMO_CONTRACT_SESSION = 'sess-3751cc5df0f0'
const DEMO_ENV = { environment_id: 12, environment_name: '联调环境 · 账单中台' }
const CONTRACT_ENV = { environment_id: 2, environment_name: '电信计费问账环境（Mock 非流式 · Agent 报文）' }

/** 四维分值键形（judge.py:26-29） */
function dims(data, logic, goal, tone) {
  return { data_accuracy: data, logic_consistency: logic, goal_completion: goal, tone_clarity: tone }
}

const DEMO_BASE = {
  task_id: DEMO_TASK_ID,
  task_name: DEMO_TASK_NAME,
  source: 'BATCH',
  attempt_no: 1,
  attempt_count: 1,
  error_kind: null,
  termination_status: 'COMPLETED',
  simulator_tokens: 0,
  lucky_pass: null,
  root_cause: null,
  ...DEMO_ENV,
  simulator_template_version: null,
  judge_template_version: 4,
  error_message: null,
  fact_results: null,
  diagnostics: null,
  grader_version: null,
  as_of: '2026-10-31',
}

/** 用例快照（snapshot.py:105-119 SNAPSHOT_FIELDS 全字段恒写入） */
function demoSnapshot({
  id,
  code,
  name,
  description,
  tags,
  caseType,
  query = null,
  turns = [],
  expectedAnswer = null,
  userGoal,
  threshold,
  mode = 'LEGACY',
  env = DEMO_ENV,
  requestVariables = { account_id: 'A10086', billing_month: '2026-10', province: '浙江' },
  hiddenContext = [],
  answerContract = null,
  answerEnvelope = null,
}) {
  return {
    code,
    name,
    description,
    case_type: caseType,
    status: 'ACTIVE',
    priority: 'P1',
    tags,
    environment_id: env.environment_id,
    request_variables: requestVariables,
    query,
    expected_answer: expectedAnswer,
    expected_rules: null,
    turns,
    session_expected: { expected_answer: userGoal },
    fail_fast: false,
    user_goal: userGoal,
    initial_query: null,
    persona_description: null,
    hidden_context: hiddenContext,
    max_turns: 3,
    terminate_conditions: null,
    simulator_template_id: null,
    simulator_template_strategy: null,
    simulator_template_version: null,
    judge_template_id: 7,
    judge_template_strategy: 'LATEST',
    judge_template_version: null,
    prompt_overrides: null,
    judge_model_ids: [3],
    judge_strategy: 'AVG',
    pass_threshold: threshold,
    grading_mode: mode,
    input_type: 'CLEAR',
    as_of: '2026-10-31',
    snapshot_id: null,
    answer_envelope: answerEnvelope,
    response_contract: null,
    plan_spec: null,
    risk_level: 'LOW',
    owner: 'qa_wang',
    review_status: 'APPROVED',
    dataset_version: 'v1',
    dedupe_hash: null,
    id,
    verified_facts: [],
    ...(answerContract ? { answer_contract: answerContract } : {}),
  }
}

/** LEGACY 单裁判记录行（judge_result 表；pass_flag 只由裁判分数决定，规则门槛在执行级叠加） */
function legacyJudge({ id, score, dimensionScores, tags = [], reason, threshold, promptTokens, completionTokens, at }) {
  return {
    id,
    model_config_id: 3,
    model_name: 'glm-4.7-0414',
    pass_flag: score >= threshold,
    total_score: score,
    dimension_scores: dimensionScores,
    error_tags: tags,
    reason,
    raw_response: { pass: score >= threshold, total_score: score, dimension_scores: dimensionScores, error_tags: tags, reason },
    judge_prompt:
      '你是话费账单问答的裁决裁判。根据用户目标、对话记录与期望答案，按数据准确度(40)/逻辑一致性(30)/目标完成度(20)/语气清晰度(10)评分，给出 error_tags 与理由。',
    prompt_tokens: promptTokens,
    completion_tokens: completionTokens,
    created_at: at,
  }
}

/** LEGACY 执行级聚合（judge.py:326-340 键形；policy_notes 由硬门槛追加） */
function legacyAggregate({ score, dimensionScores, tags = [], reason, threshold, judgeTokens, policyNotes = [] }) {
  return {
    pass: score >= threshold && policyNotes.length === 0,
    total_score: score,
    dimension_scores: dimensionScores,
    dimension_labels: DIMENSION_LABELS,
    error_tags: tags,
    reason: `【glm-4.7-0414】${score}分：${reason}`,
    strategy: 'AVG',
    threshold,
    judge_count: 1,
    policy_notes: policyNotes,
    errors: [],
    simulator_tokens: 0,
    judge_tokens: judgeTokens,
  }
}

/** SSE 轮次（target_api.py + turn_trace 落库键形） */
function sseTurn({ id, index, query, reqId, deltas, promptTokens, completionTokens, intent, sql = null, apiParams = null, rawDb = null, steps = [], ruleCheck = RULE_CHECK_EMPTY, latency, ttft, at }) {
  const meta = { session_id: DEMO_SESSION, intent, tool_calls: steps }
  if (sql) meta.generated_sql = sql
  if (apiParams) meta.api_params = apiParams
  if (rawDb) meta.raw_db_data = rawDb
  return {
    id,
    turn_index: index,
    query,
    input_source: 'STATIC',
    simulator_thought: null,
    simulator_status: null,
    hidden_context_released: null,
    rendered_prompt: null,
    request_payload: requestPayload(query, reqId, DEMO_SESSION),
    response_raw: sseResponse(reqId, {
      deltas,
      usage: { prompt_tokens: promptTokens, completion_tokens: completionTokens },
      meta,
    }),
    final_answer: deltas.join(''),
    session_id: DEMO_SESSION,
    intent,
    generated_sql: sql,
    api_params: apiParams,
    raw_db_data: rawDb,
    tool_calls: steps,
    evidence: null,
    ttft_ms: ttft,
    latency_ms: latency,
    rule_check: ruleCheck,
    simulator_prompt_tokens: 0,
    simulator_completion_tokens: 0,
    created_at: at,
  }
}

/** 非流式轮次（response_raw = {mode:'json', body:{code,message,data}}） */
function jsonTurn({ id, index, query, convId, answer, intent, apiParams, rawDb, sql, latency, at }) {
  return {
    id,
    turn_index: index,
    query,
    input_source: 'STATIC',
    simulator_thought: null,
    simulator_status: null,
    hidden_context_released: null,
    rendered_prompt: null,
    request_payload: {
      method: 'POST',
      url: 'http://127.0.0.1:8002/api/v1/agent/chat',
      headers: { 'Content-Type': 'application/json', Authorization: '***' },
      body: {
        message: { type: 'text', agent: 'opencode', model: 'ds-xxx', content: query, sessionid: '' },
        conversationId: convId,
      },
    },
    response_raw: {
      body: { code: 0, message: 'success', data: { turn: index, answer, intent, sessionid: DEMO_CONTRACT_SESSION, api_params: apiParams, raw_db_data: rawDb, generated_sql: sql } },
      mode: 'json',
    },
    final_answer: answer,
    session_id: DEMO_CONTRACT_SESSION,
    intent,
    generated_sql: sql,
    api_params: apiParams,
    raw_db_data: rawDb,
    tool_calls: null,
    evidence: null,
    // 非流式没有首字延迟，target_api 与 latency 同值落库
    ttft_ms: latency,
    latency_ms: latency,
    rule_check: RULE_CHECK_EMPTY,
    simulator_prompt_tokens: 0,
    simulator_completion_tokens: 0,
    created_at: at,
  }
}

// ---------------------------------------------------------------- 900003 单轮 · 部分命中（分够但规则硬门槛未过）
const D1_STEPS = [
  call('planner', '意图规划', '规划', 132, { result: { intent: '账单金额核对', need_db: true } }),
  call('sql_writer', '生成取数 SQL', '生成 SQL', 214, { query: 'SELECT SUM(amount) AS bill_total, COUNT(*) AS item_count FROM telecom_bill_item WHERE phone_no = :phone AND period = :period', result: { sql_valid: true } }),
  call('db_query', '话费库查询', '执行查询', 296, { capability: 'bill_query', args: { phone_no: '13800138000', period: '2026-10' }, result: { bill_total: 268.5, item_count: 4 } }),
  call('composer', '组织回复', '组织回复', 168, { result: { answer_chars: 96 } }),
]
const D1_RULE_CHECK = {
  passed: false,
  checks: [
    { type: 'must_contain', label: '必须包含「268.50」', expected: '268.50', actual: null, passed: false, evidence: '', mode: '', bound_name: null, reason: '' },
    { type: 'must_contain', label: '必须包含「4 项」', expected: '4 项', actual: null, passed: true, evidence: '共 4 项费用：套餐月费 ¥199.00、国内流量超套费 ¥27.00', mode: '', bound_name: null, reason: '' },
  ],
  // 金额正则只认 ¥/元/万（rule_evaluator.py:13-23）
  extracted: { amounts: ['298.50', '199.00', '27.00', '32.50', '10.00'], counts: [4], dates: [] },
}
const D1_ANSWER = '13800138000 在 2026-10 的账单总额为 ¥298.50，共 4 项费用：套餐月费 ¥199.00、国内流量超套费 ¥27.00、国际漫游费 ¥32.50、增值业务费（彩铃） ¥10.00。'
const D1_REASON = '金额末两位与取数结果不一致（回复 298.50，工具返回 268.50），费用项数正确；其余话术清晰。目标达成度因金额错误扣分。'
const D1_TURN = sseTurn({
  id: 201,
  index: 1,
  query: '帮我核对一下 13800138000 在 2026-10 的账单总额和费用项数',
  reqId: 'req-d182a640',
  deltas: [D1_ANSWER],
  promptTokens: 4120,
  completionTokens: 540,
  intent: '账单金额核对',
  sql: 'SELECT SUM(amount) AS bill_total, COUNT(*) AS item_count FROM telecom_bill_item WHERE phone_no = :phone AND period = :period',
  apiParams: { phone_no: '13800138000', period: '2026-10' },
  rawDb: {
    phone_no: '13800138000',
    period: '2026-10',
    bill_total: 268.5,
    item_count: 4,
    items: [
      { name: '套餐月费', amount: 199.0 },
      { name: '国内流量超套费', amount: 27.0 },
      { name: '国际漫游费', amount: 32.5 },
      { name: '增值业务费（彩铃）', amount: 10.0 },
    ],
  },
  steps: D1_STEPS,
  ruleCheck: D1_RULE_CHECK,
  latency: 2840,
  ttft: 690,
  at: msIso(22, 840),
})

const EXECUTION_900003 = {
  ...DEMO_BASE,
  id: 900003,
  case_id: 900221,
  case_code: 'BILL-DATA-0431',
  case_name: '10 月账单金额与费用项核对（单轮）',
  case_version: 3,
  status: 'FAIL',
  pass_flag: false,
  total_score: 82,
  turns_used: 1,
  latency_ms: 7600.0,
  ttft_ms: 690,
  judge_tokens: 1280,
  grading_mode: 'LEGACY',
  started_at: msIso(20, 0),
  finished_at: msIso(27, 600),
  max_turns: 3,
  rule_result: {
    passed: false,
    checks: D1_RULE_CHECK.checks.map((check) => ({ ...check, label: `T1 · ${check.label}` })),
    extracted: {},
  },
  judge_result: legacyAggregate({
    score: 82,
    dimensionScores: dims(30, 24, 18, 10),
    tags: ['数值错误/数据幻觉'],
    reason: D1_REASON,
    threshold: 60,
    judgeTokens: 1280,
    policyNotes: ['规则硬校验未通过，最终判定为不通过'],
  }),
  case_snapshot: demoSnapshot({
    id: 900221,
    code: 'BILL-DATA-0431',
    name: '10 月账单金额与费用项核对（单轮）',
    description: '单轮核对账单总额与费用项数，验证数值落库与回复一致性。',
    tags: ['单轮', '金额核对'],
    caseType: 'SINGLE',
    query: '帮我核对一下 13800138000 在 2026-10 的账单总额和费用项数',
    expectedAnswer: '账单总额 ¥268.50，共 4 项费用。',
    userGoal: '确认 10 月账单总额与费用项数，且与底层明细一致',
    threshold: 60,
  }),
  trace_uid: 'c1f60b7d-2e94-4a83-9b60-5d71e8c4a230',
  turns: [D1_TURN],
  judges: [
    legacyJudge({
      id: 11,
      score: 82,
      dimensionScores: dims(30, 24, 18, 10),
      tags: ['数值错误/数据幻觉'],
      reason: D1_REASON,
      threshold: 60,
      promptTokens: 860,
      completionTokens: 420,
      at: msIso(26, 100),
    }),
  ],
  answer_grading: null,
  self_check: null,
  raw_request_response: [{ turn: 1, request: D1_TURN.request_payload, response: D1_TURN.response_raw }],
}

// ---------------------------------------------------------------- 900004 多轮 · 三轮均「未判定」+ 执行级失败
const D2_STEPS_1 = [
  call('planner', '意图规划', '规划', 108, { result: { intent: '流量用量查询', need_db: true } }),
  call('sql_writer', '生成取数 SQL', '生成 SQL', 196, { query: 'SELECT SUM(gprs_total) AS total FROM flow_daily WHERE account_id = :account_id AND month = :month', result: { sql_valid: true } }),
  call('db_query', '账单库查询', '执行查询', 312, { capability: 'billing_flow_sum', args: { account_id: 'A10086', month: '2026-10' }, result: { total_flow: 2652.1 } }),
  call('composer', '组织回复', '组织回复', 140, { result: { answer_chars: 34 } }),
]
const D2_STEPS_2 = [
  call('planner', '意图规划', '规划', 92, { result: { intent: '增值业务费查询', need_db: true } }),
  call('db_query', '账单库查询', '执行查询', 284, { capability: 'billing_vas_fee', args: { account_id: 'A10086', month: '2026-10' }, result: { items: [{ name: '彩铃', amount: 10.0 }] } }),
  call('composer', '组织回复', '组织回复', 132, { result: { answer_chars: 30 } }),
]
const D2_STEPS_3 = [
  call('planner', '意图规划', '规划', 104, { result: { intent: '超支判断', need_db: false } }),
  call('composer', '组织回复', '组织回复', 188, { result: { answer_chars: 52 } }),
]
const D2_ANSWER_1 = '您本月（2026-10）共使用流量 2,652.1 GB。'
const D2_ANSWER_2 = '本月增值业务费合计 ¥10.00（彩铃）。'
const D2_ANSWER_3 = '两项属于不同计费项，无法直接相加判断是否超支；建议按套餐额度分别核对。'
const D2_REASON =
  '流量与增值业务两项分别答对，但用户最后追问的「是否超支」没有得到可执行结论，也没有回到套餐额度给出判断；三轮均未主动澄清「超支」的口径。'
const D2_TURNS = [
  sseTurn({
    id: 202,
    index: 1,
    query: '这个月我一共用了多少流量？',
    reqId: 'req-d283a001',
    deltas: [D2_ANSWER_1],
    promptTokens: 3880,
    completionTokens: 320,
    intent: '流量用量查询',
    sql: 'SELECT SUM(gprs_total) AS total FROM flow_daily WHERE account_id = :account_id AND month = :month',
    apiParams: { account_id: 'A10086', month: '2026-10', metric: 'gprs_total' },
    rawDb: { rows: [{ total_flow: 2652.1 }], row_count: 1 },
    steps: D2_STEPS_1,
    latency: 2100,
    ttft: 620,
    at: msIso(7, 100),
  }),
  sseTurn({
    id: 203,
    index: 2,
    query: '增值业务扣了多少钱？',
    reqId: 'req-d283a002',
    deltas: [D2_ANSWER_2],
    promptTokens: 4010,
    completionTokens: 286,
    intent: '增值业务费查询',
    apiParams: { account_id: 'A10086', month: '2026-10', category: 'VAS' },
    rawDb: { items: [{ name: '彩铃', amount: 10.0 }], count: 1 },
    steps: D2_STEPS_2,
    latency: 2200,
    ttft: 588,
    at: msIso(9, 300),
  }),
  sseTurn({
    id: 204,
    index: 3,
    query: '这两项加起来算不算超支？',
    reqId: 'req-d283a003',
    deltas: [D2_ANSWER_3],
    promptTokens: 4230,
    completionTokens: 350,
    intent: '超支判断',
    steps: D2_STEPS_3,
    latency: 3300,
    ttft: 704,
    at: msIso(12, 600),
  }),
]

const EXECUTION_900004 = {
  ...DEMO_BASE,
  id: 900004,
  case_id: 900222,
  case_code: 'BILL-FLOW-0432',
  case_name: '10 月流量与增值业务多轮（判定不足）',
  case_version: 4,
  status: 'FAIL',
  pass_flag: false,
  total_score: 71,
  turns_used: 3,
  latency_ms: 17400.0,
  ttft_ms: 637.33,
  judge_tokens: 2410,
  grading_mode: 'LEGACY',
  started_at: msIso(5, 0),
  finished_at: msIso(22, 400),
  max_turns: 5,
  rule_result: { passed: true, checks: [], extracted: {} },
  judge_result: legacyAggregate({
    score: 71,
    dimensionScores: dims(26, 22, 15, 8),
    tags: ['未主动反问'],
    reason: D2_REASON,
    threshold: 80,
    judgeTokens: 2410,
  }),
  case_snapshot: demoSnapshot({
    id: 900222,
    code: 'BILL-FLOW-0432',
    name: '10 月流量与增值业务多轮（判定不足）',
    description: '三轮追问流量、增值业务费与超支判断；用例本身未配逐轮规则，轮次判定依赖执行级裁判。',
    tags: ['多轮', '流量', '超支'],
    caseType: 'STATIC_MULTI',
    turns: [
      { index: 1, query: '这个月我一共用了多少流量？' },
      { index: 2, query: '增值业务扣了多少钱？' },
      { index: 3, query: '这两项加起来算不算超支？' },
    ],
    expectedAnswer: '分别给出流量 2,652.1 GB 与增值业务费 ¥10.00，并按套餐额度给出是否超支的结论。',
    userGoal: '确认流量与增值业务费，并得到是否超支的明确判断',
    threshold: 80,
  }),
  trace_uid: '8bd2a64e-7f10-49c2-8e3d-1a6c5b90d417',
  turns: D2_TURNS,
  judges: [
    legacyJudge({
      id: 12,
      score: 71,
      dimensionScores: dims(26, 22, 15, 8),
      tags: ['未主动反问'],
      reason: D2_REASON,
      threshold: 80,
      promptTokens: 1580,
      completionTokens: 830,
      at: msIso(21, 0),
    }),
  ],
  answer_grading: null,
  self_check: null,
  raw_request_response: D2_TURNS.map((turn) => ({ turn: turn.turn_index, request: turn.request_payload, response: turn.response_raw })),
}

// ---------------------------------------------------------------- 900005 单轮 · 契约模式通过
const D3_CONTRACT = {
  must: [
    { id: 'total_a', kind: 'scalar', unit: 'CNY', label: '13800138000 账单总额', value: 268.5, tolerance: 0.01, verified_at: '2026-11-16T10:00:00', verified_by: 'system', evidence_ref: 'capability:bill_query', verification_status: 'VERIFIED' },
    { id: 'total_b', kind: 'scalar', unit: 'CNY', label: '13900139000 账单总额', value: 356.8, tolerance: 0.01, verified_at: '2026-11-16T10:00:00', verified_by: 'system', evidence_ref: 'capability:bill_query', verification_status: 'VERIFIED' },
  ],
  must_not: [
    { id: 'banned', kind: 'text', label: '禁止出现', value: '系统错误', verified_at: '2026-11-16T10:00:00', verified_by: 'system', verification_status: 'WAIVED' },
  ],
  tolerances: { money: 0.01, percent: 0.1 },
  clarity_rules: { max_ratio: 3.0, conclusion_first: true },
  extras_policy: 'lenient',
  forbidden_phrases: [],
}
const D3_ENVELOPE = {
  must: D3_CONTRACT.must,
  must_not: D3_CONTRACT.must_not,
  aliases: {},
  tolerances: D3_CONTRACT.tolerances,
  clarity_rules: D3_CONTRACT.clarity_rules,
  extras_policy: 'lenient',
  forbidden_phrases: [],
}
const D3_ANSWER = '仅为您分别查询到两条账单：13800138000 在 2026-10 的账单总额为 ¥268.50；13900139000 在 2026-10 的账单总额为 ¥356.80。如需逐项明细，请告知优先查看哪一个号码。'
const D3_QUALITY_REASON = '两个号码均给出正确账单总额，未把两者的数据混用；主动提示可继续查看明细。'
const D3_QUALITY = {
  pass: true,
  errors: [],
  reason: `【Mock 主裁判(mock-judge)】94.0分：${D3_QUALITY_REASON}`,
  strategy: 'AVG',
  threshold: 60.0,
  error_tags: [],
  judge_count: 1,
  total_score: 94.0,
  judge_tokens: 353,
  policy_notes: [],
  dimension_labels: DIMENSION_LABELS,
  dimension_scores: dims(40.0, 28.0, 18.0, 8.0),
  simulator_tokens: 0,
}
const D3_FACTS = [
  { fact_id: 'total_a', label: '13800138000 账单总额', kind: 'scalar', passed: true, expected: { value: 268.5, unit: 'CNY', tolerance: 0.01 }, actual: { value: 268.5, bound_name: '13800138000 账单总额' }, reason: '', is_gate: true, degraded: false, mode: 'value_only' },
  { fact_id: 'total_b', label: '13900139000 账单总额', kind: 'scalar', passed: true, expected: { value: 356.8, unit: 'CNY', tolerance: 0.01 }, actual: { value: 356.8, bound_name: '13900139000 账单总额' }, reason: '', is_gate: true, degraded: false, mode: 'value_only' },
]
const D3_TURN = jsonTurn({
  id: 205,
  index: 1,
  query: '帮我同时查一下 13800138000 和 13900139000 在 2026-10 的账单总额',
  convId: 'conv-c0e1fbdb5237',
  answer: D3_ANSWER,
  intent: 'MULTI_INTENT',
  apiParams: { period: '2026-10', phone_no: ['13800138000', '13900139000'] },
  rawDb: {
    query_type: 'MULTI_INTENT',
    period: '2026-10',
    accounts: [
      { phone_no: '13800138000', bill_total: 268.5, item_count: 4 },
      { phone_no: '13900139000', bill_total: 356.8, item_count: 3 },
    ],
  },
  sql: 'SELECT phone_no, period, SUM(amount) AS bill_total, COUNT(*) AS item_count FROM telecom_bill_item WHERE phone_no IN ($phone_a, $phone_b) AND period = $period GROUP BY 1,2;',
  latency: 329.3,
  at: msIso(55, 437),
})

const EXECUTION_900005 = {
  ...DEMO_BASE,
  ...CONTRACT_ENV,
  id: 900005,
  case_id: 900223,
  case_code: 'DEMO-CONTRACT-MULTI-INTENT',
  case_name: '契约模式 · 多意图查询（两个号码）',
  case_version: 2,
  status: 'PASS',
  pass_flag: true,
  total_score: 100,
  turns_used: 1,
  latency_ms: 329.3,
  ttft_ms: 329.3,
  judge_tokens: 353,
  grading_mode: 'ANSWER_CONTRACT',
  started_at: msIso(55, 108),
  finished_at: msIso(55, 437),
  max_turns: 5,
  rule_result: { passed: true, checks: [], extracted: {} },
  judge_result: {
    mode: 'ANSWER_CONTRACT',
    gates: { facts: true, extras: true, safety: true, behavior: true },
    notes: [],
    passed: true,
    quality: D3_QUALITY,
    scorecard: { clarity: 100.0, helpful_extra: 100.0, fact_completeness: 100.0, assumption_honesty: 100.0 },
    lucky_pass: false,
    root_cause: null,
    grader_version: 'answer-grader@0.2',
  },
  case_snapshot: demoSnapshot({
    id: 900223,
    code: 'DEMO-CONTRACT-MULTI-INTENT',
    name: '契约模式 · 多意图查询（两个号码）',
    description: '同时查询两个号码的账单总额，验证契约事实绑定与多意图不混用。',
    tags: ['答案契约', '多意图'],
    caseType: 'SINGLE',
    query: '帮我同时查一下 13800138000 和 13900139000 在 2026-10 的账单总额',
    expectedAnswer: '分别给出 13800138000 的 ¥268.50 与 13900139000 的 ¥356.80。',
    userGoal: '分别拿到两个号码的账单总额，不混用',
    threshold: 60,
    mode: 'ANSWER_CONTRACT',
    env: CONTRACT_ENV,
    requestVariables: { userid: '13800138000', custid: 'C0001' },
    answerContract: D3_CONTRACT,
    answerEnvelope: D3_ENVELOPE,
  }),
  trace_uid: '9f4c1d20-6b8a-4e57-9d31-70c2a5e8b613',
  turns: [D3_TURN],
  judges: [
    {
      id: 21,
      model_config_id: 9,
      model_name: 'Mock 主裁判(mock-judge)',
      pass_flag: true,
      total_score: 94,
      dimension_scores: dims(40.0, 28.0, 18.0, 8.0),
      error_tags: [],
      reason: D3_QUALITY_REASON,
      raw_response: { pass: true, total_score: 94, dimension_scores: dims(40.0, 28.0, 18.0, 8.0), error_tags: [], reason: D3_QUALITY_REASON },
      judge_prompt:
        '你是账单问答的裁决裁判。根据用户目标与对话记录，按数据准确度(40)/逻辑一致性(30)/目标完成度(20)/语气清晰度(10)评分；契约事实由契约引擎单独判定。',
      prompt_tokens: 210,
      completion_tokens: 143,
      created_at: msIso(55, 300),
    },
  ],
  answer_grading: {
    facts: D3_FACTS,
    gates: { facts: true, extras: true, safety: true, behavior: true },
    notes: [],
    extras: [],
    passed: true,
    behavior: { clarify_first: false, conclusion_first: true },
    degraded: false,
    scorecard: { clarity: 100.0, helpful_extra: 100.0, fact_completeness: 100.0, assumption_honesty: 100.0 },
    lucky_pass: false,
    root_cause: null,
    diagnostics: null,
    total_score: 100.0,
    grader_version: 'answer-grader@0.2',
  },
  self_check: null,
  raw_request_response: [{ turn: 1, request: D3_TURN.request_payload, response: D3_TURN.response_raw }],
}

// ---------------------------------------------------------------- 900006 单轮 · 契约门槛未过（LLM 分够 / 契约分 70）
const D4_CONTRACT = {
  must: [
    { id: 'total_amount', kind: 'scalar', unit: 'CNY', label: '账单总额', value: 268.5, aliases: ['本期账单总额', '账单合计', '账单金额', 'total_amount', 'bill_total'], tolerance: 0.01, verified_at: '2026-11-16T10:00:00', verified_by: 'system', evidence_ref: 'capability:bill_query', verification_status: 'VERIFIED' },
    { id: 'item_count', kind: 'scalar', unit: null, label: '费用项数', value: 4, aliases: ['费用项', '费用条目数', 'item_count', 'itemCount'], tolerance: 0, verified_at: '2026-11-16T10:00:00', verified_by: 'system', evidence_ref: 'capability:bill_query', verification_status: 'VERIFIED' },
  ],
  must_not: [
    { id: 'banned_amount', kind: 'text', label: '禁止出现', value: '999.00', verified_at: '2026-11-16T10:00:00', verified_by: 'system', verification_status: 'WAIVED' },
  ],
  tolerances: { money: 0.01, percent: 0.1 },
  clarity_rules: { max_ratio: 3.0, conclusion_first: true },
  extras_policy: 'lenient',
  forbidden_phrases: [],
}
const D4_ENVELOPE = {
  must: D4_CONTRACT.must,
  must_not: D4_CONTRACT.must_not,
  aliases: {},
  tolerances: D4_CONTRACT.tolerances,
  clarity_rules: D4_CONTRACT.clarity_rules,
  extras_policy: 'lenient',
  forbidden_phrases: [],
}
const D4_ANSWER = '手机号 13800138000（陈晓峰）2026-10 账单总额为 ¥999.00，共 4 项费用：套餐月费 ¥199.00、国内流量超套费 ¥27.00、国际漫游费 ¥32.50、增值业务费（彩铃） ¥10.00。'
const D4_QUALITY_REASON = '金额、笔数与底层 Raw DB Data 一致，但未与契约期望值比对，无法确认金额是否正确；话术清晰。'
const D4_QUALITY = {
  pass: true,
  errors: [],
  reason: `【Mock 主裁判(mock-judge)】94.0分：${D4_QUALITY_REASON}`,
  strategy: 'AVG',
  threshold: 60.0,
  error_tags: [],
  judge_count: 1,
  total_score: 94.0,
  judge_tokens: 346,
  policy_notes: [],
  dimension_labels: DIMENSION_LABELS,
  dimension_scores: dims(40.0, 28.0, 18.0, 8.0),
  simulator_tokens: 0,
}
const D4_SCORECARD = { clarity: 75.0, helpful_extra: 100.0, fact_completeness: 50.0, assumption_honesty: 100.0 }
const D4_FACTS = [
  {
    fact_id: 'total_amount',
    label: '账单总额',
    kind: 'scalar',
    passed: false,
    expected: { unit: 'CNY', names: ['账单总额', '本期账单总额', '账单合计', '账单金额', 'total_amount', 'bill_total'], value: 268.5, tolerance: 0.01, time_scope: null },
    actual: { mode: 'name_mismatch', numbers: [999.0, 199.0, 27.0, 32.5, 10.0, 4.0], bound_name: '账单总额', bound_value: 999.0 },
    reason: '名称「账单总额」绑定到 999.0，期望 268.5',
    is_gate: true,
    degraded: false,
    mode: 'name_mismatch',
    evidence_ref: '13800138000（陈晓峰）2026-10 账单总额为 ¥999.00，共 4 项费用',
    conflict_name: null,
  },
  {
    fact_id: 'item_count',
    label: '费用项数',
    kind: 'scalar',
    passed: true,
    expected: { unit: null, names: ['费用项', '费用条目数', 'item_count', 'itemCount'], value: 4, tolerance: 0, time_scope: null },
    actual: { mode: 'value_only', numbers: [999.0, 199.0, 27.0, 32.5, 10.0, 4.0], bound_name: null, bound_value: 4.0 },
    reason: '',
    is_gate: true,
    degraded: false,
    mode: 'value_only',
    evidence_ref: '共 4 项费用',
    conflict_name: null,
  },
]
const D4_TURN = jsonTurn({
  id: 206,
  index: 1,
  query: '帮我查一下 13800138000 在 2026-10 的错误金额账单',
  convId: 'conv-d4ee23e4272a',
  answer: D4_ANSWER,
  intent: 'BILL',
  apiParams: { period: '2026-10', phone_no: '13800138000' },
  rawDb: {
    items: [
      { name: '套餐月费', amount: 199.0 },
      { name: '国内流量超套费', amount: 27.0 },
      { name: '国际漫游费', amount: 32.5 },
      { name: '增值业务费（彩铃）', amount: 10.0 },
    ],
    period: '2026-10',
    customer: '陈晓峰',
    phone_no: '13800138000',
    bill_total: 999.0,
    item_count: 4,
    query_type: 'BILL',
    package_name: '5G畅享套餐199元档',
  },
  sql: 'SELECT phone_no, period, SUM(amount) AS bill_total, COUNT(*) AS item_count FROM telecom_bill_item WHERE phone_no = $phone AND period = $period GROUP BY 1,2;',
  latency: 82.43,
  at: msIso(40, 82),
})

const EXECUTION_900006 = {
  ...DEMO_BASE,
  ...CONTRACT_ENV,
  id: 900006,
  case_id: 900224,
  case_code: 'DEMO-CONTRACT-WRONG-AMOUNT',
  case_name: '契约模式 · 错误金额幻觉失败（Mock 注入）',
  case_version: 6,
  status: 'FAIL',
  pass_flag: false,
  total_score: 70,
  turns_used: 1,
  latency_ms: 82.43,
  ttft_ms: 82.43,
  judge_tokens: 346,
  grading_mode: 'ANSWER_CONTRACT',
  started_at: msIso(40, 0),
  finished_at: msIso(40, 82),
  max_turns: 5,
  rule_result: { passed: true, checks: [], extracted: {} },
  judge_result: {
    mode: 'ANSWER_CONTRACT',
    gates: { facts: false, extras: true, safety: false, behavior: true },
    notes: [],
    // 契约模式：LLM 质量分不参与通过判定，最终 pass 由 gates + answer_grading.total_score 决定
    passed: false,
    quality: D4_QUALITY,
    scorecard: D4_SCORECARD,
    lucky_pass: false,
    root_cause: 'HALLUCINATION',
    grader_version: 'answer-grader@0.2',
  },
  case_snapshot: demoSnapshot({
    id: 900224,
    code: 'DEMO-CONTRACT-WRONG-AMOUNT',
    name: '契约模式 · 错误金额幻觉失败（Mock 注入）',
    description: '底层数据被注入错误金额，契约事实按期望值判定失败，用于验证事实闸门与安全红线。',
    tags: ['答案契约', '幻觉', '安全红线'],
    caseType: 'SINGLE',
    query: '帮我查一下 13800138000 在 2026-10 的错误金额账单',
    expectedAnswer: '正确答案应为 ¥268.50；本用例用于验证契约事实闸门与安全红线。',
    userGoal: '拿到正确的账单总额，且不出现被禁止的错误金额',
    threshold: 60,
    mode: 'ANSWER_CONTRACT',
    env: CONTRACT_ENV,
    requestVariables: { userid: '13800138000', custid: 'C0001' },
    answerContract: D4_CONTRACT,
    answerEnvelope: D4_ENVELOPE,
  }),
  trace_uid: '3a7e90c6-4d51-4b28-b0f7-6c8e21d5a904',
  turns: [D4_TURN],
  judges: [
    {
      id: 22,
      model_config_id: 9,
      model_name: 'Mock 主裁判(mock-judge)',
      pass_flag: true,
      total_score: 94,
      dimension_scores: dims(40.0, 28.0, 18.0, 8.0),
      error_tags: [],
      reason: D4_QUALITY_REASON,
      raw_response: { pass: true, total_score: 94, dimension_scores: dims(40.0, 28.0, 18.0, 8.0), error_tags: [], reason: D4_QUALITY_REASON },
      judge_prompt:
        '你是账单问答的裁决裁判。根据用户目标与对话记录，按数据准确度(40)/逻辑一致性(30)/目标完成度(20)/语气清晰度(10)评分；契约事实由契约引擎单独判定。',
      prompt_tokens: 203,
      completion_tokens: 143,
      created_at: msIso(40, 60),
    },
  ],
  answer_grading: {
    facts: D4_FACTS,
    gates: { facts: false, extras: true, safety: false, behavior: true },
    notes: [],
    extras: [],
    passed: false,
    behavior: { clarify_first: false, conclusion_first: true },
    degraded: false,
    scorecard: D4_SCORECARD,
    lucky_pass: false,
    root_cause: 'HALLUCINATION',
    diagnostics: null,
    total_score: 70.0,
    grader_version: 'answer-grader@0.2',
  },
  self_check: null,
  raw_request_response: [{ turn: 1, request: D4_TURN.request_payload, response: D4_TURN.response_raw }],
}

// ---------------------------------------------------------------- 900007 多轮 · 通过（首轮未判定 / 末轮规则通过）
const D5_STEPS_1 = [
  call('planner', '意图规划', '规划', 96, { result: { intent: '套餐外流量费查询', need_db: true } }),
  call('db_query', '账单库查询', '执行查询', 262, { capability: 'billing_overage_fee', args: { account_id: 'A10086', month: '2026-10' }, result: { overage_fee: 27.0 } }),
  call('composer', '组织回复', '组织回复', 128, { result: { answer_chars: 28 } }),
]
const D5_STEPS_2 = [
  call('planner', '意图规划', '规划', 88, { result: { intent: '套餐外明细核对', need_db: true } }),
  call('db_query', '账单库查询', '执行查询', 246, { capability: 'billing_overage_items', args: { account_id: 'A10086', month: '2026-10' }, result: { items: [{ name: '国内流量超套费', amount: 27.0 }] } }),
  call('caliber_check', '口径校验', '口径校验', 61, { result: { caliber: 'overage_fee', warn: null } }),
  call('composer', '组织回复', '组织回复', 134, { result: { answer_chars: 34 } }),
]
const D5_RULE_CHECK = {
  passed: true,
  checks: [
    { type: 'must_contain', label: '必须包含「27.00」', expected: '27.00', actual: null, passed: true, evidence: '套餐外流量费为 ¥27.00，共 1 项。', mode: '', bound_name: null, reason: '' },
    { type: 'must_contain', label: '必须包含「1 项」', expected: '1 项', actual: null, passed: true, evidence: '共 1 项：国内流量超套费 ¥27.00。', mode: '', bound_name: null, reason: '' },
  ],
  extracted: { amounts: ['27.00'], counts: [1], dates: [] },
}
const D5_ANSWER_1 = '您本月（2026-10）套餐外流量费为 ¥27.00。'
const D5_ANSWER_2 = '明细只有 1 项：国内流量超套费 ¥27.00，与总额一致。'
const D5_REASON = '两轮回答与底层明细一致，追问明细时给出逐项对照并确认总额，没有出现口径或数值偏差。'
const D5_TURNS = [
  sseTurn({
    id: 207,
    index: 1,
    query: '查一下这个月套餐外流量费多少',
    reqId: 'req-d583b001',
    deltas: [D5_ANSWER_1],
    promptTokens: 3710,
    completionTokens: 260,
    intent: '套餐外流量费查询',
    apiParams: { account_id: 'A10086', month: '2026-10', metric: 'overage_fee' },
    rawDb: { rows: [{ overage_fee: 27.0 }], row_count: 1 },
    steps: D5_STEPS_1,
    latency: 2100,
    ttft: 604,
    at: msIso(42, 100),
  }),
  sseTurn({
    id: 208,
    index: 2,
    query: '明细能对一下吗',
    reqId: 'req-d583b002',
    deltas: [D5_ANSWER_2],
    promptTokens: 3980,
    completionTokens: 300,
    intent: '套餐外明细核对',
    apiParams: { account_id: 'A10086', month: '2026-10', metric: 'overage_items' },
    rawDb: { items: [{ name: '国内流量超套费', amount: 27.0 }], count: 1 },
    steps: D5_STEPS_2,
    ruleCheck: D5_RULE_CHECK,
    latency: 2520,
    ttft: 596,
    at: msIso(44, 620),
  }),
]

const EXECUTION_900007 = {
  ...DEMO_BASE,
  id: 900007,
  case_id: 900225,
  case_code: 'BILL-FEE-0433',
  case_name: '10 月套餐外流量费多轮核对',
  case_version: 2,
  status: 'PASS',
  pass_flag: true,
  total_score: 92,
  turns_used: 2,
  latency_ms: 11400.0,
  ttft_ms: 600,
  judge_tokens: 2980,
  grading_mode: 'LEGACY',
  started_at: msIso(40, 0),
  finished_at: msIso(51, 400),
  max_turns: 5,
  rule_result: {
    passed: true,
    checks: D5_RULE_CHECK.checks.map((check) => ({ ...check, label: `T2 · ${check.label}` })),
    extracted: {},
  },
  judge_result: legacyAggregate({
    score: 92,
    dimensionScores: dims(38, 27, 17, 10),
    reason: D5_REASON,
    threshold: 60,
    judgeTokens: 2980,
  }),
  case_snapshot: demoSnapshot({
    id: 900225,
    code: 'BILL-FEE-0433',
    name: '10 月套餐外流量费多轮核对',
    description: '首轮问总额、次轮核对明细；首轮未配规则，判定落在执行级。',
    tags: ['多轮', '套餐外', '明细'],
    caseType: 'STATIC_MULTI',
    turns: [
      { index: 1, query: '查一下这个月套餐外流量费多少' },
      { index: 2, query: '明细能对一下吗', expected_answer: '套餐外流量费 ¥27.00，共 1 项（国内流量超套费）。', expected_rules: { must_contain: ['27.00', '1 项'] } },
    ],
    expectedAnswer: '套餐外流量费 ¥27.00，共 1 项，与明细一致。',
    userGoal: '确认套餐外流量费总额与明细一致',
    threshold: 60,
  }),
  trace_uid: '5c9b3e18-2a76-4d0f-8b51-9e4d7c60a382',
  turns: D5_TURNS,
  judges: [
    legacyJudge({
      id: 13,
      score: 92,
      dimensionScores: dims(38, 27, 17, 10),
      tags: [],
      reason: D5_REASON,
      threshold: 60,
      promptTokens: 1720,
      completionTokens: 1260,
      at: msIso(50, 300),
    }),
  ],
  answer_grading: null,
  self_check: null,
  raw_request_response: D5_TURNS.map((turn) => ({ turn: turn.turn_index, request: turn.request_payload, response: turn.response_raw })),
}

// ---------------------------------------------------------------- 900008 历史 RUBRIC 形态（无轮次 / 无裁判 JSON）
/*
 * 当前库里 1226 条 RUBRIC 执行都是这个形态：有 total_score 与 pass_flag，
 * 但 turn_trace / judge_result / answer_grading 全空（旧评分模式遗物）。
 * 判定页在这里无事可做，界面应显示空态而不是报错。
 */
const EXECUTION_900008 = {
  ...DEMO_BASE,
  task_name: DEMO_TASK_NAME,
  id: 900008,
  case_id: 900226,
  case_code: 'DEMO-RUBRIC-HISTORY',
  case_name: '历史 RUBRIC 评分记录（无轮次快照）',
  case_version: 1,
  status: 'PASS',
  pass_flag: true,
  total_score: 89.7,
  turns_used: 1,
  latency_ms: 960.0,
  ttft_ms: 960.0,
  judge_tokens: 0,
  grading_mode: 'RUBRIC',
  started_at: msIso(15, 0),
  finished_at: msIso(15, 960),
  max_turns: 5,
  rule_result: null,
  judge_result: null,
  case_snapshot: demoSnapshot({
    id: 900226,
    code: 'DEMO-RUBRIC-HISTORY',
    name: '历史 RUBRIC 评分记录（无轮次快照）',
    description: '旧评分模式（RUBRIC）落库形态：只有执行级分数，没有轮次与裁判明细。',
    tags: ['历史数据'],
    caseType: 'SINGLE',
    query: '请用 JSON 返回 13800138000 在 2026-10 的账单总额和费用项数',
    expectedAnswer: '{"total_amount": 268.5, "item_count": 4}',
    userGoal: '按 JSON 契约返回账单总额与费用项数',
    threshold: 60,
  }),
  trace_uid: 'b7e21a49-3f08-4c6d-9a72-0f5e8d3c6419',
  turns: [],
  judges: [],
  answer_grading: null,
  self_check: null,
  raw_request_response: [],
}

// ---------------------------------------------------------------- 900009 单轮 · 未通过（分低于通过线）
const D6_STEPS = [
  call('planner', '意图规划', '规划', 104, { result: { intent: '账单余额查询', need_db: true } }),
  call('db_query', '话费库查询', '执行查询', 238, { capability: 'balance_query', args: { phone_no: '13900139000' }, result: { balance: 56.2, bill_day: 1 } }),
  call('composer', '组织回复', '组织回复', 126, { result: { answer_chars: 33 } }),
]
const D6_RULE_CHECK = {
  passed: false,
  checks: [
    { type: 'must_contain', label: '必须包含「356.80」', expected: '356.80', actual: null, passed: false, evidence: '', mode: '', bound_name: null, reason: '' },
  ],
  extracted: { amounts: ['56.20'], counts: [], dates: [] },
}
const D6_ANSWER = '您的话费余额为 ¥56.20，账单出账日为每月 1 日。'
const D6_REASON =
  '用户问的是上月账单总额，回复改答余额与出账日，没有给出应有的 ¥356.80；数值本身取自真实余额，不构成幻觉，但目标完成度基本为零。'
const D6_TURN = sseTurn({
  id: 209,
  index: 1,
  query: '查一下 13900139000 上个月账单总额',
  reqId: 'req-d698c001',
  deltas: [D6_ANSWER],
  promptTokens: 3690,
  completionTokens: 240,
  intent: '账单余额查询',
  apiParams: { phone_no: '13900139000', metric: 'balance' },
  rawDb: { phone_no: '13900139000', balance: 56.2, bill_day: 1, row_count: 1 },
  steps: D6_STEPS,
  ruleCheck: D6_RULE_CHECK,
  latency: 1980,
  ttft: 612,
  at: msIso(58, 100),
})

const EXECUTION_900009 = {
  ...DEMO_BASE,
  id: 900009,
  case_id: 900227,
  case_code: 'BILL-TOTAL-0434',
  case_name: '10 月账单总额查询（答非所问）',
  case_version: 2,
  status: 'FAIL',
  pass_flag: false,
  total_score: 48,
  turns_used: 1,
  latency_ms: 4300.0,
  ttft_ms: 612,
  judge_tokens: 1120,
  grading_mode: 'LEGACY',
  started_at: msIso(56, 0),
  finished_at: msIso(59, 980),
  max_turns: 3,
  rule_result: {
    passed: false,
    checks: D6_RULE_CHECK.checks.map((check) => ({ ...check, label: `T1 · ${check.label}` })),
    extracted: {},
  },
  judge_result: legacyAggregate({
    score: 48,
    dimensionScores: dims(22, 14, 8, 4),
    tags: ['口径遗漏'],
    reason: D6_REASON,
    threshold: 60,
    judgeTokens: 1120,
    policyNotes: ['规则硬校验未通过，最终判定为不通过'],
  }),
  case_snapshot: demoSnapshot({
    id: 900227,
    code: 'BILL-TOTAL-0434',
    name: '10 月账单总额查询（答非所问）',
    description: '单轮问上月账单总额，Agent 答成余额与出账日；用于验证低于通过线的未通过形态。',
    tags: ['单轮', '答非所问'],
    caseType: 'SINGLE',
    query: '查一下 13900139000 上个月账单总额',
    expectedAnswer: '13900139000 在 2026-10 的账单总额为 ¥356.80。',
    userGoal: '拿到 13900139000 在 2026-10 的账单总额',
    threshold: 60,
  }),
  trace_uid: 'd40f7c2a-91b5-4e63-8a27-5f31bc09e784',
  turns: [D6_TURN],
  judges: [
    legacyJudge({
      id: 14,
      score: 48,
      dimensionScores: dims(22, 14, 8, 4),
      tags: ['口径遗漏'],
      reason: D6_REASON,
      threshold: 60,
      promptTokens: 780,
      completionTokens: 340,
      at: msIso(59, 300),
    }),
  ],
  answer_grading: null,
  self_check: null,
  raw_request_response: [{ turn: 1, request: D6_TURN.request_payload, response: D6_TURN.response_raw }],
}

const EXECUTIONS = {
  900001: EXECUTION,
  900002: EXECUTION_900002,
  900003: EXECUTION_900003,
  900004: EXECUTION_900004,
  900005: EXECUTION_900005,
  900006: EXECUTION_900006,
  900007: EXECUTION_900007,
  900008: EXECUTION_900008,
  900009: EXECUTION_900009,
}

// ---------------------------------------------------------------- 任务（面包屑 / 头部用）
const TASK = {
  id: TASK_ID,
  code: 'TRACE-DEMO-01',
  name: '【Trace 演示】10 月账单口径回归（夜间批）',
  description: '月末账单口径回归：流量 / 话费 / 增值业务三组用例',
  status: 'COMPLETED', // TaskStatus 枚举（enums.py:53），无 FINISHED 值
  control_state: 'FINISHED', // 完成态（scheduler.py:374/535/745）
  environment_id: 12,
  case_ids: [900211, 900212],
  total_cases: 2,
  finished_cases: 2,
  pass_count: 1,
  fail_count: 1,
  error_count: 0,
  max_turns_count: 5,
  concurrency: 2,
  qps_limit: 4,
  retry_on_env_error: true,
  max_env_retries: 2,
  retry_backoff_seconds: 5,
  retry_on_error: false,
  max_error_retries: 0,
  enable_self_check: true,
  schedule_type: 'IMMEDIATE', // TaskScheduleType 枚举（enums.py:62），无 NONE 值
  scheduled_at: null,
  schedule_time: null,
  schedule_weekdays: null,
  next_run_at: null,
  last_run_at: msIso(31, 0),
  schedule_active: false,
  schedule_source_id: null,
  repeat_rounds: 1, // TaskOut 默认 1（schemas/task.py:124）
  round_index: 1, // TaskOut 默认 1（schemas/task.py:125）
  created_by: 'qa_op',
  created_at: '2026-11-22T18:40:00.000',
  started_at: msIso(31, 0),
  finished_at: msIso(51, 845),
  // TaskOut 其余恒返回键（schemas/task.py:130-138，serializers task_to_out 富化）
  summary: null,
  error_message: null,
  environment_name: '联调环境 · 账单中台',
  run_count: 1,
  unfinished_cases: 0,
  retrying_cases: [],
}

// 补充样例任务：关闭追问自检（executor.py:159-190 → self_check 恒 null）
const TASK2 = {
  ...TASK,
  id: DEMO_TASK_ID,
  code: 'TRACE-DEMO-02',
  name: DEMO_TASK_NAME,
  description: '判定页分支演示：单轮通过/未通过、多轮、契约、历史 RUBRIC，自检关闭',
  case_ids: [900221, 900222, 900223, 900224, 900225, 900226, 900227],
  total_cases: 7,
  finished_cases: 7,
  pass_count: 3,
  fail_count: 4,
  error_count: 0,
  max_turns_count: 5,
  enable_self_check: false,
}

// ---------------------------------------------------------------- 任务详情（面包屑回跳）
/** 演示任务下的执行：900101 只挂 900001/900002，其余都在 900102。 */
const TASK_EXECUTIONS = {
  [TASK_ID]: [900001, 900002],
  [DEMO_TASK_ID]: [900003, 900004, 900005, 900006, 900007, 900008, 900009],
}

/** CaseExecutionItem 键形（frontend/src/types.ts:934），只从执行记录派生。 */
function caseExecutionItem(record) {
  return {
    id: record.id,
    task_id: record.task_id,
    task_name: record.task_name,
    case_id: record.case_id,
    case_code: record.case_code,
    case_name: record.case_name,
    source: record.source,
    status: record.status,
    attempt_no: record.attempt_no,
    attempt_count: record.attempt_count,
    error_kind: record.error_kind,
    pass_flag: record.pass_flag,
    total_score: record.total_score,
    case_version: record.case_version,
    turns_used: record.turns_used,
    latency_ms: record.latency_ms,
    ttft_ms: record.ttft_ms,
    started_at: record.started_at,
    finished_at: record.finished_at,
    self_check_status: record.self_check?.status ?? null,
  }
}

/** TaskMetrics 键形（frontend/src/types.ts:1106）；progress / pass_rate 都是百分数。 */
function taskMetricsOf(task, records) {
  const distribution = {
    PASS: 0,
    FAIL: 0,
    ERROR: 0,
    MAX_TURNS_EXCEEDED: 0,
    REPETITION_ABORT: 0,
    ABORTED: 0,
    RUNNING: 0,
    PENDING: 0,
  }
  records.forEach((record) => {
    if (distribution[record.status] !== undefined) distribution[record.status] += 1
  })
  const avg = (values) => (values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0)
  const scores = records.map((record) => record.total_score).filter((value) => typeof value === 'number')
  const latencies = records
    .map((record) => record.latency_ms)
    .filter((value) => typeof value === 'number')
    .sort((left, right) => left - right)
  const p95 = latencies.length ? latencies[Math.min(latencies.length - 1, Math.ceil(latencies.length * 0.95) - 1)] : 0
  const totalCases = task.total_cases || records.length
  const elapsedSeconds = records.reduce((sum, record) => sum + (record.latency_ms ?? 0), 0) / 1000
  const passCount = records.filter((record) => record.pass_flag === true).length
  return {
    task_id: task.id,
    status: task.status,
    control_state: task.control_state,
    total_cases: totalCases,
    finished_cases: task.finished_cases ?? records.length,
    progress: totalCases ? ((task.finished_cases ?? records.length) / totalCases) * 100 : 0,
    status_distribution: distribution,
    pass_rate: totalCases ? (passCount / totalCases) * 100 : 0,
    avg_score: avg(scores),
    avg_ttft_ms: avg(records.map((record) => record.ttft_ms).filter((value) => typeof value === 'number')),
    avg_latency_ms: avg(latencies),
    p95_latency_ms: p95,
    avg_turns: avg(records.map((record) => record.turns_used).filter((value) => typeof value === 'number')),
    simulator_tokens: records.reduce((sum, record) => sum + (record.simulator_tokens ?? 0), 0),
    judge_tokens: records.reduce((sum, record) => sum + (record.judge_tokens ?? 0), 0),
    total_tokens: records.reduce(
      (sum, record) => sum + (record.simulator_tokens ?? 0) + (record.judge_tokens ?? 0),
      0,
    ),
    env_retry_count: 0,
    retry_count: 0,
    eta_seconds: null,
    elapsed_seconds: elapsedSeconds,
    throughput_per_minute: elapsedSeconds ? (records.length / elapsedSeconds) * 60 : 0,
  }
}

function taskRecordsOf(taskId) {
  return (TASK_EXECUTIONS[taskId] ?? []).map((id) => EXECUTIONS[id]).filter(Boolean)
}

const ME = {
  id: 1,
  username: 'demo',
  display_name: '演示用户',
  roles: ['TESTER'],
  permissions: [
    'execution:read',
    'execution:retry',
    'case:read',
    'task:read',
    'environment:read',
    'prompt:read',
  ],
  must_change_password: false,
}

// ---------------------------------------------------------------- server
function sendJson(res, status, payload) {
  const body = JSON.stringify(payload)
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
  })
  res.end(body)
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch {
        resolve({})
      }
    })
  })
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1:8877')
  const path = url.pathname

  if (req.method === 'GET' && path === '/health') {
    return sendJson(res, 200, { status: 'ok', mode: 'mock' })
  }
  if (path === '/api/v1/auth/login' && req.method === 'POST') {
    const body = await readBody(req)
    const username = String(body.username || 'demo')
    return sendJson(res, 200, {
      access_token: 'mock-token-demo',
      token_type: 'bearer',
      expires_in: 86400,
      username,
      user: { ...ME, username },
    })
  }
  if (path === '/api/v1/auth/me') {
    if ((req.headers.authorization || '') !== 'Bearer mock-token-demo') {
      return sendJson(res, 401, { code: 'UNAUTHORIZED', message: 'token 无效' })
    }
    return sendJson(res, 200, ME)
  }
  const execMatch = path.match(/^\/api\/v1\/executions\/(\d+)$/)
  if (execMatch) {
    const record = EXECUTIONS[Number(execMatch[1])]
    if (!record) return sendJson(res, 404, { code: 'NOT_FOUND', message: '执行记录不存在' })
    return sendJson(res, 200, record)
  }
  const rawMatch = path.match(/^\/api\/v1\/executions\/(\d+)\/raw$/)
  if (rawMatch) {
    const record = EXECUTIONS[Number(rawMatch[1])]
    if (!record) return sendJson(res, 404, { code: 'NOT_FOUND', message: '执行记录不存在' })
    // 历史 RUBRIC 执行没有裁判行；按空数组返回，避免 500
    const judge = record.judges?.[0] ?? null
    return sendJson(res, 200, {
      execution_id: record.id,
      trace_uid: record.trace_uid,
      case_snapshot: record.case_snapshot,
      turns: record.raw_request_response.map((item) => ({ ...item, judge_prompt: null })),
      judges: judge ? [{ model: judge.model_name, prompt: judge.judge_prompt }] : [],
    })
  }
  const taskSubMatch = path.match(/^\/api\/v1\/tasks\/(\d+)\/(metrics|executions|retry-candidates)$/)
  if (req.method === 'GET' && taskSubMatch) {
    const taskId = Number(taskSubMatch[1])
    const task = { [TASK_ID]: TASK, [DEMO_TASK_ID]: TASK2 }[taskId]
    if (!task) return sendJson(res, 404, { code: 'NOT_FOUND', message: '任务不存在' })
    const records = taskRecordsOf(taskId)
    if (taskSubMatch[2] === 'metrics') return sendJson(res, 200, taskMetricsOf(task, records))
    if (taskSubMatch[2] === 'executions') {
      // 演示数据每个用例只有一次执行，筛选 / 排序参数不影响结果，只回显分页
      const page = Number(url.searchParams.get('page') ?? 1)
      const pageSize = Number(url.searchParams.get('page_size') ?? 30)
      const items = records.map(caseExecutionItem)
      return sendJson(res, 200, { items, total: items.length, page, page_size: pageSize })
    }
    const failed = records.filter((record) => record.pass_flag === false)
    return sendJson(res, 200, {
      groups: failed.length ? [{ status: 'FAIL', case_ids: failed.map((record) => record.case_id) }] : [],
      total: failed.length,
      retrying_cases: [],
    })
  }
  const taskMatch = path.match(/^\/api\/v1\/tasks\/(\d+)$/)
  if (req.method === 'GET' && taskMatch) {
    const task = { [TASK_ID]: TASK, [DEMO_TASK_ID]: TASK2 }[Number(taskMatch[1])]
    if (!task) return sendJson(res, 404, { code: 'NOT_FOUND', message: '任务不存在' })
    return sendJson(res, 200, task)
  }
  if (req.method === 'GET' && path === '/api/v1/notifications') {
    // 通知仅从用户/角色管理审计事件投影（notifications.py:11-21），
    // NotificationOut 键形（schemas/notification.py:11-19）：message 非 content，level ∈ info/success/warning/error
    return sendJson(res, 200, [
      { id: 12, title: '新建用户', message: 'admin 创建了 用户 #12', level: 'success', actor: 'admin', resource: 'user', resource_id: '12', created_at: msIso(52, 100) },
    ])
  }
  return sendJson(res, 404, {
    code: 'NOT_FOUND',
    message: `mock 未实现该接口：${req.method} ${path}`,
    path,
  })
})

server.listen(8877, '127.0.0.1', () => {
  console.log(`mock trace server on http://127.0.0.1:8877 (execution #${EXECUTION_ID})`)
})
