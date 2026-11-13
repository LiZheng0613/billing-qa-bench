<script setup lang="ts">
import { computed } from 'vue'
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
} from '../../utils/format'

const props = withDefaults(
  defineProps<{
    task: BatchTask | null
    metrics: TaskMetrics | null
    executions: CaseExecutionItem[]
    live?: boolean
  }>(),
  { live: false },
)

const emit = defineEmits<{
  (e: 'refresh'): void
  (e: 'pause'): void
  (e: 'resume'): void
  (e: 'abort'): void
  (e: 'retry'): void
  (e: 'select-execution', executionId: number): void
  (e: 'view-task-trace', taskId: number): void
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

const hasTask = computed(() => Boolean(props.task))
const canPause = computed(() => props.task?.status === 'RUNNING')
const canResume = computed(() => props.task?.status === 'PAUSED')
const canAbort = computed(() =>
  Boolean(props.task && ['RUNNING', 'PAUSED', 'PENDING'].includes(props.task.status)),
)
const canRetry = computed(() =>
  Boolean(props.task && ['COMPLETED', 'ABORTED', 'FAILED'].includes(props.task.status)),
)

const progress = computed(() => props.metrics?.progress ?? 0)

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
</script>

<template>
  <section class="card overflow-hidden">
    <header class="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 px-5 py-4">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-sm font-semibold text-slate-100">批量执行监控</h2>
          <span
            v-if="live"
            class="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-300"
          >
            <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
            WebSocket 实时
          </span>
        </div>
        <p v-if="task" class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
          <span class="font-mono text-slate-400">{{ task.code }}</span>
          <span>·</span>
          <span class="truncate">{{ task.name }}</span>
          <span>·</span>
          <span>{{ task.environment_name || '未绑定环境' }}</span>
          <span>·</span>
          <span>并发 {{ task.concurrency }} / QPS {{ task.qps_limit }}</span>
          <span>·</span>
          <span>
            {{ task.retry_on_env_error ? `环境失败自动重试 ${task.max_env_retries} 次` : '环境失败不重试' }}
          </span>
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <StatusPill v-if="task" :status="task.status" />
        <button class="btn-ghost btn-xs" :disabled="!hasTask" @click="emit('refresh')">刷新</button>
        <button v-if="canPause" class="btn-ghost btn-xs" @click="emit('pause')">暂停</button>
        <button v-if="canResume" class="btn-primary btn-xs" @click="emit('resume')">恢复</button>
        <button v-if="canAbort" class="btn-danger btn-xs" @click="emit('abort')">终止</button>
        <button v-if="canRetry" class="btn-ghost btn-xs" @click="emit('retry')">失败重跑</button>
        <button
          v-if="task"
          class="btn-ghost btn-xs"
          @click="emit('view-task-trace', task.id)"
        >
          执行明细
        </button>
      </div>
    </header>

    <div v-if="!task" class="px-5">
      <EmptyState title="还没有批量任务" description="在右上角新建批量任务，选择一个或多个用例开始回归。" />
    </div>

    <template v-else>
      <div class="border-b border-slate-800 px-5 py-4">
        <div class="flex items-center justify-between text-xs text-slate-400">
          <span>
            {{ metrics ? `${metrics.finished_cases} / ${metrics.total_cases}` : `${task.finished_cases} / ${task.total_cases}` }}
            条已完成
          </span>
          <span class="tabular-nums">{{ progress.toFixed(1) }}%</span>
        </div>
        <div class="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
          <div
            class="h-full rounded-full bg-gradient-to-r from-indigo-500 to-cyan-400 transition-all duration-500"
            :style="{ width: `${Math.min(100, Math.max(0, progress))}%` }"
          />
        </div>
        <div v-if="segmented.length" class="mt-3 flex h-2 overflow-hidden rounded-full bg-slate-800">
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

      <div class="grid grid-cols-2 gap-3 border-b border-slate-800 px-5 py-4 sm:grid-cols-3 xl:grid-cols-6">
        <div v-for="item in stats" :key="item.label">
          <p class="text-[10px] uppercase tracking-wider text-slate-500">{{ item.label }}</p>
          <p class="mt-0.5 text-lg font-semibold tabular-nums text-slate-100">{{ item.value }}</p>
          <p v-if="item.hint" class="mt-0.5 text-[10px] text-slate-600">{{ item.hint }}</p>
        </div>
      </div>

      <div class="overflow-x-auto">
        <div class="flex items-center justify-between px-5 pt-4">
          <h3 class="panel-title">最近执行</h3>
          <span class="text-[11px] text-slate-500">点击行查看全链路 Trace</span>
        </div>
        <table class="table-shell mt-2">
          <thead>
            <tr>
              <th class="th">#</th>
              <th class="th">用例</th>
              <th class="th">状态</th>
              <th class="th">得分</th>
              <th class="th">轮次</th>
              <th class="th">时延</th>
              <th class="th">开始时间</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in executions"
              :key="item.id"
              class="cursor-pointer"
              @click="emit('select-execution', item.id)"
            >
              <td class="td font-mono text-xs text-slate-500">{{ item.id }}</td>
              <td class="td">
                <p class="font-mono text-xs text-slate-200">{{ item.case_code || `用例 #${item.case_id ?? '—'}` }}</p>
                <p class="mt-0.5 max-w-[240px] truncate text-[11px] text-slate-500">
                  {{ item.case_name || '—' }} · v{{ item.case_version }}
                  <span v-if="item.attempt_no > 1" class="text-amber-400"> · 第 {{ item.attempt_no }} 次尝试</span>
                </p>
              </td>
              <td class="td"><StatusPill :status="item.status" size="sm" /></td>
              <td class="td tabular-nums">{{ formatScore(item.total_score) }}</td>
              <td class="td tabular-nums">{{ item.turns_used }}</td>
              <td class="td tabular-nums">{{ formatMs(item.latency_ms) }}</td>
              <td class="td text-xs text-slate-500">{{ formatDateTime(item.started_at) }}</td>
            </tr>
          </tbody>
        </table>
        <EmptyState
          v-if="executions.length === 0"
          compact
          title="暂无执行记录"
          description="任务启动后，执行结果会实时出现在这里。"
        />
      </div>
    </template>
  </section>
</template>
