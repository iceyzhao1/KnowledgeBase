import axios from 'axios'
import type { HealthStatus, LlmTaskStats, LlmTask } from '@/types'
import { useDomainStore } from '@/stores/domain'

export function useLlmApi() {
  function getClient() {
    const domain = useDomainStore()
    return axios.create({ baseURL: domain.currentConfig.llmApi })
  }

  return {
    async getHealth(): Promise<HealthStatus> {
      const { data } = await getClient().get('/health')
      return data
    },

    async getStats(): Promise<LlmTaskStats> {
      const { data } = await getClient().get('/dashboard/api/stats')
      return data
    },

    async getTasks(params?: { type?: string; status?: string; domain?: string; limit?: number }): Promise<LlmTask[]> {
      const { data } = await getClient().get('/api/v1/tasks', { params })
      return data.data ?? data
    },

    async getTask(taskId: string): Promise<LlmTask> {
      const { data } = await getClient().get(`/api/v1/tasks/${taskId}`)
      return data.data ?? data
    },
  }
}
