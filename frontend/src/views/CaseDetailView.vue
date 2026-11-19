<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import BaseModal from '../components/BaseModal.vue'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import TestCaseDrawer from './components/TestCaseDrawer.vue'
import CaseRevisionPanel from './components/CaseRevisionPanel.vue'
import CaseTrendPanel from './components/CaseTrendPanel.vue'
import RunComparePanel from './components/RunComparePanel.vue'
import type {
  CaseExecutionItem,
  CasePayload,
  CaseRevision,
  CaseTrend,
  Environment,
  FactAtom,
  ModelConfig,
  PromptListItem,
  TaskProgressEvent,
  TestCase,
} from '../types'
import { useConfirm } from '../composables/useConfirm'
import { useTaskEventStream } from '../composables/useTaskEventStream'
import { errorMessage } from '../utils/errors'
import {
  caseTypeLabel,
  formatDateTime,
  formatMs,
  formatPercent,
  formatScore,
  safeJson,
  sourceLabel,
  statusLabel,
  truncate,
} from '../utils/format'
import { useDebugStore } from '../stores/debug'
import { useToastStore } from '../stores/toast'

interface StatCard {
  label: string
  value: string
  hint: string
  warn?: boolean
}

interface InfoRow {
  label: string
  value: string
  mono?: boolean
}

const route = useRoute()
const router = useRouter()
const toast = useToastStore()
const confirm = useConfirm()
const debugStore = useDebugStore()

const caseId = computed(() => Number(route.params.id))

const item = ref<TestCase | null>(null)
const trend = ref<CaseTrend | null>(null)
const executions = ref<CaseExecutionItem[]>([])
const executionTotal = ref(0)
const executionPage = ref(1)
const executionPageSize = 20
const executionLoading = ref(false)
const revisions = ref<CaseRevision[]>([])
const environments = ref<Environment[]>([])
const templates = ref<PromptListItem[]>([])
const models = ref<ModelConfig[]>([])

const loading = ref(true)
const loadError = ref('')
const saving = ref(false)

const drawerOpen = ref(false)
const revisionOpen = ref(false)
const trendOpen = ref(false)
const compareOpen = ref(false)

const reviewOpen = ref(false)
const reviewNote = ref('')
const reviewSubmitting = ref(false)
const freezeOpen = ref(false)
const freezeVersion = ref('v1')
const freezing = ref(false)

const menuRef = ref<HTMLDetailsElement | null>(null)

const REVIEW_STATUS_META: Record<string, { label: string; className: string }> = {
  NOT_REQUIRED: { label: '无需评审', className: 'bg-slate-100 text-slate-600' },
  PENDING: { label: '待评审', className: 'bg-amber-50 text-amber-700' },
  APPROVED: { label: '已通过', className: 'bg-emerald-50 text-emerald-700' },
  REJECTED: { label: '已驳回', className: 'bg-rose-50 text-rose-700' },
}

const REVISION_META: Record<string, { label: string; className: string }> = {
  CREATE: { label: '创建', className: 'bg-emerald-50 text-emerald-700' },
  UPDATE: { label: '更新', className: 'bg-indigo-50 text-indigo-700' },
  IMPORT: { label: '导入', className: 'bg-sky-50 text-sky-700' },
  ROLLBACK: { label: '回滚', className: 'bg-amber-50 text-amber-700' },
}

const FACT_STATUS_META: Record<string, { label: string; className: string }> = {
  VERIFIED: { label: '已取证', className: 'bg-emerald-50 text-emerald-700' },
  WAIVED: { label: '已豁免', className: 'bg-slate-100 text-slate-600' },
  CONFLICT: { label: '口径冲突', className: 'bg-rose-50 text-rose-700' },
  NO_EVIDENCE: { label: '无证据', className: 'bg-rose-50 text-rose-700' },
  STALE: { label: '已过期', className: 'bg-amber-50 text-amber-700' },
  PENDING: { label: '待取证', className: 'bg-amber-50 text-amber-700' },
}

const INPUT_TYPE_LABELS: Record<string, string> = {
  CLEAR: '明确问题',
  UNDERSPECIFIED: '信息不足',
  AMBIGUOUS_REF: '指代不明',
  MULTI_INTENT: '多意图',
  VAGUE: '模糊表达',
  UNANSWERABLE: '不可回答',
  CONTRADICTORY: '前后矛盾',
  UNAUTHORIZED: '越权请求',
}

const RISK_LABELS: Record<string, string> = { LOW: '低', HIGH: '高' }

function reviewMeta(status?: string | null): { label: string; className: string } {
  return REVIEW_STATUS_META[status || 'NOT_REQUIRED'] ?? REVIEW_STATUS_META.NOT_REQUIRED
}

function revisionMeta(changeType?: string | null): { label: string; className: string } {
  return REVISION_META[changeType || 'UPDATE'] ?? REVISION_META.UPDATE
}

function factStatusMeta(status?: string | null): { label: string; className: string } {
  return FACT_STATUS_META[(status || 'PENDING').toUpperCase()] ?? FACT_STATUS_META.PENDING
}

function factValue(fact: FactAtom): string {
  if (fact.values?.length) return fact.values.join('、')
  if (fact.value === null || fact.value === undefined || fact.value === '') return '—'
  return `${fact.value}${fact.unit ? ` ${fact.unit}` : ''}`
}

function canReview(current: TestCase): boolean {
  return (
    current.grading_mode === 'ANSWER_CONTRACT' ||
    (!!current.review_status && current.review_status !== 'NOT_REQUIRED')
  )
}

const canFreeze = computed(
  () => item.value?.grading_mode === 'ANSWER_CONTRACT' && item.value.review_status === 'APPROVED',
)

