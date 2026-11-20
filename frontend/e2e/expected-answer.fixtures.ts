import type { Page } from '@playwright/test'

/**
 * 执行详情「期望答案对照」用的固定执行数据。
 *
 * 这组用例只依赖前端 dev server（5173），通过 page.route 拦截 /api/v1，
 * 不要求后端、数据库或 mock LLM 处于运行状态。
 */

const profile = {
  id: 1,
  username: 'admin',
  display_name: '管理员',
  roles: ['SYSTEM_ADMIN'],
  permissions: [
    'execution:read',
    'task:read',
    'case:read',
    'dashboard:read',
    'prompt:read',
    'stats:read',
    'user:read',
    'role:read',
    'audit:read',
    'environment:read',
  ],
  must_change_password: false,
}

interface TurnOptions {
  status?: string
  hidden?: Array<Record<string, unknown>> | null
  ruleCheck?: Record<string, unknown> | null
}

function turn(index: number, query: string, answer: string, options: TurnOptions = {}) {
  return {
    id: index,
    turn_index: index,
    query,
    input_source: index === 1 ? 'STATIC' : 'SIMULATOR',
    simulator_thought: null,
    simulator_status: options.status ?? 'CONTINUE',
    hidden_context_released: options.hidden ?? null,
    rendered_prompt: null,
    request_payload: {},
    response_raw: {},
    final_answer: answer,
    session_id: '5f8c9d21',
    intent: null,
    generated_sql: null,
    api_params: null,
    raw_db_data: null,
    ttft_ms: 862,
    latency_ms: 2410,
    rule_check: options.ruleCheck ?? null,
    simulator_prompt_tokens: 600,
    simulator_completion_tokens: 600,
    created_at: '2026-11-18T14:32:08',
  }
}

function baseDetail(patch: Record<string, unknown>) {
  return {
    id: 10231,
    task_id: 9001,
    task_name: '全量回归',
    case_id: 1,
    case_code: 'DEMO-CONTRACT-001',
    case_name: '契约模式 · 账单金额与费用项数（答案包络）',
    case_version: 12,
    source: 'BATCH',
    status: 'FAIL',
    attempt_no: 1,
    error_kind: null,
    pass_flag: false,
    total_score: 41,
    termination_status: 'COMPLETED',
    turns_used: 1,
    latency_ms: 2410,
    ttft_ms: 862,
    simulator_tokens: 1200,
    judge_tokens: 2040,
    grading_mode: 'ANSWER_CONTRACT',
    lucky_pass: false,
    root_cause: 'HALLUCINATION',
    started_at: '2026-11-18T14:32:08',
    finished_at: '2026-11-18T14:32:11',
    environment_id: 1,
    environment_name: 'QA-MOCK-JSON',
    max_turns: 1,
    simulator_template_version: 1,
    judge_template_version: 1,
    rule_result: null,
    judge_result: null,
    error_message: null,
    case_snapshot: {},
    trace_uid: 'trace-expected-answer',
    turns: [],
    judges: [],
    answer_grading: null,
    fact_results: [],
    diagnostics: null,
    grader_version: 'answer-grader@0.2',
    as_of: '2026-10',
    self_check: null,
    raw_request_response: [],
    ...patch,
  }
}

function fact(
  id: string,
  label: string,
  kind: string,
  expected: unknown,
  actual: unknown,
  passed: boolean,
  reason: string,
) {
  return {
    fact_id: id,
    label,
    kind,
    is_gate: true,
    passed,
    expected,
    actual,
    reason,
    mode: 'name_bound',
    bound_name: label,
  }
}

