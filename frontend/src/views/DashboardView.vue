<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { buildWebSocketUrl } from '../api/client'
import BaseModal from '../components/BaseModal.vue'
import MetricCard from '../components/MetricCard.vue'
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
import { caseTypeLabel, formatDateTime, formatPercent } from '../utils/format'
import { useToastStore } from '../stores/toast'

const router = useRouter()
const toast = useToastStore()

const cases = ref<TestCase[]>([])
const tasks = ref<BatchTask[]>([])
const environments = ref<Environment[]>([])
const selectedTaskId = ref<number | null>(null)
const task = ref<BatchTask | null>(null)
const metrics = ref<TaskMetrics | null>(null)
const executions = ref<CaseExecutionItem[]>([])
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
  case_ids: [] as number[],
  auto_start: true,
})

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

async function loadBase(): Promise<void> {
  loadingBase.value = true
  try {
    const [casePage, taskPage, envPage] = await Promise.all([
      api.cases.list({ page_size: 200 }),
      api.tasks.list({ page_size: 50 }),
      api.environments.list({ page_size: 100 }),
    ])
    cases.value = casePage.items
    tasks.value = taskPage.items
    environments.value = envPage.items
    const preferred =
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
  }
  selectedTaskId.value = taskId
  try {
    const [detail, taskMetrics, executionPage] = await Promise.all([
      api.tasks.get(taskId),
      api.tasks.metrics(taskId),
      api.tasks.executions(taskId, { page_size: 50 }),
    ])
    task.value = detail
    metrics.value = taskMetrics
    executions.value = executionPage.items
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
    const index = executions.value.findIndex((item) => item.id === event.execution?.id)
    if (index >= 0) executions.value.splice(index, 1, event.execution)
    else executions.value.unshift(event.execution)
    executions.value = executions.value.slice(0, 60)
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
        const page = await api.tasks.executions(taskId, { page_size: 50 })
        executions.value = page.items
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
  form.case_ids = []
  form.auto_start = true
  caseKeyword.value = ''
  showCreate.value = true
}

function toggleCase(caseId: number): void {
  const index = form.case_ids.indexOf(caseId)
  if (index >= 0) form.case_ids.splice(index, 1)
  else form.case_ids.push(caseId)
}

function selectAllFiltered(): void {
  const ids = filteredCases.value.map((item) => item.id)
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
  if (form.case_ids.length === 0) {
    toast.error('请至少选择一个用例')
    return
  }
  creating.value = true
  try {
    const created = await api.tasks.create({
      name: form.name.trim(),
      description: form.description.trim() || null,
      environment_id: form.environment_id,
      case_ids: [...form.case_ids],
      concurrency: form.concurrency,
      qps_limit: form.qps_limit,
      auto_start: form.auto_start,
    })
    toast.success(`任务 ${created.code} 已创建`)
    showCreate.value = false
    await refreshTasks()
    await selectTask(created.id)
  } catch (exc) {
    reportError(exc)
  } finally {
    creating.value = false
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
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold text-slate-100">实时测试大盘</h2>
        <p class="mt-1 text-xs text-slate-500">
          批量执行进度、通过率与指标体系；WebSocket 推送，断线时自动降级轮询。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button class="btn-ghost" @click="refreshCurrent">刷新数据</button>
        <button class="btn-primary" @click="openCreate">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" d="M12 5v14M5 12h14" />
          </svg>
          新建批量任务
        </button>
      </div>
    </div>

    <div class="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <MetricCard label="用例总数" :value="cases.length" hint="当前可用用例" accent="sky" />
      <MetricCard label="批量任务" :value="tasks.length" hint="最近 50 条任务" accent="indigo" />
      <MetricCard label="运行中" :value="runningCount" hint="实时执行任务数" accent="amber" />
      <MetricCard
        label="当前任务通过率"
        :value="formatPercent(metrics?.pass_rate, 1)"
        :hint="activeTask ? activeTask.name : '尚未选择任务'"
        accent="emerald"
      />
    </div>

    <div class="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div class="min-w-0 space-y-5">
        <BatchMonitor
          :task="task"
          :metrics="metrics"
          :executions="executions"
          :live="live"
          @refresh="refreshCurrent"
          @pause="pauseTask"
          @resume="resumeTask"
          @abort="abortTask"
          @retry="retryFailed"
          @select-execution="openExecution"
          @view-task-trace="openTaskTrace"
        />
      </div>

      <section class="card flex flex-col overflow-hidden">
        <header class="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <h2 class="panel-title">最近任务</h2>
          <button class="text-[11px] text-slate-500 transition hover:text-slate-200" @click="refreshTasks">
            刷新
          </button>
        </header>
        <div class="max-h-[560px] flex-1 overflow-y-auto">
          <button
            v-for="item in recentTasks"
            :key="item.id"
            class="w-full border-b border-slate-800/60 px-4 py-3 text-left transition hover:bg-slate-800/40"
            :class="item.id === selectedTaskId ? 'bg-indigo-500/10' : ''"
            @click="selectTask(item.id)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="truncate text-sm font-medium text-slate-200">{{ item.name }}</span>
              <StatusPill :status="item.status" size="sm" />
            </div>
            <p class="mt-1 font-mono text-[10px] text-slate-500">{{ item.code }}</p>
            <div class="mt-2 flex items-center gap-2">
              <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
                <div
                  class="h-full rounded-full bg-indigo-500/80"
                  :style="{
                    width: `${item.total_cases ? Math.min(100, (item.finished_cases / item.total_cases) * 100) : 0}%`,
                  }"
                />
              </div>
              <span class="text-[10px] tabular-nums text-slate-500">
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

    <BaseModal v-model="showCreate" title="新建批量任务" subtitle="选择用例并设置并发与限速" width="xl">
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
          <label class="flex items-center gap-2 text-sm text-slate-300">
            <input v-model="form.auto_start" type="checkbox" class="h-4 w-4 rounded border-slate-600 bg-slate-900" />
            创建后立即启动
          </label>
        </div>

        <div class="flex min-h-0 flex-col rounded-xl border border-slate-800 bg-slate-950/50">
          <div class="border-b border-slate-800 p-3">
            <div class="flex items-center justify-between">
              <span class="text-xs font-medium text-slate-300">选择用例</span>
              <span class="text-[11px] text-slate-500">已选 {{ form.case_ids.length }}</span>
            </div>
            <input v-model="caseKeyword" class="input mt-2 !py-1.5 text-xs" placeholder="搜索编号 / 名称 / 标签" />
            <div class="mt-2 flex gap-2">
              <button class="btn-ghost btn-xs" @click="selectAllFiltered">全选当前</button>
              <button class="btn-ghost btn-xs" @click="clearSelected">清空</button>
            </div>
          </div>
          <div class="max-h-[320px] flex-1 overflow-y-auto p-2">
            <label
              v-for="item in filteredCases"
              :key="item.id"
              class="flex cursor-pointer items-start gap-2 rounded-lg px-2 py-2 transition hover:bg-slate-800/60"
            >
              <input
                type="checkbox"
                class="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-900"
                :checked="form.case_ids.includes(item.id)"
                @change="toggleCase(item.id)"
              />
              <span class="min-w-0">
                <span class="block truncate font-mono text-[11px] text-slate-300">{{ item.code }}</span>
                <span class="block truncate text-[11px] text-slate-500">{{ item.name }}</span>
                <span class="mt-0.5 inline-flex items-center gap-1 text-[10px] text-slate-600">
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
          {{ creating ? '创建中…' : '创建并执行' }}
        </button>
      </template>
    </BaseModal>
  </div>
</template>
