/**
 * 后端 Schema 的 TypeScript 映射。
 * 与 billing_qa/schemas 保持一致，字段尽量不做改名，便于排查问题。
 */

export type CaseType = 'SINGLE' | 'STATIC_MULTI' | 'DYNAMIC_MULTI'
export type CaseStatus = 'ACTIVE' | 'DISABLED' | 'DRAFT'
export type TemplateType = 'USER_SIMULATOR' | 'LLM_JUDGE' | 'CASE_AUTHOR'
export type TemplateStrategy = 'LATEST' | 'PINNED'
export type ModelRole = 'SIMULATOR' | 'JUDGE' | 'CASE_AUTHOR'
export type JudgeStrategy = 'AVG' | 'VETO'
export type TaskStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'ABORTED' | 'FAILED' | 'SCHEDULED'

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

export interface OpenCodeCatalogModel {
  value: string
  provider_id: string
  model_id: string
  name: string
  status: string | null
  enabled: boolean
  context_limit: number | null
  output_limit: number | null
}

export interface OpenCodeCatalogAgent {
  id: string
  name: string
  mode: string | null
  hidden: boolean
}

export interface OpenCodeCatalog {
  serve_url: string
  models: OpenCodeCatalogModel[]
  agents: OpenCodeCatalogAgent[]
}

