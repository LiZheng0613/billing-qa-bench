<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { useTaskEventStream } from '../composables/useTaskEventStream'
import type {
  BatchTask,
  DataSource,
  Environment,
  ExecutionDailyPoint,
  ExecutionEnvironmentMetric,
  ExecutionEnvironmentSummary,
  ExecutionEnvironmentTrendSeries,
  ExecutionListItem,
  ExecutionSummary,
  FactStatsOverview,
  GraderEval,
  TaskProgressEvent,
} from '../types'
import { errorMessage } from '../utils/errors'
import {
  formatMs,
  formatPercent,
  formatScore,
  sourceLabel,
  statusLabel,
} from '../utils/format'
import { useAuthStore } from '../stores/auth'
import { useToastStore } from '../stores/toast'
import { roundLabel, stripRoundSuffix } from '../utils/schedule'
import DashboardTrendChart from './components/DashboardTrendChart.vue'
import DashboardEnvTrendChart from './components/DashboardEnvTrendChart.vue'
import TaskCreateModal from './components/TaskCreateModal.vue'

const router = useRouter()
const toast = useToastStore()
const auth = useAuthStore()

const caseTotal = ref(0)
const activeCaseTotal = ref(0)
const promptTotal = ref(0)
const modelTotal = ref(0)
const tasks = ref<BatchTask[]>([])
const recentExecutions = ref<ExecutionListItem[]>([])
const failedExecutions = ref<ExecutionListItem[]>([])
const executionTotal = ref(0)
const summary = ref<ExecutionSummary | null>(null)
const summaryError = ref(false)
type DashboardViewMode = 'overview' | 'environments'
const viewMode = ref<DashboardViewMode>('overview')
const envDays = ref<7 | 30>(7)
const envSummary = ref<ExecutionEnvironmentSummary | null>(null)
const envLoading = ref(false)
const envError = ref(false)
let envLoadedKey: string | null = null
const ENV_COLORS = ['#4f46e5', '#059669', '#d97706', '#0284c7', '#e11d48', '#7c3aed']
const environments = ref<Environment[]>([])
const dataSources = ref<DataSource[]>([])
const loading = ref(true)
const showCreate = ref(false)
const trendDays = ref<7 | 14>(7)
const runFilter = ref<'all' | 'fail'>('all')
const lastUpdated = ref<Date | null>(null)
const nowMs = ref(Date.now())

// 运营后台概览
const opsOverview = ref<FactStatsOverview | null>(null)
const candidatePending = ref(0)
const caliberTotal = ref(0)
const sourceTotal = ref(0)
const sourceActive = ref(0)
const sourceProbeFailed = ref(0)
const capabilityTotal = ref(0)
const latestEval = ref<GraderEval | null>(null)

let refreshTimer: number | null = null
let autoTimer: number | null = null
let lastLoadAt = 0
// 字典板块（环境/数据源/能力列表）上次全量拉取时间：定时刷新默认跳过它们，
// 但超过该间隔后强制兜底一次，保证前台长挂机用户也能看到探活告警更新。
let lastDictLoadAt = 0
const DICT_REFRESH_MS = 5 * 60_000
// 上一轮 load 是否仍在途：自动刷新（定时器/回前台）在未完成时跳过本轮，
// 避免慢响应堆叠与乱序覆盖；手动刷新不受限。
let loadInFlight = false

const canTask = computed(() => auth.can('task:read'))
const canExecution = computed(() => auth.can('execution:read'))
const canEnvironment = computed(() => auth.can('environment:read'))
const canStats = computed(() => auth.can('stats:read'))
const canCandidate = computed(() => auth.can('candidate:read'))
const canCapability = computed(() => auth.can('capability:read'))
const canGrader = computed(() => auth.can('grader:run'))
const canOpsCard = computed(
  () => canCandidate.value || canCapability.value || canGrader.value || canStats.value,
)

function scheduleReload(): void {
  if (refreshTimer !== null) return
  refreshTimer = window.setTimeout(() => {
    refreshTimer = null
    void load(true)
    if (viewMode.value === 'environments') void loadEnvironmentSummary(true)
  }, 1500)
}

function applyTaskEvent(event: TaskProgressEvent): void {
  if (!event || event.type === 'ping') return
  const index = tasks.value.findIndex((item) => item.id === event.task_id)
  if (index < 0) {
    // 新任务或周期计划派生的 run 不在当前列表，节流拉一次完整数据
    scheduleReload()
    return
  }
  const current = tasks.value[index]
  tasks.value.splice(index, 1, {
    ...current,
    status: event.status ?? current.status,
    control_state: event.control_state ?? current.control_state,
    finished_cases: event.finished_cases ?? current.finished_cases,
    total_cases: event.total_cases ?? current.total_cases,
    pass_count: event.pass_count ?? current.pass_count,
    fail_count: event.fail_count ?? current.fail_count,
    error_count: event.error_count ?? current.error_count,
    max_turns_count: event.max_turns_count ?? current.max_turns_count,
  })
  // 终态会改变通过率/近期执行等聚合卡片，事件驱动补一次全量刷新
  if (event.status && ['COMPLETED', 'ABORTED', 'FAILED'].includes(event.status)) {
    scheduleReload()
  }
}

useTaskEventStream(applyTaskEvent, {
  onOpen: () => {
    // 首屏 onMounted 刚拉过数据，连接建立时不再重复请求一次
    if (Date.now() - lastLoadAt < 1200) return
    void load(true)
  },
})

const windowStats = computed(() => summary.value?.window ?? null)
const passRate = computed(() => windowStats.value?.pass_rate ?? null)
const measured = computed(() => windowStats.value?.measured ?? 0)
const windowFailed = computed(() => windowStats.value?.failed ?? 0)
const unmeasured = computed(() => {
  const stats = windowStats.value
  return stats ? Math.max(0, stats.total - stats.measured) : 0
})

const passDelta = computed(() => {
  const stats = windowStats.value
  if (!stats || stats.pass_rate === null || stats.previous_pass_rate === null) return null
  return stats.pass_rate - stats.previous_pass_rate
})
const belowTarget = computed(() => passRate.value !== null && passRate.value < 95)
const targetGap = computed(() => (passRate.value === null ? null : 95 - passRate.value))

const daily = computed<ExecutionDailyPoint[]>(() => summary.value?.daily ?? [])
const trendPoints = computed(() => daily.value.slice(-trendDays.value))

function averageRate(points: ExecutionDailyPoint[]): number | null {
  const totalMeasured = points.reduce((sum, item) => sum + item.measured, 0)
  const totalPassed = points.reduce((sum, item) => sum + item.passed, 0)
  return totalMeasured ? (totalPassed / totalMeasured) * 100 : null
}

const sevenDayAvg = computed(() => averageRate(daily.value.slice(-7)))
const trendAvg = computed(() => averageRate(trendPoints.value))
const previousTrendAvg = computed(() =>
  trendDays.value === 7 && daily.value.length >= 14 ? averageRate(daily.value.slice(0, 7)) : null,
)
const trendDelta = computed(() =>
  trendAvg.value === null || previousTrendAvg.value === null
    ? null
    : trendAvg.value - previousTrendAvg.value,
)
const maxVolumeDay = computed(() =>
  trendPoints.value.reduce((max, item) => Math.max(max, item.total), 0),
)
const avgVolume = computed(() => {
  if (!trendPoints.value.length) return 0
  const total = trendPoints.value.reduce((sum, item) => sum + item.total, 0)
  return Math.round(total / trendPoints.value.length)
})

