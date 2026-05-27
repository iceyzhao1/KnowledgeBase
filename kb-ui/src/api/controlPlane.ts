import axios from 'axios'

const baseURL = import.meta.env.VITE_CONTROL_PLANE_API_BASE || 'http://localhost:8910'
const client = axios.create({ baseURL })

export function useControlPlaneApi() {
  return {
    // ── System config ──
    async listSystemConfigs(): Promise<string[]> {
      const { data } = await client.get('/api/v1/system')
      return data.items ?? []
    },

    async getSystemConfigRaw(name: string): Promise<string> {
      const { data } = await client.get(`/api/v1/system/${name}/raw`, { responseType: 'text' })
      return typeof data === 'string' ? data : JSON.stringify(data)
    },

    async updateSystemConfigRaw(name: string, yamlText: string): Promise<void> {
      await client.put(`/api/v1/system/${name}/raw`, yamlText, {
        headers: { 'Content-Type': 'text/yaml' },
      })
    },

    // ── Domains ──
    async getDomains(): Promise<ControlPlaneDomainSummary[]> {
      const { data } = await client.get('/api/v1/domains')
      return data.items ?? []
    },

    async getDomain(domainId: string): Promise<ControlPlaneDomainSummary> {
      const { data } = await client.get(`/api/v1/domains/${domainId}`)
      return data
    },

    async getDomainRaw(domainId: string): Promise<string> {
      const { data } = await client.get(`/api/v1/domains/${domainId}/raw`, { responseType: 'text' })
      return typeof data === 'string' ? data : JSON.stringify(data)
    },

    async createDomain(domainId: string, extra?: Record<string, unknown>): Promise<Record<string, unknown>> {
      const { data } = await client.post('/api/v1/domains', { domain_id: domainId, ...extra })
      return data
    },

    async updateDomainRaw(domainId: string, yamlText: string): Promise<void> {
      await client.put(`/api/v1/domains/${domainId}/raw`, yamlText, {
        headers: { 'Content-Type': 'text/yaml' },
      })
    },

    async deleteDomain(domainId: string): Promise<void> {
      await client.delete(`/api/v1/domains/${domainId}`)
    },

    // ── Scenario packs ──
    async getScenarioRaw(domainId: string): Promise<string> {
      const { data } = await client.get(`/api/v1/domains/${domainId}/scenario/raw`, { responseType: 'text' })
      return typeof data === 'string' ? data : JSON.stringify(data)
    },

    async updateScenarioRaw(domainId: string, yamlText: string): Promise<void> {
      await client.put(`/api/v1/domains/${domainId}/scenario/raw`, yamlText, {
        headers: { 'Content-Type': 'text/yaml' },
      })
    },
  }
}

// ── Minimal types (co-located) ──

export interface ControlPlaneDomainSummary {
  domain_id: string
  display_name: string
  enabled: boolean
  default_channel: string
  scenario_pack_ref: string
}
