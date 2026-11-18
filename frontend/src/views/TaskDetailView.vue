<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useConfirm } from '../composables/useConfirm'
import { buildWebSocketUrl } from '../api/client'
import EmptyState from '../components/EmptyState.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import BatchMonitor from './components/BatchMonitor.vue'
import type {
  BatchTask,
  CaseExecutionItem,
  TaskMetrics,
  TaskProgressEvent,
  TaskRunSummary,
} from '../types'
import { errorMessage } from '../utils/errors'
import { formatDateTime, formatPercent } from '../utils/format'
import { taskScheduleLabel } from '../utils/schedule'
import { useToastStore } from '../stores/toast'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()
const confirm = useConfirm()

const rootId = computed(() => Number(route.params.id))

// 周期计划本身，以及当前展示的执行任务（普通任务就是自身，周期计划是选中的一次派生执行）
const plan = ref<BatchTask | null>(null)
const task = ref<BatchTask | null>(null)
const metrics = ref<TaskMetrics | null>(null)
const executions = ref<CaseExecutionItem[]>([])
const executionTotal = ref(0)
const executionPage = ref(1)
const executionPageSize = 30
const latestPerCase = ref(true)
const executionStatus = ref('')
const executionKeyword = ref('')
const live = ref(false)
const loading = ref(true)
const notFound = ref(false)
const exporting = ref(false)

// 周期计划的历次执行
const runs = ref<BatchTask[]>([])
const runsTotal = ref(0)
const runsPage = ref(1)
const runsPageSize = 10
const runsSummary = ref<TaskRunSummary | null>(null)
const runsLoading = ref(false)
const selectedRunId = ref<number | null>(null)

let socket: WebSocket | null = null
let pollTimer: number | null = null
let detailSeq = 0

const isPeriodicPlan = computed(() => Boolean(plan.value))
const runsPageCount = computed(() => Math.max(1, Math.ceil(runsTotal.value / runsPageSize)))
const planActive = computed(
  () => Boolean(plan.value?.schedule_active && plan.value.status === 'SCHEDULED'),
)

function isPeriodic(task: BatchTask): boolean {
  return ['DAILY', 'WEEKLY'].includes(task.schedule_type)
}

/** 需要以「计划 + 历次执行」视角展示：周期计划，或连续多轮任务。 */
function isMultiRunRoot(task: BatchTask): boolean {
  return isPeriodic(task) || (task.repeat_rounds ?? 1) > 1
}

const isSchedulePlan = computed(
  () => Boolean(plan.value && plan.value.schedule_type !== 'IMMEDIATE'),
)
const planHeading = computed(() => {
  const current = plan.value
  if (!current) return '批量任务'
  if (isPeriodic(current)) return '周期计划'
  if (current.schedule_type === 'SCHEDULED') return '定时计划'
  return '连续多轮任务'
})
const planRounds = computed(() => Math.max(1, plan.value?.repeat_rounds ?? 1))
const planHint = computed(() => {
  const current = plan.value
  if (!current) return ''
  if (!isSchedulePlan.value) {
    return planRounds.value > 1
      ? `连续 ${planRounds.value} 轮执行：每轮跑完后立即自动开始下一轮，各轮独立保留结果。`
      : '创建后立即开始执行。'
  }
  if (planActive.value) {
    return isPeriodic(current)
      ? `计划生效中，到点会连续执行 ${planRounds.value} 轮：上一轮结束后立即开始下一轮。`
      : `计划生效中，到点后自动连续执行 ${planRounds.value} 轮：上一轮结束后立即开始下一轮。`
  }
  if (current.last_run_at) {
    return `计划已触发${planRounds.value > 1 ? `，连续执行 ${planRounds.value} 轮` : ''}；历史执行见下方列表，可查看每一轮结果。`
  }
  return '计划已停止，不再自动触发；启用后可手动立即执行，已生成的历史执行不受影响。'
})

function planTypeLabel(type: BatchTask['schedule_type']): string {
  if (type === 'DAILY') return '每天定时'
  if (type === 'WEEKLY') return '每周定时'
  if (type === 'SCHEDULED') return '指定时间'
  return '立即执行'
}

