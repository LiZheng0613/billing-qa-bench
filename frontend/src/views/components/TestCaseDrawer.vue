<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { api } from '../../api'
import type {
  AnswerEnvelope,
  CaseAIDraft,
  CasePayload,
  CaseType,
  DraftFact,
  DraftHiddenContext,
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
  NumericExpectation,
  PlanSpec,
  PromptListItem,
  ResponseContract,
  RuleConfig,
  TemplateStrategy,
  TestCase,
  TurnItem,
} from '../../types'
import { caseTypeLabel } from '../../utils/format'
import { useConfirm } from '../../composables/useConfirm'
import { useToastStore } from '../../stores/toast'
import SelectButton from '../../components/SelectButton.vue'

// AI 草稿只落在浏览器本地，刷新后可以接着改；不写后端、不自动落库。
const AI_DRAFT_STORAGE_KEY = 'billing-qa:ai-draft:v1'
const AI_DRAFT_TTL_MS = 24 * 60 * 60 * 1000

interface StoredAiDraft {
  savedAt: number
  scope: string
  prompt: string
  drafts: CaseAIDraft[]
  draftIndex: number
  warnings: string[]
  baseCode: string | null
  meta: {
    modelName: string | null
    elapsed: number
    calibers: number
    similar: number
  } | null
  count: number
  applyMode: 'replace' | 'fillBlank'
  selected: string[]
  missingValues: Record<string, string>
  pane: 'input' | 'result'
  appliedCount: number
}

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
  /** 复制模式的源用例：与新建一样走 POST /cases，但所有内容从源用例带入 */
  copyFrom?: TestCase | null
  environments: Environment[]
  templates: PromptListItem[]
  models: ModelConfig[]
  saving: boolean
  /** 工作台内嵌模式：去掉抽屉遮罩 / 固定定位，表单直接铺在父容器里 */
  embedded?: boolean
  /** 草稿试跑进行中：禁用重复发起 */
  running?: boolean
  /** 独立工作台：所有分区常驻展开，不再折叠 */
  noCollapse?: boolean
  /** 独立工作台：操作按钮由父级浮窗承接 */
  hideFooter?: boolean
  /** 独立工作台：表单不内部滚动，交给外层统一滚动 */
  noScroll?: boolean
  /** 独立工作台：用例状态只读，不允许在工作台修改 */
  hideStatus?: boolean
  /** 独立工作台：执行环境统一由底部运行栏控制，表单内不重复展示 */
  hideEnvironment?: boolean
  /** 受控执行环境：独立工作台浮窗与表单双向同步 */
  environmentId?: number | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'submit', payload: CasePayload): void
  (e: 'try-run', payload: CasePayload): void
  (e: 'update:environmentId', value: number | null): void
  (e: 'update:dirtyCount', value: number): void
}>()

const toast = useToastStore()
const confirm = useConfirm()

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
  judge_model_ids: [] as number[],
  judgeStrategy: 'AVG' as JudgeStrategy,
  pass_threshold: 60 as number | null,
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
  change_summary: '',
})

// 独立工作台：浮窗执行环境与表单内执行环境保持双向同步。
watch(
  () => props.environmentId,
  (value) => {
    if (value === undefined) return
    if ((value ?? null) !== form.environment_id) form.environment_id = value ?? null
  },
)
watch(
  () => form.environment_id,
  (value) => {
    if (props.environmentId === undefined) return
    if ((value ?? null) !== (props.environmentId ?? null)) {
      emit('update:environmentId', value ?? null)
    }
  },
)

const contractTab = ref<'facts' | 'mustnot' | 'contract' | 'plan'>('facts')
const originalEnvelope = ref<AnswerEnvelope | null>(null)
const originalPlan = ref<PlanSpec | null>(null)
const originalContract = ref<ResponseContract | null>(null)
const originalRequestVariables = ref<Record<string, unknown> | null>(null)

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
const isCopy = computed(() => !props.caseData && Boolean(props.copyFrom))
const copySource = computed<TestCase | null>(() =>
  props.caseData ?? (isCopy.value ? props.copyFrom ?? null : null),
)
const title = computed(() => {
  if (props.embedded) return '用例草稿'
  return isEdit.value
    ? `编辑用例 · v${props.caseData?.current_version}`
    : isCopy.value
      ? `复制用例 · 源 ${props.copyFrom?.code ?? ''}`
      : '新建用例'
})

const simulatorTemplates = computed(() =>
  props.templates.filter((item) => item.template_type === 'USER_SIMULATOR'),
)
const judgeTemplates = computed(() =>
  props.templates.filter((item) => item.template_type === 'LLM_JUDGE'),
)
const judgeModels = computed(() => props.models.filter((item) => item.role === 'JUDGE'))

// ------------------------------------------------------- 工作台内嵌折叠
const STATUS_LABELS: Record<string, string> = { ACTIVE: '启用', DRAFT: '草稿', DISABLED: '停用' }
const CASE_TYPE_LABELS: Record<string, string> = {
  SINGLE: '单轮问答',
  STATIC_MULTI: '静态多轮',
  DYNAMIC_MULTI: '动态多轮',
}

/** 仅内嵌（调试工作台）启用折叠；普通编辑抽屉保持原样全展开。 */
const collapsedSections = ref<Record<string, boolean>>({
  basic: true,
  variables: true,
  type: true,
  content: false,
  grading: true,
  rules: true,
  prompt: true,
  summary: true,
})

/** 仅普通抽屉 / 可折叠的工作台展示折叠交互；独立工作台所有分区常驻展开。 */
const collapsible = computed(() => Boolean(props.embedded && !props.noCollapse))

function isSectionCollapsed(key: string): boolean {
  return collapsible.value && Boolean(collapsedSections.value[key])
}

function toggleSection(key: string): void {
  if (!collapsible.value) return
  collapsedSections.value = { ...collapsedSections.value, [key]: !collapsedSections.value[key] }
}

const basicSummary = computed(() => {
  const environment = props.environments.find((item) => item.id === form.environment_id)
  return [STATUS_LABELS[form.status] ?? form.status, form.priority, form.tagsText, environment?.name]
    .filter(Boolean)
    .join(' · ')
})
const variablesSummary = computed(
  () =>
    `${form.requestVariables.length} 个变量 · ${form.requestVariablesMode === 'rows' ? '行编辑' : 'JSON'}`,
)
const contentSummary = computed(() => {
  const label = CASE_TYPE_LABELS[form.case_type] ?? form.case_type
  if (form.case_type === 'STATIC_MULTI') return `${label} · ${form.turns.length} 轮`
  if (form.case_type === 'DYNAMIC_MULTI') return `${label} · 最多 ${form.maxTurns} 轮`
  return `${label} · 提问 + 预期答案`
})
const gradingSummary = computed(
  () =>
    `${form.gradingMode === 'ANSWER_CONTRACT' ? '答案契约判分' : '4 维评分'} · 通过线 ${form.pass_threshold}`,
)
const rulesSummary = computed(
  () => `金额 ${form.amounts.length} · 笔数 ${form.counts.length} · 日期 ${form.datesText ? 1 : 0}`,
)
const promptSummary = computed(() => {
  const simulator = simulatorTemplates.value.find((item) => item.id === form.simulator_template_id)
  const judge = judgeTemplates.value.find((item) => item.id === form.judge_template_id)
  return [simulator?.name || '未指定模拟器', judge?.name || '未指定裁判', `通过线 ${form.pass_threshold}`].join(' · ')
})

/** 每个分组的「是否有未保存修改」签名：折叠时也能看到小圆点提示。 */
const initialSections = ref<Record<string, string>>({})

function sectionSignature(key: string): string {
  let state: Record<string, unknown>
  switch (key) {
    case 'basic':
      state = {
        name: form.name,
        description: form.description,
        status: form.status,
        priority: form.priority,
        tags: form.tagsText,
        environment_id: form.environment_id,
      }
      break
    case 'variables':
      state = {
        mode: form.requestVariablesMode,
        rows: form.requestVariables,
        json: form.requestVariablesJson,
      }
      break
    case 'content':
      state = {
        case_type: form.case_type,
        query: form.query,
        expected_answer: form.expected_answer,
        turns: form.turns,
        session: sessionExpectedText.value,
        fail_fast: form.failFast,
        user_goal: form.userGoal,
        initial_query: form.initialQuery,
        persona: form.persona,
        max_turns: form.maxTurns,
        hidden: form.hidden,
        terminate: form.terminateKeywordsText,
      }
      break
    case 'grading':
      state = {
        grading_mode: form.gradingMode,
        input_type: form.inputType,
        as_of: form.asOf,
        extras: form.extrasPolicy,
        risk: form.riskLevel,
        owner: form.owner,
        snapshot: form.snapshotId,
        must_facts: form.mustFacts,
        may_facts: form.mayFacts,
        must_not: form.mustNotText,
        forbidden: form.forbiddenPhrasesText,
        allow_clarify: form.allowClarify,
        allow_assumption: form.allowAssumption,
        allow_partial: form.allowPartial,
        allow_refuse: form.allowRefuse,
        clarify_slots: form.clarifySlotsText,
        assumption: form.assumptionText,
        assumption_amount: form.assumptionAmount,
        partial_facts: form.partialFactsText,
        plan_capabilities: form.planCapabilitiesText,
        max_tool_calls: form.maxToolCalls,
      }
      break
    case 'rules':
      state = {
        amounts: form.amounts,
        counts: form.counts,
        dates: form.datesText,
        must_contain: form.mustContainText,
        must_not_contain: form.mustNotContainText,
      }
      break
    case 'prompt':
      state = {
        simulator_id: form.simulator_template_id,
        simulator_strategy: form.simulatorStrategy,
        // LATEST 策略下具体版本号由系统决定，不作为"已修改"依据
        simulator_version: form.simulatorStrategy === 'PINNED' ? form.simulatorVersion : null,
        judge_id: form.judge_template_id,
        judge_strategy_mode: form.judgeTemplateStrategy,
        judge_version: form.judgeTemplateStrategy === 'PINNED' ? form.judgeVersion : null,
        judge_models: form.judge_model_ids,
        judge_strategy: form.judgeStrategy,
        threshold: form.pass_threshold,
      }
      break
    default:
      state = { change_summary: form.change_summary }
  }
  return JSON.stringify(state)
}

function captureInitialSections(): void {
  const keys = ['basic', 'variables', 'content', 'grading', 'rules', 'prompt', 'summary'] as const
  initialSections.value = Object.fromEntries(keys.map((key) => [key, sectionSignature(key)]))
}

function isSectionDirty(key: string): boolean {
  return Boolean(
    props.embedded && initialSections.value[key] && initialSections.value[key] !== sectionSignature(key),
  )
}

/** 独立工作台浮窗展示的未保存分区数量。 */
const DIRTY_SECTION_KEYS = ['basic', 'variables', 'content', 'grading', 'rules', 'prompt', 'summary'] as const
const dirtySectionCount = computed(
  () => DIRTY_SECTION_KEYS.filter((key) => isSectionDirty(key)).length,
)

watch(
  dirtySectionCount,
  (value) => emit('update:dirtyCount', value),
  { immediate: true },
)

