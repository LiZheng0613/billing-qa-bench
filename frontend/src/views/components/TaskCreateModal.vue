<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../../api'
import BaseModal from '../../components/BaseModal.vue'
import EmptyState from '../../components/EmptyState.vue'
import ScheduleField from '../../components/ScheduleField.vue'
import type { BatchTask, Environment, TestCase } from '../../types'
import { errorMessage } from '../../utils/errors'
import { caseTypeLabel } from '../../utils/format'
import {
  defaultSchedule,
  normalizeSchedule,
  schedulePayload,
  scheduleSummary,
  scheduleValidationError,
} from '../../utils/schedule'
import { useToastStore } from '../../stores/toast'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'created', task: BatchTask): void
}>()

const toast = useToastStore()

const cases = ref<TestCase[]>([])
const environments = ref<Environment[]>([])
const loadingOptions = ref(false)
const creating = ref(false)
const caseKeyword = ref('')

const form = reactive({
  name: '',
  description: '',
  environment_id: null as number | null,
  concurrency: 3,
  qps_limit: 5,
  retry_on_env_error: true,
  max_env_retries: 2,
  retry_backoff_seconds: 3,
  case_scope: 'SELECTED' as 'SELECTED' | 'ALL_ACTIVE',
  case_ids: [] as number[],
  auto_start: true,
  schedule: defaultSchedule(),
})

const activeCaseCount = computed(
  () => cases.value.filter((item) => item.status === 'ACTIVE').length,
)

const scopeCount = computed(() =>
  form.case_scope === 'ALL_ACTIVE' ? activeCaseCount.value : form.case_ids.length,
)

const filteredCases = computed(() => {
  const keyword = caseKeyword.value.trim().toLowerCase()
  return cases.value.filter((item) => {
    if (!keyword) return true
    return (
      item.code.toLowerCase().includes(keyword) ||
      item.name.toLowerCase().includes(keyword) ||
      (item.tags ?? []).some((tag) => tag.toLowerCase().includes(keyword))
    )
  })
})

async function loadAllCases(): Promise<TestCase[]> {
  const pageSize = 200
  const first = await api.cases.list({ page_size: pageSize, page: 1 })
  const all = [...first.items]
  const totalPages = Math.ceil(first.total / pageSize)
  for (let page = 2; page <= totalPages; page += 1) {
    const next = await api.cases.list({ page_size: pageSize, page })
    all.push(...next.items)
  }
  return all
}

async function loadOptions(): Promise<void> {
  loadingOptions.value = true
  try {
    const [allCases, envPage] = await Promise.all([
      loadAllCases(),
      api.environments.list({ page_size: 200 }),
    ])
    cases.value = allCases
    environments.value = envPage.items
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loadingOptions.value = false
  }
}

function resetForm(): void {
  const stamp = new Date()
  form.name = `回归任务 ${stamp.getMonth() + 1}/${stamp.getDate()} ${String(stamp.getHours()).padStart(2, '0')}:${String(stamp.getMinutes()).padStart(2, '0')}`
  form.description = ''
  form.environment_id = null
  form.concurrency = 3
  form.qps_limit = 5
  form.retry_on_env_error = true
  form.max_env_retries = 2
  form.retry_backoff_seconds = 3
  form.case_scope = 'SELECTED'
  form.case_ids = []
  form.auto_start = true
  form.schedule = defaultSchedule()
  caseKeyword.value = ''
}

function close(): void {
  emit('update:modelValue', false)
}

function toggleCase(item: TestCase): void {
  if (item.status !== 'ACTIVE') return
  const index = form.case_ids.indexOf(item.id)
  if (index >= 0) form.case_ids.splice(index, 1)
  else form.case_ids.push(item.id)
}

function selectAllFiltered(): void {
  const ids = filteredCases.value
    .filter((item) => item.status === 'ACTIVE')
    .map((item) => item.id)
  form.case_ids = Array.from(new Set([...form.case_ids, ...ids]))
}

function clearSelected(): void {
  form.case_ids = []
}