function reportError(exc: unknown): void {
  toast.error(errorMessage(exc))
}

function executionQuery() {
  return {
    page: executionPage.value,
    page_size: executionPageSize,
    latest_per_case: latestPerCase.value,
    status: executionStatus.value || undefined,
    keyword: executionKeyword.value.trim() || undefined,
  }
}

function matchesExecutionFilters(item: CaseExecutionItem): boolean {
  if (executionStatus.value && item.status !== executionStatus.value) return false
  const keyword = executionKeyword.value.trim().toLowerCase()
  if (!keyword) return true
  return [item.case_code, item.case_name].some((value) =>
    (value ?? '').toLowerCase().includes(keyword),
  )
}

function runProgress(run: BatchTask): number {
  if (!run.total_cases) return 0
  return Math.min(100, Math.round((run.finished_cases / run.total_cases) * 100))
}

function runPassRate(run: BatchTask): string {
  if (!run.finished_cases) return '—'
  return formatPercent((run.pass_count / run.finished_cases) * 100, 1)
}

// ---------------------------------------------------------- 数据加载
async function loadRuns(preferLatest = false): Promise<boolean> {
  const current = plan.value
  if (!current) return false
  runsLoading.value = true
  try {
    const data = await api.tasks.runs(current.id, {
      page: runsPage.value,
      page_size: runsPageSize,
    })
    runs.value = data.items
    runsTotal.value = data.total
    runsSummary.value = data.summary
    const onPage = data.items.some((item) => item.id === selectedRunId.value)
    if (preferLatest || !onPage) {
      const preferred =
        data.items.find((item) => ['RUNNING', 'PENDING'].includes(item.status)) ??
        data.items[0]
      if (preferred) {
        await selectRun(preferred.id, false)
        return true
      }
      selectedRunId.value = null
      task.value = null
      metrics.value = null
      executions.value = []
      executionTotal.value = 0
    }
    return true
  } catch (exc) {
    reportError(exc)
    return false
  } finally {
    runsLoading.value = false
  }
}

async function loadTaskDetail(id: number): Promise<void> {
  const seq = ++detailSeq
  const [detail, taskMetrics, executionPageData] = await Promise.all([
    api.tasks.get(id),
    api.tasks.metrics(id),
    api.tasks.executions(id, executionQuery()),
  ])
  if (seq !== detailSeq) return
  task.value = detail
  metrics.value = taskMetrics
  executions.value = executionPageData.items
  executionTotal.value = executionPageData.total
  patchRun(detail)
  syncLive(detail)
}

async function loadRoot(): Promise<void> {
  const id = rootId.value
  if (!Number.isFinite(id) || id <= 0) {
    notFound.value = true
    loading.value = false
    return
  }
  loading.value = true
  notFound.value = false
  try {
    const detail = await api.tasks.get(id)
    if (isMultiRunRoot(detail)) {
      plan.value = detail
      runsPage.value = 1
      selectedRunId.value = null
      await loadRuns(true)
    } else {
      plan.value = null
      runs.value = []
      runsSummary.value = null
      runsTotal.value = 0
      selectedRunId.value = null
      await loadTaskDetail(id)
    }
  } catch (exc) {
    plan.value = null
    task.value = null
    metrics.value = null
    executions.value = []
    runs.value = []
    runsSummary.value = null
    runsTotal.value = 0
    notFound.value = true
    reportError(exc)
  } finally {
    loading.value = false
  }
}

function patchRun(next: BatchTask): void {
  const index = runs.value.findIndex((item) => item.id === next.id)
  if (index >= 0) runs.value.splice(index, 1, next)
}

function syncLive(detail: BatchTask): void {
  if (['RUNNING', 'PENDING'].includes(detail.status)) {
    connectSocket(detail.id)
    startPolling(detail.id)
  } else {
    disconnectSocket()
    stopPolling()
  }
}

