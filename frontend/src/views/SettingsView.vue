<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import BaseModal from '../components/BaseModal.vue'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import type {
  BasicAuthInput,
  Environment,
  EnvironmentPayload,
  EnvDebugChatResult,
  EnvPreviewResult,
  EnvVariableInput,
  ModelConfig,
  ModelConnectionType,
  ModelPayload,
  ModelRole,
  ModelTestResult,
  MtlsAuthInput,
  OpenCodeCatalogAgent,
  OpenCodeCatalogModel,
  PreHookConfig,
  SessionMode,
  TestCase,
  VariableOrigin,
} from '../types'
import { useConfirm } from '../composables/useConfirm'
import { errorMessage } from '../utils/errors'
import {
  formatDateTime,
  formatMs,
  parseJsonObject,
  roleLabel,
} from '../utils/format'
import { useToastStore } from '../stores/toast'

const toast = useToastStore()
const confirm = useConfirm()

type TabKey = 'environments' | 'models'
const activeTab = ref<TabKey>('environments')

// ------------------------------------------------------------------ 环境
const environments = ref<Environment[]>([])
const envLoading = ref(false)
const envSaving = ref(false)
const envCreating = ref(false)
const envModalOpen = ref(false)
const editingEnv = ref<Environment | null>(null)
// 敏感字段默认以密码框展示，勾选后在表单中明文显示明文。
const showSecrets = ref(false)

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
  basicAuthEnabled: false,
  basicAuthUsername: '',
  basicAuthPassword: '',
  mtlsEnabled: false,
  mtlsCertText: '',
  mtlsKeyText: '',
  mtlsKeyPassword: '',
  mtlsCaText: '',
  session_mode: 'SERVER_RETURNED' as SessionMode,
})

// ---- 报文预览（选一条示例用例带入其请求变量与会话绑定）----
const previewCases = ref<TestCase[]>([])
const previewCaseId = ref<number | null>(null)
const previewLoading = ref(false)
const previewResult = ref<EnvPreviewResult | null>(null)
const previewError = ref('')

const SESSION_MODE_OPTIONS: Array<{ value: SessionMode; label: string; desc: string }> = [
  {
    value: 'SERVER_RETURNED',
    label: '服务端返回会话',
    desc: '首轮不传会话值，由目标接口在响应中返回，平台回填后用于后续轮次。',
  },
  {
    value: 'CLIENT_GENERATED',
    label: '客户端生成会话',
    desc: '每次执行开始时由平台生成唯一会话值，通过模板占位符注入并贯穿所有轮次。',
  },
]

const RUNTIME_VARIABLES: Array<{ name: string; desc: string }> = [
  { name: 'query', desc: '当前轮用户提问（动态多轮为模拟器生成）' },
  { name: 'session_id', desc: '会话值，按会话绑定方式解析' },
  { name: 'history', desc: '历史消息数组（role/content）' },
  { name: 'history_text', desc: '历史消息拼接文本' },
  { name: 'turn', desc: '当前轮次序号，从 1 开始' },
  { name: 'timestamp_iso', desc: '当前请求时间（ISO 8601 字符串，每轮刷新）' },
]

const ORIGIN_META: Record<VariableOrigin, { label: string; cls: string }> = {
  RUNTIME: { label: '运行时', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
  CASE: { label: '用例', cls: 'bg-indigo-50 text-indigo-700 border-indigo-100' },
  ENV: { label: '环境变量', cls: 'bg-emerald-50 text-emerald-700 border-emerald-100' },
  SESSION: { label: '会话', cls: 'bg-sky-50 text-sky-700 border-sky-100' },
  PREHOOK: { label: '前置接口', cls: 'bg-amber-50 text-amber-700 border-amber-100' },
  MISSING: { label: '缺失', cls: 'bg-rose-50 text-rose-700 border-rose-100' },
}

const templatePlaceholders = computed(() => {
  const names: string[] = []
  const pattern = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g
  const scan = (text: string) => {
    pattern.lastIndex = 0
    let match: RegExpExecArray | null
    while ((match = pattern.exec(text))) {
      if (!names.includes(match[1])) names.push(match[1])
    }
  }
  scan(envForm.headersText)
  scan(envForm.bodyText)
  return names
})

const envVariableKeys = computed(() => (editingEnv.value?.variables ?? []).map((v) => v.key))

const prehookVariableKeys = computed(() => {
  const text = envForm.preHookText.trim()
  if (!envForm.preHookEnabled || !text) return [] as string[]
  try {
    const parsed = JSON.parse(text) as {
      extract?: Record<string, string> | null
      token_var?: string
    }
    if (parsed.extract && typeof parsed.extract === 'object') {
      return Object.keys(parsed.extract)
    }
    return parsed.token_var ? [parsed.token_var] : []
  } catch {
    return [] as string[]
  }
})

const previewBodyText = computed(() =>
  previewResult.value ? prettyJson(previewResult.value.body) : '',
)

function placeholderSource(name: string): VariableOrigin {
  if (name === 'session_id') return 'SESSION'
  if (RUNTIME_VARIABLES.some((item) => item.name === name)) return 'RUNTIME'
  if (prehookVariableKeys.value.includes(name)) return 'PREHOOK'
  if (envVariableKeys.value.includes(name)) return 'ENV'
  return 'CASE'
}

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

async function loadEnvironments(): Promise<boolean> {
  envLoading.value = true
  try {
    const page = await api.environments.list({ page_size: 200 })
    environments.value = page.items
    return true
  } catch (exc) {
    toast.error(errorMessage(exc))
    return false
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
  envForm.basicAuthEnabled = Boolean(source?.basic_auth)
  envForm.basicAuthUsername = source?.basic_auth?.username ?? ''
  envForm.basicAuthPassword = source?.basic_auth?.password ?? ''
  envForm.mtlsEnabled = Boolean(source?.mtls)
  envForm.mtlsCertText = source?.mtls?.client_cert_pem ?? ''
  envForm.mtlsKeyText = source?.mtls?.client_key_pem ?? ''
  envForm.mtlsKeyPassword = source?.mtls?.key_password ?? ''
  envForm.mtlsCaText = source?.mtls?.ca_cert_pem ?? ''
  // 编辑已有配置时默认明文展示原值；新建时保持密码框。
  showSecrets.value = Boolean(source)
  envForm.session_mode = (source?.session_mode ?? 'SERVER_RETURNED') as SessionMode
  previewCases.value = []
  previewCaseId.value = null
  previewResult.value = null
  previewError.value = ''
}

async function readPemFile(
  event: Event,
  target: 'cert' | 'key' | 'ca',
): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    if (target === 'cert') envForm.mtlsCertText = text
    else if (target === 'key') envForm.mtlsKeyText = text
    else envForm.mtlsCaText = text
  } catch (exc) {
    toast.error(errorMessage(exc, '读取证书文件失败'))
  } finally {
    input.value = ''
  }
}

