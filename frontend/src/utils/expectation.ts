import type {
  AnswerEnvelope,
  CaseSnapshot,
  ExecutionDetail,
  HiddenContextItem,
  RuleConfig,
  TurnItem,
  TurnTrace,
} from '../types'

/** 规则对照的一行。passed 为 null 表示本次执行未产出该规则的校验结果。 */
export interface RuleRow {
  id: string
  label: string
  expectedText: string
  actualText: string
  passed: boolean | null
  reason?: string | null
  mode?: string | null
  boundName?: string | null
  evidence?: string | null
}

/** 答案契约事实对照的一行。 */
export interface FactRow {
  id: string
  label: string
  kind: string
  section: 'must' | 'may' | 'must_not'
  expectedText: string
  actualText: string
  passed: boolean | null
  reason?: string | null
  mode?: string | null
  boundName?: string | null
}

export interface TurnExpectation {
  turnIndex: number
  expectedText: string | null
  expectedSource: 'EXPECTED_ANSWER' | 'RULES' | 'NONE'
  ruleRows: RuleRow[]
  query: string
  answer: string
  passed: boolean | null
  simulatorStatus: string | null
}

export interface SessionExpectation {
  text: string | null
  ruleRows: RuleRow[]
}

export interface HiddenReleaseRow {
  key: string
  content: string
  releaseHint: string
  releasedTurn: number | null
}

export interface DynamicExpectation {
  goal: string | null
  persona: string | null
  initialQuery: string | null
  expectedAnswer: string | null
  hidden: HiddenReleaseRow[]
  maxTurns: number | null
  terminateKeywords: string[]
  rules: RuleRow[]
}

export interface ResolvedExpectation {
  caseType: string
  snapshot: CaseSnapshot
  version: number
  asOf: string | null
  gradingMode: string
  turns: TurnExpectation[]
  session: SessionExpectation | null
  dynamic: DynamicExpectation | null
  facts: FactRow[]
  hasExpectation: boolean
}

const RULE_TYPE_LABELS: Record<string, string> = {
  amount: '金额',
  count: '笔数',
  date: '日期',
  must_contain: '必须包含',
  must_not_contain: '禁止包含',
}

const KIND_LABELS: Record<string, string> = {
  scalar: '数值',
  date: '日期',
  set: '集合',
  topk: 'TopK',
  boolean: '布尔',
  text: '文本',
  semantic: '语义',
}

const BINDING_MODE_LABELS: Record<string, string> = {
  name_bound: '名称绑定',
  window: '邻近名称绑定',
  json_key: 'JSON key 绑定',
  claim_bound: '断言锚定',
  value_only: '仅数值兜底',
  name_mismatch: '名称绑定值不符',
  json_key_mismatch: 'JSON 值不符',
  claim_mismatch: '断言值不符',
  name_conflict: '数值归属冲突',
  name_missing: '缺少业务名称',
  name_unbound: '名称未绑定数值',
  missing: '未找到数值',
}

export function bindingModeLabel(mode?: string | null): string {
  if (!mode) return ''
  return BINDING_MODE_LABELS[mode] ?? mode
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function textOf(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

export function factKindLabel(kind?: string | null): string {
  if (!kind) return '—'
  return KIND_LABELS[kind] ?? kind
}

function looksMonetary(label: string, unit?: string): boolean {
  if (/项数|笔数|数量|次数|条数/.test(label)) return false
  if (unit === '元' || unit === 'CNY' || unit === 'RMB') return true
  return /金额|费用|余额|总额|价/.test(label)
}

function formatNumeric(value: unknown, label: string, unit?: string): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value)
  if (looksMonetary(label, unit)) return `¥${value.toFixed(2)}`
  return Number.isInteger(value) ? String(value) : String(value)
}

function formatRuleValue(value: unknown, type: string, label: string): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return textOf(value)
  if (type === 'amount' || looksMonetary(label)) return `¥${value.toFixed(2)}`
  return Number.isInteger(value) ? String(value) : String(value)
}

