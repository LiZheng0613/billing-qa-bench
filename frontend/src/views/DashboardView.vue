<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import type {
  BatchTask,
  Environment,
  ExecutionListItem,
  ExecutionPassRate,
  FactStatsOverview,
  GraderEval,
  TaskStatus,
} from '../types'
import { errorMessage } from '../utils/errors'
import {
  formatDateTime,
  formatMs,
  formatPercent,
  formatScore,
  sourceLabel,
} from '../utils/format'
import { useToastStore } from '../stores/toast'

const router = useRouter()
const toast = useToastStore()

const caseTotal = ref(0)
const activeCaseTotal = ref(0)
const promptTotal = ref(0)
const modelTotal = ref(0)
const tasks = ref<BatchTask[]>([])
const taskTotal = ref(0)
const recentExecutions = ref<ExecutionListItem[]>([])
const executionTotal = ref(0)
const passRateSummary = ref<ExecutionPassRate | null>(null)
const environments = ref<Environment[]>([])
const loading = ref(true)
// 运营后台概览
const opsOverview = ref<FactStatsOverview | null>(null)
const candidateTotal = ref(0)
const candidatePending = ref(0)
const caliberTotal = ref(0)
const sourceTotal = ref(0)
const sourceActive = ref(0)
const sourceProbeFailed = ref(0)
const capabilityTotal = ref(0)
const latestEval = ref<GraderEval | null>(null)

let refreshTimer: number | null = null

const STATUS_ORDER: Array<{ key: TaskStatus | 'SCHEDULED'; label: string; color: string }> = [
  { key: 'RUNNING', label: '执行中', color: 'bg-indigo-500' },
  { key: 'PENDING', label: '等待中', color: 'bg-slate-500' },
  { key: 'SCHEDULED', label: '计划中', color: 'bg-sky-500' },
  { key: 'COMPLETED', label: '已完成', color: 'bg-emerald-500' },
  { key: 'ABORTED', label: '已终止', color: 'bg-slate-400' },
  { key: 'FAILED', label: '执行失败', color: 'bg-rose-500' },
]

const runningCount = computed(() => tasks.value.filter((item) => item.status === 'RUNNING').length)
const activeTaskCount = computed(
  () => tasks.value.filter((item) => ['RUNNING', 'PENDING'].includes(item.status)).length,
)

const taskStatusSegments = computed(() => {
  const counts = new Map<string, number>()
  tasks.value.forEach((task) => {
    counts.set(task.status, (counts.get(task.status) ?? 0) + 1)
  })
  return STATUS_ORDER.map((item) => ({
    ...item,
    count: item.key === 'SCHEDULED'
      ? tasks.value.filter((task) => task.status === 'SCHEDULED').length
      : counts.get(item.key) ?? 0,
  }))
})

const taskStatusTotal = computed(() =>
  taskStatusSegments.value.reduce((sum, item) => sum + item.count, 0),
)

const recentPassRate = computed(() => passRateSummary.value?.pass_rate ?? null)

const recentTaskList = computed(() => tasks.value.slice(0, 8))
const recentRunList = computed(() => recentExecutions.value.slice(0, 8))

const activeEnvironments = computed(() => environments.value.filter((item) => item.is_active))
const healthyCount = computed(
  () => activeEnvironments.value.filter((item) => item.last_health_status === 'UP').length,
)
const unhealthyCount = computed(
  () =>
    activeEnvironments.value.filter(
      (item) => item.last_health_status && item.last_health_status !== 'UP',
    ).length,
)

const envPreview = computed(() => activeEnvironments.value.slice(0, 6))

const evalHealthy = computed(() => {
  const latest = latestEval.value
  return Boolean(latest && latest.false_kills === 0 && latest.detected === latest.total)
})

function reportError(exc: unknown): void {
  toast.error(errorMessage(exc))
}

