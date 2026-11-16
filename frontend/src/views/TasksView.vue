<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import TaskCreateModal from './components/TaskCreateModal.vue'
import type { BatchTask, TaskScheduleType } from '../types'
import { errorMessage } from '../utils/errors'
import { formatDateTime, formatPercent, statusLabel } from '../utils/format'
import { isPlanTask, taskScheduleLabel } from '../utils/schedule'
import { useToastStore } from '../stores/toast'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()

const PAGE_SIZE = 20

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'SCHEDULED', label: '计划中' },
  { value: 'PENDING', label: '等待中' },
  { value: 'RUNNING', label: '执行中' },
  { value: 'COMPLETED', label: '已完成' },
  { value: 'ABORTED', label: '已终止' },
  { value: 'FAILED', label: '执行失败' },
]

const SCHEDULE_OPTIONS: Array<{ value: '' | TaskScheduleType; label: string }> = [
  { value: '', label: '全部方式' },
  { value: 'IMMEDIATE', label: '立即执行' },
  { value: 'SCHEDULED', label: '指定时间' },
  { value: 'DAILY', label: '每天定时' },
  { value: 'WEEKLY', label: '每周定时' },
]

const initialKeyword = typeof route.query.keyword === 'string' ? route.query.keyword : ''
const initialStatus = typeof route.query.status === 'string' ? route.query.status : ''
const initialSchedule =
  typeof route.query.schedule_type === 'string' ? route.query.schedule_type : ''

const tasks = ref<BatchTask[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const keyword = ref(initialKeyword)
const statusFilter = ref(
  STATUS_OPTIONS.some((item) => item.value && item.value === initialStatus) ? initialStatus : '',
)
const scheduleFilter = ref<'' | TaskScheduleType>(
  SCHEDULE_OPTIONS.some((item) => item.value && item.value === initialSchedule)
    ? (initialSchedule as TaskScheduleType)
    : '',
)
const busyId = ref<number | null>(null)
const showCreate = ref(false)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const hasFilter = computed(() =>
  Boolean(keyword.value.trim() || statusFilter.value || scheduleFilter.value),
)

const ACTIVE_STATUSES = ['PENDING', 'RUNNING']
const hasActiveTask = computed(() =>
  tasks.value.some((item) => ACTIVE_STATUSES.includes(item.status)),
)

let searchTimer: number | null = null
let requestSeq = 0
let pollTimer: number | null = null
let followUpTimer: number | null = null

async function load(silent = false): Promise<boolean> {
  const seq = ++requestSeq
  if (!silent) loading.value = true
  try {
    const data = await api.tasks.list({
      page: page.value,
      page_size: PAGE_SIZE,
      keyword: keyword.value.trim() || undefined,
      status: statusFilter.value || undefined,
      schedule_type: scheduleFilter.value || undefined,
    })
    if (seq !== requestSeq) return false
    tasks.value = data.items
    total.value = data.total
    return true
  } catch (exc) {
    if (seq === requestSeq && !silent) toast.error(errorMessage(exc))
    return false
  } finally {
    if (seq === requestSeq && !silent) loading.value = false
  }
}

async function refreshList(): Promise<void> {
  if (await load()) toast.success('刷新成功')
}

/** 动作接口返回后，后端的运行状态可能还没落库；补一次延迟刷新避免列表停在旧状态。 */
function scheduleFollowUpReload(): void {
  if (followUpTimer !== null) window.clearTimeout(followUpTimer)
  followUpTimer = window.setTimeout(() => {
    followUpTimer = null
    void load(true)
  }, 900)
}

function startPolling(): void {
  if (pollTimer !== null) return
  pollTimer = window.setInterval(() => {
    if (document.visibilityState !== 'visible') return
    if (!hasActiveTask.value) return
    void load(true)
  }, 3000)
}

function syncQuery(): void {
  const query: Record<string, string> = {}
  if (keyword.value.trim()) query.keyword = keyword.value.trim()
  if (statusFilter.value) query.status = statusFilter.value
  if (scheduleFilter.value) query.schedule_type = scheduleFilter.value
  void router.replace({ name: 'tasks', query })
}

function applyFilters(): void {
  if (searchTimer !== null) {
    window.clearTimeout(searchTimer)
    searchTimer = null
  }
  page.value = 1
  syncQuery()
  void load()
}

function onKeywordInput(): void {
  if (searchTimer !== null) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    syncQuery()
    void load()
  }, 350)
}

