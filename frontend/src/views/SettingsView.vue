<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import BaseModal from '../components/BaseModal.vue'
import EmptyState from '../components/EmptyState.vue'
import StatusPill from '../components/StatusPill.vue'
import type {
  Environment,
  EnvironmentPayload,
  EnvVariableInput,
  ModelConfig,
  ModelPayload,
  ModelRole,
  ModelTestResult,
  PreHookConfig,
} from '../types'
import { errorMessage } from '../utils/errors'
import {
  formatDateTime,
  formatMs,
  parseJsonObject,
  roleLabel,
} from '../utils/format'
import { useToastStore } from '../stores/toast'

const toast = useToastStore()

type TabKey = 'environments' | 'models'
const activeTab = ref<TabKey>('environments')

// ------------------------------------------------------------------ 环境
const environments = ref<Environment[]>([])
const envLoading = ref(false)
const envSaving = ref(false)
const envCreating = ref(false)
const envModalOpen = ref(false)
const editingEnv = ref<Environment | null>(null)
const checkingId = ref<number | null>(null)

const envForm = reactive({
  code: '',
  name: '',
  description: '',
  base_url: '',
  health_path: '/health',
  api_method: 'POST',
  api_path: '/api/chat',
  is_stream: false,
  timeout_seconds: 60,
  verify_ssl: true,
  is_active: true,
  headersText: '',
  bodyText: '',
  extractorsText: '',
  preHookEnabled: false,
  preHookText: '',
})

const variablesOpen = ref(false)
const variablesSaving = ref(false)
const variablesEnv = ref<Environment | null>(null)
const variableRows = ref<Array<{ key: string; value: string; is_secret: boolean; description: string }>>([])

const envTitle = computed(() =>
  editingEnv.value ? `编辑环境 · ${editingEnv.value.code}` : '新建执行环境',
)

function prettyJson(value: unknown): string {
  if (value === null || value === undefined) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return ''
  }
}

function parseOptionalObject(text: string, label: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  try {
    return parseJsonObject(trimmed)
  } catch (exc) {
    throw new Error(`${label} JSON 格式不正确：${errorMessage(exc, '请检查括号与引号')}`)
  }
}

async function loadEnvironments(): Promise<void> {
  envLoading.value = true
  try {
    const page = await api.environments.list({ page_size: 200 })
    environments.value = page.items
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    envLoading.value = false
  }
}

function resetEnvForm(): void {
  const source = editingEnv.value
  envForm.code = source?.code ?? ''
  envForm.name = source?.name ?? ''
  envForm.description = source?.description ?? ''
  envForm.base_url = source?.base_url ?? 'http://127.0.0.1:8002'
  envForm.health_path = source?.health_path ?? '/health'
  envForm.api_method = source?.api_method ?? 'POST'
  envForm.api_path = source?.api_path ?? '/api/chat'
  envForm.is_stream = source?.is_stream ?? false
  envForm.timeout_seconds = source?.timeout_seconds ?? 60
  envForm.verify_ssl = source?.verify_ssl ?? true
  envForm.is_active = source?.is_active ?? true
  envForm.headersText = prettyJson(source?.headers_template)
  envForm.bodyText = prettyJson(source?.body_template)
  envForm.extractorsText = prettyJson(source?.extractors)
  envForm.preHookEnabled = Boolean(source?.pre_hook?.enabled)
  envForm.preHookText = source?.pre_hook ? prettyJson(source.pre_hook) : ''
}

function openCreateEnv(): void {
  editingEnv.value = null
  resetEnvForm()
  envModalOpen.value = true
}

function openEditEnv(item: Environment): void {
  editingEnv.value = item
  resetEnvForm()
  envModalOpen.value = true
}

