<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { api } from '../../api'
import type {
  AnswerEnvelope,
  CaseAIDraft,
  CasePayload,
  CaseSessionMode,
  CaseType,
  DraftFact,
  DraftMissing,
  DraftSource,
  DraftTurn,
  Environment,
  FactAtom,
  FactKind,
  FactVerificationStatus,
  GradingMode,
  HiddenContextItem,
  InputType,
  JudgeStrategy,
  ModelConfig,
  PlanSpec,
  PromptListItem,
  ResponseContract,
  RuleConfig,
  TemplateStrategy,
  TestCase,
  TurnItem,
} from '../../types'
import { caseTypeLabel } from '../../utils/format'
import { useToastStore } from '../../stores/toast'

interface AmountRule {
  label: string
  value: number | null
  tolerance: number
  aliasesText: string
  requireName: boolean
}

interface HiddenRule {
  key: string
  content: string
  release_hint: string
}

interface TurnRule {
  query: string
  expected_answer: string
  notes: string
  rulesJson: string
}

interface FactRow {
  id: string
  label: string
  kind: FactKind
  value: string
  valuesText: string
  unit: string
  tolerance: number
  keywordsText: string
  aliasesText: string
  requireName: boolean
  verification_status: FactVerificationStatus
  verified_by: string
  verified_at: string
  verification_note: string
  evidence_ref: string
}

const VERIFICATION_META: Record<
  FactVerificationStatus,
  { label: string; className: string }
> = {
  PENDING: { label: '待取证', className: 'bg-amber-50 text-amber-700' },
  VERIFIED: { label: '已取证', className: 'bg-emerald-50 text-emerald-700' },
  CONFLICT: { label: '口径冲突', className: 'bg-rose-50 text-rose-700' },
  NO_EVIDENCE: { label: '无证据', className: 'bg-rose-50 text-rose-700' },
  WAIVED: { label: '已豁免', className: 'bg-sky-50 text-sky-700' },
  STALE: { label: '数据过期', className: 'bg-slate-100 text-slate-600' },
}

const FACT_KINDS: Array<{ value: FactKind; label: string }> = [
  { value: 'scalar', label: '金额 / 数值' },
  { value: 'date', label: '日期' },
  { value: 'set', label: '列表 / 集合' },
  { value: 'topk', label: 'TopN 排名' },
  { value: 'boolean', label: '是否' },
  { value: 'text', label: '语义描述' },
]

const INPUT_TYPES: Array<{ value: InputType; label: string }> = [
  { value: 'CLEAR', label: '清晰明确' },
  { value: 'UNDERSPECIFIED', label: '信息不全' },
  { value: 'AMBIGUOUS_REF', label: '指代不清' },
  { value: 'MULTI_INTENT', label: '多意图' },
  { value: 'VAGUE', label: '过于模糊' },
  { value: 'UNANSWERABLE', label: '当前无法回答' },
  { value: 'CONTRADICTORY', label: '前后矛盾' },
  { value: 'UNAUTHORIZED', label: '越权请求' },
]

const CONTRACT_TABS = [
  { key: 'facts', label: '期望事实' },
  { key: 'mustnot', label: '禁止内容' },
  { key: 'contract', label: '非清晰输入' },
  { key: 'plan', label: '取数计划（诊断）' },
] as const

function emptyFactRow(kind: FactKind = 'scalar'): FactRow {
  return {
    id: '',
    label: '',
    kind,
    value: '',
    valuesText: '',
    unit: kind === 'scalar' ? 'CNY' : '',
    tolerance: kind === 'scalar' ? 0.01 : 0,
    keywordsText: '',
    aliasesText: '',
    requireName: false,
    verification_status: 'PENDING',
    verified_by: '',
    verified_at: '',
    verification_note: '',
    evidence_ref: '',
  }
}

const props = defineProps<{
  modelValue: boolean
  caseData: TestCase | null
  environments: Environment[]
  templates: PromptListItem[]
  models: ModelConfig[]
  saving: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'submit', payload: CasePayload): void
}>()

const toast = useToastStore()

const CASE_TYPES: CaseType[] = ['SINGLE', 'STATIC_MULTI', 'DYNAMIC_MULTI']
const simulatorVersions = ref<number[]>([])
const judgeVersions = ref<number[]>([])
const sessionExpectedText = ref('')

const form = reactive({
  code: '',
  name: '',
  description: '',
  case_type: 'SINGLE' as CaseType,
  status: 'ACTIVE',
  priority: 'P1',
  tagsText: '',
  environment_id: null as number | null,
  query: '',
  expected_answer: '',
  amounts: [] as AmountRule[],
  counts: [] as AmountRule[],
  datesText: '',
  mustContainText: '',
  mustNotContainText: '',
  turns: [] as TurnRule[],
  failFast: false,
  userGoal: '',
  initialQuery: '',
  persona: '',
  maxTurns: 5,
  hidden: [] as HiddenRule[],
  terminateKeywordsText: '',
  simulator_template_id: null as number | null,
  simulatorStrategy: 'LATEST' as TemplateStrategy,
  simulatorVersion: null as number | null,
  judge_template_id: null as number | null,
  judgeTemplateStrategy: 'LATEST' as TemplateStrategy,
  judgeVersion: null as number | null,
  simulator_model_id: null as number | null,
  judge_model_ids: [] as number[],
  judgeStrategy: 'AVG' as JudgeStrategy,
  pass_threshold: 60,
  gradingMode: 'LEGACY' as GradingMode,
  inputType: 'CLEAR' as InputType,
  asOf: '',
  extrasPolicy: 'lenient' as 'strict' | 'lenient',
  riskLevel: 'LOW' as 'LOW' | 'HIGH',
  owner: '',
  snapshotId: '',
  mustFacts: [emptyFactRow()] as FactRow[],
  mayFacts: [] as FactRow[],
  mustNotText: '',
  forbiddenPhrasesText: '',
  allowClarify: true,
  allowAssumption: true,
  allowPartial: false,
  allowRefuse: false,
  clarifySlotsText: 'time_range',
  assumptionText: '按当前账期',
  assumptionAmount: '',
  partialFactsText: '',
  planCapabilitiesText: '',
  maxToolCalls: null as number | null,
  requestVariables: [] as Array<{ key: string; value: string }>,
  requestVariablesJson: '',
  requestVariablesMode: 'rows' as 'rows' | 'json',
  sessionMode: 'INHERIT' as CaseSessionMode,
  change_summary: '',
})

const contractTab = ref<'facts' | 'mustnot' | 'contract' | 'plan'>('facts')
const originalEnvelope = ref<AnswerEnvelope | null>(null)
const originalPlan = ref<PlanSpec | null>(null)

const factSections = computed(() => [
  {
    key: 'must' as const,
    title: '必答事实（Must）——用业务语言填写，答案必须覆盖',
    rows: form.mustFacts,
  },
  {
    key: 'may' as const,
    title: '可接受扩展（May）——有据可查的中性扩展，答了不算错',
    rows: form.mayFacts,
  },
])

const isEdit = computed(() => Boolean(props.caseData))
const title = computed(() => (isEdit.value ? `编辑用例 · v${props.caseData?.current_version}` : '新建用例'))

const simulatorTemplates = computed(() =>
  props.templates.filter((item) => item.template_type === 'USER_SIMULATOR'),
)
const judgeTemplates = computed(() =>
  props.templates.filter((item) => item.template_type === 'LLM_JUDGE'),
)
const simulatorModels = computed(() => props.models.filter((item) => item.role === 'SIMULATOR'))
const judgeModels = computed(() => props.models.filter((item) => item.role === 'JUDGE'))

function splitList(text: string): string[] {
  return text
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function rulesFromCase(rules: RuleConfig | null | undefined): void {
  form.amounts = (rules?.amounts ?? []).map((item) => ({
    label: item.label,
    value: item.value,
    tolerance: item.tolerance ?? 0,
    aliasesText: (item.aliases ?? []).join(', '),
    requireName: item.require_name ?? false,
  }))
  form.counts = (rules?.counts ?? []).map((item) => ({
    label: item.label,
    value: item.value,
    tolerance: item.tolerance ?? 0,
    aliasesText: (item.aliases ?? []).join(', '),
    requireName: item.require_name ?? false,
  }))
  form.datesText = (rules?.dates ?? []).join(', ')
  form.mustContainText = (rules?.must_contain ?? []).join(', ')
  form.mustNotContainText = (rules?.must_not_contain ?? []).join(', ')
}

function buildRules(): RuleConfig | null {
  const amounts = form.amounts
    .filter((item) => item.label.trim() && item.value !== null && Number.isFinite(item.value))
    .map((item) => ({
      label: item.label.trim(),
      value: Number(item.value),
      tolerance: Number(item.tolerance) || 0,
      aliases: splitList(item.aliasesText),
      require_name: item.requireName,
    }))
  const counts = form.counts
    .filter((item) => item.label.trim() && item.value !== null && Number.isFinite(item.value))
    .map((item) => ({
      label: item.label.trim(),
      value: Number(item.value),
      tolerance: Number(item.tolerance) || 0,
      aliases: splitList(item.aliasesText),
      require_name: item.requireName,
    }))
  const dates = splitList(form.datesText)
  const must = splitList(form.mustContainText)
  const mustNot = splitList(form.mustNotContainText)
  if (!amounts.length && !counts.length && !dates.length && !must.length && !mustNot.length) {
    return null
  }
  return {
    amounts,
    counts,
    dates,
    must_contain: must,
    must_not_contain: mustNot,
  }
}

// ------------------------------------------------------- 答案契约构建
function factToRow(fact: FactAtom): FactRow {
  const kind = (fact.kind ?? 'scalar') as FactKind
  return {
    id: fact.id ?? '',
    label: fact.label ?? '',
    kind,
    value: fact.value === null || fact.value === undefined ? '' : String(fact.value),
    valuesText: (fact.values ?? []).join(', '),
    unit: fact.unit ?? '',
    tolerance: fact.tolerance ?? 0,
    keywordsText: (fact.keywords ?? []).join(', '),
    aliasesText: (fact.aliases ?? []).join(', '),
    requireName: fact.require_name ?? false,
    verification_status: (fact.verification_status ?? 'PENDING') as FactVerificationStatus,
    verified_by: fact.verified_by ?? '',
    verified_at: fact.verified_at ?? '',
    verification_note: fact.verification_note ?? '',
    evidence_ref: fact.evidence_ref ?? '',
  }
}

function factContentKey(fact: {
  label?: string | null
  kind?: string | null
  value?: unknown
  values?: unknown
  keywords?: unknown
  aliases?: unknown
  require_name?: unknown
  unit?: string | null
  tolerance?: number | null
}): string {
  return JSON.stringify([
    (fact.label ?? '').trim(),
    fact.kind ?? null,
    fact.value ?? null,
    fact.values ?? null,
    fact.keywords ?? null,
    fact.aliases ?? null,
    Boolean(fact.require_name),
    fact.unit ?? null,
    Number(fact.tolerance ?? 0),
  ])
}

function buildFacts(
  rows: FactRow[],
  prefix: string,
  originals: FactAtom[] = [],
): FactAtom[] | null {
  const facts: FactAtom[] = []
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index]
    const label = row.label.trim()
    const rawValue = row.value === null || row.value === undefined ? '' : String(row.value)
    const hasValue = rawValue.trim() || row.valuesText.trim()
    if (!label && !hasValue) continue
    if (!label) {
      toast.error(`第 ${index + 1} 条事实缺少业务名称`)
      return null
    }
    const id = row.id || `${prefix}-${index + 1}`
    // 保留界面未暴露的字段（time_scope / matcher / formula / aliases 等），
    // 避免每次保存都静默丢失取证以外的契约语义。
    const original = row.id ? originals.find((item) => item.id === row.id) : undefined
    const base: FactAtom = {
      ...(original ?? {}),
      id,
      label,
      kind: row.kind,
      unit: row.unit.trim() || null,
      tolerance: Number(row.tolerance) || 0,
      aliases: splitList(row.aliasesText),
      require_name: row.requireName,
      evidence_required: original?.evidence_required ?? true,
    }
    delete base.value
    delete base.values
    delete base.keywords

    let content: Partial<FactAtom>
    if (row.kind === 'scalar') {
      const value = Number(rawValue)
      if (!Number.isFinite(value)) {
        toast.error(`「${label}」需要填写数值`)
        return null
      }
      content = {
        value,
        unit: row.unit.trim() || null,
        tolerance: Number(row.tolerance) || 0,
      }
    } else if (row.kind === 'date') {
      if (!rawValue.trim()) {
        toast.error(`「${label}」需要填写日期`)
        return null
      }
      content = { value: rawValue.trim() }
    } else if (row.kind === 'set' || row.kind === 'topk') {
      const values = splitList(row.valuesText)
      if (!values.length) {
        toast.error(`「${label}」需要至少一个候选值`)
        return null
      }
      content = { values }
    } else if (row.kind === 'boolean') {
      content = {
        value: ['true', '是', '1', 'yes'].includes(rawValue.trim().toLowerCase()),
        keywords: splitList(row.keywordsText),
      }
    } else {
      content = {
        value: rawValue.trim() || null,
        keywords: splitList(row.keywordsText),
      }
    }

    // 事实内容（值 / 类型 / 单位 / 容差 / 语义线索）一旦变化，原取证结论即失效，
    // 自动退回待取证，避免"旧证据背书新事实"。未改动的事实保留原取证状态。
    const unchanged = original
      ? factContentKey({ ...base, ...content }) === factContentKey(original)
      : false
    const verification = unchanged
      ? {
          verification_status: row.verification_status,
          verified_by: row.verified_by || null,
          verified_at: row.verified_at || null,
          verification_note: row.verification_note || null,
          evidence_ref: row.evidence_ref || null,
        }
      : {
          verification_status: 'PENDING' as const,
          verified_by: null,
          verified_at: null,
          verification_note: null,
          evidence_ref: null,
        }

    facts.push({ ...base, ...content, ...verification })
  }
  return facts
}

function buildEnvelope(): AnswerEnvelope | null {
  const must = buildFacts(form.mustFacts, 'must', originalEnvelope.value?.must ?? [])
  if (must === null) return null
  const may = buildFacts(form.mayFacts, 'may', originalEnvelope.value?.may ?? [])
  if (may === null) return null
  if (!must.length) {
    toast.error('契约模式至少需要一条期望事实')
    return null
  }
  if (!form.asOf.trim()) {
    toast.error('契约模式必须填写冻结时间（账期/日期）')
    return null
  }
  const originalMustNot = originalEnvelope.value?.must_not ?? []
  const usedIds = new Set<string>()
  const mustNot = splitList(form.mustNotText).map((value, index) => {
    const original = originalMustNot.find(
      (fact) => String(fact.value ?? (fact.values ?? []).join(', ')) === value,
    )
    if (original?.id) {
      usedIds.add(original.id)
      return { ...original }
    }
    let id = `must-not-${index + 1}`
    while (usedIds.has(id)) id = `${id}x`
    usedIds.add(id)
    return { id, kind: 'text' as const, value }
  })
  return {
    ...(originalEnvelope.value ?? {}),
    must,
    may,
    must_not: mustNot,
    forbidden_phrases: splitList(form.forbiddenPhrasesText),
    extras_policy: form.extrasPolicy,
  }
}

