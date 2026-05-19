import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { MiningRun, MiningRunStage, MiningRunDocument } from '@/types'
import { useMiningApi } from '@/api/mining'

export const useMiningStore = defineStore('mining', () => {
  const runs = ref<MiningRun[]>([])
  const currentRun = ref<MiningRun | null>(null)
  const stages = ref<MiningRunStage[]>([])
  const documents = ref<MiningRunDocument[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  const miningApi = useMiningApi()

  async function fetchRuns() {
    loading.value = true
    error.value = null
    try {
      runs.value = await miningApi.getRuns()
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch runs'
    } finally {
      loading.value = false
    }
  }

  async function fetchRunDetail(runId: string) {
    loading.value = true
    error.value = null
    try {
      const [run, runStages, runDocs] = await Promise.all([
        miningApi.getRun(runId),
        miningApi.getRunStages(runId),
        miningApi.getRunDocuments(runId),
      ])
      currentRun.value = run
      stages.value = runStages
      documents.value = runDocs
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch run detail'
    } finally {
      loading.value = false
    }
  }

  async function createRun(config: Record<string, unknown>) {
    try {
      const run = await miningApi.createRun(config)
      runs.value = [run, ...runs.value]
      return run
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Failed to create run'
      throw e
    }
  }

  async function cancelRun(runId: string) {
    try {
      await miningApi.cancelRun(runId)
      const run = runs.value.find(r => r.id === runId)
      if (run) run.status = 'cancelled'
      if (currentRun.value?.id === runId) currentRun.value.status = 'cancelled'
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Failed to cancel run'
    }
  }

  async function publishRun(runId: string) {
    try {
      await miningApi.publishRun(runId)
      const run = runs.value.find(r => r.id === runId)
      if (run) run.build_id = `bld_${runId}`
      if (currentRun.value?.id === runId) currentRun.value.build_id = `bld_${runId}`
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Failed to publish run'
    }
  }

  function clearCurrentRun() {
    currentRun.value = null
    stages.value = []
    documents.value = []
  }

  return {
    runs, currentRun, stages, documents, loading, error,
    fetchRuns, fetchRunDetail, createRun, cancelRun, publishRun, clearCurrentRun,
  }
})
