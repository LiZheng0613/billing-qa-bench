/**
 * 后端 Schema 的 TypeScript 映射。
 * 与 billing_qa/schemas 保持一致，字段尽量不做改名，便于排查问题。
 */

export type CaseType = 'SINGLE' | 'STATIC_MULTI' | 'DYNAMIC_MULTI'
export type CaseStatus = 'ACTIVE' | 'DISABLED' | 'DRAFT'
export type TemplateType = 'USER_SIMULATOR' | 'LLM_JUDGE'
export type TemplateStrategy = 'LATEST' | 'PINNED'
export type ModelRole = 'SIMULATOR' | 'JUDGE'
export type JudgeStrategy = 'AVG' | 'VETO'
export type TaskStatus = 'PENDING' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'ABORTED' | 'FAILED'

export type ExecutionStatus =
  | 'PASS'
  | 'FAIL'
  | 'ERROR'
  | 'MAX_TURNS_EXCEEDED'
  | 'REPETITION_ABORT'
  | 'ABORTED'

export type TerminationStatus =
  | 'GOAL_ACHIEVED'
  | 'GIVE_UP'
  | 'REPETITION_ABORT'
  | 'MAX_TURNS_EXCEEDED'
  | 'COMPLETED'
  | 'ERROR'

export type RevisionChangeType = 'CREATE' | 'UPDATE' | 'IMPORT' | 'ROLLBACK'
export type ExecutionSource = 'BATCH' | 'RETRY' | 'DEBUG' | 'SANDBOX'

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface MessageResponse {
  message: string
  detail?: string | null
}

export interface ErrorBody {
  code: string
  message: string
  path?: string
  detail?: unknown
}

// ------------------------------------------------------------------ 认证
export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  username: string
}

export interface UserInfo {
  username: string
  role: string
}

// ------------------------------------------------------------------ 环境
export interface EnvVariable {
  id: number
  key: string
  value_masked: string
  is_secret: boolean
  description: string | null
  updated_at: string | null
}

export interface EnvVariableInput {
  key: string
  value: string | null
  is_secret: boolean
  description: string | null
}

export interface PreHookConfig {
  enabled: boolean
  method: string
  path: string
  headers: Record<string, string>
  body: Record<string, unknown> | null
  token_jsonpath: string
  token_var: string
  cache_seconds: number
}

export interface Environment {
  id: number
  code: string
  name: string
  description: string | null
  base_url: string
  health_path: string
  api_method: string
  api_path: string
  headers_template: Record<string, string> | null
  body_template: Record<string, unknown> | null
  is_stream: boolean
  extractors: Record<string, string> | null
  pre_hook: PreHookConfig | null
  timeout_seconds: number
  verify_ssl: boolean
  is_active: boolean
  last_health_status: string | null
  last_health_latency_ms: number | null
  last_health_at: string | null
  last_health_message: string | null
  created_at: string
  updated_at: string
  variables: EnvVariable[]
}

export interface EnvironmentPayload {
  code: string
  name: string
  description?: string | null
  base_url: string
  health_path?: string
  api_method?: string
  api_path?: string
  headers_template?: Record<string, string> | null
  body_template?: Record<string, unknown> | null
  is_stream?: boolean
  extractors?: Record<string, string> | null
  pre_hook?: PreHookConfig | null
  timeout_seconds?: number
  verify_ssl?: boolean
  is_active?: boolean
  variables?: EnvVariableInput[]
}

export interface HealthCheckResult {
  ok: boolean
  status_code: number | null
  latency_ms: number | null
  message: string
  checked_at: string
}

export interface EnvironmentUsage {
  environment_id: number
  case_count: number
  task_count: number
}

// ------------------------------------------------------------------ 模型
export type ModelConnectionType = 'OPENAI_COMPATIBLE' | 'OPENCODE_SERVE'

