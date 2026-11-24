<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import CaseTraceInspector from './components/CaseTraceInspector.vue'
import type { BatchTask, ExecutionDetail, TaskProgressEvent } from '../types'
import { errorMessage } from '../utils/errors'
import { formatDateTime, formatMs } from '../utils/format'
import { useBreadcrumbStore } from '../stores/breadcrumb'
import type { BreadcrumbItem } from '../stores/breadcrumb'
import { useToastStore } from '../stores/toast'
import { useAutoRefresh } from '../composables/useAutoRefresh'
import { useTaskEventStream } from '../composables/useTaskEventStream'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()
const breadcrumb = useBreadcrumbStore()

const detail = ref<ExecutionDetail | null>(null)
const loadingDetail = ref(false)
const relationTask = ref<BatchTask | null>(null)
const loadError = ref('')

const executionId = computed(() => {
  const value = Number(route.query.execution)
  return Number.isFinite(value) && value > 0 ? value : null
})

const ACTIVE_EXECUTION_STATUSES = new Set(['PENDING', 'RUNNING'])/** 执行中的记录才需要持续拉取最新轮次与判分结果。 */
const executionActive = computed(
  () => Boolean(detail.value && ACTIVE_EXECUTION_STATUSES.has(detail.value.status)),
)

/** 页头副标题：与原型一致展示执行号 / 时间 / 模型 / 耗时 / 会话。 */
/** 被测模型只可能出现在请求体里；取不到时退回裁判模型，并明确标注「裁判模型」。 */
const headerAgentModel = computed(() => {
  for (const turn of detail.value?.turns ?? []) {
    const payload = turn.request_payload
    if (!payload || typeof payload !== 'object') continue
    const body = (payload as Record<string, unknown>).body
    const source = body && typeof body === 'object' ? (body as Record<string, unknown>) : (payload as Record<string, unknown>)
    const name = source.model ?? source.model_name ?? source.modelName
    if (typeof name === 'string' && name) return name
  }
  return ''
})
const headerJudgeModel = computed(
  () => detail.value?.judges.map((judge) => judge.model_name).filter(Boolean)[0] ?? '',
)
const headerModel = computed(() => headerAgentModel.value || headerJudgeModel.value)
const headerModelLabel = computed(() => (headerAgentModel.value ? '模型' : '裁判模型'))
const headerSession = computed(() => detail.value?.turns[0]?.session_id ?? '')

// 从用例详情 / 调试工作台进入时优先展示用例路径，否则按批量任务路径展示。
const origin = computed<'task' | 'case' | 'workbench'>(() => {
  if (route.query.from === 'workbench') return 'workbench'
  if (route.query.from === 'case') return 'case'
  return 'task'
})

// 调试完成跳转可带 tab=selfcheck，让执行详情直接落在「追问自检」页签。
const initialTab = computed(() => {
  const value = route.query.tab
  return typeof value === 'string' && value ? value : null
})

function syncBreadcrumb(data: ExecutionDetail | null): void {
  if (!data) {
    breadcrumb.clear()
    return
  }
  const trail: BreadcrumbItem[] = []
  if ((origin.value === 'case' || origin.value === 'workbench') && data.case_id) {
    trail.push({ label: '用例资产', to: '/cases' })
    trail.push({ label: '用例详情', to: `/cases/${data.case_id}` })
    if (origin.value === 'workbench') {
      trail.push({ label: '调试工作台', to: `/cases/${data.case_id}/debug` })
    }
  } else if (data.task_id) {
    trail.push({ label: '批量任务', to: '/tasks' })
    trail.push({ label: '任务详情', to: `/tasks/${data.task_id}` })
  } else if (data.case_id) {
    trail.push({ label: '用例资产', to: '/cases' })
    trail.push({ label: '用例详情', to: `/cases/${data.case_id}` })
  }
  trail.push({ label: '单笔执行结果' })
  breadcrumb.set(trail)
}