const mustFacts = computed(() => item.value?.answer_envelope?.must ?? [])
const pendingFacts = computed(() =>
  mustFacts.value.filter((fact) => {
    const status = String(fact.verification_status ?? 'PENDING').toUpperCase()
    return status !== 'VERIFIED' && status !== 'WAIVED'
  }),
)
const verifiedCount = computed(() => mustFacts.value.length - pendingFacts.value.length)
const factProgress = computed(() =>
  mustFacts.value.length ? Math.round((verifiedCount.value / mustFacts.value.length) * 100) : 0,
)

const statCards = computed<StatCard[]>(() => [
  {
    label: '通过率',
    value: trend.value ? formatPercent(trend.value.pass_rate, 1) : '—',
    hint: `基于最近 ${trend.value?.points.length ?? 0} 次执行`,
    warn: false,
  },
  {
    label: '平均分',
    value: formatScore(trend.value?.avg_score),
    hint: item.value?.grading_mode === 'ANSWER_CONTRACT' ? '答案契约评分' : '4 维加权评分',
    warn: false,
  },
  {
    label: '平均耗时',
    value: formatMs(trend.value?.avg_latency_ms),
    hint: '端到端执行时延',
    warn: false,
  },
  {
    label: '执行次数',
    value: String(executionTotal.value),
    hint: trend.value?.flaky ? '稳定性：波动 / Flaky' : '稳定性：稳定',
    warn: !!trend.value?.flaky,
  },
])

const baseInfo = computed<InfoRow[]>(() => {
  const current = item.value
  if (!current) return []
  return [
    { label: '用例编号', value: current.code, mono: true },
    { label: '用例类型', value: caseTypeLabel(current.case_type) },
    { label: '状态', value: statusLabel(current.status) },
    { label: '优先级', value: current.priority },
    { label: '负责人', value: current.owner || '—' },
    { label: '当前版本', value: `v${current.current_version}`, mono: true },
    { label: '创建', value: `${current.created_by || '—'} · ${formatDateTime(current.created_at)}` },
    { label: '最近更新', value: `${current.updated_by || '—'} · ${formatDateTime(current.updated_at)}` },
  ]
})

function modelName(id?: number | null): string {
  if (!id) return '—'
  const model = models.value.find((entry) => entry.id === id)
  return model ? model.name : `#${id}`
}

const runInfo = computed<InfoRow[]>(() => {
  const current = item.value
  if (!current) return []
  const simulatorVersion =
    current.simulator_template_version !== null && current.simulator_template_version !== undefined
      ? ` · v${current.simulator_template_version}`
      : ''
  const judgeVersion =
    current.judge_template_version !== null && current.judge_template_version !== undefined
      ? ` · v${current.judge_template_version}`
      : ''
  const judgeModels = (current.judge_model_ids ?? []).map((id) => modelName(id))
  return [
    { label: '执行环境', value: current.environment_name || '未绑定环境' },
    {
      label: '模拟器模板',
      value: `${current.simulator_template_name || '未指定'} · ${statusLabel(current.simulator_template_strategy)}${simulatorVersion}`,
    },
    { label: '模拟器模型', value: '全局唯一启用' },
    {
      label: '裁判模板',
      value: `${current.judge_template_name || '未指定'} · ${statusLabel(current.judge_template_strategy)}${judgeVersion}`,
    },
    { label: '裁判模型', value: judgeModels.length ? judgeModels.join('、') : '按角色默认' },
    { label: '裁判策略', value: statusLabel(current.judge_strategy) },
    {
      label: '提示词覆盖',
      value: current.prompt_overrides
        ? `${Object.keys(current.prompt_overrides).length} 项自定义`
        : '无',
    },
  ]
})

const gradingInfo = computed<InfoRow[]>(() => {
  const current = item.value
  if (!current) return []
  return [
    {
      label: '判分方式',
      value: current.grading_mode === 'ANSWER_CONTRACT' ? '答案契约' : '4 维评分',
    },
    {
      label: '通过阈值',
      value:
        current.pass_threshold === null || current.pass_threshold === undefined
          ? '按默认'
          : String(current.pass_threshold),
    },
    {
      label: '风险等级',
      value: RISK_LABELS[current.risk_level || 'LOW'] ?? current.risk_level ?? '—',
    },
    {
      label: '输入类型',
      value: current.input_type ? INPUT_TYPE_LABELS[current.input_type] ?? current.input_type : '—',
    },
    {
      label: '数据集版本',
      value: current.dataset_version || '未冻结',
      mono: !!current.dataset_version,
    },
    { label: '评审人', value: current.reviewed_by || '—' },
  ]
})

const recentRevisions = computed(() => revisions.value.slice(0, 6))
const executionPageCount = computed(() =>
  Math.max(1, Math.ceil(executionTotal.value / executionPageSize)),
)
const ACTIVE_EXECUTION_STATUSES = new Set(['PENDING', 'RUNNING'])
/** 当前页存在执行中的记录时，展示实时更新标识（列表由 WS 事件驱动刷新）。 */
const hasActiveExecution = computed(() =>
  executions.value.some((run) => ACTIVE_EXECUTION_STATUSES.has(run.status)),
)

const reviewActionLabel = computed(() => {
  if (item.value?.review_status === 'APPROVED') return '复审'
  if (item.value?.review_status === 'REJECTED') return '重新评审'
  return '评审'
})

