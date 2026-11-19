<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import EmptyState from '../../components/EmptyState.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import StatusPill from '../../components/StatusPill.vue'
import type { ExecutionDetail, JudgeResult, SelfCheckResult, TurnTrace } from '../../types'
import {
  formatDateTime,
  formatMs,
  formatNumber,
  formatScore,
  safeJson,
  sourceLabel,
  statusLabel,
} from '../../utils/format'
import { copyText } from '../../utils/clipboard'
import { useToastStore } from '../../stores/toast'

const props = defineProps<{
  detail: ExecutionDetail | null
  loading: boolean
  /** 初始展示页签（调试完成跳转带 tab=selfcheck 时直接落在「追问自检」） */
  initialTab?: string | null
}>()

const toast = useToastStore()
const router = useRouter()

/** 从执行详情直接跳转到该用例详情，复用用例资产的完整上下文。 */
function openCaseDetail(): void {
  if (!props.detail?.case_id) return
  void router.push({ name: 'case-detail', params: { id: String(props.detail.case_id) } })
}

type TabKey = 'turns' | 'judges' | 'contract' | 'rules' | 'selfcheck' | 'snapshot' | 'raw'
const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'turns', label: '对话轮次' },
  { key: 'judges', label: '裁判明细' },
  { key: 'contract', label: '契约判分' },
  { key: 'rules', label: '规则校验' },
  { key: 'selfcheck', label: '追问自检' },
  { key: 'snapshot', label: '用例快照' },
  { key: 'raw', label: '原始报文' },
]
const activeTab = ref<TabKey>('turns')

// 支持从路由参数指定初始页签（如调试完成带 tab=selfcheck）
watch(
  () => props.initialTab,
  (value) => {
    if (value && TABS.some((tab) => tab.key === value)) {
      activeTab.value = value as TabKey
    }
  },
  { immediate: true },
)

interface RuleCheckItem {
  type?: string
  label?: string
  expected?: unknown
  actual?: unknown
  passed?: boolean
  evidence?: string
  mode?: string
  bound_name?: string | null
  reason?: string
}

