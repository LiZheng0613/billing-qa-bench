<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import StatusPill from '../components/StatusPill.vue'
import CaseTraceInspector from './components/CaseTraceInspector.vue'
import type { ExecutionDetail, ExecutionListItem } from '../types'
import { errorMessage } from '../utils/errors'
import {
  formatDateTime,
  formatMs,
  formatScore,
  sourceLabel,
} from '../utils/format'
import { useToastStore } from '../stores/toast'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()

const executions = ref<ExecutionListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(30)
const statusFilter = ref('')
const sourceFilter = ref('')
const caseIdFilter = ref<number | null>(null)
const taskIdFilter = ref<number | null>(null)
const loadingList = ref(false)

const detail = ref<ExecutionDetail | null>(null)
const loadingDetail = ref(false)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

async function loadList(): Promise<void> {
  loadingList.value = true
  try {
    const pageData = await api.executions.list({
      page: page.value,
      page_size: pageSize.value,
      status: statusFilter.value || undefined,
      source: sourceFilter.value || undefined,
      case_id: caseIdFilter.value ?? undefined,
      task_id: taskIdFilter.value ?? undefined,
    })
    executions.value = pageData.items
    total.value = pageData.total
    if (executions.value.length === 0 && page.value > 1) {
      page.value -= 1
      await loadList()
    }
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loadingList.value = false
  }
}

async function selectExecution(executionId: number): Promise<void> {
  loadingDetail.value = true
  try {
    detail.value = await api.executions.get(executionId)
    if (Number(route.query.execution) !== executionId) {
      const query = { ...route.query, execution: String(executionId) }
      await router.replace({ query })
    }
  } catch (exc) {
    toast.error(errorMessage(exc))
    detail.value = null
  } finally {
    loadingDetail.value = false
  }
}

async function applyFilters(): Promise<void> {
  page.value = 1
  await loadList()
  if (executions.value[0]) await selectExecution(executions.value[0].id)
  else detail.value = null
}

async function resetFilters(): Promise<void> {
  statusFilter.value = ''
  sourceFilter.value = ''
  caseIdFilter.value = null
  taskIdFilter.value = null
  page.value = 1
  await loadList()
  if (executions.value[0]) await selectExecution(executions.value[0].id)
  else detail.value = null
}

async function refreshDetail(): Promise<void> {
  if (detail.value) await selectExecution(detail.value.id)
}

function goPage(next: number): void {
  if (next < 1 || next > pageCount.value) return
  page.value = next
  void loadList()
}

onMounted(async () => {
  const query = route.query
  if (query.case) caseIdFilter.value = Number(query.case)
  if (query.task) taskIdFilter.value = Number(query.task)
  if (query.status) statusFilter.value = String(query.status)
  if (query.source) sourceFilter.value = String(query.source)
  await loadList()
  const requested = query.execution ? Number(query.execution) : null
  const fallback = executions.value[0]?.id ?? null
  if (requested) await selectExecution(requested)
  else if (fallback) await selectExecution(fallback)
})

watch(
  () => route.query.execution,
  (value) => {
    if (!value) return
    const executionId = Number(value)
    if (!Number.isFinite(executionId)) return
    if (detail.value?.id === executionId) return
    void selectExecution(executionId)
  },
)

watch(
  () => [route.query.case, route.query.task] as const,
  ([caseQuery, taskQuery]) => {
    const nextCase = caseQuery ? Number(caseQuery) : null
    const nextTask = taskQuery ? Number(taskQuery) : null
    if (nextCase === caseIdFilter.value && nextTask === taskIdFilter.value) return
    caseIdFilter.value = nextCase
    taskIdFilter.value = nextTask
    void applyFilters()
  },
)
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold text-slate-100">执行追踪</h2>
        <p class="mt-1 text-xs text-slate-500">
          批量 / 调试 / 重跑执行的完整链路：对话轮次、裁判明细、规则校验、用例快照与原始报文。
        </p>
      </div>
      <button class="btn-ghost" @click="refreshDetail">刷新详情</button>
    </div>

    <div class="card flex flex-wrap items-end gap-3 px-4 py-3">
      <div class="w-40">
        <label class="label">执行状态</label>
        <select v-model="statusFilter" class="input" @change="applyFilters">
          <option value="">全部状态</option>
          <option value="PASS">通过</option>
          <option value="FAIL">失败</option>
          <option value="ERROR">异常</option>
          <option value="MAX_TURNS_EXCEEDED">轮次超限</option>
          <option value="REPETITION_ABORT">复读熔断</option>
          <option value="ABORTED">已终止</option>
        </select>
      </div>
      <div class="w-36">
        <label class="label">执行来源</label>
        <select v-model="sourceFilter" class="input" @change="applyFilters">
          <option value="">全部来源</option>
          <option value="BATCH">批量</option>
          <option value="RETRY">失败重跑</option>
          <option value="DEBUG">单条调试</option>
          <option value="SANDBOX">沙盒</option>
        </select>
      </div>
      <div class="w-32">
        <label class="label">用例 ID</label>
        <input v-model.number="caseIdFilter" class="input" type="number" min="1" placeholder="全部" @keyup.enter="applyFilters" />
      </div>
      <div class="w-32">
        <label class="label">任务 ID</label>
        <input v-model.number="taskIdFilter" class="input" type="number" min="1" placeholder="全部" @keyup.enter="applyFilters" />
      </div>
      <button class="btn-primary" @click="applyFilters">查询</button>
      <button class="btn-ghost" @click="resetFilters">重置</button>
    </div>

    <div class="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
      <section class="card flex max-h-[760px] flex-col overflow-hidden">
        <header class="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <span class="panel-title">执行记录</span>
          <span class="text-[11px] text-slate-500">共 {{ total }} 条</span>
        </header>
        <div class="flex-1 overflow-y-auto">
          <button
            v-for="item in executions"
            :key="item.id"
            class="w-full border-b border-slate-800/60 px-4 py-3 text-left transition hover:bg-slate-800/40"
            :class="item.id === detail?.id ? 'bg-indigo-500/10' : ''"
            @click="selectExecution(item.id)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="font-mono text-[11px] text-indigo-300">#{{ item.id }}</span>
              <StatusPill :status="item.status" size="sm" />
            </div>
            <p class="mt-1 truncate font-mono text-[11px] text-slate-300">{{ item.case_code || '—' }}</p>
            <p class="truncate text-[11px] text-slate-500">{{ item.case_name || '—' }}</p>
            <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-slate-600">
              <span>v{{ item.case_version }}</span>
              <span>{{ sourceLabel(item.source) }}</span>
              <span>得分 {{ formatScore(item.total_score) }}</span>
              <span>{{ formatMs(item.latency_ms) }}</span>
            </div>
            <p class="mt-0.5 text-[10px] text-slate-600">{{ formatDateTime(item.started_at) }}</p>
          </button>
          <EmptyState
            v-if="executions.length === 0 && !loadingList"
            compact
            title="没有执行记录"
            description="运行一次批量任务或单条调试后再来查看。"
          />
        </div>
        <div class="flex items-center justify-between border-t border-slate-800 px-3 py-2">
          <span class="text-[10px] text-slate-500">{{ page }} / {{ pageCount }}</span>
          <div class="flex gap-1">
            <button class="btn-ghost btn-xs" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
            <button class="btn-ghost btn-xs" :disabled="page >= pageCount" @click="goPage(page + 1)">下一页</button>
          </div>
        </div>
      </section>

      <CaseTraceInspector
        :detail="detail"
        :loading="loadingDetail"
        @refresh="refreshDetail"
      />
    </div>
  </div>
</template>
