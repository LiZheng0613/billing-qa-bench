<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import type { CaseImportAction, CaseImportConflict, CaseImportResult } from '../api'
import BaseModal from '../components/BaseModal.vue'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import ScheduleField from '../components/ScheduleField.vue'
import StatusPill from '../components/StatusPill.vue'
import TestCaseDrawer from './components/TestCaseDrawer.vue'
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
  truncate,
} from '../utils/format'
import {
  defaultSchedule,
  normalizeSchedule,
  schedulePayload,
  scheduleSummary,
  scheduleValidationError,
} from '../utils/schedule'
import { useDebugStore } from '../stores/debug'
import { useToastStore } from '../stores/toast'

const router = useRouter()
const toast = useToastStore()
const debugStore = useDebugStore()

const cases = ref<TestCase[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const caseType = ref('')
const status = ref('')
const gradingMode = ref('')
const reviewStatus = ref('')
const loading = ref(false)

const REVIEW_STATUS_META: Record<string, { label: string; className: string }> = {
  NOT_REQUIRED: { label: '无需评审', className: 'bg-slate-100 text-slate-600' },
  PENDING: { label: '待评审', className: 'bg-amber-50 text-amber-700' },
  APPROVED: { label: '已通过', className: 'bg-emerald-50 text-emerald-700' },
  REJECTED: { label: '已驳回', className: 'bg-rose-50 text-rose-700' },
}

function reviewMeta(status?: string | null): { label: string; className: string } {
  return REVIEW_STATUS_META[status ?? ''] ?? {
    label: status || '未设置',
    className: 'bg-slate-100 text-slate-600',
  }
}

const environments = ref<Environment[]>([])
const templates = ref<PromptListItem[]>([])
const models = ref<ModelConfig[]>([])

const drawerOpen = ref(false)
const saving = ref(false)
const importInput = ref<HTMLInputElement | null>(null)
const importOpen = ref(false)
const importFile = ref<File | null>(null)
const importPreviewing = ref(false)
const importRunning = ref(false)
const importConflict = ref<CaseImportConflict>('upsert')
const importPreview = ref<CaseImportResult | null>(null)

const IMPORT_CONFLICT_OPTIONS: Array<{
  value: CaseImportConflict
  label: string
  hint: string
}> = [
  { value: 'upsert', label: '覆盖更新', hint: '编号已存在时按文件内容更新（推荐）' },
  { value: 'skip', label: '跳过', hint: '编号已存在时保持库里的记录不动' },
  { value: 'error', label: '报错', hint: '编号已存在时整条判为失败，只导入新用例' },
]

const IMPORT_ACTION_META: Record<
  CaseImportAction,
  { label: string; className: string }
> = {
  created: { label: '新增', className: 'bg-emerald-50 text-emerald-700' },
  updated: { label: '更新', className: 'bg-indigo-50 text-indigo-700' },
  skipped: { label: '跳过', className: 'bg-amber-50 text-amber-700' },
  failed: { label: '失败', className: 'bg-rose-50 text-rose-700' },
}

const importBusy = computed(() => importPreviewing.value || importRunning.value)
const importConflictHint = computed(
  () => IMPORT_CONFLICT_OPTIONS.find((option) => option.value === importConflict.value)?.hint ?? '',
)
const importStats = computed(() => {
  const preview = importPreview.value
  if (!preview) return []
  return [
    { label: '文件条数', value: preview.total, className: 'text-ink' },
    { label: '新增', value: preview.created, className: 'text-emerald-600' },
    { label: '更新', value: preview.updated, className: 'text-indigo-600' },
    { label: '跳过', value: preview.skipped, className: 'text-amber-600' },
    {
      label: '失败',
      value: preview.failed,
      className: preview.failed ? 'text-rose-600' : 'text-slate-400',
    },
  ]
})
const importHasWrites = computed(() => {
  const preview = importPreview.value
  return !!preview && preview.created + preview.updated > 0
})

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
  retry_on_error: true,
  max_error_retries: 2,
  retry_backoff_seconds: 3,
  enable_self_check: false,
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

async function loadCases(): Promise<boolean> {
  loading.value = true
  try {
    const pageData = await api.cases.list({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value.trim() || undefined,
      case_type: caseType.value || undefined,
      status: status.value || undefined,
      grading_mode: gradingMode.value || undefined,
      review_status: reviewStatus.value || undefined,
    })
    cases.value = pageData.items
    total.value = pageData.total
    if (cases.value.length === 0 && page.value > 1) {
      page.value -= 1
      await loadCases()
    }
    return true
  } catch (exc) {
    toast.error(errorMessage(exc))
    return false
  } finally {
    loading.value = false
  }
}

async function refreshCases(): Promise<void> {
  if (await loadCases()) toast.success('刷新成功')
}

function applyFilters(): void {
  page.value = 1
  void loadCases()
}

function resetFilters(): void {
  keyword.value = ''
  caseType.value = ''
  status.value = ''
  gradingMode.value = ''
  reviewStatus.value = ''
  page.value = 1
  void loadCases()
}

function openCreate(): void {
  drawerOpen.value = true
}

function openDetail(item: TestCase): void {
  void router.push({ name: 'case-detail', params: { id: String(item.id) } })
}

async function submitCase(payload: CasePayload): Promise<void> {
  saving.value = true
  try {
    const created = await api.cases.create(payload)
    toast.success(`用例 ${created.code} 已创建`)
    drawerOpen.value = false
    await loadCases()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

function debugCase(item: TestCase): void {
  // 请求生命周期归全局 debug store：跨页面保持「调试中」防重复发起；
  // 完成时仍在本页 → 自动跳转 trace；已离开 → 底部胶囊常驻提示
  void debugStore.run({
    id: item.id,
    code: item.code,
    environmentId: item.environment_id,
  })
}

function triggerImport(): void {
  importInput.value?.click()
}

async function onImport(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // 先清空，允许用户再次选择同一个文件
  input.value = ''
  if (!file) return
  importFile.value = file
  importConflict.value = 'upsert'
  importPreview.value = null
  importOpen.value = true
  await previewImport()
}

async function previewImport(): Promise<void> {
  const file = importFile.value
  if (!file) return
  importPreviewing.value = true
  try {
    importPreview.value = await api.cases.importFile(file, {
      dryRun: true,
      onConflict: importConflict.value,
    })
  } catch (exc) {
    importPreview.value = null
    toast.error(errorMessage(exc))
  } finally {
    importPreviewing.value = false
  }
}

async function changeConflict(value: CaseImportConflict): Promise<void> {
  if (importConflict.value === value) return
  importConflict.value = value
  await previewImport()
}

function importActionMeta(action: CaseImportAction): { label: string; className: string } {
  return IMPORT_ACTION_META[action]
}

async function confirmImport(): Promise<void> {
  const file = importFile.value
  if (!file) return
  importRunning.value = true
  try {
    const result = await api.cases.importFile(file, {
      dryRun: false,
      onConflict: importConflict.value,
    })
    const parts = [`新增 ${result.created}`, `更新 ${result.updated}`]
    if (result.skipped) parts.push(`跳过 ${result.skipped}`)
    parts.push(`失败 ${result.failed}`)
    toast.success(`导入完成：${parts.join('，')}`)
    if (result.errors.length) {
      toast.error(
        result.errors
          .slice(0, 3)
          .map((item) => `第 ${item.row} 条：${item.message}`)
          .join('\n'),
      )
    }
    importOpen.value = false
    await loadCases()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    importRunning.value = false
  }
}

async function exportCases(): Promise<void> {
  try {
    await api.cases.export('json', {
      case_type: caseType.value || undefined,
      status: status.value || undefined,
      grading_mode: gradingMode.value || undefined,
      review_status: reviewStatus.value || undefined,
      keyword: keyword.value.trim() || undefined,
    })
    toast.success('已导出当前筛选结果')
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
  batchForm.retry_on_error = true
  batchForm.max_error_retries = 2
  batchForm.retry_backoff_seconds = 3
  batchForm.enable_self_check = false
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
      retry_on_env_error: batchForm.retry_on_error,
      max_env_retries: batchForm.max_error_retries,
      retry_on_error: batchForm.retry_on_error,
      max_error_retries: batchForm.max_error_retries,
      retry_backoff_seconds: batchForm.retry_backoff_seconds,
      enable_self_check: batchForm.enable_self_check,
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
    await router.push({ name: 'task-detail', params: { id: String(created.id) } })
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
        <input ref="importInput" type="file" accept=".json" class="hidden" @change="onImport" />
        <button class="btn-ghost" @click="triggerImport">导入 JSON</button>
        <button class="btn-ghost" @click="exportCases">导出 JSON</button>
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
          placeholder="搜索编号 / 名称 / 描述（分号分隔多词）"
          @keyup.enter="applyFilters"
        />
      </div>
      <div class="w-40">
        <label class="label">用例类型</label>
        <select v-model="caseType" class="input" @change="applyFilters">
          <button><selectedcontent></selectedcontent></button>
          <option value="">全部类型</option>
          <option value="SINGLE">单轮</option>
          <option value="STATIC_MULTI">静态多轮</option>
          <option value="DYNAMIC_MULTI">动态多轮</option>
        </select>
      </div>
      <div class="w-32">
        <label class="label">状态</label>
        <select v-model="status" class="input" @change="applyFilters">
          <button><selectedcontent></selectedcontent></button>
          <option value="">全部</option>
          <option value="ACTIVE">启用</option>
          <option value="DRAFT">草稿</option>
          <option value="DISABLED">停用</option>
        </select>
      </div>
      <div class="w-36">
        <label class="label">判分方式</label>
        <select v-model="gradingMode" class="input" @change="applyFilters">
          <button><selectedcontent></selectedcontent></button>
          <option value="">全部</option>
          <option value="LEGACY">4 维评分</option>
          <option value="ANSWER_CONTRACT">答案契约</option>
        </select>
      </div>
      <div class="w-32">
        <label class="label">评审状态</label>
        <select v-model="reviewStatus" class="input" @change="applyFilters">
          <button><selectedcontent></selectedcontent></button>
          <option value="">全部</option>
          <option value="PENDING">待评审</option>
          <option value="APPROVED">已通过</option>
          <option value="REJECTED">已驳回</option>
          <option value="NOT_REQUIRED">无需评审</option>
        </select>
      </div>
      <button class="btn-ghost" @click="applyFilters">查询</button>
      <button class="btn-ghost" @click="resetFilters">重置</button>
    </div>

    <div class="card overflow-hidden">
      <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
        <div class="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
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
          <button class="btn-ghost btn-xs" @click="refreshCases">
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
              <th class="th">关联配置</th>
              <th class="th">更新时间</th>
              <th class="th sticky right-0 z-10 bg-white text-right">操作</th>
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
              <td class="td max-w-[380px]">
                <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <button
                    class="font-mono text-xs text-indigo-600 transition hover:text-indigo-800 hover:underline"
                    @click="openDetail(item)"
                  >
                    {{ item.code }}
                  </button>
                  <span class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                    {{ caseTypeLabel(item.case_type) }}
                  </span>
                  <StatusPill :status="item.status" size="sm" />
                  <span class="font-mono text-[10px] text-slate-500">v{{ item.current_version }}</span>
                  <span class="rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-500">{{ item.priority }}</span>
                </div>
                <button
                  class="mt-1 block max-w-full truncate text-left text-sm text-slate-800 transition hover:text-brand"
                  :title="item.name"
                  @click="openDetail(item)"
                >
                  {{ item.name }}
                </button>
                <p class="mt-0.5 text-[11px] text-slate-500" :title="item.description ?? ''">
                  {{ truncate(item.description, 48) }}
                </p>
                <div class="mt-1 flex flex-wrap items-center gap-1">
                  <span v-for="tag in item.tags ?? []" :key="tag" class="tag">{{ tag }}</span>
                </div>
                <div class="mt-1 flex flex-wrap items-center gap-1">
                  <span
                    class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                    :class="
                      item.grading_mode === 'ANSWER_CONTRACT'
                        ? 'bg-indigo-50 text-indigo-700'
                        : 'bg-slate-100 text-slate-600'
                    "
                  >
                    {{ item.grading_mode === 'ANSWER_CONTRACT' ? '答案契约' : '4 维评分' }}
                  </span>
                  <span
                    v-if="item.review_status && item.review_status !== 'NOT_REQUIRED'"
                    class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                    :class="reviewMeta(item.review_status).className"
                    :title="item.reviewed_by ? `评审人：${item.reviewed_by}` : undefined"
                  >
                    {{ reviewMeta(item.review_status).label }}
                  </span>
                </div>
              </td>
              <td class="td max-w-[220px] text-[11px] text-slate-500">
                <p class="truncate text-slate-600">{{ item.environment_name || '未绑定环境' }}</p>
                <p class="truncate">模拟：{{ item.simulator_template_name || '—' }}</p>
                <p class="truncate">裁判：{{ item.judge_template_name || '—' }}</p>
              </td>
              <td class="td text-xs text-slate-500">{{ formatDateTime(item.updated_at) }}</td>
              <td class="td sticky right-0 z-10 bg-white">
                <div class="flex flex-wrap justify-end gap-1">
                  <button class="btn-ghost btn-xs" @click="openDetail(item)">详情</button>
                  <button
                    class="btn-ghost btn-xs"
                    :disabled="debugStore.isRunning(item.id)"
                    @click="debugCase(item)"
                  >
                    {{ debugStore.isRunning(item.id) ? '调试中…' : '调试' }}
                  </button>
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
        <span class="text-[11px] text-slate-500">
          共 <span class="font-medium tabular-nums text-slate-700">{{ total }}</span> 条用例 · 第 {{ page }} / {{ pageCount }} 页
        </span>
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
              <button><selectedcontent></selectedcontent></button>
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
              v-model="batchForm.retry_on_error"
              type="checkbox"
              class="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white"
            />
            <span>
              异常失败自动重试
              <span class="block text-[11px] text-slate-500">
                仅重试环境故障、模型调用/裁决异常等执行异常；规则判定失败、轮次超限、配置或模板错误不会重试。
              </span>
            </span>
          </label>
          <div
            class="grid gap-3 sm:grid-cols-2"
            :class="batchForm.retry_on_error ? '' : 'pointer-events-none opacity-40'"
          >
            <div>
              <label class="label">最大重试次数</label>
              <input
                v-model.number="batchForm.max_error_retries"
                class="input"
                type="number"
                min="0"
                max="10"
                :disabled="!batchForm.retry_on_error"
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
                :disabled="!batchForm.retry_on_error"
              />
            </div>
          </div>
        </div>
        <label class="flex items-start gap-2 text-sm text-slate-700">
          <input
            v-model="batchForm.enable_self_check"
            type="checkbox"
            class="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white"
          />
          <span>
            失败追问自检
            <span class="block text-[11px] text-slate-500">
              LLM 裁决判失败的用例，会在同一会话内追加一轮追问，让被测 Agent
              自查错误原因并留存自检结论。
            </span>
          </span>
        </label>
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

    <BaseModal
      v-model="importOpen"
      title="导入用例"
      :subtitle="importFile ? importFile.name : ''"
      width="lg"
    >
      <div class="space-y-4">
        <div class="space-y-1.5">
          <span class="label">编号已存在时的处理策略</span>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="option in IMPORT_CONFLICT_OPTIONS"
              :key="option.value"
              class="rounded-xl border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-60"
              :class="
                importConflict === option.value
                  ? 'border-brand bg-brand/5 text-ink'
                  : 'border-line bg-white text-slate-500 hover:border-slate-300'
              "
              :disabled="importBusy"
              :title="option.hint"
              @click="changeConflict(option.value)"
            >
              {{ option.label }}
            </button>
          </div>
          <p class="hint">{{ importConflictHint }}</p>
        </div>

        <div
          v-if="importPreviewing"
          class="rounded-xl border border-line bg-slate-50 px-4 py-6 text-center text-xs text-slate-500"
        >
          正在预检文件…
        </div>
        <template v-else-if="importPreview">
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-5">
            <div
              v-for="stat in importStats"
              :key="stat.label"
              class="rounded-xl border border-line bg-white px-3 py-2"
            >
              <p class="text-[11px] text-slate-500">{{ stat.label }}</p>
              <p class="text-lg font-semibold" :class="stat.className">{{ stat.value }}</p>
            </div>
          </div>
          <p class="text-[11px] text-slate-500">
            以上只是预检结果，确认之前不会写入数据库。成功导入的用例会按内容变更记录版本。
          </p>
          <div class="max-h-72 overflow-auto rounded-xl border border-line">
            <table class="table-shell">
              <thead>
                <tr>
                  <th class="th w-16">序号</th>
                  <th class="th">用例编号</th>
                  <th class="th w-20">动作</th>
                  <th class="th">说明</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in importPreview.items" :key="`${item.row}-${item.code ?? ''}`">
                  <td class="td text-xs">{{ item.row }}</td>
                  <td class="td font-mono text-xs">{{ item.code || '—' }}</td>
                  <td class="td">
                    <span
                      class="rounded-full px-1.5 py-0.5 text-[11px] font-medium"
                      :class="importActionMeta(item.action).className"
                    >
                      {{ importActionMeta(item.action).label }}
                    </span>
                  </td>
                  <td class="td text-xs text-slate-500">{{ item.message || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div
            v-if="importPreview.warnings.length"
            class="rounded-xl border border-amber-200 bg-amber-50 p-3 text-[11px] text-amber-800"
          >
            <p class="font-medium">文件级提示</p>
            <ul class="mt-1 list-disc space-y-0.5 pl-4">
              <li v-for="(warning, index) in importPreview.warnings" :key="index">
                {{ warning }}
              </li>
            </ul>
          </div>
        </template>
        <div
          v-else
          class="rounded-xl border border-dashed border-line px-4 py-6 text-center text-xs text-slate-500"
        >
          没有可预览的内容，请重新选择文件。
        </div>
      </div>

      <template #footer>
        <button class="btn-ghost" :disabled="importRunning" @click="importOpen = false">
          取消
        </button>
        <button
          class="btn-primary"
          :disabled="importBusy || !importHasWrites"
          @click="confirmImport"
        >
          {{ importRunning ? '导入中…' : `确认导入（${importPreview?.created ?? 0} 新增 / ${importPreview?.updated ?? 0} 更新）` }}
        </button>
      </template>
    </BaseModal>

    <TestCaseDrawer
      v-model="drawerOpen"
      :case-data="null"
      :environments="environments"
      :templates="templates"
      :models="models"
      :saving="saving"
      @submit="submitCase"
    />
  </div>
</template>
