export interface DomainConfig {
  miningApi: string
  servingApi: string
  llmApi: string
  active: boolean
}

export type DomainMap = Record<string, DomainConfig>

export interface HealthStatus {
  status: 'healthy' | 'degraded' | 'unhealthy'
  message?: string
  timestamp?: string
}

export interface KnowledgeStats {
  documents: number
  segments: number
  units: number
  relations: number
  entities: number
}

export interface MiningRun {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  domain: string
  created_at: string
  started_at?: string
  finished_at?: string
  document_count: number
  build_id?: string
  error_message?: string
  config?: Record<string, unknown>
}

export interface MiningRunStage {
  name: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  started_at?: string
  finished_at?: string
  duration_seconds?: number
  progress?: number
  details?: Record<string, unknown>
}

export interface MiningRunDocument {
  document_id: string
  filename: string
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'skipped'
  action: 'new' | 'updated' | 'unchanged'
  error_message?: string
}

export interface SearchResult {
  items: SearchResultItem[]
  relations: SearchRelation[]
  debug?: SearchDebug
  total_items: number
  total_relations: number
  elapsed_ms: number
}

export interface SearchResultItem {
  id: string
  content: string
  score: number
  source: string
  item_type: string
  metadata?: Record<string, unknown>
}

export interface SearchRelation {
  from_entity: string
  to_entity: string
  relation_type: string
  weight?: number
}

export interface SearchDebug {
  pipeline_steps: DebugStep[]
  query_understanding: Record<string, unknown>
  routes: Record<string, unknown>
  fusion: Record<string, unknown>
  rerank: Record<string, unknown>
  graph_expansion: Record<string, unknown>
}

export interface DebugStep {
  name: string
  duration_ms: number
  details?: Record<string, unknown>
}

export interface KnowledgeDocument {
  id: string
  filename: string
  doc_type: string
  snapshot_count: number
  created_at: string
  updated_at: string
}

export interface KnowledgeSegment {
  id: string
  document_id: string
  segment_type: string
  content: string
  token_count: number
  heading?: string
  position: number
}

export interface KnowledgeUnit {
  id: string
  segment_id: string
  content: string
  unit_type: string
  token_count: number
}

export interface LlmTaskStats {
  total_tasks: number
  success_rate: number
  running_tasks: number
  total_tokens: number
  avg_latency_ms: number
  task_type_distribution: Record<string, number>
  latency_percentiles: {
    p50: number
    p95: number
    p99: number
  }
}

export interface LlmTask {
  id: string
  task_type: string
  domain: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  created_at: string
  finished_at?: string
  duration_ms?: number
  token_count?: number
  error_message?: string
}

export interface ApiResponse<T> {
  success: boolean
  data: T
  error?: string
  meta?: {
    total?: number
    page?: number
    limit?: number
  }
}
