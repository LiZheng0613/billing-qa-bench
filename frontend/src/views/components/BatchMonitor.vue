<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import StatusPill from '../../components/StatusPill.vue'
import EmptyState from '../../components/EmptyState.vue'
import type { BatchTask, CaseExecutionItem, TaskMetrics } from '../../types'
import {
  formatDateTime,
  formatMs,
  formatNumber,
  formatPercent,
  formatSeconds,
  formatScore,
  statusLabel,
} from '../../utils/format'
import { taskScheduleLabel } from '../../utils/schedule'

const props = withDefaults(
  defineProps<{
    task: BatchTask | null
    metrics: TaskMetrics | null
    executions: CaseExecutionItem[]
    live?: boolean
    executionTotal?: number
    executionPage?: number
    executionPageSize?: number
    latestPerCase?: boolean
    executionStatus?: string
    executionKeyword?: string
    exporting?: boolean
  }>(),
  { live: false, executionTotal: 0, executionPage: 1, executionPageSize: 30, latestPerCase: true, executionStatus: '', executionKeyword: '', exporting: false },
)

const emit = defineEmits<{
  (e: 'start'): void
  (e: 'abort'): void
  (e: 'retry'): void
  (e: 'run-now'): void
  (e: 'stop-plan'): void
  (e: 'toggle-plan', active: boolean): void
  (e: 'select-execution', executionId: number): void
  (e: 'page-change', page: number): void
  (e: 'mode-change', latestPerCase: boolean): void
  (e: 'filter-change', filters: { status: string; keyword: string }): void
  (e: 'export'): void
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
const planWaiting = computed(() =>
  Boolean(props.task && isPlan.value && ['SCHEDULED', 'ABORTED'].includes(props.task.status)),
)
const canStart = computed(
  () => !planWaiting.value && props.task?.status === 'PENDING',
)
const canAbort = computed(() =>
  !planWaiting.value &&
  Boolean(props.task && ['RUNNING', 'PENDING'].includes(props.task.status)),
)
const canRetry = computed(() =>
  !planWaiting.value &&
  !isPlan.value &&
  Boolean(props.task && ['COMPLETED', 'ABORTED', 'FAILED'].includes(props.task.status)),
)
const canRunNow = computed(() => planWaiting.value && planActive.value)
const canStopPlan = computed(() => planWaiting.value)
const canEnablePlan = computed(() => planWaiting.value && !planActive.value)
const planActive = computed(
  () => Boolean(props.task?.schedule_active && props.task.status === 'SCHEDULED'),
)

const progress = computed(() => props.metrics?.progress ?? 0)

const executionPageCount = computed(() =>
  Math.max(1, Math.ceil(props.executionTotal / props.executionPageSize)),
)

function previousPage(): void {
  if (props.executionPage > 1) emit('page-change', props.executionPage - 1)
}

function nextPage(): void {
  if (props.executionPage < executionPageCount.value) {
    emit('page-change', props.executionPage + 1)
  }
}

const stats = computed(() => [
  {
    label: '进度',
    value: formatPercent(props.metrics?.progress, 1),
    hint: props.metrics?.env_retry_count
      ? `已自动重试 ${props.metrics.env_retry_count} 条用例`
      : '',
  },
  { label: '通过率', value: formatPercent(props.metrics?.pass_rate, 1) },
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
const hasFilter = computed(
  () => Boolean(filterStatus.value) || Boolean(filterKeyword.value.trim()),
)

let keywordTimer: number | null = null

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

function clearKeywordTimer(): void {
  if (keywordTimer !== null) {
    window.clearTimeout(keywordTimer)
    keywordTimer = null
  }
}

function emitFilters(): void {
  clearKeywordTimer()
  emit('filter-change', {
    status: filterStatus.value,
    keyword: filterKeyword.value.trim(),
  })
}

function onKeywordInput(): void {
  clearKeywordTimer()
  keywordTimer = window.setTimeout(() => {
    keywordTimer = null
    emitFilters()
  }, 350)
}

function resetFilters(): void {
  filterStatus.value = ''
  filterKeyword.value = ''
  emitFilters()
}

onBeforeUnmount(clearKeywordTimer)
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
            {{ task.retry_on_env_error ? `环境失败自动重试 ${task.max_env_retries} 次` : '环境失败不重试' }}
          </span>
          <template v-if="task.enable_self_check">
            <span>·</span>
            <span class="text-violet-700">失败追问自检</span>
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
          :label="planWaiting && !planActive ? '已停止' : null"
        />
        <button
          class="btn-ghost btn-xs"
          :disabled="!task || exporting"
          @click="emit('export')"
        >
          {{ exporting ? '导出中…' : '导出 Excel' }}
        </button>
        <button v-if="canRunNow" class="btn-primary btn-xs" @click="emit('run-now')">
          立即执行
        </button>
        <button
          v-if="canEnablePlan"
          class="btn-ghost btn-xs"
          @click="emit('toggle-plan', true)"
        >
          启用计划
        </button>
        <button v-if="canStopPlan && planActive" class="btn-danger btn-xs" @click="emit('stop-plan')">
          停止计划
        </button>
        <button v-if="canStart" class="btn-primary btn-xs" @click="emit('start')">启动</button>
        <button v-if="canAbort" class="btn-danger btn-xs" @click="emit('abort')">终止</button>
        <button v-if="canRetry" class="btn-ghost btn-xs" @click="emit('retry')">失败重跑</button>
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

      <div class="grid grid-cols-2 gap-3 border-b border-slate-200 px-5 py-4 sm:grid-cols-3 xl:grid-cols-6">
        <div v-for="item in stats" :key="item.label">
          <p class="text-[10px] uppercase tracking-wider text-slate-500">{{ item.label }}</p>
          <p class="mt-0.5 text-xl font-semibold leading-tight tracking-[-0.02em] tabular-nums text-slate-900">{{ item.value }}</p>
          <p v-if="item.hint" class="mt-0.5 text-[10px] text-slate-500">{{ item.hint }}</p>
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
                {{ option.label }}（{{ option.count }}）
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
                placeholder="搜索用例编号 / 名称，输入即筛"
                @input="onKeywordInput"
                @keyup.enter="emitFilters"
              />
            </div>
          </div>
          <button v-if="hasFilter" class="btn-ghost btn-xs" @click="resetFilters">
            重置筛选
          </button>
        </div>
        <table class="table-shell mt-2">
          <thead>
            <tr>
              <th class="th">执行号</th>
              <th class="th">用例</th>
              <th class="th">状态</th>
              <th class="th">得分</th>
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
              <td class="td font-mono text-xs text-slate-500">#{{ item.id }}</td>
              <td class="td">
                <p class="font-mono text-xs text-slate-800">{{ item.case_code || `用例 #${item.case_id ?? '—'}` }}</p>
                <p class="mt-0.5 max-w-[240px] truncate text-[11px] text-slate-500">
                  {{ item.case_name || '—' }} · v{{ item.case_version }}
                  <span v-if="item.attempt_no > 1" class="text-amber-700"> · 第 {{ item.attempt_no }} 次尝试</span>
                </p>
              </td>
              <td class="td"><StatusPill :status="item.status" size="sm" /></td>
              <td class="td tabular-nums">{{ formatScore(item.total_score) }}</td>
              <td class="td tabular-nums">{{ item.turns_used }}</td>
              <td class="td tabular-nums">{{ item.attempt_no }}</td>
              <td class="td tabular-nums">{{ formatMs(item.latency_ms) }}</td>
              <td class="td text-xs text-slate-500">{{ formatDateTime(item.started_at) }}</td>
              <td class="td text-right">
                <button class="btn-ghost btn-xs" @click.stop="emit('select-execution', item.id)">
                  查看结果
                </button>
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
              ? '可调整执行状态或搜索关键词后重试。'
              : '任务启动后，每条用例的结果会实时出现在这里。'
          "
        >
          <button v-if="hasFilter" class="btn-ghost btn-xs mt-1" @click="resetFilters">
            重置筛选
          </button>
        </EmptyState>
        <div
          v-if="executionTotal > executionPageSize"
          class="flex items-center justify-between border-t border-slate-200 px-5 py-3"
        >
          <span class="text-[11px] text-slate-500">
            第 {{ executionPage }} / {{ executionPageCount }} 页
          </span>
          <div class="flex gap-1">
            <button class="btn-ghost btn-xs" :disabled="executionPage <= 1" @click="previousPage">
              上一页
            </button>
            <button
              class="btn-ghost btn-xs"
              :disabled="executionPage >= executionPageCount"
              @click="nextPage"
            >
              下一页
            </button>
          </div>
        </div>
      </div>
      </template>
    </template>
  </section>
</template>