const singleDetail = baseDetail({
  id: 10231,
  case_code: 'DEMO-CONTRACT-001',
  case_name: '契约模式 · 账单金额与费用项数（答案包络）',
  case_version: 12,
  grading_mode: 'ANSWER_CONTRACT',
  as_of: '2026-10',
  case_snapshot: {
    id: 1,
    case_type: 'SINGLE',
    grading_mode: 'ANSWER_CONTRACT',
    as_of: '2026-10',
    expected_answer: '手机号 13800138000 在 2026-10 的账单总额为 ¥268.50，共 4 项费用。',
    expected_rules: {
      amounts: [{ label: '账单总额', value: 268.5, tolerance: 0.01 }],
      counts: [{ label: '费用项数', value: 4 }],
      must_contain: ['13800138000'],
    },
    answer_envelope: { must: [] },
  },
  turns: [
    turn(
      1,
      '请帮我查一下手机号 13800138000 在 2026-10 的账单总额和费用项数',
      '手机号 13800138000 在 2026-10 的账单总额为 ¥286.50，共 4 项费用。如需费用明细，我可以继续为您查询。',
    ),
  ],
  fact_results: [
    fact(
      'total_amount',
      '账单总额',
      'scalar',
      { value: 268.5, unit: '元', tolerance: 0.01 },
      { bound_value: 286.5, bound_name: '账单总额' },
      false,
      '期望 268.50，实际 286.50',
    ),
    fact(
      'item_count',
      '费用项数',
      'scalar',
      { value: 4 },
      { bound_value: 4, bound_name: '费用项数' },
      true,
      '数量一致',
    ),
    fact(
      'phone_no',
      '手机号',
      'text',
      { values: ['13800138000'] },
      { hits: ['13800138000'] },
      true,
      '号码一致',
    ),
  ],
})

const staticDetail = baseDetail({
  id: 10232,
  case_code: 'DEMO-STATIC-001',
  case_name: '静态多轮 · 指代继承与超套费追问',
  case_version: 8,
  grading_mode: 'LEGACY',
  total_score: 58,
  turns_used: 2,
  root_cause: 'SYNTHESIS',
  grader_version: 'judge-4d@1.4',
  as_of: null,
  case_snapshot: {
    id: 2,
    case_type: 'STATIC_MULTI',
    grading_mode: 'LEGACY',
    fail_fast: false,
    turns: [
      {
        index: 1,
        query: '帮我查下 13800138000 在 2026-10 的账单总额和费用明细',
        expected_rules: {
          amounts: [{ label: '账单总额', value: 268.5, tolerance: 0.01 }],
          counts: [{ label: '费用项数', value: 4 }],
        },
      },
      {
        index: 2,
        query: '那这笔账单里流量超套了多少钱？',
        expected_rules: {
          amounts: [{ label: '超套费', value: 27, tolerance: 0.01 }],
          must_contain: ['超套'],
        },
      },
    ],
    session_expected: {
      expected_answer: '两轮均围绕手机号 13800138000：账单总额 ¥268.50，超套费 ¥27.00。',
      expected_rules: {
        amounts: [
          { label: '账单总额', value: 268.5, tolerance: 0.01 },
          { label: '超套费', value: 27, tolerance: 0.01 },
        ],
      },
    },
  },
  turns: [
    turn(
      1,
      '帮我查下 13800138000 在 2026-10 的账单总额和费用明细',
      '您 2026-10 的账单总额为 ¥268.50，共 4 项费用：套餐月费 ¥199.00、超套费 ¥27.00、漫游费 ¥32.50、增值业务费 ¥10.00。',
      {
        ruleCheck: {
          passed: true,
          checks: [
            { type: 'amount', label: '账单总额', expected: 268.5, actual: 268.5, passed: true },
            { type: 'count', label: '费用项数', expected: 4, actual: 4, passed: true },
          ],
        },
      },
    ),
    turn(2, '那这笔账单里流量超套了多少钱？', '这笔账单的流量超套费为 ¥72.00。', {
      ruleCheck: {
        passed: false,
        checks: [
          {
            type: 'amount',
            label: '超套费',
            expected: 27,
            actual: 72,
            passed: false,
            reason: '数值不一致',
            mode: 'name_bound',
            bound_name: '超套费',
          },
          { type: 'must_contain', label: '必须包含', expected: '超套', actual: '超套', passed: true },
        ],
      },
    }),
  ],
})