async function submitCreate(): Promise<void> {
  if (!form.name.trim()) {
    toast.error('请填写任务名称')
    return
  }
  if (form.case_scope === 'SELECTED' && form.case_ids.length === 0) {
    toast.error('请至少选择一个用例，或切换为「全部启用用例」')
    return
  }
  if (form.case_scope === 'ALL_ACTIVE' && activeCaseCount.value === 0) {
    toast.error('用例库中没有启用用例，无法批量执行')
    return
  }
  const scheduleError = scheduleValidationError(form.schedule)
  if (scheduleError) {
    toast.error(scheduleError)
    return
  }
  creating.value = true
  try {
    const created = await api.tasks.create({
      name: form.name.trim(),
      description: form.description.trim() || null,
      environment_id: form.environment_id,
      case_scope: form.case_scope,
      case_ids: form.case_scope === 'SELECTED' ? [...form.case_ids] : [],
      concurrency: form.concurrency,
      qps_limit: form.qps_limit,
      retry_on_env_error: form.retry_on_env_error,
      max_env_retries: form.max_env_retries,
      retry_backoff_seconds: form.retry_backoff_seconds,
      ...schedulePayload(normalizeSchedule(form.schedule)),
      auto_start: form.schedule.type === 'IMMEDIATE' && form.auto_start,
    })
    const scheduled = created.schedule_type !== 'IMMEDIATE'
    toast.success(
      scheduled
        ? `定时任务 ${created.code} 已登记：${scheduleSummary(normalizeSchedule(form.schedule))}`
        : `任务 ${created.code} 已创建，共 ${created.total_cases} 条用例`,
    )
    close()
    emit('created', created)
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    creating.value = false
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    resetForm()
    void loadOptions()
  },
)
</script>