function applyEvent(event: TaskProgressEvent): void {
  if (event.type === 'ping') return
  if (event.metrics) metrics.value = event.metrics
  if (task.value && event.task_id === task.value.id) {
    if (event.status) task.value.status = event.status
    if (event.control_state) task.value.control_state = event.control_state
    if (typeof event.finished_cases === 'number') task.value.finished_cases = event.finished_cases
    if (typeof event.total_cases === 'number') task.value.total_cases = event.total_cases
    if (typeof event.pass_count === 'number') task.value.pass_count = event.pass_count
    if (typeof event.fail_count === 'number') task.value.fail_count = event.fail_count
    if (typeof event.error_count === 'number') task.value.error_count = event.error_count
    if (typeof event.max_turns_count === 'number') task.value.max_turns_count = event.max_turns_count
    patchRun({ ...task.value })
  }
  if (event.execution) {
    const next = event.execution
    const visible = matchesExecutionFilters(next)
    if (latestPerCase.value) {
      const index = executions.value.findIndex((item) => item.case_id === next.case_id)
      if (index >= 0) {
        if (visible) {
          const current = executions.value[index]
          executions.value.splice(index, 1, {
            ...next,
            attempt_count: Math.max(current.attempt_count ?? 1, next.attempt_no),
          })
        } else {
          executions.value.splice(index, 1)
          executionTotal.value = Math.max(0, executionTotal.value - 1)
        }
      } else if (visible) {
        executions.value.unshift({ ...next, attempt_count: Math.max(1, next.attempt_no) })
        executionTotal.value += 1
      }
      executions.value = executions.value.slice(0, executionPageSize)
    } else {
      const index = executions.value.findIndex((item) => item.id === next.id)
      if (index >= 0) {
        if (visible) executions.value.splice(index, 1, next)
        else {
          executions.value.splice(index, 1)
          executionTotal.value = Math.max(0, executionTotal.value - 1)
        }
      } else if (visible) {
        executions.value.unshift(next)
        executionTotal.value += 1
      }
      executions.value = executions.value.slice(0, executionPageSize)
    }
  }
  if (event.status && ['COMPLETED', 'ABORTED', 'FAILED'].includes(event.status)) {
    disconnectSocket()
    stopPolling()
    window.setTimeout(() => {
      if (isPeriodicPlan.value) void loadRuns()
      else void loadRoot()
    }, 500)
  }
}

function connectSocket(id: number): void {
  if (socket) return
  try {
    const ws = new WebSocket(buildWebSocketUrl(`/ws/tasks/${id}`))
    socket = ws
    ws.onopen = () => {
      live.value = true
    }
    ws.onmessage = (message) => {
      try {
        applyEvent(JSON.parse(String(message.data)) as TaskProgressEvent)
      } catch {
        // 忽略非 JSON 帧
      }
    }
    ws.onerror = () => {
      live.value = false
    }
    ws.onclose = () => {
      if (socket === ws) {
        socket = null
        live.value = false
      }
    }
  } catch {
    live.value = false
  }
}

function disconnectSocket(): void {
  if (socket) {
    socket.onclose = null
    socket.close()
    socket = null
  }
  live.value = false
}

function startPolling(id: number): void {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    try {
      metrics.value = await api.tasks.metrics(id)
      if (!live.value) {
        const detail = await api.tasks.get(id)
        if (task.value?.id !== detail.id) return
        task.value = detail
        patchRun(detail)
        await loadExecutions(id)
        if (!['RUNNING', 'PENDING'].includes(detail.status)) {
          stopPolling()
          disconnectSocket()
          if (isPeriodicPlan.value) void loadRuns()
        }
      }
    } catch {
      stopPolling()
    }
  }, 4000)
}

function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

async function loadExecutions(id: number): Promise<void> {
  try {
    const pageData = await api.tasks.executions(id, executionQuery())
    executions.value = pageData.items
    executionTotal.value = pageData.total
  } catch (exc) {
    reportError(exc)
  }
}

// ---------------------------------------------------------- 交互
async function refresh(): Promise<void> {
  if (plan.value) {
    try {
      plan.value = await api.tasks.get(plan.value.id)
    } catch {
      // 计划刷新失败时保留原展示，由 runs 请求给出错误
    }
    if (await loadRuns()) toast.success('刷新成功')
    return
  }
  if (!task.value) return
  try {
    await loadTaskDetail(task.value.id)
    toast.success('刷新成功')
  } catch (exc) {
    reportError(exc)
  }
}

