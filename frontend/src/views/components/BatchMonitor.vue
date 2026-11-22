<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import StatusPill from '../../components/StatusPill.vue'
import EmptyState from '../../components/EmptyState.vue'
import PaginationBar from '../../components/PaginationBar.vue'
import type { BatchTask, CaseExecutionItem, TaskMetrics, TaskRetryCandidates } from '../../types'
import {
  formatDateTime,
  formatMs,
  formatNumber,
  formatPercent,
  formatSeconds,
  formatScore,
  statusLabel,
} from '../../utils/format'
import { canStopPlanTask, isPlanRootTask, isPlanStopped, taskScheduleLabel } from '../../utils/schedule'
import { hasKeyword } from '../../utils/search'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()

const props = withDefaults(
  defineProps<{
    task: BatchTask | null
    metrics: TaskMetrics | null
    executions: CaseExecutionItem[]
    live?: boolean
    executionTotal?: number
    executionPage?: number
    executionPageSize?: number
    executionPageSizeOptions?: number[]
    latestPerCase?: boolean
    executionStatus?: string
    executionKeyword?: string
    executionMinScore?: number | null
    executionMaxScore?: number | null
    executionSort?: '' | 'score_desc' | 'score_asc'
    exporting?: boolean
    retryCandidates?: TaskRetryCandidates | null
  }>(),
  { live: false, executionTotal: 0, executionPage: 1, executionPageSize: 30, executionPageSizeOptions: () => [10, 20, 30, 50], latestPerCase: true, executionStatus: '', executionKeyword: '', executionMinScore: null, executionMaxScore: null, executionSort: '', exporting: false, retryCandidates: null },
)

const emit = defineEmits<{
  (e: 'start'): void
  (e: 'abort'): void
  (e: 'resume'): void
  (e: 'run-now'): void
  (e: 'stop-plan'): void
  (e: 'toggle-plan', active: boolean): void
  (e: 'select-execution', executionId: number): void
  (e: 'open-case', caseId: number): void
  (e: 'retry-case', caseId: number): void
  (e: 'retry-cases', caseIds: number[]): void
  (e: 'page-change', page: number): void
  (e: 'page-size-change', size: number): void
  (e: 'mode-change', latestPerCase: boolean): void
  (
    e: 'filter-change',
    filters: {
      status: string
      keyword: string
      minScore: number | null
      maxScore: number | null
      sort: '' | 'score_desc' | 'score_asc'
    },
  ): void
  (e: 'export', format: 'xlsx' | 'jsonl'): void
}>()

const segmented = computed(() => {
  const dist = props.metrics?.status_distribution
  if (!dist) return []
  return [
    { key: 'PASS', label: '通过', count: dist.PASS, color: 'bg-emerald-500' },
    { key: 'FAIL', label: '失败', count: dist.FAIL, color: 'bg-rose-500' },
    { key: 'ERROR', label: '异常', count: dist.ERROR, color: 'bg-amber-500' },
    { key: 'MAX_TURNS_EXCEEDED', label: '轮次超限', count: dist.MAX_TURNS_EXCEEDED, color: 'bg-orange-500' },
    { key: 'REPETITION_ABORT', label: '复读熔断', count: dist.REPETITION_ABORT, color: 'bg-fuchsia-500' },
    { key: 'ABORTED', label: '已终止', count: dist.ABORTED, color: 'bg-slate-500' },
    { key: 'RUNNING', label: '执行中', count: dist.RUNNING, color: 'bg-indigo-500' },
    { key: 'PENDING', label: '等待中', count: dist.PENDING, color: 'bg-slate-700' },
  ].filter((item) => item.count > 0)
})

const distributionTotal = computed(() =>
  segmented.value.reduce((sum, item) => sum + item.count, 0),
)

// 定时计划行：只保留配置与下次触发时间，历史执行都在克隆出的批量任务里
const isPlan = computed(() => Boolean(props.task && props.task.schedule_type !== 'IMMEDIATE'))
const isPlanRoot = computed(() => isPlanRootTask(props.task))
const planWaiting = computed(() =>
  Boolean(props.task && isPlan.value && ['SCHEDULED', 'ABORTED'].includes(props.task.status)),
)
const canStart = computed(
  () => !planWaiting.value && props.task?.status === 'PENDING',
)
const canStopPlan = computed(() => canStopPlanTask(props.task))
const canAbort = computed(
  () =>
    !planWaiting.value &&
    !isPlanRoot.value &&
    Boolean(props.task && ['RUNNING', 'PENDING'].includes(props.task.status)),
)
// 续做：非计划批次已终止/异常结束，且仍有未完成用例。
// 与「失败重跑」不同，只补跑未完成用例，已有最终判定的结果保持不变。
const canResume = computed(
  () =>
    !planWaiting.value &&
    !isPlan.value &&
    Boolean(
      props.task &&
        ['ABORTED', 'FAILED'].includes(props.task.status) &&
        (props.task.unfinished_cases ?? 0) > 0,
    ),
)

