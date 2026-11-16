<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { buildWebSocketUrl } from '../api/client'
import BaseModal from '../components/BaseModal.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import ScheduleField from '../components/ScheduleField.vue'
import StatusPill from '../components/StatusPill.vue'
import EmptyState from '../components/EmptyState.vue'
import BatchMonitor from './components/BatchMonitor.vue'
import type {
  BatchTask,
  CaseExecutionItem,
  Environment,
  TaskMetrics,
  TaskProgressEvent,
  TestCase,
} from '../types'
import { errorMessage } from '../utils/errors'
import { caseTypeLabel, formatDateTime, formatPercent, statusLabel } from '../utils/format'
import {
  defaultSchedule,
  isPlanTask,
  normalizeSchedule,
  schedulePayload,
  scheduleSummary,
  scheduleValidationError,
} from '../utils/schedule'
import { useToastStore } from '../stores/toast'

const router = useRouter()
const route = useRoute()
const toast = useToastStore()

const cases = ref<TestCase[]>([])
const tasks = ref<BatchTask[]>([])
const environments = ref<Environment[]>([])
const selectedTaskId = ref<number | null>(null)
const task = ref<BatchTask | null>(null)
const metrics = ref<TaskMetrics | null>(null)
const executions = ref<CaseExecutionItem[]>([])
const executionTotal = ref(0)
const executionPage = ref(1)
const executionPageSize = 30
const live = ref(false)
const loadingBase = ref(true)

const showCreate = ref(false)
const creating = ref(false)
const caseKeyword = ref('')
const form = reactive({
  name: '',
  description: '',
  environment_id: null as number | null,
  concurrency: 3,
  qps_limit: 5,
  retry_on_env_error: true,
  max_env_retries: 2,
  retry_backoff_seconds: 3,
  case_scope: 'SELECTED' as 'SELECTED' | 'ALL_ACTIVE',
  case_ids: [] as number[],
  auto_start: true,
  schedule: defaultSchedule(),
})

const activeCaseCount = computed(
  () => cases.value.filter((item) => item.status === 'ACTIVE').length,
)

const scopeCount = computed(() =>
  form.case_scope === 'ALL_ACTIVE' ? activeCaseCount.value : form.case_ids.length,
)

let socket: WebSocket | null = null
let pollTimer: number | null = null

const activeTask = computed(() => tasks.value.find((item) => item.id === selectedTaskId.value) ?? task.value)

const filteredCases = computed(() => {
  const keyword = caseKeyword.value.trim().toLowerCase()
  return cases.value.filter((item) => {
    if (!keyword) return true
    return (
      item.code.toLowerCase().includes(keyword) ||
      item.name.toLowerCase().includes(keyword) ||
      (item.tags ?? []).some((tag) => tag.toLowerCase().includes(keyword))
    )
  })
})

const runningCount = computed(() => tasks.value.filter((item) => item.status === 'RUNNING').length)

const recentTasks = computed(() => tasks.value.slice(0, 12))

function reportError(exc: unknown): void {
  toast.error(errorMessage(exc))
}

async function loadAllCases(): Promise<TestCase[]> {
  const pageSize = 200
  const first = await api.cases.list({ page_size: pageSize, page: 1 })
  const all = [...first.items]
  const totalPages = Math.ceil(first.total / pageSize)
  for (let page = 2; page <= totalPages; page += 1) {
    const next = await api.cases.list({ page_size: pageSize, page })
    all.push(...next.items)
  }
  return all
}

async function loadBase(): Promise<void> {
  loadingBase.value = true
  try {
    const [allCases, taskPage, envPage] = await Promise.all([
      loadAllCases(),
      api.tasks.list({ page_size: 50 }),
      api.environments.list({ page_size: 100 }),
    ])
    cases.value = allCases
    tasks.value = taskPage.items
    environments.value = envPage.items
    const requestedId = Number(route.query.task)
    const preferred =
      (requestedId ? tasks.value.find((item) => item.id === requestedId) : undefined) ??
      tasks.value.find((item) => item.status === 'RUNNING') ??
      tasks.value.find((item) => item.status === 'PAUSED') ??
      tasks.value[0]
    if (preferred) await selectTask(preferred.id)
  } catch (exc) {
    reportError(exc)
  } finally {
    loadingBase.value = false
  }
}