const BINDING_MODE_LABEL: Record<string, string> = {
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

function bindingModeLabel(mode?: string | null): string {
  if (!mode) return ''
  return BINDING_MODE_LABEL[mode] ?? mode
}

const DIMENSION_MAX: Record<string, number> = {
  data_accuracy: 40,
  logic_consistency: 30,
  goal_completion: 20,
  tone_clarity: 10,
}

const DIMENSION_LABEL: Record<string, string> = {
  data_accuracy: '数据准确性',
  logic_consistency: '逻辑一致性',
  goal_completion: '目标完成度',
  tone_clarity: '语气清晰度',
}

const ROOT_CAUSE_LABEL: Record<string, string> = {
  HALLUCINATION: '答案幻觉',
  RETRIEVAL: '取数缺失',
  SYNTHESIS: '汇总错误',
  BEHAVIOR: '交互行为不当',
  UNKNOWN: '未定位',
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

const SCORECARD_WEIGHT: Record<string, string> = {
  fact_completeness: '50%',
  assumption_honesty: '20%',
  clarity: '20%',
  helpful_extra: '10%',
}

const BEHAVIOR_LABEL: Record<string, string> = {
  answer: '直接作答',
  clarify: '澄清追问',
  assumption: '声明假设后作答',
  partial: '部分披露',
  refuse: '拒绝并说明',
}

const EXTRA_KIND_LABEL: Record<string, string> = {
  grounded: '有据扩展',
  unsupported: '无据扩展',
  off_topic: '跑题',
  may: '允许项',
}

const FACT_KIND_LABEL: Record<string, string> = {
  scalar: '数值',
  date: '日期',
  set: '集合',
  topk: 'TopK',
  boolean: '布尔',
  text: '文本',
  semantic: '语义',
}

interface ContractFactItem {
  fact_id?: string
  kind?: string
  label?: string | null
  expected?: unknown
  actual?: unknown
  passed?: boolean
  reason?: string
  is_gate?: boolean
  mode?: string
  bound_name?: string | null
  conflict_name?: string | null
  degraded?: boolean
  evidence_ref?: string | null
}

interface ContractBehaviorItem {
  detected?: string
  allowed?: boolean
  stated_assumption?: boolean
  matched_scenario?: string | null
  missing_slots?: string[]
  violations?: string[]
  detail?: string
}

interface ContractExtraItem {
  text?: string
  kind?: string
  evidence_ref?: string | null
}

const ruleBlock = computed(() => {
  const raw = props.detail?.rule_result
  if (!raw || typeof raw !== 'object') return null
  return raw as { passed?: boolean; checks?: RuleCheckItem[]; extracted?: Record<string, unknown> }
})

const ruleChecks = computed<RuleCheckItem[]>(() => ruleBlock.value?.checks ?? [])

const aggregate = computed(() => {
  const raw = props.detail?.judge_result
  if (!raw || typeof raw !== 'object') return null
  return raw as Record<string, unknown>
})

const policyNotes = computed<string[]>(() => {
  const notes = aggregate.value?.policy_notes
  return Array.isArray(notes) ? notes.map(String) : []
})

const dims = computed(() => {
  const raw = aggregate.value?.dimension_scores
  if (!raw || typeof raw !== 'object') return [] as Array<{ key: string; label: string; value: number }>
  return Object.entries(raw as Record<string, number>).map(([key, value]) => ({
    key,
    label: DIMENSION_LABEL[key] ?? key,
    value,
  }))
})

const contractGrading = computed<Record<string, unknown> | null>(() => {
  const raw = props.detail?.answer_grading
  if (!raw || typeof raw !== 'object') return null
  return raw as Record<string, unknown>
})

const contractGates = computed(() => {
  const raw = contractGrading.value?.gates
  if (!raw || typeof raw !== 'object') return [] as Array<{ key: string; label: string; passed: boolean }>
  return Object.entries(raw as Record<string, boolean>).map(([key, passed]) => ({
    key,
    label: GATE_LABEL[key] ?? key,
    passed: Boolean(passed),
  }))
})

const contractScorecard = computed(() => {
  const raw = contractGrading.value?.scorecard
  if (!raw || typeof raw !== 'object') {
    return [] as Array<{ key: string; label: string; value: number }>
  }
  return Object.entries(raw as Record<string, number>).map(([key, value]) => ({
    key,
    label: SCORECARD_LABEL[key] ?? key,
    value,
  }))
})

const contractFacts = computed<ContractFactItem[]>(() => {
  const raw = props.detail?.fact_results
  if (Array.isArray(raw)) return raw as ContractFactItem[]
  const nested = contractGrading.value?.facts
  return Array.isArray(nested) ? (nested as ContractFactItem[]) : []
})

const contractDiagnostics = computed<Record<string, unknown> | null>(() => {
  const raw = props.detail?.diagnostics
  if (!raw || typeof raw !== 'object') return null
  return raw as Record<string, unknown>
})

const missingCapabilities = computed<string[]>(() => {
  const raw = contractDiagnostics.value?.missing_capabilities
  return Array.isArray(raw) ? raw.map(String) : []
})

const contractBehavior = computed<ContractBehaviorItem | null>(() => {
  const raw = contractGrading.value?.behavior
  if (!raw || typeof raw !== 'object') return null
  return raw as ContractBehaviorItem
})

const contractExtras = computed<ContractExtraItem[]>(() => {
  const raw = contractGrading.value?.extras
  return Array.isArray(raw) ? (raw as ContractExtraItem[]) : []
})

const contractNotes = computed<string[]>(() => {
  const raw = contractGrading.value?.notes
  return Array.isArray(raw) ? raw.map(String) : []
})

const contractQuality = computed<{ total_score?: number; dimension_scores?: Record<string, number> } | null>(() => {
  const raw = aggregate.value?.quality
  if (!raw || typeof raw !== 'object') return null
  return raw as { total_score?: number; dimension_scores?: Record<string, number> }
})

const contractTotalScore = computed<number | null>(() => {
  const raw = contractGrading.value?.total_score
  return typeof raw === 'number' ? raw : null
})

const contractQualityDims = computed(() => {
  const raw = contractQuality.value?.dimension_scores
  if (!raw || typeof raw !== 'object') {
    return [] as Array<{ key: string; label: string; value: number }>
  }
  return Object.entries(raw).map(([key, value]) => ({
    key,
    label: DIMENSION_LABEL[key] ?? key,
    value,
  }))
})

const failedGates = computed(() => contractGates.value.filter((gate) => !gate.passed))

const gateSummary = computed(() => {
  const total = contractGates.value.length
  if (!total) return ''
  const passed = total - failedGates.value.length
  return `${passed}/${total}`
})

const failedFacts = computed(() =>
  contractFacts.value.filter((fact) => fact.passed === false),
)

/** 失败追问自检记录（任务未开启时为 null，页签隐藏） */
const selfCheck = computed<SelfCheckResult | null>(() => props.detail?.self_check ?? null)

const SELF_CHECK_STATUS_LABELS: Record<string, string> = {
  DONE: '已追问',
  SKIPPED: '未触发',
  ERROR: '追问失败',
}

const selfCheckStatusLabel = computed(() => {
  const raw = selfCheck.value?.status
  return raw ? (SELF_CHECK_STATUS_LABELS[raw] ?? raw) : null
})

const selfCheckStatusClass = computed(() => {
  const status = selfCheck.value?.status
  if (status === 'DONE') return 'bg-emerald-50 text-emerald-700 border-emerald-200'
  if (status === 'ERROR') return 'bg-rose-50 text-rose-700 border-rose-200'
  return 'bg-slate-100 text-slate-600 border-slate-200'
})

/** 自检结论 / 跳过原因 / 错误信息的统一展示文本 */
const selfCheckBody = computed(() => {
  const record = selfCheck.value
  if (!record) return null
  if (record.status === 'DONE') return record.conclusion || '（Agent 未给出自检结论）'
  return record.skip_reason || record.error || null
})

const selfCheckPromptOpen = ref(false)

watch(
  () => props.detail?.id,
  () => {
    selfCheckPromptOpen.value = false
  },
)

const visibleTabs = computed(() =>
  TABS.filter((tab) => {
    // 契约判分与规则硬校验互斥：契约模式只看契约判分，4 维评分模式只看规则校验。
    if (tab.key === 'contract') return contractGrading.value !== null
    if (tab.key === 'rules') return props.detail?.grading_mode !== 'ANSWER_CONTRACT'
    // 追问自检仅在任务开启自检且留下记录时展示。
    if (tab.key === 'selfcheck') return selfCheck.value !== null
    return true
  }),
)

const rootCauseText = computed(() => {
  const raw = props.detail?.root_cause
  if (!raw) return null
  return ROOT_CAUSE_LABEL[raw] ?? raw
})

/** 逐轮折叠：默认展开，点击轮次头折叠 / 展开整轮内容 */
const collapsedTurnIds = ref<Set<number>>(new Set())

function isTurnCollapsed(turnId: number): boolean {
  return collapsedTurnIds.value.has(turnId)
}

function toggleTurn(turnId: number): void {
  const next = new Set(collapsedTurnIds.value)
  if (next.has(turnId)) next.delete(turnId)
  else next.add(turnId)
  collapsedTurnIds.value = next
}

const allTurnsCollapsed = computed(() => {
  const turns = props.detail?.turns ?? []
  return turns.length > 0 && turns.every((turn) => collapsedTurnIds.value.has(turn.id))
})

/** 全部折叠 / 全部展开 */
function toggleAllTurns(): void {
  const turns = props.detail?.turns ?? []
  collapsedTurnIds.value = allTurnsCollapsed.value
    ? new Set()
    : new Set(turns.map((turn) => turn.id))
}

watch(
  () => props.detail?.id,
  () => {
    // 初始页签优先（调试完成带 tab=selfcheck）；否则沿用原有行为回到对话轮次
    const initial = props.initialTab
    activeTab.value =
      initial && TABS.some((tab) => tab.key === initial) ? (initial as TabKey) : 'turns'
    collapsedTurnIds.value = new Set()
  },
)

function turnTone(turn: TurnTrace): string {
  if (turn.input_source === 'SIMULATOR') return 'border-fuchsia-500/40 text-fuchsia-700'
  if (turn.input_source === 'STATIC') return 'border-sky-200 text-sky-700'
  return 'border-indigo-200 text-indigo-600'
}

function ruleCheckLabel(check: RuleCheckItem): string {
  if (check.label) return check.label
  if (check.type === 'must_contain') return '必须包含'
  if (check.type === 'must_not_contain') return '禁止包含'
  return check.type ?? '校验项'
}

function valueText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return safeJson(value, 0)
  return String(value)
}

function filled(value: unknown): boolean {
  return value !== null && value !== undefined && value !== ''
}

function factKindLabel(kind?: string | null): string {
  if (!kind) return '—'
  return FACT_KIND_LABEL[kind] ?? kind
}

function behaviorLabel(detected?: string | null): string {
  if (!detected) return '—'
  return BEHAVIOR_LABEL[detected] ?? detected
}

function extraKindLabel(kind?: string | null): string {
  if (!kind) return '未知'
  return EXTRA_KIND_LABEL[kind] ?? kind
}

/** 把判分结果里的 expected 结构翻译成业务可读文本。 */
function factExpectedText(fact: ContractFactItem): string {
  const expected = fact.expected
  if (expected === null || expected === undefined) return '—'
  if (typeof expected !== 'object') return String(expected)
  const obj = expected as Record<string, unknown>
  const kind = fact.kind ?? ''
  const unit = filled(obj.unit) ? ` ${String(obj.unit)}` : ''
  if (kind === 'scalar') {
    if (!filled(obj.value)) return '—'
    const tolerance =
      typeof obj.tolerance === 'number' && obj.tolerance > 0
        ? `（容差 ±${obj.tolerance}）`
        : ''
    return `${String(obj.value)}${unit}${tolerance}`
  }
  if (kind === 'set' || kind === 'topk') {
    const values = Array.isArray(obj.values) ? obj.values.map(String) : []
    if (values.length) return `候选：${values.join(' / ')}`
    return filled(obj.value) ? String(obj.value) : '—'
  }
  if (kind === 'date') {
    return filled(obj.value) ? String(obj.value) : '—'
  }
  if (kind === 'boolean') {
    return obj.value === false ? '不应出现' : '应出现'
  }
  const keywords = Array.isArray(obj.keywords) ? obj.keywords.map(String).filter(Boolean) : []
  if (keywords.length) return `包含：${keywords.join(' / ')}`
  if (kind === 'text' || kind === 'semantic') {
    if (filled(obj.value)) return String(obj.value)
    return obj.value === false ? '不应出现' : '应出现'
  }
  return valueText(expected)
}

/** 把判分结果里的 actual 结构翻译成业务可读文本。 */
function factActualText(fact: ContractFactItem): string {
  const actual = fact.actual
  if (actual === null || actual === undefined) return '未命中'
  if (typeof actual !== 'object') return String(actual)
  const obj = actual as Record<string, unknown>
  if (filled(obj.bound_value)) {
    const name = filled(obj.bound_name) ? `（${String(obj.bound_name)}）` : ''
    return `${String(obj.bound_value)}${name}`
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
  if (filled(obj.value)) return String(obj.value)
  return valueText(actual)
}

function judgeDimensions(judge: JudgeResult): Array<{ key: string; label: string; value: number; max: number }> {
  return Object.entries(judge.dimension_scores ?? {}).map(([key, value]) => ({
    key,
    label: DIMENSION_LABEL[key] ?? key,
    value,
    max: DIMENSION_MAX[key] ?? 10,
  }))
}

async function copyTraceUid(): Promise<void> {
  if (!props.detail) return
  const ok = await copyText(props.detail.trace_uid)
  if (ok) toast.success('Trace UID 已复制')
  else toast.error('复制失败')
}

// ---- 多轮会话绑定：执行期会话链与用例变量来源 ----
const caseSnapshotExtra = computed(() => {
  const snap = (props.detail?.case_snapshot ?? {}) as Record<string, unknown>
  const requestVariables = (snap.request_variables ?? {}) as Record<string, unknown>
  return {
    mode: typeof snap.session_mode === 'string' ? snap.session_mode : 'INHERIT',
    requestVariables,
  }
})

const sessionModeText = computed(() => {
  switch (caseSnapshotExtra.value.mode) {
    case 'CLIENT_GENERATED':
      return '客户端生成'
    case 'SERVER_RETURNED':
      return '服务端返回'
    case 'INHERIT':
    default:
      return '跟随环境'
  }
})

const sessionChain = computed(() => {
  const ids = (props.detail?.turns ?? [])
    .map((turn) => turn.session_id)
    .filter((value): value is string => Boolean(value))
  const unique = [...new Set(ids)]
  return { ids, unique, consistent: unique.length <= 1 }
})

function shortSession(value: string | null): string {
  if (!value) return '—'
  return value.length > 18 ? `${value.slice(0, 16)}…` : value
}

function variableText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  return typeof value === 'string' ? value : safeJson(value, 0)
}

/** 本轮请求报文中实际携带的用例级变量 key（body 或 header）。 */
function turnRequestKeys(turn: TurnTrace): string[] {
  const payload = turn.request_payload as Record<string, unknown> | null
  if (!payload || typeof payload !== 'object') return []
  const body = (payload.body ?? {}) as Record<string, unknown>
  const headers = (payload.headers ?? {}) as Record<string, unknown>
  // 字段名由模板决定（如 tenant_id 可能落在 X-Tenant-Id），按归一化名称匹配
  const normalize = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '')
  const payloadKeys = [...Object.keys(body), ...Object.keys(headers)].map(normalize)
  return Object.keys(caseSnapshotExtra.value.requestVariables).filter((key) => {
    const target = normalize(key)
    return target.length >= 3 && payloadKeys.some((item) => item.endsWith(target))
  })
}
</script>