// 单用例重试：仅「用例最新」视图下展示，避免对历史尝试行误操作；
// 整批执行期间禁止单用例重试，避免与批次执行并发写入同一用例结果。
const SINGLE_RETRYABLE_STATUSES = new Set([
  'FAIL',
  'ERROR',
  'MAX_TURNS_EXCEEDED',
  'REPETITION_ABORT',
  'ABORTED',
])
const taskRunning = computed(
  () => Boolean(props.task && ['RUNNING', 'PENDING'].includes(props.task.status)),
)
// 单用例重试会让任务短暂回到 RUNNING，但此时其他用例仍可继续提交重试；
// 只有整批执行期间才应隐藏所有重试入口。
const retryingCaseIds = computed(() => new Set(props.task?.retrying_cases ?? []))
const singleRetryActive = computed(() => retryingCaseIds.value.size > 0)
function isCaseRetrying(item: CaseExecutionItem): boolean {
  return (
    item.case_id !== null &&
    item.case_id !== undefined &&
    retryingCaseIds.value.has(item.case_id)
  )
}
function canRetryCase(item: CaseExecutionItem): boolean {
  return (
    props.latestPerCase &&
    (!taskRunning.value || singleRetryActive.value) &&
    item.case_id !== null &&
    item.case_id !== undefined &&
    !isCaseRetrying(item) &&
    SINGLE_RETRYABLE_STATUSES.has(item.status)
  )
}

// 批量勾选重试：仅在「用例最新」视图开放，整批执行期间不可选，
// 但单用例重试队列运行期间仍可继续勾选其他可重试用例。
const selectionAvailable = computed(
  () =>
    props.latestPerCase &&
    !planWaiting.value &&
    !isPlan.value &&
    auth.can('execution:retry') &&
    (!taskRunning.value || singleRetryActive.value),
)
const selectedCaseIds = ref<number[]>([])
const selectedIdSet = computed(() => new Set(selectedCaseIds.value))
const pageSelectableIds = computed(() =>
  props.executions
    .filter(canRetryCase)
    .map((item) => item.case_id as number),
)
const pageAllSelected = computed(
  () =>
    pageSelectableIds.value.length > 0 &&
    pageSelectableIds.value.every((id) => selectedIdSet.value.has(id)),
)
const pageSomeSelected = computed(
  () =>
    !pageAllSelected.value &&
    pageSelectableIds.value.some((id) => selectedIdSet.value.has(id)),
)
// 后端按最新执行状态汇总的可重试用例，排除已提交的（实时刷新有延迟时兜底）。
const candidateGroups = computed(() =>
  (props.retryCandidates?.groups ?? [])
    .map((group) => ({
      status: group.status,
      caseIds: group.case_ids.filter((id) => !retryingCaseIds.value.has(id)),
    }))
    .filter((group) => group.caseIds.length > 0),
)
const candidateIdSet = computed(
  () => new Set(candidateGroups.value.flatMap((group) => group.caseIds)),
)
const selectableCandidateTotal = computed(() =>
  candidateGroups.value.reduce((sum, group) => sum + group.caseIds.length, 0),
)
const allCandidatesSelected = computed(
  () =>
    selectableCandidateTotal.value > 0 &&
    candidateGroups.value.every((group) =>
      group.caseIds.every((id) => selectedIdSet.value.has(id)),
    ),
)

function isCaseSelected(item: CaseExecutionItem): boolean {
  return (
    item.case_id !== null &&
    item.case_id !== undefined &&
    selectedIdSet.value.has(item.case_id)
  )
}

function toggleCaseSelection(item: CaseExecutionItem): void {
  if (!canRetryCase(item) || item.case_id === null || item.case_id === undefined) return
  const caseId = item.case_id
  selectedCaseIds.value = selectedIdSet.value.has(caseId)
    ? selectedCaseIds.value.filter((id) => id !== caseId)
    : [...selectedCaseIds.value, caseId]
}

function togglePageSelection(): void {
  if (pageAllSelected.value) {
    const pageIds = new Set(pageSelectableIds.value)
    selectedCaseIds.value = selectedCaseIds.value.filter((id) => !pageIds.has(id))
    return
  }
  selectedCaseIds.value = Array.from(
    new Set([...selectedCaseIds.value, ...pageSelectableIds.value]),
  )
}

function isStatusGroupSelected(group: { caseIds: number[] }): boolean {
  return group.caseIds.every((id) => selectedIdSet.value.has(id))
}

/** 按状态整组勾选/取消：覆盖当前批次所有页，不只当前页。 */
function toggleStatusSelection(group: { caseIds: number[] }): void {
  const caseIds = new Set(group.caseIds)
  if (isStatusGroupSelected(group)) {
    selectedCaseIds.value = selectedCaseIds.value.filter((id) => !caseIds.has(id))
    return
  }
  selectedCaseIds.value = Array.from(
    new Set([...selectedCaseIds.value, ...group.caseIds]),
  )
}

