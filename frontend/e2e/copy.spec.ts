import { expect, test, type Page } from '@playwright/test'

/**
 * 复制功能端到端回归（全链路字段完整性）：
 * 1) 单轮 LEGACY 用例复制；
 * 2) 单轮 ANSWER_CONTRACT 用例复制（事实包络 / 响应契约 / 取数计划嵌套结构）；
 * 3) 静态多轮用例复制（turns / session_expected / fail_fast）；
 * 4) 动态多轮用例复制（目标 / 人设 / 隐藏信息 / 终止条件）；
 * 5) 批量任务复制（用例范围 + 全部执行参数）。
 *
 * 字段校验采用「遍历源对象 API 返回的全部 key」的方式：除明确重置的系统字段外，
 * 其余字段必须与源数据完全一致，未来新增字段也会自动纳入比对，避免手写清单漏项。
 *
 * 用例通过 API 造数并在结束时清理；批量任务暂无删除接口，测试会创建不自动启动的任务并尽力终止。
 */

// 复制后按新建规则重置 / 由系统生成的字段
const CASE_RESET_FIELDS = new Set([
  'id',
  'code',
  'name',
  'current_version',
  'created_by',
  'updated_by',
  'created_at',
  'updated_at',
  'review_status',
  'reviewed_by',
  'dataset_version',
  'dedupe_hash',
  'environment_name',
  'simulator_template_code',
  'judge_template_code',
  'simulator_template_name',
  'judge_template_name',
])

const TASK_RESET_FIELDS = new Set([
  'id',
  'code',
  'name',
  'status',
  'control_state',
  'finished_cases',
  'pass_count',
  'fail_count',
  'error_count',
  'max_turns_count',
  'next_run_at',
  'last_run_at',
  'schedule_source_id',
  'round_index',
  'created_by',
  'created_at',
  'started_at',
  'finished_at',
  'summary',
  'error_message',
  'environment_name',
  'run_count',
  'unfinished_cases',
])