async function selectRun(runId: number, resetPage = true): Promise<void> {
  if (selectedRunId.value === runId && task.value?.id === runId) return
  disconnectSocket()
  stopPolling()
  selectedRunId.value = runId
  if (resetPage) executionPage.value = 1
  try {
    await loadTaskDetail(runId)
  } catch (exc) {
    reportError(exc)
  }
}

function changeRunsPage(next: number): void {
  if (next < 1 || next > runsPageCount.value || next === runsPage.value) return
  runsPage.value = next
  void loadRuns()
}

function changeExecutionPage(next: number): void {
  if (!task.value) return
  executionPage.value = next
  void loadExecutions(task.value.id)
}

function changeMode(next: boolean): void {
  if (latestPerCase.value === next) return
  latestPerCase.value = next
  executionPage.value = 1
  if (task.value) void loadExecutions(task.value.id)
}

function applyExecutionFilters(next: { status: string; keyword: string }): void {
  executionStatus.value = next.status
  executionKeyword.value = next.keyword
  executionPage.value = 1
  if (task.value) void loadExecutions(task.value.id)
}

async function startTask(): Promise<void> {
  if (!task.value) return
  try {
    await api.tasks.start(task.value.id)
    toast.success('任务已启动')
    await loadTaskDetail(task.value.id)
    if (isPeriodicPlan.value) void loadRuns()
  } catch (exc) {
    reportError(exc)
  }
}

async function abortTask(): Promise<void> {
  if (!task.value) return
  const ok = await confirm({
    title: '终止任务',
    message: '确定终止当前任务吗？',
    detail: '已完成的执行记录会保留，可通过「失败断点重跑」继续。',
    confirmText: '终止',
    tone: 'warning',
  })
  if (!ok) return
  try {
    await api.tasks.abort(task.value.id)
    toast.success('终止指令已下发')
    await loadTaskDetail(task.value.id)
    if (isPeriodicPlan.value) void loadRuns()
  } catch (exc) {
    reportError(exc)
  }
}

async function retryFailed(): Promise<void> {
  if (!task.value) return
  try {
    const result = await api.tasks.retryFailed(task.value.id)
    toast.success(result.message)
    await loadTaskDetail(task.value.id)
    if (isPeriodicPlan.value) void loadRuns()
  } catch (exc) {
    reportError(exc)
  }
}

async function exportResults(): Promise<void> {
  if (!task.value || exporting.value) return
  exporting.value = true
  try {
    const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')
    await api.tasks.exportResults(
      task.value.id,
      latestPerCase.value,
      `${task.value.code}_results_${stamp}.xlsx`,
    )
    toast.success('已导出本次批量执行结果')
  } catch (exc) {
    reportError(exc)
  } finally {
    exporting.value = false
  }
}

async function runPlanNow(): Promise<void> {
  if (!plan.value) return
  if (!planActive.value) {
    toast.error('计划已停止，请先启用计划再立即执行')
    return
  }
  try {
    const run = await api.tasks.runNow(plan.value.id)
    toast.success(`已按计划立即执行一次：${run.code}`)
    runsPage.value = 1
    await loadRuns(true)
  } catch (exc) {
    reportError(exc)
  }
}

async function startRootTask(): Promise<void> {
  if (!plan.value) return
  try {
    await api.tasks.start(plan.value.id)
    toast.success('任务已启动')
    plan.value = await api.tasks.get(plan.value.id)
    runsPage.value = 1
    await loadRuns(true)
  } catch (exc) {
    reportError(exc)
  }
}

async function togglePlan(active: boolean): Promise<void> {
  if (!plan.value) return
  try {
    plan.value = await api.tasks.updateSchedule(plan.value.id, { active })
    toast.success(active ? '定时计划已启用' : '定时计划已停用')
  } catch (exc) {
    reportError(exc)
  }
}