function selectAllCandidates(): void {
  selectedCaseIds.value = Array.from(candidateIdSet.value)
}

function clearSelection(): void {
  selectedCaseIds.value = []
}

function submitBatchRetry(): void {
  if (!selectionAvailable.value || selectedCaseIds.value.length === 0) return
  emit('retry-cases', [...selectedCaseIds.value])
  // 不清空勾选：确认框取消或接口失败时保留选择；提交成功后由
  // retrying_cases / 候选清单变化自动把已进入队列的用例移出。
}

// 只保留仍可重试的勾选项：进入执行的用例、状态已变为 PASS 的用例自动移出。
watch(
  [() => props.executions, retryingCaseIds, candidateIdSet, pageSelectableIds],
  () => {
    const pageIds = new Set(pageSelectableIds.value)
    const next = selectedCaseIds.value.filter(
      (id) =>
        !retryingCaseIds.value.has(id) &&
        (pageIds.has(id) || candidateIdSet.value.has(id)),
    )
    if (next.length !== selectedCaseIds.value.length) selectedCaseIds.value = next
  },
)

watch(
  () => props.latestPerCase,
  (value) => {
    if (!value) selectedCaseIds.value = []
  },
)

watch(
  () => props.task?.id,
  () => {
    selectedCaseIds.value = []
  },
)
const canRunNow = computed(() => planWaiting.value && planActive.value)
const canEnablePlan = computed(() => planWaiting.value && !planActive.value)
const planActive = computed(
  () => Boolean(props.task?.schedule_active && props.task.status === 'SCHEDULED'),
)

const progress = computed(() => props.metrics?.progress ?? 0)

const stats = computed<Array<{ label: string; value: string; hint?: string }>>(() => [
  {
    label: '进度',
    value: formatPercent(props.metrics?.progress, 1),
    hint: (props.metrics?.retry_count ?? props.metrics?.env_retry_count)
      ? `已自动重试 ${props.metrics?.retry_count ?? props.metrics?.env_retry_count} 条用例`
      : '',
  },
  { label: '通过率', value: formatPercent(props.metrics?.pass_rate, 1) },
  {
    label: '本批次平均分',
    value: formatScore(props.metrics?.avg_score),
  },
  { label: '平均 TTFT', value: formatMs(props.metrics?.avg_ttft_ms) },
  { label: 'P95 时延', value: formatMs(props.metrics?.p95_latency_ms) },
  {
    label: 'Tokens',
    value: formatNumber(props.metrics?.total_tokens),
    hint: props.metrics
      ? `模拟 ${formatNumber(props.metrics.simulator_tokens)} / 裁判 ${formatNumber(props.metrics.judge_tokens)}`
      : '',
  },
  {
    label: '预计剩余',
    value: props.metrics?.eta_seconds === null ? '—' : formatSeconds(props.metrics?.eta_seconds),
    hint: props.metrics
      ? `已耗时 ${formatSeconds(props.metrics.elapsed_seconds)} · ${props.metrics.throughput_per_minute.toFixed(1)} 条/分`
      : '',
  },
])

// ---------------------------------------------------------- 用例执行结果筛选
const STATUS_FILTERS = [
  'PASS',
  'FAIL',
  'ERROR',
  'MAX_TURNS_EXCEEDED',
  'REPETITION_ABORT',
  'ABORTED',
  'RUNNING',
  'PENDING',
] as const

const statusFilterOptions = computed(() =>
  STATUS_FILTERS.map((value) => ({
    value,
    label: statusLabel(value),
    count: props.metrics?.status_distribution?.[value] ?? 0,
  })),
)

const filterStatus = ref(props.executionStatus)
const filterKeyword = ref(props.executionKeyword)

type ScorePreset = '' | 'gte90' | 'gte60' | '80-89' | '60-79' | 'lt60' | 'custom'

function presetFromRange(min: number | null, max: number | null): ScorePreset {
  if (min === null && max === null) return ''
  if (min === 90 && (max === null || max === 100)) return 'gte90'
  if (min === 60 && (max === null || max === 100)) return 'gte60'
  if (min === 80 && max !== null && Math.abs(max - 89.99) < 0.011) return '80-89'
  if (min === 60 && max !== null && Math.abs(max - 79.99) < 0.011) return '60-79'
  if ((min === null || min === 0) && max !== null && Math.abs(max - 59.99) < 0.011) {
    return 'lt60'
  }
  return 'custom'
}

const filterMinScore = ref<string | number>(
  props.executionMinScore === null ? '' : String(props.executionMinScore),
)
const filterMaxScore = ref<string | number>(
  props.executionMaxScore === null ? '' : String(props.executionMaxScore),
)
const filterScorePreset = ref<ScorePreset>(
  presetFromRange(props.executionMinScore, props.executionMaxScore),
)
const filterSort = ref<'' | 'score_desc' | 'score_asc'>(props.executionSort)