function buildContract(mustFacts: FactAtom[]): ResponseContract | null {
  if (form.inputType === 'CLEAR') return null
  const allowed: ResponseContract['allowed'] = []
  if (form.allowClarify) allowed.push('clarify')
  if (form.allowAssumption) allowed.push('answer_with_assumption')
  if (form.allowPartial) allowed.push('partial')
  if (form.allowRefuse) allowed.push('refuse')
  if (allowed.length < 2) {
    toast.error('非清晰输入至少需要两种可接受行为')
    return null
  }
  const contract: ResponseContract = { allowed }
  if (form.allowClarify) {
    contract.clarify = {
      must_ask_slot: splitList(form.clarifySlotsText).length
        ? splitList(form.clarifySlotsText)
        : ['time_range'],
      must_not_assert: ['amount'],
    }
  }
  if (form.allowAssumption) {
    if (!form.assumptionAmount.trim()) {
      toast.error('请填写「带假设作答」场景下的期望值')
      return null
    }
    contract.answer_with_assumption = {
      must_state_assumption: true,
      scenarios: [
        {
          assumption: form.assumptionText.trim() || '按当前账期',
          must: { amount: form.assumptionAmount.trim() },
        },
      ],
    }
  }
  if (form.allowPartial) {
    const labels = splitList(form.partialFactsText)
    const required = labels.length
      ? mustFacts
          .filter((fact) => labels.includes(fact.label ?? ''))
          .map((fact) => String(fact.id))
      : mustFacts.slice(0, 1).map((fact) => String(fact.id))
    contract.partial = { must_disclose: true, required_facts: required }
  }
  if (form.allowRefuse) contract.refuse = { must_reason: true }
  return contract
}

function buildPlan(): PlanSpec | null {
  const capabilities = splitList(form.planCapabilitiesText)
  const original = originalPlan.value
  if (!capabilities.length && !form.maxToolCalls && !original?.alternatives) return null
  return {
    ...(original ?? {}),
    required_capabilities: capabilities,
    max_tool_calls: form.maxToolCalls,
    allow_parallel: original?.allow_parallel ?? true,
  }
}

function factRows(section: 'must' | 'may'): FactRow[] {
  return section === 'must' ? form.mustFacts : form.mayFacts
}

function addFact(section: 'must' | 'may'): void {
  factRows(section).push(emptyFactRow())
}

function removeFact(section: 'must' | 'may', index: number): void {
  const rows = factRows(section)
  rows.splice(index, 1)
  if (section === 'must' && !rows.length) rows.push(emptyFactRow())
}

// ---- 请求变量与会话绑定 ----
const RESERVED_REQUEST_VARIABLES = ['query', 'history', 'history_text', 'turn']
const RUNTIME_PLACEHOLDERS = ['query', 'session_id', 'history', 'history_text', 'turn']
const SESSION_FIELD_EXAMPLE = '"conversationId": "{{session_id}}"'

const CASE_SESSION_OPTIONS: Array<{
  value: CaseSessionMode
  label: string
  desc: string
}> = [
  { value: 'INHERIT', label: '跟随环境', desc: '使用执行环境配置的会话模式' },
  {
    value: 'SERVER_RETURNED',
    label: '服务端返回',
    desc: '首轮不带会话值，由目标接口返回后回填，后续轮次沿用',
  },
  {
    value: 'CLIENT_GENERATED',
    label: '客户端生成',
    desc: '执行开始时平台生成唯一会话值，全程各轮携带同一值',
  },
]

const selectedEnvironment = computed(
  () => props.environments.find((env) => env.id === form.environment_id) ?? null,
)

const envPlaceholders = computed(() => {
  const names: string[] = []
  const pattern = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g
  const scan = (value: unknown) => {
    pattern.lastIndex = 0
    let match: RegExpExecArray | null
    const text = JSON.stringify(value ?? {})
    while ((match = pattern.exec(text))) {
      if (!names.includes(match[1])) names.push(match[1])
    }
  }
  scan(selectedEnvironment.value?.headers_template)
  scan(selectedEnvironment.value?.body_template)
  return names
})

const requestVariableKeys = computed(() => {
  if (form.requestVariablesMode === 'json') {
    try {
      const parsed = JSON.parse(form.requestVariablesJson || '{}')
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? Object.keys(parsed as Record<string, unknown>)
        : []
    } catch {
      return []
    }
  }
  return form.requestVariables.map((row) => row.key.trim()).filter(Boolean)
})

const missingEnvPlaceholders = computed(() =>
  envPlaceholders.value.filter(
    (name) => !RUNTIME_PLACEHOLDERS.includes(name) && !requestVariableKeys.value.includes(name),
  ),
)

function parseRequestVariableValue(raw: string): unknown {
  const text = raw.trim()
  if (!text) return ''
  try {
    return JSON.parse(text)
  } catch {
    return raw
  }
}

function addRequestVariable(): void {
  form.requestVariables.push({ key: '', value: '' })
}

function removeRequestVariable(index: number): void {
  form.requestVariables.splice(index, 1)
}

function switchRequestVariablesMode(mode: 'rows' | 'json'): void {
  if (mode === form.requestVariablesMode) return
  if (mode === 'json') {
    const data: Record<string, unknown> = {}
    for (const row of form.requestVariables) {
      const key = row.key.trim()
      if (key) data[key] = parseRequestVariableValue(row.value)
    }
    form.requestVariablesJson = Object.keys(data).length ? JSON.stringify(data, null, 2) : ''
    form.requestVariablesMode = 'json'
    return
  }
  const text = form.requestVariablesJson.trim()
  let data: Record<string, unknown> = {}
  if (text) {
    try {
      const parsed = JSON.parse(text)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        data = parsed as Record<string, unknown>
      } else {
        toast.error('请求变量 JSON 需为对象（key-value 映射）')
        return
      }
    } catch {
      toast.error('请求变量 JSON 格式不正确，修正后再切换回行编辑')
      return
    }
  }
  form.requestVariables = Object.entries(data).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }))
  form.requestVariablesMode = 'rows'
}

function resetForm(): void {
  const source = props.caseData
  form.code = source?.code ?? ''
  form.name = source?.name ?? ''
  form.description = source?.description ?? ''
  form.case_type = (source?.case_type as CaseType) ?? 'SINGLE'
  form.status = source?.status ?? 'ACTIVE'
  form.priority = source?.priority ?? 'P1'
  form.tagsText = (source?.tags ?? []).join(', ')
  form.environment_id = source?.environment_id ?? props.environments[0]?.id ?? null
  form.query = source?.query ?? ''
  form.expected_answer = source?.expected_answer ?? ''
  rulesFromCase(source?.expected_rules)
  form.turns =
    source?.turns && source.turns.length
      ? source.turns.map((turn) => ({
          query: turn.query,
          expected_answer: turn.expected_answer ?? '',
          notes: turn.notes ?? '',
          rulesJson: turn.expected_rules ? JSON.stringify(turn.expected_rules, null, 2) : '',
        }))
      : [{ query: '', expected_answer: '', notes: '', rulesJson: '' }]
  sessionExpectedText.value = source?.session_expected
    ? JSON.stringify(source.session_expected, null, 2)
    : ''
  form.failFast = source?.fail_fast ?? false
  form.userGoal = source?.user_goal ?? ''
  form.initialQuery = source?.initial_query ?? ''
  form.persona = source?.persona_description ?? ''
  form.maxTurns = source?.max_turns ?? 5
  form.hidden = (source?.hidden_context ?? []).map((item) => ({
    key: item.key,
    content: item.content,
    release_hint: item.release_hint ?? '',
  }))
  if (form.hidden.length === 0) form.hidden = [{ key: '', content: '', release_hint: '' }]
  const keywords = (source?.terminate_conditions?.keywords ?? []) as unknown
  form.terminateKeywordsText = Array.isArray(keywords) ? keywords.map(String).join(', ') : ''
  form.simulator_template_id =
    source?.simulator_template_id ?? simulatorTemplates.value[0]?.id ?? null
  form.simulatorStrategy = (source?.simulator_template_strategy as TemplateStrategy) ?? 'LATEST'
  form.simulatorVersion = source?.simulator_template_version ?? null
  form.judge_template_id = source?.judge_template_id ?? judgeTemplates.value[0]?.id ?? null
  form.judgeTemplateStrategy = (source?.judge_template_strategy as TemplateStrategy) ?? 'LATEST'
  form.judgeVersion = source?.judge_template_version ?? null
  form.simulator_model_id = source?.simulator_model_id ?? simulatorModels.value[0]?.id ?? null
  form.judge_model_ids = source?.judge_model_ids ?? judgeModels.value.map((item) => item.id)
  form.judgeStrategy = (source?.judge_strategy as JudgeStrategy) ?? 'AVG'
  form.pass_threshold = source?.pass_threshold ?? 60
  form.riskLevel = (source?.risk_level as 'LOW' | 'HIGH') ?? 'LOW'
  form.owner = source?.owner ?? ''
  form.snapshotId = source?.snapshot_id ?? ''
  form.requestVariables = Object.entries(source?.request_variables ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }))
  form.requestVariablesJson = form.requestVariables.length
    ? JSON.stringify(source?.request_variables ?? {}, null, 2)
    : ''
  form.requestVariablesMode = 'rows'
  form.sessionMode = (source?.session_mode as CaseSessionMode) ?? 'INHERIT'
  resetContractForm(source)
  form.change_summary = ''
  void loadVersions('simulator')
  void loadVersions('judge')
}

function resetContractForm(source: TestCase | null): void {
  originalEnvelope.value = source?.answer_envelope ?? null
  originalPlan.value = source?.plan_spec ?? null
  form.gradingMode = (source?.grading_mode as GradingMode) ?? 'LEGACY'
  form.inputType = (source?.input_type as InputType) ?? 'CLEAR'
  form.asOf = source?.as_of ?? ''
  form.extrasPolicy = originalEnvelope.value?.extras_policy ?? 'lenient'
  form.mustFacts = (originalEnvelope.value?.must ?? []).map(factToRow)
  if (!form.mustFacts.length) form.mustFacts = [emptyFactRow()]
  form.mayFacts = (originalEnvelope.value?.may ?? []).map(factToRow)
  form.mustNotText = (originalEnvelope.value?.must_not ?? [])
    .map((fact) => String(fact.value ?? (fact.values ?? []).join(', ')))
    .filter(Boolean)
    .join(', ')
  form.forbiddenPhrasesText = (source?.answer_envelope?.forbidden_phrases ?? []).join(', ')
  form.planCapabilitiesText = (originalPlan.value?.required_capabilities ?? []).join(', ')
  form.maxToolCalls = originalPlan.value?.max_tool_calls ?? null

  const contract = source?.response_contract
  const allowed = contract?.allowed ?? ['clarify', 'answer_with_assumption']
  form.allowClarify = allowed.includes('clarify')
  form.allowAssumption = allowed.includes('answer_with_assumption')
  form.allowPartial = allowed.includes('partial')
  form.allowRefuse = allowed.includes('refuse')
  form.clarifySlotsText = (contract?.clarify?.must_ask_slot ?? ['time_range']).join(', ')
  const scenario = contract?.answer_with_assumption?.scenarios?.[0]
  form.assumptionText = scenario?.assumption ?? '按当前账期'
  form.assumptionAmount = String((scenario?.must as Record<string, unknown> | undefined)?.amount ?? '')
  const mustById = new Map(
    (source?.answer_envelope?.must ?? []).map((fact) => [String(fact.id), fact.label ?? '']),
  )
  form.partialFactsText = (contract?.partial?.required_facts ?? [])
    .map((id) => mustById.get(id) ?? id)
    .join(', ')
  contractTab.value = 'facts'
}

async function loadVersions(kind: 'simulator' | 'judge'): Promise<void> {
  const templateId = kind === 'simulator' ? form.simulator_template_id : form.judge_template_id
  const target = kind === 'simulator' ? simulatorVersions : judgeVersions
  if (!templateId) {
    target.value = []
    return
  }
  try {
    const detail = await api.prompts.get(templateId)
    target.value = detail.versions.map((item) => item.version).sort((a, b) => b - a)
    if (kind === 'simulator' && form.simulatorVersion === null) {
      form.simulatorVersion = target.value[0] ?? null
    }
    if (kind === 'judge' && form.judgeVersion === null) {
      form.judgeVersion = target.value[0] ?? null
    }
  } catch {
    target.value = []
  }
}

// AI 气泡状态放在 modelValue watcher 之前声明：watcher 是 immediate 的，
// 组件以打开态重建（如 HMR）时会立即调用 resetAiState，此时 ref 必须已初始化。
const aiOpen = ref(false)
const aiPane = ref<AiPane>('input')
const aiPrompt = ref('')
const aiLoading = ref(false)
const aiError = ref('')
const aiDrafts = ref<CaseAIDraft[]>([])
const aiDraftIndex = ref(0)
const aiResponseWarnings = ref<string[]>([])
const aiCount = ref(1)
const aiDraft = computed<CaseAIDraft | null>(() => aiDrafts.value[aiDraftIndex.value] ?? null)
const aiBaseCode = ref<string | null>(null)
const aiMeta = ref<{
  modelName: string | null
  elapsed: number
  calibers: number
  similar: number
} | null>(null)
const aiApplied = ref(false)
const aiAppliedCount = ref(0)
const aiAppliedAt = ref('')
const aiHighlight = ref<Set<string>>(new Set())
const aiSnapshot = ref<{ form: Record<string, unknown>; session: string } | null>(null)
const aiApplyMode = ref<'replace' | 'fillBlank'>('replace')
const aiSelected = ref<Set<string>>(new Set())
const aiMissingValues = ref<Record<string, string>>({})
const drawerBody = ref<HTMLElement | null>(null)

watch(
  () => [props.modelValue, props.caseData] as const,
  ([open]) => {
    if (open) {
      resetForm()
      resetAiState()
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
  },
  { immediate: true },
)

watch(
  () => form.simulator_template_id,
  () => {
    if (props.modelValue) void loadVersions('simulator')
  },
)
watch(
  () => form.judge_template_id,
  () => {
    if (props.modelValue) void loadVersions('judge')
  },
)

function close(): void {
  emit('update:modelValue', false)
}

function addAmount(): void {
  form.amounts.push({
    label: '金额',
    value: null,
    tolerance: 0.01,
    aliasesText: '',
    requireName: false,
  })
}

function addCount(): void {
  form.counts.push({
    label: '笔数',
    value: null,
    tolerance: 0,
    aliasesText: '',
    requireName: false,
  })
}

function addTurn(): void {
  form.turns.push({ query: '', expected_answer: '', notes: '', rulesJson: '' })
}

function addHidden(): void {
  form.hidden.push({ key: '', content: '', release_hint: '' })
}

function toggleJudge(modelId: number): void {
  const index = form.judge_model_ids.indexOf(modelId)
  if (index >= 0) form.judge_model_ids.splice(index, 1)
  else form.judge_model_ids.push(modelId)
}

function parseTurnRules(text: string): RuleConfig | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const parsed = JSON.parse(trimmed) as RuleConfig
  return {
    amounts: parsed.amounts ?? [],
    counts: parsed.counts ?? [],
    dates: parsed.dates ?? [],
    must_contain: parsed.must_contain ?? [],
    must_not_contain: parsed.must_not_contain ?? [],
  }
}