async function load(silent = false): Promise<boolean> {
  if (!silent) loading.value = true
  try {
    const [
      allCases,
      activeCases,
      taskPage,
      execPage,
      passRate,
      envPage,
      promptPage,
      modelPage,
      statsOverview,
      candidatesPage,
      pendingCandidatesPage,
      caliberPage,
      sourcePage,
      capabilityPage,
      evalPage,
    ] = await Promise.all([
      api.cases.list({ page: 1, page_size: 1 }),
      api.cases.list({ page: 1, page_size: 1, status: 'ACTIVE' }),
      api.tasks.list({ page_size: 50, include_runs: true }),
      api.executions.list({ page_size: 20 }),
      api.executions.passRate(24),
      api.environments.list({ page_size: 100 }),
      api.prompts.list({ page: 1, page_size: 1 }),
      api.models.list({ page: 1, page_size: 1 }),
      api.stats.overview(),
      api.candidates.list({ page: 1, page_size: 1 }),
      api.candidates.list({ status: 'NEW', page: 1, page_size: 1 }),
      api.calibers.list({ page: 1, page_size: 1 }),
      api.dataSources.list({ page: 1, page_size: 100 }),
      api.capabilities.list({ page: 1, page_size: 100 }),
      api.grader.evals({ page: 1, page_size: 1 }),
    ])
    caseTotal.value = allCases.total
    activeCaseTotal.value = activeCases.total
    tasks.value = taskPage.items
    taskTotal.value = taskPage.total
    recentExecutions.value = execPage.items
    executionTotal.value = execPage.total
    passRateSummary.value = passRate
    environments.value = envPage.items
    promptTotal.value = promptPage.total
    modelTotal.value = modelPage.total

    opsOverview.value = statsOverview
    candidateTotal.value = candidatesPage.total
    candidatePending.value = pendingCandidatesPage.total
    caliberTotal.value = caliberPage.total
    sourceTotal.value = sourcePage.total
    sourceActive.value = sourcePage.items.filter((item) => item.is_active).length
    sourceProbeFailed.value = sourcePage.items.filter(
      (item) => item.is_active && item.last_probe_status && item.last_probe_status !== 'OK',
    ).length
    capabilityTotal.value = capabilityPage.total
    latestEval.value = evalPage.items[0] ?? null
    return true
  } catch (exc) {
    if (!silent) reportError(exc)
    return false
  } finally {
    if (!silent) loading.value = false
  }
}

async function refreshData(): Promise<void> {
  if (await load()) toast.success('刷新成功')
}

function openTask(task: BatchTask): void {
  void router.push({ name: 'task-detail', params: { id: String(task.id) } })
}

function openExecution(executionId: number): void {
  void router.push({
    name: 'trace',
    query: { execution: String(executionId), from: 'task' },
  })
}

function taskProgress(task: BatchTask): number {
  if (!task.total_cases) return 0
  return Math.min(100, Math.round((task.finished_cases / task.total_cases) * 100))
}

function taskPassRate(task: BatchTask): string {
  if (!task.finished_cases) return '—'
  return formatPercent((task.pass_count / task.finished_cases) * 100, 1)
}

onMounted(() => {
  void load()
  refreshTimer = window.setInterval(() => {
    if (!document.hidden) void load(true)
  }, 20000)
})

