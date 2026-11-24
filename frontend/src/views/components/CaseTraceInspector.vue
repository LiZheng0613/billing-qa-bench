<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import EmptyState from '../../components/EmptyState.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import StatusPill from '../../components/StatusPill.vue'
import type { ExecutionDetail } from '../../types'
import {
  formatDateTime,
  formatMs,
  formatNumber,
  formatScore,
  sourceLabel,
} from '../../utils/format'
import { copyText } from '../../utils/clipboard'
import { useToastStore } from '../../stores/toast'
import { buildTraceModel, PASS_LINE_SOURCE_LABEL, phaseBuckets, phaseMeta } from './case-trace/model'
import { useTraceView, type StatusFilter } from './case-trace/useTraceView'
import TraceIcon from './case-trace/TraceIcon.vue'
import TurnBubble from './case-trace/TurnBubble.vue'
import TurnCompare from './case-trace/TurnCompare.vue'
import TurnDrawer from './case-trace/TurnDrawer.vue'
import SelfCheckBlock from './case-trace/SelfCheckBlock.vue'
import SelfCheckPanel from './case-trace/SelfCheckPanel.vue'
import JudgePanel from './case-trace/JudgePanel.vue'

const props = defineProps<{
  detail: ExecutionDetail | null
  loading: boolean
  /** 初始展示页签（调试完成跳转带 tab=selfcheck 时直接落在「追问自检」） */
  initialTab?: string | null
}>()

const toast = useToastStore()
const router = useRouter()

/** 从执行详情直接跳转到该用例详情，复用用例资产的完整上下文。 */
function openCaseDetail(): void {
  if (!props.detail?.case_id) return
  void router.push({ name: 'case-detail', params: { id: String(props.detail.case_id) } })
}

/** 前端下载 JSON：后端暂无导出接口时统一走这里。 */
function downloadJson(filename: string, payload: unknown): void {
  const text = JSON.stringify(payload, null, 2)
  const blob = new Blob([text], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/** 后端暂无导出接口：前端按执行详情生成一份 JSON 报告，口径与页面一致。 */
function exportReport(): void {
  if (!props.detail) return
  try {
    const payload = {
      exported_at: new Date().toISOString(),
      trace_uid: props.detail.trace_uid,
      case: {
        id: props.detail.case_id,
        code: props.detail.case_code,
        name: props.detail.case_name,
      },
      execution: props.detail,
    }
    downloadJson(`trace-${props.detail.trace_uid || props.detail.id}.json`, payload)
    toast.success('报告已导出（JSON）')
  } catch {
    toast.error('导出失败')
  }
}

function exportSnapshot(): void {
  if (!props.detail) return
  try {
    downloadJson(
      `snapshot-${props.detail.case_code || props.detail.case_id}-v${props.detail.case_version}.json`,
      props.detail.case_snapshot,
    )
    toast.success('快照已导出（JSON）')
  } catch {
    toast.error('导出失败')
  }
}

/** 重新执行需要回到调试工作台，按当前用例的完整上下文发起。 */
function rerun(): void {
  if (!props.detail?.case_id) return
  void router.push({ name: 'case-debug', params: { id: String(props.detail.case_id) } })
}

const model = computed(() => buildTraceModel(props.detail))

const {
  tab,
  mode,
  status,
  query,
  selfOpen,
  drawer,
  openDrawer,
  closeDrawer,
  setSection,
  setEvidTab,
  switchTurn,
  toggleRow,
  focusTag,
} = useTraceView(
  () => props.detail,
  () => props.initialTab,
)

type TabKey = 'turns' | 'judge' | 'selfcheck' | 'snapshot' | 'raw'
const TABS: Array<{ key: TabKey; label: string; icon: string }> = [
  { key: 'turns', label: '对话轮次', icon: 'list' },
  { key: 'judge', label: '判分', icon: 'scale' },
  { key: 'selfcheck', label: '追问自检', icon: 'refresh' },
  { key: 'snapshot', label: '执行快照', icon: 'file' },
  { key: 'raw', label: '原始报文 · 调用链', icon: 'route' },
]
const activeTab = tab

const visibleTabs = computed(() =>
  // 通过的用例连页签都不出现；失败用例保留入口，没有 self_check 记录时给「未触发」空态
  TABS.filter(
    (item) => item.key !== 'selfcheck' || Boolean(model.value.selfCheck) || !model.value.executionPassed,
  ),
)

/**
 * 页签必须落在当前可见集合里：自检页签在通过用例上不出现，
 * 旧链接（?tab=selfcheck）指向这种执行时若不放回对话轮次，
 * 页面会既没有高亮页签、又渲染出「未开启自检」的空态（通过的用例还会漏出自检字样）。
 */
watch(
  [visibleTabs, () => props.detail],
  ([tabs, detail]) => {
    // 详情请求返回前 model 为空，不能据此把自检页签判为不可用
    if (!detail) return
    if (!tabs.some((item) => item.key === activeTab.value)) activeTab.value = 'turns'
  },
  { immediate: true },
)

const STATUS_OPTIONS: Array<{ key: StatusFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'pass', label: '通过' },
  { key: 'warn', label: '部分命中' },
  { key: 'fail', label: '未通过' },
  { key: 'unknown', label: '未判定' },
]

const statusCounts = computed<Record<StatusFilter, number>>(() => {
  const turns = model.value.turns
  return {
    all: turns.length,
    pass: turns.filter((turn) => turn.verdict === 'pass').length,
    warn: turns.filter((turn) => turn.verdict === 'warn').length,
    fail: turns.filter((turn) => turn.verdict === 'fail').length,
    unknown: turns.filter((turn) => turn.verdict === 'unknown').length,
  }
})

// 未判定是降级态：只有真实存在这类轮次时才显示筛选，避免设计稿对齐时多出空选项
const visibleStatusOptions = computed(() =>
  STATUS_OPTIONS.filter(
    (option) => option.key !== 'unknown' || statusCounts.value.unknown > 0 || status.value === 'unknown',
  ),
)

const filteredTurns = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return model.value.turns.filter((turn) => {
    if (status.value !== 'all' && turn.verdict !== status.value) return false
    if (!keyword) return true
    const haystack = [turn.query, turn.answer, turn.intent, ...turn.tags, ...turn.sessionTags]
      .join(' ')
      .toLowerCase()
    return haystack.includes(keyword)
  })
})

const variablePreview = computed(() => model.value.sessionVariables.map(([key, value]) => `${key}=${value}`))

const sessionLabel = computed(() => {
  const ids = model.value.session.sessionIds
  if (!ids.length) return '未记录'
  const first = ids[0]
  return first.length > 20 ? `${first.slice(0, 18)}…` : first
})