async function submitEnv(): Promise<void> {
  if (!envForm.code.trim()) {
    toast.error('请填写环境编码')
    return
  }
  if (!envForm.name.trim()) {
    toast.error('请填写环境名称')
    return
  }
  if (!/^https?:\/\//.test(envForm.base_url.trim())) {
    toast.error('Base URL 需以 http:// 或 https:// 开头')
    return
  }

  let headers: Record<string, string> | null = null
  let body: Record<string, unknown> | null = null
  let extractors: Record<string, string> | null = null
  let preHook: PreHookConfig | null = null
  try {
    const parsedHeaders = parseOptionalObject(envForm.headersText, '请求头模板')
    if (parsedHeaders) {
      headers = Object.fromEntries(
        Object.entries(parsedHeaders).map(([key, value]) => [
          key,
          typeof value === 'string' ? value : JSON.stringify(value),
        ]),
      )
    }
    body = parseOptionalObject(envForm.bodyText, '请求体模板')
    const parsedExtractors = parseOptionalObject(envForm.extractorsText, '字段提取器')
    if (parsedExtractors) {
      extractors = Object.fromEntries(
        Object.entries(parsedExtractors).map(([key, value]) => [
          key,
          typeof value === 'string' ? value : JSON.stringify(value),
        ]),
      )
    }
    if (envForm.preHookEnabled) {
      const parsedPreHook = parseOptionalObject(envForm.preHookText, '前置鉴权钩子')
      preHook = {
        enabled: true,
        method: 'POST',
        path: '/api/auth/token',
        headers: {},
        body: null,
        token_jsonpath: '$.data.token',
        token_var: 'auth_token',
        cache_seconds: 300,
        ...(parsedPreHook ?? {}),
      } as unknown as PreHookConfig
    }
  } catch (exc) {
    toast.error(errorMessage(exc))
    return
  }

  const timeout = Number(envForm.timeout_seconds)
  if (!Number.isFinite(timeout) || timeout <= 0) {
    toast.error('超时时间需为正数')
    return
  }

  const payload: EnvironmentPayload = {
    code: envForm.code.trim(),
    name: envForm.name.trim(),
    description: envForm.description.trim() || null,
    base_url: envForm.base_url.trim(),
    health_path: envForm.health_path.trim() || '/health',
    api_method: envForm.api_method,
    api_path: envForm.api_path.trim() || '/api/chat',
    is_stream: envForm.is_stream,
    timeout_seconds: timeout,
    verify_ssl: envForm.verify_ssl,
    is_active: envForm.is_active,
    headers_template: headers,
    body_template: body,
    extractors,
    pre_hook: preHook,
  }
  if (!editingEnv.value) {
    if (!body) delete payload.body_template
    if (!extractors) delete payload.extractors
  }

  envCreating.value = true
  envSaving.value = true
  try {
    if (editingEnv.value) {
      const { code: _code, ...updatePayload } = payload
      void _code
      const updated = await api.environments.update(editingEnv.value.id, updatePayload)
      toast.success(`环境 ${updated.code} 已更新`)
    } else {
      const created = await api.environments.create(payload)
      toast.success(`环境 ${created.code} 已创建`)
    }
    envModalOpen.value = false
    await loadEnvironments()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    envSaving.value = false
    envCreating.value = false
  }
}