async function loadOptions(): Promise<void> {
  try {
    const [envPage, templatePage, modelPage] = await Promise.all([
      api.environments.list({ page_size: 200 }),
      api.prompts.list({ page_size: 200 }),
      api.models.list({ page_size: 200 }),
    ])
    environments.value = envPage.items
    templates.value = templatePage.items
    models.value = modelPage.items
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function refreshFromButton(): Promise<void> {
  await refresh()
  if (!loadError.value) toast.success('刷新成功')
}

async function refresh(): Promise<void> {
  if (!Number.isFinite(caseId.value) || caseId.value <= 0) {
    item.value = null
    loading.value = false
    loadError.value = '用例不存在或已删除'
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    item.value = await api.cases.get(caseId.value)
    const [trendResult, executionResult, revisionResult] = await Promise.allSettled([
      api.cases.trend(caseId.value),
      api.cases.executions(caseId.value, {
        page: executionPage.value,
        page_size: executionPageSize,
      }),
      api.cases.revisions(caseId.value),
    ])
    trend.value = trendResult.status === 'fulfilled' ? trendResult.value : null
    if (executionResult.status === 'fulfilled') {
      executions.value = executionResult.value.items
      executionTotal.value = executionResult.value.total
    } else {
      executions.value = []
      executionTotal.value = 0
    }
    revisions.value = revisionResult.status === 'fulfilled' ? revisionResult.value : []
  } catch (exc) {
    item.value = null
    executions.value = []
    revisions.value = []
    trend.value = null
    loadError.value = errorMessage(exc)
    toast.error(loadError.value)
  } finally {
    loading.value = false
  }
}

function closeMenu(): void {
  if (menuRef.value) menuRef.value.open = false
}

function runMenuAction(action: () => unknown): void {
  closeMenu()
  void action()
}

function onDocumentClick(event: MouseEvent): void {
  const menu = menuRef.value
  if (menu?.open && !menu.contains(event.target as Node)) closeMenu()
}

function openEdit(): void {
  if (!item.value) return
  drawerOpen.value = true
}

async function submitCase(payload: CasePayload): Promise<void> {
  if (!item.value) return
  saving.value = true
  try {
    const body: Partial<CasePayload> = { ...payload }
    body.code = undefined
    body.case_type = undefined
    const updated = await api.cases.update(item.value.id, body)
    toast.success(`用例 ${updated.code} 已更新至 v${updated.current_version}`)
    drawerOpen.value = false
    await refresh()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

/** 悬停「单条调试」时浮出的扩展入口：开启失败追问自检的单条调试（省去常驻开关占位） */
const debugMenuOpen = ref(false)

function closeDebugMenuOnBlur(event: FocusEvent): void {
  const next = event.relatedTarget as Node | null
  const root = event.currentTarget as HTMLElement
  if (!next || !root.contains(next)) debugMenuOpen.value = false
}

/** 执行记录中的追问自检状态标签 */
function selfCheckBadge(run: CaseExecutionItem): { label: string; className: string } {
  if (run.self_check_status === 'DONE') {
    return { label: '已追问', className: 'bg-emerald-50 text-emerald-700' }
  }
  if (run.self_check_status === 'ERROR') {
    return { label: '追问失败', className: 'bg-rose-50 text-rose-700' }
  }
  if (run.self_check_status === 'SKIPPED') {
    return {
      label: run.pass_flag ? '未触发（通过）' : '自检未触发',
      className: 'bg-slate-100 text-slate-500',
    }
  }
  return { label: run.self_check_status ?? '—', className: 'bg-slate-100 text-slate-500' }
}

function debugCase(enableSelfCheck = false): void {
  const current = item.value
  if (!current || debugStore.isRunning(current.id)) return
  // 请求生命周期归全局 debug store：跨页面保持「调试中」防重复发起；
  // 完成时仍在本页 → 自动跳转 trace；已离开 → 底部胶囊常驻提示
  void debugStore.run({
    id: current.id,
    code: current.code,
    environmentId: current.environment_id,
    enableSelfCheck,
  })
}

function openReview(): void {
  if (!item.value) return
  reviewNote.value = ''
  reviewOpen.value = true
}

async function submitReview(targetStatus: 'APPROVED' | 'REJECTED' | 'PENDING'): Promise<void> {
  const current = item.value
  if (!current) return
  const note = reviewNote.value.trim()
  if (targetStatus === 'REJECTED' && !note) {
    toast.error('驳回必须填写原因，便于用例负责人整改与追溯')
    return
  }
  if (targetStatus === 'APPROVED' && pendingFacts.value.length > 0) {
    toast.error(`仍有 ${pendingFacts.value.length} 条 Must 事实未取证，无法评审通过`)
    return
  }
  reviewSubmitting.value = true
  try {
    const updated = await api.cases.review(current.id, {
      review_status: targetStatus,
      note: note || undefined,
    })
    toast.success(
      targetStatus === 'APPROVED'
        ? `用例 ${updated.code} 评审通过`
        : targetStatus === 'REJECTED'
          ? `用例 ${updated.code} 已驳回`
          : `用例 ${updated.code} 已退回待评审`,
    )
    reviewOpen.value = false
    await refresh()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    reviewSubmitting.value = false
  }
}

function openFreeze(): void {
  if (!item.value) return
  freezeVersion.value = item.value.dataset_version || 'v1'
  freezeOpen.value = true
}

async function submitFreeze(): Promise<void> {
  const current = item.value
  if (!current) return
  freezing.value = true
  try {
    const version = freezeVersion.value.trim() || 'v1'
    const updated = await api.cases.freeze(current.id, { dataset_version: version })
    toast.success(`用例 ${updated.code} 已冻结进数据集 ${updated.dataset_version || version}`)
    freezeOpen.value = false
    await refresh()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    freezing.value = false
  }
}

async function removeCase(): Promise<void> {
  const current = item.value
  if (!current) return
  const ok = await confirm({
    title: '删除用例',
    message: `确定删除用例 ${current.code} 吗？`,
    detail: '删除为软删除，历史执行记录会保留。',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  try {
    const result = await api.cases.remove(current.id)
    toast.success(result.message)
    await router.replace({ name: 'cases' })
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function exportCase(): Promise<void> {
  const current = item.value
  if (!current) return
  try {
    await api.cases.export('json', { ids: [current.id] })
    toast.success(`已导出用例 ${current.code}`)
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function loadExecutions(silent = false): Promise<void> {
  if (!item.value) return
  if (!silent) executionLoading.value = true
  try {
    const pageData = await api.cases.executions(item.value.id, {
      page: executionPage.value,
      page_size: executionPageSize,
    })
    executions.value = pageData.items
    executionTotal.value = pageData.total
  } catch (exc) {
    if (!silent) toast.error(errorMessage(exc))
  } finally {
    if (!silent) executionLoading.value = false
  }
}

// 历史记录由 WebSocket 事件驱动刷新：只处理当前用例的执行事件，并做节流。
let liveReloadTimer: number | null = null
function applyExecutionEvent(event: TaskProgressEvent): void {
  if (!event || event.type === 'ping') return
  const execution = event.execution
  if (!execution || execution.case_id !== item.value?.id) return
  if (liveReloadTimer !== null) return
  liveReloadTimer = window.setTimeout(() => {
    liveReloadTimer = null
    void loadExecutions(true)
  }, 600)
}

useTaskEventStream(applyExecutionEvent, { onOpen: () => void loadExecutions(true) })

function changeExecutionPage(next: number): void {
  if (next < 1 || next > executionPageCount.value || next === executionPage.value) return
  executionPage.value = next
  void loadExecutions()
}

async function onTrendViewAll(): Promise<void> {
  trendOpen.value = false
  await nextTick()
  document.getElementById('case-executions')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function openTrace(executionId: number): void {
  trendOpen.value = false
  void router.push({
    name: 'trace',
    query: { execution: String(executionId), from: 'case' },
  })
}

async function onRevisionUpdated(): Promise<void> {
  await refresh()
}

watch(
  caseId,
  () => {
    executionPage.value = 1
    void refresh()
  },
  { immediate: true },
)

onMounted(() => {
  void loadOptions()
  document.addEventListener('mousedown', onDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentClick)
  if (liveReloadTimer !== null) {
    window.clearTimeout(liveReloadTimer)
    liveReloadTimer = null
  }
})
</script>

<template>
  <div class="space-y-4">
    <button
      class="inline-flex items-center gap-1 text-xs text-slate-500 transition hover:text-ink"
      @click="router.push({ name: 'cases' })"
    >
      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="m15 18-6-6 6-6" />
      </svg>
      返回用例列表
    </button>

    <PageHeader :eyebrow="item ? `Assets · 用例详情 · ${item.code}` : 'Assets · 用例详情'">
      <template #title>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <h1 class="page-title">{{ item?.name || '用例详情' }}</h1>
          <StatusPill v-if="item" :status="item.status" size="sm" />
          <span
            v-if="item && item.review_status && item.review_status !== 'NOT_REQUIRED'"
            class="rounded-full px-2 py-0.5 text-[11px] font-medium"
            :class="reviewMeta(item.review_status).className"
            :title="item.reviewed_by ? `评审人：${item.reviewed_by}` : undefined"
          >
            {{ reviewMeta(item.review_status).label }}
          </span>
        </div>
      </template>

      <template #sub>
        <p class="page-sub">
          {{ item?.description || '用例详情、执行记录与版本变更一目了然。' }}
        </p>
        <div v-if="item?.tags?.length" class="mt-2 flex flex-wrap items-center gap-1.5">
          <span class="text-[11px] text-slate-500">标签</span>
          <span v-for="tag in item.tags" :key="tag" class="tag">{{ tag }}</span>
        </div>
      </template>

      <template #actions>
        <button class="btn-ghost" :disabled="!item" @click="refreshFromButton">刷新</button>
        <button class="btn-ghost" :disabled="!item" @click="openEdit">编辑</button>
        <div
          class="relative inline-flex"
          @mouseenter="debugMenuOpen = true"
          @mouseleave="debugMenuOpen = false"
          @focusin="debugMenuOpen = true"
          @focusout="closeDebugMenuOnBlur"
        >
          <button
            class="btn-primary"
            :disabled="!item || debugStore.isRunning(caseId)"
            @click="debugMenuOpen = false; debugCase()"
          >
            {{ debugStore.isRunning(caseId) ? '调试中…' : '单条调试' }}
          </button>
          <!-- 悬停/键盘聚焦时浮出扩展入口：开启失败追问自检的单条调试（省去常驻开关占位） -->
          <div
            class="absolute left-0 top-full z-30 pt-2 transition duration-150"
            :class="
              debugMenuOpen
                ? 'visible translate-y-0 opacity-100'
                : 'invisible -translate-y-1 opacity-0'
            "
          >
            <!-- 与「单条调试」同圆角 / 同字号 / 同色系阴影，仅降为描边形态区分主次 -->
            <button
              type="button"
              class="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-xl border border-brand/40 bg-white px-3 py-2 text-sm font-medium text-brand shadow-[0_10px_24px_-16px_rgba(79,70,229,0.9)] transition duration-150 hover:border-brand hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-45"
              :disabled="!item || debugStore.isRunning(caseId)"
              title="LLM 裁决失败时，在同一会话追问 Agent 自检原因（多一轮 Agent 调用）"
              @click="debugMenuOpen = false; debugCase(true)"
            >
              失败追问自检调试
            </button>
          </div>
        </div>
        <details ref="menuRef" class="relative">
          <summary
            class="btn-ghost cursor-pointer list-none select-none [&::-webkit-details-marker]:hidden"
          >
            更多操作
            <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="m6 9 6 6 6-6" />
            </svg>
          </summary>
          <div
            class="absolute right-0 z-30 mt-2 w-48 rounded-xl border border-line bg-white p-1 shadow-tile"
          >
            <button
              class="menu-item"
              @click="runMenuAction(() => (revisionOpen = true))"
            >
              版本历史
              <span class="ml-auto text-[10px] text-slate-400">{{ revisions.length }} 个版本</span>
            </button>
            <button class="menu-item" @click="runMenuAction(() => (trendOpen = true))">
              历史趋势
            </button>
            <button class="menu-item" @click="runMenuAction(() => (compareOpen = true))">
              运行对比
            </button>
            <button
              v-if="item && canReview(item)"
              class="menu-item"
              @click="runMenuAction(openReview)"
            >
              {{ reviewActionLabel }}
            </button>
            <button v-if="canFreeze" class="menu-item" @click="runMenuAction(openFreeze)">
              冻结进数据集
            </button>
            <button class="menu-item" @click="runMenuAction(exportCase)">导出 JSON</button>
            <div class="my-1 h-px bg-line-soft" />
            <button
              class="menu-item text-rose-600 hover:bg-rose-50 hover:text-rose-700"
              @click="runMenuAction(removeCase)"
            >
              删除用例
            </button>
          </div>
        </details>
      </template>
    </PageHeader>

    <div v-if="loading" class="card px-6 py-16 text-center text-sm text-slate-500">
      加载用例详情…
    </div>

    <div v-else-if="loadError || !item" class="card">
      <EmptyState
        title="用例不存在或已删除"
        :description="loadError || '请返回用例列表重新选择目标用例。'"
      >
        <button class="btn-primary btn-xs mt-2" @click="router.replace({ name: 'cases' })">
          返回用例列表
        </button>
      </EmptyState>
    </div>

    <template v-else>
      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div v-for="stat in statCards" :key="stat.label" class="card px-4 py-3">
          <p class="text-[11px] text-slate-500">{{ stat.label }}</p>
          <p class="mt-1 text-xl font-semibold leading-tight text-ink">{{ stat.value }}</p>
          <p class="mt-0.5 text-[11px]" :class="stat.warn ? 'text-amber-600' : 'text-slate-400'">
            {{ stat.hint }}
          </p>
        </div>
      </div>

      <div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div class="space-y-4">
          <!-- 用例内容 -->
          <section class="card">
            <header class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
              <div>
                <h2 class="panel-title">用例内容</h2>
                <p class="mt-0.5 text-[11px] text-slate-500">
                  {{ caseTypeLabel(item.case_type) }} · 当前版本 v{{ item.current_version }}
                </p>
              </div>
              <button class="btn-ghost btn-xs" @click="openEdit">编辑内容</button>
            </header>

            <div class="space-y-4 px-4 py-4">
              <template v-if="item.case_type === 'SINGLE'">
                <div>
                  <p class="label">用户提问</p>
                  <p
                    class="mt-1 whitespace-pre-wrap rounded-xl border border-line-soft bg-canvas px-3 py-2 text-sm text-slate-800"
                  >
                    {{ item.query || '—' }}
                  </p>
                </div>
                <div>
                  <p class="label">期望答案</p>
                  <p
                    class="mt-1 whitespace-pre-wrap rounded-xl border border-line-soft bg-canvas px-3 py-2 text-sm text-slate-800"
                  >
                    {{ item.expected_answer || '—' }}
                  </p>
                </div>
                <details
                  v-if="item.expected_rules"
                  class="rounded-xl border border-line-soft bg-canvas"
                >
                  <summary
                    class="cursor-pointer list-none px-3 py-2 text-xs font-medium text-slate-600 [&::-webkit-details-marker]:hidden"
                  >
                    期望规则 JSON
                  </summary>
                  <pre
                    class="max-h-64 overflow-auto border-t border-line-soft px-3 py-2 text-[11px] leading-relaxed text-slate-700"
                  >{{ safeJson(item.expected_rules) }}</pre>
                </details>
              </template>

              <template v-else-if="item.case_type === 'STATIC_MULTI'">
                <div
                  v-for="(turn, index) in item.turns ?? []"
                  :key="index"
                  class="rounded-xl border border-line-soft bg-canvas px-3 py-3"
                >
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <span class="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                      第 {{ index + 1 }} 轮
                    </span>
                    <span v-if="turn.notes" class="text-[11px] text-slate-500">{{ turn.notes }}</span>
                  </div>
                  <p class="mt-2 whitespace-pre-wrap text-sm text-slate-800">{{ turn.query || '—' }}</p>
                  <p class="mt-2 text-[11px] text-slate-500">期望答案</p>
                  <p class="mt-0.5 whitespace-pre-wrap text-xs text-slate-700">
                    {{ turn.expected_answer || '—' }}
                  </p>
                  <pre
                    v-if="turn.expected_rules"
                    class="mt-2 max-h-48 overflow-auto rounded-lg border border-line-soft bg-white px-2.5 py-2 text-[11px] leading-relaxed text-slate-600"
                  >{{ safeJson(turn.expected_rules) }}</pre>
                </div>
                <EmptyState
                  v-if="!(item.turns ?? []).length"
                  compact
                  title="尚未配置轮次"
                  description="编辑用例补充静态多轮脚本。"
                />
              </template>

              <template v-else>
                <div class="grid gap-3 sm:grid-cols-2">
                  <div>
                    <p class="label">用户目标</p>
                    <p class="mt-1 whitespace-pre-wrap rounded-xl border border-line-soft bg-canvas px-3 py-2 text-sm text-slate-800">
                      {{ item.user_goal || '—' }}
                    </p>
                  </div>
                  <div>
                    <p class="label">初始提问</p>
                    <p class="mt-1 whitespace-pre-wrap rounded-xl border border-line-soft bg-canvas px-3 py-2 text-sm text-slate-800">
                      {{ item.initial_query || '—' }}
                    </p>
                  </div>
                </div>
                <div>
                  <p class="label">模拟用户人设</p>
                  <p class="mt-1 whitespace-pre-wrap rounded-xl border border-line-soft bg-canvas px-3 py-2 text-sm text-slate-800">
                    {{ item.persona_description || '—' }}
                  </p>
                </div>
                <div class="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                  <span>最大轮次：<span class="font-mono text-slate-800">{{ item.max_turns }}</span></span>
                  <span>失败即停：<span class="text-slate-800">{{ item.fail_fast ? '是' : '否' }}</span></span>
                </div>
                <div v-if="(item.hidden_context ?? []).length">
                  <p class="label">隐藏上下文</p>
                  <div class="mt-1 space-y-2">
                    <div
                      v-for="entry in item.hidden_context ?? []"
                      :key="entry.key"
                      class="rounded-xl border border-line-soft bg-canvas px-3 py-2"
                    >
                      <p class="font-mono text-[11px] text-indigo-600">{{ entry.key }}</p>
                      <p class="mt-1 whitespace-pre-wrap text-xs text-slate-700">{{ entry.content }}</p>
                      <p v-if="entry.release_hint" class="mt-1 text-[10px] text-slate-500">
                        释放时机：{{ entry.release_hint }}
                      </p>
                    </div>
                  </div>
                </div>
                <details v-if="item.terminate_conditions" class="rounded-xl border border-line-soft bg-canvas">
                  <summary
                    class="cursor-pointer list-none px-3 py-2 text-xs font-medium text-slate-600 [&::-webkit-details-marker]:hidden"
                  >
                    终止条件 JSON
                  </summary>
                  <pre
                    class="max-h-64 overflow-auto border-t border-line-soft px-3 py-2 text-[11px] leading-relaxed text-slate-700"
                  >{{ safeJson(item.terminate_conditions) }}</pre>
                </details>
              </template>

              <!-- 答案契约事实 -->
              <div
                v-if="item.grading_mode === 'ANSWER_CONTRACT'"
                class="space-y-2 border-t border-line-soft pt-4"
              >
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <h3 class="text-xs font-semibold text-slate-700">
                    Must 事实取证 {{ verifiedCount }} / {{ mustFacts.length }}
                  </h3>
                  <span
                    class="text-[11px]"
                    :class="pendingFacts.length ? 'text-amber-600' : 'text-emerald-600'"
                  >
                    {{ pendingFacts.length ? `${pendingFacts.length} 条待处理` : '全部已取证 / 豁免' }}
                  </span>
                </div>
                <div class="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                  <div class="h-full rounded-full bg-brand transition-all" :style="{ width: `${factProgress}%` }" />
                </div>
                <div v-if="mustFacts.length" class="overflow-x-auto rounded-xl border border-line-soft">
                  <table class="table-shell">
                    <thead>
                      <tr>
                        <th class="th">事实</th>
                        <th class="th">取值</th>
                        <th class="th w-24">取证状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(fact, index) in mustFacts" :key="fact.id || index">
                        <td class="td text-xs">
                          <p class="font-medium text-slate-800">{{ fact.label || fact.id || '—' }}</p>
                          <p v-if="fact.time_scope" class="text-[10px] text-slate-500">
                            {{ fact.time_scope }}
                          </p>
                        </td>
                        <td class="td text-xs text-slate-600">{{ factValue(fact) }}</td>
                        <td class="td">
                          <span
                            class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                            :class="factStatusMeta(fact.verification_status).className"
                          >
                            {{ factStatusMeta(fact.verification_status).label }}
                          </span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <EmptyState
                  v-else
                  compact
                  title="尚未配置 Must 事实"
                  description="答案契约用例需要至少一条 Must 事实。"
                />
                <details
                  v-if="item.response_contract"
                  class="rounded-xl border border-line-soft bg-canvas"
                >
                  <summary
                    class="cursor-pointer list-none px-3 py-2 text-xs font-medium text-slate-600 [&::-webkit-details-marker]:hidden"
                  >
                    响应契约 JSON
                  </summary>
                  <pre
                    class="max-h-64 overflow-auto border-t border-line-soft px-3 py-2 text-[11px] leading-relaxed text-slate-700"
                  >{{ safeJson(item.response_contract) }}</pre>
                </details>
              </div>
            </div>
          </section>

          <!-- 执行记录（页内分页查看全部） -->
          <section id="case-executions" class="card scroll-mt-20">
            <header class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
              <div>
                <h2 class="panel-title">执行记录</h2>
                <p class="mt-0.5 text-[11px] text-slate-500">
                  共 {{ executionTotal }} 次执行（含批量、调试与重跑）
                  <span v-if="executionPageCount > 1"> · 第 {{ executionPage }} / {{ executionPageCount }} 页</span>
                </p>
              </div>
              <span
                v-if="hasActiveExecution"
                class="inline-flex items-center gap-1 text-[11px] text-emerald-600"
                title="有执行中的记录，进度由服务端实时推送"
              >
                <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" aria-hidden="true" />
                执行中 · 实时推送
              </span>
            </header>
            <div v-if="executions.length" class="overflow-x-auto">
              <table class="table-shell min-w-[980px]">
                <thead>
                  <tr>
                    <th class="th">执行</th>
                    <th class="th">状态</th>
                    <th class="th">得分</th>
                    <th class="th">追问自检</th>
                    <th class="th">版本</th>
                    <th class="th">来源</th>
                    <th class="th">耗时</th>
                    <th class="th">开始时间</th>
                    <th class="th text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="run in executions" :key="run.id">
                    <td class="td">
                      <button
                        type="button"
                        class="font-mono text-xs text-indigo-600 transition hover:text-indigo-800 hover:underline"
                        title="查看执行详情"
                        @click="openTrace(run.id)"
                      >
                        #{{ run.id }}
                      </button>
                      <p v-if="run.task_name" class="mt-0.5 max-w-[160px] truncate text-[11px] text-slate-500">
                        {{ truncate(run.task_name, 20) }}
                      </p>
                    </td>
                    <td class="td"><StatusPill :status="run.status" size="sm" /></td>
                    <td class="td text-xs font-medium text-slate-800">{{ formatScore(run.total_score) }}</td>
                    <td class="td">
                      <span
                        v-if="run.self_check_status"
                        class="inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium"
                        :class="selfCheckBadge(run).className"
                      >
                        {{ selfCheckBadge(run).label }}
                      </span>
                      <span v-else class="text-xs text-slate-300">—</span>
                    </td>
                    <td class="td font-mono text-xs text-slate-600">v{{ run.case_version }}</td>
                    <td class="td text-xs text-slate-600">{{ sourceLabel(run.source) }}</td>
                    <td class="td text-xs text-slate-600">{{ formatMs(run.latency_ms) }}</td>
                    <td class="td text-xs text-slate-500">{{ formatDateTime(run.started_at) }}</td>
                    <td class="td text-right">
                      <button class="btn-ghost btn-xs" @click="openTrace(run.id)">查看</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div
              v-if="executionPageCount > 1"
              class="flex items-center justify-between border-t border-line px-4 py-3"
            >
              <span class="text-[11px] text-slate-500">
                第 {{ executionPage }} / {{ executionPageCount }} 页
                <span v-if="executionLoading" class="ml-1 text-slate-400">加载中…</span>
              </span>
              <div class="flex gap-1">
                <button
                  class="btn-ghost btn-xs"
                  :disabled="executionPage <= 1 || executionLoading"
                  @click="changeExecutionPage(executionPage - 1)"
                >
                  上一页
                </button>
                <button
                  class="btn-ghost btn-xs"
                  :disabled="executionPage >= executionPageCount || executionLoading"
                  @click="changeExecutionPage(executionPage + 1)"
                >
                  下一页
                </button>
              </div>
            </div>
            <EmptyState
              v-if="executions.length === 0"
              compact
              title="还没有执行记录"
              description="点击右上角「单条调试」运行一次，结果会出现在这里。"
            />
          </section>

          <!-- 版本历史 -->
          <section class="card">
            <header class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
              <div>
                <h2 class="panel-title">版本历史</h2>
                <p class="mt-0.5 text-[11px] text-slate-500">
                  当前 v{{ item.current_version }}，共 {{ revisions.length }} 个版本
                </p>
              </div>
              <button class="btn-ghost btn-xs" @click="revisionOpen = true">版本 Diff / 回滚</button>
            </header>
            <div v-if="recentRevisions.length" class="px-4 py-4">
              <div class="relative space-y-4 pl-4">
                <span class="absolute bottom-1 left-[3px] top-1 w-px bg-slate-200" aria-hidden="true" />
                <div v-for="revision in recentRevisions" :key="revision.id" class="relative">
                  <span
                    class="absolute -left-4 top-1.5 h-1.5 w-1.5 rounded-full"
                    :class="revision.change_type === 'ROLLBACK' ? 'bg-amber-500' : 'bg-brand'"
                  />
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="font-mono text-xs font-semibold text-slate-800">v{{ revision.version }}</span>
                    <span
                      class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                      :class="revisionMeta(revision.change_type).className"
                    >
                      {{ revisionMeta(revision.change_type).label }}
                    </span>
                    <span class="text-[11px] text-slate-500">{{ revision.operator }}</span>
                    <span class="ml-auto text-[11px] text-slate-400">{{ formatDateTime(revision.created_at) }}</span>
                  </div>
                  <p v-if="revision.summary" class="mt-1 text-[11px] text-slate-500">
                    {{ revision.summary }}
                  </p>
                </div>
              </div>
            </div>
            <EmptyState v-else compact title="还没有版本记录" />
          </section>
        </div>

        <div class="space-y-4">
          <section class="card">
            <header class="border-b border-line px-4 py-3">
              <h2 class="panel-title">基础信息</h2>
            </header>
            <dl class="divide-y divide-line-soft px-4">
              <div
                v-for="row in baseInfo"
                :key="row.label"
                class="flex items-start justify-between gap-3 py-2 text-xs"
              >
                <dt class="shrink-0 text-slate-500">{{ row.label }}</dt>
                <dd class="min-w-0 break-words text-right text-slate-800" :class="row.mono ? 'font-mono' : ''">
                  {{ row.value }}
                </dd>
              </div>
            </dl>
          </section>

          <section class="card">
            <header class="border-b border-line px-4 py-3">
              <h2 class="panel-title">执行与模型</h2>
            </header>
            <dl class="divide-y divide-line-soft px-4">
              <div
                v-for="row in runInfo"
                :key="row.label"
                class="flex items-start justify-between gap-3 py-2 text-xs"
              >
                <dt class="shrink-0 text-slate-500">{{ row.label }}</dt>
                <dd class="min-w-0 break-words text-right text-slate-800" :class="row.mono ? 'font-mono' : ''">
                  {{ row.value }}
                </dd>
              </div>
            </dl>
          </section>

          <section class="card">
            <header class="border-b border-line px-4 py-3">
              <h2 class="panel-title">评分与契约</h2>
            </header>
            <dl class="divide-y divide-line-soft px-4">
              <div
                v-for="row in gradingInfo"
                :key="row.label"
                class="flex items-start justify-between gap-3 py-2 text-xs"
              >
                <dt class="shrink-0 text-slate-500">{{ row.label }}</dt>
                <dd class="min-w-0 break-words text-right text-slate-800" :class="row.mono ? 'font-mono' : ''">
                  {{ row.value }}
                </dd>
              </div>
            </dl>
            <div
              v-if="canReview(item) || canFreeze"
              class="flex flex-wrap gap-2 border-t border-line px-4 py-3"
            >
              <button v-if="canReview(item)" class="btn-ghost btn-xs flex-1" @click="openReview">
                {{ reviewActionLabel }}
              </button>
              <button v-if="canFreeze" class="btn-ghost btn-xs flex-1" @click="openFreeze">
                冻结进数据集
              </button>
            </div>
          </section>
        </div>
      </div>
    </template>

    <!-- 评审 -->
    <BaseModal
      v-model="reviewOpen"
      title="用例评审"
      :subtitle="item ? `${item.code} · ${item.name}` : ''"
      width="sm"
    >
      <div v-if="item" class="space-y-4">
        <div class="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          <span
            class="rounded-full px-1.5 py-0.5 font-medium"
            :class="reviewMeta(item.review_status).className"
          >
            {{ reviewMeta(item.review_status).label }}
          </span>
          <span>{{ item.grading_mode === 'ANSWER_CONTRACT' ? '答案契约' : '4 维评分' }}</span>
          <span v-if="item.reviewed_by">评审人：{{ item.reviewed_by }}</span>
        </div>

        <div class="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs">
          <template v-if="item.grading_mode === 'ANSWER_CONTRACT'">
            <p class="font-medium text-slate-700">
              Must 事实取证 {{ verifiedCount }} / {{ mustFacts.length }}
            </p>
            <p v-if="pendingFacts.length > 0" class="text-amber-700">
              未取证：{{ pendingFacts.map((fact) => fact.label || fact.id).join('、') }}
            </p>
            <p v-else class="text-emerald-700">全部 Must 事实已取证或已豁免，可通过评审。</p>
            <p class="text-slate-500">
              评审通过后才可冻结进数据集；修改事实值会自动退回待取证，并撤销评审结论。
            </p>
          </template>
          <p v-else class="text-slate-500">
            该用例为 4 维评分，不设事实取证闸门；评审结论仅用于变更留痕与审计。
          </p>
        </div>

        <label class="block space-y-1">
          <span class="text-[11px] text-slate-500">评审意见（驳回必填）</span>
          <textarea
            v-model="reviewNote"
            class="input"
            rows="3"
            maxlength="512"
            placeholder="如：Must 事实口径与账期不一致，请补充 2026-10 账单总额取证后再提交。"
          />
        </label>
      </div>
      <template #footer>
        <button class="btn-ghost" :disabled="reviewSubmitting" @click="reviewOpen = false">
          取消
        </button>
        <button
          v-if="item?.review_status === 'APPROVED'"
          class="btn-ghost"
          :disabled="reviewSubmitting"
          @click="submitReview('PENDING')"
        >
          撤回评审
        </button>
        <button
          v-else-if="item?.review_status === 'REJECTED'"
          class="btn-ghost"
          :disabled="reviewSubmitting"
          @click="submitReview('PENDING')"
        >
          重置为待评审
        </button>
        <button class="btn-danger" :disabled="reviewSubmitting" @click="submitReview('REJECTED')">
          驳回
        </button>
        <button class="btn-primary" :disabled="reviewSubmitting" @click="submitReview('APPROVED')">
          {{ reviewSubmitting ? '提交中…' : '审核通过' }}
        </button>
      </template>
    </BaseModal>

    <!-- 冻结 -->
    <BaseModal
      v-model="freezeOpen"
      title="冻结进数据集"
      :subtitle="item ? `${item.code} · ${item.name}` : ''"
      width="sm"
    >
      <div class="space-y-3">
        <p class="text-xs text-slate-500">
          冻结前需已完成评审通过，且 Must 事实全部「已取证」或「已豁免」。
        </p>
        <label class="space-y-1">
          <span class="text-[11px] text-slate-500">数据集版本</span>
          <input v-model="freezeVersion" class="input" placeholder="如 v1 / v2026-11" />
        </label>
      </div>
      <template #footer>
        <button class="btn-ghost" :disabled="freezing" @click="freezeOpen = false">取消</button>
        <button class="btn-primary" :disabled="freezing" @click="submitFreeze">
          {{ freezing ? '冻结中…' : '确认冻结' }}
        </button>
      </template>
    </BaseModal>

    <TestCaseDrawer
      v-model="drawerOpen"
      :case-data="item"
      :environments="environments"
      :templates="templates"
      :models="models"
      :saving="saving"
      @submit="submitCase"
    />

    <CaseRevisionPanel
      v-model="revisionOpen"
      :case-data="item"
      @updated="onRevisionUpdated"
    />

    <CaseTrendPanel
      v-model="trendOpen"
      :case-data="item"
      @select-execution="openTrace"
      @view-all="onTrendViewAll"
    />

    <RunComparePanel v-model="compareOpen" :case-data="item" />
  </div>
</template>

<style scoped>
details[open] > summary svg {
  transform: rotate(180deg);
}

.menu-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 0.375rem;
  border-radius: 0.5rem;
  padding: 0.5rem 0.625rem;
  text-align: left;
  font-size: 0.75rem;
  line-height: 1rem;
  color: #475569;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.menu-item:hover {
  background-color: #f8fafc;
  color: #0f172a;
}
</style>
