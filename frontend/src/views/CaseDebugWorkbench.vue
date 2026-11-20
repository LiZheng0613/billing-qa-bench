<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
import { formatMs, formatScore, statusLabel } from '../utils/format'
import { useDebugStore } from '../stores/debug'
import { useToastStore } from '../stores/toast'

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
const running = ref(false)
const runError = ref('')
const selfCheck = ref(false)

const runs = ref<WorkbenchRun[]>([])
const expandedRunId = ref<number | null>(null)
const runSeq = ref(0)
const savedVersion = ref<number | null>(null)

const drawerOpen = ref(true)

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
  if (!current || running.value || debugStore.isRunning(current.id)) return
  running.value = true
  runError.value = ''
  savedVersion.value = null
  try {
    const detail = await api.cases.debug(current.id, {
      environment_id: payload.environment_id ?? current.environment_id ?? null,
      enable_self_check: selfCheck.value,
      case_snapshot_override: buildDebugSnapshotOverride(payload),
      draft: true,
    })
    runSeq.value += 1
    runs.value = [{ seq: runSeq.value, detail, finishedAt: Date.now() }, ...runs.value]
    expandedRunId.value = detail.id
    toast.success(
      `试运行完成：${statusLabel(detail.status)}，得分 ${detail.total_score ?? '—'}`,
    )
  } catch (exc) {
    runError.value = errorMessage(exc)
    toast.error(runError.value)
  } finally {
    running.value = false
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
    toast.success(`用例 ${updated.code} 已保存至 v${updated.current_version}`)
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

function toggleRun(id: number): void {
  expandedRunId.value = expandedRunId.value === id ? null : id
}

function onDrawerVisibility(value: boolean): void {
  drawerOpen.value = value
  // 内嵌表单没有可关闭的遮罩：关闭即视为放弃草稿，回到用例详情
  if (!value) goDetail()
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

function openTrace(executionId: number): void {
  const href = router.resolve({
    name: 'trace',
    query: { execution: String(executionId), from: 'case' },
  }).href
  window.open(href, '_blank', 'noopener')
}

function goDetail(action?: 'review' | 'freeze'): void {
  void router.push({
    name: 'case-detail',
    params: { id: String(caseId.value) },
    query: action ? { action } : {},
  })
}

function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false })
}

onMounted(() => {
  void loadAll()
})
</script>