onBeforeUnmount(() => {
  if (refreshTimer !== null) window.clearInterval(refreshTimer)
})
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      eyebrow="Dashboard · 系统总览"
      title="测试大盘"
      description="整个测试平台的运行总览：用例资产、批量任务状态、最近执行通过率、环境健康与模型 / Prompt 资产。"
    >
      <template #actions>
        <button class="btn-ghost" @click="refreshData">刷新</button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <MetricCard
        label="用例资产"
        :value="caseTotal"
        :hint="`其中启用 ${activeCaseTotal} 条`"
        accent="sky"
      />
      <MetricCard
        label="批量任务"
        :value="taskTotal"
        :hint="`最近 ${tasks.length} 条任务中 ${activeTaskCount} 条在队列`"
        accent="indigo"
      />
      <MetricCard
        label="运行中"
        :value="runningCount"
        hint="实时执行中的批量任务"
        accent="amber"
      />
      <MetricCard
        label="最近执行通过率"
        :value="formatPercent(recentPassRate, 1)"
        :hint="
          passRateSummary && passRateSummary.measured > 0
            ? `基于最近 24 小时 ${passRateSummary.measured} 次已判定执行`
            : '最近 24 小时暂无已判定执行'
        "
        accent="emerald"
        :progress="recentPassRate"
      />
    </div>

    <div class="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div class="min-w-0 space-y-5">
        <section class="card overflow-hidden">
          <header class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-5 py-3">
            <div>
              <h2 class="panel-title">批量任务状态</h2>
              <p class="mt-0.5 text-[11px] text-slate-500">基于最近 {{ tasks.length }} 条任务的状态分布</p>
            </div>
            <button class="text-[11px] font-medium text-indigo-600 transition hover:text-indigo-700" @click="router.push({ name: 'tasks' })">
              全部任务
            </button>
          </header>
          <div class="px-5 py-4">
            <div v-if="taskStatusTotal" class="flex h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                v-for="segment in taskStatusSegments.filter((item) => item.count > 0)"
                :key="segment.key"
                class="h-full transition-all duration-500"
                :class="segment.color"
                :style="{ width: `${(segment.count / taskStatusTotal) * 100}%` }"
                :title="`${segment.label}: ${segment.count}`"
              />
            </div>
            <div class="mt-3 flex flex-wrap gap-x-5 gap-y-2">
              <span
                v-for="segment in taskStatusSegments"
                :key="segment.key"
                class="inline-flex items-center gap-1.5 text-[11px]"
                :class="segment.count ? 'text-slate-600' : 'text-slate-400'"
              >
                <span class="h-1.5 w-1.5 rounded-full" :class="segment.color" />
                {{ segment.label }}
                <span class="font-medium tabular-nums text-slate-800">{{ segment.count }}</span>
              </span>
            </div>
          </div>
        </section>

        <section class="card overflow-hidden">
          <header class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-5 py-3">
            <div>
              <h2 class="panel-title">最近批量任务</h2>
              <p class="mt-0.5 text-[11px] text-slate-500">点击任务进入任务详情，查看逐用例结果与操作</p>
            </div>
            <button class="text-[11px] font-medium text-indigo-600 transition hover:text-indigo-700" @click="router.push({ name: 'tasks' })">
              查看更多
            </button>
          </header>
          <div class="overflow-x-auto">
            <table class="table-shell">
              <thead>
                <tr>
                  <th class="th">任务</th>
                  <th class="th">状态</th>
                  <th class="th">进度</th>
                  <th class="th">通过率</th>
                  <th class="th">创建时间</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in recentTaskList"
                  :key="item.id"
                  class="cursor-pointer"
                  @click="openTask(item)"
                >
                  <td class="td">
                    <p class="max-w-[240px] truncate text-sm font-medium text-slate-800">{{ item.name }}</p>
                    <p class="mt-0.5 font-mono text-[11px] text-slate-500">{{ item.code }}</p>
                  </td>
                  <td class="td"><StatusPill :status="item.status" size="sm" /></td>
                  <td class="td">
                    <div class="flex items-center gap-2">
                      <div class="h-1.5 w-20 overflow-hidden rounded-full bg-slate-100">
                        <div class="h-full rounded-full bg-indigo-500" :style="{ width: `${taskProgress(item)}%` }" />
                      </div>
                      <span class="text-[11px] tabular-nums text-slate-600">
                        {{ item.finished_cases }}/{{ item.total_cases }}
                      </span>
                    </div>
                  </td>
                  <td class="td text-xs tabular-nums text-slate-700">{{ taskPassRate(item) }}</td>
                  <td class="td text-xs text-slate-500">{{ formatDateTime(item.created_at) }}</td>
                </tr>
              </tbody>
            </table>
            <EmptyState
              v-if="!loading && recentTaskList.length === 0"
              compact
              title="还没有批量任务"
              description="前往批量任务页面即可创建并执行回归。"
            >
            </EmptyState>
          </div>
        </section>

        <section class="card overflow-hidden">
          <header class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-5 py-3">
            <div>
              <h2 class="panel-title">最近执行记录</h2>
              <p class="mt-0.5 text-[11px] text-slate-500">
                共 {{ executionTotal }} 次执行，点击记录查看单笔执行结果
              </p>
            </div>
          </header>
          <div class="overflow-x-auto">
            <table class="table-shell">
              <thead>
                <tr>
                  <th class="th">执行</th>
                  <th class="th">用例</th>
                  <th class="th">状态</th>
                  <th class="th">得分</th>
                  <th class="th">来源</th>
                  <th class="th">耗时</th>
                  <th class="th">开始时间</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in recentRunList"
                  :key="item.id"
                  class="cursor-pointer"
                  @click="openExecution(item.id)"
                >
                  <td class="td font-mono text-xs text-indigo-600">#{{ item.id }}</td>
                  <td class="td">
                    <p class="font-mono text-xs text-slate-800">{{ item.case_code || '—' }}</p>
                    <p class="mt-0.5 max-w-[200px] truncate text-[11px] text-slate-500">
                      {{ item.case_name || '—' }} · v{{ item.case_version }}
                    </p>
                  </td>
                  <td class="td"><StatusPill :status="item.status" size="sm" /></td>
                  <td class="td text-xs tabular-nums">{{ formatScore(item.total_score) }}</td>
                  <td class="td text-xs text-slate-600">{{ sourceLabel(item.source) }}</td>
                  <td class="td text-xs tabular-nums">{{ formatMs(item.latency_ms) }}</td>
                  <td class="td text-xs text-slate-500">{{ formatDateTime(item.started_at) }}</td>
                </tr>
              </tbody>
            </table>
            <EmptyState
              v-if="!loading && recentRunList.length === 0"
              compact
              title="还没有执行记录"
              description="运行一次批量任务或单条调试后再来查看。"
            />
          </div>
        </section>
      </div>

      <div class="space-y-5">
        <section class="card overflow-hidden">
          <header class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <div>
              <h2 class="panel-title">环境健康</h2>
              <p class="mt-0.5 text-[10px] text-slate-500">
                {{ activeEnvironments.length }} 个启用环境 · {{ healthyCount }} 健康
                <span v-if="unhealthyCount" class="text-rose-600"> · {{ unhealthyCount }} 异常</span>
              </p>
            </div>
            <RouterLink
              to="/settings"
              class="text-[11px] text-slate-500 transition hover:text-slate-800"
            >
              管理
            </RouterLink>
          </header>
          <div class="divide-y divide-slate-200">
            <div
              v-for="env in envPreview"
              :key="env.id"
              class="flex items-center justify-between gap-2 px-4 py-2.5"
            >
              <div class="min-w-0">
                <p class="truncate text-xs font-medium text-slate-800">{{ env.name }}</p>
                <p class="mt-0.5 font-mono text-[10px] text-slate-500">{{ env.code }}</p>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <span class="text-[10px] tabular-nums text-slate-500">{{ formatMs(env.last_health_latency_ms) }}</span>
                <StatusPill :status="env.last_health_status || 'PENDING'" size="sm" />
              </div>
            </div>
            <p v-if="envPreview.length === 0" class="px-4 py-4 text-[11px] text-slate-500">
              暂无启用的执行环境
            </p>
          </div>
        </section>

        <section class="card overflow-hidden">
          <header class="border-b border-slate-200 px-4 py-3">
            <h2 class="panel-title">资产概况</h2>
          </header>
          <dl class="divide-y divide-slate-200 px-4">
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">用例总数</dt>
              <dd class="font-medium tabular-nums text-slate-800">{{ caseTotal }}</dd>
            </div>
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">启用用例</dt>
              <dd class="font-medium tabular-nums text-emerald-600">{{ activeCaseTotal }}</dd>
            </div>
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">Prompt 模板</dt>
              <dd class="font-medium tabular-nums text-slate-800">{{ promptTotal }}</dd>
            </div>
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">模型配置</dt>
              <dd class="font-medium tabular-nums text-slate-800">{{ modelTotal }}</dd>
            </div>
          </dl>
        </section>

        <section class="card overflow-hidden">
          <header class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <div>
              <h2 class="panel-title">运营待办</h2>
              <p class="mt-0.5 text-[10px] text-slate-500">需要跟进的运营事项</p>
            </div>
            <RouterLink to="/ops" class="text-[11px] text-slate-500 transition hover:text-slate-800">
              运营后台
            </RouterLink>
          </header>
          <div class="divide-y divide-slate-200">
            <RouterLink
              to="/ops?tab=candidates"
              class="flex items-center justify-between gap-2 px-4 py-2.5 transition hover:bg-canvas"
            >
              <span class="text-xs text-slate-600">待分诊候选</span>
              <span
                class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                :class="
                  candidatePending
                    ? 'bg-amber-50 text-amber-700'
                    : 'bg-slate-100 text-slate-500'
                "
              >
                {{ candidatePending }} 条
              </span>
            </RouterLink>
            <RouterLink
              to="/ops?tab=sources"
              class="flex items-center justify-between gap-2 px-4 py-2.5 transition hover:bg-canvas"
            >
              <span class="text-xs text-slate-600">数据源探活异常</span>
              <span
                class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                :class="
                  sourceProbeFailed
                    ? 'bg-rose-50 text-rose-700'
                    : 'bg-emerald-50 text-emerald-700'
                "
              >
                {{ sourceProbeFailed }} 个
              </span>
            </RouterLink>
            <RouterLink
              to="/ops?tab=grader"
              class="flex items-center justify-between gap-2 px-4 py-2.5 transition hover:bg-canvas"
            >
              <span class="text-xs text-slate-600">判分器自检</span>
              <span
                v-if="latestEval"
                class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                :class="
                  evalHealthy
                    ? 'bg-emerald-50 text-emerald-700'
                    : 'bg-rose-50 text-rose-700'
                "
              >
                识别 {{ latestEval.detected }}/{{ latestEval.total }} · 误杀 {{ latestEval.false_kills }}
              </span>
              <span v-else class="text-[10px] text-slate-500">尚未运行</span>
            </RouterLink>
            <RouterLink
              to="/ops?tab=stats"
              class="flex items-center justify-between gap-2 px-4 py-2.5 transition hover:bg-canvas"
            >
              <span class="text-xs text-slate-600">幸运通过</span>
              <span
                class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                :class="
                  opsOverview?.lucky_pass
                    ? 'bg-amber-50 text-amber-700'
                    : 'bg-slate-100 text-slate-500'
                "
              >
                {{ opsOverview?.lucky_pass ?? 0 }} 次
              </span>
            </RouterLink>
          </div>
        </section>

        <section class="card overflow-hidden">
          <header class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <div>
              <h2 class="panel-title">运营概况</h2>
              <p class="mt-0.5 text-[10px] text-slate-500">口径、数据源与取数能力</p>
            </div>
            <RouterLink to="/ops" class="text-[11px] text-slate-500 transition hover:text-slate-800">
              查看全部
            </RouterLink>
          </header>
          <dl class="divide-y divide-slate-200 px-4">
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">业务口径</dt>
              <dd class="font-medium tabular-nums text-slate-800">{{ caliberTotal }}</dd>
            </div>
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">数据源</dt>
              <dd class="font-medium tabular-nums text-slate-800">
                {{ sourceTotal }}
                <span class="ml-1 font-normal text-slate-500">启用 {{ sourceActive }}</span>
              </dd>
            </div>
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">取数能力</dt>
              <dd class="font-medium tabular-nums text-slate-800">{{ capabilityTotal }}</dd>
            </div>
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">契约执行</dt>
              <dd class="font-medium tabular-nums text-slate-800">
                {{ opsOverview?.execution_total ?? 0 }} 次
              </dd>
            </div>
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">契约通过率</dt>
              <dd class="font-medium tabular-nums text-emerald-600">
                {{ formatPercent(opsOverview?.pass_rate, 1) }}
              </dd>
            </div>
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">事实通过率</dt>
              <dd class="font-medium tabular-nums text-indigo-600">
                {{ formatPercent(opsOverview?.fact_pass_rate, 1) }}
              </dd>
            </div>
            <div class="flex items-center justify-between py-2.5 text-xs">
              <dt class="text-slate-500">候选池</dt>
              <dd class="font-medium tabular-nums text-slate-800">
                {{ candidateTotal }}
                <span class="ml-1 font-normal text-slate-500">待分诊 {{ candidatePending }}</span>
              </dd>
            </div>
          </dl>
        </section>

        <p class="text-[10px] text-slate-500">
          大盘每 20 秒自动刷新一次；任务详情页内可查看用例级结果与实时进度。
        </p>
      </div>
    </div>
  </div>
</template>