<template>
  <section class="card flex min-h-[520px] flex-col overflow-hidden">
    <div v-if="loading" class="py-24 text-center text-sm text-slate-500">加载执行详情…</div>
    <EmptyState
      v-else-if="!detail"
      title="选择一条执行记录"
      description="左侧列表支持按状态与来源筛选，点击任意记录查看全链路 Trace。"
    />
    <template v-else>
      <header class="border-b border-slate-200 px-5 py-4">
        <div class="flex flex-wrap items-start gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="truncate text-base font-semibold text-slate-900">
                <button
                  type="button"
                  class="inline-flex max-w-full items-center gap-1 truncate rounded-sm text-left underline decoration-dotted underline-offset-2 transition hover:text-indigo-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
                  title="查看用例详情"
                  @click="openCaseDetail"
                >
                  <span class="truncate">{{ detail.case_code || `用例 #${detail.case_id}` }}</span>
                  <svg
                    class="h-3.5 w-3.5 shrink-0 text-slate-400"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.8"
                    aria-hidden="true"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" d="M7 17 17 7M9 7h8v8" />
                  </svg>
                </button>
              </h2>
              <StatusPill :status="detail.status" />
              <span class="tag">v{{ detail.case_version }}</span>
              <span class="tag">{{ sourceLabel(detail.source) }}</span>
              <span v-if="detail.attempt_no > 1" class="tag border-amber-200 text-amber-700">
                第 {{ detail.attempt_no }} 次尝试
              </span>
              <span v-if="detail.error_kind === 'ENV'" class="tag border-amber-200 text-amber-700">
                环境异常
              </span>
              <span v-else-if="detail.error_kind === 'LLM'" class="tag border-sky-200 text-sky-700">
                模型异常
              </span>
              <span
                v-if="detail.grading_mode === 'ANSWER_CONTRACT'"
                class="tag border-indigo-200 text-indigo-700"
              >
                答案契约
              </span>
              <span v-if="detail.lucky_pass" class="tag border-amber-300 text-amber-700">
                幸运通过（无取数证据）
              </span>
              <span v-if="rootCauseText" class="tag border-rose-200 text-rose-700">
                根因：{{ rootCauseText }}
              </span>
              <span v-if="detail.grader_version" class="tag">{{ detail.grader_version }}</span>
            </div>
            <p class="mt-1 truncate text-xs text-slate-500">{{ detail.case_name || '—' }}</p>
            <button
              class="mt-1 inline-flex items-center gap-1 font-mono text-[10px] text-slate-500 transition hover:text-slate-700"
              @click="copyTraceUid"
            >
              trace: {{ detail.trace_uid }}
              <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path stroke-linecap="round" stroke-linejoin="round" d="M8 8V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-3M5 8h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2Z" />
              </svg>
            </button>
          </div>
        </div>

        <div class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">总分</p>
            <p class="text-lg font-semibold tabular-nums text-indigo-600">{{ formatScore(detail.total_score) }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">轮次</p>
            <p class="text-lg font-semibold tabular-nums text-slate-900">{{ detail.turns_used }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">总时延</p>
            <p class="text-sm font-semibold tabular-nums text-slate-800">{{ formatMs(detail.latency_ms) }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">首字时延</p>
            <p class="text-sm font-semibold tabular-nums text-slate-800">{{ formatMs(detail.ttft_ms) }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">Tokens</p>
            <p class="text-sm font-semibold tabular-nums text-slate-800">
              {{ formatNumber(detail.simulator_tokens + detail.judge_tokens) }}
            </p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">终止状态</p>
            <p class="text-xs font-medium text-slate-700">{{ statusLabel(detail.termination_status) }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">开始时间</p>
            <p class="text-[11px] text-slate-700">{{ formatDateTime(detail.started_at) }}</p>
          </div>
        </div>

        <p
          v-if="detail.error_message"
          class="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
        >
          {{ detail.error_message }}
        </p>
        <div v-if="policyNotes.length" class="mt-3 space-y-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
          <p v-for="note in policyNotes" :key="note" class="text-xs text-amber-700">· {{ note }}</p>
        </div>
      </header>

      <div class="flex flex-wrap gap-1 border-b border-slate-200 px-4 py-2">
        <button
          v-for="tab in visibleTabs"
          :key="tab.key"
          class="tab-btn"
          :class="activeTab === tab.key ? 'tab-btn-active' : ''"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
          <span v-if="tab.key === 'turns'" class="ml-1 text-[10px] text-slate-600">{{ detail.turns.length }}</span>
          <span v-if="tab.key === 'judges'" class="ml-1 text-[10px] text-slate-600">{{ detail.judges.length }}</span>
          <span v-if="tab.key === 'contract'" class="ml-1 text-[10px] text-slate-600">{{ gateSummary }}</span>
        </button>
      </div>

      <div class="flex-1 overflow-y-auto px-4 py-4">
        <template v-if="activeTab === 'turns'">
          <div v-if="detail.turns.length === 0">
            <EmptyState compact title="没有对话轮次" description="该执行未产生有效对话。" />
          </div>
          <div v-else class="space-y-3">
            <div class="rounded-xl border border-indigo-100 bg-indigo-50/60 px-3 py-2">
              <div class="flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
                <span class="font-medium text-slate-800">会话绑定</span>
                <span
                  class="rounded-full border border-indigo-100 bg-white px-2 py-0.5 font-mono text-[10px] text-indigo-600"
                >
                  {{ sessionModeText }}
                </span>
                <span
                  v-if="sessionChain.unique.length"
                  class="rounded-full border border-slate-200 bg-white px-2 py-0.5 font-mono text-[10px] text-slate-600"
                  :title="sessionChain.ids.join('\n')"
                >
                  {{ shortSession(sessionChain.unique[0]) }}
                </span>
                <span
                  v-if="sessionChain.unique.length > 1"
                  class="rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700"
                >
                  ⚠ 检测到 {{ sessionChain.unique.length }} 个不同会话值，上下文可能未延续
                </span>
                <span
                  v-else-if="sessionChain.unique.length === 1"
                  class="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-700"
                >
                  各轮会话一致
                </span>
                <span v-else class="text-[10px] text-slate-500">
                  未携带会话值（服务端新建会话）
                </span>
              </div>
              <div
                v-if="Object.keys(caseSnapshotExtra.requestVariables).length"
                class="mt-1 flex flex-wrap items-center gap-1"
              >
                <span class="text-[10px] text-slate-500">用例变量：</span>
                <span
                  v-for="(value, key) in caseSnapshotExtra.requestVariables"
                  :key="key"
                  class="rounded-md border border-indigo-100 bg-white px-1.5 py-0.5 font-mono text-[10px] text-indigo-600"
                >
                  {{ key }}={{ variableText(value) }}
                </span>
              </div>
            </div>
            <div class="flex items-center justify-between">
              <p class="text-[11px] text-slate-500">共 {{ detail.turns.length }} 轮</p>
              <button class="btn-ghost btn-xs" @click="toggleAllTurns">
                {{ allTurnsCollapsed ? '全部展开' : '全部折叠' }}
              </button>
            </div>
            <article
              v-for="turn in detail.turns"
              :key="turn.id"
              class="rounded-xl border border-slate-200 bg-slate-50"
            >
              <header
                class="flex cursor-pointer select-none flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-3 py-2"
                :aria-expanded="!isTurnCollapsed(turn.id)"
                @click="toggleTurn(turn.id)"
              >
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-mono text-xs font-semibold text-indigo-600">T{{ turn.turn_index }}</span>
                  <span class="rounded-full border px-2 py-0.5 text-[10px]" :class="turnTone(turn)">
                    {{ sourceLabel(turn.input_source) }}
                  </span>
                  <span v-if="turn.simulator_status" class="tag">{{ statusLabel(turn.simulator_status) }}</span>
                  <span v-if="turn.intent" class="tag">意图：{{ turn.intent }}</span>
                  <span
                    v-if="turn.session_id"
                    class="rounded-full border border-indigo-100 bg-indigo-50 px-2 py-0.5 font-mono text-[10px] text-indigo-600"
                    :title="turn.session_id"
                  >
                    会话 {{ shortSession(turn.session_id) }}
                  </span>
                  <span
                    v-for="key in turnRequestKeys(turn)"
                    :key="key"
                    class="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-mono text-[10px] text-emerald-700"
                    title="该请求体字段来自用例请求变量"
                  >
                    用例变量 {{ key }}
                  </span>
                </div>
                <div class="flex items-center gap-3 text-[10px] text-slate-500">
                  <span>TTFT {{ formatMs(turn.ttft_ms) }}</span>
                  <span>时延 {{ formatMs(turn.latency_ms) }}</span>
                  <span>
                    Tokens {{ formatNumber(turn.simulator_prompt_tokens + turn.simulator_completion_tokens) }}
                  </span>
                  <svg
                    class="h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform duration-200"
                    :class="isTurnCollapsed(turn.id) ? '' : 'rotate-180'"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    aria-hidden="true"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" d="m6 9 6 6 6-6" />
                  </svg>
                </div>
              </header>

              <div v-show="!isTurnCollapsed(turn.id)">
                <div class="grid gap-3 px-3 py-3 lg:grid-cols-2">
                  <div>
                    <p class="text-[10px] uppercase tracking-wider text-slate-500">用户提问</p>
                    <p class="mt-1 whitespace-pre-wrap break-words rounded-lg bg-white p-2 text-sm text-slate-800">
                      {{ turn.query }}
                    </p>
                    <div v-if="turn.simulator_thought" class="mt-2 rounded-lg border border-fuchsia-500/20 bg-fuchsia-500/5 p-2">
                      <p class="text-[10px] uppercase tracking-wider text-fuchsia-400/80">模拟器思考</p>
                      <p class="mt-1 whitespace-pre-wrap break-words text-xs text-slate-600">{{ turn.simulator_thought }}</p>
                    </div>
                    <div v-if="turn.hidden_context_released?.length" class="mt-2">
                      <p class="text-[10px] uppercase tracking-wider text-slate-500">本轮释放隐藏上下文</p>
                      <pre class="mono mt-1 rounded-lg bg-white p-2 text-slate-600">{{ safeJson(turn.hidden_context_released) }}</pre>
                    </div>
                  </div>
                  <div>
                    <p class="text-[10px] uppercase tracking-wider text-slate-500">客服最终回复</p>
                    <p class="mt-1 whitespace-pre-wrap break-words rounded-lg bg-white p-2 text-sm text-slate-800">
                      {{ turn.final_answer || '—' }}
                    </p>
                    <div v-if="turn.generated_sql" class="mt-2">
                      <p class="text-[10px] uppercase tracking-wider text-slate-500">生成 SQL</p>
                      <pre class="mono mt-1 overflow-auto rounded-lg bg-white p-2 text-sky-700">{{ turn.generated_sql }}</pre>
                    </div>
                    <div v-if="turn.api_params" class="mt-2">
                      <JsonViewer :value="turn.api_params" label="API 参数" max-height="120px" />
                    </div>
                  </div>
                </div>

                <div v-if="turn.raw_db_data" class="border-t border-slate-200 px-3 py-2">
                  <JsonViewer :value="turn.raw_db_data" label="底层取数 raw_db_data" max-height="160px" />
                </div>

                <details
                  v-if="turn.rule_check && detail.grading_mode !== 'ANSWER_CONTRACT'"
                  class="border-t border-slate-200 px-3 py-2"
                >
                  <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-700">
                    本轮规则校验
                    <span :class="turn.rule_check.passed ? 'text-emerald-700' : 'text-rose-700'">
                      {{ turn.rule_check.passed ? '通过' : '未通过' }}
                    </span>
                  </summary>
                  <pre class="mono mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-slate-600">{{ safeJson(turn.rule_check) }}</pre>
                </details>

                <details class="border-t border-slate-200 px-3 py-2">
                  <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-700">
                    请求 / 响应原始报文
                  </summary>
                  <div class="mt-2 grid gap-2 lg:grid-cols-2">
                    <JsonViewer :value="turn.request_payload" label="请求" max-height="220px" />
                    <JsonViewer :value="turn.response_raw" label="响应" max-height="220px" />
                  </div>
                </details>

                <details v-if="turn.rendered_prompt" class="border-t border-slate-200 px-3 py-2">
                  <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-700">
                    模拟器渲染 Prompt
                  </summary>
                  <pre class="mono mt-2 max-h-52 overflow-auto whitespace-pre-wrap rounded-lg bg-white p-2 text-slate-600">{{ turn.rendered_prompt }}</pre>
                </details>
              </div>
            </article>
          </div>
        </template>

        <template v-else-if="activeTab === 'judges'">
          <div class="mb-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
            <div class="flex flex-wrap items-center gap-3 text-xs text-slate-600">
              <span>裁判策略：{{ statusLabel(String(aggregate?.strategy ?? '—')) }}</span>
              <span>裁判数量：{{ String(aggregate?.judge_count ?? detail.judges.length) }}</span>
              <span>综合得分：<span class="text-slate-900">{{ formatScore(detail.total_score) }}</span></span>
              <span>
                综合结论：
                <span :class="aggregate?.pass ? 'text-emerald-700' : 'text-rose-700'">
                  {{ aggregate?.pass ? '通过' : '未通过' }}
                </span>
              </span>
            </div>
            <div v-if="dims.length" class="mt-2 flex flex-wrap gap-3">
              <span v-for="item in dims" :key="item.key" class="text-[11px] text-slate-500">
                {{ item.label }}
                <span class="text-slate-700">{{ item.value }}</span>
              </span>
            </div>
          </div>

          <p
            v-if="detail.grading_mode === 'ANSWER_CONTRACT'"
            class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700"
          >
            答案契约模式下，四维裁判只提供质量分参考，不参与通过判定；正确性以「契约判分」页的闸门与事实为准。
          </p>

          <div v-if="detail.judges.length === 0">
            <EmptyState compact title="没有裁判明细" description="该执行未运行 LLM 裁判。" />
          </div>
          <div v-else class="space-y-3">
            <article
              v-for="judge in detail.judges"
              :key="judge.id"
              class="rounded-xl border border-slate-200 bg-slate-50 p-3"
            >
              <header class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-medium text-slate-800">{{ judge.model_name }}</span>
                  <StatusPill :status="judge.pass_flag ? 'PASS' : 'FAIL'" size="sm" />
                  <span class="tag">得分 {{ formatScore(judge.total_score) }}</span>
                </div>
                <span class="text-[10px] text-slate-500">
                  Tokens {{ judge.prompt_tokens }} / {{ judge.completion_tokens }}
                </span>
              </header>

              <div v-if="judgeDimensions(judge).length" class="mt-3 grid gap-2 sm:grid-cols-2">
                <div v-for="dim in judgeDimensions(judge)" :key="dim.key">
                  <div class="flex items-center justify-between text-[11px]">
                    <span class="text-slate-500">{{ dim.label }}</span>
                    <span class="text-slate-700">{{ dim.value }} / {{ dim.max }}</span>
                  </div>
                  <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
                    <div
                      class="h-full rounded-full"
                      :class="dim.value >= dim.max * 0.6 ? 'bg-emerald-500' : 'bg-rose-500'"
                      :style="{ width: `${Math.min(100, (dim.value / dim.max) * 100)}%` }"
                    />
                  </div>
                </div>
              </div>

              <div v-if="judge.error_tags?.length" class="mt-2 flex flex-wrap gap-1">
                <span
                  v-for="tag in judge.error_tags"
                  :key="tag"
                  class="rounded-md border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[10px] text-rose-700"
                >
                  {{ tag }}
                </span>
              </div>

              <div class="mt-2">
                <p class="text-[10px] uppercase tracking-wider text-slate-500">裁判理由</p>
                <p class="mt-1 whitespace-pre-wrap break-words rounded-lg bg-white p-2 text-xs text-slate-700">
                  {{ judge.reason || '—' }}
                </p>
              </div>

              <details v-if="judge.raw_response" class="mt-2">
                <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-700">结构化裁判响应</summary>
                <div class="mt-2">
                  <JsonViewer :value="judge.raw_response" max-height="200px" />
                </div>
              </details>
              <details v-if="judge.judge_prompt" class="mt-2">
                <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-700">实际裁判 Prompt</summary>
                <pre class="mono mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-white p-2 text-slate-600">{{ judge.judge_prompt }}</pre>
              </details>
            </article>
          </div>
        </template>

        <template v-else-if="activeTab === 'contract'">
          <div v-if="contractGrading" class="space-y-4">
            <div class="flex flex-wrap items-center gap-2">
              <StatusPill :status="contractGrading.passed ? 'PASS' : 'FAIL'" />
              <span
                v-for="gate in contractGates"
                :key="gate.key"
                class="tag"
                :class="gate.passed ? 'border-emerald-200 text-emerald-700' : 'border-rose-200 text-rose-700'"
              >
                {{ gate.label }}{{ gate.passed ? '通过' : '未过' }}
              </span>
              <span v-if="detail.as_of" class="tag">冻结：{{ detail.as_of }}</span>
              <span v-if="detail.grader_version" class="tag">{{ detail.grader_version }}</span>
              <span
                v-if="contractGrading.degraded"
                class="tag border-amber-300 text-amber-700"
              >
                判分降级
              </span>
            </div>

            <div
              v-if="failedGates.length || failedFacts.length || rootCauseText"
              class="space-y-1 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
            >
              <p class="font-medium">未通过原因</p>
              <p v-if="failedGates.length">
                闸门未过：{{ failedGates.map((gate) => gate.label).join('、') }}
              </p>
              <p v-if="failedFacts.length">
                事实未命中：{{ failedFacts.map((fact) => fact.label || fact.fact_id).join('、') }}
              </p>
              <p v-if="rootCauseText">根因：{{ rootCauseText }}</p>
            </div>

            <div>
              <div class="flex flex-wrap items-baseline justify-between gap-2">
                <p class="text-xs font-medium text-slate-700">
                  记分卡（仅用于排序与优化，不决定通过与否）
                </p>
                <p class="text-[11px] text-slate-500">
                  排序分 <span class="font-semibold text-slate-800">{{ formatScore(contractTotalScore) }}</span>
                </p>
              </div>
              <div class="mt-2 grid gap-2 sm:grid-cols-4">
                <div
                  v-for="item in contractScorecard"
                  :key="item.key"
                  class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2"
                >
                  <p class="text-[10px] uppercase tracking-wider text-slate-500">
                    {{ item.label }}
                    <span class="text-slate-400">{{ SCORECARD_WEIGHT[item.key] }}</span>
                  </p>
                  <p class="text-sm font-semibold tabular-nums text-slate-800">{{ formatScore(item.value) }}</p>
                </div>
              </div>
            </div>

            <div
              v-if="contractBehavior"
              class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600"
            >
              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="font-medium text-slate-700">响应契约（行为判定）</p>
                <span
                  class="tag"
                  :class="
                    contractBehavior.allowed
                      ? 'border-emerald-200 text-emerald-700'
                      : 'border-rose-200 text-rose-700'
                  "
                >
                  {{ contractBehavior.allowed ? '行为通过' : '行为未过' }}
                </span>
              </div>
              <div class="mt-1 flex flex-wrap gap-3">
                <span>识别行为：{{ behaviorLabel(contractBehavior.detected) }}</span>
                <span>声明假设：{{ contractBehavior.stated_assumption ? '是' : '否' }}</span>
                <span v-if="contractBehavior.matched_scenario">
                  匹配场景：{{ contractBehavior.matched_scenario }}
                </span>
              </div>
              <p v-if="contractBehavior.missing_slots?.length" class="mt-1 text-amber-700">
                未澄清槽位：{{ (contractBehavior.missing_slots ?? []).join('、') }}
              </p>
              <p
                v-for="violation in contractBehavior.violations ?? []"
                :key="violation"
                class="mt-1 text-rose-700"
              >
                违规：{{ violation }}
              </p>
              <p v-if="contractBehavior.detail" class="mt-1 text-slate-500">
                {{ contractBehavior.detail }}
              </p>
            </div>

            <div v-if="contractFacts.length" class="overflow-hidden rounded-xl border border-slate-200">
              <table class="table-shell">
                <thead>
                  <tr>
                    <th class="th">事实</th>
                    <th class="th">类型</th>
                    <th class="th">期望</th>
                    <th class="th">实际</th>
                    <th class="th">闸门</th>
                    <th class="th">结果</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(fact, index) in contractFacts" :key="fact.fact_id ?? index">
                    <td class="td">
                      <p class="text-slate-800">{{ fact.label || fact.fact_id || '—' }}</p>
                      <p class="font-mono text-[10px] text-slate-500">{{ fact.fact_id }}</p>
                      <p v-if="fact.mode" class="mt-0.5 text-[10px] text-slate-500">
                        {{ bindingModeLabel(fact.mode) }}
                        <span v-if="fact.bound_name">· {{ fact.bound_name }}</span>
                      </p>
                    </td>
                    <td class="td text-xs">{{ factKindLabel(fact.kind) }}</td>
                    <td class="td max-w-[220px] break-words text-xs">{{ factExpectedText(fact) }}</td>
                    <td class="td max-w-[220px] break-words text-xs">{{ factActualText(fact) }}</td>
                    <td class="td text-[11px] text-slate-500">{{ fact.is_gate ? 'Must' : 'May' }}</td>
                    <td class="td">
                      <span
                        v-if="fact.degraded"
                        class="rounded-full border border-amber-300 px-1.5 py-0.5 text-[10px] text-amber-700"
                      >
                        未判定
                      </span>
                      <StatusPill v-else :status="fact.passed ? 'PASS' : 'FAIL'" size="sm" />
                      <p v-if="fact.reason" class="mt-1 text-[10px]" :class="fact.passed ? 'text-slate-500' : 'text-rose-600'">
                        {{ fact.reason }}
                      </p>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p
              v-else
              class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500"
            >
              本次没有事实级明细：该用例未配置 Must 事实，或答案走的是澄清 / 拒绝等行为契约分支。
            </p>

            <div
              v-if="contractExtras.length"
              class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600"
            >
              <p class="font-medium text-slate-700">扩展内容判定</p>
              <ul class="mt-1 space-y-1">
                <li
                  v-for="(extra, index) in contractExtras"
                  :key="index"
                  class="flex flex-wrap items-center gap-2"
                >
                  <span
                    class="rounded-full border px-1.5 py-0.5 text-[10px]"
                    :class="
                      extra.kind === 'grounded' || extra.kind === 'may'
                        ? 'border-emerald-200 text-emerald-700'
                        : 'border-amber-300 text-amber-700'
                    "
                  >
                    {{ extraKindLabel(extra.kind) }}
                  </span>
                  <span class="break-words text-slate-700">{{ extra.text || '—' }}</span>
                  <span v-if="extra.evidence_ref" class="font-mono text-[10px] text-slate-500">
                    证据：{{ extra.evidence_ref }}
                  </span>
                </li>
              </ul>
            </div>

            <div
              v-if="contractQuality"
              class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600"
            >
              <p class="font-medium text-slate-700">
                质量分参考（四维裁判，仅参考，不参与判定）
                <span class="ml-1 text-slate-800">
                  {{ formatScore(contractQuality.total_score) }}
                </span>
              </p>
              <div v-if="contractQualityDims.length" class="mt-1 flex flex-wrap gap-3">
                <span v-for="dim in contractQualityDims" :key="dim.key">
                  {{ dim.label }}
                  <span class="text-slate-800">{{ dim.value }}</span>
                </span>
              </div>
            </div>

            <div
              v-if="contractDiagnostics"
              class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600"
            >
              <p class="font-medium text-slate-700">轨迹诊断（0 权重，仅用于根因定位）</p>
              <div class="mt-1 flex flex-wrap gap-3">
                <span>工具调用：{{ contractDiagnostics.tool_call_count ?? 0 }}</span>
                <span>取数行数：{{ contractDiagnostics.evidence_rows ?? 0 }}</span>
                <span v-if="detail.lucky_pass" class="text-amber-700">幸运通过（答案对但无取数证据）</span>
                <span v-if="rootCauseText" class="text-rose-700">根因：{{ rootCauseText }}</span>
              </div>
              <p v-if="missingCapabilities.length" class="mt-1 text-amber-700">
                缺失能力：{{ missingCapabilities.join('、') }}
              </p>
            </div>

            <div v-if="contractNotes.length" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2">
              <p v-for="note in contractNotes" :key="note" class="text-xs text-amber-700">
                · {{ note }}
              </p>
            </div>

            <details class="rounded-xl border border-slate-200 bg-slate-50">
              <summary class="cursor-pointer px-3 py-2 text-[11px] text-slate-600">
                answer_grading 原始 JSON
              </summary>
              <div class="border-t border-slate-200 p-2">
                <JsonViewer :value="contractGrading" max-height="360px" />
              </div>
            </details>
          </div>
          <EmptyState
            v-else
            compact
            title="没有契约判分"
            description="该执行不是答案契约模式，或判分结果尚未生成。"
          />
        </template>

        <template v-else-if="activeTab === 'rules'">
          <div v-if="ruleChecks.length" class="space-y-3">
            <div class="flex flex-wrap items-center gap-2">
              <StatusPill :status="ruleBlock?.passed ? 'PASS' : 'FAIL'" />
              <span class="text-xs text-slate-500">共 {{ ruleChecks.length }} 项硬校验</span>
            </div>
            <div class="overflow-hidden rounded-xl border border-slate-200">
              <table class="table-shell">
                <thead>
                  <tr>
                    <th class="th">校验项</th>
                    <th class="th">期望</th>
                    <th class="th">实际</th>
                    <th class="th">证据</th>
                    <th class="th">结果</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(check, index) in ruleChecks" :key="index">
                    <td class="td">
                      <p class="text-slate-800">{{ ruleCheckLabel(check) }}</p>
                      <p class="font-mono text-[10px] text-slate-500">{{ check.type }}</p>
                      <p v-if="check.mode" class="mt-0.5 text-[10px] text-slate-500">
                        {{ bindingModeLabel(check.mode) }}
                        <span v-if="check.bound_name">· {{ check.bound_name }}</span>
                      </p>
                    </td>
                    <td class="td max-w-[200px] break-all text-xs">{{ valueText(check.expected) }}</td>
                    <td class="td max-w-[220px] break-all text-xs">{{ valueText(check.actual) }}</td>
                    <td class="td max-w-[220px] break-words text-[11px] text-slate-500">{{ check.evidence || '—' }}</td>
                    <td class="td">
                      <StatusPill :status="check.passed ? 'PASS' : 'FAIL'" size="sm" />
                      <p v-if="!check.passed && check.reason" class="mt-1 text-[10px] text-rose-600">
                        {{ check.reason }}
                      </p>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <JsonViewer :value="ruleBlock?.extracted" label="抽取结果 extracted" />
          </div>
          <EmptyState
            v-else
            title="没有规则硬校验"
            description="该用例未配置金额 / 笔数 / 日期 / 关键词规则。"
          />
        </template>

        <template v-else-if="activeTab === 'selfcheck'">
          <div v-if="selfCheck" class="space-y-3">
            <div class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
              <div class="flex flex-wrap items-center gap-3 text-xs text-slate-600">
                <span
                  class="rounded-full border px-2 py-0.5 text-[11px] font-medium"
                  :class="selfCheckStatusClass"
                >
                  {{ selfCheckStatusLabel }}
                </span>
                <span v-if="selfCheck.trigger">触发原因：{{ selfCheck.trigger }}</span>
                <span v-if="selfCheck.session_id">会话：{{ selfCheck.session_id }}</span>
                <span v-if="selfCheck.template_name || selfCheck.template_code">
                  模板：{{ selfCheck.template_name || selfCheck.template_code }}
                  <template v-if="selfCheck.template_version">
                    · v{{ selfCheck.template_version }}
                  </template>
                </span>
                <span v-if="selfCheck.latency_ms != null">
                  追问时延：{{ formatMs(selfCheck.latency_ms) }}
                </span>
                <span v-if="selfCheck.performed_at">时间：{{ formatDateTime(selfCheck.performed_at) }}</span>
              </div>
              <p
                v-for="(note, index) in selfCheck.notes ?? []"
                :key="index"
                class="mt-1 text-[11px] text-amber-600"
              >
                {{ note }}
              </p>
            </div>

            <div class="rounded-xl border border-slate-200 p-3">
              <p class="label">{{ selfCheck.status === 'DONE' ? 'Agent 自检结论' : '说明' }}</p>
              <p class="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
                {{ selfCheckBody }}
              </p>
            </div>

            <div v-if="selfCheck.prompt" class="rounded-xl border border-slate-200 p-3">
              <button
                class="flex w-full items-center justify-between text-left"
                @click="selfCheckPromptOpen = !selfCheckPromptOpen"
              >
                <span class="label">追问 Prompt（渲染后）</span>
                <span class="text-xs text-slate-500">{{ selfCheckPromptOpen ? '收起' : '展开' }}</span>
              </button>
              <pre
                v-if="selfCheckPromptOpen"
                class="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-2 font-mono text-xs text-slate-700"
              >{{ selfCheck.prompt }}</pre>
            </div>

            <div
              v-if="selfCheck.request_payload || selfCheck.response_raw"
              class="grid gap-3 lg:grid-cols-2"
            >
              <JsonViewer :value="selfCheck.request_payload ?? {}" label="追问请求" max-height="260px" />
              <JsonViewer :value="selfCheck.response_raw ?? {}" label="追问响应" max-height="260px" />
            </div>
          </div>
          <EmptyState
            v-else
            compact
            title="没有追问自检记录"
            description="任务未开启「失败追问自检」，或该用例未触发追问。"
          />
        </template>

        <template v-else-if="activeTab === 'snapshot'">
          <div class="space-y-3">
            <div>
              <p class="label">执行时的用例快照（case_version v{{ detail.case_version }}）</p>
              <JsonViewer :value="detail.case_snapshot" max-height="420px" label="case_snapshot" />
            </div>
            <div class="grid gap-3 lg:grid-cols-2">
              <JsonViewer :value="detail.rule_result" label="rule_result" max-height="300px" />
              <JsonViewer :value="detail.judge_result" label="judge_result" max-height="300px" />
            </div>
          </div>
        </template>

        <template v-else>
          <div class="space-y-3">
            <div
              v-for="item in detail.raw_request_response"
              :key="String(item.turn)"
              class="grid gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3 lg:grid-cols-2"
            >
              <div class="lg:col-span-2">
                <span class="font-mono text-xs font-semibold text-indigo-600">T{{ item.turn }}</span>
              </div>
              <JsonViewer :value="item.request" label="请求" max-height="260px" />
              <JsonViewer :value="item.response" label="响应" max-height="260px" />
            </div>
            <EmptyState v-if="detail.raw_request_response.length === 0" compact title="没有原始报文" />
          </div>
        </template>
      </div>
    </template>
  </section>
</template>
