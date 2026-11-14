<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import StatusPill from '../components/StatusPill.vue'
import CaseTraceInspector from './components/CaseTraceInspector.vue'
import type { BatchTask, ExecutionDetail, ExecutionListItem } from '../types'
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
const relationTask = ref<BatchTask | null>(null)
const filterTask = ref<BatchTask | null>(null)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const singleMode = computed(() => Boolean(route.query.execution))
const caseCode = computed(
  () => detail.value?.case_code ?? executions.value[0]?.case_code ?? null,
)

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
    filterTask.value = null
    if (taskIdFilter.value) {
      filterTask.value = await api.tasks.get(taskIdFilter.value).catch(() => null)
    }
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

async function loadDetail(executionId: number): Promise<void> {
  loadingDetail.value = true
  try {
    const data = await api.executions.get(executionId)
    detail.value = data
    relationTask.value = data.task_id
      ? await api.tasks.get(data.task_id).catch(() => null)
      : null
  } catch (exc) {
    toast.error(errorMessage(exc))
    detail.value = null
    relationTask.value = null
  } finally {
    loadingDetail.value = false
  }
}

function selectExecution(executionId: number): void {
  void router.push({ query: { ...route.query, execution: String(executionId) } })
}

function backToList(): void {
  const query: Record<string, string> = {}
  for (const [key, value] of Object.entries(route.query)) {
    if (key === 'execution') continue
    if (typeof value === 'string') query[key] = value
  }
  void router.push({ query })
}

function openBreadcrumb(target: 'task' | 'case'): void {
  if (target === 'task' && detail.value?.task_id) {
    void router.push({ name: 'dashboard', query: { task: String(detail.value.task_id) } })
    return
  }
  if (target === 'case' && detail.value?.case_id) {
    void router.push({ name: 'trace', query: { case: String(detail.value.case_id) } })
  }
}

function openTaskRecords(): void {
  if (taskIdFilter.value) {
    void router.push({ name: 'trace', query: { task: String(taskIdFilter.value) } })
  }
}

function clearCaseFilter(): void {
  const query: Record<string, string> = {}
  for (const [key, value] of Object.entries(route.query)) {
    if (key === 'case') continue
    if (typeof value === 'string') query[key] = value
  }
  void router.push({ query })
}

async function applyFilters(): Promise<void> {
  page.value = 1
  await loadList()
}

async function resetFilters(): Promise<void> {
  statusFilter.value = ''
  sourceFilter.value = ''
  caseIdFilter.value = null
  taskIdFilter.value = null
  page.value = 1
  await router.replace({ name: 'trace', query: {} })
  await loadList()
}

async function refreshDetail(): Promise<void> {
  if (detail.value) await loadDetail(detail.value.id)
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
  if (query.execution) await loadDetail(Number(query.execution))
})