function splitList(text: string): string[] {
  return text
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

/**
 * 逗号分隔文本与源列表完全一致时直接复用源数组：
 * 避免列表条目本身含逗号（如话术、must_not 多值）被拆成多项造成数据变形。
 */
function reuseListIfUnchanged(
  sourceList: string[] | null | undefined,
  text: string,
): string[] {
  const original = sourceList ?? []
  return original.join(', ') === text.trim() ? [...original] : splitList(text)
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
  const sourceAmounts = copySource.value?.expected_rules?.amounts ?? []
  const sourceCounts = copySource.value?.expected_rules?.counts ?? []
  const amounts = form.amounts
    .filter((item) => item.label.trim() && item.value !== null && Number.isFinite(item.value))
    .map((item) => ({
      label: item.label.trim(),
      value: Number(item.value),
      tolerance: Number(item.tolerance) || 0,
      aliases: reuseListIfUnchanged(
        sourceAmounts.find((source) => source.label === item.label.trim())?.aliases,
        item.aliasesText,
      ),
      require_name: item.requireName,
    }))
  const counts = form.counts
    .filter((item) => item.label.trim() && item.value !== null && Number.isFinite(item.value))
    .map((item) => ({
      label: item.label.trim(),
      value: Number(item.value),
      tolerance: Number(item.tolerance) || 0,
      aliases: reuseListIfUnchanged(
        sourceCounts.find((source) => source.label === item.label.trim())?.aliases,
        item.aliasesText,
      ),
      require_name: item.requireName,
    }))
  const dates = reuseListIfUnchanged(copySource.value?.expected_rules?.dates, form.datesText)
  const must = reuseListIfUnchanged(
    copySource.value?.expected_rules?.must_contain,
    form.mustContainText,
  )
  const mustNot = reuseListIfUnchanged(
    copySource.value?.expected_rules?.must_not_contain,
    form.mustNotContainText,
  )
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
    // 空数组与 undefined 视为同一语义，避免未改动的已取证事实被误判为「内容变化」。
    fact.values ?? [],
    fact.keywords ?? [],
    fact.aliases ?? [],
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
      aliases: reuseListIfUnchanged(original?.aliases, row.aliasesText),
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
        keywords: reuseListIfUnchanged(original?.keywords, row.keywordsText),
      }
    } else if (row.kind === 'date') {
      if (!rawValue.trim()) {
        toast.error(`「${label}」需要填写日期`)
        return null
      }
      content = {
        value: rawValue.trim(),
        keywords: reuseListIfUnchanged(original?.keywords, row.keywordsText),
      }
    } else if (row.kind === 'set' || row.kind === 'topk') {
      const values = reuseListIfUnchanged(original?.values, row.valuesText)
      if (!values.length) {
        toast.error(`「${label}」需要至少一个候选值`)
        return null
      }
      content = {
        values,
        keywords: reuseListIfUnchanged(original?.keywords, row.keywordsText),
      }
    } else if (row.kind === 'boolean') {
      content = {
        value: ['true', '是', '1', 'yes'].includes(rawValue.trim().toLowerCase()),
        keywords: reuseListIfUnchanged(original?.keywords, row.keywordsText),
      }
    } else {
      content = {
        value: rawValue.trim() || null,
        keywords: reuseListIfUnchanged(original?.keywords, row.keywordsText),
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
  const originalMustNotText = originalMustNot
    .map((fact) => String(fact.value ?? (fact.values ?? []).join(', ')))
    .join(', ')
  let mustNot: FactAtom[]
  if (originalMustNot.length && form.mustNotText.trim() === originalMustNotText) {
    // 文本未变时整体复用源事实，保留 id / values / 取证等界面未暴露字段。
    mustNot = originalMustNot.map((fact) => ({ ...fact }))
  } else {
    const usedIds = new Set<string>()
    mustNot = splitList(form.mustNotText).map((value, index) => {
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
  }
  return {
    ...(originalEnvelope.value ?? {}),
    must,
    may,
    must_not: mustNot,
    forbidden_phrases: reuseListIfUnchanged(
      originalEnvelope.value?.forbidden_phrases,
      form.forbiddenPhrasesText,
    ),
    extras_policy: form.extrasPolicy,
  }
}

function buildContract(mustFacts: FactAtom[]): ResponseContract | null {
  if (form.inputType === 'CLEAR') return null
  const original = originalContract.value
  const allowed: ResponseContract['allowed'] = []
  if (form.allowClarify) allowed.push('clarify')
  if (form.allowAssumption) allowed.push('answer_with_assumption')
  if (form.allowPartial) allowed.push('partial')
  if (form.allowRefuse) allowed.push('refuse')
  if (allowed.length < 2) {
    toast.error('非清晰输入至少需要两种可接受行为')
    return null
  }
  // 以源契约为底再覆盖表单字段，保留界面未暴露的子配置（多场景、额外断言等）。
  const contract: ResponseContract = { ...(original ?? {}), allowed }
  if (form.allowClarify) {
    const clarifySlots = reuseListIfUnchanged(
      original?.clarify?.must_ask_slot,
      form.clarifySlotsText,
    )
    contract.clarify = {
      ...(original?.clarify ?? {}),
      must_ask_slot: clarifySlots.length ? clarifySlots : ['time_range'],
      must_not_assert: original?.clarify?.must_not_assert ?? ['amount'],
    }
  } else {
    delete contract.clarify
  }
  if (form.allowAssumption) {
    if (!form.assumptionAmount.trim()) {
      toast.error('请填写「带假设作答」场景下的期望值')
      return null
    }
    const scenarios = [...(original?.answer_with_assumption?.scenarios ?? [])]
    const first = scenarios[0] ?? {}
    const merged = {
      ...first,
      assumption: form.assumptionText.trim() || '按当前账期',
      must: { ...(first.must ?? {}), amount: form.assumptionAmount.trim() },
    }
    if (scenarios.length) scenarios[0] = merged
    else scenarios.push(merged)
    contract.answer_with_assumption = {
      ...(original?.answer_with_assumption ?? {}),
      must_state_assumption: original?.answer_with_assumption?.must_state_assumption ?? true,
      scenarios,
    }
  } else {
    delete contract.answer_with_assumption
  }
  if (form.allowPartial) {
    const labels = splitList(form.partialFactsText)
    const required = labels.length
      ? mustFacts
          .filter((fact) => labels.includes(fact.label ?? ''))
          .map((fact) => String(fact.id))
      : mustFacts.slice(0, 1).map((fact) => String(fact.id))
    contract.partial = {
      ...(original?.partial ?? {}),
      must_disclose: original?.partial?.must_disclose ?? true,
      required_facts: required,
    }
  } else {
    delete contract.partial
  }
  if (form.allowRefuse) {
    contract.refuse = {
      ...(original?.refuse ?? {}),
      must_reason: original?.refuse?.must_reason ?? true,
    }
  } else {
    delete contract.refuse
  }
  return contract
}

function buildPlan(): PlanSpec | null {
  const capabilities = reuseListIfUnchanged(
    originalPlan.value?.required_capabilities,
    form.planCapabilitiesText,
  )
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

async function removeFact(section: 'must' | 'may', index: number): Promise<void> {
  const rows = factRows(section)
  const label = rows[index]?.label?.trim()
  const ok = await confirm({
    title: '删除事实',
    message: label ? `确定删除事实「${label}」吗？` : '确定删除这条事实吗？',
    detail: '删除后该事实不再参与契约判分。',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  rows.splice(index, 1)
  if (section === 'must' && !rows.length) rows.push(emptyFactRow())
}

// ---- 请求变量与会话提示 ----
const RESERVED_REQUEST_VARIABLES = ['query', 'history', 'history_text', 'turn', 'timestamp_iso']
const RUNTIME_PLACEHOLDERS = ['query', 'session_id', 'history', 'history_text', 'turn', 'timestamp_iso']

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
      const parsed = parseJsonPreservingNumbers(form.requestVariablesJson || '{}')
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

const JSON_NUMBER_LITERAL = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/

/** 请求变量里的数字统一存成字符串；大整数用定点格式，避免变科学计数法。 */
function numberToPlainText(value: number): string {
  if (!Number.isFinite(value)) return String(value)
  if (Number.isInteger(value)) {
    return value.toLocaleString('en-US', { useGrouping: false, maximumFractionDigits: 0 })
  }
  return String(value)
}

function normalizeRequestVariableValue(value: unknown): unknown {
  if (typeof value === 'number') return numberToPlainText(value)
  if (Array.isArray(value)) return value.map(normalizeRequestVariableValue)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        normalizeRequestVariableValue(item),
      ]),
    )
  }
  return value
}

function normalizeRequestVariables(
  values: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  if (!values) return {}
  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => [key, normalizeRequestVariableValue(value)]),
  )
}

/**
 * 把 JSON 中的数字字面量包成字符串后再解析，避免长数字（手机号、订单号等）
 * 被转成 number 后丢失精度，或序列化成科学计数法。只扫描字符串外的字符，
 * 不会影响 key 与文本内容。
 */
function quoteJsonNumbers(text: string): string {
  let out = ''
  let inString = false
  let escaped = false
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]
    if (inString) {
      out += char
      if (escaped) escaped = false
      else if (char === '\\') escaped = true
      else if (char === '"') inString = false
      continue
    }
    if (char === '"') {
      inString = true
      out += char
      continue
    }
    if (char === '-' || (char >= '0' && char <= '9')) {
      let end = index
      if (text[end] === '-') end += 1
      while (end < text.length && /[0-9.eE+-]/.test(text[end])) end += 1
      out += `"${text.slice(index, end)}"`
      index = end - 1
      continue
    }
    out += char
  }
  return out
}

function parseJsonPreservingNumbers(text: string): unknown {
  return JSON.parse(quoteJsonNumbers(text))
}

function parseRequestVariableValue(raw: string): unknown {
  const text = raw.trim()
  if (!text) return ''
  // 纯数字一律按原文保存为字符串，保证长数字不被 number 化
  if (JSON_NUMBER_LITERAL.test(text)) return text
  try {
    return parseJsonPreservingNumbers(text)
  } catch {
    return raw
  }
}

function addRequestVariable(): void {
  form.requestVariables.push({ key: '', value: '' })
}