async function loadDetail(id: number, options: { silent?: boolean } = {}): Promise<boolean> {
  const { silent = false } = options
  if (!silent) {
    loadingDetail.value = true
    loadError.value = ''
  }
  try {
    const data = await api.executions.get(id)
    detail.value = data
    if (!silent || !relationTask.value) {
      relationTask.value = data.task_id
        ? await api.tasks.get(data.task_id).catch(() => null)
        : null
    }
    if (!silent) syncBreadcrumb(data)
    return true
  } catch (exc) {
    // 自动刷新失败时保留上一次成功的数据，避免界面被清空
    if (!silent) {
      detail.value = null
      relationTask.value = null
      loadError.value = errorMessage(exc)
      syncBreadcrumb(null)
      toast.error(loadError.value)
    }
    return false
  } finally {
    if (!silent) loadingDetail.value = false
  }
}

function openTask(): void {
  if (detail.value?.task_id) {
    void router.push({ name: 'task-detail', params: { id: String(detail.value.task_id) } })
  }
}

function openCase(): void {
  if (detail.value?.case_id) {
    void router.push({ name: 'case-detail', params: { id: String(detail.value.case_id) } })
  }
}

function openWorkbench(): void {
  if (detail.value?.case_id) {
    void router.push({ name: 'case-debug', params: { id: String(detail.value.case_id) } })
  }
}

function back(): void {
  if (window.history.length > 1) router.back()
  else void router.push({ name: 'dashboard' })
}

// 顶部左上角只保留一个返回入口：按进入来源回到用例详情或所属批量任务详情。
const backLabel = computed(() => {
  if (origin.value === 'workbench') return '返回调试工作台'
  if (origin.value === 'case') return '返回用例详情'
  if (detail.value?.task_id) return '返回批量任务详情'
  if (detail.value?.case_id) return '返回用例详情'
  return '返回'
})

function goBack(): void {
  if (origin.value === 'workbench' && detail.value?.case_id) openWorkbench()
  else if (origin.value === 'case' && detail.value?.case_id) openCase()
  else if (detail.value?.task_id) openTask()
  else if (detail.value?.case_id) openCase()
  else back()
}

async function refreshDetail(): Promise<void> {
  if (!executionId.value) return
  if (await loadDetail(executionId.value)) toast.success('刷新成功')
}

watch(
  executionId,
  (id) => {
    if (id === null) {
      detail.value = null
      relationTask.value = null
      loadError.value = ''
      syncBreadcrumb(null)
      return
    }
    if (detail.value?.id === id) return
    void loadDetail(id)
  },
  { immediate: true },
)

// 执行中的轮次/判分/自检没有轮次级事件，靠 4 秒轮询刷新，进入终态后自动停止。
async function refreshDetailSilently(): Promise<void> {
  if (!executionId.value) return
  await loadDetail(executionId.value, { silent: true })
}

useAutoRefresh(refreshDetailSilently, {
  interval: 4000,
  shouldSkip: () => !executionActive.value,
})

// WebSocket 事件只在「用例完成」时发布：命中当前执行时立即拉一次终态，
// 结果不用等下一个轮询周期。同任务其他用例的事件与当前执行无关，忽略。
function applyExecutionEvent(event: TaskProgressEvent): void {
  if (!event || event.type === 'ping') return
  const current = detail.value
  if (!current || event.execution?.id !== current.id) return
  if (executionId.value) void loadDetail(executionId.value, { silent: true })
}

useTaskEventStream(applyExecutionEvent)

onBeforeUnmount(() => {
  breadcrumb.clear()
})
</script>

