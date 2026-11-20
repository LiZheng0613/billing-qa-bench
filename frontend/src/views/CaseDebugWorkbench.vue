<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import TestCaseDrawer from './components/TestCaseDrawer.vue'
import type {
  CasePayload,
  Environment,
  ExecutionDetail,
  ModelConfig,
  PromptListItem,
  TestCase,
} from '../types'
import { buildDebugSnapshotOverride } from '../utils/caseDebug'
import { errorMessage } from '../utils/errors'
import { formatMs, formatScore, sourceLabel, statusLabel } from '../utils/format'
import { useDebugStore } from '../stores/debug'
import { useToastStore } from '../stores/toast'

// KeepAlive 需要稳定的组件名：从轨迹页返回时保留当前草稿与试运行结果。
defineOptions({ name: 'CaseDebugWorkbench' })

interface WorkbenchRun {
  seq: number
  detail: ExecutionDetail
  finishedAt: number
}

interface FactRow {
  label: string
  expected: string
  actual: string
  passed: boolean
}

const SELF_CHECK_STATUS_META: Record<string, { label: string; className: string }> = {
  DONE: { label: '已追问', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  SKIPPED: { label: '未触发', className: 'border-slate-200 bg-slate-100 text-slate-600' },
  ERROR: { label: '追问失败', className: 'border-rose-200 bg-rose-50 text-rose-700' },
}

const route = useRoute()
const router = useRouter()
const toast = useToastStore()
const debugStore = useDebugStore()

const caseId = computed(() => Number(route.params.id))

const item = ref<TestCase | null>(null)
const environments = ref<Environment[]>([])
const templates = ref<PromptListItem[]>([])
const models = ref<ModelConfig[]>([])

const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
// 试运行状态归全局 store：切到其他页面 / 其他用例时按钮态仍然准确
const running = computed(() => debugStore.isRunning(caseId.value))
const runError = ref('')
const selfCheck = ref(false)
const environmentId = ref<number | null>(null)
const dirtyCount = ref(0)

const runs = ref<WorkbenchRun[]>([])
const expandedRunId = ref<number | null>(null)
const runSeq = ref(0)
const savedVersion = ref<number | null>(null)

const drawerOpen = ref(true)
const drawerRef = ref<InstanceType<typeof TestCaseDrawer> | null>(null)

// KeepAlive 返回时 route.params.id 会短暂为空；只记录真正加载过的用例，
// 避免离开轨迹页时清空草稿与试运行结果。
let loadedCaseId: number | null = null

const canReview = computed(() => {
  const current = item.value
  if (!current) return false
  return (
    current.grading_mode === 'ANSWER_CONTRACT' ||
    (!!current.review_status && current.review_status !== 'NOT_REQUIRED')
  )
})

const canFreeze = computed(
  () => item.value?.grading_mode === 'ANSWER_CONTRACT' && item.value.review_status === 'APPROVED',
)

const selectedEnvironment = computed(
  () => environments.value.find((env) => env.id === environmentId.value) ?? null,
)

async function loadAll(): Promise<void> {
  if (!Number.isFinite(caseId.value) || caseId.value <= 0) {
    loading.value = false
    loadError.value = '用例不存在或已删除'
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const [caseData, envPage, templatePage, modelPage] = await Promise.all([
      api.cases.get(caseId.value),
      api.environments.list({ page_size: 200 }),
      api.prompts.list({ page_size: 200 }),
      api.models.list({ page_size: 200 }),
    ])
    item.value = caseData
    environments.value = envPage.items
    templates.value = templatePage.items
    models.value = modelPage.items
    environmentId.value = caseData.environment_id ?? null
  } catch (exc) {
    item.value = null
    loadError.value = errorMessage(exc)
    toast.error(loadError.value)
  } finally {
    loading.value = false
  }
}

async function onTryRun(payload: CasePayload): Promise<void> {
  const current = item.value
  if (!current || running.value) return
  runError.value = ''
  savedVersion.value = null
  const runCaseId = current.id
  try {
    // 试运行生命周期交给全局 debug store：离开工作台后底部胶囊接管进度，
    // 完成后气泡可直接回到本工作台查看结果。
    const outcome = await debugStore.run({
      id: current.id,
      code: current.code,
      environmentId:
        payload.environment_id ?? environmentId.value ?? current.environment_id ?? null,
      enableSelfCheck: selfCheck.value,
      snapshotOverride: buildDebugSnapshotOverride(payload),
      draft: true,
    })
    if (!outcome.detail) {
      if (outcome.errorText) runError.value = outcome.errorText
      return
    }
    // 已切到别的用例：结果只回填发起它的那个工作台
    if (loadedCaseId !== runCaseId) return
    runSeq.value += 1
    runs.value = [
      { seq: runSeq.value, detail: outcome.detail, finishedAt: Date.now() },
      ...runs.value,
    ]
    expandedRunId.value = outcome.detail.id
  } catch (exc) {
    runError.value = errorMessage(exc)
    toast.error(runError.value)
  }
}

async function onSave(payload: CasePayload): Promise<void> {
  const current = item.value
  if (!current || saving.value) return
  saving.value = true
  try {
    const body: Partial<CasePayload> = { ...payload }
    body.code = undefined
    body.case_type = undefined
    const updated = await api.cases.update(current.id, body)
    item.value = updated
    savedVersion.value = updated.current_version
    environmentId.value = updated.environment_id ?? null
    toast.success(`用例 ${updated.code} 已保存至 v${updated.current_version}`)
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

/** 浮窗操作统一走内嵌表单的校验 + 提交逻辑，保证与原先抽屉行为一致。 */
function saveVersion(): void {
  if (saving.value || running.value) return
  drawerRef.value?.submit()
}

function tryRun(): void {
  if (saving.value || running.value) return
  drawerRef.value?.tryRun()
}

function toggleRun(id: number): void {
  expandedRunId.value = expandedRunId.value === id ? null : id
}

function onDrawerVisibility(value: boolean): void {
  drawerOpen.value = value
  // 内嵌表单没有可关闭的遮罩：关闭即视为放弃草稿，回到用例详情
  if (!value) goDetail()
}

function selfCheckMeta(detail: ExecutionDetail): { label: string; className: string } | null {
  const status = detail.self_check?.status
  if (!status) return null
  return (
    SELF_CHECK_STATUS_META[status] ?? {
      label: status,
      className: 'border-slate-200 bg-slate-100 text-slate-600',
    }
  )
}

/** 自检结论 / 跳过原因 / 错误信息的统一展示文本 */
function selfCheckBody(detail: ExecutionDetail): string {
  const record = detail.self_check
  if (!record) return ''
  if (record.status === 'DONE') return record.conclusion || '（Agent 未给出自检结论）'
  return record.skip_reason || record.error || ''
}

function judgeReason(detail: ExecutionDetail): string {
  const raw = detail.judge_result
  if (raw && typeof raw === 'object') {
    const reason = (raw as Record<string, unknown>).reason
    if (typeof reason === 'string' && reason.trim()) return reason
  }
  return ''
}

function factRows(detail: ExecutionDetail): FactRow[] {
  const raw = detail.fact_results
  if (!Array.isArray(raw)) return []
  return raw
    .filter((entry) => entry && typeof entry === 'object')
    .slice(0, 8)
    .map((entry) => {
      const fact = entry as Record<string, unknown>
      return {
        label: String(fact.label ?? fact.fact_id ?? '事实'),
        expected: formatFactValue(fact.expected),
        actual: formatFactValue(fact.actual),
        passed: Boolean(fact.passed),
      }
    })
}

function factSummary(detail: ExecutionDetail): string {
  const rows = factRows(detail)
  if (!rows.length) return ''
  return `${rows.filter((fact) => fact.passed).length} / ${rows.length} 命中`
}

function formatFactValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join('、')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function turnPreview(detail: ExecutionDetail): Array<{ index: number; query: string; answer: string }> {
  return (detail.turns ?? []).slice(0, 3).map((turn) => ({
    index: turn.turn_index,
    query: turn.query,
    answer: turn.final_answer ?? '',
  }))
}

function runThreshold(detail: ExecutionDetail): number | null {
  const raw = (detail.case_snapshot as Record<string, unknown> | undefined)?.pass_threshold
  const value = Number(raw ?? item.value?.pass_threshold)
  return Number.isFinite(value) ? value : null
}

function runGradingLabel(detail: ExecutionDetail): string {
  const mode = detail.grading_mode ?? detail.case_snapshot?.grading_mode
  return mode === 'ANSWER_CONTRACT' ? '答案契约' : '4 维评分'
}

function runSourceLabel(detail: ExecutionDetail): string {
  return detail.source === 'SANDBOX' ? '草稿试跑' : sourceLabel(detail.source)
}

function runTerminationLabel(detail: ExecutionDetail): string {
  return detail.termination_status ? statusLabel(detail.termination_status) : '正常结束'
}

function runSimulatorLabel(detail: ExecutionDetail): string {
  const name = item.value?.simulator_template_name
  const version = detail.simulator_template_version
  return [name, version ? `v${version}` : null].filter(Boolean).join(' · ') || '按默认'
}

function runJudgeLabel(detail: ExecutionDetail): string {
  const name = item.value?.judge_template_name
  const version = detail.judge_template_version
  const modelsUsed = (detail.judges ?? [])
    .map((judge) => judge.model_name)
    .filter(Boolean)
  const fallbackModels = (item.value?.judge_model_ids ?? [])
    .map((id) => models.value.find((model) => model.id === id)?.name ?? `#${id}`)
    .filter(Boolean)
  const modelText = [...new Set(modelsUsed.length ? modelsUsed : fallbackModels)].join('、')
  return [name, version ? `v${version}` : null, modelText || null].filter(Boolean).join(' · ') || '按默认'
}

function runTokens(detail: ExecutionDetail): string {
  const total = (detail.simulator_tokens ?? 0) + (detail.judge_tokens ?? 0)
  return total > 0 ? `${total.toLocaleString('zh-CN')} tokens` : ''
}

function openTrace(executionId: number, tab?: 'selfcheck'): void {
  // 同标签页跳转：保留浏览器历史，鼠标侧键 / 返回键可直接回到当前调试工作台。
  // 工作台由 AppShell 的 KeepAlive 缓存，返回后草稿、试运行结果与展开状态都还在。
  void router.push({
    name: 'trace',
    query: { execution: String(executionId), from: 'workbench', ...(tab ? { tab } : {}) },
  })
}

function goDetail(action?: 'review' | 'freeze'): void {
  // 离开工作台时恢复内嵌表单的可见态，KeepAlive 再次进入时不会留下空白卡片。
  drawerOpen.value = true
  void router.push({
    name: 'case-detail',
    params: { id: String(caseId.value) },
    query: action ? { action } : {},
  })
}

function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false })
}