const spark = computed(() => {
  const values = daily.value
    .slice(-7)
    .map((item) => item.pass_rate)
    .filter((item): item is number => item !== null)
  if (values.length < 2) {
    return { points: '', lastX: 0, lastY: 0, visible: false }
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const coords = values.map((value, index) => ({
    x: 2 + (index * 72) / (values.length - 1),
    y: 22 - ((value - min) / span) * 18,
  }))
  const last = coords[coords.length - 1]
  return {
    points: coords.map((item) => `${item.x.toFixed(1)},${item.y.toFixed(1)}`).join(' '),
    lastX: last.x,
    lastY: last.y,
    visible: true,
  }
})

const latencyP95 = computed(() => summary.value?.latency.p95_ms ?? null)
const latencyAvg = computed(() => summary.value?.latency.avg_ms ?? null)
const latencyTtft = computed(() => summary.value?.latency.avg_ttft_ms ?? null)
const tokensTotal = computed(() => {
  const tokens = summary.value?.tokens
  return tokens ? tokens.simulator + tokens.judge : 0
})
const tokenDisplay = computed(() => {
  const value = tokensTotal.value
  if (value >= 1_000_000) return { value: (value / 1_000_000).toFixed(2), unit: 'M' }
  if (value >= 1_000) return { value: (value / 1_000).toFixed(1), unit: 'K' }
  return { value: String(value), unit: '' }
})
const tokenSplit = computed(() => {
  const tokens = summary.value?.tokens
  const total = tokens ? tokens.simulator + tokens.judge : 0
  if (!total) return []
  return [
    { cls: 'bg-violet-400', width: (tokens!.simulator / total) * 100 },
    { cls: 'bg-sky-400', width: (tokens!.judge / total) * 100 },
  ].filter((segment) => segment.width > 0)
})

const volumeSegments = computed(() => {
  const stats = windowStats.value
  if (!stats || !stats.total) return []
  return [
    { cls: 'bg-indigo-500', width: (stats.passed / stats.total) * 100 },
    { cls: 'bg-rose-500', width: (stats.failed / stats.total) * 100 },
    { cls: 'bg-slate-200', width: (unmeasured.value / stats.total) * 100 },
  ].filter((segment) => segment.width > 0)
})

const runningTasks = computed(() => tasks.value.filter((item) => item.status === 'RUNNING'))
const pendingTasks = computed(() => tasks.value.filter((item) => item.status === 'PENDING'))
const scheduledTasks = computed(() =>
  tasks.value
    .filter((item) => item.status === 'SCHEDULED' && item.schedule_active)
    .sort((a, b) => (Date.parse(a.next_run_at ?? '') || 0) - (Date.parse(b.next_run_at ?? '') || 0)),
)
const bandTasks = computed(() =>
  [...runningTasks.value, ...pendingTasks.value, ...scheduledTasks.value].slice(0, 4),
)
const bandMeta = computed(() => {
  const parts = [`${runningTasks.value.length} 个执行中`, `${pendingTasks.value.length} 个排队`]
  if (scheduledTasks.value.length) parts.push(`${scheduledTasks.value.length} 个计划任务`)
  parts.push('进度由 WebSocket 推送')
  return parts.join(' · ')
})

const endedTasks = computed(() =>
  tasks.value
    .filter((item) => ['COMPLETED', 'ABORTED', 'FAILED'].includes(item.status))
    .sort(
      (a, b) =>
        (Date.parse(b.finished_at ?? b.created_at) || 0) -
        (Date.parse(a.finished_at ?? a.created_at) || 0),
    )
    .slice(0, 4),
)

const recentRunList = computed(() => {
  const items =
    runFilter.value === 'fail' ? failedExecutions.value : recentExecutions.value
  return items.filter((item) => item.source !== 'SANDBOX').slice(0, 6)
})

const activeEnvironments = computed(() => environments.value.filter((item) => item.is_active))
const envAlerts = computed(() =>
  activeEnvironments.value.filter(
    (item) => item.last_health_status && item.last_health_status !== 'UP',
  ),
)
const healthyCount = computed(
  () =>
    activeEnvironments.value.filter(
      (item) => !item.last_health_status || item.last_health_status === 'UP',
    ).length,
)
const activeDataSources = computed(() => dataSources.value.filter((item) => item.is_active))
const sourceAlerts = computed(() =>
  activeDataSources.value.filter(
    (item) => item.last_probe_status && item.last_probe_status !== 'OK',
  ),
)

const bannerItems = computed(() => {
  const items: string[] = []
  envAlerts.value.slice(0, 2).forEach((item) => items.push(`${item.code} 探活失败`))
  sourceAlerts.value.slice(0, 2).forEach((item) => items.push(`数据源 ${item.code} 异常`))
  if (belowTarget.value) {
    items.push(`近 24h 通过率 ${formatPercent(passRate.value, 1)}（目标 95%）`)
  }
  return items
})
const bannerOk = computed(() => bannerItems.value.length === 0)
// 取到聚合结果但窗口内没有任何已判定执行时，不急着判定「正常」，先给空态结论。
const bannerIdle = computed(
  () => canExecution.value && windowStats.value !== null && measured.value === 0,
)
const bannerError = computed(() => canExecution.value && summaryError.value)
const bannerDetail = computed(() => {
  if (bannerError.value) return '执行聚合加载失败，刷新重试；其余板块不受影响。'
  if (bannerItems.value.length) return bannerItems.value.join(' · ')
  if (bannerIdle.value) return '运行一次批量任务后，这里会给出通过率与健康结论。'
  const parts: string[] = []
  if (canTask.value) {
    parts.push(`${runningTasks.value.length} 个任务执行中`)
    parts.push(`${pendingTasks.value.length} 个排队`)
  }
  if (canEnvironment.value) {
    parts.push(
      activeEnvironments.value.length
        ? `${healthyCount.value}/${activeEnvironments.value.length} 个环境健康`
        : '暂无启用环境',
    )
  }
  if (canCapability.value) {
    if (!sourceTotal.value) parts.push('暂无数据源')
    else if (sourceAlerts.value.length) parts.push(`${sourceAlerts.value.length} 个数据源异常`)
    else parts.push('数据源全部正常')
  }
  return parts.length ? parts.join(' · ') : '暂无可展示的运行指标'
})

// ---------------------------------------------------------------- 环境对比
const envMetrics = computed(() => envSummary.value?.environments ?? [])
const envWithData = computed(() => envMetrics.value.filter((item) => item.measured > 0))
const envMeasuredTotal = computed(() =>
  envWithData.value.reduce((sum, item) => sum + item.measured, 0),
)
const envBestPass = computed(() => {
  const candidates = envWithData.value.filter((item) => item.pass_rate !== null)
  if (!candidates.length) return null
  return candidates.reduce((best, item) =>
    (item.pass_rate as number) > (best.pass_rate as number) ? item : best,
  )
})
const envBestStable = computed(() => {
  // 样本太少的波动没有解释力，稳定性排名至少要求 3 个已判定执行。
  const candidates = envWithData.value.filter(
    (item) => item.volatility !== null && item.measured >= 3,
  )
  if (!candidates.length) return null
  return candidates.reduce((best, item) =>
    (item.volatility as number) < (best.volatility as number) ? item : best,
  )
})
const envBestPassKey = computed(() => envBestPass.value?.code ?? null)
const envBestStableKey = computed(() => envBestStable.value?.code ?? null)
const envColorMap = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  envMetrics.value.forEach((item, index) => {
    map[item.code] = ENV_COLORS[index % ENV_COLORS.length]
  })
  return map
})
const envTrendSeries = computed<ExecutionEnvironmentTrendSeries[]>(() =>
  envWithData.value.slice(0, 6).map((item) => ({
    key: item.code,
    name: item.name,
    color: envColorMap.value[item.code] ?? ENV_COLORS[0],
    points: item.daily,
  })),
)
const envBanner = computed(() => {
  if (envError.value) {
    return {
      tone: 'border-rose-200 bg-rose-50 text-rose-900',
      dot: 'bg-rose-500 ring-4 ring-rose-500/15',
      title: '环境对比加载失败',
      detail: '当前视图只降级这一块，质量总览与任务进度不受影响；点右上角刷新重试。',
    }
  }
  if (!envMetrics.value.length) {
    return {
      tone: 'border-slate-200 bg-slate-50 text-slate-700',
      dot: 'bg-slate-400 ring-4 ring-slate-400/15',
      title: '暂无执行环境',
      detail: '先在「设置 · 执行环境」中接入被测 Agent，再回到这里做横向对比。',
    }
  }
  if (!envMeasuredTotal.value) {
    return {
      tone: 'border-slate-200 bg-slate-50 text-slate-700',
      dot: 'bg-slate-400 ring-4 ring-slate-400/15',
      title: `近 ${envDays.value} 天暂无已判定执行`,
      detail: '环境已就绪但还没有判定结果；跑一轮调测后这里会给出正确率与稳定性对比。',
    }
  }
  if (envWithData.value.length === 1) {
    const only = envWithData.value[0]
    return {
      tone: 'border-indigo-200 bg-indigo-50/70 text-indigo-950',
      dot: 'bg-indigo-500 ring-4 ring-indigo-500/15',
      title: `近 ${envDays.value} 天仅 1 个环境有数据`,
      detail: `${only.name} 已判定 ${only.measured} 次、通过率 ${formatPercent(only.pass_rate, 1)}；接入第二个环境后才能横向比较优化思路。`,
    }
  }
  const parts = [`${envWithData.value.length} 个环境有数据`, `${envMeasuredTotal.value} 次已判定`]
  if (envBestPass.value) {
    parts.push(
      `正确率最高 ${envBestPass.value.name} ${formatPercent(envBestPass.value.pass_rate, 1)}`,
    )
  }
  if (envBestStable.value) {
    parts.push(
      `波动最小 ${envBestStable.value.name} ±${(envBestStable.value.volatility as number).toFixed(1)}pp`,
    )
  }
  const flaky = envWithData.value.reduce((sum, item) => sum + item.flaky_cases, 0)
  parts.push(flaky ? `抖动 ${flaky} 个用例` : '暂未发现抖动用例')
  return {
    tone: 'border-indigo-200 bg-indigo-50/70 text-indigo-950',
    dot: 'bg-indigo-500 ring-4 ring-indigo-500/15',
    title: `近 ${envDays.value} 天 · ${envWithData.value.length} 个环境可对比`,
    detail: parts.join(' · '),
  }
})

function envDelta(item: ExecutionEnvironmentMetric): number | null {
  if (item.pass_rate === null || item.previous_pass_rate === null) return null
  return item.pass_rate - item.previous_pass_rate
}

function envDeltaValue(item: ExecutionEnvironmentMetric): number {
  return envDelta(item) ?? 0
}

function envVolatilityValue(item: ExecutionEnvironmentMetric | null): number {
  return item?.volatility ?? 0
}

function envSpark(item: ExecutionEnvironmentMetric): {
  points: string
  lastX: number
  lastY: number
  visible: boolean
} {
  const values = item.daily
    .slice(-7)
    .map((point) => point.pass_rate)
    .filter((value): value is number => value !== null)
  if (values.length < 2) return { points: '', lastX: 0, lastY: 0, visible: false }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const coords = values.map((value, index) => ({
    x: 2 + (index * 72) / (values.length - 1),
    y: 22 - ((value - min) / span) * 18,
  }))
  const last = coords[coords.length - 1]
  return {
    points: coords.map((coord) => `${coord.x.toFixed(1)},${coord.y.toFixed(1)}`).join(' '),
    lastX: last.x,
    lastY: last.y,
    visible: true,
  }
}

function envHealth(item: ExecutionEnvironmentMetric): { label: string; cls: string } {
  if (!item.is_active) {
    return { label: '未启用', cls: 'border-slate-200 bg-slate-50 text-slate-500' }
  }
  if (!item.last_health_status) {
    return { label: '未探活', cls: 'border-slate-200 bg-slate-50 text-slate-500' }
  }
  if (item.last_health_status === 'UP' || item.last_health_status === 'OK') {
    const latency =
      item.last_health_latency_ms === null
        ? ''
        : ` ${Math.round(item.last_health_latency_ms)}ms`
    return {
      label: `健康${latency}`,
      cls: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    }
  }
  return {
    label: statusLabel(item.last_health_status),
    cls: 'border-rose-200 bg-rose-50 text-rose-700',
  }
}

const envFailureTone: Record<string, string> = {
  FAIL: 'bg-rose-400',
  MAX_TURNS_EXCEEDED: 'bg-amber-400',
  REPETITION_ABORT: 'bg-orange-400',
  ERROR: 'bg-slate-400',
}

function envFailureTotal(item: ExecutionEnvironmentMetric): number {
  return item.failure_reasons.reduce((sum, reason) => sum + reason.count, 0)
}

function envFailureWidth(item: ExecutionEnvironmentMetric, count: number): number {
  const total = envFailureTotal(item)
  return total ? (count / total) * 100 : 0
}

const envFailureLegend = computed(() => {
  const seen = new Set<string>()
  envWithData.value.forEach((item) =>
    item.failure_reasons.forEach((reason) => seen.add(reason.status)),
  )
  return [...seen].map((status) => ({
    status,
    label: statusLabel(status),
    cls: envFailureTone[status] ?? 'bg-slate-300',
  }))
})

function volatilityTone(value: number | null): string {
  if (value === null) return 'text-ink-muted'
  if (value <= 5) return 'text-emerald-700'
  if (value <= 10) return 'text-ink-soft'
  return 'text-amber-700'
}

function envRateTone(item: ExecutionEnvironmentMetric): string {
  if (item.measured === 0 || item.pass_rate === null) return 'text-ink-muted'
  if (item.code === envBestPassKey.value) return 'text-emerald-700'
  return 'text-ink'
}

const sourceRows = computed(() =>
  (summary.value?.by_source ?? []).slice(0, 4).map((item) => ({
    ...item,
    label: sourceShort(item.source),
    low: (item.pass_rate ?? 100) < 90,
  })),
)

const envTiles = computed(() => {
  if (!envAlerts.value.length) {
    return activeEnvironments.value.slice(0, 4).map((item) => ({
      bad: false,
      name: item.code,
      metric: formatMs(item.last_health_latency_ms),
      dot: 'ok',
    }))
  }
  const tiles: Array<{ bad: boolean; name: string; metric: string; dot: string }> =
    envAlerts.value.slice(0, 2).map((item) => ({
      bad: true,
      name: item.code,
      metric:
        item.last_health_status === 'DOWN'
          ? '不可用'
          : `异常 · ${formatMs(item.last_health_latency_ms)}`,
      dot: 'bad',
    }))
  const moreAlerts = envAlerts.value.length - tiles.length
  if (moreAlerts > 0 && tiles.length < 4) {
    tiles.push({
      bad: true,
      name: `其余 ${moreAlerts} 个异常`,
      metric: '需处理',
      dot: 'bad',
    })
  }
  if (healthyCount.value > 0 && tiles.length < 4) {
    tiles.push({
      bad: false,
      name: `其余 ${healthyCount.value} 个环境`,
      metric: '均正常',
      dot: 'ok',
    })
  }
  return tiles
})