function resetFilters(): void {
  keyword.value = ''
  statusFilter.value = ''
  scheduleFilter.value = ''
  page.value = 1
  syncQuery()
  void load()
}

function goPage(next: number): void {
  if (next < 1 || next > pageCount.value || next === page.value) return
  page.value = next
  void load()
}

// ------------------------------------------------------------------ 行内判断
function planWaiting(task: BatchTask): boolean {
  return isPlanTask(task) && ['SCHEDULED', 'ABORTED'].includes(task.status)
}

function planActive(task: BatchTask): boolean {
  return Boolean(task.schedule_active && task.status === 'SCHEDULED')
}

function isRecurring(task: BatchTask): boolean {
  return task.schedule_type === 'DAILY' || task.schedule_type === 'WEEKLY'
}

function progressOf(task: BatchTask): number {
  if (!task.total_cases) return 0
  return Math.min(100, Math.round((task.finished_cases / task.total_cases) * 100))
}

function passRateOf(task: BatchTask): number {
  if (!task.finished_cases) return 0
  return (task.pass_count / task.finished_cases) * 100
}

function canRetry(task: BatchTask): boolean {
  return (
    !planWaiting(task) &&
    ['COMPLETED', 'ABORTED', 'FAILED'].includes(task.status) &&
    !isPlanTask(task)
  )
}

// ------------------------------------------------------------------ 行内操作
async function withAction(
  task: BatchTask,
  action: () => Promise<void>,
  options: { refresh?: boolean } = {},
): Promise<void> {
  if (busyId.value === task.id) return
  busyId.value = task.id
  try {
    await action()
  } catch (exc) {
    // 动作失败（例如任务已被其它入口启动导致 409）时也要同步一次，避免列表停在旧状态。
    toast.error(errorMessage(exc))
  } finally {
    busyId.value = null
    if (options.refresh !== false) {
      await load(true)
      scheduleFollowUpReload()
    }
  }
}

function openDetail(task: BatchTask): void {
  void router.push({ name: 'task-detail', params: { id: String(task.id) } })
}

function openCreate(): void {
  showCreate.value = true
}

async function onCreated(task: BatchTask): Promise<void> {
  await load()
  void router.push({ name: 'task-detail', params: { id: String(task.id) } })
}

async function runNow(task: BatchTask): Promise<void> {
  await withAction(
    task,
    async () => {
      const run = await api.tasks.runNow(task.id)
      toast.success(`已启动任务 ${run.code}`)
      void router.push({ name: 'task-detail', params: { id: String(run.id) } })
    },
    { refresh: false },
  )
}

async function startTask(task: BatchTask): Promise<void> {
  await withAction(task, async () => {
    await api.tasks.start(task.id)
    toast.success('任务已启动')
  })
}

async function abortTask(task: BatchTask): Promise<void> {
  if (!window.confirm('确认终止当前任务？已完成的执行记录会保留。')) return
  await withAction(task, async () => {
    await api.tasks.abort(task.id)
    toast.success('终止指令已下发')
  })
}

async function stopPlan(task: BatchTask): Promise<void> {
  if (!window.confirm('确认停止该定时计划？已生成的历史任务不会删除。')) return
  await withAction(task, async () => {
    await api.tasks.abort(task.id)
    toast.success('定时计划已停止')
  })
}

async function enablePlan(task: BatchTask): Promise<void> {
  await withAction(task, async () => {
    await api.tasks.updateSchedule(task.id, { active: true })
    toast.success('定时计划已启用')
  })
}

async function retryFailed(task: BatchTask): Promise<void> {
  await withAction(task, async () => {
    await api.tasks.retryFailed(task.id)
    toast.success('失败重跑已提交')
  })
}

onMounted(() => {
  void load()
  startPolling()
  if (route.query.create === '1') {
    showCreate.value = true
    const query = { ...route.query }
    delete query.create
    void router.replace({ name: 'tasks', query })
  }
})