function buildRequestVariables(): Record<string, unknown> | null {
  if (form.requestVariablesMode === 'json') {
    const text = form.requestVariablesJson.trim()
    if (!text) return {}
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch (exc) {
      toast.error(`请求变量 JSON 格式不正确：${(exc as Error).message}`)
      return null
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      toast.error('请求变量 JSON 需为对象（key-value 映射）')
      return null
    }
    const invalid = Object.keys(parsed as Record<string, unknown>).find(
      (key) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(key),
    )
    if (invalid) {
      toast.error(`请求变量名「${invalid}」不合法（字母/数字/下划线，且不以数字开头）`)
      return null
    }
    const reserved = Object.keys(parsed as Record<string, unknown>).find((key) =>
      RESERVED_REQUEST_VARIABLES.includes(key),
    )
    if (reserved) {
      toast.error(`请求变量「${reserved}」由运行时自动注入，请勿自定义`)
      return null
    }
    return parsed as Record<string, unknown>
  }

  const result: Record<string, unknown> = {}
  for (const row of form.requestVariables) {
    const key = row.key.trim()
    if (!key) continue
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      toast.error(`请求变量名「${key}」不合法（字母/数字/下划线，且不以数字开头）`)
      return null
    }
    if (RESERVED_REQUEST_VARIABLES.includes(key)) {
      toast.error(`请求变量「${key}」由运行时自动注入，请勿自定义`)
      return null
    }
    if (key in result) {
      toast.error(`请求变量「${key}」重复`)
      return null
    }
    result[key] = parseRequestVariableValue(row.value)
  }
  return result
}

function buildPayload(): CasePayload | null {
  if (!form.code.trim()) {
    toast.error('请填写用例编号')
    return null
  }
  if (!form.name.trim()) {
    toast.error('请填写用例名称')
    return null
  }
  if (form.case_type === 'SINGLE' && !form.query.trim()) {
    toast.error('单轮用例必须填写用户 query')
    return null
  }
  if (form.case_type === 'STATIC_MULTI' && form.turns.every((turn) => !turn.query.trim())) {
    toast.error('静态多轮至少需要一轮 query')
    return null
  }
  if (form.case_type === 'DYNAMIC_MULTI' && !form.userGoal.trim()) {
    toast.error('动态多轮必须填写用户目标')
    return null
  }
  const requestVariables = buildRequestVariables()
  if (requestVariables === null) return null

  const payload: CasePayload = {
    code: form.code.trim(),
    name: form.name.trim(),
    description: form.description.trim() || null,
    case_type: form.case_type,
    status: form.status,
    priority: form.priority,
    tags: splitList(form.tagsText),
    environment_id: form.environment_id,
    request_variables: requestVariables,
    session_mode: form.sessionMode,
    max_turns: form.maxTurns,
    simulator_template_id: form.simulator_template_id,
    simulator_template_strategy: form.simulatorStrategy,
    simulator_template_version:
      form.simulatorStrategy === 'PINNED' ? form.simulatorVersion : null,
    judge_template_id: form.judge_template_id,
    judge_template_strategy: form.judgeTemplateStrategy,
    judge_template_version: form.judgeTemplateStrategy === 'PINNED' ? form.judgeVersion : null,
    simulator_model_id: form.simulator_model_id,
    judge_model_ids: [...form.judge_model_ids],
    judge_strategy: form.judgeStrategy,
    pass_threshold: form.pass_threshold,
    prompt_overrides: props.caseData?.prompt_overrides ?? null,
    change_summary: form.change_summary.trim() || null,
  }

  if (form.gradingMode === 'ANSWER_CONTRACT') {
    const envelope = buildEnvelope()
    if (!envelope) return null
    const contract = buildContract(envelope.must)
    if (form.inputType !== 'CLEAR' && !contract) return null
    payload.grading_mode = 'ANSWER_CONTRACT'
    payload.input_type = form.inputType
    payload.as_of = form.asOf.trim()
    payload.snapshot_id = form.snapshotId.trim() || null
    payload.risk_level = form.riskLevel
    payload.owner = form.owner.trim() || null
    payload.answer_envelope = envelope
    payload.response_contract = contract
    payload.plan_spec = buildPlan()
  } else {
    payload.grading_mode = 'LEGACY'
    payload.input_type = 'CLEAR'
    payload.as_of = form.asOf.trim() || null
    payload.answer_envelope = null
    payload.response_contract = null
    payload.plan_spec = null
  }

  if (form.case_type === 'SINGLE') {
    payload.query = form.query.trim()
    payload.expected_answer = form.expected_answer.trim() || null
    payload.expected_rules = buildRules()
    payload.turns = null
    payload.fail_fast = false
  } else if (form.case_type === 'STATIC_MULTI') {
    const turns: TurnItem[] = []
    for (let index = 0; index < form.turns.length; index += 1) {
      const turn = form.turns[index]
      if (!turn.query.trim()) continue
      try {
        turns.push({
          index: turns.length + 1,
          query: turn.query.trim(),
          expected_answer: turn.expected_answer.trim() || null,
          notes: turn.notes.trim() || null,
          expected_rules: parseTurnRules(turn.rulesJson),
        })
      } catch {
        toast.error(`第 ${index + 1} 轮的预期规则 JSON 格式不正确`)
        return null
      }
    }
    payload.turns = turns
    payload.fail_fast = form.failFast
    payload.query = null
    payload.expected_answer = null
    payload.expected_rules = null
    try {
      payload.session_expected = sessionExpectedText.value.trim()
        ? (JSON.parse(sessionExpectedText.value) as Record<string, unknown>)
        : null
    } catch {
      toast.error('会话级预期 JSON 格式不正确')
      return null
    }
  } else {
    const hidden: HiddenContextItem[] = form.hidden
      .filter((item) => item.key.trim() && item.content.trim())
      .map((item) => ({
        key: item.key.trim(),
        content: item.content.trim(),
        release_hint: item.release_hint.trim() || null,
      }))
    const keywords = splitList(form.terminateKeywordsText)
    payload.user_goal = form.userGoal.trim()
    payload.initial_query = form.initialQuery.trim() || null
    payload.persona_description = form.persona.trim() || null
    payload.hidden_context = hidden
    payload.terminate_conditions = keywords.length ? { keywords } : null
    payload.expected_answer = form.expected_answer.trim() || null
    payload.expected_rules = buildRules()
    payload.turns = null
    payload.query = null
  }

  return payload
}

function submit(): void {
  const payload = buildPayload()
  if (payload) emit('submit', payload)
}

// ------------------------------------------------------- AI 帮我填
type AiPane = 'input' | 'result'
type AiApplyMode = 'replace' | 'fillBlank'
type AiChangeStatus = 'add' | 'overwrite' | 'clear' | 'same' | 'append'

/** 组内条目（一轮对话 / 一条事实），支持逐条勾选。 */
interface AiChangeChild {
  key: string
  label: string
  current: string
  next: string
  status: AiChangeStatus
  fillable: boolean
  source: DraftSource
  /** 在草稿中的位置，用于回填。 */
  draftIndex?: number
  /** 在表单现有内容中的位置；为空表示这是草稿新增的条目。 */
  currentIndex?: number
  /** 追加到末尾的条目：整组一起追加，避免轮次顺序错位。 */
  tail?: boolean
}

interface AiChangeRow {
  key: string
  label: string
  current: string
  next: string
  status: AiChangeStatus
  fillable: boolean
  source: DraftSource
  children?: AiChangeChild[]
  /** 组内说明，提示部分勾选时的合并语义。 */
  hint?: string
}

const AI_SOURCE_LABEL: Record<DraftSource, string> = {
  USER: '用户描述',
  ASSET: '业务口径',
  CASE: '相似用例',
  LLM: '模型推断',
  NONE: '无依据',
}

const AI_STATUS_LABEL: Record<AiChangeStatus, string> = {
  add: '新增',
  overwrite: '覆盖',
  clear: '清空',
  same: '不变',
  append: '追加',
}

const AI_STATUS_CLASS: Record<AiChangeStatus, string> = {
  add: 'bg-emerald-50 text-emerald-700',
  overwrite: 'bg-amber-50 text-amber-700',
  clear: 'bg-rose-50 text-rose-700',
  same: 'bg-slate-100 text-slate-500',
  append: 'bg-teal-50 text-teal-700',
}

const AI_EXAMPLES = [
  '账单金额核对',
  '套餐余量与超套',
  '欠费停机状态',
  '多轮指代追问',
]

const AI_EDIT_EXAMPLES = [
  '把账期改成 2026-11',
  '补充欠费金额 120 元',
  '优先级改为 P0',
  '提问措辞更口语化',
]

const AI_EDIT_MULTI_EXAMPLES = [
  '把第二轮问得更口语化',
  '各轮账期改成 2026-11',
  '补充一轮费用明细追问',
  '第一轮加一句请给明细',
]

const AI_CREATE_MULTI_EXAMPLES = [
  '两轮：先查账单总额，再追问费用明细',
  '三轮：查账单 + 明细追问 + 超套追问',
  '多轮指代追问：这笔账单里超套了多少',
  '两轮账单核验，第二轮用「这笔账单」指代',
]

const aiExamples = computed(() => {
  if (!isEdit.value) {
    return form.case_type === 'STATIC_MULTI' ? AI_CREATE_MULTI_EXAMPLES : AI_EXAMPLES
  }
  return form.case_type === 'STATIC_MULTI' ? AI_EDIT_MULTI_EXAMPLES : AI_EDIT_EXAMPLES
})

// 新建支持单轮与静态多轮（多轮至少两轮才保留，否则后端回退单轮）；动态多轮由模拟器实时生成，暂不支持。
const aiAvailable = computed(() => form.case_type !== 'DYNAMIC_MULTI')

function selectDraft(index: number): void {
  if (index < 0 || index >= aiDrafts.value.length) return
  aiDraftIndex.value = index
  aiMissingValues.value = {}
  initAiSelection()
}

function factValueText(fact: DraftFact): string {
  if (fact.kind === 'set' || fact.kind === 'topk') {
    return (fact.values ?? []).join('、') || '—'
  }
  const raw = fact.value === null || fact.value === undefined ? '' : String(fact.value)
  if (!raw) return '—'
  return fact.unit ? `${raw} ${fact.unit}` : raw
}

function draftSource(draft: CaseAIDraft, key: string, fallback: DraftSource = 'LLM'): DraftSource {
  return draft.field_sources[key] ?? fallback
}

function formFactValue(row: FactRow): string {
  if (row.kind === 'set' || row.kind === 'topk') return row.valuesText.trim()
  const raw = String(row.value ?? '').trim()
  if (!raw) return ''
  return row.unit.trim() ? `${raw} ${row.unit.trim()}` : raw
}

function meaningfulFactRows(rows: FactRow[]): FactRow[] {
  return rows.filter(
    (row) =>
      Boolean(row.label.trim()) ||
      Boolean(String(row.value ?? '').trim()) ||
      Boolean(row.valuesText.trim()),
  )
}

function factRowsSummary(rows: FactRow[]): string {
  return meaningfulFactRows(rows)
    .map((row) => `${row.label.trim() || '未命名'}=${formFactValue(row) || '—'}`)
    .join('；')
}

function draftFactsSummary(facts: DraftFact[]): string {
  return facts.map((fact) => `${fact.label ?? '未命名'}=${factValueText(fact)}`).join('；')
}

function factRowSignature(row: FactRow): string {
  return JSON.stringify([
    row.label.trim().toLowerCase(),
    row.kind,
    String(row.value ?? '').trim(),
    row.valuesText.trim(),
    row.unit.trim(),
    Number(row.tolerance) || 0,
  ])
}

function factGroupRow(
  key: string,
  label: string,
  currentRows: FactRow[],
  draftFacts: DraftFact[],
  source: DraftSource,
): AiChangeRow {
  const currentList = meaningfulFactRows(currentRows)
  const currentKeys = currentList.map(factRowSignature)
  const nextKeys = draftFacts.map((fact) => factRowSignature(factToRow(fact)))
  const same =
    currentKeys.length === nextKeys.length &&
    currentKeys.every((item, index) => item === nextKeys[index])
  let status: AiChangeStatus
  if (!currentKeys.length) status = nextKeys.length ? 'add' : 'same'
  else if (!nextKeys.length) status = 'clear'
  else status = same ? 'same' : 'overwrite'
  const existing = new Set(currentKeys)

  // 逐条粒度：草稿事实逐条对应现有事实，草稿里没有的现有事实单列为「清空」。
  const matched = new Set<number>()
  const children: AiChangeChild[] = draftFacts.map((fact, index) => {
    const row = factToRow(fact)
    const signature = factRowSignature(row)
    let currentIndex = currentKeys.indexOf(signature)
    if (currentIndex < 0) {
      const lowerLabel = row.label.trim().toLowerCase()
      currentIndex = lowerLabel
        ? currentList.findIndex((item) => item.label.trim().toLowerCase() === lowerLabel)
        : -1
    }
    if (currentIndex >= 0) matched.add(currentIndex)
    const childStatus: AiChangeStatus = existing.has(signature)
      ? 'same'
      : currentIndex >= 0
        ? 'overwrite'
        : 'add'
    return {
      key: `${key}:${index}`,
      label: row.label.trim() || `第 ${index + 1} 条`,
      current: currentIndex >= 0 ? formFactValue(currentList[currentIndex]) : '',
      next: factValueText(fact),
      status: childStatus,
      fillable: childStatus === 'add',
      source,
      draftIndex: index,
      currentIndex: currentIndex >= 0 ? currentIndex : undefined,
    }
  })
  currentList.forEach((row, index) => {
    if (matched.has(index)) return
    children.push({
      key: `${key}:${draftFacts.length + index}`,
      label: row.label.trim() || `第 ${index + 1} 条`,
      current: formFactValue(row),
      next: '',
      status: 'clear',
      fillable: false,
      source,
      currentIndex: index,
    })
  })

  return {
    key,
    label,
    current: factRowsSummary(currentRows),
    next: draftFactsSummary(draftFacts),
    status,
    fillable: nextKeys.some((item) => !existing.has(item)),
    source,
    children: children.length ? children : undefined,
    hint: '逐条勾选时按条合并：未勾选的现有事实会保留。',
  }
}

function turnRowsSummary(rows: TurnRule[]): string {
  return rows
    .map((turn, index) => ({ turn, index }))
    .filter(({ turn }) => turn.query.trim())
    .map(({ turn, index }) => `第${index + 1}轮：${turn.query.trim()}`)
    .join('；')
}

function turnGroupRow(
  key: string,
  label: string,
  currentRows: TurnRule[],
  draftTurns: DraftTurn[],
  source: DraftSource,
): AiChangeRow {
  const currentTurns = currentRows.filter((turn) => turn.query.trim())
  const currentQueries = currentTurns.map((turn) => turn.query.trim())
  const nextQueries = draftTurns.map((turn) => turn.query.trim()).filter(Boolean)
  const same =
    currentQueries.length === nextQueries.length &&
    currentQueries.every((item, index) => item === nextQueries[index])
  let status: AiChangeStatus
  if (!currentQueries.length) status = nextQueries.length ? 'add' : 'same'
  else if (!nextQueries.length) status = 'clear'
  else status = same ? 'same' : 'overwrite'

  // 逐轮粒度：按轮次对齐现有脚本与草稿，新增的尾部轮次单独标为「追加」。
  const children: AiChangeChild[] = []
  const total = Math.max(currentTurns.length, draftTurns.length)
  for (let index = 0; index < total; index += 1) {
    const current = currentTurns[index]
    const draftTurn = draftTurns[index]
    const currentQuery = current?.query.trim() ?? ''
    const nextQuery = draftTurn?.query.trim() ?? ''
    const childStatus: AiChangeStatus = !currentQuery
      ? nextQuery
        ? 'add'
        : 'same'
      : !nextQuery
        ? 'clear'
        : currentQuery === nextQuery
          ? 'same'
          : 'overwrite'
    children.push({
      key: `${key}:${index}`,
      label: `第${index + 1}轮`,
      current: currentQuery,
      next: nextQuery,
      status: childStatus,
      fillable: !currentQuery && Boolean(nextQuery),
      source,
      draftIndex: draftTurn ? index : undefined,
      currentIndex: current ? index : undefined,
      tail: !current && Boolean(nextQuery),
    })
  }

  return {
    key,
    label,
    current: turnRowsSummary(currentRows),
    next: draftTurns.map((turn, index) => `第${index + 1}轮：${turn.query}`).join('；'),
    status,
    // 仅填空白只会追加草稿里多出来的尾部轮次；改写已有轮次不算可追加。
    fillable: nextQueries.length > currentQueries.length,
    source,
    children: children.length ? children : undefined,
    hint: '新增轮次会整组追加；只勾选部分时按轮合并，未勾选的轮次保持不变。',
  }
}