const envHealthText = computed(() => {
  const badEnv = envAlerts.value.length
  const badSource = sourceAlerts.value.length
  if (badEnv || badSource) {
    const parts: string[] = []
    if (badEnv) parts.push(`${badEnv} 个环境`)
    if (badSource) parts.push(`${badSource} 个数据源`)
    return `${badEnv + badSource} 项异常 · ${parts.join(' + ')}`
  }
  return `${activeEnvironments.value.length} 个运行环境 · ${activeDataSources.value.length} 个数据源`
})

const sourceLine = computed(() => {
  const list = activeDataSources.value
  if (!list.length) return '暂无启用的数据源'
  const parts = list
    .slice(0, 3)
    .map((item) => {
      const suffix = item.last_probe_status && item.last_probe_status !== 'OK' ? '（异常）' : ''
      return `${item.code} ${formatMs(item.last_probe_latency_ms)}${suffix}`
    })
  if (list.length > 3) parts.push(`其余 ${list.length - 3} 个`)
  return parts.join(' · ')
})

const REASON_META: Record<string, { label: string; color: string }> = {
  FAIL: { label: '判分不通过', color: '#f43f5e' },
  ERROR: { label: '执行异常', color: '#f59e0b' },
  MAX_TURNS_EXCEEDED: { label: '轮次超限', color: '#f97316' },
  REPETITION_ABORT: { label: '复读熔断', color: '#d946ef' },
  ABORTED: { label: '已终止', color: '#64748b' },
}

const failureReasons = computed(() => {
  const rows = summary.value?.failure_reasons ?? []
  const total = rows.reduce((sum, item) => sum + item.count, 0)
  return rows.map((item) => {
    const meta = REASON_META[item.status] ?? {
      label: statusLabel(item.status),
      color: '#94a3b8',
    }
    return {
      status: item.status,
      label: meta.label,
      color: meta.color,
      count: item.count,
      width: total ? (item.count / total) * 100 : 0,
    }
  })
})
const failureTotal = computed(() => failureReasons.value.reduce((sum, item) => sum + item.count, 0))
const topFailedCases = computed(() => summary.value?.top_failed_cases ?? [])
const failedFacts = computed(() => opsOverview.value?.top_failed.slice(0, 5) ?? [])

const failureAdvice = computed(() => {
  const top = failureReasons.value[0]
  if (!top) return ''
  const topCase = topFailedCases.value[0]
  const caseHint = topCase?.case_code ? `，先抽查 ${topCase.case_code} 的 Must 事实命中情况` : ''
  const errorCount = failureReasons.value.find((item) => item.status === 'ERROR')?.count ?? 0
  if (top.status === 'FAIL') {
    return `判分不通过占 ${top.width.toFixed(1)}%${caseHint}${errorCount ? `；另有 ${errorCount} 次执行异常，可先确认环境探活` : ''}。`
  }
  if (top.status === 'ERROR') {
    return `执行异常 ${top.count} 次，优先确认环境探活与依赖数据源，再重试失败执行。`
  }
  return `${top.label} ${top.count} 次，建议回到任务详情查看逐用例链路。`
})

function sourceShort(source: string): string {
  const map: Record<string, string> = {
    BATCH: '批量',
    DEBUG: '调试',
    RETRY: '重试',
    RESUME: '续跑',
    SANDBOX: '沙盒',
  }
  return map[source] ?? source
}

function formatToken(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}

function formatShortDateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (input: number) => String(input).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function relativeTime(value?: string | null): string {
  if (!value) return '—'
  const at = Date.parse(value)
  if (Number.isNaN(at)) return value
  const diff = Math.max(0, nowMs.value - at)
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return formatShortDateTime(value)
}

function minutesLabel(minutes: number): string {
  if (minutes < 1) return `${Math.max(1, Math.round(minutes * 60))}s`
  if (minutes < 60) {
    return `${Math.floor(minutes)}m${String(Math.round(minutes % 60)).padStart(2, '0')}s`
  }
  return `${Math.floor(minutes / 60)}h ${Math.round(minutes % 60)}m`
}

function taskFailures(task: BatchTask): number {
  return Math.max(0, task.finished_cases - task.pass_count)
}

function taskPassValue(task: BatchTask): number | null {
  if (!task.finished_cases) return null
  return (task.pass_count / task.finished_cases) * 100
}

function taskCasesLabel(task: BatchTask): string {
  if (task.finished_cases && task.finished_cases < task.total_cases) {
    return `${task.finished_cases} / ${task.total_cases}`
  }
  return String(task.total_cases)
}

function taskDuration(task: BatchTask): string {
  const started = task.started_at ? Date.parse(task.started_at) : NaN
  const finished = task.finished_at ? Date.parse(task.finished_at) : nowMs.value
  if (Number.isNaN(started) || Number.isNaN(finished)) return '—'
  const minutes = Math.max(0, (finished - started) / 60_000)
  return minutesLabel(minutes)
}

function taskSub(task: BatchTask): string {
  const round = roundLabel(task)
  if (task.status === 'SCHEDULED') {
    return `${task.code}${round ? ` · ${round}` : ' · 计划任务'}`
  }
  if (task.status === 'PENDING') return `${task.code} · 排队中`
  return `${task.code}${round ? ` · ${round}` : ''}`
}

function taskEta(task: BatchTask): { label: string; detail: string; bad: boolean } {
  if (task.status === 'SCHEDULED') {
    return {
      label: task.next_run_at ? formatShortDateTime(task.next_run_at) : '待触发',
      detail: '下次触发',
      bad: false,
    }
  }
  if (task.status === 'PENDING') {
    return {
      label: '等待资源',
      detail: `并发 ${task.concurrency} · 排队中`,
      bad: false,
    }
  }
  const started = task.started_at ? Date.parse(task.started_at) : NaN
  if (Number.isNaN(started)) {
    return { label: '执行中', detail: `并发 ${task.concurrency}`, bad: false }
  }
  const elapsed = Math.max(0.05, (nowMs.value - started) / 60_000)
  const rate = task.finished_cases / elapsed
  const remaining = rate > 0 ? (task.total_cases - task.finished_cases) / rate : null
  return {
    label: remaining !== null && remaining > 0 ? `剩余 ${minutesLabel(remaining)}` : '收尾中',
    detail: `${rate.toFixed(1)} 条/分 · 并发 ${task.concurrency}`,
    bad: Boolean(task.fail_count || task.error_count),
  }
}

function taskTrack(task: BatchTask): Array<{ cls: string; width: number }> {
  const total = Math.max(1, task.total_cases)
  if (task.status !== 'RUNNING') return [{ cls: 'bg-slate-200', width: 100 }]
  const pass = Math.max(0, task.pass_count)
  const fail = Math.max(0, task.fail_count)
  const error = Math.max(0, task.error_count)
  const rest = Math.max(0, total - pass - fail - error)
  return [
    { cls: 'bg-emerald-500', width: (pass / total) * 100 },
    { cls: 'bg-rose-500', width: (fail / total) * 100 },
    { cls: 'bg-amber-500', width: (error / total) * 100 },
    { cls: 'bg-slate-200', width: (rest / total) * 100 },
  ].filter((segment) => segment.width > 0)
}

function taskPct(task: BatchTask): string | null {
  if (task.status !== 'RUNNING' || !task.total_cases) return null
  return `${Math.round((task.finished_cases / task.total_cases) * 100)}%`
}

function taskCountText(task: BatchTask): string {
  if (task.status === 'PENDING') {
    return `排队 · ${task.total_cases} 条用例 · 等待并发位`
  }
  if (task.status === 'SCHEDULED') {
    return `${task.total_cases} 条用例 · ${task.environment_name ?? '按用例绑定环境'}`
  }
  const rest = Math.max(0, task.total_cases - task.finished_cases)
  return `通过 ${task.pass_count} · 失败 ${task.fail_count} · 异常 ${task.error_count} · 待执行 ${rest}`
}

function runDot(item: ExecutionListItem): string {
  if (item.pass_flag === true) return 'bg-emerald-500'
  if (item.status === 'ERROR' || item.status === 'MAX_TURNS_EXCEEDED') return 'bg-amber-500'
  if (item.pass_flag === false) return 'bg-rose-500'
  return 'bg-slate-300'
}

function runSubtitle(item: ExecutionListItem): string {
  const reason =
    item.pass_flag === true
      ? '通过'
      : (REASON_META[item.status]?.label ?? statusLabel(item.status))
  const retry = item.attempt_no > 0 ? ` · 重试 ${item.attempt_no} 次` : ''
  return `${sourceLabel(item.source)} · ${reason}${retry}`
}

function scoreTone(item: ExecutionListItem): string {
  if (item.total_score === null) return 'text-slate-300'
  return item.pass_flag === false ? 'text-rose-600' : 'text-slate-600'
}

function bandPctTone(task: BatchTask): string {
  if (task.status !== 'RUNNING') return 'text-slate-400'
  return 'text-ink'
}

function idleText(): string {
  const last = endedTasks.value[0]
  if (!last) return '最近还没有执行记录，先运行一次批量任务或单条调试'
  const pass = taskPassValue(last)
  const next = scheduledTasks.value[0]
  const nextText = next?.next_run_at
    ? `下次计划任务 ${formatShortDateTime(next.next_run_at)}`
    : '暂无计划任务'
  return `最近一次 ${stripRoundSuffix(last.name)} ${formatShortDateTime(last.finished_at)} 完成 · 通过率 ${
    pass === null ? '—' : formatPercent(pass, 1)
  } · ${nextText}`
}

function reportError(exc: unknown): void {
  toast.error(errorMessage(exc))
}

