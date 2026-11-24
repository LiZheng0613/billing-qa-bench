/**
 * 对话轮次视图模型：把执行详情归一化成「每轮一份档案」。
 *
 * 原则：
 * - 只用后端真实返回的字段，缺失就留空（界面负责降级说明），不编造数字与时延。
 * - 轮次级总分 / 四维分后端不提供，除单轮执行外一律为 null，由规则与执行级结论推导状态。
 * - 裁判 error_tags 不携带轮次，多轮时统一按「会话级」呈现，不硬分到某一轮。
 */
import type {
  CaseSnapshot,
  ExecutionDetail,
  JudgeResult,
  SelfCheckResult,
  TurnTrace,
} from '../../../types'

/** 前端兜底通过线：只在执行记录与用例快照都没有阈值时使用（展示时需注明来源）。 */
export const DEFAULT_PASS_LINE = 80

/** 通过线来源：执行时口径优先，历史执行不随后端配置漂移。 */
export type PassLineSource = 'judge_result' | 'answer_grading' | 'case_snapshot' | 'default'

export const PASS_LINE_SOURCE_LABEL: Record<PassLineSource, string> = {
  judge_result: '执行口径',
  answer_grading: '执行口径',
  case_snapshot: '用例快照',
  default: '前端默认',
}

/**
 * 通过线读取链（后端真实落库优先，禁止前端写死）：
 * 1. LEGACY 裁判聚合结果自带 threshold（judge_result.threshold）；
 * 2. 契约判分明细带 pass_threshold（answer_grading.pass_threshold）；
 * 3. 执行时冻结的用例快照 pass_threshold；
 * 4. 前端兜底常量（需在界面注明）。
 */
export function passLineOf(detail: ExecutionDetail | null): { value: number; source: PassLineSource } {
  const judge = obj(detail?.judge_result)
  const fromJudge = num(judge?.threshold) ?? num(judge?.pass_threshold)
  if (fromJudge !== null && fromJudge > 0) return { value: fromJudge, source: 'judge_result' }
  const grading = obj(detail?.answer_grading)
  const fromGrading = num(grading?.pass_threshold) ?? num(grading?.threshold)
  if (fromGrading !== null && fromGrading > 0) return { value: fromGrading, source: 'answer_grading' }
  const snapshot = obj(detail?.case_snapshot)
  const fromSnapshot = num(snapshot?.pass_threshold)
  if (fromSnapshot !== null && fromSnapshot > 0) return { value: fromSnapshot, source: 'case_snapshot' }
  return { value: DEFAULT_PASS_LINE, source: 'default' }
}
/** 归因作用域：这两类问题按整通会话判定 */
export const SESSION_SCOPE_TAGS = new Set(['未主动反问', '超出最大轮次'])

export type Verdict = 'pass' | 'warn' | 'fail' | 'unknown'

export const DIMENSIONS: Array<{ key: string; label: string; max: number }> = [
  { key: 'data_accuracy', label: '数据准确度', max: 40 },
  { key: 'logic_consistency', label: '逻辑一致性', max: 30 },
  { key: 'goal_completion', label: '目标完成度', max: 20 },
  { key: 'tone_clarity', label: '语气清晰度', max: 10 },
]

export const TAG_TONE: Record<string, string> = {
  '数值错误/数据幻觉': 'rose',
  '口径遗漏': 'amber',
  '上下文指代丢失': 'sky',
  '未主动反问': 'violet',
  '超出最大轮次': 'slate',
  '系统异常': 'orange',
}

export const SCOPE_META: Record<'session' | 'turn', { label: string; hint: string }> = {
  session: { label: '会话级', hint: '按整通会话判定，只作为本轮上下文，不参与本轮 pass' },
  turn: { label: '轮次级', hint: '归属单轮，计入本轮判定' },
}

export const VERDICT_META: Record<Verdict, { label: string; short: string; tone: string }> = {
  pass: { label: '通过', short: '通过', tone: 'pass' },
  warn: { label: '部分命中', short: '部分', tone: 'warn' },
  fail: { label: '未通过', short: '未过', tone: 'fail' },
  unknown: { label: '未判定', short: '未判', tone: 'unknown' },
}

