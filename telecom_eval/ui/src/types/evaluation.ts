// 评估工作台 DTO（与 telecom_eval FastAPI schemas 对齐）

export type MetricStatus =
  | 'ok'
  | 'not_applicable'
  | 'missing_inputs'
  | 'inconclusive'
  | 'error'

export interface JudgeBudget {
  allow_llm_judge: boolean
  max_llm_calls: number
  max_llm_retries: number
  max_prompt_tokens: number
  max_completion_tokens: number
  max_total_tokens: number
  max_cases_with_llm: number
}

export interface CreateRunRequest {
  dataset_id: string
  subject_id: string
  subject_search_path?: string | null
  answer_model_id?: string | null
  judge_model_id?: string | null
  eval_type: 'retrieval' | 'e2e' | 'mixed'
  metric_suite_ids: string[]
  confirmed_only: boolean
  top_k: number
  allow_llm_judge: boolean
  judge_budget: JudgeBudget
}

export interface EvaluationRunSummary {
  run_id: string
  subject_id: string
  dataset_id: string
  eval_type: string
  status: string
  allow_llm_judge?: boolean
  created_at: string
  started_at?: string | null
  completed_at?: string | null
}

export interface PublishedParadigm {
  id: string
  name: string
  label: string
  value: string
  description: string | null
  version: number
  url: string
}

export interface EvalModelOption {
  id: string
  label: string
  channel: string
  model: string
  supports_answer: boolean
  supports_judge: boolean
}

export interface EvalModelCatalog {
  models: EvalModelOption[]
  default_answer_model_id: string | null
  default_judge_model_id: string | null
}

export interface DatasetSummary {
  dataset_id: string
  case_count: number
  confirmed_count: number
}

export interface MetricRow {
  metric_id: string
  level: string
  value: unknown
  status: MetricStatus | string
}

export interface MetricCaseRow {
  case_id: string
  metric_id: string
  level: string
  value: unknown
  status: MetricStatus | string
}

export interface FailureRow {
  case_id: string
  failure_type: string | null
  severity: string | null
}

export interface JudgeUsage {
  total_invocations: number
  ok_calls: number
  skipped_calls: number
  total_tokens: number
  cases_with_llm: number
}

export interface RunReportViewModel {
  run: Record<string, unknown>
  metrics: MetricRow[]
  metric_cases: Record<string, MetricCaseRow[]>
  metric_summary: Record<string, Record<string, unknown>>
  failures: FailureRow[]
  judge_usage: JudgeUsage
  case_count: number
  markdown: string
}

export interface RunCaseRow {
  case_id: string
  metrics: Record<string, unknown>
  metric_statuses?: Record<string, MetricStatus | string>
  failure_type: string | null
  severity: string | null
}

export interface CaseDebugViewModel {
  case: Record<string, unknown>
  retrieval_trace?: Record<string, unknown> | null
  answer_trace?: Record<string, unknown> | null
  metrics: Record<string, unknown>[]
  artifacts: Record<string, unknown>[]
  diagnosis?: Record<string, unknown> | null
  evidence_alignment: Record<string, unknown>[]
  retrieval_content_judgment?: Record<string, unknown> | null
  expected_evidence: Record<string, unknown>[]
  judge_invocations: Record<string, unknown>[]
  warnings: Record<string, unknown>[]
  errors: string[]
  raw: Record<string, unknown>
}

export interface ComparisonDeltaRow {
  case_id: string
  before: number
  after: number
  absolute_delta: number
  relative_delta: number | null
  change_type: string
}

export interface ComparisonReportViewModel {
  comparison_id: string
  baseline_run_id: string
  candidate_run_id: string
  status: string
  summary: Record<string, unknown>
  delta_rows: ComparisonDeltaRow[]
  regressed_cases: Record<string, unknown>[]
  newly_passed: string[]
  newly_failed: string[]
}

export interface CreateComparisonRequest {
  baseline_run_id: string
  candidate_run_id: string
  metric_id: string
  comparison_type: 'before_after' | 'baseline_candidate' | 'ablation'
}

// ---- 测试集 / 导入 / 快照 ----

export interface EvaluationDataset {
  dataset_id: string
  name: string
  description: string
  scenario_id: string
  dataset_type: string
  owner?: string | null
  tags: string[]
  status: 'draft' | 'active' | 'archived' | string
  case_count: number
  confirmed_case_count: number
  confirmed_count?: number
  created_at: string
  updated_at: string
}

export interface CreateDatasetRequest {
  name: string
  scenario_id: string
  dataset_type: 'retrieval' | 'e2e' | 'mixed'
  description: string
  tags: string[]
  owner?: string | null
}

export interface DatasetImportPreviewRow {
  row_number: number
  status: 'confirmable' | 'draft' | 'rejected'
  normalized_question?: string | null
  mapped_case?: Record<string, unknown> | null
  warnings: string[]
  errors: string[]
  duplicate_of_case_id?: string | null
}

export interface DatasetImportPreview {
  import_id: string
  dataset_id: string
  filename: string
  status: string
  total_rows: number
  confirmable_rows: number
  draft_rows: number
  rejected_rows: number
  rows: DatasetImportPreviewRow[]
}

export interface CommitImportRequest {
  import_id: string
  duplicate_policy: 'skip' | 'import_as_draft' | 'copy' | 'update_draft'
  confirm_complete_rows: boolean
}

export interface CommitImportResult {
  import_id: string
  inserted_confirmed: number
  inserted_draft: number
  skipped: number
  rejected: number
}

export interface ConfirmCasesResult {
  confirmed: number
  skipped: number
  total: number
}

export interface DatasetSnapshot {
  dataset_snapshot_id: string
  dataset_id: string
  dataset_version: string
  case_ids: string[]
  case_fingerprints: Record<string, string>
  confirmed_only: boolean
  gold_policy: string
  created_at: string
}

// ---- 案例编辑 / 关联原文 ----

export interface EvidenceRef {
  evidence_id?: string
  raw_segment_ids?: string[]
  source_id?: string
  document_key?: string
  segment_index?: number
  title?: string
  text?: string
  score?: number
}

export interface PassageCandidate {
  evidence_id: string
  observed_item_id?: string
  title?: string
  content?: string
  source_id?: string
  source_type?: string
  score?: number
  match_keys?: string[]
  provenance?: Record<string, unknown>
}

export interface UpdateCaseRequest {
  question?: string
  expected_answer?: string
  expected_key_points?: string[]
  expected_evidence?: EvidenceRef[]
  task_type?: string
  answerability?: string
  risk_level?: string
  tags?: string[]
  gold_status?: string
  allow_confirmed_edit?: boolean
}

export function defaultJudgeBudget(): JudgeBudget {
  return {
    allow_llm_judge: false,
    max_llm_calls: 0,
    max_llm_retries: 0,
    max_prompt_tokens: 0,
    max_completion_tokens: 0,
    max_total_tokens: 0,
    max_cases_with_llm: 0,
  }
}

export function defaultCreateRunRequest(): CreateRunRequest {
  return {
    dataset_id: '',
    subject_id: '',
    subject_search_path: null,
    answer_model_id: null,
    judge_model_id: null,
    eval_type: 'mixed',
    metric_suite_ids: ['retrieval_basic_suite', 'e2e_basic_suite', 'evaluation_efficiency_suite'],
    confirmed_only: true,
    top_k: 10,
    allow_llm_judge: false,
    judge_budget: defaultJudgeBudget(),
  }
}
