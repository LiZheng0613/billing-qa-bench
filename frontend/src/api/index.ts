import { downloadFile, http } from './client'
import type {
  AuditLog,
  BatchTask,
  Caliber,
  CandidatePromoteResult,
  CandidateSource,
  CandidateStatus,
  Capability,
  CapabilityExecuteResult,
  CaseCandidate,
  CaseExecutionItem,
  CaseFreezePayload,
  CaseAIDraftRequest,
  CaseAIDraftResponse,
  CasePayload,
  CaseReviewPayload,
  CaseRetryResult,
  CaseRevision,
  CaseTrend,
  DataSource,
  DataSourceKind,
  DatasetStatItem,
  Environment,
  EnvironmentPayload,
  EnvironmentUsage,
  EnvDebugChatPayload,
  EnvDebugChatResult,
  EnvPreviewResult,
  EnvVariable,
  EnvVariableInput,
  ExecutionDetail,
  ExecutionListItem,
  ExecutionPassRate,
  FactAtomView,
  FactStatItem,
  FactStatsOverview,
  FactVerifyPayload,
  GoldQuery,
  GraderEval,
  HealthCheckResult,
  JudgeStrategy,
  LoginResponse,
  MessageResponse,
  ModelConfig,
  ModelPayload,
  ModelTestResult,
  NotificationItem,
  OpenCodeCatalog,
  OpenCodeCatalogPayload,
  Page,
  Permission,
  ProbeResult,
  PromptListItem,
  PromptTemplate,
  PromptUsage,
  PromptVersion,
  RawExecution,
  RevisionDiff,
  Role,
  RoleDetail,
  RunCompare,
  SandboxRequest,
  SandboxResponse,
  SystemUser,
  SystemUserDetail,
  TaskMetrics,
  TaskRetryCandidates,
  TaskRunsPage,
  TaskScheduleType,
  TemplateStrategy,
  TemplateType,
  TestCase,
  UserInfo,
} from '../types'

type EnvUpdate = Partial<Omit<EnvironmentPayload, 'code' | 'variables'>>
type ModelUpdate = Partial<Omit<ModelPayload, 'code'>>

export type CaseImportAction = 'created' | 'updated' | 'skipped' | 'failed'
export type CaseImportConflict = 'upsert' | 'skip' | 'error'

export interface CaseImportItem {
  row: number
  code: string | null
  action: CaseImportAction
  message: string | null
}

export interface CaseImportResult {
  total: number
  created: number
  updated: number
  skipped: number
  failed: number
  dry_run: boolean
  on_conflict: CaseImportConflict
  errors: Array<{ row: number; message: string }>
  items: CaseImportItem[]
  warnings: string[]
  case_ids: number[]
}

export interface CaseExportQuery {
  ids?: number[]
  case_type?: string
  status?: string
  tag?: string
  environment_id?: number
  grading_mode?: string
  review_status?: string
  keyword?: string
}