const dynamicDetail = baseDetail({
  id: 10233,
  case_code: 'DEMO-DYNAMIC-001',
  case_name: '动态多轮 · 隐藏手机号与账期挤牙膏式释放',
  case_version: 6,
  grading_mode: 'ANSWER_CONTRACT',
  total_score: 34,
  turns_used: 3,
  termination_status: 'GIVE_UP',
  root_cause: 'BEHAVIOR',
  as_of: '2026-10',
  case_snapshot: {
    id: 3,
    case_type: 'DYNAMIC_MULTI',
    grading_mode: 'ANSWER_CONTRACT',
    as_of: '2026-10',
    user_goal:
      '在不主动提供手机号的前提下，通过多轮追问拿到手机号 13800138000 在 2026-10 的账单总额与费用项数。',
    persona_description:
      '杭州互联网公司财务，说话口语化，关注账单总额与费用项数，习惯先描述诉求再补充号码。',
    initial_query: '我想核对一下这个月的账单金额和明细，但手机号一时想不起来了。',
    hidden_context: [
      { key: 'phone', content: '13800138000', release_hint: '手机号' },
      { key: 'period', content: '2026-10', release_hint: '账期' },
    ],
    max_turns: 5,
    terminate_conditions: { keywords: ['¥268.50'] },
    expected_answer: '最终应给出手机号 13800138000 在 2026-10 的账单总额 ¥268.50，共 4 项费用。',
    expected_rules: {
      amounts: [{ label: '账单总额', value: 268.5, tolerance: 0.01 }],
      counts: [{ label: '费用项数', value: 4 }],
    },
    answer_envelope: { must: [] },
  },
  turns: [
    turn(1, '我想核对一下这个月的账单金额和明细，但手机号一时想不起来了。', '好的，请提供一下您的手机号，我帮您查询。'),
    turn(2, '13800138000', '您本月的账单总额为 ¥298.00，共 5 项费用。', {
      hidden: [{ key: 'phone', content: '13800138000', release_hint: '手机号' }],
    }),
    turn(3, '就是账单上的那个月，你直接帮我查一下总额就行。', '您本月（2026-11）的账单总额为 ¥298.00，共 5 项费用。', {
      status: 'GIVE_UP',
    }),
  ],
  fact_results: [
    fact(
      'total_amount',
      '账单总额',
      'scalar',
      { value: 268.5, unit: '元', tolerance: 0.01 },
      { bound_value: 298, bound_name: '账单总额' },
      false,
      '期望 268.50，实际 298.00',
    ),
    fact(
      'item_count',
      '费用项数',
      'scalar',
      { value: 4 },
      { bound_value: 5, bound_name: '费用项数' },
      false,
      '期望 4，实际 5',
    ),
    fact(
      'phone_no',
      '手机号',
      'text',
      { values: ['13800138000'] },
      null,
      false,
      '最终答案未出现手机号',
    ),
  ],
})

export const executionFixtures: Record<number, Record<string, unknown>> = {
  10231: singleDetail,
  10232: staticDetail,
  10233: dynamicDetail,
}

/** 拦截 API 并注入登录态；只依赖 Vite dev server，不依赖后端。 */
export async function installExpectedAnswerMock(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('billing_qa_token', 'expected-answer-e2e-token')
  })

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const reply = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

    if (path.endsWith('/auth/me')) return reply(profile)
    if (path.endsWith('/notifications')) return reply([])

    const executionMatch = path.match(/\/executions\/(\d+)$/)
    if (executionMatch) {
      const detail = executionFixtures[Number(executionMatch[1])]
      if (detail) return reply(detail)
      return reply({ code: 'NOT_FOUND', message: 'not found' }, 404)
    }

    if (path.includes('/tasks/')) {
      return reply({ code: 'NOT_FOUND', message: 'not found' }, 404)
    }
    return reply({})
  })
}