// 同一用例从轨迹页返回时命中 KeepAlive，不重置草稿；切换到其他用例时重新加载。
watch(
  caseId,
  (id) => {
    if (!Number.isFinite(id) || id <= 0) return
    if (loadedCaseId === id) return
    loadedCaseId = id
    runs.value = []
    expandedRunId.value = null
    runSeq.value = 0
    runError.value = ''
    savedVersion.value = null
    selfCheck.value = false
    environmentId.value = null
    dirtyCount.value = 0
    drawerOpen.value = true
    void loadAll()
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-3">
    <button
      class="inline-flex shrink-0 items-center gap-1 text-xs text-slate-500 transition hover:text-ink"
      @click="goDetail()"
    >
      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="m15 18-6-6 6-6" />
      </svg>
      返回用例详情
    </button>

    <PageHeader class="shrink-0" eyebrow="Assets · 用例调试">
      <template #title>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <h1 class="page-title">调试工作台</h1>
          <span v-if="item" class="font-mono text-xs text-slate-500">{{ item.code }}</span>
          <StatusPill v-if="item" :status="item.status" size="sm" />
          <span class="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
            草稿 · 不保存版本
          </span>
          <span v-if="item" class="rounded-full border border-line bg-white px-2 py-0.5 text-[11px] text-slate-500">
            当前 v{{ item.current_version }}
          </span>
        </div>
      </template>
      <template #sub>
        <p class="page-sub">
          {{
            item
              ? `${item.name} · 修改任意内容后直接试运行，不生成版本、不影响评审状态`
              : '修改任意内容后直接试运行，不生成版本、不影响评审状态'
          }}
        </p>
      </template>
      <template #actions>
        <button class="btn-ghost" :disabled="!item" @click="goDetail()">查看用例详情</button>
      </template>
    </PageHeader>

    <div v-if="loading" class="card px-6 py-16 text-center text-sm text-slate-500">
      加载用例内容…
    </div>

    <div v-else-if="loadError || !item" class="card">
      <EmptyState title="用例不存在或已删除" :description="loadError || '请返回用例列表重新选择目标用例。'">
        <button class="btn-primary btn-xs mt-2" @click="router.replace({ name: 'cases' })">
          返回用例列表
        </button>
      </EmptyState>
    </div>

    <template v-else>
      <div class="relative min-h-0 flex-1">
        <div class="h-full overflow-y-auto pr-1 pb-40 xl:pb-28">
          <div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_440px]">
            <!-- 编辑区：所有分区常驻展开，不再折叠 -->
            <section class="card overflow-hidden">
              <TestCaseDrawer
                ref="drawerRef"
                :model-value="drawerOpen"
                embedded
                no-collapse
                no-scroll
                hide-footer
                hide-status
                hide-environment
                :case-data="item"
                :environments="environments"
                :templates="templates"
                :models="models"
                :saving="saving"
                :running="running"
                v-model:environment-id="environmentId"
                @update:model-value="onDrawerVisibility"
                @update:dirty-count="dirtyCount = $event"
                @submit="onSave"
                @try-run="onTryRun"
              />
            </section>

            <!-- 结果区：试运行配置不再重复卡片，只保留结果 -->
            <aside class="space-y-4 xl:sticky xl:top-0">
              <section class="card">
                <header class="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
                  <div>
                    <h2 class="panel-title">试运行结果</h2>
                    <p class="mt-0.5 text-[11px] text-slate-500">
                      {{
                        runs.length
                          ? `${runs.length} 次试运行 · 最新在前 · 不计入趋势`
                          : '本次会话内的试跑都会保留在这里'
                      }}
                    </p>
                  </div>
                  <span v-if="running" class="inline-flex items-center gap-1 text-[11px] text-indigo-600">
                    <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-500" aria-hidden="true" />
                    试运行中
                  </span>
                  <span v-else class="text-[11px] text-slate-400">{{ runs.length }} 次</span>
                </header>

                <div v-if="runs.length" class="space-y-3 px-3 py-3">
                  <article
                    v-for="run in runs"
                    :key="run.detail.id"
                    class="overflow-hidden rounded-xl border border-line-soft"
                  >
                    <button
                      type="button"
                      class="flex w-full items-center justify-between gap-2 bg-slate-50/70 px-3 py-2 text-left"
                      @click="toggleRun(run.detail.id)"
                    >
                      <span class="text-[11px] text-slate-500">
                        第 {{ run.seq }} 次试运行 · {{ formatTime(run.finishedAt) }}
                      </span>
                      <span class="flex items-center gap-2">
                        <StatusPill :status="run.detail.status" size="sm" />
                        <span class="text-xs font-semibold text-slate-800">
                          {{ formatScore(run.detail.total_score) }}
                          <span v-if="runThreshold(run.detail) !== null" class="text-[10px] font-normal text-slate-400">
                            / {{ runThreshold(run.detail) }}
                          </span>
                        </span>
                        <span class="text-[10px] text-slate-400">
                          {{ expandedRunId === run.detail.id ? '⌃' : '⌄' }}
                        </span>
                      </span>
                    </button>

                    <div v-if="expandedRunId === run.detail.id" class="space-y-3 px-3 py-3">
                      <!-- 执行元信息 -->
                      <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-slate-400">
                        <span>执行 #{{ run.detail.id }}</span>
                        <span aria-hidden="true">·</span>
                        <span>用例 v{{ run.detail.case_version }}</span>
                        <span aria-hidden="true">·</span>
                        <span>{{ runSourceLabel(run.detail) }}</span>
                        <span aria-hidden="true">·</span>
                        <span>{{ runTerminationLabel(run.detail) }}</span>
                        <span aria-hidden="true">·</span>
                        <span>{{ run.detail.attempt_no || 1 }} 次尝试</span>
                      </div>

                      <!-- 核心指标 -->
                      <div class="grid grid-cols-3 gap-2 rounded-xl border border-line-soft bg-canvas px-3 py-2">
                        <div>
                          <p class="text-[10px] text-slate-400">耗时</p>
                          <p class="mt-0.5 text-xs font-semibold text-slate-700">{{ formatMs(run.detail.latency_ms) }}</p>
                        </div>
                        <div>
                          <p class="text-[10px] text-slate-400">首字 TTFT</p>
                          <p class="mt-0.5 text-xs font-semibold text-slate-700">
                            {{ run.detail.ttft_ms != null ? formatMs(run.detail.ttft_ms) : '—' }}
                          </p>
                        </div>
                        <div>
                          <p class="text-[10px] text-slate-400">轮次</p>
                          <p class="mt-0.5 text-xs font-semibold text-slate-700">
                            {{ run.detail.turns_used }} / {{ run.detail.max_turns ?? '—' }} 轮
                          </p>
                        </div>
                      </div>

                      <!-- 执行配置 -->
                      <div class="grid grid-cols-2 gap-x-4 gap-y-2 rounded-xl border border-line-soft p-3">
                        <div>
                          <p class="text-[9px] text-slate-400">执行环境</p>
                          <p class="mt-0.5 break-words text-[11px] font-medium text-slate-700">
                            {{ run.detail.environment_name || '未绑定环境' }}
                          </p>
                        </div>
                        <div>
                          <p class="text-[9px] text-slate-400">判分方式</p>
                          <p class="mt-0.5 break-words text-[11px] font-medium text-slate-700">
                            {{ runGradingLabel(run.detail) }}
                            <template v-if="runThreshold(run.detail) !== null">
                              · 通过线 {{ runThreshold(run.detail) }}
                            </template>
                          </p>
                        </div>
                        <div>
                          <p class="text-[9px] text-slate-400">模拟器模板</p>
                          <p class="mt-0.5 break-words text-[11px] font-medium text-slate-700">
                            {{ runSimulatorLabel(run.detail) }}
                          </p>
                        </div>
                        <div>
                          <p class="text-[9px] text-slate-400">裁判模板 / 模型</p>
                          <p class="mt-0.5 break-words text-[11px] font-medium text-slate-700">
                            {{ runJudgeLabel(run.detail) }}
                          </p>
                        </div>
                      </div>

                      <!-- 失败追问自检 -->
                      <div
                        v-if="selfCheckMeta(run.detail)"
                        class="overflow-hidden rounded-xl border border-line-soft"
                      >
                        <div class="flex flex-wrap items-center gap-2 bg-slate-50/70 px-3 py-2">
                          <span
                            class="rounded-full border px-2 py-0.5 text-[10px] font-medium"
                            :class="selfCheckMeta(run.detail)?.className"
                          >
                            {{ selfCheckMeta(run.detail)?.label }}
                          </span>
                          <span class="text-[11px] font-medium text-slate-600">失败追问自检</span>
                          <span v-if="run.detail.self_check?.trigger" class="text-[10px] text-slate-400">
                            触发：{{ run.detail.self_check.trigger }}
                          </span>
                          <span
                            v-if="run.detail.self_check?.latency_ms != null"
                            class="ml-auto text-[10px] text-slate-400"
                          >
                            追问时延 {{ formatMs(run.detail.self_check.latency_ms) }}
                          </span>
                        </div>
                        <p
                          v-if="selfCheckBody(run.detail)"
                          class="whitespace-pre-wrap border-t border-line-soft px-3 py-2 text-[11px] leading-relaxed text-slate-600"
                        >
                          {{ selfCheckBody(run.detail) }}
                        </p>
                        <p
                          v-for="(note, index) in run.detail.self_check?.notes ?? []"
                          :key="index"
                          class="border-t border-line-soft px-3 py-1.5 text-[10px] text-amber-600"
                        >
                          {{ note }}
                        </p>
                        <div
                          v-if="run.detail.self_check?.status === 'DONE'"
                          class="border-t border-line-soft px-3 py-1.5"
                        >
                          <button
                            type="button"
                            class="text-[11px] font-medium text-indigo-600 transition hover:text-indigo-800"
                            @click="openTrace(run.detail.id, 'selfcheck')"
                          >
                            查看追问自检详情（Prompt / 请求响应）↗
                          </button>
                        </div>
                      </div>

                      <!-- 轮次摘要 -->
                      <div v-if="turnPreview(run.detail).length">
                        <div class="mb-1.5 flex items-center justify-between gap-2">
                          <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">轮次摘要</p>
                          <p class="text-[10px] text-slate-400">
                            {{ run.detail.turns_used }} 轮
                            <template v-if="runTokens(run.detail)"> · {{ runTokens(run.detail) }}</template>
                          </p>
                        </div>
                        <div class="space-y-1.5">
                          <div
                            v-for="turn in turnPreview(run.detail)"
                            :key="turn.index"
                            class="overflow-hidden rounded-xl border border-line-soft"
                          >
                            <div class="flex gap-2 border-b border-line-soft px-3 py-2 text-[11px] leading-relaxed">
                              <span class="w-10 shrink-0 text-[10px] font-medium text-slate-400">第 {{ turn.index }} 轮</span>
                              <p class="min-w-0 text-slate-500">{{ turn.query }}</p>
                            </div>
                            <div class="flex gap-2 px-3 py-2 text-[11px] leading-relaxed">
                              <span class="w-10 shrink-0 text-[10px] font-medium text-indigo-600">Agent</span>
                              <p class="min-w-0 text-slate-700">{{ turn.answer || '—' }}</p>
                            </div>
                          </div>
                        </div>
                      </div>

                      <!-- 事实判定 -->
                      <div v-if="factRows(run.detail).length">
                        <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                          事实判定 · {{ factSummary(run.detail) }}
                        </p>
                        <div class="overflow-hidden rounded-xl border border-line-soft">
                          <table class="table-shell">
                            <thead>
                              <tr>
                                <th class="th">事实</th>
                                <th class="th">期望</th>
                                <th class="th">实际</th>
                                <th class="th w-16">结果</th>
                              </tr>
                            </thead>
                            <tbody>
                              <tr v-for="(fact, index) in factRows(run.detail)" :key="index">
                                <td class="td text-xs">{{ fact.label }}</td>
                                <td class="td text-xs text-slate-500">{{ fact.expected }}</td>
                                <td class="td text-xs text-slate-500">{{ fact.actual }}</td>
                                <td class="td">
                                  <span
                                    class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                                    :class="fact.passed ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'"
                                  >
                                    {{ fact.passed ? '命中' : '未命中' }}
                                  </span>
                                </td>
                              </tr>
                            </tbody>
                          </table>
                        </div>
                      </div>

                      <!-- 裁判理由 -->
                      <div v-if="judgeReason(run.detail)">
                        <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">裁判理由</p>
                        <p class="rounded-xl border border-line-soft bg-canvas px-3 py-2 text-[11px] leading-relaxed text-slate-600">
                          {{ judgeReason(run.detail) }}
                        </p>
                      </div>

                      <p
                        v-if="run.detail.error_message"
                        class="rounded-xl bg-rose-50 px-3 py-2 text-[11px] leading-relaxed text-rose-700"
                      >
                        <span v-if="run.detail.error_kind" class="font-semibold">
                          {{ statusLabel(run.detail.error_kind) }} ·
                        </span>
                        {{ run.detail.error_message }}
                      </p>

                      <button
                        type="button"
                        class="text-[11px] font-medium text-indigo-600 transition hover:text-indigo-800"
                        @click="openTrace(run.detail.id)"
                      >
                        查看完整轨迹（SQL / 取数 / 裁判 CoT）↗
                      </button>
                    </div>
                  </article>
                </div>

                <div v-else class="px-4 py-3">
                  <EmptyState
                    compact
                    title="还没有试运行结果"
                    description="修改左侧内容后点击「试运行（不保存）」，结果会显示在这里，不生成版本、不影响评审状态。"
                  />
                </div>
              </section>

              <p v-if="runError" class="rounded-xl bg-rose-50 px-3 py-2 text-[11px] text-rose-700">
                {{ runError }}
              </p>

              <div
                v-if="savedVersion"
                class="rounded-xl border border-emerald-200 bg-emerald-50/70 px-3 py-3"
              >
                <p class="flex items-center gap-1.5 text-xs font-semibold text-emerald-800">
                  <span class="grid h-4 w-4 place-items-center rounded-full bg-emerald-500 text-[9px] text-white">✓</span>
                  已保存为 v{{ savedVersion }}
                </p>
                <p class="mt-1 text-[11px] leading-relaxed text-emerald-900/70">
                  本次试运行内容已固化为版本；如需继续流转，可直接评审或冻结。
                </p>
                <div class="mt-2 flex gap-2">
                  <button v-if="canReview" class="btn-ghost btn-xs flex-1 !border !border-emerald-200 !bg-white" @click="goDetail('review')">
                    评审
                  </button>
                  <button v-if="canFreeze" class="btn-ghost btn-xs flex-1 !border !border-emerald-200 !bg-white" @click="goDetail('freeze')">
                    冻结进数据集
                  </button>
                </div>
              </div>
            </aside>
          </div>
        </div>

        <!-- 浮窗操作栏：环境、自检开关、保存与试运行都在这里 -->
        <div
          class="absolute inset-x-3 bottom-4 z-20 rounded-[20px] border border-line bg-white/95 p-3 shadow-tile-lift backdrop-blur"
        >
          <div class="flex flex-wrap items-center gap-2.5">
            <div class="flex items-center gap-1.5 text-[11px] text-slate-600">
              <span
                class="h-1.5 w-1.5 rounded-full"
                :class="dirtyCount ? 'bg-amber-500' : 'bg-emerald-500'"
                aria-hidden="true"
              />
              {{ dirtyCount ? `${dirtyCount} 处未保存修改` : '与已保存版本一致' }}
            </div>

            <label class="flex items-center gap-1.5 text-[11px] text-slate-500">
              执行环境
              <select
                v-model="environmentId"
                class="input !h-8 !w-[180px] !py-0 text-[11px] leading-8"
                :title="selectedEnvironment?.name ?? '未绑定环境'"
              >
                <option :value="null">未绑定</option>
                <option v-for="env in environments" :key="env.id" :value="env.id" :title="env.name">
                  {{ env.code }}
                </option>
              </select>
            </label>

            <div class="flex h-8 items-center gap-2 rounded-xl border border-line bg-white px-2.5">
              <span class="text-[11px] text-slate-700">失败追问自检调试</span>
              <button
                type="button"
                role="switch"
                :aria-checked="selfCheck"
                class="relative h-4 w-7 rounded-full transition"
                :class="selfCheck ? 'bg-brand' : 'bg-slate-300'"
                title="LLM 裁决失败时，在同一会话追问 Agent 自检原因（多一轮 Agent 调用）"
                @click="selfCheck = !selfCheck"
              >
                <span
                  class="absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all"
                  :class="selfCheck ? 'left-3.5' : 'left-0.5'"
                />
              </button>
            </div>

            <span class="flex-1" />

            <div class="flex items-center gap-2">
              <button class="btn-ghost" :disabled="saving || running" @click="goDetail()">
                放弃修改
              </button>
              <button class="btn-ghost" :disabled="saving || running" @click="saveVersion">
                {{ saving ? '保存中…' : '保存为新版本' }}
              </button>
              <button class="btn-primary" :disabled="saving || running" @click="tryRun">
                {{
                  running
                    ? '试运行中…'
                    : selfCheck
                      ? '▶ 失败追问自检调试（不保存）'
                      : '▶ 试运行（不保存）'
                }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
