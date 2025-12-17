/** Shared API response types. */

export type RunStatus = 'success' | 'failure' | 'partial' | 'running' | 'unknown'
export type SpanStatus = 'ok' | 'error' | 'timeout' | 'running' | 'cancelled' | 'unknown'
export type ActionType =
  | 'agent'
  | 'planning'
  | 'model'
  | 'tool'
  | 'retrieval'
  | 'delegation'
  | 'evaluation'
  | 'state'
  | 'unknown'

export interface Span {
  span_id: string
  parent_span_id?: string | null
  action_id?: string | null
  name: string
  action_type: ActionType
  start_time: string
  end_time?: string | null
  status: SpanStatus
  inputs?: unknown
  outputs?: unknown
  tool_name?: string | null
  tool_call_id?: string | null
  model?: string | null
  provider?: string | null
  token_usage?: Record<string, number>
  cost_usd?: number | null
  retry_attempt?: number | null
  exception?: string | null
  explicit_dependencies?: string[]
  attributes?: Record<string, unknown>
  artifacts?: Array<Record<string, unknown>>
  duration_ms?: number | null
}

export interface Trace {
  schema_version: string
  trace_id: string
  run_id: string
  session_id?: string | null
  task_id?: string | null
  task: string
  agent_id?: string | null
  started_at: string
  ended_at?: string | null
  status: RunStatus
  final_answer?: unknown
  evaluation_results?: Record<string, unknown>
  environment?: Record<string, unknown>
  spans: Span[]
  provenance?: Record<string, unknown>
  import_warnings?: string[]
}

export interface RunSummary {
  run_id: string
  trace_id: string
  task_id?: string | null
  task: string
  status: RunStatus
  started_at: string
  ended_at?: string | null
  agent_id?: string | null
  provider?: string | null
  model?: string | null
  span_count: number
  token_total: number
  cost_usd?: number
  duration_ms?: number | null
  source_format: string
  imported_at: string
  environment?: Record<string, unknown>
  evaluation_results?: Record<string, unknown>
  import_warnings?: string[]
}

export interface RunsResponse {
  items: RunSummary[]
  total: number
  limit: number
  offset: number
  workspace_id: string
}

export interface RunDetail extends RunSummary {
  trace: Trace
  annotations: Annotation[]
  experiments: ExperimentRecord[]
  findings: Finding[]
  hypotheses: Hypothesis[]
}

export interface Evidence {
  span_id: string
  field: string
  value: string
}

export interface Finding {
  finding_id: string
  rule_id: string
  category: string
  status: EvidenceStatus
  confidence: number
  span_ids: string[]
  evidence: Evidence[]
  supporting_evidence?: string[]
  contradicting_evidence?: string[]
  limitations?: string[]
  suggested_investigation?: string
  calibration_note?: string
  downstream_effects?: string[]
}

export type EvidenceStatus =
  | 'Observed'
  | 'Inferred Hypothesis'
  | 'Experimentally Supported'
  | 'Experimentally Refuted'
  | 'Confirmed Under Specified Test Conditions'
  | 'Insufficient Evidence'

export interface Hypothesis {
  hypothesis_id: string
  title: string
  category: string
  status: EvidenceStatus
  confidence: number
  primary_span_id?: string | null
  span_ids: string[]
  supporting_evidence: string[]
  contradicting_evidence: string[]
  missing_information?: string[]
  limitations: string[]
  suggested_experiment?: string
  evidence_finding_ids?: string[]
}

export interface GraphNode {
  span_id: string
  parent_span_id?: string | null
  name: string
  action_type: ActionType
  status: SpanStatus
  start_time: string
  end_time?: string | null
  duration_ms?: number | null
  tool_name?: string | null
  model?: string | null
  provider?: string | null
  retry_attempt?: number | null
  exception?: string | null
  explicit_dependencies?: string[]
}

export interface GraphEdge {
  source: string
  target: string
  kind: 'parent_child' | 'explicit_dependency' | 'span_link' | 'tool_call_reference'
  dependency: boolean
  evidence: string
}

export interface GraphWarning {
  type: string
  span_id?: string
  reference?: string
  span_ids?: string[]
  message: string
}

export interface DependencyGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  chronological_order: string[]
  logical_order: string[]
  roots: string[]
  warnings: GraphWarning[]
  critical_path?: { span_ids: string[]; duration_ms: number; basis: string } | null
  critical_path_ms?: number | null
  critical_path_supported?: boolean
}

export interface Divergence {
  kind: string
  classification?: string
  status?: string
  causal_claim?: boolean
  note?: string
  left_span_id?: string | null
  right_span_id?: string | null
  left_action?: unknown
  right_action?: unknown
  differences?: Array<{ field: string; left: unknown; right: unknown; delta_ms?: number }>
  trace_ids?: string[]
}

export interface Analysis {
  run_id: string
  trace_id: string
  rules_version: string
  summary: string
  earliest_observed_error: {
    span_id?: string | null
    category: string
    status: string
    finding_id: string
    timestamp?: string | null
    rule_id?: string
    note?: string
  } | null
  earliest_observed_divergence?: Divergence | null
  earliest_plausible_failure_propagation?: Finding | null
  earliest_plausible_failure_propagation_point?: {
    span_id?: string | null
    affected_span_ids: string[]
    status: string
    rule_id: string
    confidence: number
    note: string
  } | null
  findings: Finding[]
  root_cause_hypotheses: Hypothesis[]
  graph: DependencyGraph
  impact: Record<string, string[]>
  reference_comparison?: ComparisonResult | null
  run_outcome: RunStatus
  categories: string[]
  evidence_statuses: EvidenceStatus[]
  missing_information: string[]
  suggested_experiments: Array<{ hypothesis_id: string; title: string; proposal: string; span_ids: string[]; safety: string }>
  evidence_policy: string
  annotations: Annotation[]
  experiments: ExperimentRecord[]
}