const agentModelText = computed(() => {
  for (const turn of props.detail?.turns ?? []) {
    const payload = turn.request_payload
    if (!payload || typeof payload !== 'object') continue
    const body = (payload as Record<string, unknown>).body
    const source = body && typeof body === 'object' ? (body as Record<string, unknown>) : (payload as Record<string, unknown>)
    const name = source.model ?? source.model_name ?? source.modelName
    if (typeof name === 'string' && name) return name
  }
  return '未记录模型'
})

// ---- 页头 KPI（与原型 5 卡对齐） ----
const judgeScore = computed<number | null>(() => {
  const detail = props.detail
  if (!detail) return null
  const detailScore = typeof detail.total_score === 'number' ? detail.total_score : null
  const raw = detail.judge_result as Record<string, unknown> | null
  if (!raw || typeof raw !== 'object') return detailScore
  if (detail.grading_mode === 'ANSWER_CONTRACT') {
    const quality = raw.quality as Record<string, unknown> | undefined
    return typeof quality?.total_score === 'number' ? quality.total_score : detailScore
  }
  return typeof raw.total_score === 'number' ? raw.total_score : detailScore
})

const latencySpread = computed(() => {
  const values = model.value.turns
    .map((turn) => turn.latency)
    .filter((value): value is number => typeof value === 'number')
    .sort((a, b) => a - b)
  if (!values.length) return { avg: null as number | null, p95: null as number | null }
  const avg = values.reduce((sum, value) => sum + value, 0) / values.length
  const index = Math.min(values.length - 1, Math.max(0, Math.ceil(values.length * 0.95) - 1))
  return { avg, p95: values[index] }
})

const totalTokens = computed(
  () => (props.detail?.simulator_tokens ?? 0) + (props.detail?.judge_tokens ?? 0),
)

const sessionConsistent = computed(() => model.value.session.sessionIds.length <= 1)

const allPass = computed(
  () => model.value.turns.length > 0 && model.value.session.passCount === model.value.turns.length,
)
const noneJudged = computed(() => {
  const session = model.value.session
  return (
    session.turns > 0 &&
    session.passCount === 0 &&
    session.warnCount === 0 &&
    session.failCount === 0
  )
})
const passHeadline = computed(() => {
  if (!model.value.turns.length) return '—'
  if (allPass.value) return '通过'
  if (noneJudged.value) return '未判定'
  // 单轮时轮次结论就是这一轮的结论：达线但硬门槛未全过应显示「部分命中」，
  // 不能因为整通执行未通过就抹掉轮次的部分命中（900003 / 900006）。
  if (model.value.turns.length === 1) {
    return model.value.turns[0].verdict === 'warn' ? '部分命中' : '未通过'
  }
  // 多轮：没有任何一轮通过时不能说「部分通过」（900001 是 0 通过 / 1 未通过 / 2 未判定）
  if (model.value.session.passCount === 0) {
    return model.value.session.failCount > 0 ? '未通过' : '部分命中'
  }
  return '部分通过'
})

/** 轮次结论着色跟结论走：未通过用玫红，部分通过/部分命中用琥珀，未判定用灰 */
const passTone = computed<'pass' | 'warn' | 'fail' | 'unknown'>(() => {
  if (!model.value.turns.length) return 'unknown'
  if (allPass.value) return 'pass'
  if (noneJudged.value) return 'unknown'
  return passHeadline.value === '未通过' ? 'fail' : 'warn'
})

// ---- 判分页：执行级维度（契约模式取质量分维度） ----
const pageDims = computed(() =>
  props.detail?.grading_mode === 'ANSWER_CONTRACT' ? contractQualityDims.value : dims.value,
)

// ---- 执行快照页 ----
const snapshot = computed(() => props.detail?.case_snapshot ?? null)
const snapshotTurns = computed(() => snapshot.value?.turns ?? [])

// ---- 页面级调用链（原始报文页） ----
const chainSteps = computed(() =>
  model.value.turns.flatMap((turn) => turn.steps.map((step) => ({ turn: turn.index, step }))),
)

const chainTotalMs = computed(() => chainSteps.value.reduce((sum, item) => sum + item.step.ms, 0))

const chainSlowest = computed(() => {
  if (!chainSteps.value.length) return null
  return chainSteps.value.reduce((acc, item) => (item.step.ms > acc.step.ms ? item : acc), chainSteps.value[0])
})

const chainWarnSteps = computed(() => chainSteps.value.filter((item) => item.step.status === 'warn'))

const chainWarnTurns = computed(() => [...new Set(chainWarnSteps.value.map((item) => item.turn))])

const chainAvgSteps = computed(() => {
  const used = model.value.turns.filter((turn) => turn.steps.length).length
  return used ? chainSteps.value.length / used : 0
})

const chainBusiest = computed(() => {
  if (!model.value.turns.length) return null
  return model.value.turns.reduce((best, turn) => (turn.steps.length > best.steps.length ? turn : best))
})

const maxTurnMs = computed(() =>
  model.value.turns.reduce(
    (max, turn) => Math.max(max, phaseBuckets(turn.steps).reduce((sum, bucket) => sum + bucket.ms, 0)),
    0,
  ),
)

const toolStats = computed(() => {
  const map = new Map<string, { tool: string; calls: number; fail: number; ms: number }>()
  for (const item of chainSteps.value) {
    const key = item.step.tool
    const row = map.get(key) ?? { tool: key, calls: 0, fail: 0, ms: 0 }
    row.calls += 1
    if (item.step.status === 'warn') row.fail += 1
    row.ms += item.step.ms
    map.set(key, row)
  }
  return [...map.values()]
    .map((row) => ({ ...row, avg: Math.round(row.ms / Math.max(1, row.calls)) }))
    .sort((a, b) => b.calls - a.calls || b.fail - a.fail || b.avg - a.avg)
})

const toolStatsOpen = ref(false)
const visibleToolStats = computed(() =>
  toolStatsOpen.value ? toolStats.value : toolStats.value.slice(0, 6),
)
const restToolStats = computed(() => toolStats.value.slice(6))
const restToolCalls = computed(() => restToolStats.value.reduce((sum, row) => sum + row.calls, 0))

const slowSteps = computed(() =>
  [...chainSteps.value]
    .sort((a, b) => {
      const warnDiff = Number(b.step.status === 'warn') - Number(a.step.status === 'warn')
      return warnDiff || b.step.ms - a.step.ms
    })
    .slice(0, 5),
)

const chainFocusTurn = computed(() => {
  const failed = model.value.turns.find((turn) => turn.verdict === 'fail')
  return failed?.index ?? model.value.turns[model.value.turns.length - 1]?.index ?? null
})

const currentTurn = computed(
  () => model.value.turns.find((turn) => turn.index === drawer.turnIndex) ?? null,
)