onBeforeUnmount(() => {
  if (searchTimer !== null) window.clearTimeout(searchTimer)
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
  if (followUpTimer !== null) {
    window.clearTimeout(followUpTimer)
    followUpTimer = null
  }
})
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      eyebrow="Tasks · 批量任务"
      title="批量任务管理"
      description="按名称或编号搜索，按状态与执行方式筛选；在这里查看进度、调整定时计划，并执行启动、终止与失败重跑。"
    >
      <template #actions>
        <button class="btn-ghost" @click="refreshList">刷新</button>
        <button class="btn-primary" @click="openCreate">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" d="M12 5v14M5 12h14" />
          </svg>
          新建批量任务
        </button>
      </template>
    </PageHeader>

    <div class="card flex flex-wrap items-end gap-3 px-4 py-3">
      <div class="min-w-[240px] flex-1">
        <label class="label">搜索</label>
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
            v-model="keyword"
            class="input !pl-9"
            type="search"
            placeholder="搜索任务名称 / 编号，输入即搜"
            @input="onKeywordInput"
            @keyup.enter="applyFilters"
          />
        </div>
      </div>
      <div class="w-36">
        <label class="label">任务状态</label>
        <select v-model="statusFilter" class="input" @change="applyFilters">
          <button><selectedcontent></selectedcontent></button>
          <option v-for="item in STATUS_OPTIONS" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select>
      </div>
      <div class="w-36">
        <label class="label">执行方式</label>
        <select v-model="scheduleFilter" class="input" @change="applyFilters">
          <button><selectedcontent></selectedcontent></button>
          <option v-for="item in SCHEDULE_OPTIONS" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select>
      </div>
      <button class="btn-primary" @click="applyFilters">查询</button>
      <button class="btn-ghost" :disabled="!hasFilter" @click="resetFilters">重置</button>
    </div>

    <section class="card overflow-hidden">
      <header class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
        <span class="panel-title">任务列表</span>
        <span class="text-[11px] text-slate-500">
          周期计划只保留一条记录，每次触发的执行在计划详情的「历次执行」中查看
        </span>
      </header>
      <div class="overflow-x-auto">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th">任务</th>
              <th class="th">执行方式</th>
              <th class="th">状态</th>
              <th class="th">进度</th>
              <th class="th">触发 / 执行时间</th>
              <th class="th">创建时间</th>
              <th class="th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in tasks" :key="item.id" class="cursor-pointer" @click="openDetail(item)">
              <td class="td">
                <p class="max-w-[260px] truncate text-sm font-medium text-slate-800">
                  {{ item.name }}
                </p>
                <p class="mt-0.5 font-mono text-[11px] text-slate-500">{{ item.code }}</p>
                <p class="mt-0.5 max-w-[260px] truncate text-[10px] text-slate-500">
                  {{ item.environment_name || '未绑定环境' }} · {{ item.total_cases }} 条用例
                </p>
              </td>
              <td class="td">
                <span
                  class="inline-flex rounded-full border px-2 py-0.5 text-[10px]"
                  :class="
                    item.schedule_type === 'IMMEDIATE'
                      ? 'border-slate-200 bg-slate-50 text-slate-600'
                      : 'border-sky-200 bg-sky-50 text-sky-700'
                  "
                >
                  {{ statusLabel(item.schedule_type) }}
                </span>
                <p
                  v-if="item.schedule_type !== 'IMMEDIATE'"
                  class="mt-1 max-w-[200px] truncate text-[10px] text-slate-500"
                >
                  {{ taskScheduleLabel(item) }}
                </p>
              </td>
              <td class="td">
                <StatusPill
                  :status="item.status"
                  size="sm"
                  :label="planWaiting(item) && !planActive(item) ? '已停止' : null"
                />
                <p v-if="item.error_message" class="mt-1 max-w-[200px] truncate text-[10px] text-rose-600">
                  {{ item.error_message }}
                </p>
              </td>
              <td class="td">
                <template v-if="isRecurring(item) && (item.run_count ?? 0) > 0">
                  <p class="text-[11px] font-medium text-slate-700">
                    已执行 {{ item.run_count }} 次
                  </p>
                  <p
                    class="mt-0.5 text-[10px]"
                    :class="planActive(item) ? 'text-sky-700' : 'text-slate-500'"
                  >
                    {{ planActive(item) ? '计划生效中' : '计划已停止' }} · 详情查看历次执行
                  </p>
                </template>
                <template v-else-if="planWaiting(item)">
                  <span
                    class="text-[11px]"
                    :class="planActive(item) ? 'text-sky-700' : 'text-slate-500'"
                  >
                    {{ planActive(item) ? '等待触发，尚未执行' : '计划已停止' }}
                  </span>
                </template>
                <template v-else>
                  <div class="flex items-center gap-2">
                    <div class="h-1.5 w-20 overflow-hidden rounded-full bg-slate-100">
                      <div
                        class="h-full rounded-full bg-indigo-500"
                        :style="{ width: `${progressOf(item)}%` }"
                      />
                    </div>
                    <span class="text-[11px] tabular-nums text-slate-600">
                      {{ item.finished_cases }}/{{ item.total_cases }}
                    </span>
                  </div>
                  <p class="mt-1 text-[10px] text-slate-500">
                    通过率 {{ formatPercent(passRateOf(item), 1) }} · 失败
                    {{ item.fail_count }} · 异常 {{ item.error_count }}
                  </p>
                </template>
              </td>
              <td class="td text-[11px] text-slate-500">
                <template v-if="isPlanTask(item)">
                  <p>
                    下次
                    <span class="tabular-nums text-slate-700">
                      {{ planActive(item) ? formatDateTime(item.next_run_at) : '—' }}
                    </span>
                  </p>
                  <p class="mt-0.5">
                    上次
                    <span class="tabular-nums text-slate-700">
                      {{ formatDateTime(item.last_run_at) }}
                    </span>
                  </p>
                </template>
                <template v-else>
                  <p>开始 {{ formatDateTime(item.started_at) }}</p>
                  <p class="mt-0.5">结束 {{ formatDateTime(item.finished_at) }}</p>
                </template>
              </td>
              <td class="td text-xs tabular-nums text-slate-500">
                {{ formatDateTime(item.created_at) }}
              </td>
              <td class="td whitespace-nowrap text-right">
                <div class="flex flex-nowrap items-center justify-end gap-1" @click.stop>
                  <button class="btn-ghost btn-xs" @click="openDetail(item)">详情</button>
                  <button
                    v-if="planWaiting(item) && planActive(item)"
                    class="btn-primary btn-xs"
                    :disabled="busyId === item.id"
                    @click="runNow(item)"
                  >
                    立即执行
                  </button>
                  <button
                    v-if="planWaiting(item) && planActive(item)"
                    class="btn-ghost btn-xs"
                    :disabled="busyId === item.id"
                    @click="stopPlan(item)"
                  >
                    停止计划
                  </button>
                  <button
                    v-if="planWaiting(item) && !planActive(item)"
                    class="btn-ghost btn-xs"
                    :disabled="busyId === item.id"
                    @click="enablePlan(item)"
                  >
                    启用计划
                  </button>
                  <button
                    v-if="item.status === 'PENDING' && !isPlanTask(item)"
                    class="btn-primary btn-xs"
                    :disabled="busyId === item.id"
                    @click="startTask(item)"
                  >
                    启动
                  </button>
                  <button
                    v-if="['RUNNING', 'PENDING'].includes(item.status) && !isPlanTask(item)"
                    class="btn-danger btn-xs"
                    :disabled="busyId === item.id"
                    @click="abortTask(item)"
                  >
                    终止
                  </button>
                  <button
                    v-if="canRetry(item)"
                    class="btn-ghost btn-xs"
                    :disabled="busyId === item.id"
                    @click="retryFailed(item)"
                  >
                    失败重跑
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <EmptyState
          v-if="tasks.length === 0 && !loading"
          compact
          :title="hasFilter ? '没有匹配的批量任务' : '还没有批量任务'"
          :description="
            hasFilter
              ? '试试更换关键词、状态或执行方式；也可以清空筛选查看全部任务。'
              : '点击右上角新建批量任务，选择用例与执行方式开始回归。'
          "
        />
        <div v-if="loading && tasks.length === 0" class="px-4 py-10 text-center text-xs text-slate-500">
          正在加载任务…
        </div>
      </div>
      <div class="flex items-center justify-between border-t border-slate-200 px-4 py-3">
        <span class="text-[11px] text-slate-500">
          共 <span class="font-medium tabular-nums text-slate-700">{{ total }}</span> 条任务 · 第 {{ page }} / {{ pageCount }} 页
        </span>
        <div class="flex gap-1">
          <button class="btn-ghost btn-xs" :disabled="page <= 1" @click="goPage(page - 1)">
            上一页
          </button>
          <button class="btn-ghost btn-xs" :disabled="page >= pageCount" @click="goPage(page + 1)">
            下一页
          </button>
        </div>
      </div>
    </section>

    <TaskCreateModal v-model="showCreate" @created="onCreated" />
  </div>
</template>
