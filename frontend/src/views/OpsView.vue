<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'
import BaseModal from '../components/BaseModal.vue'
import { useConfirm } from '../composables/useConfirm'
import { api } from '../api'
import { errorMessage } from '../utils/errors'
import { formatDateTime } from '../utils/format'
import { useToastStore } from '../stores/toast'
import { useAuthStore } from '../stores/auth'
import type {
  Caliber,
  CandidateSource,
  Capability,
  CaseCandidate,
  DataSource,
  DataSourceKind,
  DatasetStatItem,
  FactAtomView,
  FactStatItem,
  FactStatsOverview,
  FactVerificationStatus,
  GoldQuery,
  GraderEval,
  TestCase,
} from '../types'
import SelectButton from '../components/SelectButton.vue'

type TabKey =
  | 'stats'
  | 'facts'
  | 'candidates'
  | 'grader'
  | 'calibers'
  | 'sources'
  | 'capabilities'

const TABS: Array<{ value: TabKey; label: string }> = [
  { value: 'stats', label: '事实统计' },
  { value: 'facts', label: '事实验收' },
  { value: 'candidates', label: '候选池' },
  { value: 'grader', label: '判分器自检' },
  { value: 'calibers', label: '业务口径' },
  { value: 'sources', label: '数据源' },
  { value: 'capabilities', label: '取数能力' },
]

const toast = useToastStore()
const auth = useAuthStore()
const confirm = useConfirm()
const route = useRoute()
const router = useRouter()

function resolveTab(value: unknown): TabKey {
  const key = String(value ?? '')
  return TABS.some((item) => item.value === key) ? (key as TabKey) : 'stats'
}

const tab = ref<TabKey>(resolveTab(route.query.tab))
const loading = ref(false)

const CANDIDATE_STATUS_META: Record<string, { label: string; className: string }> = {
  NEW: { label: '待分诊', className: 'bg-amber-50 text-amber-700' },
  TRIAGED: { label: '已分诊', className: 'bg-sky-50 text-sky-700' },
  PROMOTED: { label: '已转正', className: 'bg-emerald-50 text-emerald-700' },
  REJECTED: { label: '已拒绝', className: 'bg-rose-50 text-rose-700' },
}

function candidateMeta(status: string): { label: string; className: string } {
  return CANDIDATE_STATUS_META[status] ?? {
    label: status,
    className: 'bg-slate-100 text-slate-600',
  }
}

// 枚举中文映射：界面不直接暴露后端枚举值
const GRADING_MODE_LABELS: Record<string, string> = {
  LEGACY: '4 维评分',
  ANSWER_CONTRACT: '答案契约',
}
const REVIEW_STATUS_LABELS: Record<string, string> = {
  PENDING: '待评审',
  APPROVED: '已通过',
  REJECTED: '已驳回',
  NOT_REQUIRED: '无需评审',
}
const CANDIDATE_SOURCE_LABELS: Record<string, string> = {
  MANUAL: '手工录入',
  PRODUCTION: '生产日志',
  TRACE: '执行轨迹',
  LLM: '模型生成',
}
const CALIBER_SOURCE_LABELS: Record<string, string> = {
  BUSINESS_UI: '业务界面',
  BILLING_SYSTEM: '计费系统',
  CRM: 'CRM',
  DATA_WAREHOUSE: '数据仓库',
}
const DATA_SOURCE_KIND_LABELS: Record<string, string> = {
  CYPHER_TOOL: 'Cypher 执行器工具',
  SQL_DB: '只读 SQL',
  HTTP_API: '业务查询接口',
}
const PROBE_STATUS_LABELS: Record<string, string> = {
  OK: '正常',
  FAILED: '失败',
}
const CAPABILITY_STATUS_LABELS: Record<string, string> = {
  DRAFT: '草稿',
  CERTIFIED: '已认证',
  DEPRECATED: '已废弃',
}
const GOLD_ORIGIN_LABELS: Record<string, string> = {
  CAPABILITY: '能力模板',
  CAPABILITY_AUTO: '能力模板（自动）',
  AGENT_TRACE: '执行轨迹',
  UPLOAD: '批量上传',
  MANUAL: '手工录入',
}

function enumLabel(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return '—'
  return map[value] ?? value
}

function parseJsonObject(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const parsed = JSON.parse(trimmed) as unknown
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('必须是 JSON 对象')
  }
  return parsed as Record<string, unknown>
}

// ---------------------------------------------------------------- 统计
const overview = ref<FactStatsOverview | null>(null)
const factStats = ref<FactStatItem[]>([])
const datasets = ref<DatasetStatItem[]>([])

async function loadStats(): Promise<void> {
  const [overviewData, factsData, datasetData] = await Promise.all([
    api.stats.overview(),
    api.stats.facts(),
    api.stats.datasets(),
  ])
  overview.value = overviewData
  factStats.value = factsData
  datasets.value = datasetData
}

// ---------------------------------------------------------------- 事实验收
const factCases = ref<TestCase[]>([])
const factCaseId = ref<number | null>(null)
const factRows = ref<FactAtomView[]>([])

const FACT_SECTION_META: Record<string, { label: string; className: string }> = {
  must: { label: 'Must', className: 'bg-rose-50 text-rose-700' },
  may: { label: 'May', className: 'bg-sky-50 text-sky-700' },
  must_not: { label: 'MustNot', className: 'bg-slate-100 text-slate-600' },
}
const FACT_STATUS_META: Record<string, { label: string; className: string }> = {
  PENDING: { label: '待取证', className: 'bg-amber-50 text-amber-700' },
  VERIFIED: { label: '已取证', className: 'bg-emerald-50 text-emerald-700' },
  CONFLICT: { label: '存在冲突', className: 'bg-rose-50 text-rose-700' },
  NO_EVIDENCE: { label: '无证据', className: 'bg-orange-50 text-orange-700' },
  WAIVED: { label: '已豁免', className: 'bg-sky-50 text-sky-700' },
  STALE: { label: '已过期', className: 'bg-slate-100 text-slate-600' },
}

function factSectionMeta(section: string): { label: string; className: string } {
  return FACT_SECTION_META[section] ?? {
    label: section,
    className: 'bg-slate-100 text-slate-600',
  }
}

function factStatus(item: FactAtomView): FactVerificationStatus {
  return (item.verification_status ?? 'PENDING') as FactVerificationStatus
}

function factExpected(item: FactAtomView): string {
  if (item.values?.length) return item.values.join(' / ')
  if (item.keywords?.length) return item.keywords.join(' / ')
  if (item.value !== null && item.value !== undefined && item.value !== '') {
    return `${item.value}${item.unit ? ` ${item.unit}` : ''}`
  }
  return '—'
}

const pendingMustCount = computed(
  () =>
    factRows.value.filter(
      (item) =>
        item.section === 'must' && !['VERIFIED', 'WAIVED'].includes(factStatus(item)),
    ).length,
)