async function seedAuth(page: Page): Promise<void> {
  const response = await page.request.post('/api/v1/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  expect(response.ok()).toBeTruthy()
  const { access_token: token } = await response.json()
  await page.addInitScript((value: string) => {
    localStorage.setItem('billing_qa_token', value)
  }, token)
  // 先打开应用，保证后续 api() 能读到 localStorage 中的 token。
  await page.goto('/cases')
}

async function api(page: Page, path: string, method = 'GET', data?: unknown) {
  const token = await page.evaluate(() => localStorage.getItem('billing_qa_token'))
  return page.request.fetch(`/api/v1${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(data === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    ...(data === undefined ? {} : { data }),
  })
}

function uniqueSuffix(): string {
  return `${Date.now()}`.slice(-9)
}

async function createCase(page: Page, payload: Record<string, unknown>) {
  const response = await api(page, '/cases', 'POST', payload)
  expect(response.ok(), await response.text()).toBeTruthy()
  return (await response.json()) as Record<string, any>
}

async function deleteCase(page: Page, id: number | null): Promise<void> {
  if (!id) return
  await api(page, `/cases/${id}`, 'DELETE')
}

async function findSimulatorTemplate(
  page: Page,
): Promise<{ id: number; version: number } | null> {
  const listed = await api(page, '/prompts?page_size=200')
  if (!listed.ok()) return null
  const body = await listed.json()
  const item = (body.items as Array<{ id: number; template_type: string }>).find(
    (template) => template.template_type === 'USER_SIMULATOR',
  )
  if (!item) return null
  const detailRes = await api(page, `/prompts/${item.id}`)
  if (!detailRes.ok()) return null
  const detail = await detailRes.json()
  const version = detail.versions?.[0]?.version
  return typeof version === 'number' ? { id: item.id, version } : null
}

async function requestVariableValueInput(page: Page, key: string) {
  const keys = page.locator('input[placeholder="key，如 user_id"]')
  const total = await keys.count()
  for (let index = 0; index < total; index += 1) {
    const input = keys.nth(index)
    if ((await input.inputValue()) === key) {
      return input.locator('xpath=following-sibling::input[1]')
    }
  }
  throw new Error(`未找到请求变量 ${key}`)
}

/** 遍历源用例 API 返回的所有字段，断言副本没有缺失或串值。 */
function expectCaseCopied(
  source: Record<string, any>,
  copied: Record<string, any>,
  overrides: Record<string, unknown> = {},
): void {
  for (const key of Object.keys(source)) {
    if (CASE_RESET_FIELDS.has(key)) continue
    const expected = key in overrides ? overrides[key] : source[key]
    expect(copied[key], `复制后字段 ${key} 缺失或与源用例不一致`).toEqual(expected)
  }
  expect(copied.code).toBe(`${source.code}-COPY`)
  expect(copied.name).toBe(`${source.name}（副本）`)
  expect(copied.current_version).toBe(1)
  // 不继承旧评审结论：答案契约 + HIGH 风险的新用例按后端规则自动进入待评审，其余为无需评审。
  const autoPending =
    source.grading_mode === 'ANSWER_CONTRACT' && source.risk_level === 'HIGH'
  expect(copied.review_status).toBe(autoPending ? 'PENDING' : 'NOT_REQUIRED')
  expect(copied.reviewed_by ?? null).toBeNull()
  expect(copied.dataset_version).toBe('v1')
}

/** 打开复制抽屉 → 可选修改 → 提交 → 通过 API 取回副本。 */
async function copyCaseViaDrawer(
  page: Page,
  source: Record<string, any>,
  beforeSubmit?: () => Promise<void>,
): Promise<Record<string, any>> {
  await page.goto('/cases')
  const keyword = page.getByPlaceholder('搜索编号 / 名称 / 描述')
  await keyword.fill(source.code)
  await keyword.press('Enter')

  const row = page.getByRole('row').filter({ hasText: source.code }).first()
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: '复制', exact: true }).click()

  await expect(
    page.getByRole('heading', { name: `复制用例 · 源 ${source.code}` }),
  ).toBeVisible()
  await expect(page.getByPlaceholder('CASE-BILL-001')).toHaveValue(`${source.code}-COPY`)
  await expect(page.getByPlaceholder('账单金额与明细一致性校验')).toHaveValue(
    `${source.name}（副本）`,
  )

  if (beforeSubmit) await beforeSubmit()

  await page.getByRole('button', { name: '创建用例', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: `复制用例 · 源 ${source.code}` }),
  ).toBeHidden({ timeout: 20_000 })

  const listed = await api(page, `/cases?keyword=${encodeURIComponent(`${source.code}-COPY`)}`)
  const body = await listed.json()
  const copied = body.items.find(
    (item: { code: string }) => item.code === `${source.code}-COPY`,
  ) as Record<string, any> | undefined
  expect(copied, `应创建副本 ${source.code}-COPY`).toBeTruthy()
  return copied!
}

test('单轮 LEGACY 用例复制：全字段带入，仅修改一个请求变量', async ({ page }) => {
  await seedAuth(page)
  const suffix = uniqueSuffix()
  const code = `E2E-COPY-${suffix}`
  const sourceName = 'E2E 复制完整性校验'
  let sourceId: number | null = null
  let copyId: number | null = null

  try {
    const source = await createCase(page, {
      code,
      name: sourceName,
      description: '复制功能全字段校验',
      case_type: 'SINGLE',
      status: 'ACTIVE',
      priority: 'P0',
      tags: ['e2e', 'copy'],
      environment_id: null,
      request_variables: { userid: '13800001234', custid: 'C-8821' },
      query: '帮我查一下本机号码 2026-10 的账单总额',
      expected_answer: '账单总额为 268.5 元',
      expected_rules: {
        amounts: [
          {
            label: '账单总额',
            value: 268.5,
            tolerance: 0.01,
            aliases: ['账单金额'],
            require_name: true,
          },
        ],
        counts: [{ label: '明细笔数', value: 4, tolerance: 0, aliases: [], require_name: false }],
        dates: ['2026-10'],
        must_contain: ['账单'],
        must_not_contain: ['不清楚'],
      },
      // 跨类型 / 非表单字段，用于验证复制不丢内容
      session_expected: { expected_answer: '追问时说明含税口径' },
      fail_fast: true,
      terminate_conditions: { keywords: ['结束'], custom_flag: true },
      prompt_overrides: { judge: '按电信账单口径判分' },
      grading_mode: 'LEGACY',
      input_type: 'CLEAR',
      as_of: '2026-10-31',
      snapshot_id: `SNAP-${suffix}`,
      risk_level: 'HIGH',
      owner: 'e2e-owner',
      max_turns: 3,
    })
    sourceId = Number(source.id)

    const copied = await copyCaseViaDrawer(page, source, async () => {
      const useridValue = await requestVariableValueInput(page, 'userid')
      await expect(useridValue).toBeVisible()
      await useridValue.fill('13800009999')
    })
    copyId = Number(copied.id)

    expectCaseCopied(source, copied, {
      request_variables: { userid: '13800009999', custid: 'C-8821' },
    })
  } finally {
    await deleteCase(page, copyId)
    await deleteCase(page, sourceId)
  }
})

test('单轮答案契约复制：事实包络 / 响应契约 / 取数计划完整带入', async ({ page }) => {
  await seedAuth(page)
  const suffix = uniqueSuffix()
  const code = `E2E-CONTRACT-${suffix}`
  const factId = `fact-total-${suffix}`
  let sourceId: number | null = null
  let copyId: number | null = null

  const answerEnvelope = {
    must: [
      {
        id: factId,
        kind: 'scalar',
        label: '账单总额',
        value: 268.5,
        unit: '元',
        tolerance: 0.01,
        time_scope: '2026-10',
        matcher: 'numeric',
        evidence_required: true,
        derived_from: [],
        formula: 'sum(items)',
        aliases: ['账单金额', '总额'],
        require_name: true,
        verification_status: 'VERIFIED',
        verified_by: 'e2e',
        verified_at: '2026-11-20T10:00:00',
        verification_note: '已核对账单',
        evidence_ref: 'EVID-E2E-001',
      },
    ],
    may: [
      {
        id: `may-note-${suffix}`,
        kind: 'text',
        label: '可选说明',
        value: '可提供明细',
        aliases: [],
      },
    ],
    must_not: [{ id: `must-not-${suffix}`, kind: 'text', value: '暂时无法查询' }],
    forbidden_phrases: ['不知道'],
    aliases: { total_amount: ['总额'] },
    tolerances: { money: 0.01, percent: 0.1 },
    extras_policy: 'strict',
    clarity_rules: { conclusion_first: true, max_ratio: 4.0 },
  }
  const responseContract = {
    allowed: ['clarify', 'answer_with_assumption', 'partial'],
    clarify: {
      must_ask_slot: ['time_range', 'billing_period'],
      must_not_assert: ['amount', 'balance'],
    },
    answer_with_assumption: {
      must_state_assumption: true,
      scenarios: [
        { assumption: '按当前账期', must: { amount: '268.5' } },
        { assumption: '按上月账期', must: { amount: '199.0' } },
      ],
    },
    partial: { must_disclose: false, required_facts: [factId] },
  }
  const planSpec = {
    required_capabilities: ['bill_query'],
    alternatives: { bill_query: ['balance_query'] },
    max_tool_calls: 3,
    allow_parallel: false,
  }

  try {
    const source = await createCase(page, {
      code,
      name: 'E2E 契约复制完整性',
      description: '复制功能契约嵌套字段校验',
      case_type: 'SINGLE',
      status: 'ACTIVE',
      priority: 'P0',
      tags: ['e2e', 'copy', 'contract'],
      request_variables: { userid: '13800001234' },
      query: '帮我查一下本机号码 2026-10 的账单总额',
      grading_mode: 'ANSWER_CONTRACT',
      input_type: 'AMBIGUOUS_REF',
      as_of: '2026-10-31',
      snapshot_id: `SNAP-CONTRACT-${suffix}`,
      answer_envelope: answerEnvelope,
      response_contract: responseContract,
      plan_spec: planSpec,
      risk_level: 'HIGH',
      owner: 'e2e-contract',
    })
    sourceId = Number(source.id)

    const copied = await copyCaseViaDrawer(page, source)
    copyId = Number(copied.id)

    expectCaseCopied(source, copied)
    // 额外显式断言最易丢失的嵌套结构
    expect(copied.answer_envelope).toEqual(source.answer_envelope)
    expect(copied.response_contract).toEqual(source.response_contract)
    expect(copied.plan_spec).toEqual(source.plan_spec)
    // 事实取证结论应随内容保留
    expect(copied.answer_envelope.must[0].verification_status).toBe('VERIFIED')
    expect(copied.answer_envelope.must[0].evidence_ref).toBe('EVID-E2E-001')
    // 契约里界面未暴露的子配置必须保留
    expect(copied.response_contract.clarify.must_not_assert).toEqual(['amount', 'balance'])
    expect(copied.response_contract.answer_with_assumption.scenarios).toHaveLength(2)
    expect(copied.response_contract.partial.must_disclose).toBe(false)
    expect(copied.plan_spec.allow_parallel).toBe(false)
  } finally {
    await deleteCase(page, copyId)
    await deleteCase(page, sourceId)
  }
})

test('静态多轮用例复制：轮次脚本 / 会话预期 / fail_fast 完整带入', async ({ page }) => {
  await seedAuth(page)
  const suffix = uniqueSuffix()
  const code = `E2E-STATIC-${suffix}`
  let sourceId: number | null = null
  let copyId: number | null = null

  try {
    const source = await createCase(page, {
      code,
      name: 'E2E 静态多轮复制完整性',
      description: '复制功能静态多轮字段校验',
      case_type: 'STATIC_MULTI',
      status: 'ACTIVE',
      priority: 'P1',
      tags: ['e2e', 'copy', 'static'],
      request_variables: { userid: '13800001234' },
      turns: [
        {
          index: 1,
          query: '帮我查一下本机账单',
          expected_answer: '引导用户确认账期',
          notes: '第一轮先问账期',
          expected_rules: { dates: ['2026-10'], must_contain: ['账期'] },
        },
        {
          index: 2,
          query: '2026-10 的，超套费多少',
          expected_answer: '超套费为 27 元',
          notes: '第二轮追问超套费',
          expected_rules: {
            amounts: [
              {
                label: '超套费',
                value: 27,
                tolerance: 0,
                aliases: ['超套金额'],
                require_name: true,
              },
            ],
          },
        },
      ],
      session_expected: {
        expected_answer: '全程围绕账单与超套费，口径一致',
        expected_rules: { must_not_contain: ['编造'] },
      },
      fail_fast: true,
      max_turns: 4,
    })
    sourceId = Number(source.id)

    const copied = await copyCaseViaDrawer(page, source)
    copyId = Number(copied.id)

    expectCaseCopied(source, copied)
    expect(copied.turns).toHaveLength(2)
    expect(copied.turns[1].expected_rules.amounts[0].label).toBe('超套费')
    expect(copied.session_expected).toEqual(source.session_expected)
    expect(copied.fail_fast).toBe(true)
  } finally {
    await deleteCase(page, copyId)
    await deleteCase(page, sourceId)
  }
})

test('动态多轮用例复制：目标 / 人设 / 隐藏信息 / 终止条件完整带入', async ({ page }) => {
  await seedAuth(page)
  const suffix = uniqueSuffix()
  const code = `E2E-DYNAMIC-${suffix}`
  let sourceId: number | null = null
  let copyId: number | null = null

  try {
    const source = await createCase(page, {
      code,
      name: 'E2E 动态多轮复制完整性',
      description: '复制功能动态多轮字段校验',
      case_type: 'DYNAMIC_MULTI',
      status: 'ACTIVE',
      priority: 'P1',
      tags: ['e2e', 'copy', 'dynamic'],
      request_variables: { custid: 'C-8821' },
      user_goal: '在不直接给出号码的前提下问到本月账单总额',
      initial_query: '我想问下账单',
      persona_description: '着急的中年用户，会一点点补充信息',
      hidden_context: [
        {
          key: 'phone',
          content: '13800001234',
          release_hint: '用户主动报号码时释放',
        },
        { key: 'period', content: '2026-10', release_hint: '追问账期时释放' },
      ],
      max_turns: 8,
      terminate_conditions: { keywords: ['谢谢', '没了'], custom_stop: { max_retry: 2 } },
      // 跨类型字段：动态多轮不常用，但复制必须原样保留
      session_expected: { expected_answer: '会话级预期（跨类型校验）' },
      fail_fast: true,
      expected_answer: '最终答复账单总额 268.5 元',
      expected_rules: {
        amounts: [{ label: '账单总额', value: 268.5, tolerance: 0.01, aliases: [], require_name: true }],
      },
    })
    sourceId = Number(source.id)

    const copied = await copyCaseViaDrawer(page, source)
    copyId = Number(copied.id)

    expectCaseCopied(source, copied)
    expect(copied.hidden_context).toHaveLength(2)
    expect(copied.hidden_context[1].release_hint).toBe('追问账期时释放')
    expect(copied.terminate_conditions).toEqual(source.terminate_conditions)
    expect(copied.session_expected).toEqual(source.session_expected)
  } finally {
    await deleteCase(page, copyId)
    await deleteCase(page, sourceId)
  }
})

test('特殊值复制：数字/对象请求变量、含逗号话术、set 事实与多值 MustNot 不丢数据', async ({
  page,
}) => {
  await seedAuth(page)
  const suffix = uniqueSuffix()
  const code = `E2E-TRICKY-${suffix}`
  const setFactId = `set-fact-${suffix}`
  const mustNotId = `must-not-set-${suffix}`
  let sourceId: number | null = null
  let copyId: number | null = null

  try {
    const template = await findSimulatorTemplate(page)
    const source = await createCase(page, {
      code,
      name: 'E2E 特殊值复制完整性',
      description: '数字 / 逗号话术 / set 事实 / 多值 MustNot',
      case_type: 'SINGLE',
      status: 'ACTIVE',
      priority: 'P1',
      tags: ['e2e', 'tag,with,comma'],
      // 数字 / 布尔 / 对象类型的请求变量必须原样保留，不能被字符串化
      request_variables: {
        userid: 13800001234,
        vip: true,
        amount: 268.5,
        profile: { level: 3, channel: 'app' },
      },
      query: '查一下账单',
      grading_mode: 'ANSWER_CONTRACT',
      input_type: 'CLEAR',
      as_of: '2026-10-31',
      expected_rules: {
        amounts: [
          {
            label: '账单总额',
            value: 268.5,
            tolerance: 0.01,
            aliases: ['总额，合计'],
            require_name: true,
          },
        ],
        counts: [],
        dates: ['2026-10'],
        must_contain: ['账单总额：268.5元，含税'],
        must_not_contain: ['暂不支持，请稍后再试'],
      },
      answer_envelope: {
        must: [
          {
            id: setFactId,
            kind: 'set',
            label: '账单构成项',
            values: ['月租', '增值业务'],
            keywords: ['构成', '明细'],
            aliases: ['费用项，构成'],
            require_name: true,
          },
        ],
        may: [],
        must_not: [
          {
            id: mustNotId,
            kind: 'set',
            label: '禁止话术',
            values: ['暂时无法查询', '请稍后再试'],
            keywords: ['拒绝'],
          },
        ],
        forbidden_phrases: ['您好，暂时无法查询'],
        aliases: {},
        tolerances: { money: 0.01, percent: 0.1 },
        extras_policy: 'lenient',
        clarity_rules: { conclusion_first: true, max_ratio: 3.0 },
      },
      prompt_overrides: { judge: '保留特殊值与话术' },
      ...(template
        ? {
            simulator_template_id: template.id,
            simulator_template_strategy: 'LATEST',
            simulator_template_version: template.version,
          }
        : {}),
    })
    sourceId = Number(source.id)

    const copied = await copyCaseViaDrawer(page, source)
    copyId = Number(copied.id)

    expectCaseCopied(source, copied)

    // 请求变量类型必须原样保留
    expect(copied.request_variables).toEqual(source.request_variables)
    expect(typeof copied.request_variables.userid).toBe('number')
    expect(copied.request_variables.profile).toEqual({ level: 3, channel: 'app' })

    // 含逗号的标签 / 话术 / 别名不能被拆成多项
    expect(copied.tags).toEqual(['e2e', 'tag,with,comma'])
    expect(copied.expected_rules.must_contain).toEqual(['账单总额：268.5元，含税'])
    expect(copied.expected_rules.must_not_contain).toEqual(['暂不支持，请稍后再试'])
    expect(copied.expected_rules.amounts[0].aliases).toEqual(['总额，合计'])
    expect(copied.answer_envelope.forbidden_phrases).toEqual(['您好，暂时无法查询'])
    expect(copied.answer_envelope.must[0].aliases).toEqual(['费用项，构成'])

    // set 事实的 values / keywords 必须保留
    expect(copied.answer_envelope.must[0].values).toEqual(['月租', '增值业务'])
    expect(copied.answer_envelope.must[0].keywords).toEqual(['构成', '明细'])

    // 多值 MustNot 必须仍是同一条事实，不能被拆成多条
    expect(copied.answer_envelope.must_not).toHaveLength(1)
    expect(copied.answer_envelope.must_not[0].values).toEqual([
      '暂时无法查询',
      '请稍后再试',
    ])
    expect(copied.answer_envelope.must_not[0].keywords).toEqual(['拒绝'])

    // 非 PINNED 策略下历史版本号也不能丢
    if (template) {
      expect(copied.simulator_template_version).toBe(source.simulator_template_version)
    }
  } finally {
    await deleteCase(page, copyId)
    await deleteCase(page, sourceId)
  }
})

test('批量任务复制：复用新建界面，完整带入用例范围与执行参数', async ({ page }) => {
  await seedAuth(page)
  const suffix = uniqueSuffix()
  const caseCodeA = `E2E-TASKCOPY-A-${suffix}`
  const caseCodeB = `E2E-TASKCOPY-B-${suffix}`
  const taskName = `E2E 复制任务 ${suffix}`
  let caseAId: number | null = null
  let caseBId: number | null = null
  let sourceTaskId: number | null = null
  let copiedTaskId: number | null = null

  const baseCase = {
    case_type: 'SINGLE',
    status: 'ACTIVE',
    priority: 'P1',
    request_variables: { userid: '13800001234' },
  }

  try {
    const caseA = await createCase(page, {
      ...baseCase,
      code: caseCodeA,
      name: 'E2E 任务复制用例 A',
      query: '查一下本机账单',
    })
    caseAId = Number(caseA.id)
    const caseB = await createCase(page, {
      ...baseCase,
      code: caseCodeB,
      name: 'E2E 任务复制用例 B',
      query: '查一下本机余额',
    })
    caseBId = Number(caseB.id)

    // 用 API 创建源任务：覆盖所有可配置参数，且不自动启动
    const created = await api(page, '/tasks', 'POST', {
      name: taskName,
      description: '复制功能校验任务',
      environment_id: null,
      case_scope: 'SELECTED',
      case_ids: [caseAId, caseBId],
      concurrency: 7,
      qps_limit: 9.5,
      retry_on_env_error: true,
      max_env_retries: 4,
      retry_on_error: true,
      max_error_retries: 4,
      retry_backoff_seconds: 6,
      enable_self_check: true,
      schedule_type: 'IMMEDIATE',
      repeat_rounds: 1,
      auto_start: false,
    })
    expect(created.ok(), await created.text()).toBeTruthy()
    const sourceTask = (await created.json()) as Record<string, any>
    sourceTaskId = Number(sourceTask.id)

    // 任务列表 → 搜索 → 行内「复制」
    await page.goto('/tasks')
    const keyword = page.getByPlaceholder('搜索任务名称 / 编号，输入即搜（分号分隔多词）')
    await keyword.fill(taskName)
    const row = page.getByRole('row').filter({ hasText: taskName }).first()
    await expect(row).toBeVisible({ timeout: 20_000 })
    await row.getByRole('button', { name: '复制', exact: true }).click()

    // 与新建批量任务同一个弹窗，仅标题与预填内容不同
    await expect(page.getByRole('heading', { name: '复制批量任务' })).toBeVisible()
    await expect(page.getByPlaceholder('例如：账单回归 2026-11')).toHaveValue(`${taskName}-副本`)
    await expect(page.getByText(`已选用例（2 条）`)).toBeVisible()

    // 表单参数应原样带入：连续轮次 / 并发 / QPS / 最大重试 / 退避
    const numbers = page.locator('input[type="number"]')
    await expect(numbers.nth(0)).toHaveValue('1')
    await expect(numbers.nth(1)).toHaveValue('7')
    await expect(numbers.nth(2)).toHaveValue('9.5')
    await expect(numbers.nth(3)).toHaveValue('4')
    await expect(numbers.nth(4)).toHaveValue('6')

    await expect(page.getByRole('checkbox', { name: /异常失败自动重试/ })).toBeChecked()
    await expect(page.getByRole('checkbox', { name: /失败追问自检/ })).toBeChecked()
    // 不自动启动，避免 e2e 触发真实执行
    await page.getByRole('checkbox', { name: '创建后立即启动' }).uncheck()

    await page.getByRole('button', { name: /创建并执行（2 条）/ }).click()
    await page.waitForURL(/\/tasks\/\d+$/)
    await expect(page.getByRole('heading', { name: '复制批量任务' })).toBeHidden()
    copiedTaskId = Number(new URL(page.url()).pathname.split('/').pop())

    const detail = await api(page, `/tasks/${copiedTaskId}`)
    expect(detail.ok()).toBeTruthy()
    const copiedTask = (await detail.json()) as Record<string, any>

    expect(copiedTask.name).toBe(`${taskName}-副本`)
    expect(copiedTask.code).not.toBe(sourceTask.code)
    // 遍历源任务 API 返回的所有字段：除运行态字段外必须完全一致
    for (const key of Object.keys(sourceTask)) {
      if (TASK_RESET_FIELDS.has(key)) continue
      expect(copiedTask[key], `复制后任务字段 ${key} 缺失或与源任务不一致`).toEqual(
        sourceTask[key],
      )
    }
    expect([...copiedTask.case_ids].sort()).toEqual([...sourceTask.case_ids].sort())
    expect(copiedTask.total_cases).toBe(2)
    // 未勾选「创建后立即启动」，应保持等待中而不是直接开跑
    expect(copiedTask.status).toBe('PENDING')
  } finally {
    if (copiedTaskId) {
      // 任务无删除接口：尽力终止，避免留下可执行任务。
      await api(page, `/tasks/${copiedTaskId}/abort`, 'POST')
    }
    if (sourceTaskId) {
      await api(page, `/tasks/${sourceTaskId}/abort`, 'POST')
    }
    await deleteCase(page, caseBId)
    await deleteCase(page, caseAId)
  }
})