async function removeEnv(item: Environment): Promise<void> {
  if (!window.confirm(`确认删除环境 ${item.code}？若仍被用例或任务引用将无法删除。`)) return
  try {
    const result = await api.environments.remove(item.id)
    toast.success(result.message)
    await loadEnvironments()
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function checkHealth(item: Environment): Promise<void> {
  checkingId.value = item.id
  try {
    const result = await api.environments.healthCheck(item.id)
    if (result.ok) {
      toast.success(`探活成功：${formatMs(result.latency_ms)} · ${result.message}`)
    } else {
      toast.error(`探活失败：${result.message}`)
    }
    await loadEnvironments()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    checkingId.value = null
  }
}

function openVariables(item: Environment): void {
  variablesEnv.value = item
  variableRows.value = item.variables.map((variable) => ({
    key: variable.key,
    value: '',
    is_secret: variable.is_secret,
    description: variable.description ?? '',
  }))
  variablesOpen.value = true
}

function addVariableRow(): void {
  variableRows.value.push({ key: '', value: '', is_secret: false, description: '' })
}

async function submitVariables(): Promise<void> {
  if (!variablesEnv.value) return
  const rows = variableRows.value.filter((row) => row.key.trim())
  const keys = rows.map((row) => row.key.trim())
  if (new Set(keys).size !== keys.length) {
    toast.error('变量 Key 不能重复')
    return
  }
  const invalid = keys.find((key) => !/^[A-Za-z0-9_]+$/.test(key))
  if (invalid) {
    toast.error(`变量 Key「${invalid}」只能包含字母、数字与下划线`)
    return
  }
  const variables: EnvVariableInput[] = rows.map((row) => ({
    key: row.key.trim(),
    value: row.value.trim() ? row.value : null,
    is_secret: row.is_secret,
    description: row.description.trim() || null,
  }))
  variablesSaving.value = true
  try {
    await api.environments.replaceVariables(variablesEnv.value.id, variables)
    toast.success('环境变量已保存')
    variablesOpen.value = false
    await loadEnvironments()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    variablesSaving.value = false
  }
}

// ------------------------------------------------------------------ 模型
const models = ref<ModelConfig[]>([])
const modelLoading = ref(false)
const modelSaving = ref(false)
const modelModalOpen = ref(false)
const editingModel = ref<ModelConfig | null>(null)
const testingId = ref<number | null>(null)
const testResults = ref<Record<number, ModelTestResult>>({})

const modelForm = reactive({
  code: '',
  name: '',
  role: 'SIMULATOR' as ModelRole,
  provider: 'openai-compatible',
  base_url: 'http://127.0.0.1:8001/v1',
  model_name: '',
  api_key: '',
  temperatureText: '',
  topPText: '',
  max_tokens: 2048,
  timeout_seconds: 60,
  rpm_limit: 60,
  max_retries: 3,
  response_format_json: true,
  is_active: true,
  extraText: '',
})

const modelTitle = computed(() =>
  editingModel.value ? `编辑模型 · ${editingModel.value.code}` : '注册新模型',
)

async function loadModels(): Promise<void> {
  modelLoading.value = true
  try {
    const page = await api.models.list({ page_size: 200 })
    models.value = page.items
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    modelLoading.value = false
  }
}

function resetModelForm(): void {
  const source = editingModel.value
  modelForm.code = source?.code ?? ''
  modelForm.name = source?.name ?? ''
  modelForm.role = (source?.role as ModelRole) ?? 'SIMULATOR'
  modelForm.provider = source?.provider ?? 'openai-compatible'
  modelForm.base_url = source?.base_url ?? 'http://127.0.0.1:8001/v1'
  modelForm.model_name = source?.model_name ?? ''
  modelForm.api_key = ''
  modelForm.temperatureText =
    source?.temperature === null || source?.temperature === undefined ? '' : String(source.temperature)
  modelForm.topPText = source?.top_p === null || source?.top_p === undefined ? '' : String(source.top_p)
  modelForm.max_tokens = source?.max_tokens ?? 2048
  modelForm.timeout_seconds = source?.timeout_seconds ?? 60
  modelForm.rpm_limit = source?.rpm_limit ?? 60
  modelForm.max_retries = source?.max_retries ?? 3
  modelForm.response_format_json = source?.response_format_json ?? true
  modelForm.is_active = source?.is_active ?? true
  modelForm.extraText = prettyJson(source?.extra_params)
}

function openCreateModel(): void {
  editingModel.value = null
  resetModelForm()
  modelModalOpen.value = true
}

function openEditModel(item: ModelConfig): void {
  editingModel.value = item
  resetModelForm()
  modelModalOpen.value = true
}

function optionalNumber(text: string): number | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

async function submitModel(): Promise<void> {
  if (!modelForm.code.trim()) {
    toast.error('请填写模型编码')
    return
  }
  if (!modelForm.name.trim()) {
    toast.error('请填写模型名称')
    return
  }
  if (!modelForm.model_name.trim()) {
    toast.error('请填写模型标识（model_name）')
    return
  }
  if (!/^https?:\/\//.test(modelForm.base_url.trim())) {
    toast.error('Base URL 需以 http:// 或 https:// 开头')
    return
  }

  const temperature = optionalNumber(modelForm.temperatureText)
  if (modelForm.temperatureText.trim() && temperature === null) {
    toast.error('温度需为 0~2 的数字')
    return
  }
  const topP = optionalNumber(modelForm.topPText)
  if (modelForm.topPText.trim() && (topP === null || topP <= 0 || topP > 1)) {
    toast.error('top_p 需为 0~1 之间的数字')
    return
  }

  let extraParams: Record<string, unknown> | null = null
  try {
    extraParams = parseOptionalObject(modelForm.extraText, '扩展参数')
  } catch (exc) {
    toast.error(errorMessage(exc))
    return
  }

  const numericFields: Array<[string, number, number]> = [
    ['max_tokens', Number(modelForm.max_tokens), 1],
    ['timeout_seconds', Number(modelForm.timeout_seconds), 1],
    ['rpm_limit', Number(modelForm.rpm_limit), 1],
    ['max_retries', Number(modelForm.max_retries), 0],
  ]
  for (const [label, value, min] of numericFields) {
    if (!Number.isFinite(value) || value < min) {
      toast.error(`${label} 需为不小于 ${min} 的数字`)
      return
    }
  }

  const payload: ModelPayload = {
    code: modelForm.code.trim(),
    name: modelForm.name.trim(),
    role: modelForm.role,
    provider: modelForm.provider.trim() || 'openai-compatible',
    base_url: modelForm.base_url.trim(),
    model_name: modelForm.model_name.trim(),
    api_key: modelForm.api_key.trim() || null,
    temperature,
    top_p: topP,
    max_tokens: Number(modelForm.max_tokens),
    timeout_seconds: Number(modelForm.timeout_seconds),
    rpm_limit: Number(modelForm.rpm_limit),
    max_retries: Number(modelForm.max_retries),
    response_format_json: modelForm.response_format_json,
    extra_params: extraParams,
    is_active: modelForm.is_active,
  }

  modelSaving.value = true
  try {
    if (editingModel.value) {
      const { code: _code, ...updatePayload } = payload
      void _code
      const updated = await api.models.update(editingModel.value.id, updatePayload)
      toast.success(`模型 ${updated.code} 已更新`)
    } else {
      const created = await api.models.create(payload)
      toast.success(`模型 ${created.code} 已注册`)
    }
    modelModalOpen.value = false
    await loadModels()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    modelSaving.value = false
  }
}

async function removeModel(item: ModelConfig): Promise<void> {
  if (!window.confirm(`确认删除模型 ${item.code}？若仍被用例引用将无法删除。`)) return
  try {
    const result = await api.models.remove(item.id)
    toast.success(result.message)
    await loadModels()
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function testModel(item: ModelConfig): Promise<void> {
  testingId.value = item.id
  try {
    const result = await api.models.test(item.id)
    testResults.value = { ...testResults.value, [item.id]: result }
    if (result.ok) {
      toast.success(`连通成功：${formatMs(result.latency_ms)} · ${result.message}`)
    } else {
      toast.error(`连通失败：${result.message}`)
    }
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    testingId.value = null
  }
}

const modelRoleCount = computed(() => ({
  simulator: models.value.filter((item) => item.role === 'SIMULATOR').length,
  judge: models.value.filter((item) => item.role === 'JUDGE').length,
}))

onMounted(() => {
  void loadEnvironments()
  void loadModels()
})
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold text-slate-100">环境与模型</h2>
        <p class="mt-1 text-xs text-slate-500">
          配置被测系统接入（SSE / 非流式）、环境变量与鉴权钩子，并管理模拟器与裁判模型。
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button class="btn-ghost" @click="loadEnvironments(); loadModels()">刷新</button>
        <button v-if="activeTab === 'environments'" class="btn-primary" @click="openCreateEnv">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" d="M12 5v14M5 12h14" />
          </svg>
          新建环境
        </button>
        <button v-else class="btn-primary" @click="openCreateModel">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" d="M12 5v14M5 12h14" />
          </svg>
          注册模型
        </button>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-1 rounded-xl border border-slate-800 bg-slate-900/50 p-1">
      <button
        class="tab-btn"
        :class="activeTab === 'environments' ? 'tab-btn-active' : ''"
        @click="activeTab = 'environments'"
      >
        执行环境（{{ environments.length }}）
      </button>
      <button
        class="tab-btn"
        :class="activeTab === 'models' ? 'tab-btn-active' : ''"
        @click="activeTab = 'models'"
      >
        模型注册（{{ models.length }}）
      </button>
      <span class="ml-auto px-2 text-[11px] text-slate-500">
        模拟器 {{ modelRoleCount.simulator }} · 裁判 {{ modelRoleCount.judge }}
      </span>
    </div>

    <!-- ---------------------------------------------------------- 环境 -->
    <section v-if="activeTab === 'environments'" class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th">环境</th>
              <th class="th">接入配置</th>
              <th class="th">流式</th>
              <th class="th">变量</th>
              <th class="th">健康状态</th>
              <th class="th">启用</th>
              <th class="th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in environments" :key="item.id">
              <td class="td max-w-[240px]">
                <p class="text-sm font-medium text-slate-100">{{ item.name }}</p>
                <p class="mt-0.5 font-mono text-[11px] text-indigo-300">{{ item.code }}</p>
                <p class="mt-0.5 truncate text-[11px] text-slate-500" :title="item.description ?? ''">
                  {{ item.description || '—' }}
                </p>
              </td>
              <td class="td max-w-[260px] text-[11px] text-slate-400">
                <p class="truncate font-mono">{{ item.api_method }} {{ item.base_url }}{{ item.api_path }}</p>
                <p class="mt-0.5 truncate font-mono text-slate-500">探活 {{ item.health_path }}</p>
                <p class="mt-0.5 text-slate-500">
                  超时 {{ item.timeout_seconds }}s · SSL {{ item.verify_ssl ? '校验' : '忽略' }}
                  <span v-if="item.pre_hook?.enabled" class="text-amber-400"> · 前置鉴权</span>
                </p>
              </td>
              <td class="td">
                <span
                  class="rounded-md px-1.5 py-0.5 text-[10px]"
                  :class="item.is_stream ? 'bg-sky-500/10 text-sky-300' : 'bg-slate-800 text-slate-400'"
                >
                  {{ item.is_stream ? 'SSE 流式' : '非流式' }}
                </span>
              </td>
              <td class="td">
                <span class="tag">{{ item.variables.length }} 个</span>
              </td>
              <td class="td">
                <div class="space-y-1">
                  <StatusPill v-if="item.last_health_status" :status="item.last_health_status" size="sm" />
                  <span v-else class="text-[11px] text-slate-600">未探活</span>
                  <p v-if="item.last_health_at" class="text-[10px] text-slate-600">
                    {{ formatDateTime(item.last_health_at) }}
                    <span v-if="item.last_health_latency_ms !== null">
                      · {{ formatMs(item.last_health_latency_ms) }}
                    </span>
                  </p>
                </div>
              </td>
              <td class="td">
                <StatusPill :status="item.is_active ? 'ACTIVE' : 'DISABLED'" size="sm" />
              </td>
              <td class="td">
                <div class="flex flex-wrap justify-end gap-1">
                  <button
                    class="btn-ghost btn-xs"
                    :disabled="checkingId === item.id"
                    @click="checkHealth(item)"
                  >
                    {{ checkingId === item.id ? '探活中…' : '探活' }}
                  </button>
                  <button class="btn-ghost btn-xs" @click="openVariables(item)">变量</button>
                  <button class="btn-ghost btn-xs" @click="openEditEnv(item)">编辑</button>
                  <button class="btn-danger btn-xs" @click="removeEnv(item)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <EmptyState
          v-if="!envLoading && environments.length === 0"
          title="还没有执行环境"
          description="新建一个环境，填写被测系统的 Base URL、接口路径与字段提取器。"
        >
          <button class="btn-primary btn-xs mt-2" @click="openCreateEnv">新建环境</button>
        </EmptyState>
      </div>
    </section>

    <!-- ---------------------------------------------------------- 模型 -->
    <section v-else class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="table-shell">
          <thead>
            <tr>
              <th class="th">模型</th>
              <th class="th">角色</th>
              <th class="th">接入配置</th>
              <th class="th">参数</th>
              <th class="th">最近测试</th>
              <th class="th">启用</th>
              <th class="th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in models" :key="item.id">
              <td class="td max-w-[220px]">
                <p class="text-sm font-medium text-slate-100">{{ item.name }}</p>
                <p class="mt-0.5 font-mono text-[11px] text-indigo-300">{{ item.code }}</p>
                <p class="mt-0.5 truncate text-[11px] text-slate-500">{{ item.provider }}</p>
              </td>
              <td class="td">
                <span
                  class="rounded-md px-1.5 py-0.5 text-[10px]"
                  :class="
                    item.role === 'SIMULATOR'
                      ? 'bg-fuchsia-500/10 text-fuchsia-300'
                      : 'bg-emerald-500/10 text-emerald-300'
                  "
                >
                  {{ roleLabel(item.role) }}
                </span>
              </td>
              <td class="td max-w-[260px] text-[11px] text-slate-400">
                <p class="truncate font-mono">{{ item.base_url }}</p>
                <p class="mt-0.5 truncate font-mono text-slate-500">{{ item.model_name }}</p>
                <p class="mt-0.5 font-mono text-slate-600">Key {{ item.api_key_masked || '未设置' }}</p>
              </td>
              <td class="td text-[11px] text-slate-400">
                <p>温度 {{ item.effective_temperature }} · top_p {{ item.top_p ?? '默认' }}</p>
                <p class="mt-0.5 text-slate-500">max {{ item.max_tokens }} · 超时 {{ item.timeout_seconds }}s</p>
                <p class="mt-0.5 text-slate-500">RPM {{ item.rpm_limit }} · 重试 {{ item.max_retries }}</p>
              </td>
              <td class="td text-[11px]">
                <template v-if="testResults[item.id]">
                  <StatusPill :status="testResults[item.id].ok ? 'PASS' : 'FAIL'" size="sm" />
                  <p class="mt-1 text-[10px] text-slate-500">
                    {{ formatMs(testResults[item.id].latency_ms) }}
                  </p>
                </template>
                <span v-else class="text-slate-600">未测试</span>
              </td>
              <td class="td">
                <StatusPill :status="item.is_active ? 'ACTIVE' : 'DISABLED'" size="sm" />
              </td>
              <td class="td">
                <div class="flex flex-wrap justify-end gap-1">
                  <button
                    class="btn-ghost btn-xs"
                    :disabled="testingId === item.id"
                    @click="testModel(item)"
                  >
                    {{ testingId === item.id ? '测试中…' : '测试' }}
                  </button>
                  <button class="btn-ghost btn-xs" @click="openEditModel(item)">编辑</button>
                  <button class="btn-danger btn-xs" @click="removeModel(item)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <EmptyState
          v-if="!modelLoading && models.length === 0"
          title="还没有注册模型"
          description="至少注册一个模拟器模型与裁判模型，才能运行动态多轮与 LLM 裁决。"
        >
          <button class="btn-primary btn-xs mt-2" @click="openCreateModel">注册模型</button>
        </EmptyState>
      </div>
    </section>

    <!-- ---------------------------------------------------------- 环境表单 -->
    <BaseModal v-model="envModalOpen" :title="envTitle" width="xl" subtitle="接口模板使用 {{variable}} 占位符，运行时按环境变量替换">
      <div class="space-y-4">
        <div class="grid gap-3 sm:grid-cols-3">
          <div>
            <label class="label">环境编码</label>
            <input v-model="envForm.code" class="input font-mono" :disabled="Boolean(editingEnv)" placeholder="QA-MOCK" />
          </div>
          <div>
            <label class="label">环境名称</label>
            <input v-model="envForm.name" class="input" placeholder="QA Mock 环境" />
          </div>
          <div>
            <label class="label">超时（秒）</label>
            <input v-model.number="envForm.timeout_seconds" class="input" type="number" min="1" max="600" />
          </div>
        </div>
        <div>
          <label class="label">描述</label>
          <input v-model="envForm.description" class="input" placeholder="用途、负责人、注意事项" />
        </div>
        <div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_140px_minmax(0,1fr)]">
          <div>
            <label class="label">Base URL</label>
            <input v-model="envForm.base_url" class="input font-mono" placeholder="http://127.0.0.1:8002" />
          </div>
          <div>
            <label class="label">请求方法</label>
            <select v-model="envForm.api_method" class="input">
              <option value="POST">POST</option>
              <option value="GET">GET</option>
              <option value="PUT">PUT</option>
              <option value="PATCH">PATCH</option>
            </select>
          </div>
          <div>
            <label class="label">对话接口路径</label>
            <input v-model="envForm.api_path" class="input font-mono" placeholder="/api/chat" />
          </div>
        </div>
        <div class="grid gap-3 sm:grid-cols-3">
          <div>
            <label class="label">探活路径</label>
            <input v-model="envForm.health_path" class="input font-mono" placeholder="/health" />
          </div>
          <label class="mt-6 flex items-center gap-2 text-sm text-slate-300">
            <input v-model="envForm.is_stream" type="checkbox" class="h-4 w-4 rounded border-slate-600 bg-slate-900" />
            SSE 流式响应
          </label>
          <label class="mt-6 flex items-center gap-2 text-sm text-slate-300">
            <input v-model="envForm.verify_ssl" type="checkbox" class="h-4 w-4 rounded border-slate-600 bg-slate-900" />
            校验 SSL 证书
          </label>
        </div>

        <div>
          <label class="label">请求头模板（JSON，可选）</label>
          <textarea v-model="envForm.headersText" class="input font-mono text-xs" rows="3" placeholder='{"Authorization":"Bearer {{auth_token}}"}' />
        </div>
        <div class="grid gap-3 lg:grid-cols-2">
          <div>
            <label class="label">请求体模板（JSON，可选）</label>
            <textarea v-model="envForm.bodyText" class="input font-mono text-xs" rows="6" />
          </div>
          <div>
            <label class="label">字段提取器（JSONPath，可选）</label>
            <textarea v-model="envForm.extractorsText" class="input font-mono text-xs" rows="6" />
          </div>
        </div>

        <div class="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <label class="flex items-center gap-2 text-sm text-slate-300">
              <input
                v-model="envForm.preHookEnabled"
                type="checkbox"
                class="h-4 w-4 rounded border-slate-600 bg-slate-900"
              />
              启用前置鉴权钩子
            </label>
            <span class="text-[11px] text-slate-500">用于先获取 token 并写入环境变量，再调用对话接口</span>
          </div>
          <textarea
            v-if="envForm.preHookEnabled"
            v-model="envForm.preHookText"
            class="input mt-2 font-mono text-xs"
            rows="6"
            placeholder='{"method":"POST","path":"/api/auth/token","body":{"username":"demo"},"token_jsonpath":"$.data.token","token_var":"auth_token"}'
          />
        </div>

        <div class="flex flex-wrap items-center gap-6">
          <label class="flex items-center gap-2 text-sm text-slate-300">
            <input v-model="envForm.is_active" type="checkbox" class="h-4 w-4 rounded border-slate-600 bg-slate-900" />
            启用该环境
          </label>
        </div>
      </div>
      <template #footer>
        <button class="btn-ghost" @click="envModalOpen = false">取消</button>
        <button class="btn-primary" :disabled="envSaving || envCreating" @click="submitEnv">
          {{ envSaving ? '保存中…' : editingEnv ? '保存修改' : '创建环境' }}
        </button>
      </template>
    </BaseModal>

    <!-- ---------------------------------------------------------- 环境变量 -->
    <BaseModal
      v-model="variablesOpen"
      :title="variablesEnv ? `环境变量 · ${variablesEnv.code}` : '环境变量'"
      width="lg"
      subtitle="保存为全量覆盖；已有变量留空值表示保持原值，Key 移除即删除"
    >
      <div class="space-y-2">
        <div
          v-for="(row, index) in variableRows"
          :key="index"
          class="grid gap-2 rounded-xl border border-slate-800 bg-slate-950/40 p-2 sm:grid-cols-[150px_minmax(0,1fr)_70px_minmax(0,1fr)_auto]"
        >
          <input v-model="row.key" class="input font-mono text-xs" placeholder="KEY" />
          <input
            v-model="row.value"
            class="input font-mono text-xs"
            :type="row.is_secret ? 'password' : 'text'"
            placeholder="值（留空不修改）"
          />
          <label class="flex items-center justify-center gap-1 text-[11px] text-slate-400">
            <input v-model="row.is_secret" type="checkbox" class="h-3.5 w-3.5 rounded border-slate-600 bg-slate-900" />
            密钥
          </label>
          <input v-model="row.description" class="input text-xs" placeholder="说明" />
          <button class="btn-danger btn-xs" @click="variableRows.splice(index, 1)">删除</button>
        </div>
        <EmptyState v-if="variableRows.length === 0" compact title="暂无变量" description="添加变量后可用于请求头、请求体与鉴权钩子。" />
        <button class="btn-ghost btn-xs" @click="addVariableRow">添加变量</button>
      </div>
      <template #footer>
        <button class="btn-ghost" @click="variablesOpen = false">取消</button>
        <button class="btn-primary" :disabled="variablesSaving" @click="submitVariables">
          {{ variablesSaving ? '保存中…' : '保存变量' }}
        </button>
      </template>
    </BaseModal>

    <!-- ---------------------------------------------------------- 模型表单 -->
    <BaseModal v-model="modelModalOpen" :title="modelTitle" width="xl" subtitle="OpenAI 兼容协议，模拟器用于生成用户提问，裁判用于结构化评分">
      <div class="space-y-4">
        <div class="grid gap-3 sm:grid-cols-3">
          <div>
            <label class="label">模型编码</label>
            <input v-model="modelForm.code" class="input font-mono" :disabled="Boolean(editingModel)" placeholder="MOCK-SIM" />
          </div>
          <div>
            <label class="label">显示名称</label>
            <input v-model="modelForm.name" class="input" placeholder="Mock 模拟器" />
          </div>
          <div>
            <label class="label">角色</label>
            <select v-model="modelForm.role" class="input">
              <option value="SIMULATOR">模拟器模型（SIMULATOR）</option>
              <option value="JUDGE">裁判模型（JUDGE）</option>
            </select>
          </div>
        </div>
        <div class="grid gap-3 sm:grid-cols-[180px_minmax(0,1fr)]">
          <div>
            <label class="label">Provider</label>
            <input v-model="modelForm.provider" class="input font-mono" placeholder="openai-compatible" />
          </div>
          <div>
            <label class="label">Base URL</label>
            <input v-model="modelForm.base_url" class="input font-mono" placeholder="http://127.0.0.1:8001/v1" />
          </div>
        </div>
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label">模型标识（model_name）</label>
            <input v-model="modelForm.model_name" class="input font-mono" placeholder="gpt-4o-mini / mock-judge" />
          </div>
          <div>
            <label class="label">API Key{{ editingModel ? '（留空不修改）' : '' }}</label>
            <input v-model="modelForm.api_key" class="input font-mono" type="password" placeholder="sk-..." />
          </div>
        </div>

        <div class="grid gap-3 sm:grid-cols-4">
          <div>
            <label class="label">温度（可选）</label>
            <input v-model="modelForm.temperatureText" class="input" inputmode="decimal" placeholder="默认 0.2" />
          </div>
          <div>
            <label class="label">top_p（可选）</label>
            <input v-model="modelForm.topPText" class="input" inputmode="decimal" placeholder="默认" />
          </div>
          <div>
            <label class="label">max_tokens</label>
            <input v-model.number="modelForm.max_tokens" class="input" type="number" min="1" />
          </div>
          <div>
            <label class="label">超时（秒）</label>
            <input v-model.number="modelForm.timeout_seconds" class="input" type="number" min="1" max="600" />
          </div>
        </div>
        <div class="grid gap-3 sm:grid-cols-4">
          <div>
            <label class="label">RPM 限流</label>
            <input v-model.number="modelForm.rpm_limit" class="input" type="number" min="1" />
          </div>
          <div>
            <label class="label">最大重试</label>
            <input v-model.number="modelForm.max_retries" class="input" type="number" min="0" max="10" />
          </div>
          <label class="mt-6 flex items-center gap-2 text-sm text-slate-300">
            <input
              v-model="modelForm.response_format_json"
              type="checkbox"
              class="h-4 w-4 rounded border-slate-600 bg-slate-900"
            />
            强制 JSON 输出
          </label>
          <label class="mt-6 flex items-center gap-2 text-sm text-slate-300">
            <input v-model="modelForm.is_active" type="checkbox" class="h-4 w-4 rounded border-slate-600 bg-slate-900" />
            启用模型
          </label>
        </div>

        <div>
          <label class="label">扩展参数（JSON，可选）</label>
          <textarea v-model="modelForm.extraText" class="input font-mono text-xs" rows="4" placeholder='{"frequency_penalty":0}' />
        </div>
      </div>
      <template #footer>
        <button class="btn-ghost" @click="modelModalOpen = false">取消</button>
        <button class="btn-primary" :disabled="modelSaving" @click="submitModel">
          {{ modelSaving ? '保存中…' : editingModel ? '保存修改' : '注册模型' }}
        </button>
      </template>
    </BaseModal>
  </div>
</template>
