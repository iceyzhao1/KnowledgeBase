import axios from 'axios'
import type { MiningRun, MiningRunStage, MiningRunDocument, KnowledgeStats, HealthStatus } from '@/types'
import { useDomainStore } from '@/stores/domain'

export function useMiningApi() {
  function getClient() {
    const domain = useDomainStore()
    return axios.create({ baseURL: domain.currentConfig.miningApi })
  }

  return {
    async getHealth(): Promise<HealthStatus> {
      const { data } = await getClient().get('/health')
      return data
    },

    async getStats(): Promise<KnowledgeStats> {
      const { data } = await getClient().get('/api/knowledge/stats')
      return data
    },

    async getRuns(): Promise<MiningRun[]> {
      const { data } = await getClient().get('/api/runs')
      return data.data ?? data
    },

    async getRun(runId: string): Promise<MiningRun> {
      const { data } = await getClient().get(`/api/runs/${runId}`)
      return data.data ?? data
    },

    async getRunStages(runId: string): Promise<MiningRunStage[]> {
      const { data } = await getClient().get(`/api/runs/${runId}/stages`)
      return data.data ?? data
    },

    async getRunDocuments(runId: string): Promise<MiningRunDocument[]> {
      const { data } = await getClient().get(`/api/runs/${runId}/documents`)
      return data.data ?? data
    },

    async createRun(config: Record<string, unknown>): Promise<MiningRun> {
      const { data } = await getClient().post('/api/runs', config)
      return data.data ?? data
    },

    async cancelRun(runId: string): Promise<void> {
      await getClient().post(`/api/runs/${runId}/cancel`)
    },

    async publishRun(runId: string): Promise<void> {
      await getClient().post(`/api/runs/${runId}/publish`)
    },
  }
}