<template>
  <BaseModal
    :model-value="modelValue"
    title="新建批量任务"
    subtitle="选择用例与执行方式（立即 / 指定时间 / 每天 / 每周），并设置并发、限速与失败重试策略"
    width="xl"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div class="space-y-4">
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label">任务名称</label>
            <input v-model="form.name" class="input" placeholder="例如：账单回归 2026-11" />
          </div>
          <div>
            <label class="label">执行环境</label>
            <select v-model="form.environment_id" class="input">
              <button><selectedcontent></selectedcontent></button>
              <option :value="null">不绑定环境（使用用例配置）</option>
              <option v-for="env in environments" :key="env.id" :value="env.id">
                {{ env.name }}（{{ env.code }}）
              </option>
            </select>
          </div>
        </div>
        <div>
          <label class="label">任务描述</label>
          <textarea v-model="form.description" class="input" rows="2" placeholder="可选，用于标记本次回归目的" />
        </div>
        <ScheduleField v-model="form.schedule" :disabled="creating" />
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label">并发数</label>
            <input v-model.number="form.concurrency" class="input" type="number" min="1" max="20" />
          </div>
          <div>
            <label class="label">QPS 上限</label>
            <input v-model.number="form.qps_limit" class="input" type="number" min="0.1" max="100" step="0.5" />
          </div>
        </div>
        <div class="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <label class="flex items-start gap-2 text-sm text-slate-700">
            <input
              v-model="form.retry_on_env_error"
              type="checkbox"
              class="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white"
            />
            <span>
              环境类失败自动重试
              <span class="block text-[11px] text-slate-500">
                仅重试连接失败、超时、HTTP 429/5xx、SSE 空响应等环境故障；规则不通过、裁判低分等业务失败不会重试。
              </span>
            </span>
          </label>
          <div
            class="grid gap-3 sm:grid-cols-2"
            :class="form.retry_on_env_error ? '' : 'pointer-events-none opacity-40'"
          >
            <div>
              <label class="label">最大重试次数</label>
              <input
                v-model.number="form.max_env_retries"
                class="input"
                type="number"
                min="0"
                max="10"
                :disabled="!form.retry_on_env_error"
              />
            </div>
            <div>
              <label class="label">基础退避（秒）</label>
              <input
                v-model.number="form.retry_backoff_seconds"
                class="input"
                type="number"
                min="0"
                max="60"
                step="0.5"
                :disabled="!form.retry_on_env_error"
              />
            </div>
          </div>
          <p class="text-[11px] text-slate-500">
            第 n 次重试前等待 = 基础退避 × 2^(n-1)，重试同样受并发数与 QPS 限制，终止会立即生效。
          </p>
        </div>
        <label
          v-if="form.schedule.type === 'IMMEDIATE'"
          class="flex items-center gap-2 text-sm text-slate-700"
        >
          <input v-model="form.auto_start" type="checkbox" class="h-4 w-4 rounded border-slate-300 bg-white" />
          创建后立即启动
        </label>
      </div>

      <div class="flex min-h-0 flex-col rounded-xl border border-slate-200 bg-slate-50/80">
        <div class="space-y-2 border-b border-slate-200 p-3">
          <div class="flex items-center justify-between">
            <span class="text-xs font-medium text-slate-700">执行范围</span>
            <span class="text-[11px] text-slate-500">
              本次 {{ scopeCount }} 条 / 库内启用 {{ activeCaseCount }} 条
            </span>
          </div>
          <label
            class="flex cursor-pointer items-start gap-2 rounded-lg border p-2 transition"
            :class="
              form.case_scope === 'SELECTED'
                ? 'border-indigo-200 bg-indigo-50'
                : 'border-slate-200 hover:bg-slate-100/70'
            "
          >
            <input
              v-model="form.case_scope"
              type="radio"
              value="SELECTED"
              class="mt-0.5 h-4 w-4 border-slate-300 bg-white"
            />
            <span class="min-w-0 text-xs text-slate-800">
              已选用例（{{ form.case_ids.length }} 条）
              <span class="block text-[10px] text-slate-600">下方逐条勾选后执行。</span>
            </span>
          </label>
          <label
            class="flex cursor-pointer items-start gap-2 rounded-lg border p-2 transition"
            :class="
              form.case_scope === 'ALL_ACTIVE'
                ? 'border-indigo-200 bg-indigo-50'
                : 'border-slate-200 hover:bg-slate-100/70'
            "
          >
            <input
              v-model="form.case_scope"
              type="radio"
              value="ALL_ACTIVE"
              class="mt-0.5 h-4 w-4 border-slate-300 bg-white"
            />
            <span class="min-w-0 text-xs text-slate-800">
              全部启用用例（{{ activeCaseCount }} 条，跨分页）
              <span class="block text-[10px] text-slate-600">
                自动执行用例库全部启用用例，不受勾选与分页限制。
              </span>
            </span>
          </label>
          <template v-if="form.case_scope === 'SELECTED'">
            <input v-model="caseKeyword" class="input !py-1.5 text-xs" placeholder="搜索编号 / 名称 / 标签" />
            <div class="flex gap-2">
              <button class="btn-ghost btn-xs" @click="selectAllFiltered">
                全选筛选结果（{{ filteredCases.length }}）
              </button>
              <button class="btn-ghost btn-xs" @click="clearSelected">清空</button>
            </div>
          </template>
        </div>
        <div
          class="max-h-[320px] flex-1 overflow-y-auto p-2"
          :class="form.case_scope === 'ALL_ACTIVE' ? 'pointer-events-none opacity-40' : ''"
        >
          <div v-if="loadingOptions" class="px-3 py-6 text-center text-xs text-slate-500">
            正在加载用例…
          </div>
          <label
            v-for="item in filteredCases"
            :key="item.id"
            class="flex cursor-pointer items-start gap-2 rounded-lg px-2 py-2 transition hover:bg-slate-100"
          >
            <input
              type="checkbox"
              class="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white"
              :checked="form.case_ids.includes(item.id)"
              :disabled="item.status !== 'ACTIVE'"
              :title="item.status === 'ACTIVE' ? '选择该用例' : '仅启用用例可批量执行'"
              @change="toggleCase(item)"
            />
            <span class="min-w-0">
              <span class="block truncate font-mono text-[11px] text-slate-700">{{ item.code }}</span>
              <span class="block truncate text-[11px] text-slate-600">{{ item.name }}</span>
              <span class="mt-0.5 inline-flex items-center gap-1 text-[10px] text-slate-500">
                {{ caseTypeLabel(item.case_type) }} · v{{ item.current_version }}
              </span>
            </span>
          </label>
          <EmptyState v-if="!loadingOptions && filteredCases.length === 0" compact title="没有匹配的用例" />
        </div>
      </div>
    </div>

    <template #footer>
      <button class="btn-ghost" @click="close">取消</button>
      <button class="btn-primary" :disabled="creating" @click="submitCreate">
        {{
          creating
            ? '创建中…'
            : form.schedule.type === 'IMMEDIATE'
              ? `创建并执行（${scopeCount} 条）`
              : `创建定时任务（${scopeCount} 条）`
        }}
      </button>
    </template>
  </BaseModal>
</template>
