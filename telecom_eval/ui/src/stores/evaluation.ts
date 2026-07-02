import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useEvaluationApi } from '@/api/evaluation'
import type {
  CaseDebugViewModel,
  CreateRunRequest,
  DatasetSummary,
  EvaluationRunSummary,
  RunReportViewModel,
} from '@/types/evaluation'

export const useEvaluationStore = defineStore('evaluation', () => {
  const api = useEvaluationApi()

  const runs = ref<EvaluationRunSummary[]>([])
  const datasets = ref<DatasetSummary[]>([])
  const selectedRunReport = ref<RunReportViewModel | null>(null)
  const selectedCaseDebug = ref<CaseDebugViewModel | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function _guard<T>(fn: () => Promise<T>): Promise<T | null> {
    loading.value = true
    error.value = null
    try {
      return await fn()
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : String(e)
      return null
    } finally {
      loading.value = false
    }
  }

  async function loadRuns(datasetId?: string) {
    const data = await _guard(() => api.listRuns(datasetId ? { dataset_id: datasetId } : undefined))
    if (data) runs.value = data
  }

  async function loadDatasets() {
    const data = await _guard(() => api.listDatasets())
    if (data) datasets.value = data
  }

  async function createRun(payload: CreateRunRequest) {
    return _guard(() => api.createRun(payload))
  }

  async function loadRunReport(runId: string) {
    const data = await _guard(() => api.getRunReport(runId))
    selectedRunReport.value = data
    return data
  }

  async function loadCaseDebug(runId: string, caseId: string) {
    const data = await _guard(() => api.getCaseDebug(runId, caseId))
    selectedCaseDebug.value = data
    return data
  }

  return {
    runs,
    datasets,
    selectedRunReport,
    selectedCaseDebug,
    loading,
    error,
    loadRuns,
    loadDatasets,
    createRun,
    loadRunReport,
    loadCaseDebug,
  }
})