async function loadPreviewCases(envId: number): Promise<void> {
  try {
    const page = await api.cases.list({ environment_id: envId, page_size: 200 })
    previewCases.value = [...page.items].sort((a, b) => b.id - a.id)
    previewCaseId.value = previewCases.value[0]?.id ?? null
    if (previewCaseId.value) await runPreview()
  } catch {
    previewCases.value = []
  }
}

async function runPreview(): Promise<void> {
  if (!editingEnv.value || !previewCaseId.value) return
  previewLoading.value = true
  previewError.value = ''
  try {
    previewResult.value = await api.environments.preview(
      editingEnv.value.id,
      previewCaseId.value,
    )
  } catch (exc) {
    previewResult.value = null
    previewError.value = errorMessage(exc)
  } finally {
    previewLoading.value = false
  }
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
  void loadPreviewCases(item.id)
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
  let basicAuth: BasicAuthInput | null = null
  if (envForm.basicAuthEnabled) {
    if (!envForm.basicAuthUsername.trim()) {
      toast.error('请填写 Basic 鉴权账号')
      return
    }
    if (!envForm.basicAuthPassword && !editingEnv.value?.basic_auth?.password_set) {
      toast.error('请填写 Basic 鉴权密码')
      return
    }
    basicAuth = {
      username: envForm.basicAuthUsername.trim(),
      password: envForm.basicAuthPassword || '',
    }
  }

  let mtls: MtlsAuthInput | null = null
  if (envForm.mtlsEnabled) {
    const certText = envForm.mtlsCertText.trim()
    const keyText = envForm.mtlsKeyText.trim()
    if (!certText && !editingEnv.value?.mtls?.cert_set) {
      toast.error('请填写或上传 mTLS 客户端证书')
      return
    }
    if (!keyText && !editingEnv.value?.mtls?.key_set) {
      toast.error('请填写或上传 mTLS 客户端私钥')
      return
    }
    if (certText && !certText.includes('-----BEGIN CERTIFICATE-----')) {
      toast.error('客户端证书需为 PEM 格式')
      return
    }
    if (
      keyText &&
      !/-----BEGIN (?:ENCRYPTED |RSA |EC )?PRIVATE KEY-----/.test(keyText)
    ) {
      toast.error('客户端私钥需为 PEM 格式')
      return
    }
    mtls = {
      client_cert_pem: certText || null,
      client_key_pem: keyText || null,
      key_password: envForm.mtlsKeyPassword || null,
      ca_cert_pem: envForm.mtlsCaText.trim() || null,
    }
  }
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
      const parsedPreHook = parseOptionalObject(envForm.preHookText, '前置接口钩子')
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
    basic_auth: basicAuth,
    mtls,
    session_mode: envForm.session_mode,
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
  const ok = await confirm({
    title: '删除环境',
    message: `确定删除环境 ${item.code} 吗？`,
    detail: '若仍被用例或任务引用将无法删除。',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  try {
    const result = await api.environments.remove(item.id)
    toast.success(result.message)
    await loadEnvironments()
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

// ---- 探活调试：选用例内容直发目标环境，不写执行记录 ----
const debugOpen = ref(false)
const debugEnv = ref<Environment | null>(null)
const debugCases = ref<TestCase[]>([])
const debugCaseId = ref<number | null>(null)
const debugQuery = ref('')
const debugLoadingCases = ref(false)
const debugSending = ref(false)
const debugResult = ref<EnvDebugChatResult | null>(null)
const debugError = ref('')

const debugRequestText = computed(() =>
  debugResult.value ? prettyJson(debugResult.value.request_preview) : '',
)
const debugResponseText = computed(() =>
  debugResult.value ? prettyJson(debugResult.value.response) : '',
)
const debugRawDataText = computed(() =>
  debugResult.value
    ? prettyJson({
        api_params: debugResult.value.api_params,
        raw_db_data: debugResult.value.raw_db_data,
      })
    : '',
)

function caseDebugQuery(item: TestCase): string {
  if (item.query) return item.query
  if (item.initial_query) return item.initial_query
  const first = item.turns?.[0]
  if (first && typeof first === 'object' && 'query' in first) {
    return String(first.query ?? '')
  }
  return ''
}

async function loadAllCasesForDebug(): Promise<TestCase[]> {
  // 分页拉取全量用例（后端单页上限 200），不按环境过滤，任意用例均可用于调试
  const all: TestCase[] = []
  let page = 1
  for (;;) {
    const result = await api.cases.list({ page, page_size: 200 })
    all.push(...result.items)
    if (all.length >= result.total) break
    page += 1
  }
  return all
}

async function openDebug(item: Environment): Promise<void> {
  debugEnv.value = item
  debugCases.value = []
  debugCaseId.value = null
  debugQuery.value = ''
  debugResult.value = null
  debugError.value = ''
  debugOpen.value = true
  debugLoadingCases.value = true
  try {
    const all = await loadAllCasesForDebug()
    debugCases.value = all.sort((a, b) => {
      // 绑定当前环境的用例排前面作为默认首选，其余用例仍全部可选
      const aBound = a.environment_id === item.id ? 0 : 1
      const bBound = b.environment_id === item.id ? 0 : 1
      return aBound - bBound || b.id - a.id
    })
    if (debugCases.value.length) {
      debugCaseId.value = debugCases.value[0].id
      applyDebugCase()
    }
  } catch (exc) {
    debugError.value = errorMessage(exc)
  } finally {
    debugLoadingCases.value = false
  }
}

// 非当前环境绑定的用例标注其归属环境，便于区分
function debugCaseEnvSuffix(item: TestCase): string {
  if (item.environment_id === debugEnv.value?.id) return ''
  return item.environment_name ? ` · 绑定环境：${item.environment_name}` : ' · 未绑定环境'
}

function applyDebugCase(): void {
  const item = debugCases.value.find((candidate) => candidate.id === debugCaseId.value)
  debugQuery.value = item ? caseDebugQuery(item) : ''
  debugResult.value = null
  debugError.value = ''
}

async function sendDebugMessage(): Promise<void> {
  if (!debugEnv.value) return
  if (!debugCaseId.value) {
    toast.error('请先选择一个用例')
    return
  }
  if (!debugQuery.value.trim()) {
    toast.error('调试消息内容不能为空')
    return
  }
  debugSending.value = true
  debugError.value = ''
  try {
    const result = await api.environments.debugChat(debugEnv.value.id, {
      case_id: debugCaseId.value,
      query: debugQuery.value.trim(),
    })
    debugResult.value = result
    if (result.ok) {
      toast.success(`调试成功：${formatMs(result.latency_ms)} · ${result.message}`)
    } else {
      toast.error(`调试失败：${result.message}`)
    }
  } catch (exc) {
    debugResult.value = null
    debugError.value = errorMessage(exc)
    toast.error(debugError.value)
  } finally {
    debugSending.value = false
  }
}

function openVariables(item: Environment): void {
  variablesEnv.value = item
  variableRows.value = item.variables.map((variable) => ({
    key: variable.key,
    value: variable.value_plain ?? '',
    is_secret: variable.is_secret,
    description: variable.description ?? '',
  }))
  showSecrets.value = true
  variablesOpen.value = true
}

function addVariableRow(): void {
  variableRows.value.push({ key: '', value: '', is_secret: false, description: '' })
}

async function removeVariableRow(index: number): Promise<void> {
  const key = variableRows.value[index]?.key?.trim()
  const ok = await confirm({
    title: '删除环境变量',
    message: key ? `确定删除环境变量 ${key} 吗？` : '确定删除这条环境变量吗？',
    detail: '保存后该变量将从环境中移除。',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  variableRows.value.splice(index, 1)
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
  connection_type: 'OPENAI_COMPATIBLE' as ModelConnectionType,
  provider: 'openai-compatible',
  base_url: 'http://127.0.0.1:8001/v1',
  serve_url: 'http://127.0.0.1',
  serve_port: 4096,
  serve_username: '',
  serve_password: '',
  opencode_agent: '',
  opencode_model: '',
  opencode_directory: '',
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

const isOpencodeModel = computed(() => modelForm.connection_type === 'OPENCODE_SERVE')

// OpenCode 可用模型/Agent：由 serve 的 /api/model、/api/agent 拉取，避免手填 provider/model 出错。
const catalogModels = ref<OpenCodeCatalogModel[]>([])
const catalogAgents = ref<OpenCodeCatalogAgent[]>([])
const catalogLoading = ref(false)
const catalogLoaded = ref(false)
const catalogError = ref('')
let catalogRequestSeq = 0

const catalogModelGroups = computed(() => {
  const groups: Array<{ provider: string; models: OpenCodeCatalogModel[] }> = []
  for (const model of catalogModels.value) {
    let group = groups.find((item) => item.provider === model.provider_id)
    if (!group) {
      group = { provider: model.provider_id, models: [] }
      groups.push(group)
    }
    group.models.push(model)
  }
  return groups
})

const currentCatalogModel = computed(
  () =>
    catalogModels.value.find((item) => item.value === modelForm.opencode_model.trim()) ?? null,
)

const catalogAgentOptions = computed(() => {
  const options = catalogAgents.value.filter((agent) => !agent.hidden)
  const current = modelForm.opencode_agent.trim()
  if (current && !options.some((agent) => agent.id === current)) {
    options.unshift({ id: current, name: current, mode: null, hidden: false })
  }
  return options
})

function invalidateCatalog(): void {
  catalogRequestSeq += 1
  catalogModels.value = []
  catalogAgents.value = []
  catalogLoaded.value = false
  catalogError.value = ''
  catalogLoading.value = false
}

async function loadCatalog(options: { silent?: boolean } = {}): Promise<void> {
  if (!isOpencodeModel.value) return
  const serveUrl = modelForm.serve_url.trim()
  if (!/^https?:\/\//.test(serveUrl)) {
    if (!options.silent) toast.error('请先填写正确的 OpenCode Serve 地址')
    return
  }
  const port = Number(modelForm.serve_port)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    if (!options.silent) toast.error('Serve 端口需为 1~65535 的整数')
    return
  }

  const seq = ++catalogRequestSeq
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const result = await api.models.opencodeCatalog({
      serve_url: serveUrl,
      serve_port: port,
      serve_username: modelForm.serve_username.trim() || null,
      serve_password: modelForm.serve_password.trim() || null,
      opencode_directory: modelForm.opencode_directory.trim() || null,
      model_id: editingModel.value?.id ?? null,
    })
    if (seq !== catalogRequestSeq) return
    catalogModels.value = result.models
    catalogAgents.value = result.agents
    catalogLoaded.value = true
    if (!options.silent) {
      toast.success(`已拉取 ${result.models.length} 个可用模型、${result.agents.length} 个 Agent`)
    }
  } catch (exc) {
    if (seq !== catalogRequestSeq) return
    catalogLoaded.value = false
    catalogError.value = errorMessage(exc)
    if (!options.silent) toast.error(catalogError.value)
  } finally {
    if (seq === catalogRequestSeq) catalogLoading.value = false
  }
}

function selectConnectionType(type: ModelConnectionType): void {
  if (modelForm.connection_type === type) return
  modelForm.connection_type = type
  invalidateCatalog()
}


const modelTitle = computed(() =>
  editingModel.value ? `编辑模型 · ${editingModel.value.code}` : '注册新模型',
)

async function loadModels(): Promise<boolean> {
  modelLoading.value = true
  try {
    const page = await api.models.list({ page_size: 200 })
    models.value = page.items
    return true
  } catch (exc) {
    toast.error(errorMessage(exc))
    return false
  } finally {
    modelLoading.value = false
  }
}

async function refreshConfig(): Promise<void> {
  const results = await Promise.all([loadEnvironments(), loadModels()])
  if (results.every(Boolean)) toast.success('刷新成功')
}

function resetModelForm(): void {
  const source = editingModel.value
  modelForm.code = source?.code ?? ''
  modelForm.name = source?.name ?? ''
  modelForm.role = (source?.role as ModelRole) ?? 'SIMULATOR'
  modelForm.connection_type =
    (source?.connection_type as ModelConnectionType) ?? 'OPENAI_COMPATIBLE'
  modelForm.provider = source?.provider ?? 'openai-compatible'
  modelForm.base_url = source?.base_url ?? 'http://127.0.0.1:8001/v1'
  modelForm.serve_url = source?.serve_url ?? 'http://127.0.0.1'
  modelForm.serve_port = source?.serve_port ?? 4096
  modelForm.serve_username = source?.serve_username ?? ''
  modelForm.serve_password = source?.serve_password ?? ''
  modelForm.opencode_agent = source?.opencode_agent ?? ''
  modelForm.opencode_model = source?.opencode_model ?? ''
  modelForm.opencode_directory = source?.opencode_directory ?? ''
  modelForm.model_name = source?.model_name ?? ''
  modelForm.api_key = source?.api_key ?? ''
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
  showSecrets.value = Boolean(source)
}

function openCreateModel(): void {
  editingModel.value = null
  resetModelForm()
  invalidateCatalog()
  modelModalOpen.value = true
}

function openEditModel(item: ModelConfig): void {
  editingModel.value = item
  resetModelForm()
  invalidateCatalog()
  modelModalOpen.value = true
  // 编辑 OpenCode 模型时自动拉一次目录，让选择框直接可用（密码由后端复用已保存值）。
  if (item.connection_type === 'OPENCODE_SERVE') {
    void loadCatalog({ silent: true })
  }
}

function optionalNumber(text: string): number | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function formatContextLimit(value: number | null): string {
  if (!value) return ''
  if (value >= 1000) return `${Math.round(value / 1000)}k`
  return String(value)
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
  if (isOpencodeModel.value) {
    if (!/^https?:\/\//.test(modelForm.serve_url.trim())) {
      toast.error('OpenCode Serve 地址需以 http:// 或 https:// 开头')
      return
    }
    const port = Number(modelForm.serve_port)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      toast.error('Serve 端口需为 1~65535 的整数')
      return
    }
    if (modelForm.opencode_model.trim() && !modelForm.opencode_model.includes('/')) {
      toast.error('OpenCode 模型需为 provider/model 格式')
      return
    }
  } else {
    if (!modelForm.model_name.trim()) {
      toast.error('请填写模型标识（model_name）')
      return
    }
    if (!/^https?:\/\//.test(modelForm.base_url.trim())) {
      toast.error('Base URL 需以 http:// 或 https:// 开头')
      return
    }
  }

  const modelName = isOpencodeModel.value
    ? modelForm.model_name.trim() ||
      modelForm.opencode_model.trim() ||
      modelForm.opencode_agent.trim() ||
      'opencode-default'
    : modelForm.model_name.trim()

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
    connection_type: modelForm.connection_type,
    provider:
      modelForm.provider.trim() ||
      (isOpencodeModel.value ? 'opencode-serve' : 'openai-compatible'),
    base_url: isOpencodeModel.value ? null : modelForm.base_url.trim(),
    model_name: modelName,
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
  if (isOpencodeModel.value) {
    payload.serve_url = modelForm.serve_url.trim()
    payload.serve_port = Number(modelForm.serve_port)
    payload.serve_username = modelForm.serve_username.trim() || null
    payload.serve_password = modelForm.serve_password.trim() || null
    payload.opencode_agent = modelForm.opencode_agent.trim() || null
    payload.opencode_model = modelForm.opencode_model.trim() || null
    payload.opencode_directory = modelForm.opencode_directory.trim() || null
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
  const ok = await confirm({
    title: '删除模型',
    message: `确定删除模型 ${item.code} 吗？`,
    detail: '若仍被用例引用将无法删除。',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
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
  <div class="space-y-4">
    <PageHeader
      eyebrow="Config · 接入配置"
      title="环境与模型"
      description="配置被测系统接入（SSE / 非流式）、环境变量与鉴权钩子，并管理模拟器与裁判模型。"
    >
      <template #actions>
        <button class="btn-ghost" @click="refreshConfig">刷新</button>
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
      </template>
    </PageHeader>

    <div class="flex flex-wrap items-center gap-1 rounded-tile border border-line bg-slate-100/70 p-1">
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
      <span class="ml-auto px-2 text-[11px] text-slate-600">
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
                <p class="text-sm font-medium text-slate-900">{{ item.name }}</p>
                <p class="mt-0.5 font-mono text-[11px] text-indigo-600">{{ item.code }}</p>
                <p class="mt-0.5 truncate text-[11px] text-slate-500" :title="item.description ?? ''">
                  {{ item.description || '—' }}
                </p>
              </td>
              <td class="td max-w-[260px] text-[11px] text-slate-600">
                <p class="truncate font-mono">{{ item.api_method }} {{ item.base_url }}{{ item.api_path }}</p>
                <p class="mt-0.5 truncate font-mono text-slate-500">探活 {{ item.health_path }}</p>
                <p class="mt-0.5 text-slate-500">
                  超时 {{ item.timeout_seconds }}s · SSL {{ item.verify_ssl ? '校验' : '忽略' }}
                  <span v-if="item.pre_hook?.enabled" class="text-amber-700"> · 前置接口</span>
                  <span v-if="item.basic_auth" class="text-emerald-700"> · Basic 鉴权</span>
                  <span v-if="item.mtls?.enabled" class="text-sky-700"> · mTLS</span>
                </p>
                <p class="mt-1">
                  <span
                    class="inline-flex items-center rounded-full border border-indigo-100 bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-600"
                  >
                    会话 {{ item.session_mode === 'CLIENT_GENERATED' ? '客户端生成' : '服务端返回' }}
                  </span>
                </p>
              </td>
              <td class="td">
                <span
                  class="rounded-md px-1.5 py-0.5 text-[10px]"
                  :class="item.is_stream ? 'bg-sky-50 text-sky-700' : 'bg-slate-100 text-slate-600'"
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
                  <span v-else class="text-[11px] text-slate-500">未探活</span>
                  <p v-if="item.last_health_at" class="text-[10px] text-slate-500">
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
                  <button class="btn-primary btn-xs" @click="openDebug(item)">调试</button>
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
                <p class="text-sm font-medium text-slate-900">{{ item.name }}</p>
                <p class="mt-0.5 font-mono text-[11px] text-indigo-600">{{ item.code }}</p>
                <p class="mt-0.5 truncate text-[11px] text-slate-500">{{ item.provider }}</p>
                <span
                  class="mt-1 inline-flex rounded-md px-1.5 py-0.5 text-[10px]"
                  :class="
                    item.connection_type === 'OPENCODE_SERVE'
                      ? 'bg-sky-50 text-sky-700'
                      : 'bg-slate-100 text-slate-600'
                  "
                >
                  {{ item.connection_type === 'OPENCODE_SERVE' ? 'OpenCode Serve' : 'OpenAI 兼容' }}
                </span>
              </td>
              <td class="td">
                <span
                  class="rounded-md px-1.5 py-0.5 text-[10px]"
                  :class="
                    item.role === 'SIMULATOR'
                      ? 'bg-fuchsia-500/10 text-fuchsia-700'
                      : item.role === 'CASE_AUTHOR'
                        ? 'bg-indigo-50 text-indigo-700'
                        : 'bg-emerald-50 text-emerald-700'
                  "
                >
                  {{ roleLabel(item.role) }}
                </span>
              </td>
              <td class="td max-w-[260px] text-[11px] text-slate-600">
                <template v-if="item.connection_type === 'OPENCODE_SERVE'">
                  <p class="truncate font-mono">{{ item.serve_url }}:{{ item.serve_port }}</p>
                  <p class="mt-0.5 truncate font-mono text-slate-500">
                    Agent {{ item.opencode_agent || '默认' }} · Model {{ item.opencode_model || '默认' }}
                  </p>
                  <p class="mt-0.5 truncate font-mono text-slate-500">
                    Auth {{ item.serve_username || '无' }} / {{ item.serve_password_masked || '未设置' }}
                  </p>
                  <p class="mt-0.5 truncate font-mono text-slate-500">
                    SSE · {{ item.model_name }}
                  </p>
                </template>
                <template v-else>
                  <p class="truncate font-mono">{{ item.base_url }}</p>
                  <p class="mt-0.5 truncate font-mono text-slate-500">{{ item.model_name }}</p>
                  <p class="mt-0.5 font-mono text-slate-500">Key {{ item.api_key_masked || '未设置' }}</p>
                </template>
              </td>
              <td class="td text-[11px] text-slate-600">
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
                <span v-else class="text-slate-500">未测试</span>
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
              <button><selectedcontent></selectedcontent></button>
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
          <label class="mt-6 flex items-center gap-2 text-sm text-slate-700">
            <input v-model="envForm.is_stream" type="checkbox" class="h-4 w-4 rounded border-slate-300 bg-white" />
            SSE 流式响应
          </label>
          <label class="mt-6 flex items-center gap-2 text-sm text-slate-700">
            <input v-model="envForm.verify_ssl" type="checkbox" class="h-4 w-4 rounded border-slate-300 bg-white" />
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

        <div class="grid gap-3 lg:grid-cols-2">
          <div class="space-y-2">
            <div class="flex items-baseline justify-between gap-2">
              <label class="label !mb-0">多轮会话绑定</label>
              <span class="text-[11px] text-slate-500">用例可在用例抽屉中覆盖</span>
            </div>
            <label
              v-for="option in SESSION_MODE_OPTIONS"
              :key="option.value"
              class="flex cursor-pointer items-start gap-2 rounded-xl border p-3 transition"
              :class="
                envForm.session_mode === option.value
                  ? 'border-indigo-300 bg-indigo-50/70'
                  : 'border-slate-200 bg-white hover:border-slate-300'
              "
            >
              <input
                v-model="envForm.session_mode"
                type="radio"
                :value="option.value"
                class="mt-0.5 h-4 w-4 border-slate-300 text-indigo-600"
              />
              <span class="space-y-0.5">
                <span class="block text-sm font-medium text-slate-800">{{ option.label }}</span>
                <span class="block text-[11px] leading-relaxed text-slate-500">{{ option.desc }}</span>
              </span>
            </label>
          </div>
          <div class="space-y-2">
            <div class="flex items-baseline justify-between gap-2">
              <label class="label !mb-0">可用占位符（{{ templatePlaceholders.length }}）</label>
              <span class="text-[11px] text-slate-500">从请求头/请求体模板自动识别</span>
            </div>
            <div class="max-h-48 space-y-1 overflow-auto rounded-xl border border-slate-200 bg-slate-50/80 p-2">
              <div
                v-if="!templatePlaceholders.length"
                class="px-1 py-2 text-[11px] text-slate-500"
              >
                模板中暂无 <code v-pre>{{ }}</code> 占位符
              </div>
              <div
                v-for="name in templatePlaceholders"
                :key="name"
                class="flex items-center justify-between gap-2 rounded-lg bg-white px-2 py-1"
              >
                <span class="font-mono text-[11px] text-slate-700">{{ name }}</span>
                <span
                  class="rounded-full border px-1.5 py-0.5 text-[10px]"
                  :class="ORIGIN_META[placeholderSource(name)].cls"
                >
                  {{ ORIGIN_META[placeholderSource(name)].label }}
                </span>
              </div>
            </div>
            <details class="rounded-xl border border-slate-200 bg-white p-2">
              <summary class="cursor-pointer text-[11px] font-medium text-slate-600">
                运行时可用变量
              </summary>
              <ul class="mt-1 space-y-1">
                <li
                  v-for="item in RUNTIME_VARIABLES"
                  :key="item.name"
                  class="flex gap-2 text-[11px] leading-relaxed"
                >
                  <span class="shrink-0 font-mono text-indigo-600">{{ item.name }}</span>
                  <span class="text-slate-500">{{ item.desc }}</span>
                </li>
              </ul>
            </details>
          </div>
        </div>

        <div class="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div class="text-sm font-medium text-slate-800">报文预览</div>
              <div class="text-[11px] text-slate-500">
                选一条示例用例，带入其请求变量与会话配置；基于已保存的环境配置渲染
              </div>
            </div>
            <div class="flex items-center gap-2">
              <select
                v-if="previewCases.length"
                v-model.number="previewCaseId"
                class="input h-8 max-w-[240px] text-xs"
                @change="runPreview"
              >
                <option v-for="item in previewCases" :key="item.id" :value="item.id">
                  {{ item.code }} · {{ item.name }}
                </option>
              </select>
              <button
                class="btn-ghost btn-xs"
                :disabled="previewLoading || !previewCaseId"
                @click="runPreview"
              >
                {{ previewLoading ? '渲染中…' : '刷新预览' }}
              </button>
            </div>
          </div>

          <div v-if="!editingEnv" class="mt-2 rounded-lg bg-white px-3 py-2 text-[11px] text-slate-500">
            保存环境后可预览实际请求报文。
          </div>
          <div
            v-else-if="!previewCases.length"
            class="mt-2 rounded-lg bg-white px-3 py-2 text-[11px] text-slate-500"
          >
            该环境暂无绑定用例，可在用例抽屉中指定环境后回来预览。
          </div>
          <div
            v-else-if="previewError"
            class="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-[11px] text-rose-700"
          >
            {{ previewError }}
          </div>
          <template v-else-if="previewResult">
            <div class="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
              <span
                class="rounded-full border border-indigo-100 bg-indigo-50 px-2 py-0.5 font-mono text-[10px] text-indigo-600"
              >
                会话模式 {{ previewResult.session_mode_resolved }}
              </span>
              <span
                v-if="previewResult.session_id"
                class="rounded-full border border-slate-200 bg-white px-2 py-0.5 font-mono text-[10px] text-slate-600"
              >
                session_id {{ previewResult.session_id }}
              </span>
            </div>
            <div
              v-if="previewResult.warnings.length"
              class="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-700"
            >
              {{ previewResult.warnings.join('；') }}
            </div>
            <div class="mt-2 grid gap-2 lg:grid-cols-2">
              <div>
                <div class="mb-1 text-[11px] font-medium text-slate-500">请求头</div>
                <pre
                  class="max-h-48 overflow-auto rounded-lg bg-slate-900 p-2 font-mono text-[11px] leading-relaxed text-slate-100"
                >{{ prettyJson(previewResult.headers) }}</pre>
              </div>
              <div>
                <div class="mb-1 text-[11px] font-medium text-slate-500">请求体</div>
                <pre
                  class="max-h-48 overflow-auto rounded-lg bg-slate-900 p-2 font-mono text-[11px] leading-relaxed text-slate-100"
                >{{ previewBodyText }}</pre>
              </div>
            </div>
            <div
              v-if="Object.keys(previewResult.origins).length"
              class="mt-2 flex flex-wrap gap-1.5"
            >
              <span
                v-for="(origin, name) in previewResult.origins"
                :key="name"
                class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]"
                :class="ORIGIN_META[origin].cls"
              >
                <span class="font-mono">{{ name }}</span>
                <span class="opacity-70">{{ ORIGIN_META[origin].label }}</span>
              </span>
            </div>
          </template>
        </div>

        <div class="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <label class="flex items-center gap-2 text-sm text-slate-700">
              <input
                v-model="envForm.basicAuthEnabled"
                type="checkbox"
                class="h-4 w-4 rounded border-slate-300 bg-white"
              />
              启用 Basic 鉴权
            </label>
            <span class="flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
              执行时自动生成 Authorization: Basic base64(账号:密码)
              <label class="flex items-center gap-1 text-slate-600">
                <input
                  v-model="showSecrets"
                  type="checkbox"
                  class="h-3.5 w-3.5 rounded border-slate-300 bg-white"
                />
                显示明文
              </label>
            </span>
          </div>
          <div v-if="envForm.basicAuthEnabled" class="mt-2 grid gap-2 sm:grid-cols-2">
            <input
              v-model="envForm.basicAuthUsername"
              class="input text-sm"
              placeholder="账号"
              autocomplete="off"
            />
            <input
              v-model="envForm.basicAuthPassword"
              class="input text-sm"
              :type="showSecrets ? 'text' : 'password'"
              :placeholder="
                envForm.basicAuthPassword
                  ? '已回显原值，可直接修改'
                  : editingEnv?.basic_auth?.password_set
                    ? '已配置，留空表示不修改'
                    : '密码'
              "
              autocomplete="new-password"
            />
          </div>
        </div>

        <div class="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <label class="flex items-center gap-2 text-sm text-slate-700">
              <input
                v-model="envForm.mtlsEnabled"
                type="checkbox"
                class="h-4 w-4 rounded border-slate-300 bg-white"
              />
              启用 mTLS 双向证书
            </label>
            <span class="text-[11px] text-slate-500">
              使用 client_cert.pem + client_key.pem 建立 TLS 客户端证书认证
            </span>
          </div>
          <div v-if="envForm.mtlsEnabled" class="mt-3 space-y-3">
            <div class="grid gap-3 lg:grid-cols-2">
              <div>
                <div class="mb-1 flex items-center justify-between gap-2">
                  <span class="text-[11px] font-medium text-slate-500">客户端证书（client_cert.pem）</span>
                  <label class="cursor-pointer text-[11px] text-indigo-600 hover:text-indigo-700">
                    选择文件
                    <input
                      type="file"
                      accept=".pem,.crt,.cer,.txt"
                      class="hidden"
                      @change="readPemFile($event, 'cert')"
                    />
                  </label>
                </div>
                <textarea
                  v-model="envForm.mtlsCertText"
                  class="input font-mono text-xs"
                  rows="4"
                  :placeholder="
                    envForm.mtlsCertText
                      ? '已回显原值，可直接修改'
                      : editingEnv?.mtls?.cert_set
                        ? '已配置，留空表示不修改'
                        : '-----BEGIN CERTIFICATE-----'
                  "
                />
              </div>
              <div>
                <div class="mb-1 flex items-center justify-between gap-2">
                  <span class="text-[11px] font-medium text-slate-500">客户端私钥（client_key.pem）</span>
                  <label class="cursor-pointer text-[11px] text-indigo-600 hover:text-indigo-700">
                    选择文件
                    <input
                      type="file"
                      accept=".pem,.key,.txt"
                      class="hidden"
                      @change="readPemFile($event, 'key')"
                    />
                  </label>
                </div>
                <textarea
                  v-model="envForm.mtlsKeyText"
                  class="input font-mono text-xs"
                  rows="4"
                  :placeholder="
                    envForm.mtlsKeyText
                      ? '已回显原值，可直接修改'
                      : editingEnv?.mtls?.key_set
                        ? '已配置，留空表示不修改'
                        : '-----BEGIN PRIVATE KEY-----'
                  "
                />
              </div>
            </div>
            <div class="grid gap-3 sm:grid-cols-2">
              <input
                v-model="envForm.mtlsKeyPassword"
                class="input text-sm"
                :type="showSecrets ? 'text' : 'password'"
                :placeholder="
                  envForm.mtlsKeyPassword
                    ? '已回显原值，可直接修改'
                    : editingEnv?.mtls?.key_password_set
                      ? '私钥口令已配置，留空表示不修改'
                      : '私钥口令（可选）'
                "
                autocomplete="new-password"
              />
              <div>
                <div class="mb-1 flex items-center justify-between gap-2">
                  <span class="text-[11px] font-medium text-slate-500">自定义 CA（可选）</span>
                  <label class="cursor-pointer text-[11px] text-indigo-600 hover:text-indigo-700">
                    选择文件
                    <input
                      type="file"
                      accept=".pem,.crt,.cer,.txt"
                      class="hidden"
                      @change="readPemFile($event, 'ca')"
                    />
                  </label>
                </div>
                <textarea
                  v-model="envForm.mtlsCaText"
                  class="input font-mono text-xs"
                  rows="3"
                  :placeholder="
                    envForm.mtlsCaText
                      ? '已回显原值，可直接修改'
                      : editingEnv?.mtls?.ca_set
                        ? '已配置，留空表示不修改'
                        : '用于校验服务端证书'
                  "
                />
              </div>
            </div>
          </div>
        </div>

        <div class="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <label class="flex items-center gap-2 text-sm text-slate-700">
              <input
                v-model="envForm.preHookEnabled"
                type="checkbox"
                class="h-4 w-4 rounded border-slate-300 bg-white"
              />
              启用前置接口钩子
            </label>
            <span class="text-[11px] text-slate-500">
              执行一次获取 conversationId / token 等变量，再调用对话接口
            </span>
          </div>
          <textarea
            v-if="envForm.preHookEnabled"
            v-model="envForm.preHookText"
            class="input mt-2 font-mono text-xs"
            rows="6"
            placeholder='{"method":"POST","path":"/api/v1/conversation","body":{"userid":"{{userid}}"},"extract":{"conversationId":"$.data.conversationId"}}'
          />
        </div>

        <div class="flex flex-wrap items-center gap-6">
          <label class="flex items-center gap-2 text-sm text-slate-700">
            <input v-model="envForm.is_active" type="checkbox" class="h-4 w-4 rounded border-slate-300 bg-white" />
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
      subtitle="保存为全量覆盖；已有变量值已回填，可直接修改，Key 移除即删除"
    >
      <div class="space-y-2">
        <div class="flex items-center justify-end">
          <label class="flex items-center gap-1 text-[11px] text-slate-600">
            <input
              v-model="showSecrets"
              type="checkbox"
              class="h-3.5 w-3.5 rounded border-slate-300 bg-white"
            />
            显示明文
          </label>
        </div>
        <div
          v-for="(row, index) in variableRows"
          :key="index"
          class="grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-2 sm:grid-cols-[150px_minmax(0,1fr)_70px_minmax(0,1fr)_auto]"
        >
          <input v-model="row.key" class="input font-mono text-xs" placeholder="KEY" />
          <input
            v-model="row.value"
            class="input font-mono text-xs"
            :type="row.is_secret && !showSecrets ? 'password' : 'text'"
            placeholder="值"
          />
          <label class="flex items-center justify-center gap-1 text-[11px] text-slate-600">
            <input v-model="row.is_secret" type="checkbox" class="h-3.5 w-3.5 rounded border-slate-300 bg-white" />
            密钥
          </label>
          <input v-model="row.description" class="input text-xs" placeholder="说明" />
          <button class="btn-danger btn-xs" @click="removeVariableRow(index)">删除</button>
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

    <!-- ---------------------------------------------------------- 探活调试 -->
    <BaseModal
      v-model="debugOpen"
      :title="debugEnv ? `探活调试 · ${debugEnv.code}` : '探活调试'"
      width="xl"
      subtitle="选择用例内容直发目标执行环境，仅回显报文；本次执行不记录数据"
    >
      <div class="space-y-3">
        <div class="rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
          调试发送不会写入执行记录、任务结果或更新探活状态，仅用于连通性与功能验证。
        </div>

        <div
          v-if="debugEnv"
          class="grid gap-2 rounded-xl border border-slate-200 bg-slate-50/80 p-3 text-[11px] text-slate-600 sm:grid-cols-2"
        >
          <p class="truncate font-mono">
            {{ debugEnv.api_method }} {{ debugEnv.base_url }}{{ debugEnv.api_path }}
          </p>
          <p class="flex flex-wrap items-center gap-1 sm:justify-end">
            <span class="rounded-md bg-white px-1.5 py-0.5">
              {{ debugEnv.is_stream ? 'SSE 流式' : '非流式' }}
            </span>
            <span class="rounded-md bg-white px-1.5 py-0.5">
              会话 {{ debugEnv.session_mode === 'CLIENT_GENERATED' ? '客户端生成' : '服务端返回' }}
            </span>
            <span v-if="debugEnv.pre_hook?.enabled" class="rounded-md bg-white px-1.5 py-0.5 text-amber-700">
              前置接口
            </span>
            <span v-if="debugEnv.basic_auth" class="rounded-md bg-white px-1.5 py-0.5 text-emerald-700">
              Basic 鉴权
            </span>
            <span v-if="debugEnv.mtls?.enabled" class="rounded-md bg-white px-1.5 py-0.5 text-sky-700">
              mTLS
            </span>
          </p>
        </div>

        <div>
          <label class="label">用例内容</label>
          <select
            v-model.number="debugCaseId"
            class="input text-sm"
            :disabled="debugLoadingCases || !debugCases.length"
            @change="applyDebugCase"
          >
            <option v-if="debugLoadingCases" :value="null">加载用例中…</option>
            <option v-else-if="!debugCases.length" :value="null">暂无用例</option>
            <option v-for="item in debugCases" :key="item.id" :value="item.id">
              {{ item.code }} · {{ item.name }}{{ debugCaseEnvSuffix(item) }}
            </option>
          </select>
          <p class="mt-1 text-[11px] leading-relaxed text-slate-500">
            所有用例均可用于调试，绑定当前环境的用例优先排在前面。
          </p>
        </div>

        <div>
          <label class="label">调试消息</label>
          <textarea
            v-model="debugQuery"
            class="input text-sm"
            rows="3"
            placeholder="选择用例后自动带入其提问，可直接修改后发送"
          />
          <p class="mt-1 text-[11px] leading-relaxed text-slate-500">
            消息按该环境的请求头/请求体模板渲染，并带入用例级请求变量；配置了前置接口时会真实执行一次。
          </p>
        </div>

        <div v-if="debugError" class="rounded-xl bg-rose-50 px-3 py-2 text-[11px] text-rose-700">
          {{ debugError }}
        </div>

        <div v-if="debugResult" class="space-y-2">
          <div class="flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
            <StatusPill :status="debugResult.ok ? 'PASS' : 'FAIL'" size="sm" />
            <span>{{ debugResult.message }}</span>
            <span v-if="debugResult.status_code">HTTP {{ debugResult.status_code }}</span>
            <span>耗时 {{ formatMs(debugResult.latency_ms) }}</span>
            <span v-if="debugResult.session_id" class="font-mono">
              session {{ debugResult.session_id }}
            </span>
            <span v-if="debugResult.intent" class="rounded-full bg-slate-100 px-2 py-0.5">
              intent {{ debugResult.intent }}
            </span>
            <span class="rounded-full bg-slate-100 px-2 py-0.5 text-slate-500">未落库</span>
          </div>

          <div v-if="debugResult.final_answer" class="rounded-xl border border-slate-200 bg-white p-2">
            <div class="mb-1 text-[11px] font-medium text-slate-500">最终回复</div>
            <p class="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
              {{ debugResult.final_answer }}
            </p>
          </div>

          <div class="grid gap-2 lg:grid-cols-2">
            <div>
              <div class="mb-1 text-[11px] font-medium text-slate-500">请求报文（变量已渲染）</div>
              <pre
                class="max-h-56 overflow-auto rounded-lg bg-slate-900 p-2 font-mono text-[11px] leading-relaxed text-slate-100"
              >{{ debugRequestText }}</pre>
            </div>
            <div>
              <div class="mb-1 text-[11px] font-medium text-slate-500">响应报文</div>
              <pre
                class="max-h-56 overflow-auto rounded-lg bg-slate-900 p-2 font-mono text-[11px] leading-relaxed text-slate-100"
              >{{ debugResponseText }}</pre>
            </div>
          </div>

          <div class="grid gap-2 lg:grid-cols-2">
            <div>
              <div class="mb-1 text-[11px] font-medium text-slate-500">生成 SQL</div>
              <pre
                class="max-h-40 overflow-auto rounded-lg border border-slate-200 bg-white p-2 font-mono text-[11px] leading-relaxed text-slate-700"
              >{{ debugResult.generated_sql || '—' }}</pre>
            </div>
            <div>
              <div class="mb-1 text-[11px] font-medium text-slate-500">底层取数 / 请求参数</div>
              <pre
                class="max-h-40 overflow-auto rounded-lg border border-slate-200 bg-white p-2 font-mono text-[11px] leading-relaxed text-slate-700"
              >{{ debugRawDataText }}</pre>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <button class="btn-ghost" @click="debugOpen = false">关闭</button>
        <button
          class="btn-primary"
          :disabled="debugSending || debugLoadingCases || !debugCaseId"
          @click="sendDebugMessage"
        >
          {{ debugSending ? '发送中…' : '发送调试消息' }}
        </button>
      </template>
    </BaseModal>

    <!-- ---------------------------------------------------------- 模型表单 -->
    <BaseModal v-model="modelModalOpen" :title="modelTitle" width="xl" subtitle="OpenAI 兼容直连或 OpenCode Serve + SSE 双接入，模拟器用于生成用户提问，裁判用于结构化评分">
      <div class="space-y-4">
        <div>
          <label class="label">接入方式</label>
          <div class="grid gap-2 sm:grid-cols-2">
            <button
              type="button"
              class="rounded-xl border px-3 py-2.5 text-left transition"
              :class="
                modelForm.connection_type === 'OPENAI_COMPATIBLE'
                  ? 'border-indigo-400 bg-indigo-50/70 ring-1 ring-indigo-200'
                  : 'border-line-soft bg-white hover:border-slate-300'
              "
              @click="selectConnectionType('OPENAI_COMPATIBLE')"
            >
              <span class="block text-xs font-medium text-slate-900">OpenAI 兼容直连</span>
              <span class="mt-0.5 block text-[11px] text-slate-500">
                base_url + api_key，直接调用 chat/completions
              </span>
            </button>
            <button
              type="button"
              class="rounded-xl border px-3 py-2.5 text-left transition"
              :class="
                modelForm.connection_type === 'OPENCODE_SERVE'
                  ? 'border-sky-400 bg-sky-50/70 ring-1 ring-sky-200'
                  : 'border-line-soft bg-white hover:border-slate-300'
              "
              @click="selectConnectionType('OPENCODE_SERVE')"
            >
              <span class="block text-xs font-medium text-slate-900">OpenCode Serve + SSE</span>
              <span class="mt-0.5 block text-[11px] text-slate-500">
                serve 地址 + 端口，订阅 /api/event 事件流
              </span>
            </button>
          </div>
        </div>
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
              <button><selectedcontent></selectedcontent></button>
              <option value="SIMULATOR">模拟器模型（SIMULATOR）</option>
              <option value="JUDGE">裁判模型（JUDGE）</option>
              <option value="CASE_AUTHOR">用例编写模型（CASE_AUTHOR）</option>
            </select>
          </div>
        </div>
        <template v-if="!isOpencodeModel">
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
              <label class="label flex items-center justify-between gap-2">
                <span>API Key{{ editingModel ? (modelForm.api_key ? '（已回显原值）' : '（留空不修改）') : '' }}</span>
                <span class="flex items-center gap-1 text-[11px] font-normal text-slate-500">
                  <input
                    v-model="showSecrets"
                    type="checkbox"
                    class="h-3.5 w-3.5 rounded border-slate-300 bg-white"
                  />
                  显示明文
                </span>
              </label>
              <input
                v-model="modelForm.api_key"
                class="input font-mono"
                :type="showSecrets ? 'text' : 'password'"
                placeholder="sk-..."
              />
            </div>
          </div>
        </template>

        <template v-else>
          <div class="rounded-xl border border-line-soft bg-canvas/70 p-3">
            <div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
              <div>
                <label class="label">Serve 地址</label>
                <input
                  v-model="modelForm.serve_url"
                  class="input font-mono"
                  placeholder="http://127.0.0.1"
                  @input="invalidateCatalog()"
                />
              </div>
              <div>
                <label class="label">端口</label>
                <input
                  v-model.number="modelForm.serve_port"
                  class="input font-mono"
                  type="number"
                  min="1"
                  max="65535"
                  @input="invalidateCatalog()"
                />
              </div>
            </div>
            <div class="mt-3 flex flex-wrap items-center gap-2">
              <button type="button" class="btn-ghost" :disabled="catalogLoading" @click="loadCatalog()">
                <svg
                  v-if="catalogLoading"
                  class="h-4 w-4 animate-spin"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <path stroke-linecap="round" d="M12 3a9 9 0 1 0 9 9" />
                </svg>
                <svg
                  v-else
                  class="h-4 w-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v6h6M20 20v-6h-6" />
                  <path stroke-linecap="round" d="M20 9A8 8 0 0 0 6 5.5M4 15a8 8 0 0 0 14 3.5" />
                </svg>
                {{ catalogLoading ? '拉取中…' : catalogLoaded ? '重新拉取' : '拉取可用模型' }}
              </button>
              <span v-if="catalogError" class="text-[11px] text-rose-600">{{ catalogError }}</span>
              <span v-else-if="catalogLoaded" class="text-[11px] text-emerald-600">
                已获取 {{ catalogModels.length }} 个模型 · {{ catalogAgents.length }} 个 Agent
              </span>
              <span v-else class="text-[11px] text-slate-500">
                从 Serve 的 /api/model 读取模型清单，避免手填 provider/model 出错
              </span>
            </div>
            <div class="mt-3 grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">模型</label>
                <select v-model="modelForm.opencode_model" class="input font-mono">
                  <button><selectedcontent></selectedcontent></button>
                  <option value="">跟随 Serve 默认模型</option>
                  <optgroup
                    v-for="group in catalogModelGroups"
                    :key="group.provider"
                    :label="group.provider"
                  >
                    <option
                      v-for="model in group.models"
                      :key="model.value"
                      :value="model.value"
                      :disabled="!model.enabled"
                    >
                      {{ model.name }} · {{ model.model_id }}{{ model.enabled ? '' : '（未启用）' }}
                    </option>
                  </optgroup>
                  <option
                    v-if="modelForm.opencode_model && !currentCatalogModel"
                    :value="modelForm.opencode_model"
                  >
                    {{ modelForm.opencode_model }}（当前配置）
                  </option>
                </select>
                <p class="mt-1 text-[11px] leading-relaxed text-slate-500">
                  <template v-if="currentCatalogModel">
                    {{ currentCatalogModel.provider_id }} ·
                    <template v-if="currentCatalogModel.context_limit">
                      上下文 {{ formatContextLimit(currentCatalogModel.context_limit) }} tokens ·
                    </template>
                    {{ currentCatalogModel.status || 'active' }}
                  </template>
                  <template v-else-if="catalogLoaded">未指定时由 Serve 使用默认模型</template>
                  <template v-else>点击「拉取可用模型」后从列表中选择</template>
                </p>
              </div>
              <div>
                <label class="label">Agent</label>
                <select v-model="modelForm.opencode_agent" class="input font-mono">
                  <button><selectedcontent></selectedcontent></button>
                  <option value="">跟随 Serve 默认 Agent</option>
                  <option v-for="agent in catalogAgentOptions" :key="agent.id" :value="agent.id">
                    {{ agent.name }}（{{ agent.id }}）
                  </option>
                </select>
                <p class="mt-1 text-[11px] leading-relaxed text-slate-500">
                  可选；留空时由 Serve 使用默认 agent。
                </p>
              </div>
            </div>
            <div class="mt-3 grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">工作目录（可选）</label>
                <input
                  v-model="modelForm.opencode_directory"
                  class="input font-mono"
                  placeholder="D:/workspace/qa"
                  @input="invalidateCatalog()"
                />
              </div>
              <div>
                <label class="label">显示标识（可选）</label>
                <input
                  v-model="modelForm.model_name"
                  class="input font-mono"
                  placeholder="留空自动生成"
                />
              </div>
            </div>
            <div class="mt-3 grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">Basic Auth 用户名</label>
                <input
                  v-model="modelForm.serve_username"
                  class="input font-mono"
                  placeholder="opencode"
                  @input="invalidateCatalog()"
                />
              </div>
              <div>
                <label class="label">Basic Auth 密码{{ editingModel ? (modelForm.serve_password ? '（已回显原值）' : '（留空不修改）') : '' }}</label>
                <input
                  v-model="modelForm.serve_password"
                  class="input font-mono"
                  :type="showSecrets ? 'text' : 'password'"
                  placeholder="可选"
                  @input="invalidateCatalog()"
                />
              </div>
            </div>
            <p class="mt-3 text-[11px] leading-relaxed text-slate-500">
              运行时先创建 session，再订阅 GET /api/event SSE，发送 prompt 后按 session.text.delta /
              session.text.ended 汇总文本，并从 session.usage.* 读取 token 用量。
            </p>
          </div>
        </template>

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
          <label class="mt-6 flex items-center gap-2 text-sm text-slate-700">
            <input
              v-model="modelForm.response_format_json"
              type="checkbox"
              class="h-4 w-4 rounded border-slate-300 bg-white"
            />
            强制 JSON 输出
          </label>
          <label class="mt-6 flex items-center gap-2 text-sm text-slate-700">
            <input v-model="modelForm.is_active" type="checkbox" class="h-4 w-4 rounded border-slate-300 bg-white" />
            启用模型
          </label>
        </div>

        <div v-if="!isOpencodeModel">
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