async function refreshTasks(): Promise<void> {
  try {
    const page = await api.tasks.list({ page_size: 50 })
    tasks.value = page.items
  } catch (exc) {
    reportError(exc)
  }
}

async function selectTask(taskId: number): Promise<void> {
  if (selectedTaskId.value !== taskId) {
    disconnectSocket()
    stopPolling()
    executionPage.value = 1
  }
  selectedTaskId.value = taskId
  try {
    const [detail, taskMetrics, executionPageData] = await Promise.all([
      api.tasks.get(taskId),
      api.tasks.metrics(taskId),
      api.tasks.executions(taskId, {
        page: executionPage.value,
        page_size: executionPageSize,
        latest_per_case: true,
      }),
    ])
    task.value = detail
    metrics.value = taskMetrics
    executions.value = executionPageData.items
    executionTotal.value = executionPageData.total
    patchTaskInList(detail)
    if (['RUNNING', 'PAUSED', 'PENDING'].includes(detail.status)) {
      connectSocket(taskId)
      startPolling(taskId)
    } else {
      disconnectSocket()
      stopPolling()
    }
  } catch (exc) {
    reportError(exc)
  }
}

async function loadExecutions(taskId: number): Promise<void> {
  try {
    const pageData = await api.tasks.executions(taskId, {
      page: executionPage.value,
      page_size: executionPageSize,
      latest_per_case: true,
    })
    executions.value = pageData.items
    executionTotal.value = pageData.total
  } catch (exc) {
    reportError(exc)
  }
}

function changeExecutionPage(next: number): void {
  if (!selectedTaskId.value) return
  executionPage.value = next
  void loadExecutions(selectedTaskId.value)
}

function patchTaskInList(next: BatchTask): void {
  const index = tasks.value.findIndex((item) => item.id === next.id)
  if (index >= 0) tasks.value.splice(index, 1, next)
  else tasks.value.unshift(next)
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
    patchTaskInList({ ...task.value })
  }
  if (event.execution) {
    const next = event.execution
    const index = executions.value.findIndex((item) => item.case_id === next.case_id)
    if (index >= 0) {
      const current = executions.value[index]
      executions.value.splice(index, 1, {
        ...next,
        attempt_count: Math.max(current.attempt_count ?? 1, next.attempt_no),
      })
    } else {
      executions.value.unshift({ ...next, attempt_count: Math.max(1, next.attempt_no) })
      executionTotal.value += 1
    }
    executions.value = executions.value.slice(0, executionPageSize)
  }
  if (event.status && ['COMPLETED', 'ABORTED', 'FAILED'].includes(event.status)) {
    disconnectSocket()
    stopPolling()
    window.setTimeout(() => {
      void refreshTasks()
      if (selectedTaskId.value) void selectTask(selectedTaskId.value)
    }, 500)
  }
}