async function load(silent = false, dynamicOnly = false): Promise<boolean> {
  if (silent && loadInFlight) return true
  lastLoadAt = Date.now()
  if (!silent) loading.value = true
  // 字典板块的兜底判定：定时刷新（dynamicOnly）默认跳过，但距上次全量
  // 拉取超过 5 分钟时仍执行，避免环境/数据源探活告警在长挂机时永不更新。
  const refreshDicts = !dynamicOnly || Date.now() - lastDictLoadAt > DICT_REFRESH_MS
  if (refreshDicts) lastDictLoadAt = Date.now()
  loadInFlight = true
  try {
    // 按当前用户权限裁剪请求：无权限的板块直接不请求（避免 403 刷屏）；
    // 各板块独立容错，单个接口失败不再拖垮整个大盘。
    // dynamicOnly=true 时跳过字典类板块（环境/数据源/能力列表）：
    // 这些是准静态数据，20s 定时轮询不需要反复全量拉取。
    const jobs: Array<Promise<void>> = []
    const guard = (
      perm: string,
      apply: () => Promise<void>,
      options: { skipOnAutoRefresh?: boolean } = {},
    ): void => {
      if (!auth.can(perm)) return
      if (options.skipOnAutoRefresh && !refreshDicts) return
      jobs.push(apply())
    }

    guard('case:read', async () => {
      const [allCases, activeCases] = await Promise.all([
        api.cases.list({ page: 1, page_size: 1 }),
        api.cases.list({ page: 1, page_size: 1, status: 'ACTIVE' }),
      ])
      caseTotal.value = allCases.total
      activeCaseTotal.value = activeCases.total
    })
    guard('task:read', async () => {
      const taskPage = await api.tasks.list({ page_size: 50, include_runs: true })
      tasks.value = taskPage.items
    })
    guard('execution:read', async () => {
      try {
        summary.value = await api.executions.summary({ hours: 24, days: 14 })
        summaryError.value = false
      } catch (exc) {
        // 聚合失败只降级本板块，横幅明确说明，不假装「运行正常」。
        summaryError.value = true
        throw exc
      }
    })
    guard('execution:read', async () => {
      const [execPage, failedPage] = await Promise.all([
        api.executions.list({ page_size: 50 }),
        api.executions.list({ pass_flag: false, hours: 24, page_size: 50 }),
      ])
      recentExecutions.value = execPage.items
      failedExecutions.value = failedPage.items
      executionTotal.value = execPage.total
    })
    guard(
      'environment:read',
      async () => {
        const envPage = await api.environments.list({ page_size: 100 })
        environments.value = envPage.items
      },
      { skipOnAutoRefresh: true },
    )
    guard('prompt:read', async () => {
      promptTotal.value = (await api.prompts.list({ page: 1, page_size: 1 })).total
    })
    guard('model:read', async () => {
      modelTotal.value = (await api.models.list({ page: 1, page_size: 1 })).total
    })
    guard('stats:read', async () => {
      opsOverview.value = await api.stats.overview()
    })
    guard('candidate:read', async () => {
      candidatePending.value = (
        await api.candidates.list({ status: 'NEW', page: 1, page_size: 1 })
      ).total
    })
    guard('caliber:read', async () => {
      caliberTotal.value = (await api.calibers.list({ page: 1, page_size: 1 })).total
    })
    guard(
      'capability:read',
      async () => {
        const [sourcePage, capabilityPage] = await Promise.all([
          api.dataSources.list({ page: 1, page_size: 100 }),
          api.capabilities.list({ page: 1, page_size: 100 }),
        ])
        dataSources.value = sourcePage.items
        sourceTotal.value = sourcePage.total
        sourceActive.value = sourcePage.items.filter((item) => item.is_active).length
        sourceProbeFailed.value = sourcePage.items.filter(
          (item) => item.is_active && item.last_probe_status && item.last_probe_status !== 'OK',
        ).length
        capabilityTotal.value = capabilityPage.total
      },
      { skipOnAutoRefresh: true },
    )
    guard('grader:run', async () => {
      const evalPage = await api.grader.evals({ page: 1, page_size: 1 })
      latestEval.value = evalPage.items[0] ?? null
    })

    const results = await Promise.allSettled(jobs)
    const failed = results.filter((item) => item.status === 'rejected')
    if (failed.length && !silent) {
      const first = failed[0]
      reportError(first.status === 'rejected' ? first.reason : null)
    }
    lastUpdated.value = new Date()
    nowMs.value = Date.now()
    return failed.length === 0
  } finally {
    loadInFlight = false
    if (!silent) loading.value = false
  }
}

async function loadEnvironmentSummary(force = false): Promise<boolean> {
  if (!canExecution.value) return true
  const key = String(envDays.value)
  if (!force && envLoadedKey === key && envSummary.value) return true
  envLoading.value = true
  try {
    envSummary.value = await api.executions.environmentSummary({ days: envDays.value })
    envError.value = false
    envLoadedKey = key
    return true
  } catch {
    // 环境对比是独立板块，失败只降级本视图，不影响质量总览。
    envError.value = true
    return false
  } finally {
    envLoading.value = false
  }
}

function switchView(mode: DashboardViewMode): void {
  viewMode.value = mode
  if (mode === 'environments') void loadEnvironmentSummary()
}

function setEnvDays(days: 7 | 30): void {
  if (envDays.value === days) return
  envDays.value = days
  void loadEnvironmentSummary(true)
}

async function refreshData(): Promise<void> {
  const results = await Promise.all([
    load(),
    viewMode.value === 'environments' ? loadEnvironmentSummary(true) : Promise.resolve(true),
  ])
  if (results.every(Boolean)) toast.success('刷新成功')
}

function openTask(task: BatchTask): void {
  void router.push({ name: 'task-detail', params: { id: String(task.id) } })
}

function openExecution(executionId: number): void {
  void router.push({
    name: 'trace',
    query: { execution: String(executionId), from: 'dashboard' },
  })
}

function onTaskCreated(): void {
  showCreate.value = false
  toast.success('批量任务已创建')
  void load(true)
}

function onVisibilityChange(): void {
  // 回到前台：时间戳立即校正，并补一次全量刷新（含字典类板块）。
  if (document.visibilityState === 'visible') {
    nowMs.value = Date.now()
    void load(true)
    if (viewMode.value === 'environments') void loadEnvironmentSummary(true)
  }
}