/** 数字输入框配合 v-model 时可能返回 number，统一转成字符串后再判断。 */
function hasScoreInput(value: string | number): boolean {
  return String(value).trim() !== ''
}

const hasFilter = computed(
  () =>
    Boolean(filterStatus.value) ||
    hasKeyword(filterKeyword.value) ||
    hasScoreInput(filterMinScore.value) ||
    hasScoreInput(filterMaxScore.value) ||
    Boolean(filterSort.value),
)

let filterTimer: number | null = null

watch(
  () => props.executionStatus,
  (value) => {
    filterStatus.value = value
  },
)

watch(
  () => props.executionKeyword,
  (value) => {
    filterKeyword.value = value
  },
)

watch(
  [() => props.executionMinScore, () => props.executionMaxScore],
  ([min, max]) => {
    filterMinScore.value = min === null ? '' : String(min)
    filterMaxScore.value = max === null ? '' : String(max)
    // 全部清空或不在自定义态时，跟随区间推导档位；自定义态下不把用户输入吸附成预设。
    if (min === null && max === null) filterScorePreset.value = ''
    else if (filterScorePreset.value !== 'custom') {
      filterScorePreset.value = presetFromRange(min, max)
    }
  },
)

watch(
  () => props.executionSort,
  (value) => {
    filterSort.value = value
  },
)

function clearFilterTimer(): void {
  if (filterTimer !== null) {
    window.clearTimeout(filterTimer)
    filterTimer = null
  }
}

function parseScore(text: string | number): number | null {
  const trimmed = String(text).trim()
  if (!trimmed) return null
  const value = Number(trimmed)
  if (!Number.isFinite(value)) return null
  return Math.min(100, Math.max(0, value))
}

function emitFilters(): void {
  clearFilterTimer()
  emit('filter-change', {
    status: filterStatus.value,
    keyword: filterKeyword.value.trim(),
    minScore: parseScore(filterMinScore.value),
    maxScore: parseScore(filterMaxScore.value),
    sort: filterSort.value,
  })
}

function scheduleFilters(): void {
  clearFilterTimer()
  filterTimer = window.setTimeout(() => {
    filterTimer = null
    emitFilters()
  }, 350)
}

function applyScorePreset(preset: ScorePreset): void {
  filterScorePreset.value = preset
  switch (preset) {
    case 'gte90':
      filterMinScore.value = '90'
      filterMaxScore.value = ''
      break
    case 'gte60':
      filterMinScore.value = '60'
      filterMaxScore.value = ''
      break
    case '80-89':
      filterMinScore.value = '80'
      filterMaxScore.value = '89.99'
      break
    case '60-79':
      filterMinScore.value = '60'
      filterMaxScore.value = '79.99'
      break
    case 'lt60':
      filterMinScore.value = ''
      filterMaxScore.value = '59.99'
      break
    case 'custom':
      if (!hasScoreInput(filterMinScore.value) && !hasScoreInput(filterMaxScore.value)) {
        filterMinScore.value = '0'
        filterMaxScore.value = '100'
      }
      break
    default:
      filterMinScore.value = ''
      filterMaxScore.value = ''
  }
  emitFilters()
}

function onScorePresetChange(): void {
  applyScorePreset(filterScorePreset.value)
}

function onCustomScoreInput(): void {
  filterScorePreset.value = 'custom'
  scheduleFilters()
}

function resetFilters(): void {
  filterStatus.value = ''
  filterKeyword.value = ''
  filterMinScore.value = ''
  filterMaxScore.value = ''
  filterScorePreset.value = ''
  filterSort.value = ''
  emitFilters()
}

function toggleScoreSort(): void {
  if (filterSort.value === 'score_desc') filterSort.value = 'score_asc'
  else if (filterSort.value === 'score_asc') filterSort.value = ''
  else filterSort.value = 'score_desc'
  emitFilters()
}

function scorePercent(score?: number | null): number {
  if (score === null || score === undefined || Number.isNaN(score)) return 0
  return Math.min(100, Math.max(0, score))
}

function scoreBarClass(score?: number | null): string {
  if (score === null || score === undefined || Number.isNaN(score)) return 'bg-slate-300'
  if (score >= 80) return 'bg-emerald-500'
  if (score >= 60) return 'bg-amber-500'
  return 'bg-rose-500'
}

function isLowScore(score?: number | null): boolean {
  return score !== null && score !== undefined && score < 60
}

onBeforeUnmount(clearFilterTimer)
</script>