function textStatus(current: string, next: string): AiChangeStatus {
  const left = current.trim()
  const right = next.trim()
  if (!left && !right) return 'same'
  if (!left) return 'add'
  if (!right) return 'clear'
  return left === right ? 'same' : 'overwrite'
}

function textRow(
  key: string,
  label: string,
  current: string,
  next: string,
  source: DraftSource,
  fillableOverride?: boolean,
): AiChangeRow {
  return {
    key,
    label,
    current: current.trim(),
    next: next.trim(),
    status: textStatus(current, next),
    fillable: fillableOverride ?? (!current.trim() && Boolean(next.trim())),
    source,
  }
}

const aiChangeRows = computed<AiChangeRow[]>(() => {
  const draft = aiDraft.value
  if (!draft) return []
  const rows: AiChangeRow[] = []
  const mustNotText = draft.must_not
    .map((fact) => String(fact.value ?? (fact.values ?? []).join(', ')))
    .map((value) => value.trim())
    .filter(Boolean)
    .join(', ')

  if (!isEdit.value && draft.suggested_code) {
    rows.push(
      textRow(
        'code',
        '用例编号',
        form.code,
        form.code.trim() ? '' : draft.suggested_code,
        'LLM',
        !form.code.trim(),
      ),
    )
  }
  rows.push(textRow('name', '用例名称', form.name, draft.name ?? '', draftSource(draft, 'name')))
  rows.push(
    textRow(
      'description',
      '描述',
      form.description,
      draft.description ?? '',
      draftSource(draft, 'description'),
    ),
  )
  rows.push(
    textRow('priority', '优先级', form.priority, draft.priority ?? '', draftSource(draft, 'priority')),
  )
  rows.push(
    textRow('tagsText', '标签', form.tagsText, draft.tags.join(', '), draftSource(draft, 'tags')),
  )
  rows.push(
    textRow(
      'gradingMode',
      '判分方式',
      form.gradingMode === 'ANSWER_CONTRACT' ? '答案契约' : '4 维评分',
      draft.grading_mode
        ? draft.grading_mode === 'ANSWER_CONTRACT'
          ? '答案契约'
          : '4 维评分'
        : '',
      draftSource(draft, 'grading_mode', 'CASE'),
    ),
  )
  rows.push(textRow('asOf', '冻结账期', form.asOf, draft.as_of ?? '', draftSource(draft, 'as_of')))
  if (form.case_type === 'SINGLE') {
    rows.push(
      textRow('query', '用户提问', form.query, draft.query ?? '', draftSource(draft, 'query')),
    )
    rows.push(
      textRow(
        'expected_answer',
        '预期答案',
        form.expected_answer,
        draft.expected_answer ?? '',
        draftSource(draft, 'expected_answer'),
      ),
    )
  }
  // 事实只在答案契约模式下参与落库：当前与草稿都是 4 维评分时不展示，避免应用后被丢弃。
  const factEffective =
    form.gradingMode === 'ANSWER_CONTRACT' || draft.grading_mode === 'ANSWER_CONTRACT'
  if (factEffective) {
    rows.push(
      factGroupRow('mustFacts', '必答事实', form.mustFacts, draft.must, draftSource(draft, 'must')),
    )
    rows.push(
      factGroupRow('mayFacts', '可扩展事实', form.mayFacts, draft.may, draftSource(draft, 'may')),
    )
  }
  rows.push(
    textRow(
      'mustNotText',
      '禁止内容',
      form.mustNotText,
      mustNotText,
      draftSource(draft, 'must_not'),
    ),
  )
  rows.push(
    textRow(
      'forbiddenPhrasesText',
      '禁用短语',
      form.forbiddenPhrasesText,
      draft.forbidden_phrases.join(', '),
      draftSource(draft, 'forbidden_phrases'),
    ),
  )
  rows.push(
    textRow('riskLevel', '风险等级', form.riskLevel, draft.risk_level, draftSource(draft, 'risk_level')),
  )
  if (form.case_type === 'STATIC_MULTI') {
    rows.push(turnGroupRow('turns', '多轮脚本', form.turns, draft.turns, draftSource(draft, 'turns', 'LLM')))
  }
  return rows.filter((row) => row.current || row.next)
})

function childSelectable(child: AiChangeChild): boolean {
  if (child.status === 'same') return false
  if (aiApplyMode.value === 'fillBlank') return child.fillable
  return true
}

function selectableChildren(row: AiChangeRow): AiChangeChild[] {
  return (row.children ?? []).filter(childSelectable)
}

function rowSelectable(row: AiChangeRow): boolean {
  if (row.children?.length) return selectableChildren(row).length > 0
  if (row.status === 'same') return false
  return aiApplyMode.value === 'replace' || row.fillable
}

function rowChecked(row: AiChangeRow): boolean {
  if (!row.children?.length) return aiSelected.value.has(row.key)
  const selectable = selectableChildren(row)
  return selectable.length > 0 && selectable.every((child) => aiSelected.value.has(child.key))
}

function rowPartial(row: AiChangeRow): boolean {
  if (!row.children?.length) return false
  const selectable = selectableChildren(row)
  if (!selectable.length) return false
  const picked = selectable.filter((child) => aiSelected.value.has(child.key)).length
  return picked > 0 && picked < selectable.length
}

function initAiSelection(): void {
  const keys: string[] = []
  for (const row of aiChangeRows.value) {
    if (row.children?.length) {
      for (const child of selectableChildren(row)) keys.push(child.key)
    } else if (rowSelectable(row)) {
      keys.push(row.key)
    }
  }
  aiSelected.value = new Set(keys)
}

function setApplyMode(mode: AiApplyMode): void {
  if (aiApplyMode.value === mode) return
  aiApplyMode.value = mode
  initAiSelection()
}

function toggleKeys(keys: string[], select: boolean): void {
  const next = new Set(aiSelected.value)
  for (const key of keys) {
    if (select) next.add(key)
    else next.delete(key)
  }
  aiSelected.value = next
}

function toggleRow(row: AiChangeRow): void {
  if (!row.children?.length) {
    if (!rowSelectable(row)) return
    toggleKeys([row.key], !aiSelected.value.has(row.key))
    return
  }
  const selectable = selectableChildren(row)
  const allChecked = selectable.every((child) => aiSelected.value.has(child.key))
  toggleKeys(selectable.map((child) => child.key), !allChecked)
}

function toggleChild(row: AiChangeRow, child: AiChangeChild): void {
  if (!childSelectable(child)) return
  const select = !aiSelected.value.has(child.key)
  if (child.tail) {
    // 尾部新增轮次整组追加，避免只补中间一轮导致轮次顺序错位。
    const tail = (row.children ?? []).filter((item) => item.tail && childSelectable(item))
    toggleKeys(tail.map((item) => item.key), select)
    return
  }
  toggleKeys([child.key], select)
}

/** 逐条状态下，「仅填空白」里不能补的条目按「不变」展示。 */
function childStatus(child: AiChangeChild): AiChangeStatus {
  if (aiApplyMode.value !== 'fillBlank') return child.status
  if (!child.fillable) return 'same'
  return child.status === 'overwrite' ? 'append' : child.status
}

/** 「仅填空白」下，部分新增的整组写入实际是追加，避免标签仍显示「覆盖」。 */
function rowStatus(row: AiChangeRow): AiChangeStatus {
  if (aiApplyMode.value !== 'fillBlank') return row.status
  // 仅填空白不会覆盖/清空整组：不能补的整组按「不变」展示，能补的整组按「追加」。
  if (!row.fillable) return 'same'
  return row.status === 'overwrite' ? 'append' : row.status
}

const aiSelectedCount = computed(() =>
  aiChangeRows.value.reduce((total, row) => {
    if (row.children?.length) {
      return (
        total +
        row.children.filter(
          (child) => childSelectable(child) && aiSelected.value.has(child.key),
        ).length
      )
    }
    return total + (rowSelectable(row) && aiSelected.value.has(row.key) ? 1 : 0)
  }, 0),
)

function isMissingFilled(item: DraftMissing): boolean {
  return Boolean(aiMissingValues.value[item.field]?.trim())
}

const aiMissingFilledCount = computed(
  () => aiDraft.value?.missing.filter(isMissingFilled).length ?? 0,
)

const aiApplyCount = computed(() => aiSelectedCount.value + aiMissingFilledCount.value)

function missingPlaceholder(item: DraftMissing): string {
  if (item.field === 'as_of') return '如 2026-11，补全后可直接应用'
  if (item.field === 'turns') return '补充一轮用户提问，如：那明细呢？'
  if (item.field === 'must' || item.field.startsWith('must.')) return '补充期望值，如 268.50'
  if (item.field.startsWith('may.')) return '补充可扩展事实的期望值'
  return '补充该字段的实际值'
}

const aiFieldCount = computed(() => {
  const draft = aiDraft.value
  if (!draft) return 0
  let count = 0
  if (draft.name) count += 1
  if (draft.description) count += 1
  if (draft.priority) count += 1
  if (draft.tags.length) count += 1
  if (draft.grading_mode) count += 1
  if (draft.as_of) count += 1
  if (draft.query) count += 1
  if (draft.expected_answer) count += 1
  if (draft.turns.length) count += 1
  if (draft.must.length) count += 1
  if (draft.may.length) count += 1
  if (draft.must_not.length) count += 1
  if (draft.forbidden_phrases.length) count += 1
  if (draft.risk_level) count += 1
  if (draft.suggested_code && !form.code.trim()) count += 1
  return count
})

function openAi(): void {
  aiOpen.value = true
  if (!aiDraft.value) {
    aiPane.value = 'input'
    return
  }
  aiPane.value = 'result'
  initAiSelection()
}

function closeAi(): void {
  aiOpen.value = false
}

function resetAiState(): void {
  aiOpen.value = false
  aiPane.value = 'input'
  aiPrompt.value = ''
  aiError.value = ''
  aiLoading.value = false
  aiDrafts.value = []
  aiDraftIndex.value = 0
  aiResponseWarnings.value = []
  aiCount.value = 1
  aiBaseCode.value = null
  aiMeta.value = null
  aiApplied.value = false
  aiAppliedCount.value = 0
  aiAppliedAt.value = ''
  aiHighlight.value = new Set()
  aiSnapshot.value = null
  aiApplyMode.value = 'replace'
  aiSelected.value = new Set()
  aiMissingValues.value = {}
}

async function generateDraft(): Promise<void> {
  const text = aiPrompt.value.trim()
  if (!text) {
    aiError.value = '请先用一句话描述要生成的用例'
    return
  }
  aiLoading.value = true
  aiError.value = ''
  try {
    const response = await api.cases.aiDraft({
      request: text,
      mode: isEdit.value ? 'EDIT' : 'CREATE',
      count: aiCount.value,
      base_case_id: isEdit.value ? props.caseData?.id ?? null : null,
      environment_id: form.environment_id,
    })
    if (!response.drafts.length) throw new Error('AI 未返回任何草稿')
    aiDrafts.value = response.drafts
    aiDraftIndex.value = 0
    aiResponseWarnings.value = response.warnings
    aiBaseCode.value = response.base_case_code
    if (!isEdit.value) {
      // 新建时类型仍可切换：草稿给出多轮时就地切换，避免轮次没有落点。
      const draftType = response.drafts[0]?.case_type
      if (draftType === 'STATIC_MULTI' && form.case_type !== 'STATIC_MULTI') {
        form.case_type = 'STATIC_MULTI'
        aiResponseWarnings.value = [
          ...aiResponseWarnings.value,
          '草稿是静态多轮，已自动将用例类型切换为静态多轮',
        ]
      } else if (draftType === 'SINGLE' && form.case_type === 'STATIC_MULTI') {
        aiResponseWarnings.value = [
          ...aiResponseWarnings.value,
          '草稿是单轮形态，已保留当前多轮脚本；需要多轮请在描述里写明轮次',
        ]
      }
    }
    aiPane.value = 'result'
    aiApplyMode.value = 'replace'
    aiSelected.value = new Set()
    aiMissingValues.value = {}
    initAiSelection()
    aiMeta.value = {
      modelName: response.model_name,
      elapsed: response.elapsed_ms,
      calibers: response.context.calibers,
      similar: response.context.similar_cases,
    }
  } catch (error) {
    aiError.value = error instanceof Error ? error.message : '生成失败，请稍后重试'
  } finally {
    aiLoading.value = false
  }
}

function snapshotForm(): Record<string, unknown> {
  return JSON.parse(JSON.stringify(form)) as Record<string, unknown>
}

function applyFactsGroup(section: 'must' | 'may', picked?: AiChangeChild[]): void {
  const draft = aiDraft.value
  if (!draft) return
  const draftFacts = section === 'must' ? draft.must : draft.may
  const draftRows = draftFacts.map(factToRow)
  if (!picked) {
    if (aiApplyMode.value === 'replace') {
      if (section === 'must') form.mustFacts = draftRows.length ? draftRows : [emptyFactRow()]
      else form.mayFacts = draftRows
      return
    }
    const currentRows = meaningfulFactRows(section === 'must' ? form.mustFacts : form.mayFacts)
    const existing = new Set(currentRows.map(factRowSignature))
    const additions = draftRows.filter((row) => !existing.has(factRowSignature(row)))
    const merged = [...currentRows, ...additions]
    if (section === 'must') form.mustFacts = merged.length ? merged : [emptyFactRow()]
    else form.mayFacts = merged
    return
  }
  // 逐条应用：只写入勾选的条目，未勾选的现有事实原样保留。
  const replaceAt = new Map<number, number>()
  const clearAt = new Set<number>()
  const additions: number[] = []
  for (const child of picked) {
    if (child.draftIndex === undefined && child.currentIndex === undefined) continue
    if (child.status === 'add' && child.draftIndex !== undefined) {
      additions.push(child.draftIndex)
    } else if (
      child.status === 'overwrite' &&
      child.currentIndex !== undefined &&
      child.draftIndex !== undefined
    ) {
      replaceAt.set(child.currentIndex, child.draftIndex)
    } else if (child.status === 'clear' && child.currentIndex !== undefined) {
      clearAt.add(child.currentIndex)
    }
  }
  const merged: FactRow[] = []
  meaningfulFactRows(section === 'must' ? form.mustFacts : form.mayFacts).forEach((row, index) => {
    if (clearAt.has(index)) return
    const draftIndex = replaceAt.get(index)
    merged.push(draftIndex === undefined ? row : factToRow(draftFacts[draftIndex]))
  })
  for (const draftIndex of additions.sort((left, right) => left - right)) {
    merged.push(factToRow(draftFacts[draftIndex]))
  }
  if (section === 'must') form.mustFacts = merged.length ? merged : [emptyFactRow()]
  else form.mayFacts = merged
}

