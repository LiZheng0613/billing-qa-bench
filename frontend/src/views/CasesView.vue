<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import BaseModal from '../components/BaseModal.vue'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import ScheduleField from '../components/ScheduleField.vue'
import StatusPill from '../components/StatusPill.vue'
import TestCaseDrawer from './components/TestCaseDrawer.vue'
import CaseRevisionPanel from './components/CaseRevisionPanel.vue'
import CaseTrendPanel from './components/CaseTrendPanel.vue'
import RunComparePanel from './components/RunComparePanel.vue'
import type {
  CasePayload,
  Environment,
  ModelConfig,
  PromptListItem,
  TestCase,
} from '../types'
import { errorMessage } from '../utils/errors'
import {
  caseTypeLabel,
  formatDateTime,
  statusLabel,
  truncate,
} from '../utils/format'
import {
  defaultSchedule,
  normalizeSchedule,
  schedulePayload,
  scheduleSummary,
  scheduleValidationError,
} from '../utils/schedule'
import { useToastStore } from '../stores/toast'

const router = useRouter()
const toast = useToastStore()

const cases = ref<TestCase[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const caseType = ref('')
const status = ref('')
const loading = ref(false)

const environments = ref<Environment[]>([])
const templates = ref<PromptListItem[]>([])
const models = ref<ModelConfig[]>([])

const drawerOpen = ref(false)
const editingCase = ref<TestCase | null>(null)
const saving = ref(false)
const debuggingId = ref<number | null>(null)
const importInput = ref<HTMLInputElement | null>(null)

const revisionOpen = ref(false)
const revisionCase = ref<TestCase | null>(null)
const trendOpen = ref(false)
const trendCase = ref<TestCase | null>(null)
const compareOpen = ref(false)
const compareCase = ref<TestCase | null>(null)

const selectedIds = ref<number[]>([])
const batchOpen = ref(false)
const batchCreating = ref(false)
const allActiveCount = ref(0)
const allActiveLoading = ref(false)
const batchForm = reactive({
  name: '',
  environment_id: null as number | null,
  scope: 'SELECTED' as 'SELECTED' | 'ALL_ACTIVE',
  concurrency: 3,
  qps_limit: 5,
  retry_on_env_error: true,
  max_env_retries: 2,
  retry_backoff_seconds: 3,
  auto_start: true,
  schedule: defaultSchedule(),
})

const batchScopeCount = computed(() =>
  batchForm.scope === 'ALL_ACTIVE' ? allActiveCount.value : selectedIds.value.length,
)

const activeOnPage = computed(() => cases.value.filter((item) => item.status === 'ACTIVE'))

const pageAllSelected = computed(
  () =>
    activeOnPage.value.length > 0 &&
    activeOnPage.value.every((item) => selectedIds.value.includes(item.id)),
)

const pageSelectableCount = computed(() => activeOnPage.value.length)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

const typeCount = computed(() => ({
  single: cases.value.filter((item) => item.case_type === 'SINGLE').length,
  static: cases.value.filter((item) => item.case_type === 'STATIC_MULTI').length,
  dynamic: cases.value.filter((item) => item.case_type === 'DYNAMIC_MULTI').length,
}))

async function loadOptions(): Promise<void> {
  try {
    const [envPage, templatePage, modelPage] = await Promise.all([
      api.environments.list({ page_size: 200 }),
      api.prompts.list({ page_size: 200 }),
      api.models.list({ page_size: 200 }),
    ])
    environments.value = envPage.items
    templates.value = templatePage.items
    models.value = modelPage.items
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function loadCases(): Promise<void> {
  loading.value = true
  try {
    const pageData = await api.cases.list({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value.trim() || undefined,
      case_type: caseType.value || undefined,
      status: status.value || undefined,
    })
    cases.value = pageData.items
    total.value = pageData.total
    if (cases.value.length === 0 && page.value > 1) {
      page.value -= 1
      await loadCases()
    }
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

function applyFilters(): void {
  page.value = 1
  void loadCases()
}

function resetFilters(): void {
  keyword.value = ''
  caseType.value = ''
  status.value = ''
  page.value = 1
  void loadCases()
}

function openCreate(): void {
  editingCase.value = null
  drawerOpen.value = true
}

function openEdit(item: TestCase): void {
  editingCase.value = item
  drawerOpen.value = true
}

async function submitCase(payload: CasePayload): Promise<void> {
  saving.value = true
  try {
    if (editingCase.value) {
      const body: Partial<CasePayload> = { ...payload }
      body.code = undefined
      body.case_type = undefined
      const updated = await api.cases.update(editingCase.value.id, body)
      toast.success(`用例 ${updated.code} 已更新至 v${updated.current_version}`)
    } else {
      const created = await api.cases.create(payload)
      toast.success(`用例 ${created.code} 已创建`)
    }
    drawerOpen.value = false
    await loadCases()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

async function removeCase(item: TestCase): Promise<void> {
  if (!window.confirm(`确认删除用例 ${item.code}？删除为软删除，历史执行记录保留。`)) return
  try {
    const result = await api.cases.remove(item.id)
    toast.success(result.message)
    await loadCases()
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function debugCase(item: TestCase): Promise<void> {
  debuggingId.value = item.id
  toast.info(`正在执行 ${item.code} 单条调试，请稍候…`)
  try {
    const detail = await api.cases.debug(item.id, { environment_id: item.environment_id })
    toast.success(`调试完成：${statusLabel(detail.status)}，得分 ${detail.total_score ?? '—'}`)
    await router.push({ name: 'trace', query: { execution: String(detail.id) } })
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    debuggingId.value = null
  }
}

function openRevisions(item: TestCase): void {
  revisionCase.value = item
  revisionOpen.value = true
}

function openTrend(item: TestCase): void {
  trendCase.value = item
  trendOpen.value = true
}

function openCompare(item: TestCase): void {
  compareCase.value = item
  compareOpen.value = true
}

function openTrace(executionId: number): void {
  trendOpen.value = false
  void router.push({ name: 'trace', query: { execution: String(executionId) } })
}

function openCaseHistory(item: TestCase): void {
  void router.push({ name: 'trace', query: { case: String(item.id) } })
}

function openTrendHistory(): void {
  if (!trendCase.value) return
  trendOpen.value = false
  openCaseHistory(trendCase.value)
}

async function onRevisionUpdated(): Promise<void> {
  await loadCases()
}

function triggerImport(): void {
  importInput.value?.click()
}

async function onImport(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const result = await api.cases.importFile(file)
    toast.success(`导入完成：新增 ${result.created}，更新 ${result.updated}，失败 ${result.failed}`)
    if (result.errors.length) {
      toast.error(
        result.errors
          .slice(0, 3)
          .map((item) => `第 ${item.row} 行：${item.message}`)
          .join('\n'),
      )
    }
    await loadCases()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    input.value = ''
  }
}

async function exportCases(format: 'json' | 'xlsx'): Promise<void> {
  try {
    await api.cases.export(format)
    toast.success(`已导出 ${format.toUpperCase()} 文件`)
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

// ---------------------------------------------------------------- 批量执行
function toggleSelect(item: TestCase): void {
  if (item.status !== 'ACTIVE') return
  const index = selectedIds.value.indexOf(item.id)
  if (index >= 0) selectedIds.value.splice(index, 1)
  else selectedIds.value.push(item.id)
}

function togglePageSelection(): void {
  if (pageAllSelected.value) {
    const pageIds = new Set(cases.value.map((item) => item.id))
    selectedIds.value = selectedIds.value.filter((id) => !pageIds.has(id))
    return
  }
  const ids = cases.value
    .filter((item) => item.status === 'ACTIVE')
    .map((item) => item.id)
  selectedIds.value = Array.from(new Set([...selectedIds.value, ...ids]))
}

async function selectAllFiltered(): Promise<void> {
  try {
    const batchSize = 200
    const ids: number[] = []
    let current = 1
    while (true) {
      const data = await api.cases.list({
        page: current,
        page_size: batchSize,
        keyword: keyword.value.trim() || undefined,
        case_type: caseType.value || undefined,
        status: status.value || undefined,
      })
      ids.push(
        ...data.items.filter((item) => item.status === 'ACTIVE').map((item) => item.id),
      )
      if (current * batchSize >= data.total) break
      current += 1
    }
    selectedIds.value = Array.from(new Set([...selectedIds.value, ...ids]))
    toast.success(`已选中 ${ids.length} 条筛选结果（仅启用用例可执行）`)
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

function clearSelection(): void {
  selectedIds.value = []
}

async function loadAllActiveCount(): Promise<void> {
  allActiveLoading.value = true
  try {
    const data = await api.cases.list({ page: 1, page_size: 1, status: 'ACTIVE' })
    allActiveCount.value = data.total
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    allActiveLoading.value = false
  }
}

function openBatch(): void {
  const stamp = new Date()
  batchForm.name = `批量执行 ${stamp.getMonth() + 1}/${stamp.getDate()} ${String(stamp.getHours()).padStart(2, '0')}:${String(stamp.getMinutes()).padStart(2, '0')}`
  batchForm.environment_id = null
  batchForm.scope = selectedIds.value.length > 0 ? 'SELECTED' : 'ALL_ACTIVE'
  batchForm.concurrency = 3
  batchForm.qps_limit = 5
  batchForm.retry_on_env_error = true
  batchForm.max_env_retries = 2
  batchForm.retry_backoff_seconds = 3
  batchForm.auto_start = true
  batchForm.schedule = defaultSchedule()
  batchOpen.value = true
  void loadAllActiveCount()
}

async function submitBatch(): Promise<void> {
  if (!batchForm.name.trim()) {
    toast.error('请填写任务名称')
    return
  }
  if (batchForm.scope === 'SELECTED' && selectedIds.value.length === 0) {
    toast.error('请先勾选用例，或将执行范围切换为「全部启用用例」')
    return
  }
  if (batchForm.scope === 'ALL_ACTIVE' && allActiveCount.value === 0) {
    toast.error('用例库中没有启用用例，无法批量执行')
    return
  }
  const scheduleError = scheduleValidationError(batchForm.schedule)
  if (scheduleError) {
    toast.error(scheduleError)
    return
  }
  batchCreating.value = true
  try {
    const created = await api.tasks.create({
      name: batchForm.name.trim(),
      environment_id: batchForm.environment_id,
      case_scope: batchForm.scope,
      case_ids: batchForm.scope === 'SELECTED' ? [...selectedIds.value] : [],
      concurrency: batchForm.concurrency,
      qps_limit: batchForm.qps_limit,
      retry_on_env_error: batchForm.retry_on_env_error,
      max_env_retries: batchForm.max_env_retries,
      retry_backoff_seconds: batchForm.retry_backoff_seconds,
      ...schedulePayload(normalizeSchedule(batchForm.schedule)),
      auto_start: batchForm.schedule.type === 'IMMEDIATE' && batchForm.auto_start,
    })
    const scheduled = created.schedule_type !== 'IMMEDIATE'
    toast.success(
      scheduled
        ? `定时任务 ${created.code} 已登记：${scheduleSummary(normalizeSchedule(batchForm.schedule))}，共 ${created.total_cases} 条用例`
        : `批量任务 ${created.code} 已创建，共 ${created.total_cases} 条用例`,
    )
    batchOpen.value = false
    selectedIds.value = []
    await router.push({ name: 'dashboard', query: { task: String(created.id) } })
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    batchCreating.value = false
  }
}

function goPage(next: number): void {
  if (next < 1 || next > pageCount.value) return
  page.value = next
  void loadCases()
}

onMounted(() => {
  void loadOptions()
  void loadCases()
})
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      eyebrow="Assets · 用例库"
      title="用例资产"
      description="单轮 / 静态多轮 / 动态多轮三类用例，支持批量执行、版本快照、字段 Diff、回滚、导入导出与单条调试。"
    >
      <template #actions>
        <input ref="importInput" type="file" accept=".xlsx,.json" class="hidden" @change="onImport" />
        <button class="btn-ghost" @click="triggerImport">导入</button>
        <button class="btn-ghost" @click="exportCases('json')">导出 JSON</button>
        <button class="btn-ghost" @click="exportCases('xlsx')">导出 Excel</button>
        <button
          class="btn-ghost"
          title="选择当前页、跨页筛选结果或全部启用用例执行"
          @click="openBatch"
        >
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="m5 13 4 4L19 7" />
          </svg>
          批量执行<span v-if="selectedIds.length">（{{ selectedIds.length }}）</span>
        </button>
        <button class="btn-primary" @click="openCreate">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" d="M12 5v14M5 12h14" />
          </svg>
          新建用例
        </button>
      </template>
    </PageHeader>

    <div class="card flex flex-wrap items-end gap-3 px-4 py-3">
      <div class="min-w-[220px] flex-1">
        <label class="label">关键词</label>
        <input
          v-model="keyword"
          class="input"
          placeholder="搜索编号 / 名称 / 描述"
          @keyup.enter="applyFilters"
        />
      </div>
      <div class="w-40">
        <label class="label">用例类型</label>
        <select v-model="caseType" class="input" @change="applyFilters">
          <option value="">全部类型</option>
          <option value="SINGLE">单轮</option>
          <option value="STATIC_MULTI">静态多轮</option>
          <option value="DYNAMIC_MULTI">动态多轮</option>
        </select>
      </div>
      <div class="w-32">
        <label class="label">状态</label>
        <select v-model="status" class="input" @change="applyFilters">
          <option value="">全部</option>
          <option value="ACTIVE">启用</option>
          <option value="DRAFT">草稿</option>
          <option value="DISABLED">停用</option>
        </select>
      </div>
      <button class="btn-ghost" @click="applyFilters">查询</button>
      <button class="btn-ghost" @click="resetFilters">重置</button>
    </div>

    <div class="card overflow-hidden">
      <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
        <div class="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          <span>共 {{ total }} 条</span>
          <span class="text-slate-300" aria-hidden="true">|</span>
          <span>当前页：单轮 {{ typeCount.single }} · 静态 {{ typeCount.static }} · 动态 {{ typeCount.dynamic }}</span>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <span v-if="selectedIds.length" class="text-[11px] text-indigo-600">
            已选 {{ selectedIds.length }} 条
          </span>
          <button class="btn-ghost btn-xs" @click="selectAllFiltered">全选筛选结果（跨页）</button>
          <button v-if="selectedIds.length" class="btn-ghost btn-xs" @click="clearSelection">
            清空选择
          </button>
          <button class="text-[11px] text-slate-500 transition hover:text-slate-800" @click="loadCases">
            刷新
          </button>
        </div>
      </div>

      <div class="overflow-x-auto">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th w-10">
                <input
                  type="checkbox"
                  class="h-4 w-4 rounded border-slate-300 bg-white"
                  :checked="pageAllSelected"
                  :disabled="pageSelectableCount === 0"
                  title="全选当前页启用用例"
                  @change="togglePageSelection"
                />
              </th>
              <th class="th">用例</th>
              <th class="th">类型</th>
              <th class="th">状态</th>
              <th class="th">版本</th>
              <th class="th">环境 / Prompt</th>
              <th class="th">更新时间</th>
              <th class="th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in cases" :key="item.id">
              <td class="td">
                <input
                  type="checkbox"
                  class="h-4 w-4 rounded border-slate-300 bg-white"
                  :checked="selectedIds.includes(item.id)"
                  :disabled="item.status !== 'ACTIVE'"
                  :title="item.status === 'ACTIVE' ? '选择该用例' : '仅启用用例可批量执行'"
                  @change="toggleSelect(item)"
                />
              </td>
              <td class="td max-w-[300px]">
                <p class="font-mono text-xs text-indigo-600">{{ item.code }}</p>
                <p class="mt-0.5 truncate text-sm text-slate-800" :title="item.name">{{ item.name }}</p>
                <p class="mt-0.5 text-[11px] text-slate-500" :title="item.description ?? ''">
                  {{ truncate(item.description, 44) }}
                </p>
                <div class="mt-1 flex flex-wrap gap-1">
                  <span v-for="tag in item.tags ?? []" :key="tag" class="tag">{{ tag }}</span>
                </div>
              </td>
              <td class="td">
                <span class="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700">
                  {{ caseTypeLabel(item.case_type) }}
                </span>
              </td>
              <td class="td"><StatusPill :status="item.status" size="sm" /></td>
              <td class="td">
                <span class="font-mono text-xs text-slate-700">v{{ item.current_version }}</span>
                <span class="ml-1 rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-600">{{ item.priority }}</span>
              </td>
              <td class="td max-w-[200px] text-[11px] text-slate-500">
                <p class="truncate text-slate-600">{{ item.environment_name || '未绑定环境' }}</p>
                <p class="truncate">模拟：{{ item.simulator_template_code || '—' }}</p>
                <p class="truncate">裁判：{{ item.judge_template_code || '—' }}</p>
              </td>
              <td class="td text-xs text-slate-500">{{ formatDateTime(item.updated_at) }}</td>
              <td class="td">
                <div class="flex flex-wrap justify-end gap-1">
                  <button class="btn-ghost btn-xs" @click="openEdit(item)">编辑</button>
                  <button
                    class="btn-ghost btn-xs"
                    :disabled="debuggingId === item.id"
                    @click="debugCase(item)"
                  >
                    {{ debuggingId === item.id ? '执行中…' : '调试' }}
                  </button>
                  <button class="btn-ghost btn-xs" @click="openRevisions(item)">版本</button>
                  <button class="btn-ghost btn-xs" @click="openTrend(item)">趋势</button>
                  <button class="btn-ghost btn-xs" @click="openCaseHistory(item)">历史</button>
                  <button class="btn-ghost btn-xs" @click="openCompare(item)">对比</button>
                  <button class="btn-danger btn-xs" @click="removeCase(item)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <EmptyState
          v-if="!loading && cases.length === 0"
          title="没有匹配的用例"
          description="调整筛选条件，或新建一条用例开始测试。"
        >
          <button class="btn-primary btn-xs mt-2" @click="openCreate">新建用例</button>
        </EmptyState>
      </div>

      <div class="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-4 py-3">
        <span class="text-[11px] text-slate-500">第 {{ page }} / {{ pageCount }} 页</span>
        <div class="flex items-center gap-2">
          <button class="btn-ghost btn-xs" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
          <button class="btn-ghost btn-xs" :disabled="page >= pageCount" @click="goPage(page + 1)">下一页</button>
        </div>
      </div>
    </div>

    <BaseModal
      v-model="batchOpen"
      title="批量执行用例"
      subtitle="选择执行方式（立即 / 指定时间 / 每天 / 每周）并设置并发、QPS 与重试策略"
      width="lg"
    >
      <div class="space-y-4">
        <div class="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <p class="text-xs font-medium text-slate-700">执行范围</p>
          <label
            class="flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 transition"
            :class="
              batchForm.scope === 'SELECTED'
                ? 'border-indigo-200 bg-indigo-50'
                : 'border-slate-200 hover:bg-slate-100/70'
            "
          >
            <input
              v-model="batchForm.scope"
              type="radio"
              value="SELECTED"
              class="mt-1 h-4 w-4 border-slate-300 bg-white"
            />
            <span class="min-w-0 text-sm text-slate-800">
              已选用例（{{ selectedIds.length }} 条）
              <span class="block text-[11px] text-slate-500">
                当前页勾选，或通过「全选筛选结果（跨页）」选中的用例。
              </span>
            </span>
          </label>
          <label
            class="flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 transition"
            :class="
              batchForm.scope === 'ALL_ACTIVE'
                ? 'border-indigo-200 bg-indigo-50'
                : 'border-slate-200 hover:bg-slate-100/70'
            "
          >
            <input
              v-model="batchForm.scope"
              type="radio"
              value="ALL_ACTIVE"
              class="mt-1 h-4 w-4 border-slate-300 bg-white"
            />
            <span class="min-w-0 text-sm text-slate-800">
              全部启用用例（{{ allActiveLoading ? '统计中…' : `${allActiveCount} 条` }}）
              <span class="block text-[11px] text-slate-500">
                自动跨分页执行用例库中所有启用用例，不受当前筛选与分页影响，适合全量回归。
              </span>
            </span>
          </label>
        </div>
        <div class="rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs text-indigo-700">
          本次将执行 <span class="font-semibold">{{ batchScopeCount }}</span> 条用例，执行进度可在「测试大盘」实时查看。
        </div>
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label">任务名称</label>
            <input v-model="batchForm.name" class="input" placeholder="例如：账单全量回归" />
          </div>
          <div>
            <label class="label">执行环境</label>
            <select v-model="batchForm.environment_id" class="input">
              <option :value="null">不绑定环境（使用用例配置）</option>
              <option v-for="env in environments" :key="env.id" :value="env.id">
                {{ env.name }}（{{ env.code }}）
              </option>
            </select>
          </div>
        </div>
        <ScheduleField v-model="batchForm.schedule" :disabled="batchCreating" />
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label">并发数</label>
            <input v-model.number="batchForm.concurrency" class="input" type="number" min="1" max="20" />
            <p class="mt-1 text-[10px] text-slate-500">同时执行的用例数上限，建议不超过被测服务承载能力。</p>
          </div>
          <div>
            <label class="label">QPS 上限</label>
            <input v-model.number="batchForm.qps_limit" class="input" type="number" min="0.1" max="100" step="0.5" />
            <p class="mt-1 text-[10px] text-slate-500">任务整体每秒请求上限，重试请求同样受限。</p>
          </div>
        </div>
        <div class="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <label class="flex items-start gap-2 text-sm text-slate-700">
            <input
              v-model="batchForm.retry_on_env_error"
              type="checkbox"
              class="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white"
            />
            <span>
              环境类失败自动重试
              <span class="block text-[11px] text-slate-500">
                仅重试连接失败、超时、HTTP 429/5xx、SSE 空响应等环境故障；业务失败不会重试。
              </span>
            </span>
          </label>
          <div
            class="grid gap-3 sm:grid-cols-2"
            :class="batchForm.retry_on_env_error ? '' : 'pointer-events-none opacity-40'"
          >
            <div>
              <label class="label">最大重试次数</label>
              <input
                v-model.number="batchForm.max_env_retries"
                class="input"
                type="number"
                min="0"
                max="10"
                :disabled="!batchForm.retry_on_env_error"
              />
            </div>
            <div>
              <label class="label">基础退避（秒）</label>
              <input
                v-model.number="batchForm.retry_backoff_seconds"
                class="input"
                type="number"
                min="0"
                max="60"
                step="0.5"
                :disabled="!batchForm.retry_on_env_error"
              />
            </div>
          </div>
        </div>
        <label
          v-if="batchForm.schedule.type === 'IMMEDIATE'"
          class="flex items-center gap-2 text-sm text-slate-700"
        >
          <input
            v-model="batchForm.auto_start"
            type="checkbox"
            class="h-4 w-4 rounded border-slate-300 bg-white"
          />
          创建后立即启动
        </label>
      </div>

      <template #footer>
        <button class="btn-ghost" @click="batchOpen = false">取消</button>
        <button class="btn-primary" :disabled="batchCreating" @click="submitBatch">
          {{
            batchCreating
              ? '创建中…'
              : batchForm.schedule.type === 'IMMEDIATE'
                ? `创建并执行（${batchScopeCount} 条）`
                : `创建定时任务（${batchScopeCount} 条）`
          }}
        </button>
      </template>
    </BaseModal>

    <TestCaseDrawer
      v-model="drawerOpen"
      :case-data="editingCase"
      :environments="environments"
      :templates="templates"
      :models="models"
      :saving="saving"
      @submit="submitCase"
    />

    <CaseRevisionPanel
      v-model="revisionOpen"
      :case-data="revisionCase"
      @updated="onRevisionUpdated"
    />

    <CaseTrendPanel
      v-model="trendOpen"
      :case-data="trendCase"
      @select-execution="openTrace"
      @view-all="openTrendHistory"
    />

    <RunComparePanel v-model="compareOpen" :case-data="compareCase" />
  </div>
</template>