export interface TurnRuleVM {
  name: string
  pass: boolean
  why: string
  type: string
  expected: string
  actual: string
  evidence: string
}

export interface TurnFactVM {
  id: string
  label: string
  kind: string
  pass: boolean
  degraded: boolean
  expected: string
  actual: string
  reason: string
  isGate: boolean
}

export interface TurnStepVM {
  tool: string
  name: string
  param: string
  ms: number
  status: 'ok' | 'warn'
  phase: string
  args: unknown
  result: unknown
  raw: Record<string, unknown>
}

export interface TurnRequestVM {
  method: string
  url: string
  headers: Array<[string, string]>
  body: unknown
  hasHeaders: boolean
  hasPayload: boolean
}

export interface TurnVM {
  index: number
  turn: TurnTrace
  query: string
  answer: string
  intent: string
  source: string
  sessionId: string | null
  at: string
  ttft: number | null
  latency: number | null
  promptTokens: number
  completionTokens: number
  tokens: number
  /** 单轮执行才有轮次级总分；多轮为 null（界面显示 —） */
  score: number | null
  verdict: Verdict
  pass: boolean
  gateOk: boolean
  rules: TurnRuleVM[]
  facts: TurnFactVM[]
  /** facts 是否来自本轮（后端暂无轮次级事实时为 false，回落到执行级最终答案） */
  factsFromTurn: boolean
  /** facts 的归属：turn=本轮独立，execution=执行级最终答案，none=没有 */
  factsScope: 'turn' | 'execution' | 'none'
  /** 轮次级契约门槛（后端目前按整通判定，缺失时显示 —） */
  contract: { passed: number; total: number } | null
  steps: TurnStepVM[]
  tags: string[]
  sessionTags: string[]
  dimensions: Array<{ key: string; label: string; value: number; max: number }>
  /** 本轮独立裁判结论；多轮时后端不提供轮次级 reason 就留空，不拿会话级结论顶替 */
  judgeReason: string
  /** judgeReason 的归属：turn=本轮独立，none=本轮没有 */
  judgeReasonScope: 'turn' | 'none'
  /** 执行级裁判结论：整通会话的汇总，多轮时不归属单轮 */
  sessionJudgeReason: string
  judgeModels: string[]
  expected: string
  expectedSource: string
  /** 期望答案的归属：turn=本轮快照，session=会话级（当前只在单轮时回落），none=缺失 */
  expectedScope: 'turn' | 'session' | 'none'
  sql: string
  request: TurnRequestVM
  responseBody: unknown
  responseMode: string
  responseFrames: number | null
  /** 响应状态码：新执行随 response_payload.status_code 落库；历史执行为 null */
  responseStatus: number | null
  responseHeaders: Array<[string, string]>
  variables: Array<[string, string]>
  hiddenContext: unknown[]
  selfCheck: boolean
  evidenceRows: number
}

export interface SessionStats {
  turns: number
  passCount: number
  warnCount: number
  failCount: number
  unknownCount: number
  tagCount: number
  sessionTagCount: number
  avgLatency: number | null
  totalTokens: number
  lowest: TurnVM | null
  sessionIds: string[]
  consistentSession: boolean
}

export interface TraceModel {
  turns: TurnVM[]
  session: SessionStats
  selfCheck: SelfCheckResult | null
  /** 失败执行里 self_check = SKIPPED 的后端跳过原因；通过用例恒为 null，界面不展示自检内容 */
  selfCheckSkipReason: string | null
  selfCheckTurn: TurnVM | null
  gradingMode: string
  /** 本次执行实际使用的通过线（passLineOf 读取链），组件不得再各自写死 */
  passLine: number
  passLineSource: PassLineSource
  /** 执行级综合分（与 pass 判定同源）。多轮没有轮次分时，用它说明分数口径 */
  totalScore: number | null
  /** 执行级四维得分（judge_result 里唯一真实存在的一份；轮次级字段后端没有） */
  dimensions: Array<{ key: string; label: string; value: number; max: number }>
  /** 执行级判定（pass_flag）：通过的用例界面不展示任何自检相关内容 */
  executionPassed: boolean
  contract: {
    exists: boolean
    passed: boolean
    gates: Array<{ key: string; label: string; passed: boolean }>
    facts: TurnFactVM[]
    behavior: Record<string, unknown> | null
    extras: Array<Record<string, unknown>>
    scorecard: Array<{ key: string; label: string; value: number }>
    totalScore: number | null
    degraded: boolean
  }
  judges: JudgeResult[]
  judgeTags: string[]
  sessionVariables: Array<[string, string]>
}