function applyTurnsGroup(picked?: AiChangeChild[]): void {
  const draft = aiDraft.value
  if (!draft) return
  if (!picked) {
    if (aiApplyMode.value === 'replace') {
      form.turns = draft.turns.length
        ? draft.turns.map((turn) => {
            const existing = form.turns.find(
              (item) => item.query.trim() && item.query.trim() === turn.query.trim(),
            )
            return {
              query: turn.query,
              expected_answer: turn.expected_answer ?? '',
              notes: turn.notes ?? '',
              rulesJson: existing?.rulesJson ?? '',
            }
          })
        : [{ query: '', expected_answer: '', notes: '', rulesJson: '' }]
      return
    }
    // 仅填空白：已有轮次一律保留，只追加草稿里多出来的尾部轮次，避免改写被重复插入。
    const existingCount = form.turns.filter((turn) => turn.query.trim()).length
    const additions = draft.turns
      .slice(existingCount)
      .filter((turn) => turn.query.trim())
      .map((turn) => ({
        query: turn.query,
        expected_answer: turn.expected_answer ?? '',
        notes: turn.notes ?? '',
        rulesJson: '',
      }))
    if (additions.length) form.turns = [...form.turns, ...additions]
    return
  }
  // 逐轮应用：只重写勾选的轮次，未勾选的轮次保持原样；新增轮次按草稿顺序追加。
  const result = form.turns
    .filter((turn) => turn.query.trim())
    .map((turn) => ({
      query: turn.query,
      expected_answer: turn.expected_answer,
      notes: turn.notes,
      rulesJson: turn.rulesJson,
    }))
  for (const child of picked) {
    if (child.draftIndex === undefined) continue
    const draftTurn = draft.turns[child.draftIndex]
    if (!draftTurn) continue
    if (child.currentIndex !== undefined) {
      const target = result[child.currentIndex]
      if (!target) continue
      target.query = draftTurn.query
      target.expected_answer = draftTurn.expected_answer ?? ''
      target.notes = draftTurn.notes ?? ''
    } else {
      result.push({
        query: draftTurn.query,
        expected_answer: draftTurn.expected_answer ?? '',
        notes: draftTurn.notes ?? '',
        rulesJson: '',
      })
    }
  }
  form.turns = result.length
    ? result
    : [{ query: '', expected_answer: '', notes: '', rulesJson: '' }]
}

function inferMissingKind(value: string): FactKind {
  const text = value.trim()
  if (/^20\d{2}[-/年.]\d{1,2}([-/月.]\d{1,2})?$/.test(text)) return 'date'
  const numeric = text.replace(/[,，\s元人民币CNYcny]/g, '')
  if (numeric && Number.isFinite(Number(numeric))) return 'scalar'
  return 'text'
}

function fillFactRowValue(row: FactRow, value: string): void {
  const text = value.trim()
  if (row.kind === 'scalar') {
    const match = text.replace(/,/g, '').match(/-?\d+(?:\.\d+)?/)
    row.value = match ? match[0] : text
  } else if (row.kind === 'set' || row.kind === 'topk') {
    row.valuesText = text
  } else if (row.kind === 'boolean') {
    row.value = ['是', 'true', '1', 'yes', 'y'].includes(text.toLowerCase())
      ? 'true'
      : 'false'
  } else {
    row.value = text
  }
}

function applyMissingValue(
  item: DraftMissing,
  value: string,
  highlighted: Set<string>,
): boolean {
  const text = value.trim()
  if (!text) return false
  if (item.field === 'as_of') {
    form.asOf = text
    highlighted.add('asOf')
    return true
  }
  if (item.field === 'turns') {
    form.turns.push({
      query: text,
      expected_answer: '',
      notes: '',
      rulesJson: '',
    })
    highlighted.add('turns')
    return true
  }
  if (item.field === 'must' || item.field.startsWith('must.') || item.field.startsWith('may.')) {
    const section: 'must' | 'may' = item.field.startsWith('may') ? 'may' : 'must'
    const rows = factRows(section)
    let row = rows.find((candidate) => candidate.label.trim() === item.label.trim())
    if (!row) {
      row = emptyFactRow(inferMissingKind(text))
      row.label = item.label
      rows.push(row)
    }
    fillFactRowValue(row, text)
    // 人工补值同样属于新内容，不能沿用旧的取证结论。
    row.verification_status = 'PENDING'
    row.verified_by = ''
    row.verified_at = ''
    row.verification_note = ''
    row.evidence_ref = ''
    highlighted.add(section === 'must' ? 'mustFacts' : 'mayFacts')
    return true
  }
  return false
}

function applyChangeRow(
  row: AiChangeRow,
  highlighted: Set<string>,
  picked?: AiChangeChild[],
): void {
  const draft = aiDraft.value
  if (!draft) return
  switch (row.key) {
    case 'name':
      form.name = draft.name ?? ''
      highlighted.add('name')
      break
    case 'description':
      form.description = draft.description ?? ''
      highlighted.add('description')
      break
    case 'priority':
      form.priority = draft.priority ?? 'P1'
      highlighted.add('priority')
      break
    case 'tagsText':
      form.tagsText = draft.tags.join(', ')
      highlighted.add('tagsText')
      break
    case 'gradingMode':
      form.gradingMode = draft.grading_mode ?? 'LEGACY'
      highlighted.add('gradingMode')
      break
    case 'asOf':
      form.asOf = draft.as_of ?? ''
      highlighted.add('asOf')
      break
    case 'query':
      form.query = draft.query ?? ''
      highlighted.add('query')
      break
    case 'expected_answer':
      form.expected_answer = draft.expected_answer ?? ''
      highlighted.add('expected_answer')
      break
    case 'mustFacts':
      applyFactsGroup('must', picked)
      highlighted.add('mustFacts')
      break
    case 'mayFacts':
      applyFactsGroup('may')
      highlighted.add('mayFacts')
      break
    case 'mustNotText':
      form.mustNotText = draft.must_not
        .map((fact) => String(fact.value ?? (fact.values ?? []).join(', ')))
        .map((value) => value.trim())
        .filter(Boolean)
        .join(', ')
      highlighted.add('mustNotText')
      break
    case 'forbiddenPhrasesText':
      form.forbiddenPhrasesText = draft.forbidden_phrases.join(', ')
      highlighted.add('forbiddenPhrasesText')
      break
    case 'riskLevel':
      form.riskLevel = draft.risk_level
      highlighted.add('riskLevel')
      break
    case 'turns':
      applyTurnsGroup(picked)
      highlighted.add('turns')
      break
    case 'code':
      if (!form.code.trim() && draft.suggested_code) {
        form.code = draft.suggested_code
        highlighted.add('code')
      }
      break
    default:
      break
  }
}

function applyDraft(): void {
  const draft = aiDraft.value
  if (!draft) return
  if (!aiApplyCount.value) {
    toast.info('没有勾选可应用的变更')
    return
  }
  aiSnapshot.value = { form: snapshotForm(), session: sessionExpectedText.value }
  const highlighted = new Set<string>()
  const selected = new Set(aiSelected.value)
  let count = 0

  for (const row of aiChangeRows.value) {
    if (row.children?.length) {
      const picked = row.children.filter(
        (child) => childSelectable(child) && selected.has(child.key),
      )
      if (!picked.length) continue
      applyChangeRow(row, highlighted, picked)
      count += picked.length
      continue
    }
    if (!rowSelectable(row) || !selected.has(row.key)) continue
    applyChangeRow(row, highlighted)
    count += 1
  }

  for (const item of draft.missing) {
    const value = aiMissingValues.value[item.field]?.trim()
    if (!value) continue
    if (applyMissingValue(item, value, highlighted)) count += 1
  }

  if (draft.input_type === 'CLEAR') {
    form.inputType = 'CLEAR'
  } else if (draft.input_type) {
    toast.info('草稿为非清晰输入，需人工配置响应契约后再切换输入形态')
  }

  if (draft.grading_mode === 'ANSWER_CONTRACT') contractTab.value = 'facts'
  aiHighlight.value = highlighted
  aiAppliedCount.value = count
  aiApplied.value = true
  aiAppliedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  aiOpen.value = false
  const remaining = draft.missing.filter((item) => !isMissingFilled(item)).length
  const hints: string[] = []
  if (
    (highlighted.has('mustFacts') || highlighted.has('mayFacts')) &&
    form.gradingMode !== 'ANSWER_CONTRACT'
  ) {
    hints.push('必答/可扩展事实仅在答案契约模式下落库，请同时勾选「判分方式」')
  }
  if (remaining) hints.push(`还有 ${remaining} 项缺依据需人工补充`)
  if (hints.length) {
    toast.info(`已应用 ${count} 项变更；${hints.join('；')}`)
  } else {
    toast.success(`已应用 AI 草稿 · ${count} 项变更，可继续手动调整`)
  }
  void nextTick(() => drawerBody.value?.scrollTo({ top: 0, behavior: 'smooth' }))
}

function undoApply(): void {
  const snapshot = aiSnapshot.value
  if (!snapshot) return
  Object.assign(form, snapshot.form)
  sessionExpectedText.value = snapshot.session
  aiApplied.value = false
  aiAppliedCount.value = 0
  aiAppliedAt.value = ''
  aiHighlight.value = new Set()
  aiSnapshot.value = null
  initAiSelection()
  toast.info('已撤销 AI 回填，表单恢复为应用前的内容')
}

function locateMissing(item: DraftMissing): void {
  const field = item.field
  const needsContract = field === 'as_of' || field === 'must' || field.startsWith('must.')
  if (needsContract && form.gradingMode !== 'ANSWER_CONTRACT') {
    form.gradingMode = 'ANSWER_CONTRACT'
    toast.info('已切换到答案契约模式以定位该字段')
  }
  if (needsContract) contractTab.value = 'facts'
  aiOpen.value = false
  void nextTick(() => {
    let targetId = ''
    if (field === 'as_of') targetId = 'ai-field-asOf'
    else if (field === 'turns') targetId = 'ai-field-turns'
    else if (field === 'must' || field.startsWith('must.') || field.startsWith('may.')) {
      const section: 'must' | 'may' = field.startsWith('may') ? 'may' : 'must'
      const index = factRows(section).findIndex(
        (row) => row.label.trim() === item.label.trim(),
      )
      targetId = index >= 0 ? `ai-field-${section}-${index}-value` : 'ai-field-facts'
    }
    if (!targetId) {
      toast.info('请在表单中手动补充该字段')
      return
    }
    const element = drawerBody.value?.querySelector<HTMLElement>(`#${targetId}`)
    if (!element) {
      toast.info('已展开对应区域，请手动定位')
      return
    }
    element.scrollIntoView({ block: 'center', behavior: 'smooth' })
    // 找不到具体行时定位到分组容器本身（tabindex=-1），不要误聚焦行内第一个标签输入框。
    window.setTimeout(() => {
      const target = element.matches('input, textarea, select, [tabindex]')
        ? element
        : element.querySelector<HTMLElement>('input, textarea, select, [tabindex]')
      target?.focus({ preventScroll: true })
    }, 280)
  })
}