export interface OpenCodeCatalogPayload {
  serve_url?: string | null
  serve_port?: number | null
  serve_username?: string | null
  serve_password?: string | null
  opencode_directory?: string | null
  model_id?: number | null
  timeout_seconds?: number
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
  aliases?: string[]
  require_name?: boolean
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

// ------------------------------------------------------- 答案契约（P0）
export type GradingMode = 'LEGACY' | 'ANSWER_CONTRACT'

export type InputType =
  | 'CLEAR'
  | 'UNDERSPECIFIED'
  | 'AMBIGUOUS_REF'
  | 'MULTI_INTENT'
  | 'VAGUE'
  | 'UNANSWERABLE'
  | 'CONTRADICTORY'
  | 'UNAUTHORIZED'

export type FactKind = 'scalar' | 'set' | 'topk' | 'date' | 'boolean' | 'text'

export type FactVerificationStatus =
  | 'PENDING'
  | 'VERIFIED'
  | 'CONFLICT'
  | 'NO_EVIDENCE'
  | 'WAIVED'
  | 'STALE'

export interface FactAtom {
  id?: string
  kind?: FactKind
  label?: string | null
  value?: number | string | boolean | null
  values?: string[]
  unit?: string | null
  tolerance?: number
  time_scope?: string | null
  matcher?: string | null
  evidence_required?: boolean
  derived_from?: string[]
  formula?: string | null
  keywords?: string[]
  aliases?: string[]
  require_name?: boolean
  // ---- 取证与验证（存于答案包络 JSON）----
  verification_status?: FactVerificationStatus
  verified_by?: string | null
  verified_at?: string | null
  verification_note?: string | null
  evidence_ref?: string | null
}

export interface FactAtomView extends FactAtom {
  section: 'must' | 'may' | 'must_not'
}


export interface AnswerEnvelope {
  must: FactAtom[]
  may?: FactAtom[]
  must_not?: FactAtom[]
  forbidden_phrases?: string[]
  aliases?: Record<string, string[]>
  tolerances?: Record<string, number>
  extras_policy?: 'strict' | 'lenient'
  clarity_rules?: Record<string, unknown>
}

export interface ClarifySpec {
  must_ask_slot: string[]
  must_not_assert?: string[]
}

export interface AssumptionScenario {
  assumption: string
  must: Record<string, unknown>
}

export interface AssumptionSpec {
  must_state_assumption: boolean
  scenarios: AssumptionScenario[]
}

export interface PartialSpec {
  must_disclose: boolean
  required_facts: string[]
}

export interface RefuseSpec {
  must_reason: boolean
}

export interface ResponseContract {
  allowed: Array<'clarify' | 'answer_with_assumption' | 'partial' | 'refuse'>
  clarify?: ClarifySpec | null
  answer_with_assumption?: AssumptionSpec | null
  partial?: PartialSpec | null
  refuse?: RefuseSpec | null
}

export interface PlanSpec {
  required_capabilities: string[]
  alternatives?: Record<string, string[]>
  max_tool_calls?: number | null
  allow_parallel?: boolean
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
  grading_mode?: string | null
  input_type?: string | null
  as_of?: string | null
  snapshot_id?: string | null
  answer_envelope?: AnswerEnvelope | null
  response_contract?: ResponseContract | null
  plan_spec?: PlanSpec | null
  risk_level?: string | null
  owner?: string | null
  review_status?: string | null
  reviewed_by?: string | null
  dataset_version?: string | null
  dedupe_hash?: string | null
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
  grading_mode?: GradingMode
  input_type?: InputType
  as_of?: string | null
  snapshot_id?: string | null
  answer_envelope?: AnswerEnvelope | null
  response_contract?: ResponseContract | null
  plan_spec?: PlanSpec | null
  risk_level?: 'LOW' | 'HIGH'
  owner?: string | null
  review_status?: string | null
  dataset_version?: string | null
  change_summary?: string | null
}

// ------------------------------------------------------------------ AI 用例草稿
export type DraftSource = 'USER' | 'ASSET' | 'CASE' | 'LLM' | 'NONE'

export interface DraftFact extends FactAtom {
  section: 'must' | 'may' | 'must_not'
  source: DraftSource
  source_ref?: string | null
}

export interface DraftMissing {
  field: string
  label: string
  reason: string
}

export interface DraftTurn {
  query: string
  expected_answer: string | null
  notes: string | null
}

export interface CaseAIDraft {
  case_type: CaseType
  name: string | null
  description: string | null
  priority: string | null
  tags: string[]
  grading_mode: GradingMode | null
  input_type: InputType | null
  as_of: string | null
  query: string | null
  expected_answer: string | null
  turns: DraftTurn[]
  must: DraftFact[]
  may: DraftFact[]
  must_not: DraftFact[]
  forbidden_phrases: string[]
  risk_level: 'LOW' | 'HIGH'
  confidence: number
  field_sources: Partial<Record<string, DraftSource>>
  missing: DraftMissing[]
  warnings: string[]
  suggested_code: string | null
}

export interface CaseAIDraftContext {
  calibers: number
  similar_cases: number
}

export interface CaseAIDraftResponse {
  drafts: CaseAIDraft[]
  mode: 'CREATE' | 'EDIT'
  base_case_id: number | null
  base_case_code: string | null
  model_id: number | null
  model_code: string | null
  model_name: string | null
  elapsed_ms: number
  context: CaseAIDraftContext
  warnings: string[]
}

export interface CaseAIDraftRequest {
  request: string
  mode?: 'CREATE' | 'EDIT'
  count?: number
  base_case_id?: number | null
  model_id?: number | null
  environment_id?: number | null
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
export type TaskScheduleType = 'IMMEDIATE' | 'SCHEDULED' | 'DAILY' | 'WEEKLY'

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
  schedule_type: TaskScheduleType
  scheduled_at: string | null
  schedule_time: string | null
  schedule_weekdays: number[] | null
  next_run_at: string | null
  last_run_at: string | null
  schedule_active: boolean
  schedule_source_id: number | null
  created_by: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  summary: Record<string, unknown> | null
  error_message: string | null
  environment_name?: string | null
  run_count?: number
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

export interface TaskRunSummary {
  total_runs: number
  completed: number
  failed: number
  aborted: number
  running: number
  pending: number
  pass_count: number
  finished_cases: number
  total_cases: number
  pass_rate: number
}

export interface TaskRunsPage {
  items: BatchTask[]
  total: number
  page: number
  page_size: number
  summary: TaskRunSummary
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
  grading_mode?: string | null
  lucky_pass?: boolean | null
  root_cause?: string | null
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
  answer_grading?: Record<string, unknown> | null
  fact_results?: Array<Record<string, unknown>> | null
  diagnostics?: Record<string, unknown> | null
  grader_version?: string | null
  as_of?: string | null
  raw_request_response: Array<Record<string, unknown>>
}

export interface RawExecution {
  execution_id: number
  trace_uid: string
  case_snapshot: Record<string, unknown>
  turns: Array<Record<string, unknown>>
  judges: Array<{ model: string; prompt: string | null }>
}

// ------------------------------------------------------- 事实验证 / 评审 / 冻结
export interface FactVerifyPayload {
  verification_status?: FactVerificationStatus
  note?: string | null
  evidence_ref?: string | null
}

export interface CaseReviewPayload {
  review_status: 'PENDING' | 'APPROVED' | 'REJECTED'
  note?: string | null
}

export interface CaseFreezePayload {
  dataset_version?: string | null
  note?: string | null
}

// ------------------------------------------------------- 运营：候选池
export type CandidateSource = 'PRODUCTION' | 'TRACE' | 'LLM' | 'MANUAL'
export type CandidateStatus = 'NEW' | 'TRIAGED' | 'PROMOTED' | 'REJECTED'

export interface CaseCandidate {
  id: number
  source: CandidateSource | string
  raw_question: string
  context: Record<string, unknown> | null
  trace_uid: string | null
  environment_id: number | null
  draft: Record<string, unknown> | null
  status: CandidateStatus | string
  dedupe_hash: string | null
  note: string | null
  reviewed_by: string | null
  promoted_case_id: number | null
  created_at: string
  updated_at: string
}

export interface CandidatePromoteResult {
  candidate_id: number
  case_id: number
  case_code: string
}

// ------------------------------------------------------- 运营：判分器自检
export interface GraderEval {
  id: number
  dataset_version: string | null
  grader_version: string
  mutant_kind: string
  total: number
  detected: number
  false_kills: number
  run_at: string
  details: Record<string, unknown> | null
}

// ------------------------------------------------------- 运营：业务口径
export interface Caliber {
  id: number
  key: string
  name: string
  definition: string | null
  canonical_source: string
  owner: string | null
  created_at: string
  updated_at: string
}

// ------------------------------------------------------- 运营：数据源
export type DataSourceKind = 'CYPHER_TOOL' | 'SQL_DB' | 'HTTP_API'

export interface DataSource {
  id: number
  code: string
  name: string
  kind: DataSourceKind | string
  endpoint_url: string | null
  description: string | null
  has_auth: boolean
  timeout_seconds: number
  max_rows: number
  tool_version: string | null
  is_active: boolean
  last_probe_status: string | null
  last_probe_latency_ms: number | null
  last_probe_at: string | null
  last_probe_message: string | null
  created_at: string
  updated_at: string
}

export interface ProbeResult {
  ok: boolean
  status: string
  latency_ms: number | null
  message: string
  meta: Record<string, unknown> | null
}

// ------------------------------------------------------- 运营：取数能力
export interface Capability {
  id: number
  key: string
  name: string
  kind: string
  description: string | null
  source_id: number | null
  query_text: string | null
  params_schema: Array<Record<string, unknown>> | null
  param_binding: Record<string, string> | null
  intent_patterns: string[] | null
  lookup_queries: Record<string, string> | null
  version: number
  status: string
  cert_result: Record<string, unknown> | null
  certified_at: string | null
  certified_by: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface GoldQuery {
  id: number
  case_id: number
  capability_id: number | null
  capability_version: number | null
  origin: string
  fact_ids: string[] | null
  template_params: Record<string, unknown> | null
  query_text: string | null
  columns: string[] | null
  rows: Array<Record<string, unknown>> | null
  row_count: number
  truncated: boolean
  executor_version: string | null
  executed_at: string
  is_active: boolean
}

export interface CapabilityExecuteResult {
  capability_id: number
  capability_key: string
  execution: Record<string, unknown>
  gold_query: GoldQuery
}

// ------------------------------------------------------- 运营：统计
export interface FactStatItem {
  fact_id: string
  label: string | null
  total: number
  passed: number
  pass_rate: number
  last_run_at: string | null
}

export interface FactStatsOverview {
  execution_total: number
  passed: number
  pass_rate: number
  lucky_pass: number
  avg_score: number
  fact_total: number
  fact_passed: number
  fact_pass_rate: number
  top_failed: FactStatItem[]
}

export interface DatasetStatItem {
  dataset_version: string | null
  grading_mode: string
  review_status: string
  total: number
}