export const api = {
  // ---------------------------------------------------------------- 认证
  login: (username: string, password: string) =>
    http.post<LoginResponse>('/auth/login', { username, password }),
  me: () => http.get<UserInfo>('/auth/me'),
  changePassword: (old_password: string, new_password: string) =>
    http.post<LoginResponse>('/auth/change-password', { old_password, new_password }),
  logout: () => http.post<MessageResponse>('/auth/logout'),

  // ---------------------------------------------------------------- 系统管理
  users: {
    list: (query?: {
      keyword?: string
      status?: string
      role_id?: number
      page?: number
      page_size?: number
    }) => http.get<Page<SystemUser>>('/users', query),
    get: (id: number) => http.get<SystemUserDetail>(`/users/${id}`),
    create: (payload: {
      username: string
      display_name: string
      email?: string | null
      phone?: string | null
      password: string
      role_ids: number[]
    }) => http.post<SystemUserDetail>('/users', payload),
    update: (
      id: number,
      payload: {
        display_name?: string
        email?: string | null
        phone?: string | null
        status?: string
      },
    ) => http.patch<SystemUserDetail>(`/users/${id}`, payload),
    setRoles: (id: number, role_ids: number[]) =>
      http.put<SystemUserDetail>(`/users/${id}/roles`, { role_ids }),
    resetPassword: (id: number) =>
      http.post<{ temp_password: string; message: string }>(
        `/users/${id}/reset-password`,
      ),
    unlock: (id: number) => http.post<SystemUserDetail>(`/users/${id}/unlock`),
  },
  roles: {
    list: () => http.get<Role[]>('/roles'),
    get: (id: number) => http.get<RoleDetail>(`/roles/${id}`),
    create: (payload: {
      code: string
      name: string
      description?: string | null
      data_scope?: string
      permission_codes: string[]
    }) => http.post<RoleDetail>('/roles', payload),
    update: (
      id: number,
      payload: { name?: string; description?: string | null; data_scope?: string; status?: string },
    ) => http.patch<RoleDetail>(`/roles/${id}`, payload),
    setPermissions: (id: number, permission_codes: string[]) =>
      http.put<RoleDetail>(`/roles/${id}/permissions`, { permission_codes }),
    remove: (id: number) => http.delete<MessageResponse>(`/roles/${id}`),
    permissions: () => http.get<Permission[]>('/roles/permissions/catalog'),
  },
  auditLogs: {
    list: (query?: {
      page?: number
      page_size?: number
      actor_id?: number
      action?: string
      resource?: string
      success?: boolean
      date_from?: string
      date_to?: string
    }) => http.get<Page<AuditLog>>('/audit-logs', query),
  },
  notifications: {
    list: (limit = 5) => http.get<NotificationItem[]>('/notifications', { limit }),
  },

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
    preview: (id: number, caseId: number) =>
      http.post<EnvPreviewResult>(`/environments/${id}/preview`, { case_id: caseId }),
    debugChat: (id: number, payload: EnvDebugChatPayload) =>
      http.post<EnvDebugChatResult>(`/environments/${id}/debug-chat`, payload),
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
      template_type?: TemplateType
      keyword?: string
      is_active?: boolean
      page?: number
      page_size?: number
    }) => http.get<Page<PromptListItem>>('/prompts', query),
    get: (id: number) => http.get<PromptTemplate>(`/prompts/${id}`),
    create: (payload: {
      code: string
      name: string
      template_type: TemplateType
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
      grading_mode?: string
      review_status?: string
      keyword?: string
      page?: number
      page_size?: number
    }) => http.get<Page<TestCase>>('/cases', query),
    get: (id: number) => http.get<TestCase>(`/cases/${id}`),
    create: (payload: CasePayload) => http.post<TestCase>('/cases', payload),
    aiDraft: (payload: CaseAIDraftRequest) =>
      http.post<CaseAIDraftResponse>('/cases/ai-draft', payload),
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
    debug: (
      id: number,
      payload?: {
        environment_id?: number | null
        enable_self_check?: boolean
        /** 未保存的草稿快照，浅合并到当前版本快照后执行 */
        case_snapshot_override?: Record<string, unknown> | null
        /** 草稿试跑：执行记录标记为 SANDBOX，不进入趋势统计 */
        draft?: boolean
      },
    ) => http.post<ExecutionDetail>(`/cases/${id}/debug`, payload ?? {}),
    facts: (id: number) => http.get<FactAtomView[]>(`/cases/${id}/facts`),
    verifyFact: (id: number, factId: string, payload: FactVerifyPayload) =>
      http.post<TestCase>(`/cases/${id}/facts/${encodeURIComponent(factId)}/verify`, payload),
    review: (id: number, payload: CaseReviewPayload) =>
      http.post<TestCase>(`/cases/${id}/review`, payload),
    freeze: (id: number, payload: CaseFreezePayload) =>
      http.post<TestCase>(`/cases/${id}/freeze`, payload),
    export: (format: 'json' | 'xlsx', query: CaseExportQuery = {}) =>
      downloadFile('/cases/export', `test_cases.${format}`, {
        format,
        ids: query.ids?.length ? query.ids.join(',') : undefined,
        case_type: query.case_type,
        status: query.status,
        tag: query.tag,
        environment_id: query.environment_id,
        grading_mode: query.grading_mode,
        review_status: query.review_status,
        keyword: query.keyword,
      }),
    importFile: (file: File, options: { dryRun?: boolean; onConflict?: CaseImportConflict } = {}) => {
      const formData = new FormData()
      formData.append('file', file)
      return http.upload<CaseImportResult>('/cases/import', formData, {
        dry_run: options.dryRun ?? false,
        on_conflict: options.onConflict ?? 'upsert',
      })
    },
  },

  // ---------------------------------------------------------------- 批量任务
  tasks: {
    list: (query?: {
      status?: string
      keyword?: string
      schedule_type?: TaskScheduleType
      include_runs?: boolean
      page?: number
      page_size?: number
    }) => http.get<Page<BatchTask>>('/tasks', query),
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
      retry_on_error?: boolean
      max_error_retries?: number
      retry_backoff_seconds?: number
      enable_self_check?: boolean
      schedule_type?: TaskScheduleType
      scheduled_at?: string | null
      schedule_time?: string | null
      schedule_weekdays?: number[]
      repeat_rounds?: number
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
    abort: (id: number) => http.post<MessageResponse>(`/tasks/${id}/abort`),
    stopPlan: (id: number) => http.post<BatchTask>(`/tasks/${id}/stop-plan`),
    retryFailed: (id: number) => http.post<MessageResponse>(`/tasks/${id}/retry-failed`),
    resume: (id: number) => http.post<MessageResponse>(`/tasks/${id}/resume`),
    retryCase: (taskId: number, caseId: number) =>
      http.post<MessageResponse>(`/tasks/${taskId}/cases/${caseId}/retry`),
    retryCases: (taskId: number, caseIds: number[]) =>
      http.post<CaseRetryResult>(`/tasks/${taskId}/cases/retry`, { case_ids: caseIds }),
    retryCandidates: (taskId: number) =>
      http.get<TaskRetryCandidates>(`/tasks/${taskId}/retry-candidates`),
    metrics: (id: number) => http.get<TaskMetrics>(`/tasks/${id}/metrics`),
    runs: (id: number, query?: { page?: number; page_size?: number }) =>
      http.get<TaskRunsPage>(`/tasks/${id}/runs`, query),
    executions: (
      id: number,
      query?: {
        status?: string
        keyword?: string
        min_score?: number
        max_score?: number
        sort?: 'score_desc' | 'score_asc'
        latest_per_case?: boolean
        page?: number
        page_size?: number
      },
    ) => http.get<Page<CaseExecutionItem>>(`/tasks/${id}/executions`, query),
    exportResults: (id: number, latestPerCase: boolean, filename: string, format: 'xlsx' | 'jsonl' = 'xlsx') =>
      downloadFile(`/tasks/${id}/export`, filename, {
        latest_per_case: latestPerCase,
        format,
      }),
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
    passRate: (hours = 24) =>
      http.get<ExecutionPassRate>('/executions/pass-rate', { hours }),
    get: (id: number) => http.get<ExecutionDetail>(`/executions/${id}`),
    raw: (id: number) => http.get<RawExecution>(`/executions/${id}/raw`),
  },

  // ---------------------------------------------------------------- 运营：候选池
  candidates: {
    list: (query?: {
      status?: CandidateStatus | string
      source?: CandidateSource | string
      keyword?: string
      page?: number
      page_size?: number
    }) => http.get<Page<CaseCandidate>>('/candidates', query),
    get: (id: number) => http.get<CaseCandidate>(`/candidates/${id}`),
    create: (payload: {
      source?: CandidateSource
      raw_question: string
      context?: Record<string, unknown> | null
      trace_uid?: string | null
      environment_id?: number | null
      draft?: Record<string, unknown> | null
      note?: string | null
    }) => http.post<CaseCandidate>('/candidates', payload),
    update: (
      id: number,
      payload: {
        status?: CandidateStatus
        note?: string | null
        draft?: Record<string, unknown> | null
      },
    ) => http.patch<CaseCandidate>(`/candidates/${id}`, payload),
    reject: (id: number, note?: string) =>
      http.post<CaseCandidate>(`/candidates/${id}/reject`, undefined, { note }),
    promote: (
      id: number,
      payload?: { code?: string | null; name?: string | null; case?: Record<string, unknown> | null },
    ) => http.post<CandidatePromoteResult>(`/candidates/${id}/promote`, payload ?? {}),
  },

  // ---------------------------------------------------------------- 运营：判分器自检
  grader: {
    mutants: () => http.get<{ mutants: string[] }>('/grader/mutants'),
    runEvals: (payload?: {
      snapshot?: Record<string, unknown> | null
      correct_answer?: string | null
      dataset_version?: string | null
    }) => http.post<GraderEval>('/grader/evals/run', payload ?? {}),
    evals: (query?: { grader_version?: string; page?: number; page_size?: number }) =>
      http.get<Page<GraderEval>>('/grader/evals', query),
  },

  // ---------------------------------------------------------------- 运营：业务口径
  calibers: {
    list: (query?: { keyword?: string; page?: number; page_size?: number }) =>
      http.get<Page<Caliber>>('/calibers', query),
    get: (id: number) => http.get<Caliber>(`/calibers/${id}`),
    create: (payload: {
      key: string
      name: string
      definition?: string | null
      canonical_source?: string
      owner?: string | null
    }) => http.post<Caliber>('/calibers', payload),
    update: (
      id: number,
      payload: {
        name?: string
        definition?: string | null
        canonical_source?: string
        owner?: string | null
      },
    ) => http.put<Caliber>(`/calibers/${id}`, payload),
    remove: (id: number) => http.delete<MessageResponse>(`/calibers/${id}`),
  },

  // ---------------------------------------------------------------- 运营：数据源
  dataSources: {
    list: (query?: {
      kind?: DataSourceKind | string
      is_active?: boolean
      page?: number
      page_size?: number
    }) => http.get<Page<DataSource>>('/data-sources', query),
    get: (id: number) => http.get<DataSource>(`/data-sources/${id}`),
    create: (payload: {
      code: string
      name: string
      kind?: DataSourceKind
      endpoint_url?: string | null
      description?: string | null
      auth_config?: Record<string, unknown> | null
      timeout_seconds?: number
      max_rows?: number
      tool_version?: string | null
      is_active?: boolean
    }) => http.post<DataSource>('/data-sources', payload),
    update: (
      id: number,
      payload: Partial<{
        name: string
        kind: DataSourceKind
        endpoint_url: string | null
        description: string | null
        auth_config: Record<string, unknown> | null
        timeout_seconds: number
        max_rows: number
        tool_version: string | null
        is_active: boolean
      }>,
    ) => http.put<DataSource>(`/data-sources/${id}`, payload),
    remove: (id: number) => http.delete<MessageResponse>(`/data-sources/${id}`),
    probe: (id: number) => http.post<ProbeResult>(`/data-sources/${id}/probe`),
  },

  // ---------------------------------------------------------------- 运营：取数能力
  capabilities: {
    list: (query?: {
      status?: string
      is_active?: boolean
      keyword?: string
      page?: number
      page_size?: number
    }) => http.get<Page<Capability>>('/capabilities', query),
    get: (id: number) => http.get<Capability>(`/capabilities/${id}`),
    create: (payload: {
      key: string
      name: string
      kind?: 'CYPHER' | 'SQL' | 'API'
      description?: string | null
      source_id?: number | null
      query_text?: string | null
      params_schema?: Array<Record<string, unknown>> | null
      param_binding?: Record<string, string> | null
      intent_patterns?: string[] | null
      lookup_queries?: Record<string, string> | null
    }) => http.post<Capability>('/capabilities', payload),
    update: (
      id: number,
      payload: Partial<{
        name: string
        kind: 'CYPHER' | 'SQL' | 'API'
        description: string | null
        source_id: number | null
        query_text: string | null
        params_schema: Array<Record<string, unknown>> | null
        param_binding: Record<string, string> | null
        intent_patterns: string[] | null
        lookup_queries: Record<string, string> | null
        status: 'DRAFT' | 'CERTIFIED' | 'DEPRECATED'
        is_active: boolean
      }>,
    ) => http.put<Capability>(`/capabilities/${id}`, payload),
    remove: (id: number) => http.delete<MessageResponse>(`/capabilities/${id}`),
    evidence: (
      id: number,
      payload: {
        case_id: number
        params?: Record<string, unknown>
        context?: Record<string, unknown>
        fact_ids?: string[]
        session_id?: string | null
        max_rows?: number | null
        timeout_seconds?: number | null
      },
    ) => http.post<CapabilityExecuteResult>(`/capabilities/${id}/evidence`, payload),
    goldQueries: (query?: {
      case_id?: number
      capability_id?: number
      page?: number
      page_size?: number
    }) => http.get<Page<GoldQuery>>('/gold-queries', query),
  },

  // ---------------------------------------------------------------- 运营：统计
  stats: {
    overview: () => http.get<FactStatsOverview>('/stats/overview'),
    facts: (query?: { case_id?: number; since?: string; until?: string }) =>
      http.get<FactStatItem[]>('/stats/facts', query),
    datasets: () => http.get<DatasetStatItem[]>('/stats/datasets'),
  },
}

export type {
  JudgeStrategy,
  TemplateStrategy,
}