function openDraftResult(): void {
  aiPane.value = 'result'
  aiOpen.value = true
  initAiSelection()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div
        v-if="modelValue"
        class="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm"
        @click.self="close"
      >
        <aside
          class="absolute right-0 top-0 flex h-full w-full max-w-3xl flex-col overflow-hidden rounded-l-tile border-l border-line bg-white shadow-tile-lift"
        >
          <header class="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
            <div class="min-w-0">
              <h2 class="text-base font-semibold text-slate-900">{{ title }}</h2>
              <p class="mt-1 text-xs text-slate-500">
                保存后自动生成版本快照，可用于 Diff 对比与一键回滚
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-2">
              <button
                v-if="aiAvailable"
                class="inline-flex h-[30px] items-center gap-1.5 rounded-[9px] border border-indigo-200 bg-indigo-50 px-2.5 text-[11.5px] font-semibold text-indigo-700 transition hover:border-indigo-300 hover:bg-indigo-100"
                :class="{ '!border-indigo-600 !bg-indigo-600 !text-white': aiOpen }"
                :title="isEdit ? '基于当前用例改写，AI 帮你填表单' : '用自然语言描述，AI 帮你填表单'"
                @click="aiOpen ? closeAi() : openAi()"
              >
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
                  <path stroke-linecap="round" stroke-linejoin="round" d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Z" />
                  <path stroke-linecap="round" d="M19 15.5 20 18l2.5 1-2.5 1-1 2.5-1-2.5L15.5 19l2.5-1 1-2.5Z" />
                </svg>
                AI 帮我填
              </button>
              <button
                class="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
                @click="close"
              >
                <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </div>
          </header>

          <!-- AI 帮我填：气泡式输入 / 回显 -->
          <div
            v-if="aiOpen && aiAvailable"
            class="absolute right-4 top-[76px] z-30 flex max-h-[calc(100vh-140px)] w-[452px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_24px_60px_-22px_rgba(15,23,42,0.38),0_2px_6px_rgba(15,23,42,0.06)]"
          >
            <div class="flex items-start gap-2.5 border-b border-slate-100 bg-gradient-to-b from-indigo-50/80 to-white px-3.5 py-3">
              <div class="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-indigo-400 to-indigo-600 text-white shadow-sm">
                <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                  <path stroke-linecap="round" stroke-linejoin="round" d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Z" />
                  <path stroke-linecap="round" d="M19 15.5 20 18l2.5 1-2.5 1-1 2.5-1-2.5L15.5 19l2.5-1 1-2.5Z" />
                </svg>
              </div>
              <div class="min-w-0">
                <h3 class="flex items-center gap-1.5 text-xs font-bold text-slate-900">
                  AI 帮我填
                  <span class="rounded-full bg-indigo-600 px-1.5 py-0.5 text-[8px] font-bold tracking-wide text-white">BETA</span>
                </h3>
                <p class="mt-0.5 text-[10px] leading-relaxed text-slate-500">
                  {{
                    aiPane === 'input'
                      ? isEdit
                        ? '描述要怎么改，生成后先在这里回显，不会直接写入表单'
                        : '描述你要测什么，生成后先在这里回显，不会直接写入表单'
                      : '检查回显内容，确认后再应用到表单'
                  }}
                </p>
                <p v-if="isEdit && aiBaseCode" class="mt-0.5 text-[9px] text-indigo-500">
                  改写基线：{{ aiBaseCode }} · 保存后生成新版本
                </p>
              </div>
              <button
                class="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                aria-label="关闭 AI 气泡"
                @click="closeAi"
              >
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </div>

            <div class="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
              <template v-if="aiPane === 'input'">
                <textarea
                  v-model="aiPrompt"
                  class="input min-h-[72px] resize-y text-xs leading-relaxed"
                  rows="3"
                  :placeholder="
                    isEdit
                      ? form.case_type === 'STATIC_MULTI'
                        ? '例如：把第二轮问得更口语化；各轮账期改成 2026-11'
                        : '例如：把 13800138000 在 2026-10 的账单金额改成 300 元，优先级改为 P0'
                      : '例如：生成一条答案契约用例，验证 13800138000 在 2026-10 的账单总额（268.50 元）和费用项数'
                  "
                  @keydown.ctrl.enter.prevent="generateDraft"
                />
                <div class="mt-2 flex flex-wrap gap-1.5">
                  <button
                    v-for="example in aiExamples"
                    :key="example"
                    class="rounded-full border border-slate-200 bg-white px-2 py-1 text-[9.5px] font-medium text-slate-600 transition hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700"
                    @click="aiPrompt = example"
                  >
                    {{ example }}
                  </button>
                </div>
                <div class="mt-2.5 flex items-center gap-2">
                  <span class="text-[9.5px] font-medium text-slate-500">生成方案</span>
                  <div class="flex items-center gap-1">
                    <button
                      v-for="n in 3"
                      :key="n"
                      class="h-6 min-w-6 rounded-md border px-1.5 text-[9.5px] font-semibold transition"
                      :class="
                        aiCount === n
                          ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                          : 'border-slate-200 text-slate-500 hover:bg-slate-50'
                      "
                      :title="`一次生成 ${n} 条候选`"
                      @click="aiCount = n"
                    >
                      {{ n }}
                    </button>
                  </div>
                  <span class="text-[9px] text-slate-400">条候选，生成后可切换对比</span>
                </div>
                <p class="mt-2 flex items-start gap-1.5 text-[9.5px] leading-relaxed text-slate-400">
                  <span class="mt-0.5 text-indigo-500">✦</span>
                  <span>
                    <template v-if="aiMeta">
                      会参考
                      <b class="text-slate-500">{{ aiMeta.calibers }}</b>
                      条业务口径与
                      <b class="text-slate-500">{{ aiMeta.similar }}</b>
                      条相似用例；
                    </template>
                    <template v-else>会参考业务口径与相似历史用例；</template>
                    没有权威依据的数值留空，不编造。
                  </span>
                </p>
                <p v-if="aiError" class="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-2 py-1.5 text-[10px] text-rose-700">
                  {{ aiError }}
                </p>
              </template>

              <template v-else-if="aiDraft">
                <div class="flex items-center gap-2">
                  <b class="text-[11px] text-slate-700">将回填 {{ aiFieldCount }} 个字段</b>
                  <span class="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[9px] font-semibold text-emerald-700">
                    结构校验通过
                  </span>
                  <span v-if="aiMeta" class="ml-auto text-[9px] text-slate-400">
                    {{ aiMeta.elapsed }}ms · {{ aiMeta.modelName }}
                  </span>
                </div>

                <div v-if="aiDrafts.length > 1" class="mt-2 flex flex-wrap items-center gap-1.5">
                  <span class="text-[9.5px] font-medium text-slate-500">切换方案</span>
                  <button
                    v-for="(item, index) in aiDrafts"
                    :key="index"
                    class="h-6 rounded-md border px-2 text-[9.5px] font-semibold transition"
                    :class="
                      index === aiDraftIndex
                        ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                        : 'border-slate-200 text-slate-500 hover:bg-slate-50'
                    "
                    :title="item.name ?? `方案 ${index + 1}`"
                    @click="selectDraft(index)"
                  >
                    {{ index + 1 }}
                  </button>
                  <span class="ml-auto text-[9px] text-slate-400">
                    方案 {{ aiDraftIndex + 1 }}/{{ aiDrafts.length }}
                  </span>
                </div>

                <div
                  v-if="aiError"
                  class="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-2 py-1.5 text-[10px] text-rose-700"
                >
                  {{ aiError }}
                </div>

                <div
                  v-for="warning in aiResponseWarnings"
                  :key="warning"
                  class="mt-2 flex items-start gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1.5 text-[9.5px] leading-relaxed text-amber-800"
                >
                  <span class="mt-px shrink-0">⚠</span>
                  <span>{{ warning }}</span>
                </div>

                <div
                  v-for="warning in aiDraft.warnings.slice(0, 3)"
                  :key="warning"
                  class="mt-2 flex items-start gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1.5 text-[9.5px] leading-relaxed text-amber-800"
                >
                  <span class="mt-px shrink-0">⚠</span>
                  <span>{{ warning }}</span>
                </div>

                <div class="mt-2 flex flex-wrap items-center gap-1.5">
                  <span class="text-[9.5px] font-medium text-slate-500">应用方式</span>
                  <button
                    class="h-6 rounded-md border px-2 text-[9.5px] font-semibold transition"
                    :class="
                      aiApplyMode === 'replace'
                        ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                        : 'border-slate-200 text-slate-500 hover:bg-slate-50'
                    "
                    title="以草稿为准整组写入，草稿留空的项一并清空"
                    @click="setApplyMode('replace')"
                  >
                    整组替换
                  </button>
                  <button
                    class="h-6 rounded-md border px-2 text-[9.5px] font-semibold transition"
                    :class="
                      aiApplyMode === 'fillBlank'
                        ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                        : 'border-slate-200 text-slate-500 hover:bg-slate-50'
                    "
                    title="只补表单里为空的项，保留已有内容"
                    @click="setApplyMode('fillBlank')"
                  >
                    仅填空白
                  </button>
                  <span class="ml-auto text-[9px] text-slate-400">
                    已选 {{ aiSelectedCount }} 项 · 待补 {{ aiDraft.missing.length }}
                  </span>
                </div>

                <div class="mt-2 max-h-[288px] overflow-y-auto rounded-xl border border-slate-200">
                  <div
                    v-for="row in aiChangeRows"
                    :key="row.key"
                    class="border-b border-slate-100 last:border-b-0"
                  >
                    <label
                      class="flex cursor-pointer items-start gap-2 px-2.5 py-2"
                      :class="rowSelectable(row) ? '' : 'opacity-60'"
                    >
                      <input
                        type="checkbox"
                        class="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border-slate-300 bg-white"
                        :checked="rowChecked(row)"
                        :indeterminate="rowPartial(row)"
                        :disabled="!rowSelectable(row)"
                        @change="toggleRow(row)"
                      />
                      <span class="w-[66px] shrink-0 pt-px text-[9.5px] font-semibold leading-relaxed text-slate-500">
                        {{ row.label }}
                      </span>
                      <span class="min-w-0 flex-1">
                        <span
                          v-if="row.current"
                          class="block break-words text-[9.5px] leading-relaxed text-slate-400"
                        >
                          <span class="line-through">{{ row.current }}</span>
                        </span>
                        <span
                          v-if="row.next"
                          class="block break-words text-[10.5px] leading-relaxed text-slate-900"
                        >
                          {{ row.next }}
                        </span>
                        <span v-else class="block text-[10.5px] leading-relaxed text-rose-600">
                          （清空）
                        </span>
                        <span class="mt-0.5 inline-block text-[8.5px] text-slate-400">
                          {{ AI_SOURCE_LABEL[row.source] }}
                        </span>
                      </span>
                      <span
                        class="mt-px shrink-0 whitespace-nowrap rounded px-1.5 py-0.5 text-[8.5px] font-semibold"
                        :class="AI_STATUS_CLASS[rowStatus(row)]"
                      >
                        {{ AI_STATUS_LABEL[rowStatus(row)] }}
                      </span>
                    </label>

                    <div v-if="row.children?.length" class="pb-1.5 pl-8 pr-2.5">
                      <div
                        v-for="child in row.children"
                        :key="child.key"
                        class="mt-1 rounded-lg border border-slate-100 bg-slate-50/70 px-2 py-1.5"
                      >
                        <label
                          class="flex cursor-pointer items-start gap-2"
                          :class="childSelectable(child) ? '' : 'opacity-60'"
                        >
                          <input
                            type="checkbox"
                            class="mt-0.5 h-3 w-3 shrink-0 rounded border-slate-300 bg-white"
                            :checked="aiSelected.has(child.key)"
                            :disabled="!childSelectable(child)"
                            @change="toggleChild(row, child)"
                          />
                          <span class="w-[44px] shrink-0 pt-px text-[9px] font-semibold leading-relaxed text-slate-500">
                            {{ child.label }}
                          </span>
                          <span class="min-w-0 flex-1">
                            <span
                              v-if="child.current"
                              class="block break-words text-[9px] leading-relaxed text-slate-400"
                            >
                              <span class="line-through">{{ child.current }}</span>
                            </span>
                            <span
                              v-if="child.next"
                              class="block break-words text-[9.5px] leading-relaxed text-slate-800"
                            >
                              {{ child.next }}
                            </span>
                            <span v-else class="block text-[9.5px] leading-relaxed text-rose-600">
                              （清空）
                            </span>
                          </span>
                          <span
                            class="mt-px shrink-0 whitespace-nowrap rounded px-1.5 py-0.5 text-[8px] font-semibold"
                            :class="AI_STATUS_CLASS[childStatus(child)]"
                          >
                            {{ AI_STATUS_LABEL[childStatus(child)] }}
                          </span>
                        </label>
                      </div>
                      <p v-if="row.hint" class="mt-1 text-[8.5px] leading-relaxed text-slate-400">
                        {{ row.hint }}
                      </p>
                    </div>
                  </div>
                  <p v-if="!aiChangeRows.length" class="px-2.5 py-3 text-[10px] text-slate-400">
                    没有可回填的字段
                  </p>
                </div>

                <div
                  v-if="aiDraft.missing.length"
                  class="mt-2 space-y-1.5 rounded-xl border border-amber-200 bg-amber-50 px-2.5 py-2"
                >
                  <p class="text-[10px] font-semibold text-amber-800">
                    待人工补充（{{ aiDraft.missing.length }}）
                  </p>
                  <div
                    v-for="item in aiDraft.missing"
                    :key="item.field"
                    class="rounded-lg border border-amber-200/70 bg-white/70 px-2 py-1.5"
                  >
                    <div class="flex items-start gap-2">
                      <div class="min-w-0 flex-1">
                        <b class="text-[10px] text-amber-900">{{ item.label }}</b>
                        <p class="text-[9px] leading-relaxed text-amber-800">{{ item.reason }}</p>
                      </div>
                      <span
                        v-if="isMissingFilled(item)"
                        class="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-[8.5px] font-semibold text-emerald-700"
                      >
                        已补
                      </span>
                      <button
                        class="shrink-0 text-[9.5px] font-semibold text-amber-800 underline underline-offset-2 transition hover:text-amber-950"
                        @click="locateMissing(item)"
                      >
                        定位
                      </button>
                    </div>
                    <input
                      v-model="aiMissingValues[item.field]"
                      class="input mt-1.5 !h-7 !py-1 text-[10px]"
                      :placeholder="missingPlaceholder(item)"
                    />
                  </div>
                </div>

                <p class="mt-2 text-[9px] leading-relaxed text-slate-400">
                  整组替换会按草稿写入并清空草稿留空项；仅填空白只补空值，保留已有内容。
                  编号不会被覆盖，应用后可用顶部「撤销」恢复。
                </p>
              </template>

              <template v-else>
                <p class="py-4 text-center text-[10px] text-slate-400">暂无草稿，请先描述要生成的用例。</p>
              </template>
            </div>

            <div class="flex shrink-0 items-center gap-2 border-t border-slate-100 bg-slate-50/70 px-3.5 py-2.5">
              <template v-if="aiPane === 'input'">
                <button
                  class="btn-primary h-8 !px-3 !text-xs"
                  :disabled="aiLoading || !aiPrompt.trim()"
                  @click="generateDraft"
                >
                  {{ aiLoading ? '生成中…' : '生成草稿' }}
                </button>
                <span class="ml-auto text-[9px] text-slate-400">
                  <kbd class="rounded border border-slate-300 bg-white px-1">Ctrl</kbd>
                  +
                  <kbd class="rounded border border-slate-300 bg-white px-1">Enter</kbd>
                </span>
              </template>
              <template v-else>
                <button
                  class="btn-primary h-8 !px-3 !text-xs"
                  :disabled="aiLoading || !aiApplyCount"
                  @click="applyDraft"
                >
                  应用 {{ aiApplyCount }} 项变更
                </button>
                <button class="btn-ghost h-8 !px-3 !text-xs" :disabled="aiLoading" @click="generateDraft">
                  {{ aiLoading ? '生成中…' : '重新生成' }}
                </button>
                <button class="btn-ghost h-8 !px-3 !text-xs" :disabled="aiLoading" @click="aiPane = 'input'">
                  改描述
                </button>
                <span class="ml-auto text-[9px] text-slate-400">
                  {{ isEdit ? '保存后生成新版本' : '应用后仍可修改' }}
                </span>
              </template>
            </div>
          </div>

          <div ref="drawerBody" class="flex-1 space-y-6 overflow-y-auto px-5 py-5">
            <div
              v-if="aiApplied"
              class="sticky top-0 z-10 flex items-center gap-2.5 rounded-xl border border-indigo-200 bg-gradient-to-r from-indigo-50 to-white px-3 py-2 shadow-sm"
            >
              <div class="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-indigo-100 text-indigo-700">
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="m5 13 4 4L19 7" />
                </svg>
              </div>
              <div class="min-w-0">
                <b class="block text-[11px] leading-tight text-indigo-900">
                  已应用 AI 草稿 · {{ aiAppliedCount }} 项变更
                  <span v-if="aiAppliedAt" class="font-medium text-indigo-500">· {{ aiAppliedAt }}</span>
                </b>
                <span class="text-[10px] text-slate-500">可直接修改；创建前请核对标蓝字段与待补充项</span>
              </div>
              <div class="ml-auto flex shrink-0 items-center gap-3">
                <button
                  class="text-[10.5px] font-semibold text-slate-600 underline underline-offset-2 transition hover:text-slate-900"
                  @click="undoApply"
                >
                  撤销
                </button>
                <button
                  v-if="aiDraft"
                  class="text-[10.5px] font-semibold text-indigo-700 underline underline-offset-2 transition hover:text-indigo-900"
                  @click="openDraftResult"
                >
                  查看草稿
                </button>
              </div>
            </div>
            <section class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">基础信息</h3>
              <div class="grid gap-3 sm:grid-cols-2">
                <div>
                  <label class="label">用例编号</label>
                  <input v-model="form.code" class="input font-mono" :class="{ 'ai-fill': aiHighlight.has('code') }" :disabled="isEdit" placeholder="CASE-BILL-001" />
                </div>
                <div>
                  <label class="label">用例名称</label>
                  <input v-model="form.name" class="input" :class="{ 'ai-fill': aiHighlight.has('name') }" placeholder="账单金额与明细一致性校验" />
                </div>
              </div>
              <div>
                <label class="label">描述</label>
                <textarea v-model="form.description" class="input" :class="{ 'ai-fill': aiHighlight.has('description') }" rows="2" placeholder="用例背景与关注点" />
              </div>
              <div class="grid gap-3 sm:grid-cols-3">
                <div>
                  <label class="label">状态</label>
                  <select v-model="form.status" class="input">
                    <button><selectedcontent></selectedcontent></button>
                    <option value="ACTIVE">启用</option>
                    <option value="DRAFT">草稿</option>
                    <option value="DISABLED">停用</option>
                  </select>
                </div>
                <div>
                  <label class="label">优先级</label>
                  <select v-model="form.priority" class="input" :class="{ 'ai-fill': aiHighlight.has('priority') }">
                    <button><selectedcontent></selectedcontent></button>
                    <option value="P0">P0</option>
                    <option value="P1">P1</option>
                    <option value="P2">P2</option>
                    <option value="P3">P3</option>
                  </select>
                </div>
                <div>
                  <label class="label">标签（逗号分隔）</label>
                  <input v-model="form.tagsText" class="input" :class="{ 'ai-fill': aiHighlight.has('tagsText') }" placeholder="账单核验, 金额" />
                </div>
              </div>
              <div>
                <label class="label">执行环境</label>
                <select v-model="form.environment_id" class="input">
                  <button><selectedcontent></selectedcontent></button>
                  <option :value="null">未绑定</option>
                  <option v-for="env in environments" :key="env.id" :value="env.id">
                    {{ env.name }}（{{ env.code }}）
                  </option>
                </select>
              </div>
            </section>

            <section class="space-y-3">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">请求变量</h3>
                <div class="flex items-center gap-1">
                  <button
                    v-if="form.requestVariablesMode === 'rows'"
                    class="btn-ghost btn-xs"
                    @click="addRequestVariable"
                  >
                    ＋ 添加变量
                  </button>
                  <button
                    class="btn-ghost btn-xs"
                    @click="
                      switchRequestVariablesMode(
                        form.requestVariablesMode === 'rows' ? 'json' : 'rows',
                      )
                    "
                  >
                    {{ form.requestVariablesMode === 'rows' ? 'JSON 模式' : '行编辑' }}
                  </button>
                </div>
              </div>
              <p class="text-[11px] text-slate-500">
                渲染进环境请求头 / 请求体模板；值支持字符串、数字与 JSON。优先级：运行时 &gt; 用例变量 &gt;
                环境变量池。
              </p>

              <div v-if="form.requestVariablesMode === 'rows'" class="space-y-2">
                <div
                  v-if="!form.requestVariables.length"
                  class="rounded-xl border border-dashed border-slate-300 px-3 py-3 text-center text-[11px] text-slate-500"
                >
                  暂无请求变量，点击「添加变量」按用例注入不同取值
                </div>
                <div
                  v-for="(row, index) in form.requestVariables"
                  :key="index"
                  class="grid gap-2 rounded-xl border border-slate-200 bg-slate-50/80 p-2 sm:grid-cols-[170px_minmax(0,1fr)_auto]"
                >
                  <input v-model="row.key" class="input font-mono text-xs" placeholder="key，如 user_id" />
                  <input
                    v-model="row.value"
                    class="input font-mono text-xs"
                    placeholder='value，如 13800138000 或 {"vip":true}'
                  />
                  <button class="btn-danger btn-xs" @click="removeRequestVariable(index)">删除</button>
                </div>
              </div>
              <textarea
                v-else
                v-model="form.requestVariablesJson"
                class="input font-mono text-xs"
                rows="6"
                placeholder='{"user_id": "13800138000", "channel": "app"}'
              />

              <div
                v-if="envPlaceholders.length"
                class="rounded-xl border border-slate-200 bg-white px-3 py-2 text-[11px] leading-relaxed text-slate-600"
              >
                <p>
                  当前环境模板占位符：
                  <code
                    v-for="name in envPlaceholders"
                    :key="name"
                    class="mr-1 rounded bg-slate-100 px-1 py-0.5 font-mono text-[10px]"
                    :class="
                      RUNTIME_PLACEHOLDERS.includes(name) ? 'text-slate-400' : 'text-indigo-600'
                    "
                  >
                    {{ name }}
                  </code>
                </p>
                <p class="mt-1 text-slate-500">
                  query / session_id / history / turn 由运行时自动注入，无需在此填写；需要固定会话值时添加
                  key 为 session_id 的变量即可（优先级最高）。
                </p>
                <p v-if="missingEnvPlaceholders.length" class="mt-1 text-amber-700">
                  尚未提供取值：{{ missingEnvPlaceholders.join('、') }}
                </p>
              </div>
            </section>

            <section class="space-y-3">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">
                  多轮会话绑定
                </h3>
                <span class="text-[11px] text-slate-500">
                  环境默认：{{
                    selectedEnvironment
                      ? selectedEnvironment.session_mode === 'CLIENT_GENERATED'
                        ? '客户端生成'
                        : '服务端返回'
                      : '未绑定环境'
                  }}
                </span>
              </div>
              <div class="grid gap-2 sm:grid-cols-3">
                <label
                  v-for="option in CASE_SESSION_OPTIONS"
                  :key="option.value"
                  class="flex cursor-pointer items-start gap-2 rounded-xl border p-2.5 transition"
                  :class="
                    form.sessionMode === option.value
                      ? 'border-indigo-300 bg-indigo-50/70'
                      : 'border-slate-200 bg-white hover:border-slate-300'
                  "
                >
                  <input
                    v-model="form.sessionMode"
                    type="radio"
                    :value="option.value"
                    class="mt-0.5 h-4 w-4 border-slate-300 text-indigo-600"
                  />
                  <span>
                    <span class="block text-xs font-medium text-slate-800">{{ option.label }}</span>
                    <span class="block text-[11px] leading-relaxed text-slate-500">
                      {{ option.desc }}
                    </span>
                  </span>
                </label>
              </div>
              <p class="text-[11px] text-slate-500">
                请求报文中的会话字段名由环境模板占位符决定，例如
                <code class="font-mono text-[10px] text-slate-700">{{ SESSION_FIELD_EXAMPLE }}</code>
                ，不同接口可各自定义字段名。
              </p>
            </section>

            <section class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">用例类型</h3>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="type in CASE_TYPES"
                  :key="type"
                  class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
                  :class="
                    form.case_type === type
                      ? 'border-indigo-500/60 bg-indigo-50 text-indigo-700'
                      : 'border-slate-300 text-slate-600 hover:bg-slate-100'
                  "
                  :disabled="isEdit"
                  @click="form.case_type = type"
                >
                  {{ caseTypeLabel(type) }}
                </button>
              </div>
              <p class="text-[11px] text-slate-500">
                用例类型创建后不可修改，如需切换请新建用例。
              </p>
            </section>

            <section v-if="form.case_type === 'SINGLE'" class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">单轮配置</h3>
              <div>
                <label class="label">用户提问（query）</label>
                <textarea v-model="form.query" class="input" :class="{ 'ai-fill': aiHighlight.has('query') }" rows="2" placeholder="帮我查一下订单 A2024001 的含税金额和明细笔数" />
              </div>
              <div>
                <label class="label">预期答案</label>
                <textarea v-model="form.expected_answer" class="input" :class="{ 'ai-fill': aiHighlight.has('expected_answer') }" rows="2" placeholder="订单 A2024001 含税金额为 ¥12,345.67，共 3 笔明细" />
              </div>
            </section>

            <section v-else-if="form.case_type === 'STATIC_MULTI'" id="ai-field-turns" tabindex="-1" class="space-y-3">
              <div class="flex items-center justify-between">
                <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">静态多轮脚本</h3>
                <button class="btn-ghost btn-xs" @click="addTurn">添加一轮</button>
              </div>
              <div
                v-for="(turn, index) in form.turns"
                :key="index"
                class="rounded-xl border border-slate-200 bg-slate-50/80 p-3"
              >
                <div class="flex items-center justify-between">
                  <span class="text-xs font-medium text-slate-600">第 {{ index + 1 }} 轮</span>
                  <button
                    v-if="form.turns.length > 1"
                    class="text-[11px] text-rose-700 hover:text-rose-700"
                    @click="form.turns.splice(index, 1)"
                  >
                    删除
                  </button>
                </div>
                <div class="mt-2 space-y-2">
                  <input v-model="turn.query" class="input" placeholder="本轮用户提问，例如：那含税总额是多少？" />
                  <input v-model="turn.expected_answer" class="input" placeholder="本轮预期答案（可选）" />
                  <input v-model="turn.notes" class="input" placeholder="备注（可选）" />
                  <textarea
                    v-if="form.gradingMode !== 'ANSWER_CONTRACT'"
                    v-model="turn.rulesJson"
                    class="input font-mono text-xs"
                    rows="2"
                    placeholder='本轮规则 JSON（可选）：{"amounts":[{"label":"金额","value":12345.67}]}'
                  />
                </div>
              </div>
              <div>
                <label class="label">会话级预期 JSON（可选）</label>
                <textarea
                  v-model="sessionExpectedText"
                  class="input font-mono text-xs"
                  rows="3"
                  placeholder='{"expected_answer":"...","expected_rules":{"amounts":[...]}}'
                />
              </div>
              <label class="flex items-center gap-2 text-sm text-slate-700">
                <input v-model="form.failFast" type="checkbox" class="h-4 w-4 rounded border-slate-300 bg-white" />
                任一规则失败即中止后续轮次（fail_fast）
              </label>
            </section>

            <section v-else class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">动态多轮配置</h3>
              <div>
                <label class="label">用户目标（user_goal）</label>
                <textarea v-model="form.userGoal" class="input" rows="2" placeholder="在不直接提供订单号的前提下，通过追问拿到订单 A2024001 的含税金额与明细笔数" />
              </div>
              <div>
                <label class="label">初始提问（initial_query）</label>
                <textarea v-model="form.initialQuery" class="input" rows="2" placeholder="我想核对一下最近订单的账单明细" />
              </div>
              <div>
                <label class="label">人设描述（persona_description）</label>
                <textarea v-model="form.persona" class="input" rows="2" placeholder="某科技公司财务，表达口语化，关注金额细节" />
              </div>
              <div class="grid gap-3 sm:grid-cols-2">
                <div>
                  <label class="label">最大轮次</label>
                  <input v-model.number="form.maxTurns" class="input" type="number" min="1" max="20" />
                </div>
                <div>
                  <label class="label">终止关键词（逗号分隔）</label>
                  <input v-model="form.terminateKeywordsText" class="input" placeholder="¥12,345.67, 3 笔" />
                </div>
              </div>
              <div class="flex items-center justify-between">
                <label class="label !mb-0">隐藏上下文（按提示逐轮释放）</label>
                <button class="btn-ghost btn-xs" @click="addHidden">添加条目</button>
              </div>
              <div
                v-for="(item, index) in form.hidden"
                :key="index"
                class="grid gap-2 rounded-xl border border-slate-200 bg-slate-50/80 p-3 sm:grid-cols-[120px_1fr_110px_auto]"
              >
                <input v-model="item.key" class="input" placeholder="键，如 order_no" />
                <input v-model="item.content" class="input" placeholder="内容，如 A2024001" />
                <input v-model="item.release_hint" class="input" placeholder="释放提示" />
                <button class="btn-danger btn-xs" @click="form.hidden.splice(index, 1)">删除</button>
              </div>
            </section>

            <section class="space-y-3">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">判分方式</h3>
                <div class="flex gap-2">
                  <button
                    class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
                    :class="
                      form.gradingMode === 'LEGACY'
                        ? 'border-indigo-500/60 bg-indigo-50 text-indigo-700'
                        : 'border-slate-300 text-slate-600 hover:bg-slate-100'
                    "
                    @click="form.gradingMode = 'LEGACY'"
                  >
                    4 维评分
                  </button>
                  <button
                    class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
                    :class="
                      form.gradingMode === 'ANSWER_CONTRACT'
                        ? 'border-indigo-500/60 bg-indigo-50 text-indigo-700'
                        : 'border-slate-300 text-slate-600 hover:bg-slate-100'
                    "
                    @click="form.gradingMode = 'ANSWER_CONTRACT'"
                  >
                    答案契约判分
                  </button>
                </div>
              </div>
              <p class="text-[11px] text-slate-500">
                契约模式只看最终答案是否正确；工具调用轨迹只用于失败诊断，不参与通过与否。
                录入时无需填写 Cypher / SQL，期望事实由后台能力模板自动取证。
              </p>
              <p v-if="form.gradingMode === 'ANSWER_CONTRACT'" class="text-[11px] text-slate-500">
                答案契约判分使用「事实包络 + 响应契约」判定，规则硬校验不参与最终结论，因此已隐藏。
              </p>

              <div
                v-if="form.gradingMode === 'ANSWER_CONTRACT'"
                class="space-y-3 rounded-xl border border-indigo-100 bg-indigo-50/40 p-3"
              >
                <div class="grid gap-3 sm:grid-cols-3">
                  <div>
                    <label class="label">输入形态</label>
                    <select v-model="form.inputType" class="input">
                      <button><selectedcontent></selectedcontent></button>
                      <option v-for="item in INPUT_TYPES" :key="item.value" :value="item.value">
                        {{ item.label }}
                      </option>
                    </select>
                  </div>
                  <div>
                    <label class="label">冻结时间（账期/日期）</label>
                    <input v-model="form.asOf" id="ai-field-asOf" class="input" :class="{ 'ai-fill': aiHighlight.has('asOf') }" placeholder="2026-10 / 2026-10-31" />
                  </div>
                  <div>
                    <label class="label">扩展策略</label>
                    <select v-model="form.extrasPolicy" class="input">
                      <button><selectedcontent></selectedcontent></button>
                      <option value="lenient">宽松（有据扩展中性）</option>
                      <option value="strict">严格（无据扩展即失败）</option>
                    </select>
                  </div>
                </div>

                <div class="grid gap-3 sm:grid-cols-3">
                  <div>
                    <label class="label">风险等级</label>
                    <select v-model="form.riskLevel" class="input" :class="{ 'ai-fill': aiHighlight.has('riskLevel') }">
                      <button><selectedcontent></selectedcontent></button>
                      <option value="LOW">低（LOW）</option>
                      <option value="HIGH">高（HIGH，默认进入待评审）</option>
                    </select>
                  </div>
                  <div>
                    <label class="label">用例负责人</label>
                    <input v-model="form.owner" class="input" placeholder="如 qa / 计费组" />
                  </div>
                  <div>
                    <label class="label">数据快照标识</label>
                    <input v-model="form.snapshotId" class="input" placeholder="如 MOCK-2026-10" />
                  </div>
                </div>

                <div class="flex flex-wrap gap-2">
                  <button
                    v-for="tab in CONTRACT_TABS"
                    :key="tab.key"
                    class="rounded-lg border px-2.5 py-1 text-[11px] font-medium transition"
                    :class="
                      contractTab === tab.key
                        ? 'border-indigo-500/60 bg-white text-indigo-700'
                        : 'border-transparent text-slate-500 hover:bg-white/70'
                    "
                    @click="contractTab = tab.key"
                  >
                    {{ tab.label }}
                  </button>
                </div>

                <div v-show="contractTab === 'facts'" id="ai-field-facts" tabindex="-1" class="space-y-5">
                  <div
                    v-for="section in factSections"
                    :key="section.key"
                    class="space-y-3"
                  >
                    <div class="flex items-center justify-between">
                      <span class="text-xs font-medium text-slate-600">
                        {{ section.title }}
                      </span>
                      <button class="btn-ghost btn-xs" @click="addFact(section.key)">
                        添加事实
                      </button>
                    </div>
                    <div
                      v-for="(fact, index) in section.rows"
                      :key="index"
                      :id="'ai-field-' + section.key + '-' + index"
                      class="space-y-2 rounded-xl border border-slate-200 bg-white p-3"
                    >
                    <div class="flex items-center justify-between">
                      <span class="text-xs font-medium text-slate-600">事实 {{ index + 1 }}</span>
                      <div class="flex items-center gap-2">
                        <span
                          class="rounded-full px-2 py-0.5 text-[10px] font-medium"
                          :class="
                            VERIFICATION_META[fact.verification_status ?? 'PENDING'].className
                          "
                        >
                          {{ VERIFICATION_META[fact.verification_status ?? 'PENDING'].label }}
                        </span>
                        <button
                          class="text-[11px] text-rose-700"
                          @click="removeFact(section.key, index)"
                        >
                          删除
                        </button>
                      </div>
                    </div>
                    <div class="grid gap-2 sm:grid-cols-2">
                      <input
                        v-model="fact.label"
                        class="input"
                        placeholder="业务名称，如 本期账单金额"
                      />
                      <select v-model="fact.kind" class="input">
                        <button><selectedcontent></selectedcontent></button>
                        <option v-for="item in FACT_KINDS" :key="item.value" :value="item.value">
                          {{ item.label }}
                        </option>
                      </select>
                    </div>
                    <div class="grid gap-2 sm:grid-cols-3">
                      <template v-if="fact.kind === 'scalar'">
                        <input
                          v-model="fact.value"
                          :id="'ai-field-' + section.key + '-' + index + '-value'"
                          class="input"
                          type="number"
                          step="0.01"
                          placeholder="期望数值"
                        />
                        <input v-model="fact.unit" class="input" placeholder="单位，如 CNY" />
                        <input
                          v-model.number="fact.tolerance"
                          class="input"
                          type="number"
                          step="0.01"
                          placeholder="容差"
                        />
                      </template>
                      <template v-else-if="fact.kind === 'date'">
                        <input
                          v-model="fact.value"
                          :id="'ai-field-' + section.key + '-' + index + '-value'"
                          class="input"
                          placeholder="2026-10-31"
                        />
                      </template>
                      <template v-else-if="fact.kind === 'set' || fact.kind === 'topk'">
                        <input
                          v-model="fact.valuesText"
                          :id="'ai-field-' + section.key + '-' + index + '-value'"
                          class="input sm:col-span-3"
                          placeholder="候选值，逗号分隔，如 套餐A, 套餐B"
                        />
                      </template>
                      <template v-else-if="fact.kind === 'boolean'">
                        <select
                          v-model="fact.value"
                          :id="'ai-field-' + section.key + '-' + index + '-value'"
                          class="input"
                        >
                          <button><selectedcontent></selectedcontent></button>
                          <option value="true">是</option>
                          <option value="false">否</option>
                        </select>
                        <input
                          v-model="fact.keywordsText"
                          class="input sm:col-span-2"
                          placeholder="命中关键词，逗号分隔"
                        />
                      </template>
                      <template v-else>
                        <input
                          v-model="fact.value"
                          :id="'ai-field-' + section.key + '-' + index + '-value'"
                          class="input"
                          placeholder="期望语义"
                        />
                        <input
                          v-model="fact.keywordsText"
                          class="input sm:col-span-2"
                          placeholder="语义线索，逗号分隔"
                        />
                      </template>
                    </div>
                    <div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                      <input
                        v-model="fact.aliasesText"
                        class="input"
                        placeholder="名称别名，逗号分隔，如 账单合计, total_amount"
                      />
                      <label
                        class="flex items-center gap-2 rounded-lg border border-slate-200 px-2.5 py-2 text-[11px] text-slate-600"
                      >
                        <input v-model="fact.requireName" type="checkbox" />
                        名称必须与数值绑定
                      </label>
                    </div>
                    <div class="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-600">
                      <span class="text-slate-500">取证状态</span>
                      <span
                        class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                        :class="VERIFICATION_META[fact.verification_status ?? 'PENDING'].className"
                      >
                        {{ VERIFICATION_META[fact.verification_status ?? 'PENDING'].label }}
                      </span>
                      <span v-if="fact.evidence_ref" class="truncate">
                        证据：{{ fact.evidence_ref }}
                      </span>
                      <span v-if="fact.verification_note" class="truncate">
                        备注：{{ fact.verification_note }}
                      </span>
                      <span class="text-slate-400">
                        由运营后台「事实验收」维护，修改事实值后会自动退回待取证。
                      </span>
                    </div>
                    </div>
                  </div>
                </div>

                <div v-show="contractTab === 'mustnot'" class="space-y-3">
                  <div>
                    <label class="label">禁止出现的事实（品牌/口径/错误结论）</label>
                    <input
                      v-model="form.mustNotText"
                      class="input"
                      :class="{ 'ai-fill': aiHighlight.has('mustNotText') }"
                      placeholder="例如：系统异常, 已全额退款"
                    />
                  </div>
                  <div>
                    <label class="label">禁用短语（命中即安全失败）</label>
                    <input
                      v-model="form.forbiddenPhrasesText"
                      class="input"
                      :class="{ 'ai-fill': aiHighlight.has('forbiddenPhrasesText') }"
                      placeholder="例如：保证收益, 百分之百准确"
                    />
                  </div>
                </div>

                <div v-show="contractTab === 'contract'" class="space-y-3">
                  <p class="text-[11px] text-slate-500">
                    输入不清晰时，「正确」指行为得体：允许澄清，或声明假设后作答，或部分披露，或拒绝。
                    至少选择两种可接受行为。
                  </p>
                  <div class="grid gap-2 sm:grid-cols-2">
                    <label class="flex items-center gap-2 text-sm text-slate-700">
                      <input v-model="form.allowClarify" type="checkbox" class="h-4 w-4" />
                      允许澄清（clarify）
                    </label>
                    <label class="flex items-center gap-2 text-sm text-slate-700">
                      <input v-model="form.allowAssumption" type="checkbox" class="h-4 w-4" />
                      允许带假设作答
                    </label>
                    <label class="flex items-center gap-2 text-sm text-slate-700">
                      <input v-model="form.allowPartial" type="checkbox" class="h-4 w-4" />
                      允许部分披露
                    </label>
                    <label class="flex items-center gap-2 text-sm text-slate-700">
                      <input v-model="form.allowRefuse" type="checkbox" class="h-4 w-4" />
                      允许拒绝并说明原因
                    </label>
                  </div>
                  <div v-if="form.allowClarify">
                    <label class="label">澄清必须问到的槽位</label>
                    <input
                      v-model="form.clarifySlotsText"
                      class="input"
                      placeholder="time_range, focus"
                    />
                  </div>
                  <div v-if="form.allowAssumption" class="grid gap-3 sm:grid-cols-2">
                    <div>
                      <label class="label">假设声明（答案必须说出来）</label>
                      <input v-model="form.assumptionText" class="input" placeholder="按当前账期" />
                    </div>
                    <div>
                      <label class="label">该假设下的期望值</label>
                      <input v-model="form.assumptionAmount" class="input" placeholder="123.45元" />
                    </div>
                  </div>
                  <div v-if="form.allowPartial">
                    <label class="label">部分披露必须给出的业务事实</label>
                    <input
                      v-model="form.partialFactsText"
                      class="input"
                      placeholder="本期账单金额（逗号分隔，留空默认第一条）"
                    />
                  </div>
                </div>

                <div v-show="contractTab === 'plan'" class="space-y-3">
                  <p class="text-[11px] text-slate-500">
                    仅用于失败诊断与能力覆盖分析，不参与正确性判定。录入人员无需了解底层实现。
                  </p>
                  <div class="grid gap-3 sm:grid-cols-2">
                    <div>
                      <label class="label">期望使用的能力（可选）</label>
                      <input
                        v-model="form.planCapabilitiesText"
                        class="input"
                        placeholder="bill_current, order_detail"
                      />
                    </div>
                    <div>
                      <label class="label">工具调用预算（可选）</label>
                      <input
                        v-model.number="form.maxToolCalls"
                        class="input"
                        type="number"
                        min="1"
                        placeholder="3"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <section v-if="form.case_type !== 'STATIC_MULTI' && form.gradingMode !== 'ANSWER_CONTRACT'" class="space-y-3">
              <div class="flex items-center justify-between">
                <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">规则硬校验</h3>
                <div class="flex gap-2">
                  <button class="btn-ghost btn-xs" @click="addAmount">添加金额</button>
                  <button class="btn-ghost btn-xs" @click="addCount">添加笔数</button>
                </div>
              </div>
              <div
                v-for="(rule, index) in form.amounts"
                :key="`amount-${index}`"
                class="space-y-2 rounded-lg border border-slate-200 p-2"
              >
                <div class="flex gap-2">
                  <input v-model="rule.label" class="input" placeholder="业务名称，如 账单总额" />
                  <input
                    v-model.number="rule.value"
                    class="input !w-32"
                    type="number"
                    step="0.01"
                    placeholder="期望金额"
                  />
                  <input
                    v-model.number="rule.tolerance"
                    class="input !w-24"
                    type="number"
                    step="0.01"
                    placeholder="容差"
                  />
                  <button class="btn-danger btn-xs shrink-0" @click="form.amounts.splice(index, 1)">
                    删除
                  </button>
                </div>
                <div class="flex gap-2">
                  <input
                    v-model="rule.aliasesText"
                    class="input"
                    placeholder="名称别名，逗号分隔，如 账单合计, total_amount"
                  />
                  <label class="flex shrink-0 items-center gap-1 text-[11px] text-slate-600">
                    <input v-model="rule.requireName" type="checkbox" />
                    必须绑定名称
                  </label>
                </div>
              </div>
              <div
                v-for="(rule, index) in form.counts"
                :key="`count-${index}`"
                class="space-y-2 rounded-lg border border-slate-200 p-2"
              >
                <div class="flex gap-2">
                  <input v-model="rule.label" class="input" placeholder="业务名称，如 费用项数" />
                  <input
                    v-model.number="rule.value"
                    class="input !w-32"
                    type="number"
                    placeholder="期望笔数"
                  />
                  <button class="btn-danger btn-xs shrink-0" @click="form.counts.splice(index, 1)">
                    删除
                  </button>
                </div>
                <div class="flex gap-2">
                  <input
                    v-model="rule.aliasesText"
                    class="input"
                    placeholder="名称别名，逗号分隔，如 费用项数, item_count"
                  />
                  <label class="flex shrink-0 items-center gap-1 text-[11px] text-slate-600">
                    <input v-model="rule.requireName" type="checkbox" />
                    必须绑定名称
                  </label>
                </div>
              </div>
              <div class="grid gap-3 sm:grid-cols-3">
                <div>
                  <label class="label">日期（逗号分隔）</label>
                  <input v-model="form.datesText" class="input" placeholder="2026-10-01" />
                </div>
                <div>
                  <label class="label">必须包含</label>
                  <input v-model="form.mustContainText" class="input" placeholder="A2024001" />
                </div>
                <div>
                  <label class="label">禁止包含</label>
                  <input v-model="form.mustNotContainText" class="input" placeholder="系统异常" />
                </div>
              </div>
            </section>

            <section class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">Prompt 与模型</h3>
              <div class="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <label class="label">用户模拟器模板（USER_SIMULATOR）</label>
                <div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_130px_130px]">
                  <select v-model="form.simulator_template_id" class="input">
                    <button><selectedcontent></selectedcontent></button>
                    <option :value="null">未指定</option>
                    <option v-for="item in simulatorTemplates" :key="item.id" :value="item.id">
                      {{ item.code }} · v{{ item.current_version }}
                    </option>
                  </select>
                  <select v-model="form.simulatorStrategy" class="input">
                    <button><selectedcontent></selectedcontent></button>
                    <option value="LATEST">跟随最新</option>
                    <option value="PINNED">锁定版本</option>
                  </select>
                  <select
                    v-if="form.simulatorStrategy === 'PINNED'"
                    v-model="form.simulatorVersion"
                    class="input"
                  >
                    <button><selectedcontent></selectedcontent></button>
                    <option :value="null">选择版本</option>
                    <option v-for="version in simulatorVersions" :key="version" :value="version">
                      v{{ version }}
                    </option>
                  </select>
                </div>
              </div>

              <div class="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <label class="label">LLM 裁判模板（LLM_JUDGE）</label>
                <div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_130px_130px]">
                  <select v-model="form.judge_template_id" class="input">
                    <button><selectedcontent></selectedcontent></button>
                    <option :value="null">未指定</option>
                    <option v-for="item in judgeTemplates" :key="item.id" :value="item.id">
                      {{ item.code }} · v{{ item.current_version }}
                    </option>
                  </select>
                  <select v-model="form.judgeTemplateStrategy" class="input">
                    <button><selectedcontent></selectedcontent></button>
                    <option value="LATEST">跟随最新</option>
                    <option value="PINNED">锁定版本</option>
                  </select>
                  <select v-if="form.judgeTemplateStrategy === 'PINNED'" v-model="form.judgeVersion" class="input">
                    <button><selectedcontent></selectedcontent></button>
                    <option :value="null">选择版本</option>
                    <option v-for="version in judgeVersions" :key="version" :value="version">
                      v{{ version }}
                    </option>
                  </select>
                </div>
              </div>

              <div class="grid gap-3 sm:grid-cols-2">
                <div>
                  <label class="label">模拟器模型</label>
                  <select v-model="form.simulator_model_id" class="input">
                    <button><selectedcontent></selectedcontent></button>
                    <option :value="null">未指定</option>
                    <option v-for="item in simulatorModels" :key="item.id" :value="item.id">
                      {{ item.name }}（{{ item.model_name }}）
                    </option>
                  </select>
                </div>
                <div>
                  <label class="label">裁判策略</label>
                  <select v-model="form.judgeStrategy" class="input">
                    <button><selectedcontent></selectedcontent></button>
                    <option value="AVG">平均分（AVG）</option>
                    <option value="VETO">一票否决（VETO）</option>
                  </select>
                </div>
              </div>

              <div>
                <label class="label">裁判模型（可多选）</label>
                <div class="flex flex-wrap gap-2">
                  <label
                    v-for="item in judgeModels"
                    :key="item.id"
                    class="flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition"
                    :class="
                      form.judge_model_ids.includes(item.id)
                        ? 'border-indigo-500/50 bg-indigo-50 text-indigo-700'
                        : 'border-slate-300 text-slate-600 hover:bg-slate-100'
                    "
                  >
                    <input
                      type="checkbox"
                      class="h-3.5 w-3.5 rounded border-slate-300 bg-white"
                      :checked="form.judge_model_ids.includes(item.id)"
                      @change="toggleJudge(item.id)"
                    />
                    {{ item.name }}
                  </label>
                  <span v-if="judgeModels.length === 0" class="text-xs text-slate-500">暂无裁判模型</span>
                </div>
              </div>

              <div class="w-40">
                <label class="label">通过分数线</label>
                <input v-model.number="form.pass_threshold" class="input" type="number" min="0" max="100" />
              </div>
            </section>

            <section v-if="isEdit" class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-600">变更说明</h3>
              <input v-model="form.change_summary" class="input" placeholder="例如：调整预期金额与裁判阈值" />
            </section>
          </div>

          <footer class="flex items-center justify-between gap-2 border-t border-slate-200 px-5 py-3">
            <p class="text-[11px] text-slate-500">
              {{ isEdit ? '保存会生成新版本快照' : '创建后版本号为 v1' }}
            </p>
            <div class="flex gap-2">
              <button class="btn-ghost" @click="close">取消</button>
              <button class="btn-primary" :disabled="saving" @click="submit">
                {{ saving ? '保存中…' : isEdit ? '保存新版本' : '创建用例' }}
              </button>
            </div>
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/*
 * 抽屉进出场：遮罩淡入 + 面板从右侧滑入（进场 300ms / 离场 240ms）。
 * 淡/滑分属两层，但根节点（遮罩）时长 >= 面板时长，
 * 否则 Vue 会提前移除 enter/leave-active，导致面板动画被截断。
 */