function expectedUnit(fact: Record<string, unknown>): string | undefined {
  const expected = fact.expected
  if (!isRecord(expected)) return undefined
  return typeof expected.unit === 'string' ? expected.unit : undefined
}

export function factExpectedText(fact: Record<string, unknown>): string {
  const expected = fact.expected
  if (expected === null || expected === undefined) return '—'
  if (typeof expected !== 'object') return String(expected)
  const obj = expected as Record<string, unknown>
  const kind = String(fact.kind ?? '')
  const unit = obj.unit ? ` ${String(obj.unit)}` : ''
  if (kind === 'scalar') {
    if (obj.value === null || obj.value === undefined || obj.value === '') return '—'
    const tolerance =
      typeof obj.tolerance === 'number' && obj.tolerance > 0
        ? `（容差 ±${obj.tolerance}）`
        : ''
    const base = formatNumeric(obj.value, String(fact.label ?? ''), unit ? String(obj.unit) : undefined)
    const suffix = looksMonetary(String(fact.label ?? ''), obj.unit ? String(obj.unit) : undefined) ? '' : unit
    return `${base}${suffix}${tolerance}`
  }
  if (kind === 'set' || kind === 'topk') {
    const values = Array.isArray(obj.values) ? obj.values.map(String) : []
    if (values.length) return `候选：${values.join(' / ')}`
    return obj.value === null || obj.value === undefined ? '—' : String(obj.value)
  }
  if (kind === 'date') {
    return obj.value === null || obj.value === undefined ? '—' : String(obj.value)
  }
  if (kind === 'boolean') {
    return obj.value === false ? '不应出现' : '应出现'
  }
  const keywords = Array.isArray(obj.keywords)
    ? obj.keywords.map(String).filter(Boolean)
    : []
  if (keywords.length) return `包含：${keywords.join(' / ')}`
  if (kind === 'text' || kind === 'semantic') {
    const values = Array.isArray(obj.values) ? obj.values.map(String).filter(Boolean) : []
    if (values.length) return `包含：${values.join(' / ')}`
    if (obj.value === false) return '不应出现'
    if (obj.value !== null && obj.value !== undefined && obj.value !== '') {
      return String(obj.value)
    }
    return '应出现'
  }
  return textOf(expected)
}

export function factActualText(fact: Record<string, unknown>): string {
  const actual = fact.actual
  if (actual === null || actual === undefined) return '未命中'
  if (typeof actual !== 'object') return String(actual)
  const obj = actual as Record<string, unknown>
  if (obj.bound_value !== null && obj.bound_value !== undefined && obj.bound_value !== '') {
    const name =
      obj.bound_name !== null && obj.bound_name !== undefined && obj.bound_name !== ''
        ? `（${String(obj.bound_name)}）`
        : ''
    const formatted = formatNumeric(
      obj.bound_value,
      String(fact.label ?? ''),
      expectedUnit(fact),
    )
    return `${formatted}${name}`
  }
  if (Array.isArray(obj.hits)) {
    const hits = obj.hits.map(String).filter(Boolean)
    return hits.length ? `命中：${hits.join(' / ')}` : '未命中'
  }
  if (Array.isArray(obj.numbers)) {
    const numbers = obj.numbers.map(String)
    return numbers.length ? `提取数值：${numbers.join(', ')}` : '未提取到数值'
  }
  if (Array.isArray(obj.values)) {
    const values = obj.values.map(String)
    return values.length ? `提取：${values.join(' / ')}` : '未提取到候选值'
  }
  if (obj.value !== null && obj.value !== undefined && obj.value !== '') {
    return formatNumeric(obj.value, String(fact.label ?? ''), expectedUnit(fact))
  }
  return textOf(actual)
}