const GATE_LABEL: Record<string, string> = {
  safety: '安全',
  behavior: '行为',
  facts: '事实',
  extras: '扩展',
}

const SCORECARD_LABEL: Record<string, string> = {
  fact_completeness: '事实完整',
  assumption_honesty: '假设诚实',
  clarity: '表达清晰',
  helpful_extra: '有帮助扩展',
}

const RULE_LABEL: Record<string, string> = {
  must_contain: '必须包含',
  must_not_contain: '禁止包含',
}

// 与原型一致的 5 个阶段（plan / sql / query / verify / compose），未知阶段归「其他」
const PHASE_META: Record<string, { label: string; color: string }> = {
  规划: { label: '规划', color: '#c7d2fe' },
  '生成 SQL': { label: '生成 SQL', color: '#a5b4fc' },
  执行查询: { label: '执行查询', color: '#6366f1' },
  口径校验: { label: '口径校验', color: '#34d399' },
  组织回复: { label: '组织回复', color: '#cbd5e1' },
  其他: { label: '其他', color: '#94a3b8' },
}

/** 显式阶段字段优先：中文阶段名与英文 type 都能直接映射 */
const PHASE_ALIAS: Record<string, string> = {
  规划: '规划',
  plan: '规划',
  planning: '规划',
  生成sql: '生成 SQL',
  sql: '生成 SQL',
  执行查询: '执行查询',
  query: '执行查询',
  execute: '执行查询',
  exec: '执行查询',
  取数: '执行查询',
  口径校验: '口径校验',
  校验: '口径校验',
  verify: '口径校验',
  check: '口径校验',
  validate: '口径校验',
  组织回复: '组织回复',
  生成: '组织回复',
  compose: '组织回复',
  answer: '组织回复',
  render: '组织回复',
}

export function phaseMeta(phase: string): { label: string; color: string } {
  return PHASE_META[phase] ?? PHASE_META['其他']
}

