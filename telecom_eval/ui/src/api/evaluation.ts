import { http, extractItems, extractOne } from '@/api/http'
import type {
  CaseDebugViewModel,
  ConfirmCasesResult,
  CommitImportRequest,
  CommitImportResult,
  ComparisonReportViewModel,
  CreateComparisonRequest,
  CreateDatasetRequest,
  CreateRunRequest,
  DatasetImportPreview,
  DatasetSnapshot,
  DatasetSummary,
  EvalModelCatalog,
  EvaluationDataset,
  EvaluationRunSummary,
  PassageCandidate,
  PublishedParadigm,
  RunCaseRow,
  RunReportViewModel,
  UpdateCaseRequest,
} from '@/types/evaluation'
import { normalizeParadigms } from '@/utils/paradigmCatalog.mjs'

const base = '/api/v1/eval'
// Dev-only proxy path. Vite rewrites this to:
// {runtime.ui.paradigm_api_base_url}/api/v1/paradigm
const paradigmBase = '/paradigm-api/api/v1/paradigm'

export function useEvaluationApi() {
  return {
    async getHealth(): Promise<{ status: string }> {
      const { data } = await http.get(`${base}/health`)
      return data
    },

    async getModelCatalog(): Promise<EvalModelCatalog> {
      const { data } = await http.get(`${base}/models`)
      return data
    },

    async listDatasets(): Promise<DatasetSummary[]> {
      const { data } = await http.get(`${base}/datasets`)
      return extractItems<DatasetSummary>(data)
    },

    // ---- 测试集管理 ----
    async listDatasetsFull(status?: string): Promise<EvaluationDataset[]> {
      const { data } = await http.get(`${base}/datasets`, { params: status ? { status } : undefined })
      return extractItems<EvaluationDataset>(data)
    },

    async createDataset(payload: CreateDatasetRequest): Promise<EvaluationDataset> {
      const { data } = await http.post(`${base}/datasets`, payload)
      return extractOne<EvaluationDataset>(data)
    },

    async getDataset(datasetId: string): Promise<EvaluationDataset> {
      const { data } = await http.get(`${base}/datasets/${datasetId}`)
      return extractOne<EvaluationDataset>(data)
    },

    async deleteDataset(datasetId: string): Promise<void> {
      await http.delete(`${base}/datasets/${datasetId}`)
    },

    async updateDataset(datasetId: string, patch: Record<string, unknown>): Promise<EvaluationDataset> {
      const { data } = await http.patch(`${base}/datasets/${datasetId}`, patch)
      return extractOne<EvaluationDataset>(data)
    },

    async listDatasetCases(datasetId: string, confirmedOnly = false): Promise<Record<string, unknown>[]> {
      const { data } = await http.get(`${base}/datasets/${datasetId}/cases`, {
        params: { confirmed_only: confirmedOnly },
      })
      return extractItems<Record<string, unknown>>(data)
    },

    async previewImport(datasetId: string, filename: string, content: string): Promise<DatasetImportPreview> {
      const { data } = await http.post(`${base}/datasets/${datasetId}/imports:preview`, { filename, content })
      return extractOne<DatasetImportPreview>(data)
    },

    async commitImport(datasetId: string, payload: CommitImportRequest): Promise<CommitImportResult> {
      const { data } = await http.post(`${base}/datasets/${datasetId}/imports`, payload)
      return extractOne<CommitImportResult>(data)
    },

    async createSnapshot(datasetId: string, confirmedOnly = true): Promise<DatasetSnapshot> {
      const { data } = await http.post(`${base}/datasets/${datasetId}/snapshots`, {
        confirmed_only: confirmedOnly,
        gold_policy: 'confirmed_only',
      })
      return extractOne<DatasetSnapshot>(data)
    },

    async getCase(caseId: string): Promise<Record<string, unknown>> {
      const { data } = await http.get(`${base}/cases/${caseId}`)
      return extractOne<Record<string, unknown>>(data)
    },

    async addCase(datasetId: string, caseData: Record<string, unknown>): Promise<Record<string, unknown>> {
      const { data } = await http.post(`${base}/datasets/${datasetId}/cases`, { case: caseData })
      return extractOne<Record<string, unknown>>(data)
    },

    async confirmDatasetCases(datasetId: string): Promise<ConfirmCasesResult> {
      const { data } = await http.post(`${base}/datasets/${datasetId}/cases:confirm`)
      return extractOne<ConfirmCasesResult>(data)
    },

    async updateCase(caseId: string, patch: UpdateCaseRequest): Promise<Record<string, unknown>> {
      const { data } = await http.patch(`${base}/cases/${caseId}`, patch)
      return extractOne<Record<string, unknown>>(data)
    },

    async deleteCase(caseId: string): Promise<void> {
      await http.delete(`${base}/cases/${caseId}`)
    },

    async searchPassages(query: string, topK = 10, domain?: string): Promise<PassageCandidate[]> {
      const { data } = await http.post(`${base}/retrieval/search`, { query, top_k: topK, domain })
      return extractItems<PassageCandidate>(data)
    },

    async listRuns(params?: { dataset_id?: string }): Promise<EvaluationRunSummary[]> {
      const { data } = await http.get(`${base}/runs`, { params })
      return extractItems<EvaluationRunSummary>(data)
    },

    async listPublishedParadigms(): Promise<PublishedParadigm[]> {
      const { data } = await http.get(`${paradigmBase}/published`)
      return normalizeParadigms(data) as PublishedParadigm[]
    },

    async getRun(runId: string): Promise<EvaluationRunSummary> {
      const { data } = await http.get(`${base}/runs/${runId}`)
      return extractOne<EvaluationRunSummary>(data)
    },

    async deleteRun(runId: string): Promise<void> {
      await http.delete(`${base}/runs/${runId}`)
    },

    async createRun(payload: CreateRunRequest): Promise<EvaluationRunSummary> {
      const { data } = await http.post(`${base}/runs`, payload)
      return extractOne<EvaluationRunSummary>(data)
    },

    async getRunReport(runId: string): Promise<RunReportViewModel> {
      const { data } = await http.get(`${base}/runs/${runId}/report`)
      return extractOne<RunReportViewModel>(data)
    },

    async listRunCases(runId: string): Promise<RunCaseRow[]> {
      const { data } = await http.get(`${base}/runs/${runId}/cases`)
      return extractItems<RunCaseRow>(data)
    },

    async getCaseDebug(runId: string, caseId: string): Promise<CaseDebugViewModel> {
      const { data } = await http.get(`${base}/debug/runs/${runId}/cases/${caseId}`)
      return extractOne<CaseDebugViewModel>(data)
    },

    async createComparison(payload: CreateComparisonRequest): Promise<ComparisonReportViewModel> {
      const { data } = await http.post(`${base}/comparisons`, payload)
      return extractOne<ComparisonReportViewModel>(data)
    },

    async getComparisonReport(comparisonId: string): Promise<ComparisonReportViewModel> {
      const { data } = await http.get(`${base}/comparisons/${comparisonId}/report`)
      return extractOne<ComparisonReportViewModel>(data)
    },
  }
}