async function removeRequestVariable(index: number): Promise<void> {
  const key = form.requestVariables[index]?.key?.trim()
  const ok = await confirm({
    title: '删除请求变量',
    message: key ? `确定删除请求变量 ${key} 吗？` : '确定删除这条请求变量吗？',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  form.requestVariables.splice(index, 1)
}

async function removeHiddenRow(index: number): Promise<void> {
  const key = form.hidden[index]?.key?.trim()
  const ok = await confirm({
    title: '删除隐藏信息',
    message: key ? `确定删除隐藏信息 ${key} 吗？` : '确定删除这条隐藏信息吗？',
    detail: '删除后模拟器将不再释放该条信息。',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  form.hidden.splice(index, 1)
}

async function removeTurn(index: number): Promise<void> {
  const query = form.turns[index]?.query?.trim()
  const ok = await confirm({
    title: '删除对话轮次',
    message: query
      ? `确定删除第 ${index + 1} 轮对话「${query.slice(0, 24)}」吗？`
      : `确定删除第 ${index + 1} 轮对话吗？`,
    detail: '该轮的提问与预期答案将一并移除。',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  form.turns.splice(index, 1)
}

async function removeAmountRule(index: number): Promise<void> {
  const label = form.amounts[index]?.label?.trim()
  const ok = await confirm({
    title: '删除金额校验',
    message: label ? `确定删除金额校验「${label}」吗？` : '确定删除这条金额校验吗？',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  form.amounts.splice(index, 1)
}

async function removeCountRule(index: number): Promise<void> {
  const label = form.counts[index]?.label?.trim()
  const ok = await confirm({
    title: '删除次数校验',
    message: label ? `确定删除次数校验「${label}」吗？` : '确定删除这条次数校验吗？',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  form.counts.splice(index, 1)
}

function switchRequestVariablesMode(mode: 'rows' | 'json'): void {
  if (mode === form.requestVariablesMode) return
  if (mode === 'json') {
    const data: Record<string, unknown> = {}
    for (const row of form.requestVariables) {
      const key = row.key.trim()
      if (key) data[key] = parseRequestVariableValue(row.value)
    }
    const normalized = normalizeRequestVariables(data)
    form.requestVariablesJson = Object.keys(normalized).length
      ? JSON.stringify(normalized, null, 2)
      : ''
    form.requestVariablesMode = 'json'
    return
  }
  const text = form.requestVariablesJson.trim()
  let data: Record<string, unknown> = {}
  if (text) {
    try {
      const parsed = parseJsonPreservingNumbers(text)
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
  form.requestVariables = Object.entries(normalizeRequestVariables(data)).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }))
  form.requestVariablesMode = 'rows'
}

function suggestCopyCode(code: string): string {
  const suffix = '-COPY'
  const base = (code || 'CASE').trim() || 'CASE'
  return base.length + suffix.length > 64 ? `${base.slice(0, 64 - suffix.length)}${suffix}` : `${base}${suffix}`
}

function suggestCopyName(name: string): string {
  const suffix = '（副本）'
  const base = (name || '').trim() || '未命名用例'
  return base.length + suffix.length > 200 ? `${base.slice(0, 200 - suffix.length)}${suffix}` : `${base}${suffix}`
}

function resetForm(): void {
  const source = copySource.value
  form.code = source?.code ?? ''
  form.name = source?.name ?? ''
  form.description = source?.description ?? ''
  form.case_type = (source?.case_type as CaseType) ?? 'SINGLE'
  form.status = source?.status ?? 'ACTIVE'
  form.priority = source?.priority ?? 'P1'
  form.tagsText = (source?.tags ?? []).join(', ')
  form.environment_id =
    props.environmentId !== undefined
      ? props.environmentId ?? null
      : source
        ? source.environment_id ?? null
        : props.environments[0]?.id ?? null
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
  form.simulator_template_id = source
    ? source.simulator_template_id ?? null
    : simulatorTemplates.value[0]?.id ?? null
  form.simulatorStrategy = (source?.simulator_template_strategy as TemplateStrategy) ?? 'LATEST'
  form.simulatorVersion = source?.simulator_template_version ?? null
  form.judge_template_id = source
    ? source.judge_template_id ?? null
    : judgeTemplates.value[0]?.id ?? null
  form.judgeTemplateStrategy = (source?.judge_template_strategy as TemplateStrategy) ?? 'LATEST'
  form.judgeVersion = source?.judge_template_version ?? null
  form.judge_model_ids = source
    ? [...(source.judge_model_ids ?? [])]
    : judgeModels.value.map((item) => item.id)
  form.judgeStrategy = (source?.judge_strategy as JudgeStrategy) ?? 'AVG'
  form.pass_threshold = source ? source.pass_threshold ?? null : 60
  form.riskLevel = (source?.risk_level as 'LOW' | 'HIGH') ?? 'LOW'
  form.owner = source?.owner ?? ''
  form.snapshotId = source?.snapshot_id ?? ''
  const normalizedRequestVariables = normalizeRequestVariables(source?.request_variables)
  form.requestVariables = Object.entries(normalizedRequestVariables).map(([key, value]) => ({
    key,
    value: requestVariableValueText(value),
  }))
  originalRequestVariables.value = Object.keys(normalizedRequestVariables).length
    ? normalizedRequestVariables
    : null
  form.requestVariablesJson = form.requestVariables.length
    ? JSON.stringify(normalizedRequestVariables, null, 2)
    : ''
  form.requestVariablesMode = 'rows'
  resetContractForm(source)
  form.change_summary = ''
  if (isCopy.value && source) {
    // 复制：编号/名称给默认建议但不落库，其余内容全部照搬源用例。
    form.code = suggestCopyCode(source.code)
    form.name = suggestCopyName(source.name)
  }
  void loadVersions('simulator')
  void loadVersions('judge')
  captureInitialSections()
}

function resetContractForm(source: TestCase | null): void {
  originalEnvelope.value = source?.answer_envelope ?? null
  originalPlan.value = source?.plan_spec ?? null
  originalContract.value = source?.response_contract ?? null
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
const aiPromptInput = ref<HTMLTextAreaElement | null>(null)
const aiLoading = ref(false)
const aiError = ref('')
const aiDrafts = ref<CaseAIDraft[]>([])
const aiDraftIndex = ref(0)
const aiResponseWarnings = ref<string[]>([])
const aiCount = ref(1)
const aiDraft = computed<CaseAIDraft | null>(() => aiDrafts.value[aiDraftIndex.value] ?? null)
const aiDraftConfidenceClass = computed(() => {
  const value = aiDraft.value?.confidence ?? 0
  if (value >= 0.7) return 'bg-emerald-50 text-emerald-700'
  if (value >= 0.4) return 'bg-amber-50 text-amber-700'
  return 'bg-rose-50 text-rose-700'
})
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
const aiRestored = ref(false)
const aiRestoredApplied = ref(false)
const drawerBody = ref<HTMLElement | null>(null)

watch(
  () => [props.modelValue, props.caseData, props.copyFrom] as const,
  ([open]) => {
    if (open) {
      resetForm()
      resetAiState()
      restoreAiDraft()
      if (!props.embedded) document.body.style.overflow = 'hidden'
    } else if (!props.embedded) {
      document.body.style.overflow = ''
    }
  },
  { immediate: true },
)

// AI 草稿一旦变化就写本地缓存；抽屉关闭时保留，刷新或下次打开可继续。
watch(
  [aiPrompt, aiDrafts, aiDraftIndex, aiApplyMode, aiMissingValues, aiSelected, aiPane, aiCount, aiOpen],
  () => {
    if (!props.modelValue) return
    persistAiDraft()
  },
  { deep: true },
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
    // 文本与源值完全一致时直接复用源对象，避免重新解析造成类型漂移；仍统一归一化为字符串。
    const sourceVariables = originalRequestVariables.value
    if (sourceVariables && text === JSON.stringify(sourceVariables, null, 2).trim()) {
      return normalizeRequestVariables(sourceVariables)
    }
    let parsed: unknown
    try {
      parsed = parseJsonPreservingNumbers(text)
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
    // 行内文本未变时保留源值（布尔 / 对象等结构），数字则在返回前统一归一化为字符串。
    const sourceVariables = originalRequestVariables.value
    const originalValue =
      sourceVariables && Object.prototype.hasOwnProperty.call(sourceVariables, key)
        ? sourceVariables[key]
        : undefined
    const unchanged =
      originalValue !== undefined &&
      requestVariableValueText(originalValue) === row.value
    result[key] = unchanged ? originalValue : parseRequestVariableValue(row.value)
  }
  return normalizeRequestVariables(result)
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
    tags: reuseListIfUnchanged(copySource.value?.tags, form.tagsText),
    environment_id: form.environment_id,
    request_variables: requestVariables,
    max_turns: form.maxTurns,
    simulator_template_id: form.simulator_template_id,
    simulator_template_strategy: form.simulatorStrategy,
    simulator_template_version:
      form.simulatorStrategy === 'PINNED'
        ? form.simulatorVersion
        : isCopy.value
          ? copySource.value?.simulator_template_version ?? null
          : null,
    judge_template_id: form.judge_template_id,
    judge_template_strategy: form.judgeTemplateStrategy,
    judge_template_version:
      form.judgeTemplateStrategy === 'PINNED'
        ? form.judgeVersion
        : isCopy.value
          ? copySource.value?.judge_template_version ?? null
          : null,
    judge_model_ids: [...form.judge_model_ids],
    judge_strategy: form.judgeStrategy,
    pass_threshold:
      typeof form.pass_threshold === 'number' && Number.isFinite(form.pass_threshold)
        ? form.pass_threshold
        : null,
    prompt_overrides: copySource.value?.prompt_overrides ?? null,
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
    payload.snapshot_id = form.snapshotId.trim() || null
    payload.risk_level = form.riskLevel
    payload.owner = form.owner.trim() || null
    payload.answer_envelope = null
    payload.response_contract = null
    payload.plan_spec = null
  }

  if (form.case_type === 'SINGLE') {
    payload.query = form.query.trim()
    payload.expected_answer = form.expected_answer.trim() || null
    payload.expected_rules = buildRules()
    payload.turns = null
    // 跨类型字段一并保留，避免复制/编辑时被默认值静默覆盖。
    payload.session_expected = copySource.value?.session_expected ?? null
    payload.fail_fast = copySource.value?.fail_fast ?? false
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
    // 用例级规则通常为空，但源用例若显式配置过则原样保留。
    payload.expected_answer = copySource.value?.expected_answer ?? null
    payload.expected_rules = copySource.value?.expected_rules ?? null
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
    const keywords = reuseListIfUnchanged(
      Array.isArray(copySource.value?.terminate_conditions?.keywords)
        ? (copySource.value!.terminate_conditions!.keywords as unknown[]).map(String)
        : undefined,
      form.terminateKeywordsText,
    )
    // 保留源用例里界面未暴露的终止条件字段（例如自定义判定开关），避免复制/编辑时丢失。
    const terminateRest: Record<string, unknown> = {
      ...(copySource.value?.terminate_conditions ?? {}),
    }
    delete terminateRest.keywords
    payload.user_goal = form.userGoal.trim()
    payload.initial_query = form.initialQuery.trim() || null
    payload.persona_description = form.persona.trim() || null
    payload.hidden_context = hidden
    payload.terminate_conditions = keywords.length
      ? { ...terminateRest, keywords }
      : Object.keys(terminateRest).length
        ? terminateRest
        : null
    payload.expected_answer = form.expected_answer.trim() || null
    payload.expected_rules = buildRules()
    payload.turns = null
    payload.query = null
    payload.session_expected = copySource.value?.session_expected ?? null
    payload.fail_fast = copySource.value?.fail_fast ?? false
  }

  // 复制模式兜底：当前类型分支未显式设置的字段，原样从源用例补入，
  // 保证任何跨类型字段都不会因表单未暴露而被默认值覆盖。
  if (isCopy.value && props.copyFrom) {
    const source = props.copyFrom
    if (payload.hidden_context === undefined) payload.hidden_context = source.hidden_context ?? null
    if (payload.user_goal === undefined) payload.user_goal = source.user_goal ?? null
    if (payload.initial_query === undefined) payload.initial_query = source.initial_query ?? null
    if (payload.persona_description === undefined) {
      payload.persona_description = source.persona_description ?? null
    }
    if (payload.terminate_conditions === undefined) {
      payload.terminate_conditions = source.terminate_conditions ?? null
    }
    if (payload.session_expected === undefined) {
      payload.session_expected = source.session_expected ?? null
    }
    if (payload.fail_fast === undefined) payload.fail_fast = source.fail_fast ?? false
    if (payload.query === undefined) payload.query = source.query ?? null
    if (payload.expected_answer === undefined) {
      payload.expected_answer = source.expected_answer ?? null
    }
    if (payload.expected_rules === undefined) {
      payload.expected_rules = source.expected_rules ?? null
    }
    if (payload.turns === undefined) payload.turns = source.turns ?? null
  }

  // 待补充项回写：已应用草稿里仍未补上依据的项，保存时写进用例描述，
  // 避免关闭抽屉后无人记得「这里还缺一个数」。只追加缺的，不重复堆积。
  const pendingMissing = aiApplied.value
    ? (aiDraft.value?.missing ?? []).filter((item) => !isMissingFilled(item))
    : []
  if (pendingMissing.length) {
    const description = payload.description ?? ''
    const existingNote = description
      .split('\n')
      .filter((line) => line.trim().startsWith('待补充：'))
      .join('\n')
    const unresolved = pendingMissing.filter((item) => !existingNote.includes(item.label))
    if (unresolved.length) {
      const note = `待补充：${unresolved
        .map((item) => {
          const filled = aiMissingValues.value[item.field]?.trim()
          // 无法回填到表单的字段（如模型自由声明的 expected_answer），
          // 至少把用户手填的值一并落进描述，避免输入了却悄悄消失。
          return filled
            ? `${item.label}（用户填写：${filled}；${item.reason}）`
            : `${item.label}（${item.reason}）`
        })
        .join('；')}`
      payload.description = description ? `${description}\n${note}` : note
    }
  }

  return payload
}

function submit(): void {
  const payload = buildPayload()
  if (!payload) return
  // 已提交保存的草稿不再保留，避免下次编辑时恢复出过期内容。
  clearStoredDraft()
  aiRestored.value = false
  aiRestoredApplied.value = false
  emit('submit', payload)
}

/** 工作台：用当前表单内容直接试跑，不落版本、不动评审状态 */
function tryRun(): void {
  const payload = buildPayload()
  if (!payload) return
  emit('try-run', payload)
}

// 独立工作台底部浮窗通过 ref 调用表单校验 + 提交 / 试运行。
defineExpose({ tryRun, submit })

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

const AI_CREATE_DYNAMIC_EXAMPLES = [
  '动态多轮：用户不主动给号码，客服反问后才释放隐藏信息',
  '动态多轮核对账单总额，最多 6 轮，终止关键词「账单总额」',
  '动态多轮：用户先问余额，客服反问后释放 13800138000',
  '动态多轮超套费咨询，隐藏信息是手机号与账期',
]

const AI_EDIT_DYNAMIC_EXAMPLES = [
  '最多 8 轮就结束',
  '补充一条隐藏信息：账期',
  '用户目标改成「拿到账单总额与费用项数」',
  '初始提问更口语化',
]

const aiExamples = computed(() => {
  if (!isEdit.value) {
    if (form.case_type === 'DYNAMIC_MULTI') return AI_CREATE_DYNAMIC_EXAMPLES
    return form.case_type === 'STATIC_MULTI' ? AI_CREATE_MULTI_EXAMPLES : AI_EXAMPLES
  }
  if (form.case_type === 'DYNAMIC_MULTI') return AI_EDIT_DYNAMIC_EXAMPLES
  return form.case_type === 'STATIC_MULTI' ? AI_EDIT_MULTI_EXAMPLES : AI_EDIT_EXAMPLES
})

function applyAiExample(example: string): void {
  const current = aiPrompt.value
  // 输入框为空时直接填入；已有内容时追加，绝不覆盖用户已经写好的描述。
  if (!current.trim()) {
    aiPrompt.value = example
  } else if (!current.includes(example)) {
    const joiner = /[，,。；;！!？?\s]$/.test(current) ? '' : '，'
    aiPrompt.value = `${current.trimEnd()}${joiner}${example}`
  }
  // 追加后把光标移到末尾，方便继续补充。
  void nextTick(() => {
    const element = aiPromptInput.value
    if (!element) return
    element.focus()
    element.setSelectionRange(element.value.length, element.value.length)
  })
}

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

function hiddenSignature(key: string, content: string, hint: string | null): string {
  return `${key.trim()}|${content.trim()}|${(hint ?? '').trim()}`
}

function hiddenRuleText(key: string, content: string, hint: string | null): string {
  const head = key.trim() ? `${key.trim()}：` : ''
  return `${head}${content.trim()}${hint && hint.trim() ? `（${hint.trim()}）` : ''}`
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

/** 动态多轮的隐藏信息：按 key 对齐，逐条可勾选。 */
function hiddenGroupRow(
  current: HiddenRule[],
  draftItems: DraftHiddenContext[],
  source: DraftSource,
): AiChangeRow {
  const currentRows = current.filter((item) => item.key.trim() || item.content.trim())
  const currentKeys = currentRows.map((item) =>
    hiddenSignature(item.key, item.content, item.release_hint),
  )
  const nextKeys = draftItems.map((item) =>
    hiddenSignature(item.key, item.content, item.release_hint),
  )
  const same =
    currentKeys.length === nextKeys.length &&
    currentKeys.every((item, index) => item === nextKeys[index])
  let status: AiChangeStatus
  if (!currentKeys.length) status = nextKeys.length ? 'add' : 'same'
  else if (!nextKeys.length) status = 'clear'
  else status = same ? 'same' : 'overwrite'
  const existing = new Set(currentKeys)

  const matched = new Set<number>()
  const children: AiChangeChild[] = draftItems.map((item, index) => {
    const signature = hiddenSignature(item.key, item.content, item.release_hint)
    let currentIndex = currentKeys.indexOf(signature)
    if (currentIndex < 0) {
      const targetKey = item.key.trim()
      currentIndex = targetKey
        ? currentRows.findIndex((row) => row.key.trim() === targetKey)
        : -1
    }
    if (currentIndex >= 0) matched.add(currentIndex)
    const childStatus: AiChangeStatus = existing.has(signature)
      ? 'same'
      : currentIndex >= 0
        ? 'overwrite'
        : 'add'
    return {
      key: `hidden:${index}`,
      label: item.key.trim() || `第 ${index + 1} 条`,
      current:
        currentIndex >= 0
          ? hiddenRuleText(
              currentRows[currentIndex].key,
              currentRows[currentIndex].content,
              currentRows[currentIndex].release_hint,
            )
          : '',
      next: hiddenRuleText(item.key, item.content, item.release_hint),
      status: childStatus,
      fillable: childStatus === 'add',
      source,
      draftIndex: index,
      currentIndex: currentIndex >= 0 ? currentIndex : undefined,
    }
  })
  currentRows.forEach((row, index) => {
    if (matched.has(index)) return
    children.push({
      key: `hidden:${draftItems.length + index}`,
      label: row.key.trim() || `第 ${index + 1} 条`,
      current: hiddenRuleText(row.key, row.content, row.release_hint),
      next: '',
      status: 'clear',
      fillable: false,
      source,
      currentIndex: index,
    })
  })

  return {
    key: 'hidden',
    label: '隐藏信息',
    current: currentRows
      .map((row) => hiddenRuleText(row.key, row.content, row.release_hint))
      .join('；'),
    next: draftItems
      .map((item) => hiddenRuleText(item.key, item.content, item.release_hint))
      .join('；'),
    status,
    // 仅填空白只会追加草稿里新增的隐藏信息。
    fillable: draftItems.some((item) => !existing.has(hiddenSignature(item.key, item.content, item.release_hint))),
    source,
    children: children.length ? children : undefined,
    hint: '逐条勾选时按条合并：未勾选的现有隐藏信息会保留。',
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

function boolText(value: boolean | null | undefined, fallback = false): string {
  return (value ?? fallback) ? '是' : '否'
}

function ruleSummary(rules: RuleConfig | null | undefined): string {
  if (!rules) return ''
  const parts: string[] = []
  for (const item of rules.amounts ?? []) parts.push(`金额·${item.label} ${item.value}`)
  for (const item of rules.counts ?? []) parts.push(`笔数·${item.label} ${item.value}`)
  for (const value of rules.dates ?? []) parts.push(`日期 ${value}`)
  for (const value of rules.must_contain ?? []) parts.push(`必须含 ${value}`)
  for (const value of rules.must_not_contain ?? []) parts.push(`禁止含 ${value}`)
  return parts.join('；')
}

function requestVariableValueText(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** 当前表单里的请求变量（行编辑 / JSON 两种模式统一成映射）。 */
function currentRequestVariables(): Record<string, unknown> {
  if (form.requestVariablesMode === 'json') {
    try {
      const parsed = parseJsonPreservingNumbers(form.requestVariablesJson || '{}')
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return normalizeRequestVariables(parsed as Record<string, unknown>)
      }
    } catch {
      // 非法 JSON 时按空处理，保存前会再次校验。
    }
    return {}
  }
  const result: Record<string, unknown> = {}
  for (const row of form.requestVariables) {
    const key = row.key.trim()
    if (key) result[key] = parseRequestVariableValue(row.value)
  }
  return normalizeRequestVariables(result)
}

function requestVariablesSummary(values: Record<string, unknown>): string {
  return Object.entries(values)
    .map(([key, value]) => `${key}=${requestVariableValueText(value)}`)
    .join('；')
}

function assumptionAmountText(contract: ResponseContract | null): string {
  const must = contract?.answer_with_assumption?.scenarios?.[0]?.must ?? {}
  const value = (must as Record<string, unknown>).amount ?? Object.values(must)[0]
  return value === null || value === undefined ? '' : String(value)
}

function inputTypeLabel(value: InputType): string {
  return INPUT_TYPES.find((item) => item.value === value)?.label ?? value
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
  rows.push(
    textRow(
      'inputType',
      '输入形态',
      inputTypeLabel(form.inputType),
      draft.input_type ? inputTypeLabel(draft.input_type) : '',
      draftSource(draft, 'input_type'),
    ),
  )
  if (draft.request_variables) {
    rows.push(
      textRow(
        'requestVariables',
        '请求变量',
        requestVariablesSummary(currentRequestVariables()),
        requestVariablesSummary(draft.request_variables),
        draftSource(draft, 'request_variables'),
      ),
    )
  }
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
  // 4 维规则（LEGACY）：只在草稿明确给出规则时展示，避免编辑时误清空既有规则。
  const draftRules = draft.expected_rules
  if (draftRules) {
    rows.push(
      textRow(
        'expectedRules',
        '4 维规则',
        ruleSummary(buildRules()),
        ruleSummary(draftRules),
        draftSource(draft, 'expected_rules'),
      ),
    )
  }
  if (draft.extras_policy) {
    rows.push(
      textRow(
        'extrasPolicy',
        '包络策略',
        form.extrasPolicy,
        draft.extras_policy,
        draftSource(draft, 'extras_policy'),
      ),
    )
  }
  rows.push(
    textRow('riskLevel', '风险等级', form.riskLevel, draft.risk_level, draftSource(draft, 'risk_level')),
  )
  if (draft.judge_strategy) {
    rows.push(
      textRow(
        'judgeStrategy',
        '裁判策略',
        form.judgeStrategy,
        draft.judge_strategy,
        draftSource(draft, 'judge_strategy'),
      ),
    )
  }
  if (draft.pass_threshold !== null && draft.pass_threshold !== undefined) {
    rows.push(
      textRow(
        'passThreshold',
        '通过分数线',
        String(form.pass_threshold ?? ''),
        String(draft.pass_threshold),
        draftSource(draft, 'pass_threshold'),
      ),
    )
  }
  if (isEdit.value && draft.change_summary) {
    rows.push(
      textRow(
        'changeSummary',
        '变更说明',
        form.change_summary,
        draft.change_summary,
        draftSource(draft, 'change_summary'),
      ),
    )
  }
  if (form.case_type === 'STATIC_MULTI') {
    rows.push(turnGroupRow('turns', '多轮脚本', form.turns, draft.turns, draftSource(draft, 'turns', 'LLM')))
    if (draft.session_expected) {
      rows.push(
        textRow(
          'sessionExpected',
          '会话级预期',
          sessionExpectedText.value,
          JSON.stringify(draft.session_expected),
          draftSource(draft, 'session_expected'),
        ),
      )
    }
    if (draft.fail_fast !== null && draft.fail_fast !== undefined) {
      rows.push(
        textRow(
          'failFast',
          '失败即停',
          boolText(form.failFast),
          boolText(draft.fail_fast),
          draftSource(draft, 'fail_fast'),
        ),
      )
    }
  }
  if (form.case_type === 'DYNAMIC_MULTI') {
    rows.push(
      textRow('userGoal', '用户目标', form.userGoal, draft.user_goal ?? '', draftSource(draft, 'user_goal')),
    )
    rows.push(
      textRow(
        'initialQuery',
        '初始提问',
        form.initialQuery,
        draft.initial_query ?? '',
        draftSource(draft, 'initial_query'),
      ),
    )
    rows.push(
      textRow(
        'persona',
        '人设描述',
        form.persona,
        draft.persona_description ?? '',
        draftSource(draft, 'persona_description'),
      ),
    )
    rows.push(
      textRow(
        'maxTurns',
        '最大轮次',
        String(form.maxTurns),
        draft.max_turns ? String(draft.max_turns) : '',
        draftSource(draft, 'max_turns'),
      ),
    )
    rows.push(
      textRow(
        'terminateKeywords',
        '终止关键词',
        form.terminateKeywordsText,
        (draft.terminate_conditions?.keywords ?? []).join(', '),
        draftSource(draft, 'terminate_conditions'),
      ),
    )
    rows.push(hiddenGroupRow(form.hidden, draft.hidden_context, draftSource(draft, 'hidden_context')))
  }
  // 响应契约：草稿给出契约，或当前表单已处于非清晰输入时展示。
  const contract = draft.response_contract
  if (contract) {
    const allowed = new Set(contract.allowed ?? [])
    const source = draftSource(draft, 'response_contract')
    rows.push(
      textRow('allowClarify', '允许澄清', boolText(form.allowClarify), boolText(allowed.has('clarify')), source),
    )
    rows.push(
      textRow(
        'clarifySlots',
        '澄清槽位',
        form.clarifySlotsText,
        (contract.clarify?.must_ask_slot ?? []).join(', '),
        source,
      ),
    )
    rows.push(
      textRow(
        'allowAssumption',
        '允许假设作答',
        boolText(form.allowAssumption),
        boolText(allowed.has('answer_with_assumption')),
        source,
      ),
    )
    rows.push(
      textRow(
        'assumptionText',
        '假设说明',
        form.assumptionText,
        contract.answer_with_assumption?.scenarios?.[0]?.assumption ?? '',
        source,
      ),
    )
    rows.push(
      textRow(
        'assumptionAmount',
        '假设期望值',
        form.assumptionAmount,
        assumptionAmountText(contract),
        source,
      ),
    )
    rows.push(
      textRow('allowPartial', '允许部分披露', boolText(form.allowPartial), boolText(allowed.has('partial')), source),
    )
    rows.push(
      textRow(
        'partialFacts',
        '部分披露事实',
        form.partialFactsText,
        (contract.partial?.required_facts ?? []).join(', '),
        source,
      ),
    )
    rows.push(
      textRow('allowRefuse', '允许拒绝', boolText(form.allowRefuse), boolText(allowed.has('refuse')), source),
    )
  }
  // 取数计划（诊断）：只在草稿给出时展示。
  if (draft.plan_spec) {
    const source = draftSource(draft, 'plan_spec')
    rows.push(
      textRow(
        'planCapabilities',
        '取数能力',
        form.planCapabilitiesText,
        (draft.plan_spec.required_capabilities ?? []).join(', '),
        source,
      ),
    )
    rows.push(
      textRow(
        'maxToolCalls',
        '工具调用上限',
        form.maxToolCalls === null ? '' : String(form.maxToolCalls),
        draft.plan_spec.max_tool_calls ? String(draft.plan_spec.max_tool_calls) : '',
        source,
      ),
    )
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
  if (child.tail && aiApplyMode.value === 'fillBlank') {
    // 仅填空白时，尾部新增轮次必须整组追加，避免只补中间一轮导致轮次顺序错位；
    // 整组替换下每轮都可独立勾选。
    const tail = (row.children ?? []).filter((item) => item.tail && childSelectable(item))
    toggleKeys(tail.map((item) => item.key), select)
    return
  }
  toggleKeys([child.key], select)
}

/** 逐条状态下，「仅填空白」只会追加，不能补的条目按「不变」展示。 */
function childStatus(child: AiChangeChild): AiChangeStatus {
  if (aiApplyMode.value !== 'fillBlank') return child.status
  return child.fillable ? 'append' : 'same'
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
  return missingFieldSupported(item.field) && Boolean(aiMissingValues.value[item.field]?.trim())
}

/** 能被自动写回表单的字段白名单。其余字段只能进备注，不能因为填了值就标「已补」。 */
function missingFieldSupported(field: string): boolean {
  return (
    field === 'as_of' ||
    field === 'turns' ||
    field === 'must' ||
    field === 'may' ||
    field === 'hidden_context' ||
    field === 'response_contract' ||
    field === 'plan_spec' ||
    field.startsWith('must.') ||
    field.startsWith('may.') ||
    field.startsWith('must_not.') ||
    field.startsWith('hidden_context.') ||
    field.startsWith('request_variables.') ||
    field.startsWith('expected_rules.') ||
    field.startsWith('response_contract.') ||
    /^turns\.\d+\.expected_rules\./.test(field)
  )
}

/** 用例备注里已经回写的待补充项：编辑时提示人工补齐，避免反复丢项。 */
const pendingNotes = computed(() =>
  form.description
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('待补充：'))
    .join('\n'),
)

const aiMissingFilledCount = computed(
  () => aiDraft.value?.missing.filter(isMissingFilled).length ?? 0,
)

const aiApplyCount = computed(() => aiSelectedCount.value + aiMissingFilledCount.value)

function missingPlaceholder(item: DraftMissing): string {
  if (item.field === 'as_of') return '如 2026-11，补全后可直接应用'
  if (item.field === 'turns') return '补充一轮用户提问，如：那明细呢？'
  if (item.field === 'must' || item.field.startsWith('must.')) return '补充期望值，如 268.50'
  if (item.field.startsWith('may.')) return '补充可扩展事实的期望值'
  if (item.field.startsWith('request_variables.')) return '补充取值，如 2026-11 或 13800138000'
  if (item.field.includes('.dates.')) return '补充账期/日期，如 2026-11'
  if (item.field.includes('.amounts.') || item.field.includes('.counts.')) {
    return '补充数值，如 268.50'
  }
  if (item.field === 'response_contract') return '填可接受行为，如 clarify,refuse'
  if (item.field === 'response_contract.partial') return '填必须披露的事实名称'
  if (item.field === 'response_contract.answer_with_assumption') return '补充假设作答的期望值'
  if (item.field === 'plan_spec') return '填能力编码，如 bill_query'
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
  if (draft.user_goal) count += 1
  if (draft.initial_query) count += 1
  if (draft.persona_description) count += 1
  if (draft.hidden_context.length) count += 1
  if (draft.max_turns) count += 1
  if (draft.terminate_conditions?.keywords?.length) count += 1
  if (draft.must.length) count += 1
  if (draft.may.length) count += 1
  if (draft.must_not.length) count += 1
  if (draft.forbidden_phrases.length) count += 1
  if (draft.request_variables && Object.keys(draft.request_variables).length) count += 1
  if (draft.expected_rules) count += 1
  if (draft.session_expected) count += 1
  if (draft.fail_fast !== null && draft.fail_fast !== undefined) count += 1
  if (draft.response_contract) count += 1
  if (draft.plan_spec) count += 1
  if (draft.extras_policy) count += 1
  if (draft.judge_strategy) count += 1
  if (draft.pass_threshold !== null) count += 1
  if (draft.change_summary) count += 1
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

function aiDraftScope(): string {
  const current = props.caseData
  if (current?.id) return `edit:${current.id}`
  if (current?.code) return `edit:${current.code}`
  if (isCopy.value && props.copyFrom?.id) return `copy:${props.copyFrom.id}`
  return 'create'
}

function loadStoredDraft(): StoredAiDraft | null {
  try {
    const raw = localStorage.getItem(AI_DRAFT_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as StoredAiDraft
    if (!parsed || parsed.scope !== aiDraftScope()) return null
    if (!Array.isArray(parsed.drafts) || !parsed.drafts.length) return null
    if (Date.now() - Number(parsed.savedAt || 0) > AI_DRAFT_TTL_MS) {
      localStorage.removeItem(AI_DRAFT_STORAGE_KEY)
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function persistAiDraft(): void {
  if (!aiDrafts.value.length) return
  try {
    const payload: StoredAiDraft = {
      savedAt: Date.now(),
      scope: aiDraftScope(),
      prompt: aiPrompt.value,
      drafts: aiDrafts.value,
      draftIndex: aiDraftIndex.value,
      warnings: aiResponseWarnings.value,
      baseCode: aiBaseCode.value,
      meta: aiMeta.value,
      count: aiCount.value,
      applyMode: aiApplyMode.value,
      selected: [...aiSelected.value],
      missingValues: aiMissingValues.value,
      pane: aiPane.value,
      // 应用状态用于刷新后提示「表单已重置，请重新应用」：本次已应用写实际条数，
      // 恢复自「已应用」草稿时保留标记（写 1 即可，仅用于判断是否已应用过）。
      appliedCount: aiApplied.value ? aiAppliedCount.value : aiRestoredApplied.value ? 1 : 0,
    }
    localStorage.setItem(AI_DRAFT_STORAGE_KEY, JSON.stringify(payload))
  } catch {
    // 隐私模式或配额不足时静默降级：仅本次会话可用。
  }
}

function clearStoredDraft(): void {
  try {
    localStorage.removeItem(AI_DRAFT_STORAGE_KEY)
  } catch {
    // 忽略本地存储不可用
  }
}

function restoreAiDraft(): void {
  aiRestored.value = false
  aiRestoredApplied.value = false
  const stored = loadStoredDraft()
  if (!stored) return
  aiPrompt.value = stored.prompt ?? ''
  aiDrafts.value = stored.drafts
  aiDraftIndex.value = Math.min(
    Math.max(Number(stored.draftIndex) || 0, 0),
    stored.drafts.length - 1,
  )
  aiResponseWarnings.value = stored.warnings ?? []
  aiBaseCode.value = stored.baseCode ?? null
  aiMeta.value = stored.meta ?? null
  aiCount.value = Number(stored.count) || 1
  aiApplyMode.value = stored.applyMode === 'fillBlank' ? 'fillBlank' : 'replace'
  aiMissingValues.value = stored.missingValues ?? {}
  aiSelected.value = new Set(stored.selected ?? [])
  aiPane.value = stored.pane === 'input' ? 'input' : 'result'
  // 恢复草稿但不自动展开浮窗：抽屉每次打开都保持收起状态。
  // 草稿仍在（24h 内可继续），按钮上的小圆点提示「有未完成的草稿」，
  // 用户主动点开 AI 面板才显示，避免「生成过一次就次次弹窗」。
  aiRestored.value = true
  aiRestoredApplied.value = Number(stored.appliedCount) > 0
  // 新建时类型可切换：恢复多轮草稿时同步切到对应类型，否则轮次/动态字段没有落点。
  const restoredType = aiDrafts.value[aiDraftIndex.value]?.case_type
  if (
    !isEdit.value &&
    (restoredType === 'STATIC_MULTI' || restoredType === 'DYNAMIC_MULTI') &&
    form.case_type !== restoredType
  ) {
    form.case_type = restoredType
  }
}

/** 用户主动丢弃草稿：清掉本地缓存并回到输入态。 */
function discardAiDraft(): void {
  clearStoredDraft()
  resetAiState()
  aiRestored.value = false
  aiRestoredApplied.value = false
  aiOpen.value = true
  aiPane.value = 'input'
  toast.info('已清除本地保存的 AI 草稿')
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
  aiRestored.value = false
  aiRestoredApplied.value = false
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
      // 新建时类型仍可切换：草稿给出多轮时就地切换，避免轮次/动态字段没有落点。
      const draftType = response.drafts[0]?.case_type
      if (draftType === 'DYNAMIC_MULTI' && form.case_type !== 'DYNAMIC_MULTI') {
        form.case_type = 'DYNAMIC_MULTI'
        aiResponseWarnings.value = [
          ...aiResponseWarnings.value,
          '草稿是动态多轮，已自动将用例类型切换为动态多轮',
        ]
      } else if (draftType === 'STATIC_MULTI' && form.case_type !== 'STATIC_MULTI') {
        form.case_type = 'STATIC_MULTI'
        aiResponseWarnings.value = [
          ...aiResponseWarnings.value,
          '草稿是静态多轮，已自动将用例类型切换为静态多轮',
        ]
      } else if (draftType === 'SINGLE' && form.case_type !== 'SINGLE') {
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
              rulesJson: turn.expected_rules
                ? JSON.stringify(turn.expected_rules)
                : (existing?.rulesJson ?? ''),
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
        rulesJson: turn.expected_rules ? JSON.stringify(turn.expected_rules) : '',
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
      target.rulesJson = draftTurn.expected_rules
        ? JSON.stringify(draftTurn.expected_rules)
        : ''
    } else {
      result.push({
        query: draftTurn.query,
        expected_answer: draftTurn.expected_answer ?? '',
        notes: draftTurn.notes ?? '',
        rulesJson: draftTurn.expected_rules ? JSON.stringify(draftTurn.expected_rules) : '',
      })
    }
  }
  form.turns = result.length
    ? result
    : [{ query: '', expected_answer: '', notes: '', rulesJson: '' }]
}

/** 隐藏信息按条应用：勾选的条目覆盖 / 新增，未勾选的现有条目原样保留。 */
function applyHiddenGroup(picked?: AiChangeChild[]): void {
  const draft = aiDraft.value
  if (!draft) return
  const draftItems = draft.hidden_context.map((item) => ({
    key: item.key.trim(),
    content: item.content.trim(),
    release_hint: (item.release_hint ?? '').trim(),
  }))
  const toRule = (item: { key: string; content: string; release_hint: string }): HiddenRule => ({
    key: item.key,
    content: item.content,
    release_hint: item.release_hint,
  })
  const currentRows = form.hidden.filter((item) => item.key.trim() || item.content.trim())

  if (!picked) {
    if (aiApplyMode.value === 'replace') {
      form.hidden = draftItems.length
        ? draftItems.map(toRule)
        : [{ key: '', content: '', release_hint: '' }]
      return
    }
    const existing = new Set(currentRows.map((item) => item.key.trim()))
    const additions = draftItems.filter((item) => !existing.has(item.key))
    const merged = [...currentRows, ...additions.map(toRule)]
    form.hidden = merged.length ? merged : [{ key: '', content: '', release_hint: '' }]
    return
  }

  const replaceAt = new Map<number, number>()
  const clearAt = new Set<number>()
  const additions: number[] = []
  for (const child of picked) {
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
  const merged: HiddenRule[] = []
  currentRows.forEach((row, index) => {
    if (clearAt.has(index)) return
    const draftIndex = replaceAt.get(index)
    merged.push(draftIndex === undefined ? row : toRule(draftItems[draftIndex]))
  })
  for (const draftIndex of additions.sort((left, right) => left - right)) {
    merged.push(toRule(draftItems[draftIndex]))
  }
  form.hidden = merged.length ? merged : [{ key: '', content: '', release_hint: '' }]
}

function writeRequestVariables(values: Record<string, unknown>): void {
  const normalized = normalizeRequestVariables(values)
  const rows = Object.entries(normalized).map(([key, value]) => ({
    key,
    value: requestVariableValueText(value),
  }))
  form.requestVariables = rows
  form.requestVariablesJson = rows.length ? JSON.stringify(normalized, null, 2) : ''
  form.requestVariablesMode = 'rows'
}

function applyRequestVariables(): void {
  const next = aiDraft.value?.request_variables
  if (!next) return
  const current = currentRequestVariables()
  const merged: Record<string, unknown> = aiApplyMode.value === 'fillBlank' ? { ...current } : {}
  for (const [key, value] of Object.entries(next)) {
    if (aiApplyMode.value === 'fillBlank') {
      const existing = current[key]
      if (existing !== undefined && String(requestVariableValueText(existing)).trim()) continue
    }
    merged[key] = value
  }
  writeRequestVariables(merged)
}

function amountRuleFrom(item: NumericExpectation): AmountRule {
  return {
    label: item.label,
    value: item.value,
    tolerance: item.tolerance ?? 0,
    aliasesText: (item.aliases ?? []).join(', '),
    requireName: item.require_name ?? false,
  }
}

function applyExpectedRules(): void {
  const rules = aiDraft.value?.expected_rules
  if (!rules) return
  if (aiApplyMode.value === 'replace') {
    rulesFromCase(rules)
    return
  }
  // 仅填空白：只补当前为空的规则槽位，已有内容保持不动。
  if (!form.amounts.length) form.amounts = (rules.amounts ?? []).map(amountRuleFrom)
  if (!form.counts.length) form.counts = (rules.counts ?? []).map(amountRuleFrom)
  if (!form.datesText.trim()) form.datesText = (rules.dates ?? []).join(', ')
  if (!form.mustContainText.trim()) form.mustContainText = (rules.must_contain ?? []).join(', ')
  if (!form.mustNotContainText.trim()) {
    form.mustNotContainText = (rules.must_not_contain ?? []).join(', ')
  }
}

function parseRuleNumber(text: string): number | null {
  const raw = text.replace(/[,，\s元人民币CNYcny]/g, '')
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

function applyRuleMissing(rest: string, text: string, highlighted: Set<string>): boolean {
  const [kind, ...labelParts] = rest.split('.')
  const label = labelParts.join('.') || '数值'
  if (kind === 'amounts' || kind === 'counts') {
    const value = parseRuleNumber(text)
    if (value === null) return false
    const target = kind === 'amounts' ? form.amounts : form.counts
    const existing = target.find((rule) => rule.label.trim() === label.trim())
    if (existing) existing.value = value
    else {
      target.push({
        label,
        value,
        tolerance: kind === 'amounts' ? 0.01 : 0,
        aliasesText: '',
        requireName: false,
      })
    }
    highlighted.add('expectedRules')
    return true
  }
  if (kind === 'dates') {
    const dates = splitList(form.datesText)
    if (!dates.includes(text)) form.datesText = [...dates, text].join(', ')
    highlighted.add('expectedRules')
    return true
  }
  return false
}

function applyTurnRuleMissing(field: string, text: string, highlighted: Set<string>): boolean {
  const match = field.match(/^turns\.(\d+)\.expected_rules\.(.+)$/)
  if (!match) return false
  const position = Number(match[1]) - 1
  const turn = form.turns.filter((item) => item.query.trim())[position]
  if (!turn) return false
  let rules: RuleConfig = {
    amounts: [],
    counts: [],
    dates: [],
    must_contain: [],
    must_not_contain: [],
  }
  if (turn.rulesJson.trim()) {
    try {
      rules = { ...rules, ...(JSON.parse(turn.rulesJson) as RuleConfig) }
    } catch {
      // 非法 JSON 时按空规则继续补值。
    }
  }
  const [kind, ...labelParts] = match[2].split('.')
  const label = labelParts.join('.') || '数值'
  if (kind === 'amounts' || kind === 'counts') {
    const value = parseRuleNumber(text)
    if (value === null) return false
    const list = kind === 'amounts' ? rules.amounts : rules.counts
    const existing = list.find((rule) => rule.label.trim() === label.trim())
    if (existing) existing.value = value
    else {
      list.push({
        label,
        value,
        tolerance: kind === 'amounts' ? 0.01 : 0,
        aliases: [],
        require_name: false,
      })
    }
  } else if (kind === 'dates') {
    if (!rules.dates.includes(text)) rules.dates.push(text)
  } else {
    return false
  }
  turn.rulesJson = JSON.stringify(rules)
  highlighted.add('turns')
  return true
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
  if (item.field === 'hidden_context' || item.field.startsWith('hidden_context.')) {
    const key = item.field.startsWith('hidden_context.')
      ? item.field.slice('hidden_context.'.length)
      : item.label
    form.hidden.push({ key, content: text, release_hint: '' })
    highlighted.add('hidden')
    return true
  }
  if (item.field === 'must' || item.field === 'may') {
    const rows = factRows(item.field)
    const row = emptyFactRow(inferMissingKind(text))
    row.label = item.label
    fillFactRowValue(row, text)
    rows.push(row)
    highlighted.add(item.field === 'must' ? 'mustFacts' : 'mayFacts')
    return true
  }
  if (item.field.startsWith('must_not.')) {
    form.mustNotText = [form.mustNotText.trim(), text].filter(Boolean).join(', ')
    highlighted.add('mustNotText')
    return true
  }
  if (item.field.startsWith('must.') || item.field.startsWith('may.')) {
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
  if (item.field.startsWith('request_variables.')) {
    const key = item.field.slice('request_variables.'.length).trim()
    if (!key) return false
    const values = currentRequestVariables()
    values[key] = parseRequestVariableValue(text)
    writeRequestVariables(values)
    highlighted.add('requestVariables')
    return true
  }
  if (/^turns\.\d+\.expected_rules\./.test(item.field)) {
    return applyTurnRuleMissing(item.field, text, highlighted)
  }
  if (item.field.startsWith('expected_rules.')) {
    return applyRuleMissing(item.field.slice('expected_rules.'.length), text, highlighted)
  }
  if (item.field === 'response_contract') {
    const behaviorMap: Record<string, 'allowClarify' | 'allowAssumption' | 'allowPartial' | 'allowRefuse'> = {
      clarify: 'allowClarify',
      answer_with_assumption: 'allowAssumption',
      partial: 'allowPartial',
      refuse: 'allowRefuse',
    }
    let applied = false
    for (const raw of text.split(/[,，、\s]+/)) {
      const key = raw.trim().toLowerCase()
      const target = behaviorMap[key]
      if (!target) continue
      form[target] = true
      highlighted.add(target)
      applied = true
    }
    return applied
  }
  if (item.field === 'response_contract.answer_with_assumption') {
    form.allowAssumption = true
    form.assumptionAmount = text
    highlighted.add('allowAssumption')
    highlighted.add('assumptionAmount')
    return true
  }
  if (item.field === 'response_contract.partial') {
    form.allowPartial = true
    form.partialFactsText = text
    highlighted.add('allowPartial')
    highlighted.add('partialFactsText')
    return true
  }
  if (item.field === 'plan_spec') {
    form.planCapabilitiesText = text
    highlighted.add('planCapabilitiesText')
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
      applyFactsGroup('may', picked)
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
    case 'userGoal':
      form.userGoal = draft.user_goal ?? ''
      highlighted.add('userGoal')
      break
    case 'initialQuery':
      form.initialQuery = draft.initial_query ?? ''
      highlighted.add('initialQuery')
      break
    case 'persona':
      form.persona = draft.persona_description ?? ''
      highlighted.add('persona')
      break
    case 'maxTurns':
      form.maxTurns = draft.max_turns ?? 5
      highlighted.add('maxTurns')
      break
    case 'terminateKeywords':
      form.terminateKeywordsText = (draft.terminate_conditions?.keywords ?? []).join(', ')
      highlighted.add('terminateKeywords')
      break
    case 'hidden':
      applyHiddenGroup(picked)
      highlighted.add('hidden')
      break
    case 'inputType':
      if (draft.input_type) form.inputType = draft.input_type
      highlighted.add('inputType')
      break
    case 'requestVariables':
      applyRequestVariables()
      highlighted.add('requestVariables')
      break
    case 'expectedRules':
      applyExpectedRules()
      highlighted.add('expectedRules')
      break
    case 'sessionExpected':
      sessionExpectedText.value = draft.session_expected
        ? JSON.stringify(draft.session_expected, null, 2)
        : ''
      highlighted.add('sessionExpected')
      break
    case 'failFast':
      form.failFast = Boolean(draft.fail_fast)
      highlighted.add('failFast')
      break
    case 'extrasPolicy':
      form.extrasPolicy = draft.extras_policy ?? 'lenient'
      highlighted.add('extrasPolicy')
      break
    case 'judgeStrategy':
      form.judgeStrategy = draft.judge_strategy ?? 'AVG'
      highlighted.add('judgeStrategy')
      break
    case 'passThreshold':
      form.pass_threshold = draft.pass_threshold ?? 60
      highlighted.add('pass_threshold')
      break
    case 'changeSummary':
      form.change_summary = draft.change_summary ?? ''
      highlighted.add('change_summary')
      break
    case 'allowClarify':
      form.allowClarify = (draft.response_contract?.allowed ?? []).includes('clarify')
      highlighted.add('allowClarify')
      break
    case 'clarifySlots':
      form.clarifySlotsText = (draft.response_contract?.clarify?.must_ask_slot ?? []).join(', ')
      highlighted.add('clarifySlotsText')
      break
    case 'allowAssumption':
      form.allowAssumption = (draft.response_contract?.allowed ?? []).includes(
        'answer_with_assumption',
      )
      highlighted.add('allowAssumption')
      break
    case 'assumptionText':
      form.assumptionText =
        draft.response_contract?.answer_with_assumption?.scenarios?.[0]?.assumption ?? ''
      highlighted.add('assumptionText')
      break
    case 'assumptionAmount':
      form.assumptionAmount = assumptionAmountText(draft.response_contract)
      highlighted.add('assumptionAmount')
      break
    case 'allowPartial':
      form.allowPartial = (draft.response_contract?.allowed ?? []).includes('partial')
      highlighted.add('allowPartial')
      break
    case 'partialFacts':
      form.partialFactsText = (draft.response_contract?.partial?.required_facts ?? []).join(', ')
      highlighted.add('partialFactsText')
      break
    case 'allowRefuse':
      form.allowRefuse = (draft.response_contract?.allowed ?? []).includes('refuse')
      highlighted.add('allowRefuse')
      break
    case 'planCapabilities':
      form.planCapabilitiesText = (draft.plan_spec?.required_capabilities ?? []).join(', ')
      highlighted.add('planCapabilitiesText')
      break
    case 'maxToolCalls':
      form.maxToolCalls = draft.plan_spec?.max_tool_calls ?? null
      highlighted.add('maxToolCalls')
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

  if (draft.grading_mode === 'ANSWER_CONTRACT') contractTab.value = 'facts'
  aiHighlight.value = highlighted
  aiAppliedCount.value = count
  aiApplied.value = true
  aiAppliedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  aiRestored.value = false
  aiRestoredApplied.value = false
  aiOpen.value = false
  const remaining = draft.missing.filter((item) => !isMissingFilled(item)).length
  const hints: string[] = []
  if (
    (highlighted.has('mustFacts') || highlighted.has('mayFacts')) &&
    form.gradingMode !== 'ANSWER_CONTRACT'
  ) {
    hints.push('必答/可扩展事实仅在答案契约模式下落库，请同时勾选「判分方式」')
  }
  const contractApplied =
    highlighted.has('allowClarify') ||
    highlighted.has('allowAssumption') ||
    highlighted.has('allowPartial') ||
    highlighted.has('allowRefuse')
  if (contractApplied) {
    if (form.gradingMode !== 'ANSWER_CONTRACT') {
      hints.push('响应契约仅在答案契约模式落库，请同时勾选「判分方式」')
    } else if (form.inputType === 'CLEAR') {
      hints.push('已填入响应契约，但输入形态仍为「清晰明确」，请同时应用「输入形态」')
    } else {
      contractTab.value = 'contract'
    }
  }
  if (highlighted.has('planCapabilitiesText') || highlighted.has('maxToolCalls')) {
    if (form.gradingMode !== 'ANSWER_CONTRACT') {
      hints.push('取数计划仅在答案契约模式落库，请同时勾选「判分方式」')
    } else {
      contractTab.value = 'plan'
    }
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
  const isMustNot = field.startsWith('must_not.')
  const isContractField =
    field === 'response_contract' || field.startsWith('response_contract.')
  const isPlanField = field === 'plan_spec'
  const isRuleField = field.startsWith('expected_rules.')
  const isTurnRuleField = /^turns\.\d+\.expected_rules\./.test(field)
  const isRequestVariableField = field.startsWith('request_variables.')
  const needsContract =
    field === 'as_of' ||
    field === 'must' ||
    field.startsWith('must.') ||
    field.startsWith('may.') ||
    isMustNot ||
    isContractField ||
    isPlanField
  if (needsContract && form.gradingMode !== 'ANSWER_CONTRACT') {
    form.gradingMode = 'ANSWER_CONTRACT'
    toast.info('已切换到答案契约模式以定位该字段')
  }
  if (isRuleField && form.gradingMode === 'ANSWER_CONTRACT') {
    toast.info('规则硬校验仅在 4 维评分模式生效，请切回 4 维评分再补充')
    return
  }
  if (isTurnRuleField && form.case_type !== 'STATIC_MULTI') {
    toast.info('该字段属于静态多轮脚本，请先切换用例类型')
    return
  }
  if (needsContract) {
    if (isMustNot) contractTab.value = 'mustnot'
    else if (isContractField) contractTab.value = 'contract'
    else if (isPlanField) contractTab.value = 'plan'
    else contractTab.value = 'facts'
  }
  aiOpen.value = false
  void nextTick(() => {
    let targetId = ''
    if (field === 'as_of') targetId = 'ai-field-asOf'
    else if (field === 'turns') targetId = 'ai-field-turns'
    else if (isTurnRuleField) targetId = 'ai-field-turns'
    else if (isMustNot) targetId = 'ai-field-mustNotText'
    else if (field.startsWith('hidden_context')) targetId = 'ai-field-hidden'
    else if (isRequestVariableField) targetId = 'ai-field-requestVariables'
    else if (isRuleField) targetId = 'ai-field-rules'
    else if (isContractField) targetId = 'ai-field-contract'
    else if (isPlanField) targetId = 'ai-field-plan'
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
  <Teleport to="body" :disabled="embedded">
    <Transition :name="embedded ? 'none' : 'drawer'">
      <div
        v-if="modelValue"
        :class="
          embedded
            ? 'flex h-full min-h-0 w-full'
            : 'fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm'
        "
        @click.self="!embedded && close()"
      >
        <aside
          :class="
            embedded
              ? 'flex h-full min-h-0 w-full flex-col overflow-hidden bg-white'
              : 'absolute right-0 top-0 flex h-full w-full max-w-3xl flex-col overflow-hidden rounded-l-tile border-l border-line bg-white shadow-tile-lift'
          "
        >
          <header class="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
            <div class="min-w-0">
              <h2 class="text-base font-semibold text-slate-900">{{ title }}</h2>
              <p class="mt-1 text-xs text-slate-500">
                {{
                  embedded
                    ? '试运行只生成执行记录、不产生版本；保存后才生成新版本'
                    : '保存后自动生成版本快照，可用于 Diff 对比与一键回滚'
                }}
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-2">
              <button
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
                <span
                  v-if="aiDrafts.length && !aiOpen"
                  class="ml-0.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-500"
                  title="有未完成的 AI 草稿，点击继续"
                ></span>
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
            v-if="aiOpen"
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
                <p v-if="aiRestored" class="mt-0.5 text-[9px] leading-relaxed text-amber-600">
                  已恢复刷新前的草稿{{ aiRestoredApplied ? '（表单已重置，请重新应用）' : '' }}
                </p>
              </div>
              <div class="ml-auto flex shrink-0 items-center gap-1">
                <button
                  v-if="aiDrafts.length || aiPrompt.trim()"
                  class="rounded-md px-1.5 py-1 text-[9px] font-medium text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                  title="清除本地保存的 AI 草稿"
                  @click="discardAiDraft"
                >
                  清除草稿
                </button>
                <button
                  class="grid h-6 w-6 shrink-0 place-items-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                  aria-label="关闭 AI 气泡"
                  @click="closeAi"
                >
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
              <template v-if="aiPane === 'input'">
                <textarea
                  ref="aiPromptInput"
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
                    type="button"
                    class="rounded-full border border-slate-200 bg-white px-2 py-1 text-[9.5px] font-medium text-slate-600 transition hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700"
                    title="追加到输入框，不覆盖已写内容"
                    @click="applyAiExample(example)"
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
                <div
                  v-if="isEdit && pendingNotes"
                  class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1.5"
                >
                  <p class="text-[9px] font-semibold text-amber-800">用例备注里记录的待补充项</p>
                  <p class="mt-0.5 whitespace-pre-line text-[9px] leading-relaxed text-amber-700">
                    {{ pendingNotes }}
                  </p>
                  <p class="mt-0.5 text-[8.5px] text-amber-600">
                    补齐后可直接在描述里删掉这条备注。
                  </p>
                </div>
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
                  <span
                    class="rounded px-1.5 py-0.5 text-[8.5px] font-semibold"
                    :class="aiDraftConfidenceClass"
                    :title="`AI 自评置信度 ${Math.round((aiDraft.confidence ?? 0) * 100)}%；待补项越多置信度越低`"
                  >
                    把握 {{ Math.round((aiDraft.confidence ?? 0) * 100) }}%
                  </span>
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
                  <p class="text-[9px] leading-relaxed text-amber-700">
                    保存时仍未补的项会写入用例描述备注（「待补充：…」），方便后续追溯。
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

          <div
            ref="drawerBody"
            class="flex-1 space-y-6 px-5 py-5"
            :class="noScroll ? '' : 'overflow-y-auto'"
          >
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
              <h3
                class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                :class="collapsible ? 'flex cursor-pointer select-none items-center gap-2' : ''"
                @click="toggleSection('basic')"
              >
                基础信息
                <span v-if="collapsible" class="section-summary">{{ basicSummary }}</span>
                <span v-if="isSectionDirty('basic')" class="section-dirty" title="有未保存修改">●</span>
                <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('basic') ? '⌄' : '⌃' }}</span>
              </h3>
              <div v-show="!isSectionCollapsed('basic')" class="space-y-3">
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
                <div v-if="hideStatus">
                  <label class="label">用例类型</label>
                  <input class="input" :value="CASE_TYPE_LABELS[form.case_type] ?? form.case_type" disabled />
                </div>
                <div v-else>
                  <label class="label">状态</label>
                  <select v-model="form.status" class="input">
                    <SelectButton />
                    <option value="ACTIVE">启用</option>
                    <option value="DRAFT">草稿</option>
                    <option value="DISABLED">停用</option>
                  </select>
                </div>
                <div>
                  <label class="label">优先级</label>
                  <select v-model="form.priority" class="input" :class="{ 'ai-fill': aiHighlight.has('priority') }">
                    <SelectButton />
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
              <div v-if="!hideEnvironment" class="sm:max-w-[420px]">
                <label class="label">执行环境</label>
                <select v-model="form.environment_id" class="input">
                  <SelectButton />
                  <option :value="null">未绑定</option>
                  <option v-for="env in environments" :key="env.id" :value="env.id">
                    {{ env.name }}（{{ env.code }}）
                  </option>
                </select>
              </div>
              </div>
            </section>

            <section id="ai-field-requestVariables" tabindex="-1" class="space-y-3">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h3
                  class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                  :class="collapsible ? 'flex flex-1 cursor-pointer select-none items-center gap-2' : ''"
                  @click="toggleSection('variables')"
                >
                  请求变量
                  <span v-if="collapsible" class="section-summary">{{ variablesSummary }}</span>
                  <span v-if="isSectionDirty('variables')" class="section-dirty" title="有未保存修改">●</span>
                  <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('variables') ? '⌄' : '⌃' }}</span>
                </h3>
                <div v-if="!isSectionCollapsed('variables')" class="flex items-center gap-1">
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
              <div v-show="!isSectionCollapsed('variables')" class="space-y-3">
              <p class="text-[11px] text-slate-500">
                渲染进环境请求头 / 请求体模板；所有数字（含 JSON 内嵌套数字）均按原文保存为字符串，避免长数字丢精度或变成科学计数法。优先级：运行时 &gt; 用例变量 &gt;
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
                  query / session_id / history / turn / timestamp_iso 由运行时自动注入，无需在此填写。
                </p>
                <p v-if="missingEnvPlaceholders.length" class="mt-1 text-amber-700">
                  尚未提供取值：{{ missingEnvPlaceholders.join('、') }}
                </p>
              </div>
              </div>
            </section>

            <section class="space-y-3">
              <h3
                class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                :class="collapsible ? 'flex cursor-pointer select-none items-center gap-2' : ''"
                @click="toggleSection('type')"
              >
                用例类型
                <span v-if="collapsible" class="section-summary">{{ CASE_TYPE_LABELS[form.case_type] ?? form.case_type }}</span>
                <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('type') ? '⌄' : '⌃' }}</span>
              </h3>
              <div v-show="!isSectionCollapsed('type')" class="space-y-3">
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
              </div>
            </section>

            <section v-if="form.case_type === 'SINGLE'" class="space-y-3">
              <h3
                class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                :class="collapsible ? 'flex cursor-pointer select-none items-center gap-2' : ''"
                @click="toggleSection('content')"
              >
                单轮配置
                <span v-if="collapsible" class="section-summary">{{ contentSummary }}</span>
                <span v-if="isSectionDirty('content')" class="section-dirty" title="有未保存修改">●</span>
                <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('content') ? '⌄' : '⌃' }}</span>
              </h3>
              <div v-show="!isSectionCollapsed('content')" class="space-y-3">
              <div>
                <label class="label">用户提问（query）</label>
                <textarea v-model="form.query" class="input" :class="{ 'ai-fill': aiHighlight.has('query') }" rows="2" placeholder="帮我查一下订单 A2024001 的含税金额和明细笔数" />
              </div>
              <div>
                <label class="label">预期答案</label>
                <textarea v-model="form.expected_answer" class="input" :class="{ 'ai-fill': aiHighlight.has('expected_answer') }" rows="2" placeholder="订单 A2024001 含税金额为 ¥12,345.67，共 3 笔明细" />
              </div>
              </div>
            </section>

            <section v-else-if="form.case_type === 'STATIC_MULTI'" id="ai-field-turns" tabindex="-1" class="space-y-3">
              <div class="flex items-center justify-between">
                <h3
                  class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                  :class="collapsible ? 'flex flex-1 cursor-pointer select-none items-center gap-2' : ''"
                  @click="toggleSection('content')"
                >
                  静态多轮脚本
                  <span v-if="collapsible" class="section-summary">{{ contentSummary }}</span>
                <span v-if="isSectionDirty('content')" class="section-dirty" title="有未保存修改">●</span>
                  <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('content') ? '⌄' : '⌃' }}</span>
                </h3>
                <button v-if="!isSectionCollapsed('content')" class="btn-ghost btn-xs" @click="addTurn">添加一轮</button>
              </div>
              <div v-show="!isSectionCollapsed('content')" class="space-y-3">
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
                    @click="removeTurn(index)"
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
              </div>
            </section>

            <section v-else class="space-y-3">
              <h3
                class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                :class="collapsible ? 'flex cursor-pointer select-none items-center gap-2' : ''"
                @click="toggleSection('content')"
              >
                动态多轮配置
                <span v-if="collapsible" class="section-summary">{{ contentSummary }}</span>
                <span v-if="isSectionDirty('content')" class="section-dirty" title="有未保存修改">●</span>
                <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('content') ? '⌄' : '⌃' }}</span>
              </h3>
              <div v-show="!isSectionCollapsed('content')" class="space-y-3">
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
              <div
                id="ai-field-hidden"
                tabindex="-1"
                class="flex items-center justify-between"
              >
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
                <button class="btn-danger btn-xs" @click="removeHiddenRow(index)">删除</button>
              </div>
              </div>
            </section>

            <section class="space-y-3">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h3
                  class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                  :class="collapsible ? 'flex flex-1 cursor-pointer select-none items-center gap-2' : ''"
                  @click="toggleSection('grading')"
                >
                  判分方式
                  <span v-if="collapsible" class="section-summary">{{ gradingSummary }}</span>
                  <span v-if="isSectionDirty('grading')" class="section-dirty" title="有未保存修改">●</span>
                  <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('grading') ? '⌄' : '⌃' }}</span>
                </h3>
                <div v-if="!isSectionCollapsed('grading')" class="flex gap-2">
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
              <div v-show="!isSectionCollapsed('grading')" class="space-y-3">
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
                      <SelectButton />
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
                      <SelectButton />
                      <option value="lenient">宽松（有据扩展中性）</option>
                      <option value="strict">严格（无据扩展即失败）</option>
                    </select>
                  </div>
                </div>

                <div class="grid gap-3 sm:grid-cols-3">
                  <div>
                    <label class="label">风险等级</label>
                    <select v-model="form.riskLevel" class="input" :class="{ 'ai-fill': aiHighlight.has('riskLevel') }">
                      <SelectButton />
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
                        <SelectButton />
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
                          <SelectButton />
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
                      id="ai-field-mustNotText"
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

                <div v-show="contractTab === 'contract'" id="ai-field-contract" tabindex="-1" class="space-y-3">
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

                <div v-show="contractTab === 'plan'" id="ai-field-plan" tabindex="-1" class="space-y-3">
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
              </div>
            </section>

            <section v-if="form.case_type !== 'STATIC_MULTI' && form.gradingMode !== 'ANSWER_CONTRACT'" id="ai-field-rules" tabindex="-1" class="space-y-3">
              <div class="flex items-center justify-between">
                <h3
                  class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                  :class="collapsible ? 'flex flex-1 cursor-pointer select-none items-center gap-2' : ''"
                  @click="toggleSection('rules')"
                >
                  规则硬校验
                  <span v-if="collapsible" class="section-summary">{{ rulesSummary }}</span>
                  <span v-if="isSectionDirty('rules')" class="section-dirty" title="有未保存修改">●</span>
                  <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('rules') ? '⌄' : '⌃' }}</span>
                </h3>
                <div v-if="!isSectionCollapsed('rules')" class="flex gap-2">
                  <button class="btn-ghost btn-xs" @click="addAmount">添加金额</button>
                  <button class="btn-ghost btn-xs" @click="addCount">添加笔数</button>
                </div>
              </div>
              <div v-show="!isSectionCollapsed('rules')" class="space-y-3">
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
                  <button class="btn-danger btn-xs shrink-0" @click="removeAmountRule(index)">
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
                  <button class="btn-danger btn-xs shrink-0" @click="removeCountRule(index)">
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
              </div>
            </section>

            <section class="space-y-3">
              <h3
                class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                :class="collapsible ? 'flex cursor-pointer select-none items-center gap-2' : ''"
                @click="toggleSection('prompt')"
              >
                Prompt 与模型
                <span v-if="collapsible" class="section-summary">{{ promptSummary }}</span>
                <span v-if="isSectionDirty('prompt')" class="section-dirty" title="有未保存修改">●</span>
                <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('prompt') ? '⌄' : '⌃' }}</span>
              </h3>
              <div v-show="!isSectionCollapsed('prompt')" class="space-y-3">
              <div class="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <label class="label">用户模拟器模板（USER_SIMULATOR）</label>
                <div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_130px_130px]">
                  <select v-model="form.simulator_template_id" class="input">
                    <SelectButton />
                    <option :value="null">未指定</option>
                    <option v-for="item in simulatorTemplates" :key="item.id" :value="item.id">
                      {{ item.name }} · v{{ item.current_version }}
                    </option>
                  </select>
                  <select v-model="form.simulatorStrategy" class="input">
                    <SelectButton />
                    <option value="LATEST">跟随最新</option>
                    <option value="PINNED">锁定版本</option>
                  </select>
                  <select
                    v-if="form.simulatorStrategy === 'PINNED'"
                    v-model="form.simulatorVersion"
                    class="input"
                  >
                    <SelectButton />
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
                    <SelectButton />
                    <option :value="null">未指定</option>
                    <option v-for="item in judgeTemplates" :key="item.id" :value="item.id">
                      {{ item.name }} · v{{ item.current_version }}
                    </option>
                  </select>
                  <select v-model="form.judgeTemplateStrategy" class="input">
                    <SelectButton />
                    <option value="LATEST">跟随最新</option>
                    <option value="PINNED">锁定版本</option>
                  </select>
                  <select v-if="form.judgeTemplateStrategy === 'PINNED'" v-model="form.judgeVersion" class="input">
                    <SelectButton />
                    <option :value="null">选择版本</option>
                    <option v-for="version in judgeVersions" :key="version" :value="version">
                      v{{ version }}
                    </option>
                  </select>
                </div>
              </div>

              <div class="grid gap-3 sm:grid-cols-2">
                <div>
                  <label class="label">裁判策略</label>
                  <select v-model="form.judgeStrategy" class="input">
                    <SelectButton />
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
              </div>
            </section>

            <section v-if="isEdit" class="space-y-3">
              <h3
                class="text-xs font-semibold uppercase tracking-wider text-indigo-600"
                :class="collapsible ? 'flex cursor-pointer select-none items-center gap-2' : ''"
                @click="toggleSection('summary')"
              >
                变更说明
                <span v-if="collapsible" class="section-summary">保存新版本时写入版本记录</span>
                <span v-if="isSectionDirty('summary')" class="section-dirty" title="有未保存修改">●</span>
                <span v-if="collapsible" class="section-chev">{{ isSectionCollapsed('summary') ? '⌄' : '⌃' }}</span>
              </h3>
              <div v-show="!isSectionCollapsed('summary')">
                <input v-model="form.change_summary" class="input" placeholder="例如：调整预期金额与裁判阈值" />
              </div>
            </section>
          </div>

          <footer
            v-if="!hideFooter"
            class="flex items-center justify-between gap-2 border-t border-slate-200 px-5 py-3"
          >
            <p class="text-[11px] text-slate-500">
              {{
                embedded
                  ? '试运行使用当前表单内容生成快照，不产生版本'
                  : isEdit
                    ? '保存会生成新版本快照'
                    : '创建后版本号为 v1'
              }}
            </p>
            <div class="flex gap-2">
              <button class="btn-ghost" @click="close">
                {{ embedded ? '放弃修改' : '取消' }}
              </button>
              <button
                v-if="embedded"
                class="btn-ghost !border !border-line !bg-white"
                :disabled="saving"
                @click="submit"
              >
                {{ saving ? '保存中…' : '保存为新版本' }}
              </button>
              <button
                v-if="embedded"
                class="btn-primary"
                :disabled="running || saving"
                @click="tryRun"
              >
                {{ running ? '试运行中…' : '▶ 试运行（不保存）' }}
              </button>
              <button v-else class="btn-primary" :disabled="saving" @click="submit">
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

/* 调试工作台内嵌：分组折叠摘要 */
.section-summary {
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  font-weight: 400;
  letter-spacing: 0;
  text-transform: none;
  color: #94a3b8;
}
.section-chev {
  flex: none;
  font-size: 10px;
  color: #94a3b8;
}
.section-dirty {
  flex: none;
  font-size: 8px;
  line-height: 1;
  color: #f59e0b;
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