function parseRuleConfig(value: unknown): RuleConfig | null {
  if (!isRecord(value)) return null
  const config: RuleConfig = {
    amounts: Array.isArray(value.amounts) ? (value.amounts as RuleConfig['amounts']) : [],
    counts: Array.isArray(value.counts) ? (value.counts as RuleConfig['counts']) : [],
    dates: Array.isArray(value.dates) ? value.dates.map(String) : [],
    must_contain: Array.isArray(value.must_contain)
      ? value.must_contain.map(String)
      : [],
    must_not_contain: Array.isArray(value.must_not_contain)
      ? value.must_not_contain.map(String)
      : [],
  }
  const hasAny =
    config.amounts.length ||
    config.counts.length ||
    config.dates.length ||
    config.must_contain.length ||
    config.must_not_contain.length
  return hasAny ? config : null
}

function ruleLabel(type: string, label?: unknown): string {
  if (label !== null && label !== undefined && String(label).trim()) {
    return String(label)
  }
  return RULE_TYPE_LABELS[type] ?? type
}

/** 把运行时 rule_check.checks 转成对照行（优先使用，避免前端重算规则）。 */
export function ruleRowsFromChecks(raw: unknown): RuleRow[] {
  if (!isRecord(raw) || !Array.isArray(raw.checks)) return []
  return raw.checks
    .filter(isRecord)
    .map((check, index) => {
      const type = String(check.type ?? 'rule')
      const label = ruleLabel(type, check.label)
      return {
        id: `${type}-${index}`,
        label,
        expectedText: formatRuleValue(check.expected, type, label),
        actualText: formatRuleValue(check.actual, type, label),
        passed: typeof check.passed === 'boolean' ? check.passed : null,
        reason: check.reason ? String(check.reason) : null,
        mode: check.mode ? String(check.mode) : null,
        boundName: check.bound_name ? String(check.bound_name) : null,
        evidence: check.evidence ? String(check.evidence) : null,
      }
    })
}

/** 没有运行时校验结果时，按用例配置生成期望行（actual 留空）。 */
export function ruleRowsFromConfig(config: RuleConfig | null): RuleRow[] {
  if (!config) return []
  const rows: RuleRow[] = []
  config.amounts.forEach((item, index) => {
    const label = ruleLabel('amount', item.label)
    rows.push({
      id: `amount-${index}`,
      label,
      expectedText:
        item.tolerance && item.tolerance > 0
          ? `¥${Number(item.value).toFixed(2)}（容差 ±${item.tolerance}）`
          : `¥${Number(item.value).toFixed(2)}`,
      actualText: '未校验',
      passed: null,
    })
  })
  config.counts.forEach((item, index) => {
    rows.push({
      id: `count-${index}`,
      label: ruleLabel('count', item.label),
      expectedText: String(item.value),
      actualText: '未校验',
      passed: null,
    })
  })
  config.dates.forEach((item, index) => {
    rows.push({
      id: `date-${index}`,
      label: '日期',
      expectedText: String(item),
      actualText: '未校验',
      passed: null,
    })
  })
  config.must_contain.forEach((item, index) => {
    rows.push({
      id: `must-${index}`,
      label: '必须包含',
      expectedText: String(item),
      actualText: '未校验',
      passed: null,
    })
  })
  config.must_not_contain.forEach((item, index) => {
    rows.push({
      id: `must-not-${index}`,
      label: '禁止包含',
      expectedText: String(item),
      actualText: '未校验',
      passed: null,
    })
  })
  return rows
}

function factsFromDetail(detail: ExecutionDetail): FactRow[] {
  const raw = detail.fact_results
  const list = Array.isArray(raw)
    ? raw
    : isRecord(detail.answer_grading) && Array.isArray(detail.answer_grading.facts)
      ? (detail.answer_grading.facts as Array<Record<string, unknown>>)
      : []
  return list.filter(isRecord).map((fact, index) => {
    const isGate = Boolean(fact.is_gate)
    return {
      id: String(fact.fact_id ?? `fact-${index}`),
      label: String(fact.label ?? fact.fact_id ?? `事实 ${index + 1}`),
      kind: String(fact.kind ?? ''),
      section: isGate ? 'must' : 'may',
      expectedText: factExpectedText(fact),
      actualText: factActualText(fact),
      passed: typeof fact.passed === 'boolean' ? fact.passed : null,
      reason: fact.reason ? String(fact.reason) : null,
      mode: fact.mode ? String(fact.mode) : null,
      boundName: fact.bound_name ? String(fact.bound_name) : null,
    }
  })
}