async function stopPlan(): Promise<void> {
  if (!plan.value) return
  const ok = await confirm({
    title: '停止定时计划',
    message: '确定停止该定时计划吗？',
    detail: '已生成的历史任务与执行记录不会删除。',
    confirmText: '停止',
    tone: 'warning',
  })
  if (!ok) return
  try {
    await api.tasks.abort(plan.value.id)
    plan.value = await api.tasks.get(plan.value.id)
    toast.success('定时计划已停止')
  } catch (exc) {
    reportError(exc)
  }
}

function openExecution(executionId: number): void {
  void router.push({
    name: 'trace',
    query: { execution: String(executionId), from: 'task' },
  })
}

function backToList(): void {
  void router.push({ name: 'tasks' })
}

watch(
  rootId,
  (id, previous) => {
    if (id === previous) return
    disconnectSocket()
    stopPolling()
    notFound.value = false
    runsPage.value = 1
    selectedRunId.value = null
    executionPage.value = 1
    latestPerCase.value = true
    executionStatus.value = ''
    executionKeyword.value = ''
    void loadRoot()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  disconnectSocket()
  stopPolling()
})
</script>

<template>
  <div class="space-y-4">
    <button
      class="inline-flex items-center gap-1 text-xs text-slate-500 transition hover:text-ink"
      @click="backToList"
    >
      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="m15 18-6-6 6-6" />
      </svg>
      返回批量任务列表
    </button>

    <PageHeader
      :eyebrow="
        plan
          ? `Tasks · 周期计划 · ${plan.code}`
          : task
            ? `Tasks · 批量任务详情 · ${task.code}`
            : 'Tasks · 批量任务详情'
      "
      :title="plan?.name || task?.name || '批量任务详情'"
      :description="
        plan?.description ||
        task?.description ||
        '查看任务运行进度、指标与逐用例结果，并执行启动、终止与失败重跑等操作。'
      "
    >
      <template #actions>
        <button class="btn-ghost" :disabled="!plan && !task" @click="refresh">刷新</button>
      </template>
    </PageHeader>

    <div v-if="loading && !plan && !task" class="card px-6 py-16 text-center text-sm text-slate-500">
      正在加载批量任务…
    </div>

    <div v-else-if="notFound && !plan && !task" class="card">
      <EmptyState
        title="批量任务不存在或已删除"
        description="请返回批量任务列表重新选择目标任务。"
      >
        <button class="btn-primary btn-xs mt-2" @click="backToList">返回任务列表</button>
      </EmptyState>
    </div>

    <template v-else-if="plan">
      <!-- 周期计划配置与操作 -->
      <section class="card overflow-hidden">
        <header class="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="text-sm font-semibold text-slate-900">{{ planHeading }}</h2>
              <span
                v-if="isSchedulePlan"
                class="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] text-sky-700"
              >
                {{ planTypeLabel(plan.schedule_type) }}
              </span>
              <span
                v-if="planRounds > 1"
                class="rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[10px] text-indigo-700"
              >
                连续 {{ planRounds }} 轮
              </span>
              <StatusPill
                :status="plan.status"
                :label="planActive || plan.status !== 'ABORTED' ? null : '已停止'"
              />
            </div>
            <p class="mt-1 text-xs text-slate-600">{{ taskScheduleLabel(plan) }}</p>
            <p class="mt-0.5 text-[11px] text-slate-500">{{ planHint }}</p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button v-if="isSchedulePlan && planActive" class="btn-primary btn-xs" @click="runPlanNow">
              立即执行
            </button>
            <button
              v-if="!isSchedulePlan && plan.status === 'PENDING'"
              class="btn-primary btn-xs"
              @click="startRootTask"
            >
              启动
            </button>
            <button
              v-if="isSchedulePlan && !planActive && plan.status === 'ABORTED'"
              class="btn-ghost btn-xs"
              @click="togglePlan(true)"
            >
              启用计划
            </button>
            <button v-if="isSchedulePlan && planActive" class="btn-danger btn-xs" @click="stopPlan">
              停止计划
            </button>
          </div>
        </header>
        <div class="grid grid-cols-2 gap-4 px-5 py-4 sm:grid-cols-3 xl:grid-cols-6">
          <div v-if="isSchedulePlan">
            <p class="text-[10px] uppercase tracking-wider text-slate-500">下次触发</p>
            <p class="mt-0.5 text-sm font-medium tabular-nums text-slate-900">
              {{ planActive ? formatDateTime(plan.next_run_at) : '—' }}
            </p>
          </div>
          <div v-if="isSchedulePlan">
            <p class="text-[10px] uppercase tracking-wider text-slate-500">上次触发</p>
            <p class="mt-0.5 text-sm font-medium tabular-nums text-slate-900">
              {{ formatDateTime(plan.last_run_at) }}
            </p>
          </div>
          <div v-else>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">开始时间</p>
            <p class="mt-0.5 text-sm font-medium tabular-nums text-slate-900">
              {{ formatDateTime(plan.started_at) }}
            </p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">用例数</p>
            <p class="mt-0.5 text-sm font-medium tabular-nums text-slate-900">{{ plan.total_cases }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">执行环境</p>
            <p class="mt-0.5 truncate text-sm font-medium text-slate-900">
              {{ plan.environment_name || '未绑定环境' }}
            </p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">并发 / QPS</p>
            <p class="mt-0.5 text-sm font-medium tabular-nums text-slate-900">
              {{ plan.concurrency }} / {{ plan.qps_limit }}
            </p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">失败重试</p>
            <p class="mt-0.5 text-sm font-medium text-slate-900">
              {{ plan.retry_on_env_error ? `最多 ${plan.max_env_retries} 次` : '不重试' }}
            </p>
          </div>
        </div>
      </section>

      <!-- 历次执行统计 -->
      <div class="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <MetricCard
          label="已执行次数"
          :value="runsSummary?.total_runs ?? 0"
          :hint="`运行中 ${runsSummary?.running ?? 0} · 等待 ${runsSummary?.pending ?? 0}`"
          accent="indigo"
        />
        <MetricCard
          label="已完成"
          :value="runsSummary?.completed ?? 0"
          hint="正常跑完的批次"
          accent="emerald"
        />
        <MetricCard
          label="失败 / 终止"
          :value="(runsSummary?.failed ?? 0) + (runsSummary?.aborted ?? 0)"
          :hint="`失败 ${runsSummary?.failed ?? 0} · 终止 ${runsSummary?.aborted ?? 0}`"
          accent="rose"
        />
        <MetricCard
          label="聚合通过率"
          :value="formatPercent(runsSummary?.pass_rate, 1)"
          :hint="`累计完成 ${runsSummary?.finished_cases ?? 0} 条用例执行`"
          accent="sky"
          :progress="runsSummary?.pass_rate ?? null"
        />
      </div>

      <!-- 历次执行列表 -->
      <section class="card overflow-hidden">
        <header class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-5 py-3">
          <div>
            <h2 class="panel-title">历次执行</h2>
            <p class="mt-0.5 text-[11px] text-slate-500">
              共 {{ runsTotal }} 次执行；选择一次执行查看该批次的逐用例结果
            </p>
          </div>
          <span v-if="runsLoading" class="text-[11px] text-slate-500">加载中…</span>
        </header>
        <div class="overflow-x-auto">
          <table class="table-shell">
            <thead>
              <tr>
                <th class="th">执行批次</th>
                <th class="th">状态</th>
                <th class="th">进度</th>
                <th class="th">通过率</th>
                <th class="th">开始时间</th>
                <th class="th">结束时间</th>
                <th class="th text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="run in runs"
                :key="run.id"
                class="cursor-pointer"
                :class="run.id === selectedRunId ? 'bg-indigo-50/70' : ''"
                @click="selectRun(run.id)"
              >
                <td class="td">
                  <div class="flex flex-wrap items-center gap-1.5">
                    <p class="max-w-[240px] truncate text-sm font-medium text-slate-800">
                      {{ run.name }}
                    </p>
                    <span
                      v-if="(run.repeat_rounds ?? 1) > 1"
                      class="tag border-indigo-200 bg-indigo-50 text-indigo-700"
                    >
                      第 {{ run.round_index }}/{{ run.repeat_rounds }} 轮
                    </span>
                  </div>
                  <p class="mt-0.5 font-mono text-[11px] text-slate-500">{{ run.code }}</p>
                </td>
                <td class="td"><StatusPill :status="run.status" size="sm" /></td>
                <td class="td">
                  <div class="flex items-center gap-2">
                    <div class="h-1.5 w-20 overflow-hidden rounded-full bg-slate-100">
                      <div class="h-full rounded-full bg-indigo-500" :style="{ width: `${runProgress(run)}%` }" />
                    </div>
                    <span class="text-[11px] tabular-nums text-slate-600">
                      {{ run.finished_cases }}/{{ run.total_cases }}
                    </span>
                  </div>
                </td>
                <td class="td text-xs tabular-nums text-slate-700">{{ runPassRate(run) }}</td>
                <td class="td text-xs text-slate-500">{{ formatDateTime(run.started_at) }}</td>
                <td class="td text-xs text-slate-500">{{ formatDateTime(run.finished_at) }}</td>
                <td class="td text-right">
                  <button class="btn-ghost btn-xs" @click.stop="selectRun(run.id)">
                    {{ run.id === selectedRunId ? '查看中' : '查看结果' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
          <EmptyState
            v-if="!runsLoading && runs.length === 0"
            compact
            title="还没有执行过"
            description="到点触发或手动执行后，每次执行（每轮）都会生成一个独立批次。"
          />
        </div>
        <div
          v-if="runsTotal > runsPageSize"
          class="flex items-center justify-between border-t border-slate-200 px-5 py-3"
        >
          <span class="text-[11px] text-slate-500">第 {{ runsPage }} / {{ runsPageCount }} 页</span>
          <div class="flex gap-1">
            <button class="btn-ghost btn-xs" :disabled="runsPage <= 1" @click="changeRunsPage(runsPage - 1)">
              上一页
            </button>
            <button
              class="btn-ghost btn-xs"
              :disabled="runsPage >= runsPageCount"
              @click="changeRunsPage(runsPage + 1)"
            >
              下一页
            </button>
          </div>
        </div>
      </section>

      <!-- 选中批次的逐用例结果 -->
      <div v-if="task" class="space-y-2">
        <p class="text-[11px] text-slate-500">
          当前查看：
          <span class="font-mono text-slate-700">{{ task.code }}</span>
          <span class="ml-1">{{ task.name }}</span>
        </p>
        <BatchMonitor
          :task="task"
          :metrics="metrics"
          :executions="executions"
          :live="live"
          :execution-total="executionTotal"
          :execution-page="executionPage"
          :execution-page-size="executionPageSize"
          :latest-per-case="latestPerCase"
          :execution-status="executionStatus"
          :execution-keyword="executionKeyword"
          :exporting="exporting"
          @start="startTask"
          @abort="abortTask"
          @retry="retryFailed"
          @run-now="runPlanNow"
          @stop-plan="stopPlan"
          @toggle-plan="togglePlan"
          @select-execution="openExecution"
          @page-change="changeExecutionPage"
          @mode-change="changeMode"
          @filter-change="applyExecutionFilters"
          @export="exportResults"
        />
      </div>
    </template>

    <template v-else-if="task">
      <BatchMonitor
        :task="task"
        :metrics="metrics"
        :executions="executions"
        :live="live"
        :execution-total="executionTotal"
        :execution-page="executionPage"
        :execution-page-size="executionPageSize"
        :latest-per-case="latestPerCase"
        :execution-status="executionStatus"
        :execution-keyword="executionKeyword"
        :exporting="exporting"
        @start="startTask"
        @abort="abortTask"
        @retry="retryFailed"
        @run-now="runPlanNow"
        @stop-plan="stopPlan"
        @toggle-plan="togglePlan"
        @select-execution="openExecution"
        @page-change="changeExecutionPage"
        @mode-change="changeMode"
        @filter-change="applyExecutionFilters"
        @export="exportResults"
      />
    </template>
  </div>
</template>
