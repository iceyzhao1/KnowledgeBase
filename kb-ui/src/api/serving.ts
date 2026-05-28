import axios from 'axios'
import type { HealthStatus, SearchResult } from '@/types'
import { useDomainStore } from '@/stores/domain'

export function useServingApi() {
  const domain = useDomainStore()
  const client = axios.create({
    baseURL: `/api/control-plane/api/v1/proxy/${domain.currentDomain}/serving`,
  })

  return {
    async getHealth(): Promise<HealthStatus> {
      const { data } = await client.get('/actuator/health')
      return data
    },

    async search(query: string, options?: { domain?: string; debug?: boolean }): Promise<SearchResult> {
      const { data } = await client.post('/api/v1/search', {
        query,
        domain: options?.domain,
        debug: options?.debug ?? true,
      })
      return data.data ?? data
    },
  }
}