function expectedTurnFor(
  snapshot: CaseSnapshot,
  turnIndex: number,
): TurnItem | null {
  const turns = Array.isArray(snapshot.turns) ? snapshot.turns : []
  if (!turns.length) return null
  const exact = turns.find((item, index) => {
    const indexValue = item.index ?? index + 1
    return Number(indexValue) === turnIndex
  })
  return exact ?? turns[turnIndex - 1] ?? null
}

function passedOfRuleRows(rows: RuleRow[]): boolean | null {
  const measured = rows.filter((row) => row.passed !== null)
  if (!measured.length) return null
  return measured.every((row) => row.passed === true)
}

function buildTurns(
  detail: ExecutionDetail,
  snapshot: CaseSnapshot,
  caseType: string,
  gradingMode: string,
  singleChecks: RuleRow[],
): TurnExpectation[] {
  const actualTurns: TurnTrace[] = Array.isArray(detail.turns) ? detail.turns : []
  if (!actualTurns.length) return []

  return actualTurns.map((turn) => {
    if (caseType === 'STATIC_MULTI') {
      const expectedTurn = expectedTurnFor(snapshot, turn.turn_index)
      const expectedText = expectedTurn?.expected_answer ?? null
      const config = parseRuleConfig(expectedTurn?.expected_rules)
      const checks =
        gradingMode === 'ANSWER_CONTRACT'
          ? []
          : ruleRowsFromChecks(turn.rule_check)
      const ruleRows = checks.length ? checks : gradingMode === 'ANSWER_CONTRACT' ? [] : ruleRowsFromConfig(config)
      return {
        turnIndex: turn.turn_index,
        expectedText,
        expectedSource: expectedText
          ? 'EXPECTED_ANSWER'
          : ruleRows.length
            ? 'RULES'
            : 'NONE',
        ruleRows,
        query: turn.query ?? '',
        answer: turn.final_answer ?? '',
        passed:
          ruleRows.length && ruleRows.every((row) => row.passed !== null)
            ? passedOfRuleRows(ruleRows)
            : null,
        simulatorStatus: turn.simulator_status,
      }
    }

    const expectedText = snapshot.expected_answer ?? null
    const config = parseRuleConfig(snapshot.expected_rules)
    const ruleRows =
      gradingMode === 'ANSWER_CONTRACT'
        ? []
        : singleChecks.length
          ? singleChecks
          : ruleRowsFromConfig(config)
    return {
      turnIndex: turn.turn_index,
      expectedText,
      expectedSource: expectedText
        ? 'EXPECTED_ANSWER'
        : ruleRows.length
          ? 'RULES'
          : 'NONE',
      ruleRows,
      query: turn.query ?? '',
      answer: turn.final_answer ?? '',
      passed:
        ruleRows.length && ruleRows.every((row) => row.passed !== null)
          ? passedOfRuleRows(ruleRows)
          : null,
      simulatorStatus: turn.simulator_status,
    }
  })
}

function buildSession(snapshot: CaseSnapshot): SessionExpectation | null {
  const raw = snapshot.session_expected
  if (!isRecord(raw)) return null
  const text =
    raw.expected_answer !== null && raw.expected_answer !== undefined
      ? String(raw.expected_answer)
      : null
  const ruleRows = ruleRowsFromConfig(parseRuleConfig(raw.expected_rules))
  if (!text && !ruleRows.length) return null
  return { text, ruleRows }
}

function releasedMap(turns: TurnTrace[]): Map<string, number> {
  const map = new Map<string, number>()
  turns.forEach((turn) => {
    const items = Array.isArray(turn.hidden_context_released)
      ? turn.hidden_context_released
      : []
    items.forEach((item) => {
      if (!isRecord(item)) return
      const key = String(item.key ?? '')
      if (key && !map.has(key)) map.set(key, turn.turn_index)
    })
  })
  return map
}