function str(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function obj(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function errorTagsOf(judges: JudgeResult[]): string[] {
  const out: string[] = []
  judges.forEach((judge) => {
    ;(judge.error_tags ?? []).forEach((tag) => {
      if (tag && !out.includes(tag)) out.push(tag)
    })
  })
  return out
}

function totalScoreOf(detail: ExecutionDetail): number | null {
  if (detail.grading_mode === 'ANSWER_CONTRACT') {
    const quality = obj(obj(detail.judge_result)?.quality)
    const value = num(quality?.total_score)
    if (value !== null) return value
    return num(obj(detail.answer_grading)?.total_score)
  }
  return num(detail.total_score)
}

function dimensionsOf(detail: ExecutionDetail): Array<{ key: string; label: string; value: number; max: number }> {
  const source =
    detail.grading_mode === 'ANSWER_CONTRACT'
      ? obj(obj(detail.judge_result)?.quality)?.dimension_scores
      : obj(detail.judge_result)?.dimension_scores
  const scores = obj(source)
  if (!scores) return []
  return DIMENSIONS.filter((dim) => typeof scores[dim.key] === 'number').map((dim) => ({
    ...dim,
    value: Number(scores[dim.key]),
  }))
}

function rulesOf(
  turn: TurnTrace,
  detail: ExecutionDetail,
  single: boolean,
): TurnRuleVM[] {
  const check = obj(turn.rule_check)
  let checks = list(check?.checks)
  // 单轮执行时允许回退到执行级规则结果（多轮无法拆分，不硬塞）
  if (!checks.length && single) checks = list(obj(detail.rule_result)?.checks)
  const rows: TurnRuleVM[] = checks.map((raw) => {
    const item = obj(raw) ?? {}
    const type = str(item.type)
    return {
      name: str(item.label) || RULE_LABEL[type] || type || '校验项',
      pass: item.passed !== false,
      why: str(item.reason) || (item.passed === false ? '未命中' : ''),
      type,
      expected: str(item.expected),
      actual: str(item.actual),
      evidence: str(item.evidence),
    }
  })
  if (!rows.length && check && check.passed === false) {
    rows.push({
      name: '本轮规则校验',
      pass: false,
      why: str(check.reason) || '未通过',
      type: '',
      expected: '',
      actual: '',
      evidence: '',
    })
  }
  return rows
}

function factText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (typeof value === 'object') {
    const item = value as Record<string, unknown>
    // 后端事实常见 { value: 271.2 }：直接展示数值，避免界面里出现裸 JSON
    // 契约事实的 actual/expected 是结构体，优先展示真正比对的数值，而不是整段 JSON
    if (typeof item.bound_value === 'string' || typeof item.bound_value === 'number') {
      return String(item.bound_value)
    }
    if (typeof item.value === 'string' || typeof item.value === 'number') return String(item.value)
    const keys = Object.keys(item)
    if (keys.length === 1) {
      const only = item[keys[0]]
      if (typeof only === 'string' || typeof only === 'number') return String(only)
    }
    return str(value)
  }
  return String(value)
}

function mapFacts(rawFacts: unknown[]): TurnFactVM[] {
  return rawFacts.map((raw) => {
    const item = obj(raw) ?? {}
    return {
      id: str(item.fact_id),
      label: str(item.label) || str(item.fact_id) || '事实',
      kind: str(item.kind),
      pass: item.passed === true,
      degraded: item.degraded === true,
      expected: factText(item.expected),
      actual: factText(item.actual),
      reason: str(item.reason),
      isGate: item.is_gate === true,
    }
  })
}

function factsOf(detail: ExecutionDetail): TurnFactVM[] {
  const grading = obj(detail.answer_grading)
  const rawFacts = Array.isArray(detail.fact_results)
    ? detail.fact_results
    : list(grading?.facts)
  return mapFacts(rawFacts)
}

function phaseOf(item: Record<string, unknown>): string {
  const explicit = `${str(item.phase)}`.toLowerCase().replace(/[\s_-]/g, '')
  if (explicit && PHASE_ALIAS[explicit]) return PHASE_ALIAS[explicit]
  const kind = `${str(item.kind)}`.toLowerCase().replace(/[\s_-]/g, '')
  if (kind && PHASE_ALIAS[kind]) return PHASE_ALIAS[kind]

  // 没有可用阶段字段时，退化为「阶段 + 名称 + 工具」的关键词归类
  const text = `${str(item.phase)} ${str(item.kind)} ${str(item.capability)} ${str(item.name)} ${str(item.tool)}`.toLowerCase()
  if (/verify|check|validate|fact|rule|assert|contract|口径|校验/.test(text)) return '口径校验'
  if (/plan|route|intent|rewrite|规划/.test(text)) return '规划'
  if (/sql/.test(text)) return '生成 SQL'
  if (/query|search|retrieve|db|fetch|lookup|查询|取数/.test(text)) return '执行查询'
  if (/render|format|compose|answer|generate|write|回复|生成/.test(text)) return '组织回复'
  return '其他'
}

function stepsOf(turn: TurnTrace): TurnStepVM[] {
  return (turn.tool_calls ?? [])
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    .map((item, index) => {
      const ms =
        num(item.latency_ms) ?? num(item.elapsed_ms) ?? num(item.duration_ms) ?? num(item.ms) ?? 0
      const flag = str(item.status).toUpperCase()
      const status: 'ok' | 'warn' = /FAIL|ERROR|TIMEOUT|DENIED|WARN/.test(flag) ? 'warn' : 'ok'
      const tool = str(item.tool) || str(item.name) || str(item.kind) || `tool_${index + 1}`
      return {
        tool,
        name: str(item.name) || str(item.kind) || tool,
        param: str(item.query) || str(item.capability) || str(item.summary),
        ms,
        status,
        phase: phaseOf(item),
        args: item.args ?? item.arguments ?? item.input ?? null,
        result: item.result ?? item.output ?? null,
        raw: item,
      }
    })
}

function headersOf(payload: Record<string, unknown> | null): Array<[string, string]> {
  const headers = payload?.headers
  if (!headers) return []
  if (Array.isArray(headers)) {
    return headers
      .map((item) => {
        if (Array.isArray(item) && item.length >= 2) return [String(item[0]), String(item[1])] as [string, string]
        const pair = obj(item)
        if (pair) {
          const key = str(pair.name ?? pair.key)
          if (key) return [key, str(pair.value)] as [string, string]
        }
        return null
      })
      .filter((item): item is [string, string] => item !== null)
  }
  const record = obj(headers)
  if (!record) return []
  return Object.entries(record).map(([key, value]) => [key, str(value)] as [string, string])
}

/** 部分历史执行只把 SQL 写在响应体里，generated_sql 列未回填：两处都查。 */
export function turnSql(turn: TurnTrace | undefined | null): string {
  if (!turn) return ''
  const response = obj(turn.response_raw)
  return str(turn.generated_sql) || str(obj(obj(response?.body)?.data)?.generated_sql)
}

function requestOf(turn: TurnTrace): TurnRequestVM {  const payload = obj(turn.request_payload)
  const headers = headersOf(payload)
  return {
    method: str(payload?.method) || 'POST',
    url: str(payload?.url),
    headers,
    body: payload?.body ?? null,
    hasHeaders: headers.length > 0,
    hasPayload: Boolean(payload),
  }
}

function expectedOf(
  snapshot: CaseSnapshot | undefined,
  index: number,
  single: boolean,
): { text: string; source: string; scope: 'turn' | 'session' | 'none' } {
  const turns = snapshot?.turns ?? []
  const matched = turns.find((item) => item.index === index) ?? turns[index - 1]
  if (matched?.expected_answer) {
    return { text: matched.expected_answer, source: `用例快照 · 第 ${index} 轮期望`, scope: 'turn' }
  }
  // 单轮用例的顶层期望就是唯一一轮的期望；多轮不把会话级期望硬套到每一轮
  if (single && snapshot?.expected_answer) {
    return { text: snapshot.expected_answer, source: '用例快照 · 期望答案', scope: 'session' }
  }
  return { text: '', source: '', scope: 'none' }
}

function variablesOf(snapshot: CaseSnapshot | undefined): Array<[string, string]> {
  const raw = obj((snapshot as Record<string, unknown> | undefined)?.request_variables)
  if (!raw) return []
  return Object.entries(raw).map(([key, value]) => [key, typeof value === 'string' ? value : str(value)])
}

function triggerTurnIndex(selfCheck: SelfCheckResult | null): number | null {
  if (!selfCheck) return null
  // 后端已落库触发轮次（SelfCheckResult.turn_index）时优先使用；老执行记录才退正则解析
  if (typeof selfCheck.turn_index === 'number' && selfCheck.turn_index > 0) {
    return selfCheck.turn_index
  }
  const text = `${selfCheck.trigger ?? ''}`
  const match = text.match(/T\s*(\d+)|第\s*(\d+)\s*轮/i)
  if (match) return Number(match[1] ?? match[2])
  return null
}

/** 自检是否真实发生：DONE=已追问出结论；ERROR=自检尝试本身失败（模板缺失/接口异常）。
 * SKIPPED 是「未触发」形态（通过的用例落「LLM 裁决通过，未触发追问自检」，
 * self_check.py:128），界面对话视图不展示任何自检内容。 */
export function selfCheckPerformed(record: SelfCheckResult | null | undefined): boolean {
  return Boolean(record) && record!.status !== 'SKIPPED'
}

/** 被测 Agent 本轮 usage：优先响应体 data.usage / usage，流式退 merged metadata；缺失返回 0。 */
function usageOf(turn: TurnTrace): { prompt: number; completion: number } {
  const response = obj(turn.response_raw)
  const body = obj(response?.body)
  const data = obj(body?.data)
  const meta = obj(response?.metadata)
  const usage = obj(data?.usage) ?? obj(body?.usage) ?? obj(meta?.usage)
  const prompt = num(usage?.prompt_tokens) ?? num(usage?.input_tokens)
  const completion = num(usage?.completion_tokens) ?? num(usage?.output_tokens)
  return { prompt: prompt ?? 0, completion: completion ?? 0 }
}

/** finish_reason：从落库的原始响应报文读取（JSON body 或 SSE metadata），没有就留空降级。 */
export function finishReasonOf(turn: TurnTrace | null | undefined): string {
  if (!turn) return ''
  const response = obj(turn.response_raw)
  const body = obj(response?.body)
  const data = obj(body?.data)
  const meta = obj(response?.metadata)
  return str(data?.finish_reason ?? body?.finish_reason ?? meta?.finish_reason)
}

export function buildTraceModel(detail: ExecutionDetail | null): TraceModel {
  const turns = (detail?.turns ?? []).slice().sort((a, b) => a.turn_index - b.turn_index)
  const single = turns.length === 1
  const score = detail ? totalScoreOf(detail) : null
  const passLine = passLineOf(detail)
  const dimensions = detail ? dimensionsOf(detail) : []
  const judges = detail?.judges ?? []
  const judgeTags = errorTagsOf(judges)
  // 执行级裁判结论：整通会话汇总；多轮时不归属单轮
  const executionJudgeReason = judges[0]?.reason ?? ''
  const facts = detail ? factsOf(detail) : []
  const grading = detail ? obj(detail.answer_grading) : null
  const contractGates = Object.entries(obj(grading?.gates) ?? {}).map(([key, passed]) => ({
    key,
    label: GATE_LABEL[key] ?? key,
    passed: Boolean(passed),
  }))
  const contractExists = Boolean(grading) || (detail?.grading_mode === 'ANSWER_CONTRACT' && score !== null)
  const sessionVariables = variablesOf(detail?.case_snapshot)
  // 自检仅在真实发生（DONE/ERROR）时参与呈现；通过的用例落 SKIPPED（self_check.py:128），
  // 对话界面不应出现任何自检内容（S1 派生块、页签、抽屉上下文全部由此收口）
  // 通过用例即便历史数据里残留 self_check=DONE 也不展示；SKIPPED 是「未触发」，
  // 失败执行需要把后端 skip_reason 带出来，不能笼统说「没有记录」。
  const rawSelfCheck = detail?.self_check ?? null
  const selfCheck =
    detail?.pass_flag !== true && selfCheckPerformed(rawSelfCheck) ? rawSelfCheck : null
  const selfCheckSkipReason =
    detail?.pass_flag !== true && rawSelfCheck?.status === 'SKIPPED'
      ? rawSelfCheck.skip_reason || '后端未记录跳过原因'
      : null
  const triggerIndex = triggerTurnIndex(selfCheck)
  const lastIndex = turns.length ? turns[turns.length - 1].turn_index : 0
  const assignedTurnTags = new Set<string>()
  turns.forEach((turn) => {
    ;(turn.error_tags ?? []).forEach((tag) => {
      if (tag) assignedTurnTags.add(String(tag))
    })
  })

  const turnVMs: TurnVM[] = turns.map((turn) => {
    const rules = rulesOf(turn, detail as ExecutionDetail, single)
    const turnScore = num(turn.judge_score)
    const contractPassed = num(turn.contract_passed)
    const contractTotal = num(turn.contract_total)
    const contract =
      contractPassed !== null && contractTotal !== null
        ? { passed: contractPassed, total: contractTotal }
        : null
    const contractOk = !contract || contract.passed >= contract.total
    const gateOk = rules.every((rule) => rule.pass) && contractOk
    const scoreValue = turnScore ?? (single ? score : null)
    let verdict: Verdict
    if (single && detail) {
      const passed = detail.pass_flag === true
      verdict = passed ? 'pass' : scoreValue !== null && scoreValue >= passLine.value ? 'warn' : 'fail'
    } else if (scoreValue !== null) {
      verdict = gateOk && scoreValue >= passLine.value ? 'pass' : scoreValue >= passLine.value ? 'warn' : 'fail'
    } else if (!rules.length && !contract) {
      // 多轮下没有轮次级分数，也没有规则/契约门槛：无法判定，不臆造通过
      verdict = 'unknown'
    } else {
      verdict = gateOk ? 'pass' : 'fail'
    }
    const response = obj(turn.response_raw)
    const usage = usageOf(turn)
    const sql = turnSql(turn)
    const isLast = turn.turn_index === lastIndex
    // 归因没有轮次字段：单轮可视为本轮；多轮统一放会话级，避免伪造归属
    const rawTurnTags = Array.isArray(turn.error_tags) ? turn.error_tags.map(String) : null
    const turnTags = rawTurnTags ?? (single ? judgeTags.filter((tag) => !SESSION_SCOPE_TAGS.has(tag)) : [])
    const sessionTags = rawTurnTags
      ? judgeTags.filter(
          (tag) => SESSION_SCOPE_TAGS.has(tag) || !assignedTurnTags.has(tag),
        )
      : single
        ? judgeTags.filter((tag) => SESSION_SCOPE_TAGS.has(tag))
        : judgeTags
    const expected = expectedOf(detail?.case_snapshot, turn.turn_index, single)
    const answer = turn.final_answer ?? ''
    const steps = stepsOf(turn)
    const evidence = Array.isArray(turn.evidence) ? turn.evidence : []
    const turnDimsRaw = obj(turn.dimension_scores)
    const turnDims = turnDimsRaw
      ? DIMENSIONS.filter((dim) => typeof turnDimsRaw[dim.key] === 'number').map((dim) => ({
          ...dim,
          value: Number(turnDimsRaw[dim.key]),
        }))
      : []
    const turnFactsRaw = Array.isArray(turn.fact_results) ? turn.fact_results : null
    const turnFacts = turnFactsRaw ? mapFacts(turnFactsRaw) : []
    const turnJudgeReason = str(turn.judge_reason)
    // 单轮执行时执行级结论就是这一轮的结论；多轮不把会话级结论挂到某一轮
    const judgeReason = turnJudgeReason || (single ? executionJudgeReason : '')
    const turnExpected = expected.scope === 'none' ? '' : expected.text
    return {
      index: turn.turn_index,
      turn,
      query: turn.query ?? '',
      answer,
      intent: turn.intent ?? '',
      source: turn.input_source,
      sessionId: turn.session_id,
      at: turn.created_at,
      ttft: turn.ttft_ms,
      latency: turn.latency_ms,
      // 被测 Agent 的逐轮 usage 优先（响应体 data.usage）；缺退模拟器 tokens（动态多轮），再退 0
      promptTokens: usage.prompt || turn.simulator_prompt_tokens || 0,
      completionTokens: usage.completion || turn.simulator_completion_tokens || 0,
      tokens:
        (usage.prompt || turn.simulator_prompt_tokens || 0) +
        (usage.completion || turn.simulator_completion_tokens || 0),
      score: scoreValue,
      verdict,
      pass: verdict === 'pass',
      gateOk,
      rules,
      // 契约只有执行级结果：挂在最后一轮并标注口径
      facts: turnFacts.length ? turnFacts : isLast ? facts : [],
      factsFromTurn: Boolean(turnFactsRaw),
      factsScope: turnFacts.length ? 'turn' : isLast && facts.length ? 'execution' : 'none',
      contract,
      steps,
      tags: turnTags,
      sessionTags,
      dimensions: turnDims.length ? turnDims : single ? dimensions : [],
      judgeReason,
      judgeReasonScope: judgeReason ? 'turn' : 'none',
      sessionJudgeReason: executionJudgeReason,
      judgeModels: judges.map((judge) => judge.model_name).filter(Boolean),
      expected: turnExpected,
      expectedSource: expected.source,
      expectedScope: expected.scope,
      sql,
      request: requestOf(turn),
      // SSE 落库没有 body（只有 frames/content），响应体回落到聚合内容；JSON 模式读 body
      responseBody: response?.body ?? response?.content ?? null,
      responseMode: str(response?.mode) || (response ? 'json' : ''),
      responseFrames: num(response?.frame_count),
      responseStatus: num(response?.status_code),
      // 响应头：新执行随 response_payload.headers 落库（已脱敏）；历史执行多数为空，按空值降级
      responseHeaders: headersOf(response),
      variables: sessionVariables,
      hiddenContext: list(turn.hidden_context_released),
      // 单轮执行的自检只能属于唯一一轮；多轮必须靠触发轮次字段，定位不到就不认领
      // 单轮只会有一个候选轮，但「有自检记录」仍是前提：900009 这类单轮失败但未跑自检的
      // 执行不能被标成「已触发」，否则标题与空态自相矛盾。
      selfCheck:
        Boolean(selfCheck) && (single || (triggerIndex !== null && triggerIndex === turn.turn_index)),
      evidenceRows: evidence.length,
    }
  })

  const passCount = turnVMs.filter((turn) => turn.verdict === 'pass').length
  const warnCount = turnVMs.filter((turn) => turn.verdict === 'warn').length
  const failCount = turnVMs.filter((turn) => turn.verdict === 'fail').length
  const unknownCount = turnVMs.filter((turn) => turn.verdict === 'unknown').length
  const sessionIds = [...new Set(turnVMs.map((turn) => turn.sessionId).filter((id): id is string => Boolean(id)))]
  const latencies = turnVMs.map((turn) => turn.latency).filter((value): value is number => typeof value === 'number')
  const lowest = turnVMs
    .filter((turn) => turn.score !== null)
    .reduce<TurnVM | null>((acc, turn) => {
      if (!acc) return turn
      return (turn.score ?? 0) < (acc.score ?? 0) ? turn : acc
    }, null)
  const session: SessionStats = {
    turns: turnVMs.length,
    passCount,
    warnCount,
    failCount,
    unknownCount,
    tagCount:
      new Set(turnVMs.flatMap((turn) => turn.tags)).size +
      new Set(turnVMs.flatMap((turn) => turn.sessionTags)).size,
    sessionTagCount: turnVMs.reduce((sum, turn) => sum + turn.sessionTags.length, 0),
    avgLatency: latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null,
    totalTokens: turnVMs.reduce((sum, turn) => sum + turn.tokens, 0),
    lowest,
    sessionIds,
    consistentSession: sessionIds.length <= 1,
  }

  return {
    turns: turnVMs,
    session,
    selfCheck,
    selfCheckSkipReason,
    selfCheckTurn: turnVMs.find((turn) => turn.selfCheck) ?? null,
    gradingMode: detail?.grading_mode ?? 'LEGACY',
    passLine: passLine.value,
    passLineSource: passLine.source,
    totalScore: score,
    dimensions,
    executionPassed: detail?.pass_flag === true,
    contract: {
      exists: contractExists,
      passed: Boolean(grading?.passed),
      gates: contractGates,
      facts,
      behavior: obj(grading?.behavior),
      extras: list(grading?.extras).map((item) => obj(item) ?? {}),
      scorecard: Object.entries(obj(grading?.scorecard) ?? {}).map(([key, value]) => ({
        key,
        label: SCORECARD_LABEL[key] ?? key,
        value: Number(value),
      })),
      totalScore: num(grading?.total_score),
      degraded: grading?.degraded === true,
    },
    judges,
    judgeTags,
    sessionVariables,
  }
}

/** 当前模式下的「综合分」口径，避免把不同模式的分数都叫裁判得分 */
export function scoreLabel(mode: string): { label: string; hint: string } {
  if (mode === 'ANSWER_CONTRACT') {
    return {
      label: '质量分参考',
      hint: '契约模式四维裁判质量分（仅参考，不参与通过判定）；通过与否只由契约闸门与终止状态决定',
    }
  }
  return { label: 'LLM 裁判综合评分', hint: '裁判四维加权总分；达到本次执行的通过线且硬门槛全过才算通过' }
}

export function fmtMs(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (value >= 1000) return `${(value / 1000).toFixed(2)}s`
  return `${Math.round(value)}ms`
}

export function fmtScore(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

export function bytesOf(value: unknown): string {
  try {
    const text = typeof value === 'string' ? value : JSON.stringify(value)
    return `${new Blob([text ?? '']).size} B`
  } catch {
    return '—'
  }
}

export function jsonText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function phaseBuckets(steps: TurnStepVM[]): Array<{
  key: string
  label: string
  color: string
  n: number
  ms: number
  warn: number
}> {
  const order = ['规划', '生成 SQL', '执行查询', '口径校验', '组织回复', '其他']
  return order
    .map((phase) => {
      const items = steps.filter((step) => step.phase === phase)
      return {
        key: phase,
        label: phaseMeta(phase).label,
        color: phaseMeta(phase).color,
        n: items.length,
        ms: items.reduce((sum, step) => sum + step.ms, 0),
        warn: items.filter((step) => step.status === 'warn').length,
      }
    })
    .filter((group) => group.n > 0)
}

export function statusText(turn: TurnVM): string {
  return VERDICT_META[turn.verdict].label
}