export interface Annotation {
  id: number
  run_id: string
  finding_id?: string | null
  author: string
  note: string
  disposition?: string | null
  created_at: string
}

export interface ExperimentStep {
  span_id: string
  action: string
  adapter?: string
  outcome: string
  reason?: string
  input_from_span?: string
  details?: unknown
  side_effects?: boolean
}

export interface ExperimentResult {
  title: string
  status: EvidenceStatus
  decision: string
  scope: string
  source_span_id: string
  source_run_id: string
  fixture_sha256: string
  fixture_redacted: unknown
  steps: ExperimentStep[]
  limitations: string[]
  reproducibility: Record<string, unknown>
}

export interface ExperimentRecord {
  id: string
  run_id: string
  source_span_id: string
  title: string
  status: EvidenceStatus
  decision: string
  result: ExperimentResult
  created_at: string
}

export interface AlignedAction {
  kind: 'matched' | 'changed' | 'missing_in_right' | 'added_in_right'
  left_run_id: string
  right_run_id: string
  left_span_id?: string | null
  right_span_id?: string | null
  left: Span | null
  right: Span | null
  differences: Array<{ field: string; left: unknown; right: unknown; delta_ms?: number }>
}

export interface ComparisonPair {
  left_run_id: string
  right_run_id: string
  aligned_actions: AlignedAction[]
  first_divergence: Divergence | null
  divergence_note: string
  topology_changed: boolean
  metrics: {
    left: { run_id: string; trace_id: string; task: string; status: RunStatus; duration_ms: number | null; tokens: number; cost_usd: number; model_provider: string[] }
    right: { run_id: string; trace_id: string; task: string; status: RunStatus; duration_ms: number | null; tokens: number; cost_usd: number; model_provider: string[] }
    duration_delta_ms: number | null
    token_delta: number | null
    cost_delta_usd: number | null
  }
  left_order: string[]
  right_order: string[]
  alignment_method: string
}

export interface ComparisonResult {
  baseline_run_id: string
  run_ids: string[]
  task_equivalence: string
  pairwise: ComparisonPair[]
  first_divergence: Divergence | null
  comparison_note: string
}

export interface Overview {
  total_runs: number
  successful_runs: number
  failed_runs: number
  success_rate: number | null
  avg_duration_ms: number | null
  p95_duration_ms: number | null
  total_tokens: number
  total_cost_usd: number
  failure_categories: Record<string, number>
  failure_categories_runs_analyzed: number
  recent_runs: RunSummary[]
  latency_series: Array<{ run_id: string; task: string; duration_ms: number; status: RunStatus; started_at: string }>
  token_series: Array<{ run_id: string; task: string; tokens: number; status: RunStatus }>
  provider_counts: Record<string, number>
  status_counts: Record<string, number>
}

export interface ToolAnalytics {
  tools: Array<{
    tool: string
    calls: number
    errors: number
    timeouts: number
    ok: number
    recovered: number
    error_types: Record<string, number>
    failure_rate: number
    median_duration_ms: number | null
    p95_duration_ms: number | null
    max_duration_ms: number | null
  }>
  total_tool_calls: number
  total_failures: number
  recovered_calls: number
  error_types: Record<string, number>
  latency_outliers: Array<{ run_id: string; span_id: string; tool: string; duration_ms: number; status: string }>
}

export interface LlmProviderOption {
  id: string
  label: string
  configured: boolean
  requires_api_key: boolean
  model_discovery: boolean
  setup_hint: string
  docs_url: string
  credential_present: boolean
}

export interface LlmStatus {
  provider: string
  provider_label: string
  model: string
  configured: boolean
  credential_source: string
  ready: boolean
  enabled: boolean
  reason: string | null
  providers: LlmProviderOption[]
  requires_external_consent: boolean
  redacts_evidence: boolean
  daily_limit: number
  calls_today: number
  remaining_today: number
  per_minute_limit: number
  timeout_seconds: number
  max_completion_tokens: number
  daily_budget_usd: number
  monthly_budget_usd: number
  cost_spent_today_usd: number
  cost_visibility: string
  pricing: {
    as_of: string
    sources: Record<string, string>
    models_with_known_price: Record<string, number>
    override_env: string
    pricing_file_env: string
    note: string
  }
}

export interface AppSettings {
  app_name: string
  version: string
  environment: string
  mode: string
  authentication_enabled: boolean
  workspace_id: string
  redaction_enabled: boolean
  default_redaction: boolean
  ai_enabled: boolean
  ai_status: string
  llm: LlmStatus
  api_base: string
  retention_days: number
  retention: string
  limits: Record<string, number>
  storage: { backend: string; redis_queue: boolean }
}

export interface AuditEvent {
  id: number
  event_type: string
  run_id?: string | null
  actor: string
  detail: Record<string, unknown>
  created_at: string
}