async function loadFactCases(): Promise<void> {
  const pageData = await api.cases.list({
    grading_mode: 'ANSWER_CONTRACT',
    page_size: 100,
  })
  factCases.value = pageData.items
  if (factCaseId.value === null && factCases.value.length) {
    factCaseId.value = factCases.value[0].id
  }
  if (
    factCaseId.value !== null &&
    !factCases.value.some((item) => item.id === factCaseId.value)
  ) {
    factCaseId.value = factCases.value[0]?.id ?? null
  }
  await loadCaseFacts()
}

async function refreshFactCases(): Promise<void> {
  try {
    await loadFactCases()
    toast.success('刷新成功')
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function loadCaseFacts(): Promise<void> {
  if (factCaseId.value === null) {
    factRows.value = []
    return
  }
  factRows.value = await api.cases.facts(factCaseId.value)
}

const factDialogOpen = ref(false)
const factAction = ref<'VERIFIED' | 'WAIVED' | 'PENDING'>('VERIFIED')
const factTarget = ref<FactAtomView | null>(null)
const factEvidence = ref('')
const factNote = ref('')

const FACT_ACTION_META: Record<
  'VERIFIED' | 'WAIVED' | 'PENDING',
  { title: string; hint: string; confirm: string }
> = {
  VERIFIED: {
    title: '登记事实取证',
    hint: '填写证据引用（可选，如 gold_query:12、capability:bill_order_detail）。',
    confirm: '确认取证',
  },
  WAIVED: {
    title: '豁免事实取证',
    hint: '豁免必须说明原因，便于后续复核与审计。',
    confirm: '确认豁免',
  },
  PENDING: {
    title: '退回待取证',
    hint: '退回后原取证结论会被清空；若用例已评审通过，将自动退回待评审。',
    confirm: '确认退回',
  },
}

function openFactDialog(
  item: FactAtomView,
  status: 'VERIFIED' | 'WAIVED' | 'PENDING',
): void {
  if (factCaseId.value === null || !item.id) return
  factTarget.value = item
  factAction.value = status
  factEvidence.value = item.evidence_ref ?? ''
  factNote.value = item.verification_note ?? ''
  factDialogOpen.value = true
}

async function submitFactDialog(): Promise<void> {
  const item = factTarget.value
  if (!item || !item.id || factCaseId.value === null) return
  const status = factAction.value
  if (status === 'WAIVED' && !factNote.value.trim()) {
    toast.error('豁免必须填写原因')
    return
  }

  loading.value = true
  try {
    await api.cases.verifyFact(factCaseId.value, String(item.id), {
      verification_status: status,
      evidence_ref: status === 'PENDING' ? null : factEvidence.value.trim() || null,
      note: factNote.value.trim() || null,
    })
    toast.success(
      status === 'VERIFIED'
        ? '事实已登记取证'
        : status === 'WAIVED'
          ? '事实已豁免'
          : '事实已退回待取证',
    )
    factDialogOpen.value = false
    await loadFactCases()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 候选池
const candidates = ref<CaseCandidate[]>([])
const candidateTotal = ref(0)
const candidateFilter = reactive({ status: '', source: '' })
const candidateForm = reactive({
  raw_question: '',
  source: 'MANUAL' as CandidateSource,
  note: '',
})

async function loadCandidates(): Promise<void> {
  const pageData = await api.candidates.list({
    status: candidateFilter.status || undefined,
    source: candidateFilter.source || undefined,
    page_size: 100,
  })
  candidates.value = pageData.items
  candidateTotal.value = pageData.total
}

async function createCandidate(): Promise<void> {
  if (!candidateForm.raw_question.trim()) {
    toast.error('请填写候选问题')
    return
  }
  loading.value = true
  try {
    await api.candidates.create({
      raw_question: candidateForm.raw_question.trim(),
      source: candidateForm.source,
      note: candidateForm.note.trim() || null,
    })
    toast.success('候选已登记')
    candidateForm.raw_question = ''
    candidateForm.note = ''
    await loadCandidates()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function promoteCandidate(item: CaseCandidate): Promise<void> {
  loading.value = true
  try {
    const result = await api.candidates.promote(item.id)
    toast.success(`已转正为用例 ${result.case_code}`)
    await loadCandidates()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function triageCandidate(item: CaseCandidate): Promise<void> {
  loading.value = true
  try {
    await api.candidates.update(item.id, { status: 'TRIAGED' })
    toast.success('候选已标记为已分诊')
    await loadCandidates()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function rejectCandidate(item: CaseCandidate): Promise<void> {
  const quoted = item.raw_question.slice(0, 40)
  const ok = await confirm({
    title: '拒绝候选',
    message: `确定拒绝候选「${quoted}」吗？`,
    detail: '拒绝后该候选不会再进入转正流程。',
    confirmText: '拒绝',
    tone: 'warning',
  })
  if (!ok) return
  loading.value = true
  try {
    await api.candidates.reject(item.id)
    toast.success('候选已拒绝')
    await loadCandidates()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 判分器自检
const evals = ref<GraderEval[]>([])
const lastEval = ref<GraderEval | null>(null)

async function loadEvals(): Promise<void> {
  const pageData = await api.grader.evals({ page_size: 50 })
  evals.value = pageData.items
}

async function runEvals(): Promise<void> {
  loading.value = true
  try {
    const result = await api.grader.runEvals()
    lastEval.value = result
    toast.success(
      result.false_kills === 0 && result.detected === result.total
        ? `自检通过：${result.detected}/${result.total} 变异全部识别`
        : `自检异常：识别 ${result.detected}/${result.total}，误杀 ${result.false_kills}`,
    )
    await loadEvals()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 业务口径
const calibers = ref<Caliber[]>([])
const editingCaliberId = ref<number | null>(null)
const caliberForm = reactive({
  key: '',
  name: '',
  definition: '',
  canonical_source: 'BUSINESS_UI',
  owner: '',
})

async function loadCalibers(): Promise<void> {
  const pageData = await api.calibers.list({ page_size: 100 })
  calibers.value = pageData.items
}

function resetCaliberForm(): void {
  editingCaliberId.value = null
  caliberForm.key = ''
  caliberForm.name = ''
  caliberForm.definition = ''
  caliberForm.canonical_source = 'BUSINESS_UI'
  caliberForm.owner = ''
}

function startEditCaliber(item: Caliber): void {
  editingCaliberId.value = item.id
  caliberForm.key = item.key
  caliberForm.name = item.name
  caliberForm.definition = item.definition ?? ''
  caliberForm.canonical_source = item.canonical_source
  caliberForm.owner = item.owner ?? ''
}

async function submitCaliber(): Promise<void> {
  if (!editingCaliberId.value && !caliberForm.key.trim()) {
    toast.error('请填写口径键')
    return
  }
  if (!caliberForm.name.trim()) {
    toast.error('请填写口径名称')
    return
  }
  loading.value = true
  try {
    if (editingCaliberId.value) {
      await api.calibers.update(editingCaliberId.value, {
        name: caliberForm.name.trim(),
        definition: caliberForm.definition.trim() || null,
        canonical_source: caliberForm.canonical_source,
        owner: caliberForm.owner.trim() || null,
      })
      toast.success('口径已更新')
    } else {
      await api.calibers.create({
        key: caliberForm.key.trim(),
        name: caliberForm.name.trim(),
        definition: caliberForm.definition.trim() || null,
        canonical_source: caliberForm.canonical_source,
        owner: caliberForm.owner.trim() || null,
      })
      toast.success('口径已新增')
    }
    resetCaliberForm()
    await loadCalibers()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function removeCaliber(item: Caliber): Promise<void> {
  const ok = await confirm({
    title: '删除口径',
    message: `确定删除口径 ${item.key} 吗？`,
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  loading.value = true
  try {
    await api.calibers.remove(item.id)
    toast.success('口径已删除')
    await loadCalibers()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 数据源
const sources = ref<DataSource[]>([])
const editingSourceId = ref<number | null>(null)
const sourceForm = reactive({
  code: '',
  name: '',
  kind: 'CYPHER_TOOL' as DataSourceKind,
  endpoint_url: '',
  description: '',
  authText: '',
  timeout_seconds: 10,
  max_rows: 200,
})

async function loadSources(): Promise<void> {
  const pageData = await api.dataSources.list({ page_size: 100, is_active: true })
  sources.value = pageData.items
}

function resetSourceForm(): void {
  editingSourceId.value = null
  sourceForm.code = ''
  sourceForm.name = ''
  sourceForm.kind = 'CYPHER_TOOL'
  sourceForm.endpoint_url = ''
  sourceForm.description = ''
  sourceForm.authText = ''
  sourceForm.timeout_seconds = 10
  sourceForm.max_rows = 200
}

function startEditSource(item: DataSource): void {
  editingSourceId.value = item.id
  sourceForm.code = item.code
  sourceForm.name = item.name
  sourceForm.kind = item.kind as DataSourceKind
  sourceForm.endpoint_url = item.endpoint_url ?? ''
  sourceForm.description = item.description ?? ''
  sourceForm.authText = item.auth_config
    ? JSON.stringify(item.auth_config, null, 2)
    : ''
  sourceForm.timeout_seconds = item.timeout_seconds
  sourceForm.max_rows = item.max_rows
}

async function submitSource(): Promise<void> {
  if (!editingSourceId.value && !sourceForm.code.trim()) {
    toast.error('请填写数据源编码')
    return
  }
  if (!sourceForm.name.trim()) {
    toast.error('请填写数据源名称')
    return
  }
  loading.value = true
  try {
    const authConfig = sourceForm.authText.trim()
      ? parseJsonObject(sourceForm.authText)
      : undefined
    const base = {
      name: sourceForm.name.trim(),
      kind: sourceForm.kind,
      endpoint_url: sourceForm.endpoint_url.trim() || null,
      description: sourceForm.description.trim() || null,
      timeout_seconds: sourceForm.timeout_seconds,
      max_rows: sourceForm.max_rows,
      ...(authConfig !== undefined ? { auth_config: authConfig } : {}),
    }
    if (editingSourceId.value) {
      await api.dataSources.update(editingSourceId.value, base)
      toast.success('数据源已更新')
    } else {
      await api.dataSources.create({ ...base, code: sourceForm.code.trim() })
      toast.success('数据源已新增')
    }
    resetSourceForm()
    await loadSources()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function probeSource(item: DataSource): Promise<void> {
  loading.value = true
  try {
    const result = await api.dataSources.probe(item.id)
    if (result.ok) toast.success(`探活成功：${result.latency_ms ?? '—'} ms`)
    else toast.error(`探活失败：${result.message}`)
    await loadSources()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function toggleSource(item: DataSource): Promise<void> {
  const next = !item.is_active
  if (!next) {
    const ok = await confirm({
      title: '停用数据源',
      message: `确定停用数据源 ${item.code} 吗？`,
      detail: '停用后依赖该数据源的取数能力将无法执行。',
      confirmText: '停用',
      tone: 'warning',
    })
    if (!ok) return
  }
  loading.value = true
  try {
    await api.dataSources.update(item.id, { is_active: next })
    toast.success(next ? '数据源已启用' : '数据源已停用')
    if (editingSourceId.value === item.id && !next) resetSourceForm()
    await loadSources()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 取数能力
const capabilities = ref<Capability[]>([])
const editingCapabilityId = ref<number | null>(null)
const capabilityForm = reactive({
  key: '',
  name: '',
  description: '',
  source_id: null as number | null,
  query_text: '',
  param_binding: '',
})
const evidenceForm = reactive({
  capability_id: null as number | null,
  case_id: null as number | null,
  paramsText: '{}',
  factIdsText: '',
})
const lastEvidence = ref<Record<string, unknown> | null>(null)
const goldQueries = ref<GoldQuery[]>([])
const goldQueryTotal = ref(0)
const expandedGoldId = ref<number | null>(null)

function capabilityName(capabilityId: number | null): string {
  if (capabilityId === null) return '—'
  const found = capabilities.value.find((item) => item.id === capabilityId)
  return found ? found.name : `#${capabilityId}`
}

async function loadGoldQueries(): Promise<void> {
  const pageData = await api.capabilities.goldQueries({ page_size: 50 })
  goldQueries.value = pageData.items
  goldQueryTotal.value = pageData.total
}

async function refreshGoldQueries(): Promise<void> {
  try {
    await loadGoldQueries()
    toast.success('刷新成功')
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function loadCapabilities(): Promise<void> {
  const pageData = await api.capabilities.list({ page_size: 100, is_active: true })
  capabilities.value = pageData.items
  if (capabilityForm.source_id === null && sources.value.length) {
    capabilityForm.source_id = sources.value[0].id
  }
}

async function ensureSources(): Promise<void> {
  if (!sources.value.length) await loadSources()
}

function resetCapabilityForm(): void {
  editingCapabilityId.value = null
  capabilityForm.key = ''
  capabilityForm.name = ''
  capabilityForm.description = ''
  capabilityForm.query_text = ''
  capabilityForm.param_binding = ''
  capabilityForm.source_id = sources.value[0]?.id ?? null
}

function startEditCapability(item: Capability): void {
  editingCapabilityId.value = item.id
  capabilityForm.key = item.key
  capabilityForm.name = item.name
  capabilityForm.description = item.description ?? ''
  capabilityForm.source_id = item.source_id
  capabilityForm.query_text = item.query_text ?? ''
  capabilityForm.param_binding = item.param_binding
    ? JSON.stringify(item.param_binding, null, 2)
    : ''
}

async function submitCapability(): Promise<void> {
  if (!editingCapabilityId.value && !capabilityForm.key.trim()) {
    toast.error('请填写能力键')
    return
  }
  if (!capabilityForm.name.trim()) {
    toast.error('请填写能力名称')
    return
  }
  loading.value = true
  try {
    const paramBinding = parseJsonObject(capabilityForm.param_binding)
    const binding = paramBinding
      ? Object.fromEntries(
          Object.entries(paramBinding).map(([key, value]) => [key, String(value)]),
        )
      : null
    if (editingCapabilityId.value) {
      await api.capabilities.update(editingCapabilityId.value, {
        name: capabilityForm.name.trim(),
        description: capabilityForm.description.trim() || null,
        source_id: capabilityForm.source_id,
        query_text: capabilityForm.query_text.trim() || null,
        param_binding: binding,
      })
      toast.success('能力已更新')
    } else {
      await api.capabilities.create({
        key: capabilityForm.key.trim(),
        name: capabilityForm.name.trim(),
        description: capabilityForm.description.trim() || null,
        source_id: capabilityForm.source_id,
        query_text: capabilityForm.query_text.trim() || null,
        param_binding: binding,
      })
      toast.success('能力已新增')
    }
    resetCapabilityForm()
    await loadCapabilities()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function removeCapability(item: Capability): Promise<void> {
  const ok = await confirm({
    title: '删除能力',
    message: `确定删除能力 ${item.key} 吗？`,
    detail: '已生成的取证记录会保留。',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  loading.value = true
  try {
    await api.capabilities.remove(item.id)
    toast.success('能力已删除')
    if (editingCapabilityId.value === item.id) resetCapabilityForm()
    await loadCapabilities()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function certifyCapability(item: Capability): Promise<void> {
  loading.value = true
  try {
    await api.capabilities.update(item.id, { status: 'CERTIFIED' })
    toast.success(`能力 ${item.key} 已认证`)
    await loadCapabilities()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function runEvidence(): Promise<void> {
  if (!evidenceForm.capability_id || !evidenceForm.case_id) {
    toast.error('请选择能力与用例')
    return
  }
  loading.value = true
  try {
    const params = parseJsonObject(evidenceForm.paramsText) ?? {}
    const factIds = evidenceForm.factIdsText
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    const result = await api.capabilities.evidence(evidenceForm.capability_id, {
      case_id: evidenceForm.case_id,
      params,
      fact_ids: factIds,
    })
    lastEvidence.value = result as unknown as Record<string, unknown>
    toast.success(`取证完成：返回 ${result.gold_query.row_count} 行`)
    await loadGoldQueries()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 加载调度
async function loadActive(): Promise<boolean> {
  loading.value = true
  try {
    if (tab.value === 'stats') await loadStats()
    else if (tab.value === 'facts') await loadFactCases()
    else if (tab.value === 'candidates') await loadCandidates()
    else if (tab.value === 'grader') await loadEvals()
    else if (tab.value === 'calibers') await loadCalibers()
    else if (tab.value === 'sources') await loadSources()
    else {
      await ensureSources()
      await loadCapabilities()
      await loadGoldQueries()
    }
    return true
  } catch (exc) {
    toast.error(errorMessage(exc))
    return false
  } finally {
    loading.value = false
  }
}

async function refreshActive(): Promise<void> {
  if (loading.value) return
  if (await loadActive()) toast.success('刷新成功')
}

watch(tab, (value) => {
  void loadActive()
  if (route.query.tab !== value) {
    void router.replace({ query: { ...route.query, tab: value } })
  }
})

watch(
  () => route.query.tab,
  (value) => {
    const next = resolveTab(value)
    if (next !== tab.value) tab.value = next
  },
)

onMounted(() => {
  void loadActive()
})

const topFacts = computed(() => factStats.value.filter((item) => item.passed < item.total).slice(0, 8))
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      eyebrow="Ops · 运营后台"
      title="运营与取数资产"
      description="事实级统计、候选转正、判分器自检、业务口径、数据源与取数能力。外部执行器地址与鉴权待对接确认。"
    >
      <template #actions>
        <button class="btn-ghost" :disabled="loading" @click="refreshActive">刷新</button>
      </template>
    </PageHeader>

    <div class="flex flex-wrap gap-2">
      <button
        v-for="item in TABS"
        :key="item.value"
        class="btn-ghost btn-xs"
        :class="tab === item.value ? '!bg-slate-900 !text-white' : ''"
        @click="tab = item.value"
      >
        {{ item.label }}
      </button>
      <span v-if="loading" class="text-[11px] text-slate-500">加载中…</span>
    </div>

    <!-- ------------------------------------------------------- 事实统计 -->
    <div v-if="tab === 'stats'" class="space-y-4">
      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div class="card p-4">
          <p class="text-xs text-slate-500">契约执行数</p>
          <p class="mt-1 text-2xl font-semibold text-slate-900">
            {{ overview?.execution_total ?? 0 }}
          </p>
        </div>
        <div class="card p-4">
          <p class="text-xs text-slate-500">通过率</p>
          <p class="mt-1 text-2xl font-semibold text-emerald-600">
            {{ overview?.pass_rate ?? 0 }}%
          </p>
        </div>
        <div class="card p-4">
          <p class="text-xs text-slate-500">事实通过率</p>
          <p class="mt-1 text-2xl font-semibold text-indigo-600">
            {{ overview?.fact_pass_rate ?? 0 }}%
          </p>
        </div>
        <div class="card p-4">
          <p class="text-xs text-slate-500">幸运通过</p>
          <p class="mt-1 text-2xl font-semibold text-amber-600">
            {{ overview?.lucky_pass ?? 0 }}
          </p>
        </div>
      </div>

      <div class="grid gap-4 xl:grid-cols-2">
        <div class="card overflow-hidden">
          <div class="border-b border-slate-200 px-4 py-3 text-sm font-medium text-slate-700">
            失败最多的事实
          </div>
          <div class="overflow-x-auto">
            <table class="table-shell">
              <thead>
                <tr>
                  <th class="th">事实</th>
                  <th class="th">通过 / 总数</th>
                  <th class="th">通过率</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in topFacts" :key="item.fact_id">
                  <td class="td">
                    <p class="text-sm text-slate-800">{{ item.label || item.fact_id }}</p>
                    <p class="font-mono text-[11px] text-slate-500">{{ item.fact_id }}</p>
                  </td>
                  <td class="td text-xs">{{ item.passed }} / {{ item.total }}</td>
                  <td class="td text-xs text-rose-600">{{ item.pass_rate }}%</td>
                </tr>
                <tr v-if="!topFacts.length">
                  <td class="td text-xs text-slate-500" colspan="3">暂无失败事实</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="card overflow-hidden">
          <div class="border-b border-slate-200 px-4 py-3 text-sm font-medium text-slate-700">
            数据集分布
          </div>
          <div class="overflow-x-auto">
            <table class="table-shell">
              <thead>
                <tr>
                  <th class="th">版本</th>
                  <th class="th">判分方式</th>
                  <th class="th">评审状态</th>
                  <th class="th">用例数</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in datasets" :key="index">
                  <td class="td text-xs">{{ item.dataset_version || '—' }}</td>
                  <td class="td text-xs">{{ enumLabel(GRADING_MODE_LABELS, item.grading_mode) }}</td>
                  <td class="td text-xs">{{ enumLabel(REVIEW_STATUS_LABELS, item.review_status) }}</td>
                  <td class="td text-xs">{{ item.total }}</td>
                </tr>
                <tr v-if="!datasets.length">
                  <td class="td text-xs text-slate-500" colspan="4">暂无数据</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- ------------------------------------------------------- 事实验收 -->
    <div v-if="tab === 'facts'" class="space-y-4">
      <div class="card space-y-3 p-4">
        <div class="flex flex-wrap items-end justify-between gap-3">
          <div class="min-w-[280px] flex-1">
            <label class="label">契约用例</label>
            <select v-model.number="factCaseId" class="input" @change="loadCaseFacts">
              <SelectButton />
              <option :value="null">选择契约用例</option>
              <option v-for="item in factCases" :key="item.id" :value="item.id">
                {{ item.code }} · {{ item.name }}
              </option>
            </select>
          </div>
          <div class="flex items-center gap-2">
            <span
              v-if="pendingMustCount"
              class="rounded-full bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700"
            >
              {{ pendingMustCount }} 条 Must 事实待取证
            </span>
            <span
              v-else-if="factRows.length"
              class="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700"
            >
              Must 事实均已取证
            </span>
            <button class="btn-ghost btn-xs" :disabled="loading" @click="refreshFactCases">
              刷新
            </button>
          </div>
        </div>
        <p class="text-[11px] text-slate-500">
          Must 事实必须全部「已取证」或「已豁免」后，用例才能评审通过并冻结进数据集。
          这里登记的结论会记录操作人与时间，并自动退回已通过的用例重新评审。
        </p>
      </div>

      <div class="card overflow-hidden">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th">分区</th>
              <th class="th">事实</th>
              <th class="th">期望值</th>
              <th class="th">取证状态</th>
              <th class="th">证据 / 备注</th>
              <th class="th">操作人 / 时间</th>
              <th class="th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in factRows" :key="`${item.section}-${item.id}`">
              <td class="td">
                <span
                  class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                  :class="factSectionMeta(item.section).className"
                >
                  {{ factSectionMeta(item.section).label }}
                </span>
              </td>
              <td class="td max-w-[240px]">
                <p class="text-sm text-slate-800">{{ item.label || item.id }}</p>
                <p class="font-mono text-[11px] text-slate-500">{{ item.id }}</p>
              </td>
              <td class="td max-w-[220px] text-xs text-slate-700">
                {{ factExpected(item) }}
              </td>
              <td class="td">
                <span
                  class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                  :class="FACT_STATUS_META[factStatus(item)].className"
                >
                  {{ FACT_STATUS_META[factStatus(item)].label }}
                </span>
              </td>
              <td class="td max-w-[260px] text-[11px] text-slate-600">
                <p class="truncate" :title="item.evidence_ref ?? ''">
                  {{ item.evidence_ref || '无证据引用' }}
                </p>
                <p v-if="item.verification_note" class="truncate" :title="item.verification_note">
                  {{ item.verification_note }}
                </p>
              </td>
              <td class="td text-[11px] text-slate-600">
                <p>{{ item.verified_by || '—' }}</p>
                <p class="text-slate-500">{{ item.verified_at || '—' }}</p>
              </td>
              <td class="td">
                <div class="flex justify-end gap-1">
                  <button
                    v-if="auth.can('case:update')"
                    class="btn-ghost btn-xs"
                    :disabled="loading || factStatus(item) === 'VERIFIED'"
                    @click="openFactDialog(item, 'VERIFIED')"
                  >
                    通过
                  </button>
                  <button
                    v-if="auth.can('case:update')"
                    class="btn-ghost btn-xs"
                    :disabled="loading || factStatus(item) === 'WAIVED'"
                    @click="openFactDialog(item, 'WAIVED')"
                  >
                    豁免
                  </button>
                  <button
                    v-if="auth.can('case:update')"
                    class="btn-ghost btn-xs"
                    :disabled="loading || factStatus(item) === 'PENDING'"
                    @click="openFactDialog(item, 'PENDING')"
                  >
                    退回
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="!factRows.length">
              <td class="td text-xs text-slate-500" colspan="7">
                请选择契约用例，或该用例尚未配置答案包络。
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ------------------------------------------------------- 候选池 -->
    <div v-if="tab === 'candidates'" class="space-y-4">
      <div v-if="auth.can('candidate:triage')" class="card space-y-3 p-4">
        <div class="grid gap-2 sm:grid-cols-2">
          <input v-model="candidateForm.raw_question" class="input" placeholder="候选问题原文" />
          <select v-model="candidateForm.source" class="input">
            <SelectButton />
            <option value="MANUAL">手工录入</option>
            <option value="PRODUCTION">生产日志</option>
            <option value="TRACE">执行轨迹</option>
            <option value="LLM">模型生成</option>
          </select>
        </div>
        <input v-model="candidateForm.note" class="input" placeholder="备注（可留空）" />
        <div class="flex justify-end">
          <button class="btn-primary btn-xs" :disabled="loading" @click="createCandidate">
            登记候选
          </button>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <select v-model="candidateFilter.status" class="input w-36" @change="loadCandidates">
          <SelectButton />
          <option value="">全部状态</option>
          <option value="NEW">待分诊</option>
          <option value="TRIAGED">已分诊</option>
          <option value="PROMOTED">已转正</option>
          <option value="REJECTED">已拒绝</option>
        </select>
        <select v-model="candidateFilter.source" class="input w-36" @change="loadCandidates">
          <SelectButton />
          <option value="">全部来源</option>
          <option value="MANUAL">手工录入</option>
          <option value="PRODUCTION">生产日志</option>
          <option value="TRACE">执行轨迹</option>
          <option value="LLM">模型生成</option>
        </select>
        <span class="text-[11px] text-slate-500">共 {{ candidateTotal }} 条</span>
      </div>

      <div class="card overflow-hidden">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th">候选问题</th>
              <th class="th">来源</th>
              <th class="th">状态</th>
              <th class="th">关联用例</th>
              <th class="th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in candidates" :key="item.id">
              <td class="td max-w-[420px]">
                <p class="truncate text-sm text-slate-800" :title="item.raw_question">
                  {{ item.raw_question }}
                </p>
                <p v-if="item.note" class="text-[11px] text-slate-500">{{ item.note }}</p>
              </td>
              <td class="td text-xs text-slate-600">{{ enumLabel(CANDIDATE_SOURCE_LABELS, item.source) }}</td>
              <td class="td">
                <span
                  class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                  :class="candidateMeta(item.status).className"
                >
                  {{ candidateMeta(item.status).label }}
                </span>
              </td>
              <td class="td text-xs text-slate-600">{{ item.promoted_case_id ?? '—' }}</td>
              <td class="td">
                <div class="flex justify-end gap-1">
                  <button
                    v-if="auth.can('candidate:triage')"
                    class="btn-ghost btn-xs"
                    :disabled="loading || item.status !== 'NEW'"
                    @click="triageCandidate(item)"
                  >
                    分诊
                  </button>
                  <button
                    v-if="auth.can('candidate:promote')"
                    class="btn-ghost btn-xs"
                    :disabled="loading || item.status === 'PROMOTED' || item.status === 'REJECTED'"
                    @click="promoteCandidate(item)"
                  >
                    转正
                  </button>
                  <button
                    v-if="auth.can('candidate:triage')"
                    class="btn-ghost btn-xs"
                    :disabled="loading || item.status === 'REJECTED'"
                    @click="rejectCandidate(item)"
                  >
                    拒绝
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="!candidates.length">
              <td class="td text-xs text-slate-500" colspan="5">暂无候选</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ------------------------------------------------------- 判分器自检 -->
    <div v-if="tab === 'grader'" class="space-y-4">
      <div class="card flex flex-wrap items-center justify-between gap-3 p-4">
        <div>
          <p class="text-sm font-medium text-slate-800">内置变异集自检</p>
          <p class="mt-1 text-[11px] text-slate-500">
            对同一份正确用例做定点变异，必须全部识别，且正确基线不能被误杀。
          </p>
        </div>
        <button
          v-if="auth.can('grader:run')"
          class="btn-primary btn-xs"
          :disabled="loading"
          @click="runEvals"
        >
          运行自检
        </button>
      </div>

      <div v-if="lastEval" class="card grid gap-3 p-4 sm:grid-cols-4">
        <div>
          <p class="text-xs text-slate-500">判分器版本</p>
          <p class="mt-1 font-mono text-xs text-slate-800">{{ lastEval.grader_version }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">变异识别</p>
          <p class="mt-1 text-lg font-semibold text-emerald-600">
            {{ lastEval.detected }} / {{ lastEval.total }}
          </p>
        </div>
        <div>
          <p class="text-xs text-slate-500">误杀</p>
          <p class="mt-1 text-lg font-semibold text-rose-600">{{ lastEval.false_kills }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">运行时间</p>
          <p class="mt-1 text-xs text-slate-800">{{ formatDateTime(lastEval.run_at) }}</p>
        </div>
      </div>

      <div class="card overflow-hidden">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th">运行时间</th>
              <th class="th">版本</th>
              <th class="th">识别 / 总数</th>
              <th class="th">误杀</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in evals" :key="item.id">
              <td class="td text-xs text-slate-600">{{ formatDateTime(item.run_at) }}</td>
              <td class="td font-mono text-xs">{{ item.grader_version }}</td>
              <td class="td text-xs">{{ item.detected }} / {{ item.total }}</td>
              <td class="td text-xs" :class="item.false_kills ? 'text-rose-600' : 'text-slate-600'">
                {{ item.false_kills }}
              </td>
            </tr>
            <tr v-if="!evals.length">
              <td class="td text-xs text-slate-500" colspan="4">暂无自检记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ------------------------------------------------------- 业务口径 -->
    <div v-if="tab === 'calibers'" class="space-y-4">
      <div class="card space-y-3 p-4">
        <div class="grid gap-2 sm:grid-cols-2">
          <input
            v-model="caliberForm.key"
            class="input"
            :disabled="editingCaliberId !== null"
            placeholder="口径键，如 billing_period"
          />
          <input v-model="caliberForm.name" class="input" placeholder="口径名称" />
          <select v-model="caliberForm.canonical_source" class="input">
            <SelectButton />
            <option value="BUSINESS_UI">业务界面</option>
            <option value="BILLING_SYSTEM">计费系统</option>
            <option value="CRM">CRM</option>
            <option value="DATA_WAREHOUSE">数据仓库</option>
          </select>
          <input v-model="caliberForm.owner" class="input" placeholder="责任人（可留空）" />
        </div>
        <textarea
          v-model="caliberForm.definition"
          class="input min-h-16"
          placeholder="口径定义，如：账期按自然月，金额含税"
        />
        <div class="flex justify-end gap-2">
          <button
            v-if="editingCaliberId !== null"
            class="btn-ghost btn-xs"
            :disabled="loading"
            @click="resetCaliberForm"
          >
            取消编辑
          </button>
          <button
            v-if="auth.can('caliber:write')"
            class="btn-primary btn-xs"
            :disabled="loading"
            @click="submitCaliber"
          >
            {{ editingCaliberId !== null ? '保存修改' : '新增口径' }}
          </button>
        </div>
      </div>

      <div class="card overflow-hidden">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th">口径</th>
              <th class="th">基准来源</th>
              <th class="th">责任人</th>
              <th class="th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in calibers" :key="item.id">
              <td class="td">
                <p class="text-sm text-slate-800">{{ item.name }}</p>
                <p class="font-mono text-[11px] text-slate-500">{{ item.key }}</p>
                <p v-if="item.definition" class="text-[11px] text-slate-500">
                  {{ item.definition }}
                </p>
              </td>
              <td class="td text-xs text-slate-600">{{ enumLabel(CALIBER_SOURCE_LABELS, item.canonical_source) }}</td>
              <td class="td text-xs text-slate-600">{{ item.owner || '—' }}</td>
              <td class="td text-right">
                <div class="flex justify-end gap-1">
                  <button
                    v-if="auth.can('caliber:write')"
                    class="btn-ghost btn-xs"
                    :disabled="loading"
                    @click="startEditCaliber(item)"
                  >
                    编辑
                  </button>
                  <button
                    v-if="auth.can('caliber:write')"
                    class="btn-ghost btn-xs"
                    :disabled="loading"
                    @click="removeCaliber(item)"
                  >
                    删除
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="!calibers.length">
              <td class="td text-xs text-slate-500" colspan="4">暂无口径</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ------------------------------------------------------- 数据源 -->
    <div v-if="tab === 'sources'" class="space-y-4">
      <div class="card space-y-3 p-4">
        <div class="grid gap-2 sm:grid-cols-2">
          <input
            v-model="sourceForm.code"
            class="input"
            :disabled="editingSourceId !== null"
            placeholder="数据源编码，如 cypher-prod"
          />
          <input v-model="sourceForm.name" class="input" placeholder="数据源名称" />
          <select v-model="sourceForm.kind" class="input">
            <SelectButton />
            <option value="CYPHER_TOOL">Cypher 执行器工具</option>
            <option value="SQL_DB">只读 SQL</option>
            <option value="HTTP_API">业务查询接口</option>
          </select>
          <input v-model="sourceForm.endpoint_url" class="input" placeholder="Endpoint URL" />
          <input
            v-model.number="sourceForm.timeout_seconds"
            class="input"
            type="number"
            step="1"
            placeholder="超时秒数"
          />
          <input
            v-model.number="sourceForm.max_rows"
            class="input"
            type="number"
            step="1"
            placeholder="最大返回行数"
          />
        </div>
        <input v-model="sourceForm.description" class="input" placeholder="说明（可选）" />
        <textarea
          v-model="sourceForm.authText"
          class="input min-h-16 font-mono text-xs"
          :placeholder="
            editingSourceId !== null
              ? sourceForm.authText
                ? '鉴权配置 JSON（已回显原值，可直接修改）'
                : '鉴权配置 JSON（留空则保持不变）'
              : '鉴权配置 JSON（可选），支持 header / value 字段'
          "
        />
        <div class="flex justify-end gap-2">
          <button
            v-if="editingSourceId !== null"
            class="btn-ghost btn-xs"
            :disabled="loading"
            @click="resetSourceForm"
          >
            取消编辑
          </button>
          <button
            v-if="auth.can('capability:write')"
            class="btn-primary btn-xs"
            :disabled="loading"
            @click="submitSource"
          >
            {{ editingSourceId !== null ? '保存修改' : '新增数据源' }}
          </button>
        </div>
      </div>

      <div class="card overflow-hidden">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th">数据源</th>
              <th class="th">类型</th>
              <th class="th">探活</th>
              <th class="th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in sources" :key="item.id">
              <td class="td max-w-[360px]">
                <p class="text-sm text-slate-800">{{ item.name }}</p>
                <p class="font-mono text-[11px] text-slate-500">{{ item.code }}</p>
                <p class="truncate text-[11px] text-slate-500">{{ item.endpoint_url || '未配置地址' }}</p>
                <span
                  v-if="item.has_auth"
                  class="mt-1 inline-block rounded-full bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-700"
                >
                  已配置鉴权
                </span>
              </td>
              <td class="td text-xs text-slate-600">{{ enumLabel(DATA_SOURCE_KIND_LABELS, item.kind) }}</td>
              <td class="td text-xs">
                <span
                  class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                  :class="
                    item.last_probe_status === 'OK'
                      ? 'bg-emerald-50 text-emerald-700'
                      : item.last_probe_status
                        ? 'bg-rose-50 text-rose-700'
                        : 'bg-slate-100 text-slate-600'
                  "
                >
                  {{ item.last_probe_status ? enumLabel(PROBE_STATUS_LABELS, item.last_probe_status) : '未探活' }}
                </span>
                <p v-if="item.last_probe_latency_ms !== null" class="mt-1 text-[11px] text-slate-500">
                  {{ item.last_probe_latency_ms }} ms
                </p>
              </td>
              <td class="td">
                <div class="flex justify-end gap-1">
                  <button
                    v-if="auth.can('capability:write')"
                    class="btn-ghost btn-xs"
                    :disabled="loading"
                    @click="startEditSource(item)"
                  >
                    编辑
                  </button>
                  <button
                    v-if="auth.can('capability:execute')"
                    class="btn-ghost btn-xs"
                    :disabled="loading"
                    @click="probeSource(item)"
                  >
                    探活
                  </button>
                  <button
                    v-if="auth.can('capability:write')"
                    class="btn-ghost btn-xs"
                    :disabled="loading"
                    @click="toggleSource(item)"
                  >
                    {{ item.is_active ? '停用' : '启用' }}
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="!sources.length">
              <td class="td text-xs text-slate-500" colspan="4">暂无数据源</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ------------------------------------------------------- 取数能力 -->
    <div v-if="tab === 'capabilities'" class="space-y-4">
      <div class="card space-y-3 p-4">
        <div class="grid gap-2 sm:grid-cols-2">
          <input
            v-model="capabilityForm.key"
            class="input"
            :disabled="editingCapabilityId !== null"
            placeholder="能力键，如 bill_order_detail"
          />
          <input v-model="capabilityForm.name" class="input" placeholder="能力名称" />
          <select v-model.number="capabilityForm.source_id" class="input">
            <SelectButton />
            <option :value="null">未绑定数据源</option>
            <option v-for="item in sources" :key="item.id" :value="item.id">
              {{ item.name }}（{{ item.code }}）
            </option>
          </select>
          <input
            v-model="capabilityForm.param_binding"
            class="input"
            placeholder='参数映射 JSON，如 {"order_no":"order_no"}'
          />
        </div>
        <textarea
          v-model="capabilityForm.query_text"
          class="input min-h-20 font-mono text-xs"
          placeholder="只读 Cypher 模板，如 MATCH (b:Bill {order_no: $order_no}) RETURN b.amount AS amount"
        />
        <input v-model="capabilityForm.description" class="input" placeholder="能力说明（可选）" />
        <div class="flex justify-end gap-2">
          <button
            v-if="editingCapabilityId !== null"
            class="btn-ghost btn-xs"
            :disabled="loading"
            @click="resetCapabilityForm"
          >
            取消编辑
          </button>
          <button
            v-if="auth.can('capability:write')"
            class="btn-primary btn-xs"
            :disabled="loading"
            @click="submitCapability"
          >
            {{ editingCapabilityId !== null ? '保存修改' : '新增能力' }}
          </button>
        </div>
      </div>

      <div class="card overflow-hidden">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th">能力</th>
              <th class="th">版本</th>
              <th class="th">状态</th>
              <th class="th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in capabilities" :key="item.id">
              <td class="td max-w-[420px]">
                <p class="text-sm text-slate-800">{{ item.name }}</p>
                <p class="font-mono text-[11px] text-slate-500">{{ item.key }}</p>
                <p class="truncate font-mono text-[11px] text-slate-500">
                  {{ item.query_text || '未配置查询模板' }}
                </p>
              </td>
              <td class="td text-xs">v{{ item.version }}</td>
              <td class="td">
                <span
                  class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                  :class="
                    item.status === 'CERTIFIED'
                      ? 'bg-emerald-50 text-emerald-700'
                      : 'bg-amber-50 text-amber-700'
                  "
                >
                  {{ enumLabel(CAPABILITY_STATUS_LABELS, item.status) }}
                </span>
              </td>
              <td class="td text-right">
                <div class="flex justify-end gap-1">
                  <button
                    v-if="auth.can('capability:write')"
                    class="btn-ghost btn-xs"
                    :disabled="loading"
                    @click="startEditCapability(item)"
                  >
                    编辑
                  </button>
                  <button
                    v-if="auth.can('capability:write')"
                    class="btn-ghost btn-xs"
                    :disabled="loading || item.status === 'CERTIFIED'"
                    @click="certifyCapability(item)"
                  >
                    认证
                  </button>
                  <button
                    v-if="auth.can('capability:write')"
                    class="btn-ghost btn-xs"
                    :disabled="loading"
                    @click="removeCapability(item)"
                  >
                    删除
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="!capabilities.length">
              <td class="td text-xs text-slate-500" colspan="4">暂无能力</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card space-y-3 p-4">
        <p class="text-sm font-medium text-slate-800">执行取证（生成 GoldQuery）</p>
        <div class="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <select v-model.number="evidenceForm.capability_id" class="input">
            <SelectButton />
            <option :value="null">选择能力</option>
            <option v-for="item in capabilities" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
          <input
            v-model.number="evidenceForm.case_id"
            class="input"
            type="number"
            placeholder="用例 ID"
          />
          <input v-model="evidenceForm.factIdsText" class="input" placeholder="事实 ID，逗号分隔" />
          <button
            v-if="auth.can('capability:execute')"
            class="btn-primary btn-xs"
            :disabled="loading"
            @click="runEvidence"
          >
            执行取证
          </button>
        </div>
        <textarea
          v-model="evidenceForm.paramsText"
          class="input min-h-16 font-mono text-xs"
          placeholder='绑定参数 JSON，如 {"order_no": "A2024001"}'
        />
        <pre
          v-if="lastEvidence"
          class="max-h-64 overflow-auto rounded-lg bg-slate-900 p-3 text-[11px] text-slate-100"
        >{{ JSON.stringify(lastEvidence, null, 2) }}</pre>
      </div>

      <div class="card overflow-hidden">
        <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
          <div>
            <p class="text-sm font-medium text-slate-800">取证记录（GoldQuery）</p>
            <p class="mt-0.5 text-[11px] text-slate-500">
              共 {{ goldQueryTotal }} 条，展示最近 50 条；查询结果受数据源最大行数限制。
            </p>
          </div>
          <button class="btn-ghost btn-xs" :disabled="loading" @click="refreshGoldQueries">刷新</button>
        </div>
        <div class="overflow-x-auto">
          <table class="table-shell">
            <thead>
              <tr>
                <th class="th">用例</th>
                <th class="th">能力</th>
                <th class="th">来源</th>
                <th class="th">行数</th>
                <th class="th">执行时间</th>
                <th class="th text-right">详情</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="item in goldQueries" :key="item.id">
                <tr>
                  <td class="td text-xs text-slate-700">#{{ item.case_id }}</td>
                  <td class="td max-w-[260px] text-xs">
                    <p class="truncate text-slate-800" :title="capabilityName(item.capability_id)">
                      {{ capabilityName(item.capability_id) }}
                    </p>
                    <p class="font-mono text-[11px] text-slate-500">
                      v{{ item.capability_version ?? '—' }}
                    </p>
                  </td>
                  <td class="td text-xs text-slate-600">
                    {{ enumLabel(GOLD_ORIGIN_LABELS, item.origin) }}
                  </td>
                  <td class="td text-xs">
                    {{ item.row_count }}
                    <span v-if="item.truncated" class="text-amber-600">（已截断）</span>
                  </td>
                  <td class="td text-xs text-slate-600">{{ formatDateTime(item.executed_at) }}</td>
                  <td class="td text-right">
                    <button
                      class="btn-ghost btn-xs"
                      @click="expandedGoldId = expandedGoldId === item.id ? null : item.id"
                    >
                      {{ expandedGoldId === item.id ? '收起' : '查看' }}
                    </button>
                  </td>
                </tr>
                <tr v-if="expandedGoldId === item.id">
                  <td class="td bg-slate-50" colspan="6">
                    <div class="grid gap-3 lg:grid-cols-2">
                      <div>
                        <p class="text-[11px] font-medium text-slate-600">查询模板</p>
                        <pre class="mt-1 max-h-48 overflow-auto rounded-lg bg-slate-900 p-3 text-[11px] text-slate-100">{{ item.query_text || '—' }}</pre>
                      </div>
                      <div>
                        <p class="text-[11px] font-medium text-slate-600">绑定参数 / 结果行</p>
                        <pre class="mt-1 max-h-48 overflow-auto rounded-lg bg-slate-900 p-3 text-[11px] text-slate-100">{{ JSON.stringify({ params: item.template_params, columns: item.columns, rows: item.rows }, null, 2) }}</pre>
                      </div>
                    </div>
                  </td>
                </tr>
              </template>
              <tr v-if="!goldQueries.length">
                <td class="td text-xs text-slate-500" colspan="6">暂无取证记录</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <BaseModal
      v-model="factDialogOpen"
      :title="FACT_ACTION_META[factAction].title"
      :subtitle="factTarget ? `${factTarget.label || factTarget.id} · ${factTarget.id}` : ''"
      width="md"
    >
      <div class="space-y-3">
        <p class="text-xs text-slate-500">{{ FACT_ACTION_META[factAction].hint }}</p>
        <label v-if="factAction !== 'PENDING'" class="space-y-1">
          <span class="text-[11px] text-slate-500">
            {{ factAction === 'WAIVED' ? '豁免原因（必填）' : '证据引用（可选）' }}
          </span>
          <textarea
            v-if="factAction === 'WAIVED'"
            v-model="factNote"
            class="input min-h-20"
            placeholder="如：历史数据不可回溯，由业务确认豁免"
          />
          <input
            v-else
            v-model="factEvidence"
            class="input"
            placeholder="如 gold_query:12、capability:bill_order_detail"
          />
        </label>
        <label v-if="factAction === 'VERIFIED'" class="space-y-1">
          <span class="text-[11px] text-slate-500">备注（可选）</span>
          <input v-model="factNote" class="input" placeholder="补充说明" />
        </label>
        <div class="rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-600">
          当前状态：
          <span class="font-medium">{{ factStatus(factTarget ?? ({} as FactAtomView)) }}</span>
          · 操作人将记录为当前登录账号，并写入用例修改历史。
        </div>
      </div>
      <template #footer>
        <button class="btn-ghost btn-sm" :disabled="loading" @click="factDialogOpen = false">
          取消
        </button>
        <button
          v-if="auth.can('case:update')"
          class="btn-primary btn-sm"
          :disabled="loading"
          @click="submitFactDialog"
        >
          {{ FACT_ACTION_META[factAction].confirm }}
        </button>
      </template>
    </BaseModal>
  </div>
</template>
