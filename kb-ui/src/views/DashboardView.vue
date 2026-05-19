<template>
  <div class="dashboard">
    <!-- Health Row -->
    <div class="dashboard__row">
      <ServiceHealthCard name="Mining Service" :status="miningHealth" />
      <ServiceHealthCard name="Serving API" :status="servingHealth" />
      <ServiceHealthCard name="LLM Service" :status="llmHealth" />
    </div>

    <!-- Stats Row -->
    <div class="dashboard__row dashboard__row--stats">
      <StatsCard label="文档" :value="stats?.documents ?? '-'" />
      <StatsCard label="段落" :value="stats?.segments ?? '-'" />
      <StatsCard label="检索单元" :value="stats?.units ?? '-'" />
      <StatsCard label="关系" :value="stats?.relations ?? '-'" />
      <StatsCard label="实体" :value="stats?.entities ?? '-'" />
    </div>

    <!-- Recent Runs -->
    <div class="dashboard__section">
      <div class="dashboard__section-header">
        <h3>最近 Mining Runs</h3>
        <el-button text type="primary" @click="$router.push('/mining')">查看全部</el-button>
      </div>
      <el-table :data="recentRuns" stripe size="default" v-loading="loading">
        <el-table-column prop="id" label="Run ID" width="140">
          <template #default="{ row }">
            <router-link :to="`/mining/${row.id}`" class="link">{{ row.id }}</router-link>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="plain">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="document_count" label="文档数" width="100" />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useDomainStore } from '@/stores/domain'
import { useMiningStore } from '@/stores/mining'
import { useMiningApi } from '@/api/mining'
import { useServingApi } from '@/api/serving'
import { useLlmApi } from '@/api/llm'
import type { KnowledgeStats, HealthStatus } from '@/types'
import StatsCard from '@/components/common/StatsCard.vue'
import ServiceHealthCard from '@/components/common/ServiceHealthCard.vue'

const domainStore = useDomainStore()
const miningStore = useMiningStore()
const miningApi = useMiningApi()
const servingApi = useServingApi()
const llmApi = useLlmApi()

const stats = ref<KnowledgeStats | null>(null)
const miningHealth = ref<'healthy' | 'degraded' | 'unhealthy' | 'unknown'>('unknown')
const servingHealth = ref<'healthy' | 'degraded' | 'unhealthy' | 'unknown'>('unknown')
const llmHealth = ref<'healthy' | 'degraded' | 'unhealthy' | 'unknown'>('unknown')
const loading = ref(false)
const recentRuns = computed(() => miningStore.runs.slice(0, 5))

async function loadData() {
  loading.value = true
  const healthCheck = async (
    fn: () => Promise<HealthStatus>,
    setter: (v: 'healthy' | 'degraded' | 'unhealthy') => void
  ) => {
    try {
      const h = await fn()
      setter(h.status === 'healthy' ? 'healthy' : h.status === 'degraded' ? 'degraded' : 'unhealthy')
    } catch {
      setter('unhealthy')
    }
  }

  await Promise.allSettled([
    healthCheck(() => miningApi.getHealth(), (v) => { miningHealth.value = v }),
    healthCheck(() => servingApi.getHealth(), (v) => { servingHealth.value = v }),
    healthCheck(() => llmApi.getHealth(), (v) => { llmHealth.value = v }),
    miningApi.getStats().then(s => { stats.value = s }),
    miningStore.fetchRuns(),
  ])
  loading.value = false
}

function statusTagType(status: string) {
  const map: Record<string, string> = {
    running: 'warning', completed: 'success', failed: 'danger', cancelled: 'info', pending: 'info',
  }
  return map[status] || 'info'
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', pending: '等待中',
  }
  return map[status] || status
}

function formatTime(t: string) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

onMounted(loadData)
watch(() => domainStore.currentDomain, loadData)
</script>

<style scoped>
.dashboard__row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.dashboard__row--stats {
  grid-template-columns: repeat(5, 1fr);
}

.dashboard__section {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px;
  box-shadow: var(--kb-shadow-card);
}

.dashboard__section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.dashboard__section-header h3 {
  font-size: 15px;
  font-weight: 600;
  color: var(--kb-text-primary);
  margin: 0;
}

.link {
  color: var(--kb-primary);
  text-decoration: none;
}
.link:hover { text-decoration: underline; }
</style>