.drawer-enter-active {
  transition: opacity 0.3s ease-out;
}
.drawer-leave-active {
  transition: opacity 0.24s ease-in;
}
.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}

.drawer-enter-active aside,
.drawer-leave-active aside {
  will-change: transform, opacity;
}
.drawer-enter-active aside {
  transition:
    transform 0.3s cubic-bezier(0.22, 0.68, 0, 1),
    opacity 0.3s ease-out;
}
.drawer-leave-active aside {
  transition:
    transform 0.24s cubic-bezier(0.4, 0, 0.2, 1),
    opacity 0.24s ease-in;
}
.drawer-enter-from aside,
.drawer-leave-to aside {
  transform: translateX(100%);
  opacity: 0.6;
}

/* AI「定位」落点：分组容器用 tabindex=-1 接收焦点，给一圈可见但克制的轮廓 */
div[id^='ai-field-']:focus,
section[id^='ai-field-']:focus {
  outline: 2px solid rgb(99 102 241 / 0.45);
  outline-offset: 4px;
  border-radius: 0.75rem;
}

/* 减少动效偏好：把大幅位移换成一档小位移（16px）＋淡入，仍有"打开"的反馈 */
@media (prefers-reduced-motion: reduce) {
  .drawer-enter-from aside,
  .drawer-leave-to aside {
    transform: translateX(16px);
    opacity: 0;
  }
  .drawer-enter-active aside,
  .drawer-leave-active aside {
    transition:
      transform 0.2s ease-out,
      opacity 0.2s ease-out;
    will-change: auto;
  }
}
</style>