export interface ModelConfig {
  id: number
  code: string
  name: string
  role: string
  connection_type: ModelConnectionType
  provider: string
  base_url: string
  model_name: string
  serve_url: string | null
  serve_port: number | null
  serve_username: string | null
  serve_password_masked: string
  opencode_agent: string | null
  opencode_model: string | null
  opencode_directory: string | null
  api_key_masked: string
  temperature: number | null
  effective_temperature: number
  top_p: number | null
  max_tokens: number
  timeout_seconds: number
  rpm_limit: number
  max_retries: number
  response_format_json: boolean
  extra_params: Record<string, unknown> | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface ModelPayload {
  code: string
  name: string
  role: ModelRole
  connection_type?: ModelConnectionType
  provider?: string
  base_url?: string | null
  serve_url?: string | null
  serve_port?: number | null
  serve_username?: string | null
  serve_password?: string | null
  opencode_agent?: string | null
  opencode_model?: string | null
  opencode_directory?: string | null
  model_name?: string
  api_key?: string | null
  temperature?: number | null
  top_p?: number | null
  max_tokens?: number
  timeout_seconds?: number
  rpm_limit?: number
  max_retries?: number
  response_format_json?: boolean
  extra_params?: Record<string, unknown> | null
  is_active?: boolean
}

export interface ModelTestResult {
  ok: boolean
  connection_type?: string | null
  latency_ms: number | null
  content: string | null
  prompt_tokens: number
  completion_tokens: number
  message: string
}

// ------------------------------------------------------------------ Prompt 资产
export interface PromptVersion {
  id: number
  version: number
  content: string
  variables: string[] | null
  changelog: string | null
  created_by: string | null
  created_at: string
}

export interface PromptTemplate {
  id: number
  code: string
  name: string
  template_type: string
  description: string | null
  current_version: number
  ref_count: number
  is_builtin: boolean
  is_active: boolean
  created_by?: string | null
  updated_by?: string | null
  created_at: string
  updated_at: string
  versions: PromptVersion[]
}

export interface PromptListItem {
  id: number
  code: string
  name: string
  template_type: string
  description: string | null
  current_version: number
  ref_count: number
  is_builtin: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface PromptUsageCase {
  id: number
  code: string
  name: string
  strategy: string
  pinned_version: number | null
}

export interface PromptUsage {
  template_id: number
  ref_count: number
  cases: PromptUsageCase[]
  can_delete: boolean
  delete_blockers: string[]
}

export interface SandboxRequest {
  mode: 'SIMULATOR' | 'JUDGE'
  execution_id?: number | null
  variables?: Record<string, unknown> | null
  model_id?: number | null
  content_override?: string | null
  temperature?: number | null
}

export interface SandboxResponse {
  rendered_prompt: string
  output: Record<string, unknown> | null
  raw_content: string
  missing_variables: string[]
  latency_ms: number
  prompt_tokens: number
  completion_tokens: number
  model_name: string
}

// ------------------------------------------------------------------ 用例
export interface NumericExpectation {
  label: string
  value: number
  tolerance: number
}

export interface RuleConfig {
  amounts: NumericExpectation[]
  counts: NumericExpectation[]
  dates: string[]
  must_contain: string[]
  must_not_contain: string[]
}

export interface TurnItem {
  index?: number | null
  query: string
  expected_answer?: string | null
  expected_rules?: RuleConfig | null
  notes?: string | null
}

export interface HiddenContextItem {
  key: string
  content: string
  release_hint?: string | null
}

export interface TestCase {
  id: number
  code: string
  name: string
  description: string | null
  case_type: string
  status: string
  priority: string
  tags: string[] | null
  environment_id: number | null
  query: string | null
  expected_answer: string | null
  expected_rules: RuleConfig | null
  turns: TurnItem[] | null
  session_expected: Record<string, unknown> | null
  fail_fast: boolean
  user_goal: string | null
  initial_query: string | null
  persona_description: string | null
  hidden_context: HiddenContextItem[] | null
  max_turns: number
  terminate_conditions: Record<string, unknown> | null
  simulator_template_id: number | null
  simulator_template_strategy: string
  simulator_template_version: number | null
  judge_template_id: number | null
  judge_template_strategy: string
  judge_template_version: number | null
  prompt_overrides: Record<string, string> | null
  simulator_model_id: number | null
  judge_model_ids: number[] | null
  judge_strategy: string
  pass_threshold: number | null
  current_version: number
  created_by: string | null
  updated_by: string | null
  created_at: string
  updated_at: string
  simulator_template_code: string | null
  judge_template_code: string | null
  environment_name: string | null
}

export interface CasePayload {
  code: string
  name: string
  description?: string | null
  case_type: CaseType
  status?: string
  priority?: string
  tags?: string[]
  environment_id?: number | null
  query?: string | null
  expected_answer?: string | null
  expected_rules?: RuleConfig | null
  turns?: TurnItem[] | null
  session_expected?: Record<string, unknown> | null
  fail_fast?: boolean
  user_goal?: string | null
  initial_query?: string | null
  persona_description?: string | null
  hidden_context?: HiddenContextItem[] | null
  max_turns?: number
  terminate_conditions?: Record<string, unknown> | null
  simulator_template_id?: number | null
  simulator_template_strategy?: TemplateStrategy
  simulator_template_version?: number | null
  judge_template_id?: number | null
  judge_template_strategy?: TemplateStrategy
  judge_template_version?: number | null
  prompt_overrides?: Record<string, string> | null
  simulator_model_id?: number | null
  judge_model_ids?: number[]
  judge_strategy?: JudgeStrategy
  pass_threshold?: number | null
  change_summary?: string | null
}

export interface CaseRevision {
  id: number
  case_id: number
  version: number
  change_type: RevisionChangeType
  operator: string
  summary: string | null
  created_at: string
  snapshot: Record<string, unknown> | null
}

export interface DiffChange {
  path: string
  label: string
  old: unknown
  new: unknown
  change_type: 'added' | 'removed' | 'modified'
}

export interface RevisionDiff {
  case_id: number
  from_version: number
  to_version: number
  changes: DiffChange[]
  total: number
}

export interface CaseExecutionItem {
  id: number
  task_id: number | null
  task_name?: string | null
  case_id?: number | null
  case_code?: string | null
  case_name?: string | null
  source: string
  status: string
  attempt_no: number
  attempt_count?: number
  error_kind: string | null
  pass_flag: boolean | null
  total_score: number | null
  case_version: number
  turns_used: number
  latency_ms: number | null
  ttft_ms: number | null
  started_at: string
  finished_at: string | null
}

export interface TrendPoint {
  execution_id: number
  started_at: string
  total_score: number | null
  latency_ms: number | null
  status: string
  pass_flag: boolean | null
  turns_used: number
}

export interface CaseTrend {
  case_id: number
  points: TrendPoint[]
  pass_rate: number
  avg_score: number
  avg_latency_ms: number
  flaky: boolean
}

export interface RunCompareSide {
  execution_id: number
  case_version: number
  status: string
  total_score: number | null
  pass_flag: boolean | null
  started_at: string
  final_answer: string | null
  generated_sql: string | null
  raw_db_data: unknown
  judge_reason: string | null
  turns: Array<Record<string, unknown>>
}

export interface RunCompare {
  case_id: number
  left: RunCompareSide
  right: RunCompareSide
  diffs: DiffChange[]
}

// ------------------------------------------------------------------ 任务与执行
export interface BatchTask {
  id: number
  code: string
  name: string
  description: string | null
  status: string
  control_state: string
  environment_id: number | null
  case_ids: number[]
  total_cases: number
  finished_cases: number
  pass_count: number
  fail_count: number
  error_count: number
  max_turns_count: number
  concurrency: number
  qps_limit: number
  retry_on_env_error: boolean
  max_env_retries: number
  retry_backoff_seconds: number
  created_by: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  summary: Record<string, unknown> | null
  error_message: string | null
  environment_name?: string | null
}

export interface StatusDistribution {
  PASS: number
  FAIL: number
  ERROR: number
  MAX_TURNS_EXCEEDED: number
  REPETITION_ABORT: number
  ABORTED: number
  RUNNING: number
  PENDING: number
}

export interface TaskMetrics {
  task_id: number
  status: string
  control_state: string
  total_cases: number
  finished_cases: number
  progress: number
  status_distribution: StatusDistribution
  pass_rate: number
  avg_ttft_ms: number
  avg_latency_ms: number
  p95_latency_ms: number
  avg_turns: number
  simulator_tokens: number
  judge_tokens: number
  total_tokens: number
  env_retry_count: number
  eta_seconds: number | null
  elapsed_seconds: number
  throughput_per_minute: number
}

export interface TaskProgressEvent {
  type: string
  task_id: number
  status?: string
  control_state?: string
  progress?: number
  finished_cases?: number
  total_cases?: number
  pass_count?: number
  fail_count?: number
  error_count?: number
  max_turns_count?: number
  metrics?: TaskMetrics | null
  execution?: CaseExecutionItem | null
  message?: string | null
  timestamp: string
}

export interface TurnTrace {
  id: number
  turn_index: number
  query: string
  input_source: string
  simulator_thought: string | null
  simulator_status: string | null
  hidden_context_released: unknown[] | null
  rendered_prompt: string | null
  request_payload: Record<string, unknown> | null
  response_raw: Record<string, unknown> | null
  final_answer: string | null
  session_id: string | null
  intent: string | null
  generated_sql: string | null
  api_params: Record<string, unknown> | null
  raw_db_data: Record<string, unknown> | null
  ttft_ms: number | null
  latency_ms: number | null
  rule_check: Record<string, unknown> | null
  simulator_prompt_tokens: number
  simulator_completion_tokens: number
  created_at: string
}

export interface JudgeResult {
  id: number
  model_config_id: number | null
  model_name: string
  pass_flag: boolean
  total_score: number
  dimension_scores: Record<string, number> | null
  error_tags: string[] | null
  reason: string | null
  raw_response: Record<string, unknown> | null
  judge_prompt: string | null
  prompt_tokens: number
  completion_tokens: number
  created_at: string
}

export interface ExecutionListItem {
  id: number
  task_id: number | null
  task_name: string | null
  case_id: number
  case_code: string | null
  case_name: string | null
  case_version: number
  source: string
  status: string
  attempt_no: number
  error_kind: string | null
  pass_flag: boolean | null
  total_score: number | null
  termination_status: string | null
  turns_used: number
  latency_ms: number | null
  ttft_ms: number | null
  simulator_tokens: number
  judge_tokens: number
  started_at: string
  finished_at: string | null
}

export interface ExecutionDetail extends ExecutionListItem {
  environment_id: number | null
  max_turns: number | null
  simulator_template_version: number | null
  judge_template_version: number | null
  rule_result: Record<string, unknown> | null
  judge_result: Record<string, unknown> | null
  error_message: string | null
  case_snapshot: Record<string, unknown>
  trace_uid: string
  turns: TurnTrace[]
  judges: JudgeResult[]
  raw_request_response: Array<Record<string, unknown>>
}

export interface RawExecution {
  execution_id: number
  trace_uid: string
  case_snapshot: Record<string, unknown>
  turns: Array<Record<string, unknown>>
  judges: Array<{ model: string; prompt: string | null }>
}