function connectSocket(taskId: number): void {
  disconnectSocket()
  try {
    const ws = new WebSocket(buildWebSocketUrl(`/ws/tasks/${taskId}`))
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

function startPolling(taskId: number): void {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    try {
      const nextMetrics = await api.tasks.metrics(taskId)
      metrics.value = nextMetrics
      if (!live.value) {
        const detail = await api.tasks.get(taskId)
        task.value = detail
        patchTaskInList(detail)
        const page = await api.tasks.executions(taskId, {
          page: executionPage.value,
          page_size: executionPageSize,
          latest_per_case: true,
        })
        executions.value = page.items
        executionTotal.value = page.total
        if (!['RUNNING', 'PAUSED', 'PENDING'].includes(detail.status)) {
          stopPolling()
          disconnectSocket()
          void refreshTasks()
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

async function refreshCurrent(): Promise<void> {
  await refreshTasks()
  if (selectedTaskId.value) await selectTask(selectedTaskId.value)
}

async function pauseTask(): Promise<void> {
  if (!task.value) return
  try {
    await api.tasks.pause(task.value.id)
    toast.success('任务已暂停')
    await refreshCurrent()
  } catch (exc) {
    reportError(exc)
  }
}

async function resumeTask(): Promise<void> {
  if (!task.value) return
  try {
    await api.tasks.resume(task.value.id)
    toast.success('任务已恢复')
    await refreshCurrent()
  } catch (exc) {
    reportError(exc)
  }
}

async function abortTask(): Promise<void> {
  if (!task.value) return
  if (!window.confirm('确认终止当前任务？已完成的执行记录会保留。')) return
  try {
    await api.tasks.abort(task.value.id)
    toast.success('终止指令已下发')
    await refreshCurrent()
  } catch (exc) {
    reportError(exc)
  }
}

async function retryFailed(): Promise<void> {
  if (!task.value) return
  try {
    const result = await api.tasks.retryFailed(task.value.id)
    toast.success(result.message)
    await refreshCurrent()
  } catch (exc) {
    reportError(exc)
  }
}

function openCreate(): void {
  const stamp = new Date()
  form.name = `回归任务 ${stamp.getMonth() + 1}/${stamp.getDate()} ${String(stamp.getHours()).padStart(2, '0')}:${String(stamp.getMinutes()).padStart(2, '0')}`
  form.description = ''
  form.environment_id = environments.value[0]?.id ?? null
  form.concurrency = 3
  form.qps_limit = 5
  form.retry_on_env_error = true
  form.max_env_retries = 2
  form.retry_backoff_seconds = 3
  form.case_scope = 'SELECTED'
  form.case_ids = []
  form.auto_start = true
  form.schedule = defaultSchedule()
  caseKeyword.value = ''
  showCreate.value = true
}

function toggleCase(item: TestCase): void {
  if (item.status !== 'ACTIVE') return
  const index = form.case_ids.indexOf(item.id)
  if (index >= 0) form.case_ids.splice(index, 1)
  else form.case_ids.push(item.id)
}

function selectAllFiltered(): void {
  const ids = filteredCases.value
    .filter((item) => item.status === 'ACTIVE')
    .map((item) => item.id)
  form.case_ids = Array.from(new Set([...form.case_ids, ...ids]))
}

function clearSelected(): void {
  form.case_ids = []
}

async function submitCreate(): Promise<void> {
  if (!form.name.trim()) {
    toast.error('请填写任务名称')
    return
  }
  if (form.case_scope === 'SELECTED' && form.case_ids.length === 0) {
    toast.error('请至少选择一个用例，或切换为「全部启用用例」')
    return
  }
  if (form.case_scope === 'ALL_ACTIVE' && activeCaseCount.value === 0) {
    toast.error('用例库中没有启用用例，无法批量执行')
    return
  }
  const scheduleError = scheduleValidationError(form.schedule)
  if (scheduleError) {
    toast.error(scheduleError)
    return
  }
  creating.value = true
  try {
    const created = await api.tasks.create({
      name: form.name.trim(),
      description: form.description.trim() || null,
      environment_id: form.environment_id,
      case_scope: form.case_scope,
      case_ids: form.case_scope === 'SELECTED' ? [...form.case_ids] : [],
      concurrency: form.concurrency,
      qps_limit: form.qps_limit,
      retry_on_env_error: form.retry_on_env_error,
      max_env_retries: form.max_env_retries,
      retry_backoff_seconds: form.retry_backoff_seconds,
      ...schedulePayload(normalizeSchedule(form.schedule)),
      auto_start: form.schedule.type === 'IMMEDIATE' && form.auto_start,
    })
    const scheduled = created.schedule_type !== 'IMMEDIATE'
    toast.success(
      scheduled
        ? `定时任务 ${created.code} 已登记：${scheduleSummary(normalizeSchedule(form.schedule))}`
        : `任务 ${created.code} 已创建，共 ${created.total_cases} 条用例`,
    )
    showCreate.value = false
    await refreshTasks()
    await selectTask(created.id)
  } catch (exc) {
    reportError(exc)
  } finally {
    creating.value = false
  }
}

async function runTaskNow(): Promise<void> {
  if (!task.value) return
  const plan = isPlanTask(task.value)
  const oneOff = task.value.schedule_type === 'SCHEDULED'
  try {
    const run = await api.tasks.runNow(task.value.id)
    toast.success(plan ? `已按计划立即执行一次：${run.code}` : '任务已启动')
    await refreshCurrent()
    // 周期计划会克隆出新任务，直接切到这次执行
    if (plan && !oneOff) {
      await selectTask(run.id)
      void router.replace({ name: 'dashboard', query: { task: String(run.id) } })
    }
  } catch (exc) {
    reportError(exc)
  }
}

async function stopPlan(): Promise<void> {
  if (!task.value) return
  if (!window.confirm('确认停止该定时计划？已生成的历史任务不会删除。')) return
  try {
    await api.tasks.abort(task.value.id)
    toast.success('定时计划已停止')
    await refreshCurrent()
  } catch (exc) {
    reportError(exc)
  }
}

async function togglePlan(active: boolean): Promise<void> {
  if (!task.value) return
  try {
    await api.tasks.updateSchedule(task.value.id, { active })
    toast.success(active ? '定时计划已启用' : '定时计划已暂停')
    await refreshCurrent()
  } catch (exc) {
    reportError(exc)
  }
}

function openExecution(executionId: number): void {
  void router.push({ name: 'trace', query: { execution: String(executionId) } })
}

function openTaskTrace(taskId: number): void {
  void router.push({ name: 'trace', query: { task: String(taskId) } })
}

onMounted(() => {
  void loadBase()
})

onBeforeUnmount(() => {
  disconnectSocket()
  stopPolling()
})
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      eyebrow="Dashboard · 实时大盘"
      title="批量任务测试大盘"
      description="批量任务概览与单次运行详情；选中任务后查看通过分布与用例级结果，点击用例行进入单笔执行结果页。"
    >
      <template #actions>
        <button class="btn-ghost" @click="refreshCurrent">刷新数据</button>
        <button class="btn-primary" @click="openCreate">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" d="M12 5v14M5 12h14" />
          </svg>
          新建批量任务
        </button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 gap-3 xl:grid-cols-[minmax(0,0.92fr)_minmax(0,0.92fr)_minmax(0,0.86fr)_minmax(0,1.3fr)]">
      <MetricCard label="用例总数" :value="cases.length" hint="当前可用用例" accent="sky" />
      <MetricCard label="批量任务" :value="tasks.length" hint="最近 50 条任务" accent="indigo" />
      <MetricCard label="运行中" :value="runningCount" hint="实时执行任务数" accent="amber" />
      <MetricCard
        label="任务通过率"
        :value="formatPercent(metrics?.pass_rate, 1)"
        :hint="activeTask ? activeTask.name : '尚未选择任务'"
        accent="emerald"
        :progress="metrics?.pass_rate ?? null"
      />
    </div>

    <div class="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div class="min-w-0 space-y-5">
        <BatchMonitor
          :task="task"
          :metrics="metrics"
          :executions="executions"
          :live="live"
          :execution-total="executionTotal"
          :execution-page="executionPage"
          :execution-page-size="executionPageSize"
          @refresh="refreshCurrent"
          @pause="pauseTask"
          @resume="resumeTask"
          @abort="abortTask"
          @retry="retryFailed"
          @run-now="runTaskNow"
          @stop-plan="stopPlan"
          @toggle-plan="togglePlan"
          @select-execution="openExecution"
          @view-task-trace="openTaskTrace"
          @page-change="changeExecutionPage"
        />
      </div>

      <section class="card flex flex-col overflow-hidden">
        <header class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <h2 class="panel-title">批量任务</h2>
            <p class="mt-0.5 text-[10px] text-slate-500">点击任务查看批量详情</p>
          </div>
          <button class="text-[11px] text-slate-500 transition hover:text-slate-800" @click="refreshTasks">
            刷新
          </button>
        </header>
        <div class="max-h-[560px] flex-1 overflow-y-auto">
          <button
            v-for="item in recentTasks"
            :key="item.id"
            class="w-full border-b border-slate-200 px-4 py-3 text-left transition hover:bg-slate-100/70"
            :class="item.id === selectedTaskId ? 'bg-indigo-50' : ''"
            @click="selectTask(item.id)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="truncate text-sm font-medium text-slate-800">{{ item.name }}</span>
              <StatusPill :status="item.status" size="sm" />
            </div>
            <p class="mt-1 font-mono text-[10px] text-slate-600">{{ item.code }}</p>
            <div class="mt-1 flex flex-wrap items-center gap-1.5 text-[10px]">
              <span
                class="rounded-full border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-slate-600"
              >
                {{ statusLabel(item.schedule_type) }}
              </span>
              <span
                v-if="item.schedule_type !== 'IMMEDIATE'"
                class="truncate"
                :class="
                  item.status === 'SCHEDULED' && item.next_run_at ? 'text-sky-700' : 'text-slate-500'
                "
              >
                {{
                  item.status === 'SCHEDULED'
                    ? item.next_run_at
                      ? `下次 ${formatDateTime(item.next_run_at)}`
                      : '等待触发'
                    : item.schedule_type === 'SCHEDULED'
                      ? formatDateTime(item.scheduled_at)
                      : '计划已停止'
                }}
              </span>
            </div>
            <div class="mt-2 flex items-center gap-2">
              <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                <div
                  class="h-full rounded-full bg-indigo-500"
                  :style="{
                    width: `${item.total_cases ? Math.min(100, (item.finished_cases / item.total_cases) * 100) : 0}%`,
                  }"
                />
              </div>
              <span class="text-[10px] tabular-nums text-slate-600">
                {{ item.finished_cases }}/{{ item.total_cases }}
              </span>
            </div>
            <p class="mt-1 text-[10px] text-slate-600">{{ formatDateTime(item.created_at) }}</p>
          </button>
          <EmptyState
            v-if="recentTasks.length === 0 && !loadingBase"
            compact
            title="暂无任务"
            description="创建第一个批量任务开始测试。"
          />
        </div>
      </section>
    </div>

    <BaseModal v-model="showCreate" title="新建批量任务" subtitle="选择用例与执行方式（立即 / 指定时间 / 每天 / 每周），并设置并发、限速与失败重试策略" width="xl">
      <div class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div class="space-y-4">
          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <label class="label">任务名称</label>
              <input v-model="form.name" class="input" placeholder="例如：账单回归 2026-11" />
            </div>
            <div>
              <label class="label">执行环境</label>
              <select v-model="form.environment_id" class="input">
                <option :value="null">不绑定环境（使用用例配置）</option>
                <option v-for="env in environments" :key="env.id" :value="env.id">
                  {{ env.name }}（{{ env.code }}）
                </option>
              </select>
            </div>
          </div>
          <div>
            <label class="label">任务描述</label>
            <textarea v-model="form.description" class="input" rows="2" placeholder="可选，用于标记本次回归目的" />
          </div>
          <ScheduleField v-model="form.schedule" :disabled="creating" />
          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <label class="label">并发数</label>
              <input v-model.number="form.concurrency" class="input" type="number" min="1" max="20" />
            </div>
            <div>
              <label class="label">QPS 上限</label>
              <input v-model.number="form.qps_limit" class="input" type="number" min="0.1" max="100" step="0.5" />
            </div>
          </div>
          <div class="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
            <label class="flex items-start gap-2 text-sm text-slate-700">
              <input
                v-model="form.retry_on_env_error"
                type="checkbox"
                class="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white"
              />
              <span>
                环境类失败自动重试
                <span class="block text-[11px] text-slate-500">
                  仅重试连接失败、超时、HTTP 429/5xx、SSE 空响应等环境故障；规则不通过、裁判低分等业务失败不会重试。
                </span>
              </span>
            </label>
            <div
              class="grid gap-3 sm:grid-cols-2"
              :class="form.retry_on_env_error ? '' : 'pointer-events-none opacity-40'"
            >
              <div>
                <label class="label">最大重试次数</label>
                <input
                  v-model.number="form.max_env_retries"
                  class="input"
                  type="number"
                  min="0"
                  max="10"
                  :disabled="!form.retry_on_env_error"
                />
              </div>
              <div>
                <label class="label">基础退避（秒）</label>
                <input
                  v-model.number="form.retry_backoff_seconds"
                  class="input"
                  type="number"
                  min="0"
                  max="60"
                  step="0.5"
                  :disabled="!form.retry_on_env_error"
                />
              </div>
            </div>
            <p class="text-[11px] text-slate-500">
              第 n 次重试前等待 = 基础退避 × 2^(n-1)，重试同样受并发数与 QPS 限制，暂停/终止会立即生效。
            </p>
          </div>
          <label
            v-if="form.schedule.type === 'IMMEDIATE'"
            class="flex items-center gap-2 text-sm text-slate-700"
          >
            <input v-model="form.auto_start" type="checkbox" class="h-4 w-4 rounded border-slate-300 bg-white" />
            创建后立即启动
          </label>
        </div>

        <div class="flex min-h-0 flex-col rounded-xl border border-slate-200 bg-slate-50/80">
          <div class="space-y-2 border-b border-slate-200 p-3">
            <div class="flex items-center justify-between">
              <span class="text-xs font-medium text-slate-700">执行范围</span>
              <span class="text-[11px] text-slate-500">
                本次 {{ scopeCount }} 条 / 库内启用 {{ activeCaseCount }} 条
              </span>
            </div>
            <label
              class="flex cursor-pointer items-start gap-2 rounded-lg border p-2 transition"
              :class="
                form.case_scope === 'SELECTED'
                  ? 'border-indigo-200 bg-indigo-50'
                  : 'border-slate-200 hover:bg-slate-100/70'
              "
            >
              <input
                v-model="form.case_scope"
                type="radio"
                value="SELECTED"
                class="mt-0.5 h-4 w-4 border-slate-300 bg-white"
              />
              <span class="min-w-0 text-xs text-slate-800">
                已选用例（{{ form.case_ids.length }} 条）
                <span class="block text-[10px] text-slate-600">下方逐条勾选后执行。</span>
              </span>
            </label>
            <label
              class="flex cursor-pointer items-start gap-2 rounded-lg border p-2 transition"
              :class="
                form.case_scope === 'ALL_ACTIVE'
                  ? 'border-indigo-200 bg-indigo-50'
                  : 'border-slate-200 hover:bg-slate-100/70'
              "
            >
              <input
                v-model="form.case_scope"
                type="radio"
                value="ALL_ACTIVE"
                class="mt-0.5 h-4 w-4 border-slate-300 bg-white"
              />
              <span class="min-w-0 text-xs text-slate-800">
                全部启用用例（{{ activeCaseCount }} 条，跨分页）
                <span class="block text-[10px] text-slate-600">
                  自动执行用例库全部启用用例，不受勾选与分页限制。
                </span>
              </span>
            </label>
            <template v-if="form.case_scope === 'SELECTED'">
              <input v-model="caseKeyword" class="input !py-1.5 text-xs" placeholder="搜索编号 / 名称 / 标签" />
              <div class="flex gap-2">
                <button class="btn-ghost btn-xs" @click="selectAllFiltered">
                  全选筛选结果（{{ filteredCases.length }}）
                </button>
                <button class="btn-ghost btn-xs" @click="clearSelected">清空</button>
              </div>
            </template>
          </div>
          <div
            class="max-h-[320px] flex-1 overflow-y-auto p-2"
            :class="form.case_scope === 'ALL_ACTIVE' ? 'pointer-events-none opacity-40' : ''"
          >
            <label
              v-for="item in filteredCases"
              :key="item.id"
              class="flex cursor-pointer items-start gap-2 rounded-lg px-2 py-2 transition hover:bg-slate-100"
            >
              <input
                type="checkbox"
                class="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white"
                :checked="form.case_ids.includes(item.id)"
                :disabled="item.status !== 'ACTIVE'"
                :title="item.status === 'ACTIVE' ? '选择该用例' : '仅启用用例可批量执行'"
                @change="toggleCase(item)"
              />
              <span class="min-w-0">
                <span class="block truncate font-mono text-[11px] text-slate-700">{{ item.code }}</span>
                <span class="block truncate text-[11px] text-slate-600">{{ item.name }}</span>
                <span class="mt-0.5 inline-flex items-center gap-1 text-[10px] text-slate-500">
                  {{ caseTypeLabel(item.case_type) }} · v{{ item.current_version }}
                </span>
              </span>
            </label>
            <EmptyState v-if="filteredCases.length === 0" compact title="没有匹配的用例" />
          </div>
        </div>
      </div>

      <template #footer>
        <button class="btn-ghost" @click="showCreate = false">取消</button>
        <button class="btn-primary" :disabled="creating" @click="submitCreate">
          {{
            creating
              ? '创建中…'
              : form.schedule.type === 'IMMEDIATE'
                ? `创建并执行（${scopeCount} 条）`
                : `创建定时任务（${scopeCount} 条）`
          }}
        </button>
      </template>
    </BaseModal>
  </div>
</template>
