export interface DomainConfig {
  miningApi: string
  servingApi: string
  llmApi: string
  active: boolean
}

export type DomainMap = Record<string, DomainConfig>

export interface HealthStatus {
  status: string
  message?: string
  timestamp?: string
  version?: string
}

// ─── Knowledge Stats ───

export interface KnowledgeStats {
  documents: number
  snapshots: number
  segments: number
  relations: number
  retrieval_units: number
  embeddings: number
  builds: number
  releases: number
  retrieval_units_by_type?: Record<string, number>
  active_release?: string
}

// ─── Mining Run ───

export interface MiningRun {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  input_path?: string
  domain?: string
  created_at: string
  started_at?: string
  finished_at?: string
  total_documents: number
  committed_count: number
  failed_count: number
  skipped_count: number
  new_count: number
  updated_count: number
  build_id?: string
  error_message?: string
  config?: Record<string, unknown>
}

export interface MiningRunStage {
  id: string
  stage: string
  status: string
  created_at: string
  duration_ms?: number | null
  output_summary?: string | null
  error_message?: string | null
  run_document_id?: string | null
}

export interface MiningRunDocument {
  id?: string
  document_id: string
  document_name: string
  document_key?: string
  status: 'pending' | 'processing' | 'committed' | 'failed' | 'skipped'
  action: 'new' | 'updated' | 'unchanged'
  error_message?: string
  error_summary?: string
  current_stage?: string | null
  duration_ms?: number | null
  started_at?: string
  finished_at?: string
  document_snapshot_id?: string | null
  stage?: string
}

// ─── Knowledge Assets ───

export interface KnowledgeDocument {
  id: string
  document_key: string
  document_name: string
  document_type: string
  metadata_json?: Record<string, unknown>
  created_at: string
}

export interface KnowledgeSegment {
  id: string
  segment_key: string
  segment_index: number
  block_type: string
  semantic_role: string
  section_title?: string
  raw_text: string
  token_count: number
}

export interface KnowledgeUnit {
  id: string
  unit_key: string
  unit_type: 'raw_text' | 'contextual_text' | 'summary' | 'generated_question' | 'entity_card'
  target_type: string
  title: string
  text: string
  weight: number
  block_type?: string
  semantic_role?: string
  created_at?: string
}

export interface KnowledgeRelation {
  id: string
  document_snapshot_id: string
  source_segment_id: string
  target_segment_id: string
  relation_type: string
  weight: number
  confidence: number
  distance: number
  source_text?: string
  target_text?: string
}

// ─── Search / Serving ───

export interface SearchResult {
  items: SearchContextItem[]
  relations: SearchContextRelation[]
  sources: SearchSourceRef[]
  evidence_groups?: SearchEvidenceGroup[]
  issues?: SearchIssue[]
  suggestions?: string[]
  debug?: SearchDebug
}

export interface SearchContextItem {
  id: string
  kind: string
  role: 'seed' | 'context' | 'support'
  text: string
  score: number
  title: string
  blockType: string
  semanticRole: string
  sourceId: string
  relationToSeed?: string
  routeSources?: string[]
  scoreChain?: Record<string, number>
  evidenceRole: string
  metadata?: Record<string, unknown>
}

export interface SearchContextRelation {
  id: string
  fromId: string
  toId: string
  relationType: string
  distance?: number
}

export interface SearchSourceRef {
  id: string
  documentKey: string
  title: string
  relativePath?: string
  metadata?: Record<string, unknown>
}

export interface SearchEvidenceGroup {
  id: string
  role: string
  items: string[]
}

export interface SearchIssue {
  severity: string
  message: string
}

export interface SearchDebug {
  understanding?: {
    original_query: string
    intent: string
    source: string
    keywords: string[]
    entities_count: number
  }
  route_plan?: {
    routes_count: number
    fusion_method: string
    rerank_method: string
  }
  scope?: {
    release_id: string
    snapshot_count: number
  }
  trace?: {
    request_id: string
    total_duration_ms: number
    stages: SearchDebugStage[]
  }
  candidate_count?: number
  fusion_method?: string
  query_embedding_dim?: number
}

export interface SearchDebugStage {
  name: string
  duration_ms: number
  summary?: string
  input?: string
  output?: string
  error?: string | null
}

// ─── LLM Service ───

export interface LlmTaskStats {
  tasks_by_status: Record<string, number>
  tasks_by_type?: Record<string, number>
  succeeded_attempts: number
  total_tokens: number
  avg_latency_ms: number
  services?: string[]
  domains?: string[]
  stages?: string[]
}

export interface LlmTask {
  id: string
  task_type: 'chat' | 'embedding' | 'rerank'
  caller_service?: string
  knowledge_domain?: string
  pipeline_stage?: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'dead_letter' | 'cancelled'
  priority: number
  attempt_count: number
  max_attempts: number
  created_at: string
  started_at?: string
  finished_at?: string
  idempotency_key?: string
  error_message?: string
  total_tokens?: number
  latency_ms?: number
  metadata?: Record<string, unknown>
}

export interface LlmTaskDetail extends LlmTask {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  latency_ms?: number
  raw_response?: Record<string, unknown>
  parsed_output?: Record<string, unknown>
}

// ─── Paginated Response ───

export interface PaginatedResponse<T> {
  total: number
  limit: number
  offset: number
  items: T[]
}