function terminateKeywords(snapshot: CaseSnapshot): string[] {
  const raw = snapshot.terminate_conditions
  if (!isRecord(raw) || !Array.isArray(raw.keywords)) return []
  return raw.keywords.map(String).filter(Boolean)
}

function buildDynamic(
  detail: ExecutionDetail,
  snapshot: CaseSnapshot,
): DynamicExpectation {
  const released = releasedMap(Array.isArray(detail.turns) ? detail.turns : [])
  const hiddenRaw = Array.isArray(snapshot.hidden_context)
    ? (snapshot.hidden_context as HiddenContextItem[])
    : []
  const hidden: HiddenReleaseRow[] = hiddenRaw.map((item) => ({
    key: String(item.key ?? ''),
    content: String(item.content ?? ''),
    releaseHint: String(item.release_hint ?? ''),
    releasedTurn: released.get(String(item.key ?? '')) ?? null,
  }))
  return {
    goal: snapshot.user_goal ?? null,
    persona: snapshot.persona_description ?? null,
    initialQuery: snapshot.initial_query ?? null,
    expectedAnswer: snapshot.expected_answer ?? null,
    hidden,
    maxTurns:
      typeof snapshot.max_turns === 'number' ? snapshot.max_turns : null,
    terminateKeywords: terminateKeywords(snapshot),
    rules: ruleRowsFromConfig(parseRuleConfig(snapshot.expected_rules)),
  }
}

export function resolveExpectation(detail: ExecutionDetail): ResolvedExpectation {
  const snapshot = (isRecord(detail.case_snapshot)
    ? detail.case_snapshot
    : {}) as CaseSnapshot
  const caseType = String(snapshot.case_type ?? 'SINGLE')
  const gradingMode = String(
    detail.grading_mode ?? snapshot.grading_mode ?? 'LEGACY',
  )
  const singleChecks =
    gradingMode === 'ANSWER_CONTRACT' ? [] : ruleRowsFromChecks(detail.rule_result)
  const facts = factsFromDetail(detail)
  const turns = buildTurns(
    detail,
    snapshot,
    caseType,
    gradingMode,
    singleChecks,
  )
  const session = buildSession(snapshot)
  if (session && session.ruleRows.length) {
    const byLabel = new Map<string, { passed: boolean; actualText: string }>()
    turns.forEach((turn) => {
      turn.ruleRows.forEach((row) => {
        if (row.passed === null) return
        const previous = byLabel.get(row.label)
        if (!previous) {
          byLabel.set(row.label, { passed: row.passed, actualText: row.actualText })
        } else if (row.passed === false) {
          byLabel.set(row.label, { passed: false, actualText: row.actualText })
        }
      })
    })
    session.ruleRows = session.ruleRows.map((row) => {
      const hit = byLabel.get(row.label)
      return hit
        ? { ...row, passed: hit.passed, actualText: hit.actualText }
        : row
    })
  }
  const dynamic =
    caseType === 'DYNAMIC_MULTI' ? buildDynamic(detail, snapshot) : null
  const hasExpectation = Boolean(
    turns.some(
      (turn) =>
        turn.expectedText ||
        turn.ruleRows.length ||
        turn.expectedSource !== 'NONE',
    ) ||
      session ||
      facts.length ||
      dynamic?.goal ||
      dynamic?.expectedAnswer,
  )
  return {
    caseType,
    snapshot,
    version: detail.case_version,
    asOf: snapshot.as_of ?? null,
    gradingMode,
    turns,
    session,
    dynamic,
    facts,
    hasExpectation,
  }
}

/** 用例里的答案包络（目前用于空态提示，保持类型收口）。 */
export function envelopeFacts(snapshot: CaseSnapshot): AnswerEnvelope | null {
  return snapshot.answer_envelope ?? null
}

export function factRowsForDisplay(facts: FactRow[]): FactRow[] {
  const must = facts.filter((fact) => fact.section === 'must')
  return must.length ? must : facts
}