<template>
  <div class="space-y-4">
    <template v-if="executionId">
      <button
        class="inline-flex items-center gap-1 text-xs text-slate-500 transition hover:text-ink"
        @click="goBack"
      >
        <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="m15 18-6-6 6-6" />
        </svg>
        {{ backLabel }}
      </button>

      <PageHeader eyebrow="Case execution · 单笔执行结果">
        <template #title>
          <h1 class="mt-2 flex flex-wrap items-center gap-2 text-[26px] font-semibold leading-tight tracking-[-0.02em] text-ink">
            {{ detail?.case_name || `执行 #${detail?.id ?? executionId}` }}
            <button
              v-if="detail?.case_id"
              type="button"
              class="font-mono text-sm font-medium text-slate-600 underline decoration-dotted underline-offset-2 transition hover:text-indigo-600"
              title="查看用例详情"
              @click="openCase"
            >
              {{ detail.case_code || `用例 #${detail.case_id}` }}
            </button>
            <StatusPill v-if="detail" :status="detail.status" size="sm" />
            <span
              v-if="detail?.source === 'SANDBOX'"
              class="rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[10px] font-medium text-indigo-700"
            >
              草稿试跑
            </span>
          </h1>
        </template>
        <template #sub>
          <p class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600">
            <span>执行 #{{ detail?.id ?? executionId }}</span>
            <template v-if="detail?.started_at">
              <span class="text-slate-300">·</span>
              <span>{{ formatDateTime(detail.started_at) }}</span>
            </template>
            <template v-if="headerModel">
              <span class="text-slate-300">·</span>
              <span>{{ headerModelLabel }} <span class="font-mono text-slate-700">{{ headerModel }}</span></span>
            </template>
            <template v-if="detail?.latency_ms !== null && detail?.latency_ms !== undefined">
              <span class="text-slate-300">·</span>
              <span>总耗时 {{ formatMs(detail.latency_ms) }}</span>
            </template>
            <template v-if="headerSession">
              <span class="text-slate-300">·</span>
              <span>会话 <span class="font-mono text-slate-700">{{ headerSession }}</span></span>
            </template>
          </p>
          <p class="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-600">
            <span v-if="detail?.environment_name">
              实际执行环境
              <span class="rounded-md border border-indigo-100 bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-700">
                {{ detail.environment_name }}
              </span>
            </span>
            <span v-if="relationTask">
              所属批量任务
              <button class="font-mono text-indigo-600 underline decoration-dotted" @click="openTask">
                {{ relationTask.code }}
              </button>
              {{ relationTask.name ? `· ${relationTask.name}` : '' }}
            </span>
            <span v-else-if="detail">
              {{ detail.source === 'SANDBOX' ? '草稿试跑记录（不进入趋势统计）' : '单笔调试 / 重跑记录' }}
            </span>
          </p>
        </template>
        <template #actions>
          <span
            v-if="executionActive"
            class="inline-flex items-center gap-1 text-[11px] text-emerald-600"
            title="执行中每 4 秒自动刷新，完成后立即更新"
          >
            <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" aria-hidden="true" />
            执行中 · 自动刷新
          </span>
          <button class="btn-ghost" @click="refreshDetail">刷新</button>
        </template>
      </PageHeader>

      <div v-if="loadError && !detail" class="card">
        <EmptyState title="执行记录不存在或已删除" :description="loadError">
          <button class="btn-primary btn-xs mt-2" @click="router.push({ name: 'dashboard' })">
            返回测试大盘
          </button>
        </EmptyState>
      </div>

      <CaseTraceInspector
        v-else
        :detail="detail"
        :loading="loadingDetail"
        :initial-tab="initialTab"
      />
    </template>

    <div v-else class="card">
      <EmptyState
        title="请选择一条执行记录"
        description="执行记录入口已合并到批量任务详情与用例详情；在任务详情的用例结果或用例详情的历史记录中点击即可查看单笔执行。"
      >
        <div class="mt-3 flex flex-wrap items-center justify-center gap-2">
          <button class="btn-primary btn-xs" @click="router.push({ name: 'tasks' })">前往批量任务</button>
          <button class="btn-ghost btn-xs" @click="router.push({ name: 'cases' })">前往用例资产</button>
        </div>
      </EmptyState>
    </div>
  </div>
</template>
