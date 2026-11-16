import { downloadFile, http } from './client'
import type {
  BatchTask,
  CaseExecutionItem,
  CasePayload,
  CaseRevision,
  CaseTrend,
  Environment,
  EnvironmentPayload,
  EnvironmentUsage,
  EnvVariable,
  EnvVariableInput,
  ExecutionDetail,
  ExecutionListItem,
  HealthCheckResult,
  JudgeStrategy,
  LoginResponse,
  MessageResponse,
  ModelConfig,
  ModelPayload,
  ModelTestResult,
  OpenCodeCatalog,
  OpenCodeCatalogPayload,
  Page,
  PromptListItem,
  PromptTemplate,
  PromptUsage,
  PromptVersion,
  RawExecution,
  RevisionDiff,
  RunCompare,
  SandboxRequest,
  SandboxResponse,
  TaskMetrics,
  TaskScheduleType,
  TemplateStrategy,
  TestCase,
  UserInfo,
} from '../types'

type EnvUpdate = Partial<Omit<EnvironmentPayload, 'code' | 'variables'>>
type ModelUpdate = Partial<Omit<ModelPayload, 'code'>>

export const api = {
  // ---------------------------------------------------------------- 认证
  login: (username: string, password: string) =>
    http.post<LoginResponse>('/auth/login', { username, password }),
  me: () => http.get<UserInfo>('/auth/me'),

  // ---------------------------------------------------------------- 环境
  environments: {
    list: (query?: { keyword?: string; is_active?: boolean; page?: number; page_size?: number }) =>
      http.get<Page<Environment>>('/environments', query),
    get: (id: number) => http.get<Environment>(`/environments/${id}`),
    create: (payload: EnvironmentPayload) => http.post<Environment>('/environments', payload),
    update: (id: number, payload: EnvUpdate) =>
      http.put<Environment>(`/environments/${id}`, payload),
    remove: (id: number) => http.delete<MessageResponse>(`/environments/${id}`),
    variables: (id: number) => http.get<EnvVariable[]>(`/environments/${id}/variables`),
    replaceVariables: (id: number, variables: EnvVariableInput[]) =>
      http.put<EnvVariable[]>(`/environments/${id}/variables`, { variables }),
    healthCheck: (id: number) =>
      http.post<HealthCheckResult>(`/environments/${id}/health-check`),
    usage: (id: number) => http.get<EnvironmentUsage>(`/environments/${id}/usage`),
  },

  // ---------------------------------------------------------------- 模型
  models: {
    list: (query?: {
      role?: 'SIMULATOR' | 'JUDGE'
      is_active?: boolean
      keyword?: string
      page?: number
      page_size?: number
    }) => http.get<Page<ModelConfig>>('/models', query),
    get: (id: number) => http.get<ModelConfig>(`/models/${id}`),
    create: (payload: ModelPayload) => http.post<ModelConfig>('/models', payload),
    update: (id: number, payload: ModelUpdate) =>
      http.put<ModelConfig>(`/models/${id}`, payload),
    remove: (id: number) => http.delete<MessageResponse>(`/models/${id}`),
    test: (id: number) => http.post<ModelTestResult>(`/models/${id}/test`),
    opencodeCatalog: (payload: OpenCodeCatalogPayload) =>
      http.post<OpenCodeCatalog>('/models/opencode/catalog', payload),
  },

  // ---------------------------------------------------------------- Prompt 资产
  prompts: {
    list: (query?: {
      template_type?: 'USER_SIMULATOR' | 'LLM_JUDGE'
      keyword?: string
      is_active?: boolean
      page?: number
      page_size?: number
    }) => http.get<Page<PromptListItem>>('/prompts', query),
    get: (id: number) => http.get<PromptTemplate>(`/prompts/${id}`),
    create: (payload: {
      code: string
      name: string
      template_type: 'USER_SIMULATOR' | 'LLM_JUDGE'
      description?: string | null
      content: string
      changelog?: string | null
    }) => http.post<PromptTemplate>('/prompts', payload),
    update: (id: number, payload: { name?: string; description?: string | null; is_active?: boolean }) =>
      http.put<PromptTemplate>(`/prompts/${id}`, payload),
    remove: (id: number) => http.delete<MessageResponse>(`/prompts/${id}`),
    createVersion: (id: number, content: string, changelog?: string | null) =>
      http.post<PromptTemplate>(`/prompts/${id}/versions`, { content, changelog }),
    getVersion: (id: number, version: number) =>
      http.get<PromptVersion>(`/prompts/${id}/versions/${version}`),
    usage: (id: number) => http.get<PromptUsage>(`/prompts/${id}/usage`),
    sandbox: (id: number, payload: SandboxRequest) =>
      http.post<SandboxResponse>(`/prompts/${id}/sandbox`, payload),
  },

  // ---------------------------------------------------------------- 用例
  cases: {
    list: (query?: {
      case_type?: string
      status?: string
      tag?: string
      environment_id?: number
      keyword?: string
      page?: number
      page_size?: number
    }) => http.get<Page<TestCase>>('/cases', query),
    get: (id: number) => http.get<TestCase>(`/cases/${id}`),
    create: (payload: CasePayload) => http.post<TestCase>('/cases', payload),
    update: (id: number, payload: Partial<CasePayload>) =>
      http.put<TestCase>(`/cases/${id}`, payload),
    remove: (id: number) => http.delete<MessageResponse>(`/cases/${id}`),
    revisions: (id: number, includeSnapshot = false) =>
      http.get<CaseRevision[]>(`/cases/${id}/revisions`, { include_snapshot: includeSnapshot }),
    revisionDiff: (id: number, fromVersion: number, toVersion: number) =>
      http.get<RevisionDiff>(`/cases/${id}/revisions/diff`, {
        from_version: fromVersion,
        to_version: toVersion,
      }),
    rollback: (id: number, version: number, summary?: string) =>
      http.post<TestCase>(`/cases/${id}/rollback`, { version, summary }),
    executions: (
      id: number,
      query?: { status?: string; page?: number; page_size?: number },
    ) => http.get<Page<CaseExecutionItem>>(`/cases/${id}/executions`, query),
    trend: (id: number) => http.get<CaseTrend>(`/cases/${id}/trend`),
    compareRuns: (runA: number, runB: number) =>
      http.get<RunCompare>('/cases/compare/runs', { run_a: runA, run_b: runB }),
    debug: (id: number, payload?: { environment_id?: number | null }) =>
      http.post<ExecutionDetail>(`/cases/${id}/debug`, payload ?? {}),
    export: (format: 'json' | 'xlsx', ids?: number[]) =>
      downloadFile('/cases/export', `test_cases.${format}`, {
        format,
        ids: ids?.length ? ids.join(',') : undefined,
      }),
    importFile: (file: File) => {
      const formData = new FormData()
      formData.append('file', file)
      return http.upload<{
        total: number
        created: number
        updated: number
        failed: number
        errors: Array<{ row: number; message: string }>
        case_ids: number[]
      }>('/cases/import', formData)
    },
  },

  // ---------------------------------------------------------------- 批量任务
  tasks: {
    list: (query?: { status?: string; keyword?: string; page?: number; page_size?: number }) =>
      http.get<Page<BatchTask>>('/tasks', query),
    get: (id: number) => http.get<BatchTask>(`/tasks/${id}`),
    create: (payload: {
      name: string
      description?: string | null
      environment_id?: number | null
      case_scope?: 'SELECTED' | 'ALL_ACTIVE'
      case_ids?: number[]
      concurrency?: number
      qps_limit?: number
      retry_on_env_error?: boolean
      max_env_retries?: number
      retry_backoff_seconds?: number
      schedule_type?: TaskScheduleType
      scheduled_at?: string | null
      schedule_time?: string | null
      schedule_weekdays?: number[]
      auto_start?: boolean
    }) => http.post<BatchTask>('/tasks', payload),
    start: (id: number) => http.post<MessageResponse>(`/tasks/${id}/start`),
    runNow: (id: number) => http.post<BatchTask>(`/tasks/${id}/run-now`),
    updateSchedule: (
      id: number,
      payload: {
        schedule_type?: TaskScheduleType
        scheduled_at?: string | null
        schedule_time?: string | null
        schedule_weekdays?: number[]
        active?: boolean
      },
    ) => http.put<BatchTask>(`/tasks/${id}/schedule`, payload),
    pause: (id: number) => http.post<MessageResponse>(`/tasks/${id}/pause`),
    resume: (id: number) => http.post<MessageResponse>(`/tasks/${id}/resume`),
    abort: (id: number) => http.post<MessageResponse>(`/tasks/${id}/abort`),
    retryFailed: (id: number) => http.post<MessageResponse>(`/tasks/${id}/retry-failed`),
    metrics: (id: number) => http.get<TaskMetrics>(`/tasks/${id}/metrics`),
    executions: (
      id: number,
      query?: { status?: string; latest_per_case?: boolean; page?: number; page_size?: number },
    ) => http.get<Page<CaseExecutionItem>>(`/tasks/${id}/executions`, query),
  },

  // ---------------------------------------------------------------- 执行追踪
  executions: {
    list: (query?: {
      case_id?: number
      task_id?: number
      status?: string
      source?: string
      page?: number
      page_size?: number
    }) => http.get<Page<ExecutionListItem>>('/executions', query),
    get: (id: number) => http.get<ExecutionDetail>(`/executions/${id}`),
    raw: (id: number) => http.get<RawExecution>(`/executions/${id}/raw`),
  },
}

export type {
  JudgeStrategy,
  TemplateStrategy,
}