<template>
  <div class="space-y-4">
    <button
      class="inline-flex items-center gap-1 text-xs text-slate-500 transition hover:text-ink"
      @click="goDetail()"
    >
      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="m15 18-6-6 6-6" />
      </svg>
      返回用例详情
    </button>

    <PageHeader eyebrow="Assets · 用例调试">
      <template #title>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <h1 class="page-title">调试工作台</h1>
          <span v-if="item" class="font-mono text-xs text-slate-500">{{ item.code }}</span>
          <span class="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
            草稿 · 不保存版本
          </span>
        </div>
      </template>
      <template #sub>
        <p class="page-sub">
          {{
            item
              ? `${item.name} · 当前版本 v${item.current_version} · 修改任意内容后直接试运行，不生成版本、不影响评审状态`
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

    <div
      v-else
      class="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(360px,420px)]"
    >
      <section class="card flex h-[calc(100vh-13rem)] min-h-[640px] flex-col overflow-hidden">
        <TestCaseDrawer
          :model-value="drawerOpen"
          embedded
          :case-data="item"
          :environments="environments"
          :templates="templates"
          :models="models"
          :saving="saving"
          :running="running"
          @update:model-value="onDrawerVisibility"
          @submit="onSave"
          @try-run="onTryRun"
        />
      </section>

      <aside class="card xl:sticky xl:top-20">
        <header class="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
          <div>
            <h2 class="panel-title">试运行结果</h2>
            <p class="mt-0.5 text-[11px] text-slate-500">
              {{ runs.length ? `${runs.length} 次试运行 · 最新在前` : '本次会话内的试跑都会保留在这里' }}
            </p>
          </div>
          <span v-if="running" class="inline-flex items-center gap-1 text-[11px] text-indigo-600">
            <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-500" aria-hidden="true" />
            试运行中
          </span>
          <span v-else class="text-[11px] text-slate-400">{{ runs.length }} 次</span>
        </header>

        <div class="flex flex-wrap items-center gap-3 border-b border-line-soft px-4 py-2.5">
          <label class="flex cursor-pointer items-center gap-2 text-[11px] text-slate-600">
            <input v-model="selfCheck" type="checkbox" class="h-3.5 w-3.5 rounded border-slate-300" />
            失败追问自检
          </label>
          <span class="ml-auto text-[10px] text-slate-400">草稿试跑不进入趋势统计</span>
        </div>

        <div v-if="runs.length" class="max-h-[calc(100vh-24rem)] space-y-3 overflow-y-auto px-4 py-3">
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
                </span>
                <span class="text-[10px] text-slate-400">
                  {{ expandedRunId === run.detail.id ? '⌃' : '⌄' }}
                </span>
              </span>
            </button>

            <div v-if="expandedRunId === run.detail.id" class="space-y-3 px-3 py-3">
              <div class="flex flex-wrap items-center gap-4 rounded-lg border border-line-soft bg-canvas px-3 py-2">
                <div>
                  <p class="text-[10px] text-slate-400">耗时</p>
                  <p class="text-xs font-semibold text-slate-700">{{ formatMs(run.detail.latency_ms) }}</p>
                </div>
                <div>
                  <p class="text-[10px] text-slate-400">轮次</p>
                  <p class="text-xs font-semibold text-slate-700">{{ run.detail.turns_used }} 轮</p>
                </div>
                <div>
                  <p class="text-[10px] text-slate-400">判定</p>
                  <p class="text-xs font-semibold text-slate-700">
                    {{ run.detail.pass_flag ? '通过' : '未通过' }}
                  </p>
                </div>
                <div v-if="run.detail.self_check?.status" class="ml-auto">
                  <p class="text-[10px] text-slate-400">追问自检</p>
                  <p class="text-xs font-semibold text-slate-700">{{ run.detail.self_check.status }}</p>
                </div>
              </div>

              <div v-if="factRows(run.detail).length">
                <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">事实判定</p>
                <div class="overflow-hidden rounded-lg border border-line-soft">
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

              <div v-if="turnPreview(run.detail).length">
                <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">轮次摘要</p>
                <div class="space-y-1.5">
                  <details
                    v-for="turn in turnPreview(run.detail)"
                    :key="turn.index"
                    class="rounded-lg border border-line-soft bg-white"
                    :open="turn.index === 1"
                  >
                    <summary
                      class="cursor-pointer list-none px-2.5 py-1.5 text-[11px] text-slate-600 [&::-webkit-details-marker]:hidden"
                    >
                      第 {{ turn.index }} 轮
                    </summary>
                    <div class="space-y-1 border-t border-line-soft px-2.5 py-2 text-[11px] leading-relaxed">
                      <p class="text-slate-400">用户：{{ turn.query }}</p>
                      <p class="text-slate-700">Agent：{{ turn.answer || '—' }}</p>
                    </div>
                  </details>
                </div>
              </div>

              <div v-if="judgeReason(run.detail)">
                <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">裁判理由</p>
                <p class="rounded-lg border border-line-soft bg-canvas px-3 py-2 text-[11px] leading-relaxed text-slate-600">
                  {{ judgeReason(run.detail) }}
                </p>
              </div>

              <p v-if="run.detail.error_message" class="rounded-lg bg-rose-50 px-3 py-2 text-[11px] text-rose-700">
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

        <div v-else class="px-4">
          <EmptyState
            compact
            title="还没有试运行结果"
            description="修改左侧内容后点击「试运行（不保存）」，结果会显示在这里，不生成版本、不影响评审状态。"
          />
        </div>

        <p v-if="runError" class="mx-4 mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[11px] text-rose-700">
          {{ runError }}
        </p>

        <div
          v-if="savedVersion"
          class="mx-4 mb-4 rounded-xl border border-emerald-200 bg-emerald-50/70 px-3 py-3"
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
</template>