// ---- 逐轮折叠（页面级） ----
const collapsedTurns = ref<Set<number>>(new Set())

const allCollapsed = computed(
  () =>
    filteredTurns.value.length > 0 &&
    filteredTurns.value.every((turn) => collapsedTurns.value.has(turn.index)),
)

function toggleTurn(index: number): void {
  const next = new Set(collapsedTurns.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  collapsedTurns.value = next
}

function toggleAllTurns(): void {
  collapsedTurns.value = allCollapsed.value
    ? new Set()
    : new Set(filteredTurns.value.map((turn) => turn.index))
}

watch(
  () => props.detail?.id,
  () => {
    collapsedTurns.value = new Set()
    selfOpen.value = true
  },
)

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && drawer.open) closeDrawer()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

// ---- 判分页（沿用原页级的判分 / 契约 / 规则内容） ----
interface RuleCheckItem {
  type?: string
  label?: string
  expected?: unknown
  actual?: unknown
  passed?: boolean
  evidence?: string
  mode?: string
  bound_name?: string | null
  reason?: string
}

const DIMENSION_LABEL: Record<string, string> = {
  data_accuracy: '数据准确性',
  logic_consistency: '逻辑一致性',
  goal_completion: '目标完成度',
  tone_clarity: '语气清晰度',
}

const ROOT_CAUSE_LABEL: Record<string, string> = {
  HALLUCINATION: '答案幻觉',
  RETRIEVAL: '取数缺失',
  SYNTHESIS: '汇总错误',
  BEHAVIOR: '交互行为不当',
  UNKNOWN: '未定位',
}

const GATE_LABEL: Record<string, string> = {
  safety: '安全',
  behavior: '行为',
  facts: '事实',
  extras: '扩展',
}

interface ContractFactItem {
  fact_id?: string
  kind?: string
  label?: string | null
  expected?: unknown
  actual?: unknown
  passed?: boolean
  reason?: string
  is_gate?: boolean
  mode?: string
  bound_name?: string | null
  conflict_name?: string | null
  degraded?: boolean
  evidence_ref?: string | null
}

const ruleBlock = computed(() => {
  const raw = props.detail?.rule_result
  if (!raw || typeof raw !== 'object') return null
  return raw as { passed?: boolean; checks?: RuleCheckItem[]; extracted?: Record<string, unknown> }
})

const ruleChecks = computed<RuleCheckItem[]>(() => ruleBlock.value?.checks ?? [])

const aggregate = computed(() => {
  const raw = props.detail?.judge_result
  if (!raw || typeof raw !== 'object') return null
  return raw as Record<string, unknown>
})

const policyNotes = computed<string[]>(() => {
  const notes = aggregate.value?.policy_notes
  return Array.isArray(notes) ? notes.map(String) : []
})

const dims = computed(() => {
  const raw = aggregate.value?.dimension_scores
  if (!raw || typeof raw !== 'object') return [] as Array<{ key: string; label: string; value: number }>
  return Object.entries(raw as Record<string, number>).map(([key, value]) => ({
    key,
    label: DIMENSION_LABEL[key] ?? key,
    value,
  }))
})

const contractGrading = computed<Record<string, unknown> | null>(() => {
  const raw = props.detail?.answer_grading
  if (!raw || typeof raw !== 'object') return null
  return raw as Record<string, unknown>
})

const contractGates = computed(() => {
  const raw = contractGrading.value?.gates
  if (!raw || typeof raw !== 'object') return [] as Array<{ key: string; label: string; passed: boolean }>
  return Object.entries(raw as Record<string, boolean>).map(([key, passed]) => ({
    key,
    label: GATE_LABEL[key] ?? key,
    passed: Boolean(passed),
  }))
})

const contractFacts = computed<ContractFactItem[]>(() => {
  const raw = props.detail?.fact_results
  if (Array.isArray(raw)) return raw as ContractFactItem[]
  const nested = contractGrading.value?.facts
  return Array.isArray(nested) ? (nested as ContractFactItem[]) : []
})

const contractQuality = computed<{ total_score?: number; dimension_scores?: Record<string, number> } | null>(() => {
  const raw = aggregate.value?.quality
  if (!raw || typeof raw !== 'object') return null
  return raw as { total_score?: number; dimension_scores?: Record<string, number> }
})

const contractQualityDims = computed(() => {
  const raw = contractQuality.value?.dimension_scores
  if (!raw || typeof raw !== 'object') {
    return [] as Array<{ key: string; label: string; value: number }>
  }
  return Object.entries(raw).map(([key, value]) => ({
    key,
    label: DIMENSION_LABEL[key] ?? key,
    value,
  }))
})

const rootCauseText = computed(() => {
  const raw = props.detail?.root_cause
  if (!raw) return null
  return ROOT_CAUSE_LABEL[raw] ?? raw
})

/** 从判分行「定位到对话」：切回对话页签，滚动并短暂高亮目标轮次。 */
function locateTurn(index: number): void {
  activeTab.value = 'turns'
  if (mode.value === 'compare') mode.value = 'chat'
  // 目标轮被筛选隐藏时先恢复全部，避免「点了没反应」
  status.value = 'all'
  query.value = ''
  void nextTick(() => {
    const el = document.querySelector(`[data-turn="${index}"]`)
    if (!(el instanceof HTMLElement)) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('turn-flash')
    window.setTimeout(() => el.classList.remove('turn-flash'), 1800)
  })
}

/** 复制一轮判定结论（纯文本，便于贴到缺陷单）。 */
async function copyJudgeConclusion(text: string): Promise<void> {
  const ok = await copyText(text)
  if (ok) toast.success('判定结论已复制')
  else toast.error('复制失败')
}

async function copyTraceUid(): Promise<void> {
  if (!props.detail) return
  const ok = await copyText(props.detail.trace_uid)
  if (ok) toast.success('Trace UID 已复制')
  else toast.error('复制失败')
}
</script>