<template>
  <section class="card overflow-hidden">
    <header class="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-sm font-semibold text-slate-900">批量任务详情</h2>
          <span class="rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[10px] text-indigo-600">
            批量
          </span>
          <span
            v-if="live"
            class="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-700"
          >
            <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
            WebSocket 实时
          </span>
        </div>
        <p v-if="task" class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
          <span class="font-mono text-slate-600">{{ task.code }}</span>
          <span>·</span>
          <span class="truncate">{{ task.name }}</span>
          <span>·</span>
          <span>{{ task.total_cases }} 条用例</span>
          <span>·</span>
          <span>{{ task.environment_name || '未绑定环境' }}</span>
          <span>·</span>
          <span>并发 {{ task.concurrency }} / QPS {{ task.qps_limit }}</span>
          <span>·</span>
          <span>
            {{ (task.retry_on_error ?? task.retry_on_env_error) ? `异常失败自动重试 ${task.max_error_retries ?? task.max_env_retries} 次` : '异常失败不重试' }}
          </span>
          <template v-if="task.enable_self_check">
            <span>·</span>
            <span class="text-violet-700">失败追问自检</span>
          </template>
          <template
            v-if="
              (task.unfinished_cases ?? 0) > 0 &&
              ['ABORTED', 'FAILED'].includes(task.status)
            "
          >
            <span>·</span>
            <span class="text-amber-700">未完成 {{ task.unfinished_cases }} 条，可继续执行</span>
          </template>
          <template v-if="isPlan">
            <span>·</span>
            <span class="text-sky-700">{{ taskScheduleLabel(task) }}</span>
            <template v-if="planWaiting">
              <span>·</span>
              <span>{{ planActive ? `下次触发 ${formatDateTime(task.next_run_at)}` : '计划已停止' }}</span>
            </template>
          </template>
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <StatusPill
          v-if="task"
          :status="task.status"
          :label="(planWaiting && !planActive) || isPlanStopped(task) ? '已停止' : null"
        />
        <button
          v-if="auth.can('task:export')"
          class="btn-ghost btn-xs"
          :disabled="!task || exporting"
          title="人读视图，超长字段按 Excel 上限截断"
          @click="emit('export', 'xlsx')"
        >
          {{ exporting ? '导出中…' : '导出 Excel' }}
        </button>
        <button
          v-if="auth.can('task:export')"
          class="btn-ghost btn-xs"
          :disabled="!task || exporting"
          title="完整数据（JSONL），超长字段不截断，适合程序消费"
          @click="emit('export', 'jsonl')"
        >
          {{ exporting ? '导出中…' : '导出 JSONL' }}
        </button>
        <button v-if="auth.can('task:create') && canRunNow" class="btn-primary btn-xs" @click="emit('run-now')">
          立即执行
        </button>
        <button
          v-if="auth.can('task:create') && canEnablePlan"
          class="btn-ghost btn-xs"
          @click="emit('toggle-plan', true)"
        >
          启用计划
        </button>
        <button v-if="auth.can('task:stop') && canStopPlan" class="btn-danger btn-xs" @click="emit('stop-plan')">
          停止计划
        </button>
        <button v-if="auth.can('task:create') && canStart" class="btn-primary btn-xs" @click="emit('start')">启动</button>
        <button v-if="auth.can('task:stop') && canAbort" class="btn-danger btn-xs" @click="emit('abort')">终止</button>
        <button
          v-if="auth.can('execution:retry') && canResume"
          class="btn-primary btn-xs"
          title="只补跑未完成的用例，已有结果保持不动"
          @click="emit('resume')"
        >
          继续执行
        </button>
      </div>
    </header>

    <div v-if="!task" class="px-5">
      <EmptyState title="还没有批量任务" description="在右上角新建批量任务，选择一个或多个用例开始回归。" />
    </div>

    <template v-else>
      <div v-if="planWaiting" class="space-y-4 px-5 py-5">
        <div class="rounded-xl border border-sky-200 bg-sky-50/70 p-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="text-xs font-medium text-sky-800">定时计划</p>
              <p class="mt-1 text-sm font-semibold text-slate-900">{{ taskScheduleLabel(task) }}</p>
              <p class="mt-1 text-[11px] text-slate-600">
                {{
                  planActive
                    ? '计划生效中，到点会自动生成新的批量任务执行。'
                    : '计划已停止，不再自动触发；启用后可立即执行，已生成的历史任务不受影响。'
                }}
              </p>
            </div>
            <button v-if="canRunNow" class="btn-primary btn-xs" @click="emit('run-now')">
              立即执行
            </button>
          </div>
          <div class="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div>
              <p class="text-[10px] uppercase tracking-wider text-slate-500">下次触发</p>
              <p class="mt-0.5 text-sm font-medium tabular-nums text-slate-900">
                {{ planActive ? formatDateTime(task.next_run_at) : '—' }}
              </p>
            </div>
            <div>
              <p class="text-[10px] uppercase tracking-wider text-slate-500">上次触发</p>
              <p class="mt-0.5 text-sm font-medium tabular-nums text-slate-900">
                {{ formatDateTime(task.last_run_at) }}
              </p>
            </div>
            <div>
              <p class="text-[10px] uppercase tracking-wider text-slate-500">用例数</p>
              <p class="mt-0.5 text-sm font-medium tabular-nums text-slate-900">
                {{ task.total_cases }}
              </p>
            </div>
            <div>
              <p class="text-[10px] uppercase tracking-wider text-slate-500">触发后</p>
              <p class="mt-0.5 text-sm font-medium text-slate-900">克隆新任务执行</p>
            </div>
          </div>
        </div>
        <p class="text-[11px] text-slate-500">
          每次触发都会生成一条独立批量任务，进度、指标与失败重跑互不影响；可在左侧任务列表中查看历史执行。
        </p>
      </div>

      <template v-else>
      <div class="border-b border-slate-200 px-5 py-4">
        <div class="flex items-center justify-between text-xs text-slate-600">
          <span>
            {{ metrics ? `${metrics.finished_cases} / ${metrics.total_cases}` : `${task.finished_cases} / ${task.total_cases}` }}
            条已完成
          </span>
          <span class="tabular-nums">{{ progress.toFixed(1) }}%</span>
        </div>
        <div class="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
          <div
            class="h-full rounded-full bg-gradient-to-r from-indigo-500 to-indigo-600 transition-all duration-500"
            :style="{ width: `${Math.min(100, Math.max(0, progress))}%` }"
          />
        </div>
        <div v-if="segmented.length" class="mt-3 flex h-2 overflow-hidden rounded-full bg-slate-100">
          <div
            v-for="segment in segmented"
            :key="segment.key"
            class="h-full transition-all duration-500"
            :class="segment.color"
            :style="{ width: `${(segment.count / distributionTotal) * 100}%` }"
            :title="`${segment.label}: ${segment.count}`"
          />
        </div>
        <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
          <span v-for="segment in segmented" :key="segment.key" class="inline-flex items-center gap-1.5">
            <span class="h-1.5 w-1.5 rounded-full" :class="segment.color" />
            {{ segment.label }} {{ segment.count }}
          </span>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3 border-b border-slate-200 px-5 py-4 sm:grid-cols-3 xl:grid-cols-7">
        <div v-for="item in stats" :key="item.label">
          <p class="text-[10px] uppercase tracking-wider text-slate-500">
            {{ item.label }}
          </p>
          <p
            class="mt-0.5 text-xl font-semibold leading-tight tracking-[-0.02em] tabular-nums text-slate-900"
          >
            {{ item.value }}
          </p>
          <p v-if="item.hint" class="mt-0.5 text-[10px] text-slate-500">
            {{ item.hint }}
          </p>
        </div>
      </div>

      <div class="overflow-x-auto">
        <div class="flex flex-wrap items-center justify-between gap-2 px-5 pt-4">
          <div>
            <h3 class="panel-title">用例执行结果</h3>
            <p class="mt-0.5 text-[11px] text-slate-500">
              {{ latestPerCase ? '按用例只保留最新一次尝试' : '展示每次尝试（含失败重跑）' }}；点击行进入该用例的单笔执行结果页。
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <div class="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 text-[11px]">
              <button
                class="rounded-md px-2.5 py-1 transition"
                :class="
                  latestPerCase
                    ? 'bg-white font-medium text-slate-900 shadow-sm'
                    : 'text-slate-500 hover:text-slate-800'
                "
                @click="emit('mode-change', true)"
              >
                用例最新
              </button>
              <button
                class="rounded-md px-2.5 py-1 transition"
                :class="
                  !latestPerCase
                    ? 'bg-white font-medium text-slate-900 shadow-sm'
                    : 'text-slate-500 hover:text-slate-800'
                "
                @click="emit('mode-change', false)"
              >
                全部尝试
              </button>
            </div>
            <span class="text-[11px] text-slate-500">
              共 {{ executionTotal }} {{ latestPerCase ? '条用例' : '次执行' }}
            </span>
          </div>
        </div>
        <div class="flex flex-wrap items-end gap-3 px-5 pt-3">
          <div class="w-40">
            <label class="label">执行状态</label>
            <select v-model="filterStatus" class="input !py-1.5 text-xs" @change="emitFilters">
              <button><selectedcontent></selectedcontent></button>
              <option value="">全部状态</option>
              <option v-for="option in statusFilterOptions" :key="option.value" :value="option.value">
                {{ option.label }}{{ latestPerCase ? `（${option.count}）` : '' }}
              </option>
            </select>
          </div>
          <div class="w-64">
            <label class="label">用例搜索</label>
            <div class="relative">
              <svg
                class="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                aria-hidden="true"
              >
                <circle cx="11" cy="11" r="7" />
                <path stroke-linecap="round" d="m20 20-3.5-3.5" />
              </svg>
              <input
                v-model="filterKeyword"
                class="input !py-1.5 !pl-9 text-xs"
                type="search"
                placeholder="搜索用例编号 / 名称，输入即筛（分号分隔多词）"
                @input="scheduleFilters"
                @keyup.enter="emitFilters"
              />
            </div>
          </div>
          <div class="flex items-end gap-1.5">
            <div class="w-36">
              <label class="label">得分</label>
              <select
                v-model="filterScorePreset"
                class="input !py-1.5 text-xs"
                @change="onScorePresetChange"
              >
                <button><selectedcontent></selectedcontent></button>
                <option value="">全部得分</option>
                <option value="gte90">≥ 90</option>
                <option value="gte60">≥ 60</option>
                <option value="80-89">80 – 89</option>
                <option value="60-79">60 – 79</option>
                <option value="lt60">&lt; 60</option>
                <option value="custom">自定义区间…</option>
              </select>
            </div>
            <div v-if="filterScorePreset === 'custom'" class="flex items-center gap-1.5">
              <input
                v-model="filterMinScore"
                class="input !w-[68px] !py-1.5 !px-2 text-center text-xs tabular-nums"
                type="number"
                min="0"
                max="100"
                placeholder="最低分"
                @input="onCustomScoreInput"
                @keyup.enter="emitFilters"
              />
              <span class="text-xs text-slate-400">—</span>
              <input
                v-model="filterMaxScore"
                class="input !w-[68px] !py-1.5 !px-2 text-center text-xs tabular-nums"
                type="number"
                min="0"
                max="100"
                placeholder="最高分"
                @input="onCustomScoreInput"
                @keyup.enter="emitFilters"
              />
            </div>
          </div>
          <div class="w-40">
            <label class="label">排序方式</label>
            <select v-model="filterSort" class="input !py-1.5 text-xs" @change="emitFilters">
              <button><selectedcontent></selectedcontent></button>
              <option value="">默认排序</option>
              <option value="score_desc">得分从高到低</option>
              <option value="score_asc">得分从低到高</option>
            </select>
          </div>
          <button v-if="hasFilter" class="btn-ghost btn-xs" @click="resetFilters">
            重置筛选
          </button>
        </div>
        <div
          v-if="selectionAvailable"
          class="mx-5 mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-indigo-100 bg-indigo-50/50 px-3 py-2 text-xs text-slate-600"
        >
          <span class="font-medium text-slate-800">批量重试</span>
          <span class="text-slate-500">已选 {{ selectedCaseIds.length }} 条</span>
          <span class="h-3 w-px bg-slate-300" />
          <span class="text-[11px] text-slate-500">按状态勾选</span>
          <button
            v-for="group in candidateGroups"
            :key="group.status"
            type="button"
            class="rounded-full border px-2 py-0.5 text-[11px] transition"
            :class="
              isStatusGroupSelected(group)
                ? 'border-indigo-300 bg-indigo-100 text-indigo-700'
                : 'border-slate-200 bg-white text-slate-600 hover:border-indigo-200 hover:text-indigo-600'
            "
            :title="`${isStatusGroupSelected(group) ? '取消勾选' : '勾选'}全部「${statusLabel(group.status)}」用例（${group.caseIds.length} 条，含其他页）`"
            @click="toggleStatusSelection(group)"
          >
            {{ statusLabel(group.status) }} {{ group.caseIds.length }}
          </button>
          <span v-if="retryCandidates && candidateGroups.length === 0" class="text-[11px] text-slate-400">
            暂无可重试用例
          </span>
          <button
            v-if="retryCandidates && selectableCandidateTotal > 0 && !allCandidatesSelected"
            type="button"
            class="text-[11px] text-indigo-600 hover:underline"
            @click="selectAllCandidates"
          >
            全选可重试
          </button>
          <button
            v-if="selectedCaseIds.length"
            type="button"
            class="text-[11px] text-slate-500 hover:text-slate-700 hover:underline"
            @click="clearSelection"
          >
            清空
          </button>
          <div class="ml-auto flex items-center gap-2">
            <span class="hidden text-[11px] text-slate-500 sm:inline">
              勾选后可一次提交，按顺序逐条执行
            </span>
            <button
              class="btn-primary btn-xs"
              :disabled="selectedCaseIds.length === 0"
              @click="submitBatchRetry"
            >
              重试选中（{{ selectedCaseIds.length }}）
            </button>
          </div>
        </div>
        <table class="table-shell mt-2">
          <thead>
            <tr>
              <th v-if="selectionAvailable" class="th w-8">
                <input
                  type="checkbox"
                  class="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                  :checked="pageAllSelected"
                  :indeterminate="pageSomeSelected"
                  :disabled="pageSelectableIds.length === 0"
                  title="勾选 / 取消本页全部可重试用例"
                  @change="togglePageSelection"
                />
              </th>
              <th class="th">执行号</th>
              <th class="th">用例</th>
              <th class="th">状态</th>
              <th class="th">
                <button
                  class="inline-flex items-center gap-1 transition"
                  :class="filterSort ? 'text-indigo-600' : 'hover:text-slate-700'"
                  title="点击切换得分排序"
                  @click="toggleScoreSort"
                >
                  得分
                  <span class="text-[9px] leading-none">
                    {{ filterSort === 'score_asc' ? '▲' : filterSort === 'score_desc' ? '▼' : '↕' }}
                  </span>
                </button>
              </th>
              <th class="th">轮次</th>
              <th class="th">尝试</th>
              <th class="th">时延</th>
              <th class="th">开始时间</th>
              <th class="th text-right">结果</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in executions"
              :key="item.id"
              class="cursor-pointer"
              @click="emit('select-execution', item.id)"
            >
              <td v-if="selectionAvailable" class="td">
                <input
                  type="checkbox"
                  class="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                  :checked="isCaseSelected(item)"
                  :disabled="!canRetryCase(item)"
                  :title="canRetryCase(item) ? '勾选该用例' : '该用例当前不可重试'"
                  @click.stop
                  @change="toggleCaseSelection(item)"
                />
              </td>
              <td class="td font-mono text-xs text-slate-500">#{{ item.id }}</td>
              <td class="td">
                <button
                  v-if="item.case_id !== null && item.case_id !== undefined"
                  type="button"
                  class="max-w-[240px] truncate font-mono text-xs text-slate-800 underline decoration-dotted underline-offset-2 transition hover:text-indigo-600"
                  :title="`查看用例详情：${item.case_code || `用例 #${item.case_id}`}`"
                  @click.stop="emit('open-case', item.case_id)"
                >
                  {{ item.case_code || `用例 #${item.case_id}` }}
                </button>
                <p v-else class="max-w-[240px] truncate font-mono text-xs text-slate-800">
                  {{ item.case_code || '—' }}
                </p>
                <p class="mt-0.5 max-w-[240px] truncate text-[11px] text-slate-500">
                  {{ item.case_name || '—' }} · v{{ item.case_version }}
                  <span v-if="item.attempt_no > 1" class="text-amber-700"> · 第 {{ item.attempt_no }} 次尝试</span>
                </p>
              </td>
              <td class="td"><StatusPill :status="item.status" size="sm" /></td>
              <td class="td">
                <div class="flex items-center gap-2">
                  <div class="h-1 w-14 overflow-hidden rounded-full bg-slate-100">
                    <div
                      class="h-full rounded-full transition-all"
                      :class="scoreBarClass(item.total_score)"
                      :style="{ width: `${scorePercent(item.total_score)}%` }"
                    />
                  </div>
                  <span
                    class="tabular-nums"
                    :class="isLowScore(item.total_score) ? 'font-medium text-rose-600' : ''"
                  >
                    {{ formatScore(item.total_score) }}
                  </span>
                </div>
              </td>
              <td class="td tabular-nums">{{ item.turns_used }}</td>
              <td class="td tabular-nums">{{ item.attempt_no }}</td>
              <td class="td tabular-nums">{{ formatMs(item.latency_ms) }}</td>
              <td class="td text-xs text-slate-500">{{ formatDateTime(item.started_at) }}</td>
              <td class="td text-right">
                <div class="inline-flex items-center gap-1">
                  <button
                    v-if="latestPerCase && auth.can('execution:retry') && isCaseRetrying(item)"
                    class="btn-ghost btn-xs"
                    disabled
                    title="该用例正在重试，完成后可再次提交"
                  >
                    重试中
                  </button>
                  <button
                    v-else-if="auth.can('execution:retry') && canRetryCase(item)"
                    class="btn-ghost btn-xs"
                    title="在当前批次内重跑该用例，作为最新一次执行结果"
                    @click.stop="emit('retry-case', item.case_id as number)"
                  >
                    重试
                  </button>
                  <button class="btn-ghost btn-xs" @click.stop="emit('select-execution', item.id)">
                    查看结果
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <EmptyState
          v-if="executions.length === 0"
          compact
          :title="hasFilter ? '没有符合筛选条件的执行记录' : '暂无执行记录'"
          :description="
            hasFilter
              ? '可调整执行状态、得分区间或搜索关键词后重试。'
              : '任务启动后，每条用例的结果会实时出现在这里。'
          "
        >
          <button v-if="hasFilter" class="btn-ghost btn-xs mt-1" @click="resetFilters">
            重置筛选
          </button>
        </EmptyState>
        <PaginationBar
          v-if="executionTotal > 0"
          :total="executionTotal"
          :page="executionPage"
          :page-size="executionPageSize"
          :page-size-options="executionPageSizeOptions"
          :show-summary="false"
          unit="次执行"
          @update:page="(next) => emit('page-change', next)"
          @update:page-size="(size) => emit('page-size-change', size)"
        />
      </div>
      </template>
    </template>
  </section>
</template>