watch(
  () => route.query.execution,
  (value) => {
    if (!value) {
      detail.value = null
      relationTask.value = null
      return
    }
    const executionId = Number(value)
    if (!Number.isFinite(executionId)) return
    if (detail.value?.id === executionId) return
    void loadDetail(executionId)
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
    <template v-if="!singleMode">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-lg font-semibold text-slate-100">执行记录</h2>
          <p class="mt-1 text-xs text-slate-500">
            批量 / 调试 / 重跑产生的全部执行记录；点击任意记录进入单笔执行结果页。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button
            v-if="filterTask"
            class="btn-ghost btn-xs"
            @click="openTaskRecords"
          >
            返回批量任务详情
          </button>
          <button v-if="caseIdFilter" class="btn-ghost btn-xs" @click="clearCaseFilter">
            清除用例筛选
          </button>
        </div>
      </div>

      <div
        v-if="caseIdFilter || taskIdFilter"
        class="rounded-xl border border-indigo-500/30 bg-indigo-500/10 px-4 py-2.5 text-xs text-indigo-200"
      >
        <span v-if="caseIdFilter">
          用例历史：<span class="font-mono font-semibold">{{ caseCode || `#${caseIdFilter}` }}</span>
          的全部执行记录（含批量、调试、重跑），共 {{ total }} 条。
        </span>
        <span v-else-if="taskIdFilter">
          批量任务
          <span class="font-mono font-semibold">{{ filterTask?.code || `#${taskIdFilter}` }}</span>
          {{ filterTask?.name ? `· ${filterTask.name}` : '' }}
          的全部执行记录（含每次重试），共 {{ total }} 条。
          <button class="ml-2 underline decoration-dotted" @click="openTaskRecords">
            打开批量任务详情
          </button>
        </span>
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

      <section class="card overflow-hidden">
        <header class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
          <span class="panel-title">执行记录列表</span>
          <span class="text-[11px] text-slate-500">共 {{ total }} 条 · 点击行查看单笔执行结果</span>
        </header>
        <div class="overflow-x-auto">
          <table class="table-shell">
            <thead>
              <tr>
                <th class="th">执行号</th>
                <th class="th">用例</th>
                <th class="th">来源</th>
                <th class="th">状态</th>
                <th class="th">得分</th>
                <th class="th">轮次</th>
                <th class="th">尝试</th>
                <th class="th">时延</th>
                <th class="th">所属任务</th>
                <th class="th">开始时间</th>
                <th class="th text-right">结果</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in executions"
                :key="item.id"
                class="cursor-pointer"
                @click="selectExecution(item.id)"
              >
                <td class="td font-mono text-xs text-indigo-300">#{{ item.id }}</td>
                <td class="td">
                  <p class="font-mono text-xs text-slate-200">{{ item.case_code || '—' }}</p>
                  <p class="mt-0.5 max-w-[220px] truncate text-[11px] text-slate-500">
                    {{ item.case_name || '—' }} · v{{ item.case_version }}
                  </p>
                </td>
                <td class="td text-xs text-slate-400">{{ sourceLabel(item.source) }}</td>
                <td class="td"><StatusPill :status="item.status" size="sm" /></td>
                <td class="td tabular-nums">{{ formatScore(item.total_score) }}</td>
                <td class="td tabular-nums">{{ item.turns_used }}</td>
                <td class="td tabular-nums">
                  <span :class="item.attempt_no > 1 ? 'text-amber-300' : 'text-slate-400'">
                    {{ item.attempt_no }}
                  </span>
                </td>
                <td class="td tabular-nums">{{ formatMs(item.latency_ms) }}</td>
                <td class="td text-xs text-slate-500">
                  <span v-if="item.task_id" class="font-mono">#{{ item.task_id }}</span>
                  <span v-else class="text-slate-600">调试 / 单条</span>
                </td>
                <td class="td text-xs text-slate-500">{{ formatDateTime(item.started_at) }}</td>
                <td class="td text-right">
                  <button class="btn-ghost btn-xs" @click.stop="selectExecution(item.id)">
                    查看结果
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
          <EmptyState
            v-if="executions.length === 0 && !loadingList"
            compact
            title="没有执行记录"
            description="运行一次批量任务或单条调试后再来查看。"
          />
        </div>
        <div class="flex items-center justify-between border-t border-slate-800 px-4 py-3">
          <span class="text-[11px] text-slate-500">第 {{ page }} / {{ pageCount }} 页</span>
          <div class="flex gap-1">
            <button class="btn-ghost btn-xs" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
            <button class="btn-ghost btn-xs" :disabled="page >= pageCount" @click="goPage(page + 1)">下一页</button>
          </div>
        </div>
      </section>
    </template>

    <template v-else>
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="text-[11px] font-medium uppercase tracking-wider text-indigo-300">单笔执行结果</p>
          <h2 class="mt-1 flex flex-wrap items-center gap-2 text-lg font-semibold text-slate-100">
            执行 #{{ detail?.id ?? route.query.execution }}
            <span v-if="detail?.case_code" class="font-mono text-sm text-slate-300">
              · {{ detail.case_code }}
            </span>
            <StatusPill v-if="detail" :status="detail.status" size="sm" />
          </h2>
          <p class="mt-1 text-xs text-slate-500">
            <span v-if="detail?.case_name">{{ detail.case_name }} · </span>
            <span v-if="relationTask">
              所属批量任务
              <button class="font-mono text-indigo-300 underline decoration-dotted" @click="openBreadcrumb('task')">
                {{ relationTask.code }}
              </button>
              {{ relationTask.name ? `· ${relationTask.name}` : '' }}
            </span>
            <span v-else-if="detail">单笔调试 / 重跑记录</span>
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button class="btn-ghost btn-xs" @click="backToList">返回执行记录</button>
          <button v-if="detail?.task_id" class="btn-ghost btn-xs" @click="openBreadcrumb('task')">
            返回批量任务详情
          </button>
          <button v-if="detail?.case_id" class="btn-ghost btn-xs" @click="openBreadcrumb('case')">
            该用例全部历史
          </button>
          <button class="btn-ghost btn-xs" @click="refreshDetail">刷新</button>
        </div>
      </div>

      <CaseTraceInspector
        :detail="detail"
        :loading="loadingDetail"
        @refresh="refreshDetail"
      />
    </template>
  </div>
</template>