<template>
  <section class="card flex min-h-[520px] flex-col overflow-hidden">
    <div v-if="loading" class="py-24 text-center text-sm text-slate-500">加载执行详情…</div>
    <EmptyState
      v-else-if="!detail"
      title="选择一条执行记录"
      description="点击任意执行记录，在这里查看完整的对话轮次、判分、执行与证据。"
    />
    <template v-else>
      <header class="border-b border-slate-200 px-5 py-4">
        <div class="flex flex-wrap items-start gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="truncate text-base font-semibold text-slate-900">
                <button
                  type="button"
                  class="inline-flex max-w-full items-center gap-1 truncate rounded-sm text-left underline decoration-dotted underline-offset-2 transition hover:text-indigo-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
                  title="查看用例详情"
                  @click="openCaseDetail"
                >
                  <span class="truncate">{{ detail.case_code || `用例 #${detail.case_id}` }}</span>
                  <svg
                    class="h-3.5 w-3.5 shrink-0 text-slate-400"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.8"
                    aria-hidden="true"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" d="M7 17 17 7M9 7h8v8" />
                  </svg>
                </button>
              </h2>
              <StatusPill :status="detail.status" />
              <span class="tag">v{{ detail.case_version }}</span>
              <span class="tag">{{ sourceLabel(detail.source) }}</span>
              <span
                v-if="detail.environment_name"
                class="tag border-indigo-200 bg-indigo-50 text-indigo-700"
                title="本次执行实际使用的环境（任务指定环境优先）"
              >
                执行环境 {{ detail.environment_name }}
              </span>
              <span v-if="detail.attempt_no > 1" class="tag border-amber-200 text-amber-700">
                第 {{ detail.attempt_no }} 次尝试
              </span>
              <span v-if="detail.error_kind === 'ENV'" class="tag border-amber-200 text-amber-700">
                环境异常
              </span>
              <span v-else-if="detail.error_kind === 'LLM'" class="tag border-sky-200 text-sky-700">
                模型异常
              </span>
              <span
                v-if="detail.grading_mode === 'ANSWER_CONTRACT'"
                class="tag border-indigo-200 text-indigo-700"
              >
                答案契约
              </span>
              <span v-if="detail.lucky_pass" class="tag border-amber-300 text-amber-700">
                幸运通过（无取数证据）
              </span>
              <span v-if="rootCauseText" class="tag border-rose-200 text-rose-700">
                根因：{{ rootCauseText }}
              </span>
              <span v-if="detail.grader_version" class="tag">{{ detail.grader_version }}</span>
            </div>
            <p class="mt-1 truncate text-xs text-slate-500">{{ detail.case_name || '—' }}</p>
            <button
              class="mt-1 inline-flex items-center gap-1 font-mono text-[10px] text-slate-500 transition hover:text-slate-700"
              @click="copyTraceUid"
            >
              trace: {{ detail.trace_uid }}
              <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path stroke-linecap="round" stroke-linejoin="round" d="M8 8V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-3M5 8h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2Z" />
              </svg>
            </button>
          </div>
          <div class="ml-auto flex flex-none flex-wrap items-center gap-2">
            <button type="button" class="btn btn-xs" title="按当前执行详情导出 JSON 报告" @click="exportReport">
              <TraceIcon name="download" :size="11" />
              导出报告
            </button>
            <button type="button" class="btn-primary btn-xs" title="回到调试工作台重新执行" @click="rerun">
              <TraceIcon name="refresh" :size="11" />
              重新执行
            </button>
          </div>
        </div>

        <div class="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-5">
          <div class="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <span
              class="mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full"
              :class="{
                'bg-emerald-50 text-emerald-600': passTone === 'pass',
                'bg-amber-50 text-amber-600': passTone === 'warn',
                'bg-rose-50 text-rose-600': passTone === 'fail',
                'bg-slate-100 text-slate-500': passTone === 'unknown',
              }"
            >
              <TraceIcon
                :name="passTone === 'pass' ? 'check' : passTone === 'warn' ? 'alert' : passTone === 'fail' ? 'x' : 'info'"
                :size="13"
              />
            </span>
            <div class="min-w-0">
              <p class="text-[10px] uppercase tracking-wider text-slate-500">轮次结论</p>
              <p class="text-lg font-semibold tabular-nums text-slate-900">
                {{ passHeadline }}
                <span v-if="model.turns.length" class="text-xs font-normal text-slate-500">
                  / {{ model.turns.length }} 轮
                </span>
              </p>
              <p v-if="!model.turns.length" class="truncate text-[10px] text-slate-500">
                {{
                  judgeScore !== null
                    ? '执行记录只保留执行级结论，没有逐轮明细'
                    : '本次执行没有产出判定结论，也没有逐轮明细'
                }}
              </p>
              <p v-else-if="noneJudged" class="truncate text-[10px] text-slate-500">
                {{ model.turns.length }} 轮均未判定 · 无轮次级分数与门槛
              </p>
              <p v-else class="truncate text-[10px] text-slate-500">
                {{ model.session.passCount }} 轮通过<template v-if="model.session.warnCount">
                  · {{ model.session.warnCount }} 轮部分命中</template><template v-if="model.session.failCount">
                  · {{ model.session.failCount }} 轮未通过</template><template
                  v-if="model.session.unknownCount"
                > · {{ model.session.unknownCount }} 轮未判定</template>
              </p>
            </div>
          </div>

          <div class="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <span class="mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full bg-indigo-50 text-indigo-600">
              <TraceIcon name="grid" :size="13" />
            </span>
            <div class="min-w-0">
              <p class="text-[10px] uppercase tracking-wider text-slate-500">
                {{ detail.grading_mode === 'ANSWER_CONTRACT' ? '排序分' : '裁判综合分' }}
              </p>
              <p class="text-lg font-semibold tabular-nums text-indigo-600">
                {{ formatScore(judgeScore) }}
                <span class="text-xs font-normal text-slate-500">/ 100</span>
              </p>
              <p class="truncate text-[10px] text-slate-500">
                {{ detail.grading_mode === 'ANSWER_CONTRACT' ? '质量分参考' : 'LLM 裁判综合评分' }}
              </p>
            </div>
          </div>

          <div class="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <span class="mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full bg-sky-50 text-sky-600">
              <TraceIcon name="bolt" :size="13" />
            </span>
            <div class="min-w-0">
              <p class="text-[10px] uppercase tracking-wider text-slate-500">平均时延</p>
              <p class="text-lg font-semibold tabular-nums text-slate-900">
                {{ formatMs(latencySpread.avg) }}
                <span class="text-[10px] font-normal text-slate-500">P95 {{ formatMs(latencySpread.p95) }}</span>
              </p>
              <p class="truncate text-[10px] text-slate-500">按轮次 TTFT / 总时延</p>
            </div>
          </div>

          <div class="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <span class="mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full bg-slate-100 text-slate-600">
              <TraceIcon name="hash" :size="13" />
            </span>
            <div class="min-w-0">
              <p class="text-[10px] uppercase tracking-wider text-slate-500">总 Tokens</p>
              <p class="text-lg font-semibold tabular-nums text-slate-900">{{ formatNumber(totalTokens) }}</p>
              <p class="truncate text-[10px] text-slate-500">模拟器 {{ formatNumber(detail.simulator_tokens) }} · 裁判 {{ formatNumber(detail.judge_tokens) }}</p>
            </div>
          </div>

          <div class="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <span
              class="mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full"
              :class="
                !model.turns.length
                  ? 'bg-slate-100 text-slate-500'
                  : sessionConsistent
                    ? 'bg-emerald-50 text-emerald-600'
                    : 'bg-amber-50 text-amber-600'
              "
            >
              <TraceIcon :name="!model.turns.length ? 'info' : sessionConsistent ? 'shield' : 'alert'" :size="13" />
            </span>
            <div class="min-w-0">
              <p class="text-[10px] uppercase tracking-wider text-slate-500">会话一致性</p>
              <p class="text-lg font-semibold text-slate-900">
                {{ !model.turns.length ? '—' : sessionConsistent ? 'PERSIST' : '不一致' }}
              </p>
              <p class="truncate text-[10px] text-slate-500">
                {{
                  !model.turns.length
                    ? '无逐轮记录，无法判断'
                    : sessionConsistent
                      ? '各轮会话一致'
                      : `${model.session.sessionIds.length} 个会话 ID`
                }}
              </p>
            </div>
          </div>
        </div>

        <p
          v-if="detail.error_message"
          class="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
        >
          {{ detail.error_message }}
        </p>
        <div v-if="policyNotes.length" class="mt-3 space-y-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
          <p v-for="note in policyNotes" :key="note" class="text-xs text-amber-700">· {{ note }}</p>
        </div>
      </header>

      <div class="flex flex-wrap gap-1 border-b border-slate-200 px-4 py-2">
        <button
          v-for="item in visibleTabs"
          :key="item.key"
          type="button"
          class="tab-btn inline-flex items-center gap-1.5"
          :class="activeTab === item.key ? 'tab-btn-active' : ''"
          @click="activeTab = item.key"
        >
          <TraceIcon :name="item.icon" :size="11" />
          {{ item.label }}
        </button>
      </div>

      <div class="flex-1 overflow-y-auto px-4 py-4">
        <!-- ============================ 对话轮次 ============================ -->
        <template v-if="activeTab === 'turns'">
          <div v-if="model.turns.length" class="mb-3 flex flex-wrap items-center gap-2">
            <div class="inline-flex rounded-lg border border-line bg-slate-50 p-0.5">
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition"
                :class="mode === 'chat' ? 'bg-white text-ink shadow-sm' : 'text-slate-500 hover:text-ink'"
                :aria-pressed="mode === 'chat'"
                @click="mode = 'chat'"
              >
                <TraceIcon name="chat" :size="11" />
                对话
              </button>
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition"
                :class="mode === 'compare' ? 'bg-brand-soft text-brand shadow-sm' : 'text-slate-500 hover:text-ink'"
                :aria-pressed="mode === 'compare'"
                @click="mode = 'compare'"
              >
                <TraceIcon name="columns" :size="11" />
                对照
              </button>
            </div>

            <div class="inline-flex flex-wrap items-center gap-0.5 rounded-lg border border-line bg-white p-0.5">
              <button
                v-for="option in visibleStatusOptions"
                :key="option.key"
                type="button"
                class="rounded-md px-2 py-1 text-[11px] font-medium transition"
                :class="
                  status === option.key
                    ? 'bg-brand-soft text-brand ring-1 ring-brand-edge'
                    : 'text-slate-500 hover:text-ink'
                "
                @click="status = option.key"
              >
                {{ option.label }}
                <span class="ml-0.5 font-mono text-[10px] text-slate-400">{{ statusCounts[option.key] }}</span>
              </button>
            </div>

            <label class="inline-flex items-center gap-1.5 rounded-lg border border-line bg-white px-2 py-1">
              <TraceIcon name="search" :size="11" class="text-slate-400" />
              <input
                v-model="query"
                type="search"
                class="w-36 bg-transparent text-[11px] text-slate-700 outline-none placeholder:text-slate-400"
                placeholder="搜索提问 / 回复 / 归因"
              />
            </label>

            <span class="flex-1" />
            <span class="text-[10px] text-slate-500">
              显示 {{ filteredTurns.length }} / {{ model.turns.length }} 轮
            </span>
            <button
              v-if="mode === 'chat'"
              type="button"
              class="btn-ghost btn-xs"
              @click="toggleAllTurns"
            >
              {{ allCollapsed ? '全部展开' : '全部折叠' }}
            </button>
          </div>

          <div class="mb-3 rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div class="flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
              <span class="font-medium text-slate-800">会话</span>
              <span
                class="rounded-full border bg-white px-2 py-0.5 font-mono text-[10px]"
                :class="
                  model.session.consistentSession
                    ? 'border-indigo-100 text-indigo-600'
                    : 'border-amber-300 text-amber-700'
                "
                :title="model.session.sessionIds.join('\n')"
              >
                {{ sessionLabel }}
              </span>
              <span v-if="!model.session.consistentSession" class="text-amber-700">
                检测到 {{ model.session.sessionIds.length }} 个会话值，上下文可能未延续
              </span>
              <span v-if="model.session.turns">共 {{ model.session.turns }} 轮</span>
              <template v-if="model.session.turns">
                <span class="text-emerald-700">通过 {{ model.session.passCount }}</span>
                <span v-if="model.session.warnCount" class="text-amber-700">
                  部分命中 {{ model.session.warnCount }}
                </span>
                <span class="text-rose-700">未通过 {{ model.session.failCount }}</span>
                <span v-if="model.session.unknownCount" class="text-slate-500">
                  未判定 {{ model.session.unknownCount }}
                </span>
              </template>
              <span v-else class="text-slate-500">没有逐轮明细</span>
              <span v-if="model.session.totalTokens" class="text-slate-500">
                {{ model.session.totalTokens }} tokens
              </span>
            </div>
            <div v-if="variablePreview.length" class="mt-1 flex flex-wrap items-center gap-1">
              <span class="text-[10px] text-slate-500">用例变量：</span>
              <span
                v-for="item in variablePreview"
                :key="item"
                class="rounded-md border border-indigo-100 bg-white px-1.5 py-0.5 font-mono text-[10px] text-indigo-600"
              >
                {{ item }}
              </span>
            </div>
            <div v-if="model.judgeTags.length" class="mt-1 flex flex-wrap items-center gap-1">
              <span class="text-[10px] text-slate-500">错误归因（执行级）：</span>
              <span
                v-for="tag in model.judgeTags"
                :key="tag"
                class="rounded-md border border-rose-200 bg-white px-1.5 py-0.5 text-[10px] text-rose-700"
              >
                {{ tag }}
              </span>
            </div>
          </div>

          <EmptyState
            v-if="!model.turns.length"
            compact
            title="没有对话轮次"
            :description="
              judgeScore === null
                ? '本次执行没有产生对话轮次，也没有执行级评分与结论。'
                : detail.grading_mode === 'RUBRIC'
                  ? '该执行由旧评分模式（RUBRIC）落库，只保留执行级分数与结论，没有逐轮明细。'
                  : '执行记录只有执行级分数与结论，没有逐轮明细。'
            "
          />
          <div v-else-if="!filteredTurns.length" class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-6 text-center text-xs text-slate-500">
            当前筛选条件下没有轮次，可清空搜索或切回「全部」。
          </div>

          <TurnCompare
            v-else-if="mode === 'compare'"
            :turns="filteredTurns"
            @open="(index, section) => openDrawer(index, section)"
          />

          <div v-else class="mx-auto flex w-full max-w-[1020px] flex-col gap-2">
            <div v-for="turn in filteredTurns" :key="turn.index" class="turn-item" :data-turn="turn.index">
              <TurnBubble
                :turn="turn"
                :model="agentModelText"
                :collapsed="collapsedTurns.has(turn.index)"
                @toggle="toggleTurn(turn.index)"
                @open="(section) => openDrawer(turn.index, section)"
              />
            </div>

            <SelfCheckBlock
              v-if="model.selfCheck"
              class="mt-2"
              :record="model.selfCheck"
              :trigger="model.selfCheckTurn"
              :model="agentModelText"
              :turn-count="model.turns.length"
              :open="selfOpen"
              @toggle="selfOpen = !selfOpen"
              @locate="(index) => openDrawer(index, 'context')"
              @page="activeTab = 'selfcheck'"
              @judge="(index) => openDrawer(index, 'judge')"
            />
          </div>
        </template>

        <!-- ============================ 判分（1:1 还原原型） ============================ -->
        <JudgePanel
          v-if="activeTab === 'judge'"
          :model="model"
          :detail="detail"
          :score="judgeScore"
          :page-dims="pageDims"
          @open="openDrawer"
          @locate="locateTurn"
          @copy="copyJudgeConclusion"
        />

        <!-- ============================ 追问自检（1:1 还原原型） ============================ -->
        <template v-if="activeTab === 'selfcheck'">
          <SelfCheckPanel
            v-if="model.selfCheck"
            :record="model.selfCheck"
            :trigger="model.selfCheckTurn"
            :turn-count="model.turns.length"
            @locate="locateTurn"
          />
          <EmptyState
            v-else
            compact
            :title="model.selfCheckSkipReason ? '未触发追问自检' : '没有追问自检记录'"
            :description="
              model.selfCheckSkipReason
                ? `执行记录：${model.selfCheckSkipReason}（self_check = SKIPPED）；页面不补造自检内容。`
                : detail.status === 'ERROR' || detail.status === 'ABORTED'
                  ? '执行以异常 / 终止结束，未进入追问自检；没有 self_check 记录。'
                  : '本次执行没有 self_check 记录（任务未开启或本次未触发）；页面不补造自检内容。'
            "
          />
        </template>

        <!-- ============================ 执行快照 ============================ -->
        <template v-if="activeTab === 'snapshot'">
          <div class="space-y-3">
            <div class="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
              <span class="flex h-7 w-7 flex-none items-center justify-center rounded-full bg-indigo-50 text-indigo-600">
                <TraceIcon name="file" :size="13" />
              </span>
              <div class="min-w-0">
                <p class="text-xs font-medium text-slate-800">执行快照 v{{ detail.case_version }}</p>
                <p class="text-[10px] text-slate-500">
                  用例当前版本 v{{ detail.case_version }} · 执行于 {{ formatDateTime(detail.started_at) }} · 快照中的变量与期望答案不随后续用例编辑改变
                </p>
              </div>
              <span class="flex-1" />
              <button type="button" class="btn btn-xs" @click="exportSnapshot">
                <TraceIcon name="download" :size="11" />
                导出快照 JSON
              </button>
            </div>

            <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <div class="rounded-xl border border-slate-200 bg-white p-3">
                <p class="flex items-center gap-1 text-[11px] font-medium text-slate-700">
                  请求变量
                  <span class="rounded bg-slate-100 px-1 font-mono text-[10px] text-slate-500">{{ model.sessionVariables.length }}</span>
                </p>
                <div v-if="model.sessionVariables.length" class="mt-2 flex flex-wrap gap-1">
                  <span
                    v-for="([key, value], index) in model.sessionVariables"
                    :key="index"
                    class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[10px] text-slate-600"
                  >
                    {{ key }}={{ value }}
                  </span>
                </div>
                <p v-else class="mt-2 text-[11px] text-slate-400">快照未保存请求变量。</p>
                <p class="mt-2 text-[10px] leading-relaxed text-slate-500">
                  变量在执行时固定，后续编辑用例不会改写本次执行。
                </p>
              </div>

              <div class="rounded-xl border border-slate-200 bg-white p-3">
                <p class="text-[11px] font-medium text-slate-700">期望答案</p>
                <p v-if="snapshot?.expected_answer" class="mt-2 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-700">
                  {{ snapshot.expected_answer }}
                </p>
                <div v-else-if="snapshotTurns.length" class="mt-2 space-y-1.5">
                  <p
                    v-for="item in snapshotTurns"
                    :key="String(item.index)"
                    class="text-[11px] leading-relaxed text-slate-700"
                  >
                    <span class="mr-1 font-mono text-[10px] text-indigo-600">T{{ item.index }}</span>
                    {{ item.expected_answer || '—' }}
                  </p>
                </div>
                <p v-else class="mt-2 text-[11px] text-slate-400">快照未保存期望答案。</p>
                <p class="mt-2 text-[10px] text-slate-500">
                  共 {{ snapshotTurns.length }} 轮逐轮期望；{{ snapshotTurns.length ? '逐轮对齐' : '仅有执行级期望' }}。
                </p>
              </div>

              <div class="rounded-xl border border-slate-200 bg-white p-3">
                <p class="text-[11px] font-medium text-slate-700">判分口径</p>
                <div class="mt-2 flex flex-wrap gap-1">
                  <span class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-600">规则 {{ ruleChecks.length }} 条</span>
                  <span class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-600">facts {{ contractFacts.length }}</span>
                  <span class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-600">契约闸门 {{ contractGates.length }}</span>
                  <span class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-600" :title="`来源：${PASS_LINE_SOURCE_LABEL[model.passLineSource]}`">pass 阈值 {{ model.passLine }}（{{ PASS_LINE_SOURCE_LABEL[model.passLineSource] }}）</span>
                </div>
                <p class="mt-2 text-[10px] leading-relaxed text-slate-500">
                  {{ detail.grading_mode === 'ANSWER_CONTRACT'
                    ? '契约模式：最终答案正确性优先，工具调用轨迹仅用于失败诊断。'
                    : 'LEGACY 模式：规则 + LLM 裁判四维加权，硬门槛与总分同时满足才通过。' }}
                </p>
              </div>

              <div class="rounded-xl border border-dashed border-slate-300 bg-slate-50/60 p-3">
                <p class="flex items-center gap-1 text-[11px] font-medium text-slate-700">
                  与当前用例的差异
                  <span class="rounded bg-white px-1 font-mono text-[10px] text-slate-400">—</span>
                </p>
                <p class="mt-2 text-[11px] leading-relaxed text-slate-400">
                  执行记录只保存本次快照，未保存当前用例版本与最新规则，无法计算差异。
                </p>
                <p class="mt-2 text-[10px] leading-relaxed text-slate-500">
                  快照已固化本次执行的变量与期望答案，后续编辑用例不会改写历史判定。
                </p>
              </div>
            </div>

            <div class="grid gap-3 lg:grid-cols-2">
              <details class="rounded-xl border border-slate-200 bg-white">
                <summary class="cursor-pointer px-3 py-2 text-[11px] font-medium text-slate-700">
                  原始快照 JSON · case_snapshot（只读）
                </summary>
                <div class="border-t border-slate-100 p-2">
                  <JsonViewer :value="detail.case_snapshot" label="case_snapshot" max-height="360px" />
                </div>
              </details>
              <details class="rounded-xl border border-slate-200 bg-white">
                <summary class="cursor-pointer px-3 py-2 text-[11px] font-medium text-slate-700">
                  判定原始 JSON · rule_result / judge_result
                </summary>
                <div class="space-y-3 border-t border-slate-100 p-2">
                  <JsonViewer :value="detail.rule_result" label="rule_result" max-height="170px" />
                  <JsonViewer :value="detail.judge_result" label="judge_result" max-height="170px" />
                </div>
              </details>
            </div>
          </div>
        </template>

        <!-- ============================ 原始报文 · 调用链 ============================ -->
        <template v-if="activeTab === 'raw'">
          <div v-if="chainSteps.length" class="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <div class="rounded-xl border border-slate-200 bg-white px-3 py-2">
              <p class="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500">
                <TraceIcon name="route" :size="11" />
                平均步数
              </p>
              <p class="text-lg font-semibold tabular-nums text-slate-900">
                {{ chainAvgSteps.toFixed(1) }}
                <span class="text-xs font-normal text-slate-500">步 / 轮</span>
              </p>
              <p class="truncate text-[10px] text-slate-500">
                共 {{ chainSteps.length }} 次工具调用<template v-if="chainBusiest && chainBusiest.steps.length">
                  · T{{ chainBusiest.index }} {{ chainBusiest.steps.length }} 步</template>
              </p>
            </div>
            <div class="rounded-xl border border-slate-200 bg-white px-3 py-2">
              <p class="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500">
                <TraceIcon name="clock" :size="11" />
                链路总耗时
              </p>
              <p class="text-lg font-semibold tabular-nums text-slate-900">
                {{ (chainTotalMs / 1000).toFixed(2) }}
                <span class="text-xs font-normal text-slate-500">s</span>
              </p>
              <p class="truncate text-[10px] text-slate-500">
                占执行总耗时
                {{ detail.latency_ms ? Math.min(100, Math.round((chainTotalMs / detail.latency_ms) * 100)) : '—' }}%
              </p>
            </div>
            <div
              class="rounded-xl border px-3 py-2"
              :class="chainWarnSteps.length ? 'border-amber-200 bg-amber-50/60' : 'border-slate-200 bg-white'"
            >
              <p class="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500">
                <TraceIcon name="alert" :size="11" />
                失败调用
              </p>
              <p class="text-lg font-semibold tabular-nums" :class="chainWarnSteps.length ? 'text-amber-700' : 'text-slate-900'">
                {{ chainWarnSteps.length }}
                <span class="text-xs font-normal text-slate-500">次</span>
              </p>
              <p class="truncate text-[10px] text-slate-500">
                {{ chainWarnTurns.length ? chainWarnTurns.map((index) => `T${index}`).join(' / ') + ' · 校验未通过' : '没有失败调用' }}
              </p>
            </div>
            <div class="rounded-xl border border-slate-200 bg-white px-3 py-2">
              <p class="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500">
                <TraceIcon name="bolt" :size="11" />
                最慢步骤
              </p>
              <p class="text-lg font-semibold tabular-nums text-slate-900">
                {{ chainSlowest ? chainSlowest.step.ms : '—' }}
                <span class="text-xs font-normal text-slate-500">ms</span>
              </p>
              <p class="truncate text-[10px] text-slate-500">
                {{ chainSlowest ? `T${chainSlowest.turn} · ${chainSlowest.step.name}` : '—' }}
              </p>
            </div>
          </div>

          <div v-if="chainSteps.length" class="mb-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <div class="flex flex-wrap items-center gap-3 text-[10px] text-slate-500">
              <span v-for="phase in ['规划', '生成 SQL', '执行查询', '口径校验', '组织回复']" :key="phase" class="inline-flex items-center gap-1">
                <i class="h-2 w-2 rounded-sm" :style="{ background: phaseMeta(phase).color }" />
                {{ phase }}
              </span>
              <span class="inline-flex items-center gap-1"><i class="h-2 w-2 rounded-sm bg-amber-500" />失败 / 警告</span>
              <span class="ml-auto">按阶段聚合 · 横轴统一 0–{{ (maxTurnMs / 1000).toFixed(2) }}s · 点击行看明细</span>
            </div>

            <div class="mt-2 space-y-1.5">
              <button
                v-for="turn in model.turns"
                :key="turn.index"
                type="button"
                class="flex w-full items-center gap-2 rounded-md px-1 py-0.5 text-left transition hover:bg-slate-50"
                :title="`T${turn.index}：${turn.steps.length} 步 · 点击查看调用链明细`"
                @click="openDrawer(turn.index, 'exec')"
              >
                <span class="w-7 flex-none font-mono text-[10px] font-semibold text-indigo-600">T{{ turn.index }}</span>
                <span class="flex h-3 min-w-[120px] flex-1 items-center overflow-hidden rounded-sm bg-slate-100">
                  <span
                    v-for="bucket in phaseBuckets(turn.steps)"
                    :key="bucket.key"
                    class="h-full flex-none"
                    :style="{
                      width: `${Math.max(0.4, (bucket.ms / Math.max(1, maxTurnMs)) * 100)}%`,
                      background: bucket.warn ? '#f59e0b' : bucket.color,
                    }"
                    :title="`${bucket.label} · ${bucket.n} 步 · ${bucket.ms}ms`"
                  />
                </span>
                <span
                  class="w-12 flex-none text-right font-mono text-[10px]"
                  :class="turn.steps.length > 12 ? 'font-semibold text-amber-600' : 'text-slate-500'"
                >
                  {{ turn.steps.length }} 步
                </span>
                <span class="w-12 flex-none text-right font-mono text-[10px] text-slate-500">
                  {{ turn.latency ? (turn.latency / 1000).toFixed(2) + 's' : '—' }}
                </span>
              </button>
            </div>

            <p class="mt-2 text-[10px] leading-relaxed text-slate-500">
              链路长时不再逐条铺开：每轮按阶段聚合成段，右侧标注总步数（超过 12 步高亮）。点击任意轮次行，在抽屉「执行 → 调用链」按阶段折叠查看明细。
            </p>

            <div class="mt-3 grid gap-3 lg:grid-cols-2">
              <div class="rounded-lg border border-slate-200 bg-slate-50 p-2.5">
                <p class="flex items-center gap-1 text-[11px] font-medium text-slate-700">
                  工具调用统计
                  <span class="rounded bg-white px-1 font-mono text-[10px] text-slate-500">{{ toolStats.length }}</span>
                </p>
                <table class="mt-2 w-full text-left text-[10px]">
                  <thead>
                    <tr class="text-slate-400">
                      <th class="py-1 font-medium">工具</th>
                      <th class="py-1 text-right font-medium">调用</th>
                      <th class="py-1 text-right font-medium">失败</th>
                      <th class="py-1 text-right font-medium">平均耗时</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in visibleToolStats" :key="row.tool" class="border-t border-slate-200/70">
                      <td class="max-w-[220px] truncate py-1 font-mono text-slate-600" :title="row.tool">{{ row.tool }}</td>
                      <td class="py-1 text-right font-mono text-slate-700">{{ row.calls }}</td>
                      <td class="py-1 text-right font-mono" :class="row.fail ? 'text-rose-600' : 'text-slate-500'">{{ row.fail }}</td>
                      <td class="py-1 text-right font-mono text-slate-500">{{ row.avg }}ms</td>
                    </tr>
                  </tbody>
                </table>
                <button
                  v-if="restToolStats.length && !toolStatsOpen"
                  type="button"
                  class="mt-2 w-full rounded-md border border-dashed border-slate-300 py-1 text-[10px] text-slate-500 transition hover:text-indigo-600"
                  @click="toolStatsOpen = true"
                >
                  展开其余 {{ restToolStats.length }} 种工具 · {{ restToolCalls }} 次调用
                </button>
              </div>

              <div class="rounded-lg border border-slate-200 bg-slate-50 p-2.5">
                <p class="text-[11px] font-medium text-slate-700">慢步骤 / 异常</p>
                <div class="mt-2 space-y-1">
                  <div
                    v-for="item in slowSteps"
                    :key="`${item.turn}-${item.step.tool}-${item.step.ms}`"
                    class="flex items-center gap-2 text-[10px]"
                  >
                    <span class="flex-none font-mono font-semibold text-slate-500">T{{ item.turn }}</span>
                    <span class="min-w-0 flex-1 truncate" :class="item.step.status === 'warn' ? 'text-amber-700' : 'text-slate-600'" :title="`${item.step.name} · ${item.step.tool}`">
                      {{ item.step.name }} · {{ item.step.tool }}
                    </span>
                    <span class="flex-none font-mono text-slate-500">{{ item.step.ms }}ms</span>
                  </div>
                </div>
                <p class="mt-2 text-[10px] leading-relaxed text-slate-500">
                  <template v-if="chainWarnSteps.length">
                    {{ chainWarnSteps.length }} 次失败集中在 {{ chainWarnTurns.map((index) => `T${index}`).join(' / ') }}；其余轮次没有失败调用。
                  </template>
                  <template v-else>本次执行没有失败调用。</template>
                </p>
              </div>
            </div>
          </div>

          <div v-else class="mb-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] text-slate-500">
            <template v-if="model.turns.length">所有轮次的 <code class="font-mono">tool_calls</code> 都为空，页面不补造调用步骤。</template>
            <template v-else>本次执行没有逐轮记录，页面不补造调用步骤。</template>
          </div>

          <div class="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-[10px] text-slate-500">
            <TraceIcon name="code" :size="12" />
            <span v-if="detail.raw_request_response.length">原始报文按轮次留存，可在对话轮次的「证据」入口或此处打开。</span>
            <span v-else-if="detail.grading_mode === 'RUBRIC'">旧评分模式（RUBRIC）没有留存原始报文。</span>
            <span v-else>本次执行没有保存原始报文。</span>
            <span class="flex-1" />
            <button
              v-if="chainFocusTurn !== null"
              type="button"
              class="inline-flex items-center gap-1 text-indigo-600 hover:underline"
              @click="openDrawer(chainFocusTurn, 'evid', null, 'req')"
            >
              <TraceIcon name="code" :size="11" />
              查看请求原文
            </button>
            <button
              v-if="chainFocusTurn !== null"
              type="button"
              class="inline-flex items-center gap-1 text-indigo-600 hover:underline"
              @click="openDrawer(chainFocusTurn, 'evid', null, 'res')"
            >
              <TraceIcon name="chat" :size="11" />
              查看响应
            </button>
            <button type="button" class="inline-flex items-center gap-1 hover:text-indigo-600" @click="exportReport">
              <TraceIcon name="download" :size="11" />
              导出 trace
            </button>
          </div>

          <div class="space-y-3">
            <div
              v-for="item in detail.raw_request_response"
              :key="String(item.turn)"
              class="grid gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3 lg:grid-cols-2"
            >
              <div class="lg:col-span-2">
                <span class="font-mono text-xs font-semibold text-indigo-600">T{{ item.turn }}</span>
              </div>
              <JsonViewer :value="item.request" label="请求" max-height="260px" />
              <JsonViewer :value="item.response" label="响应" max-height="260px" />
            </div>
            <EmptyState v-if="detail.raw_request_response.length === 0" compact title="没有原始报文" />
          </div>
        </template>
      </div>

      <TurnDrawer
        :open="drawer.open"
        :turn="currentTurn"
        :turns="model.turns"
        :model="model"
        :detail="detail"
        :section="drawer.section"
        :evid-tab="drawer.evidTab"
        :focus-tag="drawer.focusTag"
        :open-rows="drawer.openRows"
        @close="closeDrawer"
        @section="setSection"
        @evid-tab="setEvidTab"
        @switch-turn="switchTurn"
        @toggle-row="toggleRow"
        @page="activeTab = 'selfcheck'"
        @focus-tag="focusTag"
      />
    </template>
  </section>
</template>

<style scoped>
.turn-item {
  min-width: 0;
}

/* 判分行「定位到对话」后的短暂高亮 */
.turn-flash {
  border-radius: 18px;
  box-shadow: 0 0 0 2px #c7d2fe;
  animation: turn-flash 1.8s ease-out;
}

@keyframes turn-flash {
  0% {
    background: #eef2ff;
    box-shadow: 0 0 0 3px #a5b4fc;
  }
  100% {
    background: transparent;
    box-shadow: 0 0 0 2px #c7d2fe;
  }
}
</style>