onMounted(() => {
  void load()
  document.addEventListener('visibilitychange', onVisibilityChange)
  autoTimer = window.setInterval(() => {
    // 后台标签页暂停轮询（回到前台由 visibilitychange 立即补全量），
    // 避免挂机用户在不可见状态下持续产生每 20s 十几个请求。
    if (document.visibilityState !== 'visible') return
    nowMs.value = Date.now()
    void load(true, true)
    if (viewMode.value === 'environments') void loadEnvironmentSummary(true)
  }, 20_000)
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (refreshTimer !== null) window.clearTimeout(refreshTimer)
  if (autoTimer !== null) window.clearInterval(autoTimer)
})
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      eyebrow="Dashboard · 系统运行总览"
      title="测试大盘"
      description="先看结论再看过程：近 24 小时质量是否达标、失败聚集在哪，以及有哪些任务正在跑。"
    >
      <template #actions>
        <button class="btn-ghost" @click="refreshData">刷新</button>
        <button class="btn-primary" @click="showCreate = true">＋ 新建批量任务</button>
      </template>
    </PageHeader>

    <!-- 视图切换：质量总览 / 按执行环境对比 -->
    <div
      v-if="canExecution"
      class="flex flex-wrap items-center gap-1 rounded-tile border border-line bg-slate-100/70 p-1"
    >
      <button
        class="tab-btn"
        :class="viewMode === 'overview' ? 'tab-btn-active' : ''"
        @click="switchView('overview')"
      >
        质量总览
      </button>
      <button
        class="tab-btn"
        :class="viewMode === 'environments' ? 'tab-btn-active' : ''"
        @click="switchView('environments')"
      >
        环境对比
      </button>
      <span class="ml-auto px-2 text-[11px] text-slate-600">
        {{
          viewMode === 'environments'
            ? `近 ${envDays} 天 · ${envWithData.length}/${envMetrics.length} 个环境有数据`
            : '近 24 小时 · 先结论后证据'
        }}
      </span>
    </div>

    <template v-if="viewMode === 'overview'">
    <!-- ① 系统状态横幅 -->
    <section
      class="flex flex-wrap items-center gap-3 rounded-2xl border px-4 py-3"
      :class="
        bannerError
          ? 'border-rose-200 bg-rose-50 text-rose-900'
          : bannerIdle
            ? 'border-slate-200 bg-slate-50 text-slate-700'
            : bannerOk
              ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
              : 'border-amber-200 bg-amber-50 text-amber-900'
      "
    >
      <span
        class="h-2.5 w-2.5 flex-none rounded-full"
        :class="
          bannerError
            ? 'bg-rose-500 ring-4 ring-rose-500/15'
            : bannerIdle
              ? 'bg-slate-400 ring-4 ring-slate-400/15'
              : bannerOk
                ? 'bg-emerald-500 ring-4 ring-emerald-500/15'
                : 'bg-amber-500 ring-4 ring-amber-500/15'
        "
      />
      <div class="min-w-0">
        <p class="text-[12.5px] font-semibold">
          {{
            bannerError
              ? '执行数据加载失败'
              : bannerIdle
                ? '尚无执行数据'
                : bannerOk
                  ? '系统运行正常'
                  : `需要关注 · ${bannerItems.length} 项告警`
          }}
        </p>
        <p class="mt-0.5 text-[11px] opacity-85">{{ bannerDetail }}</p>
      </div>
      <p class="ml-auto whitespace-nowrap text-[10.5px] opacity-70">
        实时推送 · 更新于 {{ lastUpdated ? lastUpdated.toLocaleTimeString('zh-CN', { hour12: false }) : '—' }}
      </p>
    </section>

    <!-- ② 核心指标：通过率做主角 -->
    <div
      v-if="canExecution"
      class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-[1.42fr_1fr_1fr_1fr]"
    >
      <div class="rounded-2xl border border-line bg-gradient-to-b from-white to-[#fbfbff] px-4 py-3.5 shadow-tile">
        <div class="flex items-center justify-between gap-2 text-[9.5px] uppercase tracking-[0.07em] text-ink-muted">
          <span>近 24h 通过率</span>
          <span
            v-if="passDelta !== null"
            class="shrink-0 whitespace-nowrap rounded-full border px-1.5 py-px text-[9.5px] font-semibold normal-case tracking-normal"
            :class="
              passDelta >= 0
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                : 'border-rose-200 bg-rose-50 text-rose-700'
            "
          >
            {{ passDelta >= 0 ? '↑' : '↓' }} {{ Math.abs(passDelta).toFixed(1) }}pp
          </span>
        </div>
        <div class="mt-1.5 flex items-end justify-between gap-3">
          <p
            class="flex items-baseline gap-1.5 text-[30px] font-semibold leading-none tracking-[-0.025em] tabular-nums"
            :class="belowTarget ? 'text-rose-700' : 'text-ink'"
          >
            {{ passRate === null ? '—' : passRate.toFixed(1) }}
            <small class="text-xs font-medium text-ink-muted">%</small>
          </p>
          <svg v-if="spark.visible" class="mb-1 h-[26px] w-[78px] flex-none" viewBox="0 0 76 26" aria-hidden="true">
            <polyline
              :points="spark.points"
              fill="none"
              :stroke="belowTarget ? '#e11d48' : '#4f46e5'"
              stroke-width="1.6"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
            <circle :cx="spark.lastX" :cy="spark.lastY" r="2.1" :fill="belowTarget ? '#e11d48' : '#4f46e5'" />
          </svg>
        </div>
        <p class="mt-1 text-[10px] leading-relaxed text-ink-faint">
          已判定 {{ measured }} 次 · 目标 95%，
          <span v-if="targetGap !== null">{{ targetGap > 0 ? `还差 ${targetGap.toFixed(1)}pp` : '已达标' }} · </span>
          7 日均值 {{ formatPercent(sevenDayAvg, 1) }}
        </p>
        <div class="relative mt-2.5 h-1 rounded-full bg-slate-100">
          <span
            class="block h-full rounded-full"
            :class="
              belowTarget
                ? 'bg-gradient-to-r from-rose-400 to-rose-600'
                : 'bg-gradient-to-r from-indigo-500 to-brand'
            "
            :style="{ width: `${Math.min(100, passRate ?? 0)}%` }"
          />
          <i class="absolute -top-[3px] -bottom-[3px] left-[95%] w-[1.5px] rounded bg-slate-400" aria-hidden="true" />
        </div>
      </div>

      <div class="rounded-2xl border border-line bg-white px-4 py-3.5 shadow-tile">
        <p class="text-[9.5px] uppercase tracking-[0.07em] text-ink-muted">近 24h 执行量</p>
        <p class="mt-1.5 flex items-baseline gap-1.5 text-[26px] font-semibold leading-none tracking-[-0.02em] tabular-nums text-ink">
          {{ windowStats?.total ?? 0 }}<small class="text-xs font-medium text-ink-muted">次</small>
        </p>
        <p class="mt-1 text-[10px] leading-relaxed text-ink-faint">
          已判定 {{ measured }} · 失败 {{ windowFailed }} · 未判定 {{ unmeasured }}
        </p>
        <div class="mt-2.5 flex h-1 gap-px overflow-hidden rounded-full bg-slate-100">
          <i
            v-for="(segment, index) in volumeSegments"
            :key="index"
            class="block h-full"
            :class="segment.cls"
            :style="{ width: `${segment.width}%` }"
          />
        </div>
      </div>

      <div class="rounded-2xl border border-line bg-white px-4 py-3.5 shadow-tile">
        <p class="text-[9.5px] uppercase tracking-[0.07em] text-ink-muted">近 24h P95 时延</p>
        <p
          class="mt-1.5 flex items-baseline gap-1.5 text-[26px] font-semibold leading-none tracking-[-0.02em] tabular-nums"
          :class="latencyP95 !== null && latencyP95 > 5000 ? 'text-rose-700' : 'text-ink'"
        >
          {{ latencyP95 === null ? '—' : (latencyP95 / 1000).toFixed(2) }}
          <small class="text-xs font-medium text-ink-muted">s</small>
        </p>
        <p class="mt-1 text-[10px] leading-relaxed text-ink-faint">
          平均 TTFT {{ formatMs(latencyTtft) }} · 平均耗时 {{ formatMs(latencyAvg) }}
        </p>
        <div class="mt-2.5 h-1 overflow-hidden rounded-full bg-slate-100">
          <span
            class="block h-full rounded-full bg-gradient-to-r from-sky-300 to-sky-500"
            :style="{ width: `${Math.min(100, ((latencyP95 ?? 0) / 8000) * 100)}%` }"
          />
        </div>
      </div>

      <div class="rounded-2xl border border-line bg-white px-4 py-3.5 shadow-tile">
        <p class="text-[9.5px] uppercase tracking-[0.07em] text-ink-muted">近 24h Token 消耗</p>
        <p class="mt-1.5 flex items-baseline gap-1.5 text-[26px] font-semibold leading-none tracking-[-0.02em] tabular-nums text-ink">
          {{ tokenDisplay.value }}<small class="text-xs font-medium text-ink-muted">{{ tokenDisplay.unit }}</small>
        </p>
        <p class="mt-1 text-[10px] leading-relaxed text-ink-faint">
          模拟器 {{ formatToken(summary?.tokens.simulator ?? 0) }} · 裁判
          {{ formatToken(summary?.tokens.judge ?? 0) }}
        </p>
        <div class="mt-2.5 flex h-1 gap-px overflow-hidden rounded-full bg-slate-100">
          <span
            v-for="(segment, index) in tokenSplit"
            :key="index"
            class="block h-full"
            :class="segment.cls"
            :style="{ width: `${segment.width}%` }"
          />
        </div>
      </div>
    </div>

    <!-- ③ 运行中任务带 -->
    <section v-if="canTask" class="card overflow-hidden">
      <div class="flex items-center justify-between gap-3 px-4 py-2.5">
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <span class="inline-grid h-[18px] min-w-[18px] place-items-center rounded-md border border-brand-edge bg-brand-soft px-1 text-[9.5px] font-bold text-brand-deep">
            ③
          </span>
          <b class="text-[12px] font-semibold tracking-[-0.01em] text-ink">运行中</b>
          <span class="inline-flex items-center gap-1 rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[9.5px] font-semibold text-indigo-700">
            <span class="h-[5px] w-[5px] animate-pulse rounded-full bg-indigo-600" />
            实时
          </span>
          <span class="text-[10px] text-ink-faint">{{ bandMeta }}</span>
        </div>
        <RouterLink
          :to="{ name: 'tasks' }"
          class="whitespace-nowrap text-[10.5px] font-medium text-brand transition hover:text-brand-deep"
        >
          全部任务 →
        </RouterLink>
      </div>

      <div v-if="bandTasks.length" class="border-t border-line-soft px-4 pb-1">
        <button
          v-for="task in bandTasks"
          :key="task.id"
          type="button"
          class="grid w-full grid-cols-[minmax(0,1.55fr)_minmax(64px,1fr)_42px_minmax(0,1.3fr)_96px] items-center gap-3.5 border-t border-dashed border-line-soft py-2 text-left first:border-t-0 max-[1240px]:grid-cols-[minmax(0,1.5fr)_minmax(56px,1fr)_40px_86px] max-[900px]:grid-cols-[minmax(0,1fr)_76px_40px]"
          @click="openTask(task)"
        >
          <span class="min-w-0">
            <span class="flex items-center gap-1.5 text-[10.5px] font-semibold leading-snug text-ink-soft">
              <i
                class="h-1.5 w-1.5 flex-none rounded-full"
                :class="
                  task.status === 'RUNNING'
                    ? 'bg-indigo-500 ring-[3px] ring-indigo-50'
                    : task.status === 'PENDING'
                      ? 'bg-slate-300 ring-[3px] ring-slate-100'
                      : 'bg-slate-200 ring-[3px] ring-slate-50'
                "
              />
              <span class="truncate">{{ stripRoundSuffix(task.name) }}</span>
            </span>
            <span class="ml-3 block truncate font-mono text-[9px] text-ink-faint">{{ taskSub(task) }}</span>
          </span>

          <span class="flex h-1.5 overflow-hidden rounded-full bg-slate-100">
            <span
              v-for="(segment, index) in taskTrack(task)"
              :key="index"
              class="h-full"
              :class="segment.cls"
              :style="{ width: `${segment.width}%` }"
            />
          </span>

          <span
            class="text-right text-[12.5px] font-bold tracking-[-0.02em] tabular-nums"
            :class="bandPctTone(task)"
          >
            {{ taskPct(task) ?? '—' }}
          </span>

          <span class="truncate text-[9.5px] tabular-nums text-ink-faint max-[1240px]:hidden">
            {{ taskCountText(task) }}
          </span>

          <span class="text-right text-[9px] text-ink-faint max-[900px]:hidden">
            <b
              class="mb-px block text-[10.5px] font-semibold tabular-nums"
              :class="taskEta(task).bad ? 'text-rose-700' : 'text-ink-soft'"
            >
              {{ taskEta(task).label }}
            </b>
            {{ taskEta(task).detail }}
          </span>
        </button>
      </div>

      <div
        v-else
        class="flex flex-wrap items-center gap-2.5 border-t border-line-soft px-4 py-3 text-[10.5px] text-ink-muted"
      >
        <b class="text-[11.5px] font-semibold text-ink-soft">系统空闲</b>
        <span class="min-w-0">{{ idleText() }}</span>
        <button class="btn-primary ml-auto !px-2.5 !py-1.5 !text-[10.5px]" @click="showCreate = true">
          ＋ 新建批量任务
        </button>
      </div>
    </section>

    <!-- 主体：主列 + 右栏运营三卡 -->
    <div
      class="grid items-stretch gap-4"
      :class="canExecution ? 'xl:grid-cols-[minmax(0,1fr)_340px]' : 'xl:grid-cols-1'"
    >
      <div v-if="canExecution" class="flex min-w-0 flex-col gap-4">
        <!-- ④ 通过率趋势 -->
        <section class="card">
          <header class="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
            <div class="flex min-w-0 items-start gap-2">
              <span class="mt-0.5 inline-grid h-[18px] min-w-[18px] place-items-center rounded-md border border-brand-edge bg-brand-soft px-1 text-[9.5px] font-bold text-brand-deep">
                ④
              </span>
              <div class="min-w-0">
                <h2 class="panel-title">通过率趋势</h2>
                <p class="mt-0.5 text-[10.5px] text-ink-muted">
                  近 {{ trendDays }} 天每日已判定执行通过率与执行量（不含草稿试跑）
                </p>
              </div>
            </div>
            <div class="inline-flex flex-none gap-0.5 rounded-lg bg-slate-100 p-[2px]">
              <button
                class="rounded-md px-2.5 py-1 text-[10px] transition"
                :class="trendDays === 7 ? 'bg-white font-semibold text-brand-deep shadow-sm' : 'text-slate-500'"
                @click="trendDays = 7"
              >
                近 7 天
              </button>
              <button
                class="rounded-md px-2.5 py-1 text-[10px] transition"
                :class="trendDays === 14 ? 'bg-white font-semibold text-brand-deep shadow-sm' : 'text-slate-500'"
                @click="trendDays = 14"
              >
                近 14 天
              </button>
            </div>
          </header>
          <div class="px-4 py-3.5">
            <div class="flex flex-wrap items-center justify-between gap-2 pb-1.5">
              <div class="flex flex-wrap items-center gap-3.5 text-[10px] text-ink-muted">
                <span class="inline-flex items-center gap-1.5">
                  <i class="h-0.5 w-4 rounded-full bg-brand" />通过率
                </span>
                <span class="inline-flex items-center gap-1.5">
                  <i class="h-2.5 w-2.5 rounded-[3px] border border-indigo-200 bg-indigo-100" />执行量
                </span>
                <span
                  v-if="belowTarget"
                  class="font-medium text-rose-700"
                >
                  近 24h 低于目标 95%
                </span>
                <span v-else class="text-ink-faint">近 {{ trendDays }} 天日均 {{ avgVolume }} 次</span>
              </div>
              <span class="text-[9.5px] text-ink-faint">悬停查看每日明细</span>
            </div>

            <DashboardTrendChart v-if="trendPoints.length" :points="trendPoints" />
            <p v-else class="py-10 text-center text-[11px] text-ink-faint">近 {{ trendDays }} 天暂无执行数据</p>

            <div class="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 border-t border-line-soft pt-2.5 text-[9.5px] text-ink-faint">
              <span>近 {{ trendDays }} 天均值<b class="ml-1.5 font-semibold tabular-nums text-ink-soft">{{ formatPercent(trendAvg, 1) }}</b></span>
              <span v-if="trendDelta !== null">
                较上一周期
                <b
                  class="ml-1.5 font-semibold tabular-nums"
                  :class="trendDelta >= 0 ? 'text-emerald-700' : 'text-rose-700'"
                >
                  {{ trendDelta >= 0 ? '+' : '' }}{{ trendDelta.toFixed(1) }}pp
                </b>
              </span>
              <span>单日执行量最高<b class="ml-1.5 font-semibold tabular-nums text-ink-soft">{{ maxVolumeDay }} 次</b></span>
            </div>
          </div>
        </section>

        <!-- ⑤ 失败聚焦 -->
        <section class="card flex flex-1 flex-col">
          <header class="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
            <div class="flex min-w-0 items-start gap-2">
              <span class="mt-0.5 inline-grid h-[18px] min-w-[18px] place-items-center rounded-md border border-brand-edge bg-brand-soft px-1 text-[9.5px] font-bold text-brand-deep">
                ⑤
              </span>
              <div class="min-w-0">
                <h2 class="panel-title">
                  失败聚焦
                  <span
                    class="ml-1.5 text-[10px] font-medium"
                    :class="failureTotal ? 'text-rose-700' : 'text-ink-faint'"
                  >
                    近 24 小时{{ failureTotal ? ' · 需关注' : '' }}
                  </span>
                </h2>
                <p class="mt-0.5 text-[10.5px] text-ink-muted">失败原因分布与最需要复盘的用例、事实</p>
              </div>
            </div>
            <RouterLink
              :to="{ name: 'trace' }"
              class="whitespace-nowrap text-[10.5px] font-medium text-brand transition hover:text-brand-deep"
            >
              全部执行记录 →
            </RouterLink>
          </header>
          <div class="flex flex-1 flex-col px-4 py-3.5">
            <div class="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_1.05fr]">
              <div>
                <div class="mb-2 flex items-center justify-between text-[10.5px] font-semibold text-ink-soft">
                  失败原因分布
                  <span class="text-[9.5px] font-normal text-ink-faint">共 {{ failureTotal }} 次</span>
                </div>
                <div class="flex h-2.5 overflow-hidden rounded-full bg-slate-100">
                  <span
                    v-for="reason in failureReasons"
                    :key="reason.status"
                    class="h-full"
                    :style="{ width: `${reason.width}%`, background: reason.color }"
                    :title="`${reason.label}: ${reason.count}`"
                  />
                </div>
                <div class="mt-3 grid gap-1.5 text-[10px] text-slate-600">
                  <span
                    v-for="reason in failureReasons"
                    :key="`legend-${reason.status}`"
                    class="flex items-center gap-1.5"
                  >
                    <i class="h-1.5 w-1.5 flex-none rounded-full" :style="{ background: reason.color }" />
                    {{ reason.label }}
                    <i class="mx-0.5 flex-1 border-b border-dotted border-slate-200" />
                    <b class="font-semibold tabular-nums text-ink-soft">{{ reason.count }}</b>
                  </span>
                  <span v-if="!failureReasons.length" class="text-[10px] text-ink-faint">
                    近 24 小时没有失败执行
                  </span>
                </div>
                <div
                  v-if="failureAdvice"
                  class="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[10px] leading-relaxed text-amber-800"
                >
                  <b class="font-semibold">建议：</b>{{ failureAdvice }}
                </div>
              </div>

              <div>
                <div class="mb-2 flex items-center justify-between text-[10.5px] font-semibold text-ink-soft">
                  Top 失败用例
                  <span class="text-[9.5px] font-normal text-ink-faint">按失败次数排序</span>
                </div>
                <div
                  v-for="(item, index) in topFailedCases"
                  :key="item.case_id"
                  class="flex items-center gap-2.5 border-b border-dashed border-line-soft py-1.5 last:border-b-0"
                >
                  <span class="grid h-4 w-4 flex-none place-items-center rounded-[5px] bg-slate-100 text-[9px] font-bold text-slate-500">
                    {{ index + 1 }}
                  </span>
                  <span class="min-w-0 flex-1">
                    <b class="block truncate text-[10.5px] font-semibold leading-snug text-ink-soft">
                      {{ item.case_code || '—' }} · {{ item.case_name || '未命名用例' }}
                    </b>
                    <span class="block truncate text-[9px] text-ink-faint">
                      最近失败 {{ formatShortDateTime(item.last_failed_at) }} · {{ statusLabel(item.last_status) }}
                    </span>
                  </span>
                  <span class="flex-none whitespace-nowrap rounded-full border border-rose-200 bg-rose-50 px-1.5 py-px text-[9.5px] font-semibold text-rose-700">
                    {{ item.failures }} 次
                  </span>
                </div>
                <p v-if="!topFailedCases.length" class="py-6 text-center text-[10px] text-ink-faint">
                  近 24 小时没有失败用例
                </p>
              </div>
            </div>

            <div
              v-if="failedFacts.length"
              class="mt-auto flex flex-wrap items-center gap-1.5 border-t border-line-soft pt-2.5"
            >
              <span class="py-0.5 text-[9.5px] text-ink-faint">Top 失败事实</span>
              <span
                v-for="fact in failedFacts"
                :key="fact.fact_id"
                class="whitespace-nowrap rounded-md border border-line bg-[#fbfcfe] px-1.5 py-0.5 text-[9.5px] text-slate-600"
              >
                {{ fact.label || fact.fact_id }}
                <b :class="fact.pass_rate < 90 ? 'text-rose-700' : 'text-emerald-700'">
                  {{ formatPercent(fact.pass_rate, 1) }}
                </b>
              </span>
            </div>
          </div>
        </section>
      </div>

      <!-- 右栏 · 运营三卡 -->
      <aside
        v-if="canExecution || canEnvironment || canOpsCard"
        class="flex min-w-0 flex-col gap-4 max-[1240px]:grid max-[1240px]:grid-cols-2 max-[900px]:!grid-cols-1"
        :class="canExecution ? 'xl:col-start-2' : ''"
      >
        <section v-if="canExecution" class="card flex flex-col">
          <header class="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
            <div class="min-w-0">
              <h2 class="panel-title">执行来源通过率</h2>
              <p class="mt-0.5 text-[10px]" :class="belowTarget ? 'text-rose-700' : 'text-ink-muted'">
                近 24h · 按 Execution.source，含样本数
              </p>
            </div>
            <RouterLink
              :to="{ name: 'trace' }"
              class="whitespace-nowrap text-[10.5px] font-medium text-brand transition hover:text-brand-deep"
            >
              全部记录 →
            </RouterLink>
          </header>
          <div class="flex flex-1 flex-col px-4 py-3">
            <div
              v-for="row in sourceRows"
              :key="row.source"
              class="grid grid-cols-[52px_minmax(0,1fr)_44px_34px] items-center gap-2 border-t border-line-soft py-2 text-[10.5px] text-slate-600 first:border-t-0 first:pt-0"
            >
              <span>{{ row.label }}</span>
              <span class="h-1.5 overflow-hidden rounded-full bg-slate-100">
                <i
                  class="block h-full rounded-full"
                  :class="row.low ? 'bg-rose-400' : 'bg-indigo-500'"
                  :style="{ width: `${Math.min(100, row.pass_rate ?? 0)}%` }"
                />
              </span>
              <b
                class="text-right font-semibold tabular-nums"
                :class="row.low ? 'text-rose-700' : 'text-ink-soft'"
              >
                {{ formatPercent(row.pass_rate, 1) }}
              </b>
              <span class="text-right text-[9px] tabular-nums text-ink-faint">{{ row.total }}</span>
            </div>
            <p v-if="!sourceRows.length" class="py-4 text-center text-[10px] text-ink-faint">
              近 24 小时暂无执行
            </p>
            <div class="mt-auto flex justify-between gap-2 border-t border-line-soft pt-2 text-[9.5px] text-ink-faint">
              <span>整体 {{ formatPercent(passRate, 1) }}</span>
              <span>未判定 {{ unmeasured }} 次</span>
            </div>
          </div>
        </section>

        <section v-if="canEnvironment || canCapability" class="card flex flex-col">
          <header class="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
            <div class="min-w-0">
              <h2 class="panel-title">环境与数据源</h2>
              <p
                class="mt-0.5 text-[10px]"
                :class="envAlerts.length || sourceAlerts.length ? 'text-rose-700' : 'text-ink-muted'"
              >
                {{ envHealthText }}
              </p>
            </div>
            <RouterLink
              to="/settings"
              class="whitespace-nowrap text-[10.5px] font-medium text-brand transition hover:text-brand-deep"
            >
              管理
            </RouterLink>
          </header>
          <div class="flex flex-1 flex-col px-4 py-3">
            <div v-if="canEnvironment" class="grid grid-cols-2 gap-2">
              <div
                v-for="tile in envTiles"
                :key="tile.name"
                class="min-w-0 rounded-[10px] border px-2.5 py-2"
                :class="tile.bad ? 'border-rose-200 bg-rose-50' : 'border-line-soft bg-[#fbfcff]'"
              >
                <b
                  class="block truncate text-[10px] font-semibold leading-snug"
                  :class="tile.bad ? 'text-rose-700' : 'text-ink-soft'"
                >
                  {{ tile.name }}
                </b>
                <span class="mt-0.5 flex items-center justify-between gap-1.5">
                  <span
                    class="text-[9.5px] tabular-nums"
                    :class="tile.bad ? 'text-rose-600' : 'text-ink-faint'"
                  >
                    {{ tile.metric }}
                  </span>
                  <i
                    class="h-1.5 w-1.5 flex-none rounded-full"
                    :class="tile.dot === 'ok' ? 'bg-emerald-500 ring-[3px] ring-emerald-50' : 'bg-rose-500 ring-[3px] ring-rose-50'"
                  />
                </span>
              </div>
              <p v-if="!envTiles.length" class="col-span-2 py-3 text-center text-[10px] text-ink-faint">
                暂无启用的执行环境
              </p>
            </div>
            <p
              v-if="canCapability"
              class="mt-2 border-t border-line-soft pt-2 text-[9.5px] leading-relaxed text-ink-faint"
            >
              数据源 {{ sourceActive }}/{{ sourceTotal }} 启用 · {{ sourceLine }}
            </p>
          </div>
        </section>

        <section v-if="canOpsCard" class="card flex flex-col">
          <header class="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
            <div class="min-w-0">
              <h2 class="panel-title">运营待办</h2>
              <p
                class="mt-0.5 text-[10px]"
                :class="windowFailed || candidatePending || sourceProbeFailed ? 'text-rose-700' : 'text-ink-muted'"
              >
                {{
                  windowFailed || candidatePending || sourceProbeFailed
                    ? '需要跟进的异常与治理事项'
                    : '当前没有需要立刻处理的事项'
                }}
              </p>
            </div>
            <RouterLink
              to="/ops"
              class="whitespace-nowrap text-[10.5px] font-medium text-brand transition hover:text-brand-deep"
            >
              运营后台
            </RouterLink>
          </header>
          <div class="flex flex-1 flex-col px-4 py-3">
            <div
              v-if="canExecution && windowFailed"
              class="flex items-center justify-between gap-2.5 border-t border-line-soft py-2 text-[10.5px] text-slate-600 first:border-t-0 first:pt-0"
            >
              <span>失败用例待归因</span>
              <span class="whitespace-nowrap rounded-full border border-rose-200 bg-rose-50 px-1.5 py-px text-[9.5px] font-semibold text-rose-700">
                {{ windowFailed }} 次
              </span>
            </div>
            <RouterLink
              v-if="canCandidate"
              to="/ops?tab=candidates"
              class="flex items-center justify-between gap-2.5 border-t border-line-soft py-2 text-[10.5px] text-slate-600 transition hover:text-ink-soft"
            >
              <span>待分诊候选</span>
              <span
                class="whitespace-nowrap rounded-full border px-1.5 py-px text-[9.5px] font-semibold"
                :class="candidatePending ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-slate-200 bg-slate-50 text-slate-500'"
              >
                {{ candidatePending }} 条
              </span>
            </RouterLink>
            <RouterLink
              v-if="canCapability"
              to="/ops?tab=sources"
              class="flex items-center justify-between gap-2.5 border-t border-line-soft py-2 text-[10.5px] text-slate-600 transition hover:text-ink-soft"
            >
              <span>数据源探活异常</span>
              <span
                class="whitespace-nowrap rounded-full border px-1.5 py-px text-[9.5px] font-semibold"
                :class="sourceProbeFailed ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'"
              >
                {{ sourceProbeFailed }} 个
              </span>
            </RouterLink>
            <RouterLink
              v-if="canGrader"
              to="/ops?tab=grader"
              class="flex items-center justify-between gap-2.5 border-t border-line-soft py-2 text-[10.5px] text-slate-600 transition hover:text-ink-soft"
            >
              <span>判分器自检</span>
              <span
                v-if="latestEval"
                class="whitespace-nowrap rounded-full border px-1.5 py-px text-[9.5px] font-semibold"
                :class="
                  latestEval.false_kills === 0 && latestEval.detected === latestEval.total
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                    : 'border-rose-200 bg-rose-50 text-rose-700'
                "
              >
                识别 {{ latestEval.detected }}/{{ latestEval.total }} · 误杀 {{ latestEval.false_kills }}
              </span>
              <span v-else class="text-[9.5px] text-ink-faint">尚未运行</span>
            </RouterLink>
            <RouterLink
              v-if="canStats"
              to="/ops?tab=stats"
              class="flex items-center justify-between gap-2.5 border-t border-line-soft py-2 text-[10.5px] text-slate-600 transition hover:text-ink-soft"
            >
              <span>幸运通过</span>
              <span
                class="whitespace-nowrap rounded-full border px-1.5 py-px text-[9.5px] font-semibold"
                :class="opsOverview?.lucky_pass ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-slate-200 bg-slate-50 text-slate-500'"
              >
                {{ opsOverview?.lucky_pass ?? 0 }} 次
              </span>
            </RouterLink>

            <div class="-mx-4 -mb-3 mt-auto flex flex-wrap items-center gap-x-3.5 gap-y-1 rounded-b-2xl border-t border-line-soft bg-[#fbfcfe] px-4 py-2 text-[9px] text-ink-faint">
              <span><b class="font-semibold text-slate-500">资产</b> 用例 {{ caseTotal }} · 启用 {{ activeCaseTotal }}</span>
              <span>Prompt {{ promptTotal }} · 模型 {{ modelTotal }}</span>
              <span>口径 {{ caliberTotal }} · 取数 {{ capabilityTotal }}</span>
              <span class="ml-auto">20s 刷新</span>
            </div>
          </div>
        </section>
      </aside>
    </div>

    <!-- ⑥ 已结束任务 -->
      <section v-if="canTask" class="card overflow-hidden">
        <header class="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div class="flex min-w-0 items-start gap-2">
            <span class="mt-0.5 inline-grid h-[18px] min-w-[18px] place-items-center rounded-md border border-brand-edge bg-brand-soft px-1 text-[9.5px] font-bold text-brand-deep">
              ⑥
            </span>
            <div class="min-w-0">
              <h2 class="panel-title">已结束任务</h2>
              <p class="mt-0.5 text-[10.5px] text-ink-muted">
                只列终态任务 · 执行中的任务见上方「运行中」，点击行查看逐用例结果
              </p>
            </div>
          </div>
          <RouterLink
            :to="{ name: 'tasks' }"
            class="whitespace-nowrap text-[10.5px] font-medium text-brand transition hover:text-brand-deep"
          >
            任务历史 →
          </RouterLink>
        </header>
        <div class="overflow-x-auto">
          <table class="table-shell">
            <thead>
              <tr>
                <th class="th">任务</th>
                <th class="th w-[104px] max-[1240px]:!w-auto">状态</th>
                <th class="th w-[68px] max-[1240px]:!w-auto max-[900px]:hidden">失败</th>
                <th class="th w-[90px] max-[1240px]:!w-auto">通过率</th>
                <th class="th w-[76px] max-[1240px]:!w-auto max-[900px]:hidden">用例</th>
                <th class="th w-[88px] max-[1240px]:!w-auto max-[900px]:hidden">耗时</th>
                <th class="th w-[116px]">结束时间</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="task in endedTasks"
                :key="task.id"
                class="cursor-pointer"
                @click="openTask(task)"
              >
                <td class="td">
                  <p class="text-[11.5px] font-semibold leading-snug text-ink-soft">
                    {{ stripRoundSuffix(task.name) }}
                  </p>
                  <p class="mt-0.5 font-mono text-[9.5px] text-ink-faint">{{ task.code }}</p>
                </td>
                <td class="td"><StatusPill :status="task.status" size="sm" /></td>
                <td class="td text-[11px] tabular-nums text-rose-600 max-[900px]:hidden">{{ taskFailures(task) }}</td>
                <td class="td">
                  <span
                    class="text-[11px] tabular-nums"
                    :class="(taskPassValue(task) ?? 100) < 90 ? 'font-semibold text-rose-700' : 'text-ink-soft'"
                  >
                    {{ formatPercent(taskPassValue(task), 1) }}
                  </span>
                </td>
                <td class="td text-[11px] tabular-nums text-slate-600 max-[900px]:hidden">{{ taskCasesLabel(task) }}</td>
                <td class="td text-[11px] tabular-nums text-slate-600 max-[900px]:hidden">{{ taskDuration(task) }}</td>
                <td class="td text-[11px] text-slate-500">{{ formatShortDateTime(task.finished_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="!loading && !endedTasks.length" class="px-4 py-6 text-center text-[11px] text-ink-faint">
            还没有已结束的任务，运行一次回归后这里会显示终态结果。
          </p>
        </div>
      </section>

      <!-- ⑦ 最近执行用例 -->
      <section v-if="canExecution" class="card overflow-hidden">
        <header class="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div class="min-w-0">
            <h2 class="panel-title">最近执行用例</h2>
            <p class="mt-0.5 text-[10.5px] text-ink-muted">
              {{
                runFilter === 'fail'
                  ? `仅失败 ${recentRunList.length} 条 · 近 24h 共 ${windowFailed} 次失败`
                  : `最新 ${recentRunList.length} 条 · 近 24h 失败 ${windowFailed} 次 · 点击查看单笔判分明细`
              }}
            </p>
          </div>
          <div class="flex flex-none items-center gap-2.5">
            <div class="inline-flex gap-0.5 rounded-lg bg-slate-100 p-[2px]">
              <button
                class="rounded-md px-2 py-1 text-[10px] transition"
                :class="runFilter === 'all' ? 'bg-white font-semibold text-brand-deep shadow-sm' : 'text-slate-500'"
                @click="runFilter = 'all'"
              >
                全部
              </button>
              <button
                class="rounded-md px-2 py-1 text-[10px] transition"
                :class="runFilter === 'fail' ? 'bg-white font-semibold text-rose-700 shadow-sm' : 'text-slate-500'"
                @click="runFilter = 'fail'"
              >
                仅失败 {{ windowFailed }}
              </button>
            </div>
            <RouterLink
              :to="{ name: 'trace' }"
              class="whitespace-nowrap text-[10.5px] font-medium text-brand transition hover:text-brand-deep"
            >
              全部记录 →
            </RouterLink>
          </div>
        </header>

        <div class="flex items-center gap-2.5 bg-[#fbfcfe] px-4 py-1.5 text-[9px] uppercase tracking-[0.06em] text-ink-faint">
          <span class="w-1.5 flex-none" />
          <span class="flex-1">用例</span>
          <span class="w-[50px] flex-none text-right">得分</span>
          <span class="w-[52px] flex-none text-right max-[900px]:hidden">耗时</span>
          <span class="w-[62px] flex-none text-right">时间</span>
        </div>

        <button
          v-for="item in recentRunList"
          :key="item.id"
          type="button"
          class="flex w-full items-center gap-2.5 border-t border-line-soft px-4 py-2 text-left transition hover:bg-[#fafbff]"
          @click="openExecution(item.id)"
        >
          <span class="h-1.5 w-1.5 flex-none rounded-full" :class="runDot(item)" />
          <span class="min-w-0 flex-1">
            <span class="block truncate text-[11px] font-semibold leading-snug text-ink-soft">
              <span class="mr-1 inline font-mono text-[9.5px] font-medium text-slate-400">{{ item.case_code || '—' }}</span>
              {{ item.case_name || '未命名用例' }}
            </span>
            <span class="mt-0.5 block truncate text-[9px] text-ink-faint">{{ runSubtitle(item) }}</span>
          </span>
          <span class="w-[50px] flex-none text-right text-[11px] font-semibold tabular-nums" :class="scoreTone(item)">
            {{ item.total_score === null ? '—' : formatScore(item.total_score) }}
          </span>
          <span class="w-[52px] flex-none text-right text-[9.5px] tabular-nums text-slate-400 max-[900px]:hidden">
            {{ formatMs(item.latency_ms) }}
          </span>
          <span class="w-[62px] flex-none text-right text-[9.5px] text-slate-400">
            {{ relativeTime(item.started_at) }}
          </span>
        </button>

        <p v-if="!loading && !recentRunList.length" class="border-t border-line-soft px-4 py-6 text-center text-[11px] text-ink-faint">
          {{ runFilter === 'fail' ? '近 24 小时没有失败执行' : '还没有执行记录，运行一次批量任务或单条调试后再来查看。' }}
        </p>
      </section>

    <p class="text-[10px] text-ink-faint">
      大盘每 20 秒自动刷新一次，执行进度由 WebSocket 实时推送；共 {{ executionTotal }} 次历史执行。
    </p>
    </template>

    <template v-else>
      <!-- 环境对比：先给横向结论 -->
      <section
        class="flex flex-wrap items-center gap-3 rounded-2xl border px-4 py-3"
        :class="envBanner.tone"
      >
        <span class="h-2.5 w-2.5 flex-none rounded-full" :class="envBanner.dot" />
        <div class="min-w-0">
          <p class="text-[12.5px] font-semibold">{{ envBanner.title }}</p>
          <p class="mt-0.5 text-[11px] opacity-85">{{ envBanner.detail }}</p>
        </div>
        <p class="ml-auto flex items-center gap-2 whitespace-nowrap text-[10.5px] opacity-70">
          <span v-if="envLoading" class="inline-flex items-center gap-1">
            <span class="h-2.5 w-2.5 animate-spin rounded-full border border-current border-t-transparent" />
            刷新中
          </span>
          <span v-else>口径：已排除草稿试跑</span>
        </p>
      </section>

      <section
        v-if="envLoading && !envSummary"
        class="card flex items-center justify-center gap-2 px-4 py-16 text-[11px] text-ink-muted"
      >
        <span class="h-3 w-3 animate-spin rounded-full border-2 border-slate-200 border-t-brand" />
        正在汇总各执行环境…
      </section>

      <section
        v-else-if="envError && !envSummary"
        class="card flex flex-wrap items-center justify-center gap-2 px-4 py-10 text-[11px] text-ink-muted"
      >
        环境对比加载失败，质量总览不受影响。
        <button class="btn-ghost btn-xs" @click="loadEnvironmentSummary(true)">重试</button>
      </section>

      <section v-else-if="!envMetrics.length" class="card px-4 py-12 text-center">
        <p class="text-[11.5px] font-medium text-ink-soft">还没有可对比的执行环境</p>
        <p class="mt-1 text-[10.5px] text-ink-faint">
          到「设置 · 执行环境」接入被测 Agent 后，这里会按环境汇总正确率与稳定性。
        </p>
      </section>

      <template v-else>
        <!-- 环境排行榜：一行一个环境，正确率 / 稳定性 / 效率 / 覆盖 -->
        <section class="card overflow-hidden">
          <header class="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
            <div class="min-w-0">
              <h2 class="panel-title">环境正确率与稳定性</h2>
              <p class="mt-0.5 text-[10.5px] text-ink-muted">
                同一时间窗、同一判定口径 · 按已判定样本量排序 · 波动越小越稳定
              </p>
            </div>
            <div class="inline-flex flex-none gap-0.5 rounded-lg bg-slate-100 p-[2px]">
              <button
                class="rounded-md px-2.5 py-1 text-[10px] transition"
                :class="envDays === 7 ? 'bg-white font-semibold text-brand-deep shadow-sm' : 'text-slate-500'"
                @click="setEnvDays(7)"
              >
                近 7 天
              </button>
              <button
                class="rounded-md px-2.5 py-1 text-[10px] transition"
                :class="envDays === 30 ? 'bg-white font-semibold text-brand-deep shadow-sm' : 'text-slate-500'"
                @click="setEnvDays(30)"
              >
                近 30 天
              </button>
            </div>
          </header>

          <div class="overflow-x-auto">
            <table class="table-shell">
              <thead>
                <tr>
                  <th class="th">执行环境</th>
                  <th class="th w-[224px] max-[1240px]:!w-auto">正确率</th>
                  <th class="th w-[230px] max-[1240px]:!w-auto max-[900px]:hidden">稳定性</th>
                  <th class="th w-[178px] max-[1240px]:hidden">效率</th>
                  <th class="th w-[152px] max-[900px]:hidden">用例覆盖</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="env in envMetrics" :key="env.code">
                  <td class="td">
                    <div class="flex items-start gap-2.5">
                      <span
                        class="mt-1 h-2 w-2 flex-none rounded-full"
                        :style="{ backgroundColor: envColorMap[env.code] }"
                      />
                      <div class="min-w-0">
                        <p class="flex flex-wrap items-center gap-1.5 text-[11.5px] font-semibold leading-snug text-ink-soft">
                          <span class="max-w-[260px] break-words">{{ env.name }}</span>
                          <span
                            v-if="envWithData.length > 1 && envBestPassKey === env.code"
                            class="rounded-full border border-emerald-200 bg-emerald-50 px-1.5 py-px text-[9px] font-semibold text-emerald-700"
                          >
                            正确率最高
                          </span>
                          <span
                            v-if="envWithData.length > 1 && envBestStableKey === env.code"
                            class="rounded-full border border-sky-200 bg-sky-50 px-1.5 py-px text-[9px] font-semibold text-sky-700"
                          >
                            最稳
                          </span>
                          <span
                            v-if="env.measured > 0 && env.measured < 10"
                            class="rounded-full border border-amber-200 bg-amber-50 px-1.5 py-px text-[9px] font-semibold text-amber-700"
                          >
                            样本偏少
                          </span>
                        </p>
                        <p class="mt-0.5 flex flex-wrap items-center gap-1.5 font-mono text-[9px] text-ink-faint">
                          <span>{{ env.code }}</span>
                          <span
                            class="rounded border px-1 py-px font-sans"
                            :class="envHealth(env).cls"
                          >
                            {{ envHealth(env).label }}
                          </span>
                        </p>
                        <p class="mt-0.5 text-[9px] text-ink-faint">
                          最近执行 {{ relativeTime(env.last_executed_at) }}
                        </p>
                      </div>
                    </div>
                  </td>

                  <td class="td">
                    <div class="flex items-end justify-between gap-2">
                      <div class="flex items-baseline gap-1.5">
                        <span
                          class="text-[19px] font-semibold leading-none tracking-[-0.02em] tabular-nums"
                          :class="envRateTone(env)"
                        >
                          {{ formatPercent(env.pass_rate, 1) }}
                        </span>
                        <span
                          v-if="envDelta(env) !== null"
                          class="shrink-0 whitespace-nowrap rounded-full border px-1.5 py-px text-[9px] font-semibold tabular-nums"
                          :class="
                            envDeltaValue(env) >= 0
                              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                              : 'border-rose-200 bg-rose-50 text-rose-700'
                          "
                        >
                          {{ envDeltaValue(env) >= 0 ? '↑' : '↓' }}
                          {{ Math.abs(envDeltaValue(env)).toFixed(1) }}pp
                        </span>
                      </div>
                      <svg
                        v-if="envSpark(env).visible"
                        class="mb-0.5 h-[24px] w-[70px] flex-none"
                        viewBox="0 0 76 26"
                        aria-hidden="true"
                      >
                        <polyline
                          :points="envSpark(env).points"
                          fill="none"
                          :stroke="envColorMap[env.code]"
                          stroke-width="1.6"
                          stroke-linecap="round"
                          stroke-linejoin="round"
                        />
                        <circle
                          :cx="envSpark(env).lastX"
                          :cy="envSpark(env).lastY"
                          r="2"
                          :fill="envColorMap[env.code]"
                        />
                      </svg>
                    </div>
                    <p class="mt-1 text-[9.5px] text-ink-faint">
                      已判定 {{ env.measured }} 次
                      <template v-if="env.total > env.measured"> · 未判定 {{ env.total - env.measured }}</template>
                    </p>
                  </td>

                  <td class="td max-[900px]:hidden">
                    <p class="text-[11px] text-ink-soft">
                      波动
                      <b class="ml-1 font-semibold tabular-nums" :class="volatilityTone(env.volatility)">
                        {{ env.volatility === null ? '—' : `±${env.volatility.toFixed(1)}pp` }}
                      </b>
                    </p>
                    <p class="mt-0.5 text-[9.5px] text-ink-faint">
                      <template v-if="env.case_total">
                        抖动 {{ env.flaky_cases }}/{{ env.case_total }} 用例
                        <span class="mx-1 text-slate-300">·</span>
                      </template>
                      异常 {{ env.error_total }} 次
                    </p>
                  </td>

                  <td class="td max-[1240px]:hidden">
                    <p class="text-[11px] text-ink-soft">
                      P95
                      <b class="ml-1 font-semibold tabular-nums text-ink">{{ formatMs(env.p95_ms) }}</b>
                      <span class="ml-1.5 text-ink-faint">
                        · 平均 {{ env.avg_turns === null ? '—' : env.avg_turns.toFixed(1) }} 轮
                      </span>
                    </p>
                    <p class="mt-0.5 text-[9.5px] text-ink-faint">
                      均分 {{ env.avg_score === null ? '—' : formatScore(env.avg_score) }}
                      <span class="mx-1 text-slate-300">·</span>
                      Token {{ formatToken(env.tokens) }}
                    </p>
                  </td>

                  <td class="td max-[900px]:hidden">
                    <p class="text-[11px] tabular-nums text-ink-soft">
                      {{ env.case_total }} 用例 · {{ env.measured }} 次判定
                    </p>
                    <p class="mt-0.5 text-[9.5px] text-ink-faint">
                      至少通过一次 {{ formatPercent(env.case_pass_rate, 1) }}
                    </p>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="flex flex-wrap gap-x-5 gap-y-1 border-t border-line-soft bg-[#fbfcfe] px-4 py-2 text-[9px] text-ink-faint">
            <span>正确率口径<b class="ml-1.5 font-semibold text-slate-600">已判定执行通过占比</b></span>
            <span>抖动<b class="ml-1.5 font-semibold text-slate-600">窗口内既通过又失败的用例</b></span>
            <span>异常<b class="ml-1.5 font-semibold text-slate-600">状态为 ERROR 的执行</b></span>
          </div>
        </section>

        <!-- 趋势对比 + 失败构成 -->
        <div class="grid items-stretch gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <section class="card min-w-0">
            <header class="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
              <div class="min-w-0">
                <h2 class="panel-title">每日通过率对比</h2>
                <p class="mt-0.5 text-[10.5px] text-ink-muted">
                  同一天多条线的距离就是正确率差距 · 断点表示当天未判定
                </p>
              </div>
              <span v-if="envWithData.length > 6" class="text-[9.5px] text-ink-faint">
                仅显示样本量前 6 个环境
              </span>
            </header>
            <div class="px-4 py-3.5">
              <DashboardEnvTrendChart v-if="envTrendSeries.length" :series="envTrendSeries" />
              <p v-else class="py-12 text-center text-[11px] text-ink-faint">
                近 {{ envDays }} 天暂无已判定执行
              </p>

              <div class="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 border-t border-line-soft pt-2.5 text-[9.5px] text-ink-faint">
                <span>
                  对比窗口
                  <b class="ml-1.5 font-semibold tabular-nums text-ink-soft">近 {{ envDays }} 天</b>
                </span>
                <span>
                  总样本
                  <b class="ml-1.5 font-semibold tabular-nums text-ink-soft">{{ envMeasuredTotal }} 次已判定</b>
                </span>
                <span v-if="envBestStable">
                  波动最小
                  <b class="ml-1.5 font-semibold text-ink-soft">
                    {{ envBestStable.name }} ±{{ envVolatilityValue(envBestStable).toFixed(1) }}pp
                  </b>
                </span>
              </div>
            </div>
          </section>

          <aside class="card flex min-w-0 flex-col">
            <header class="border-b border-line px-4 py-3">
              <h2 class="panel-title">失败构成</h2>
              <p class="mt-0.5 text-[10.5px] text-ink-muted">
                判负原因按执行环境拆分 · 用于判断是 Agent 行为还是环境异常
              </p>
            </header>

            <div class="flex-1 divide-y divide-line-soft">
              <div v-for="env in envWithData" :key="`fail-${env.code}`" class="px-4 py-3">
                <div class="flex items-center justify-between gap-2">
                  <span class="flex min-w-0 items-center gap-1.5 text-[10.5px] font-medium text-ink-soft">
                    <i
                      class="h-1.5 w-1.5 flex-none rounded-full"
                      :style="{ backgroundColor: envColorMap[env.code] }"
                    />
                    <span class="min-w-0 break-words">{{ env.name }}</span>
                  </span>
                  <span class="flex-none text-[10px] tabular-nums text-ink-faint">
                    {{ envFailureTotal(env) }} 次失败
                  </span>
                </div>

                <div
                  v-if="env.failure_reasons.length"
                  class="mt-2 flex h-2 gap-px overflow-hidden rounded-full bg-slate-100"
                >
                  <span
                    v-for="reason in env.failure_reasons"
                    :key="reason.status"
                    class="block h-full"
                    :class="envFailureTone[reason.status] ?? 'bg-slate-300'"
                    :style="{ width: `${envFailureWidth(env, reason.count)}%` }"
                  />
                </div>
                <p v-else class="mt-2 text-[9.5px] text-ink-faint">窗口内没有判负执行</p>

                <div
                  v-if="env.failure_reasons.length"
                  class="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] text-ink-muted"
                >
                  <span v-for="reason in env.failure_reasons" :key="`${env.code}-${reason.status}`">
                    <i
                      class="mr-1 inline-block h-1.5 w-1.5 rounded-sm align-middle"
                      :class="envFailureTone[reason.status] ?? 'bg-slate-300'"
                    />{{ statusLabel(reason.status) }} {{ reason.count }}
                  </span>
                </div>
              </div>

              <p v-if="!envWithData.length" class="px-4 py-6 text-center text-[11px] text-ink-faint">
                暂无失败数据
              </p>
            </div>

            <div
              v-if="envFailureLegend.length"
              class="mt-auto flex flex-wrap gap-x-3 gap-y-1 border-t border-line-soft bg-[#fbfcfe] px-4 py-2 text-[9px] text-ink-faint"
            >
              <span v-for="item in envFailureLegend" :key="item.status" class="inline-flex items-center gap-1">
                <i class="h-1.5 w-1.5 rounded-sm" :class="item.cls" />{{ item.label }}
              </span>
            </div>
          </aside>
        </div>

        <p class="text-[10px] text-ink-faint">
          环境对比按 started_at 归窗并排除草稿试跑；波动为每日通过率标准差，抖动为窗口内既通过又失败的用例占比。
          每 20 秒自动刷新。
        </p>
      </template>
    </template>

    <TaskCreateModal v-model="showCreate" @created="onTaskCreated" />
  </div>
</template>
