<template>
  <div class="settings-view">
    <!-- Header -->
    <div class="settings-view__header">
      <h2 class="settings-view__title">系统设置</h2>
    </div>

    <!-- Service Health -->
    <div class="settings-view__section">
      <h3 class="section-heading">服务状态</h3>
      <div class="settings-view__health-grid">
        <div class="health-tile" v-for="svc in serviceList" :key="svc.name">
          <div class="health-tile__top" :class="`health-tile__top--${svc.status}`" />
          <div class="health-tile__body">
            <span class="health-tile__name">{{ svc.name }}</span>
            <span class="health-tile__url text-mono">{{ svc.url }}</span>
          </div>
          <div class="health-tile__status">
            <span class="health-dot" :class="`health-dot--${svc.status}`" />
            <span class="health-label">{{ healthLabel(svc.status) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Domain Config -->
    <div class="settings-view__section">
      <h3 class="section-heading">Domain 配置</h3>
      <div class="settings-view__table-wrap">
        <el-table :data="domainRows" class="kb-table" :header-cell-style="{ background: 'transparent' }">
          <el-table-column prop="name" label="Domain" width="180">
            <template #default="{ row }">
              <span class="domain-name" :class="{ 'domain-name--active': row.name === domainStore.currentDomain }">
                {{ row.name }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="miningApi" label="挖掘服务" min-width="200">
            <template #default="{ row }">
              <span class="text-mono">{{ row.miningApi }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="servingApi" label="检索服务" min-width="200">
            <template #default="{ row }">
              <span class="text-mono">{{ row.servingApi }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="llmApi" label="LLM服务" min-width="200">
            <template #default="{ row }">
              <span class="text-mono">{{ row.llmApi }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="active" label="启用" width="80" align="center">
            <template #default="{ row }">
              <el-switch v-model="row.active" @change="handleToggle(row)" />
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- Edit Current Domain -->
    <div class="settings-view__section">
      <h3 class="section-heading">编辑当前 Domain</h3>
      <p class="settings-view__hint">当前: <strong>{{ domainStore.currentDomain }}</strong></p>
      <el-form label-width="120px" class="settings-view__form">
        <el-form-item label="挖掘服务">
          <el-input v-model="editConfig.miningApi" />
        </el-form-item>
        <el-form-item label="检索服务">
          <el-input v-model="editConfig.servingApi" />
        </el-form-item>
        <el-form-item label="LLM服务">
          <el-input v-model="editConfig.llmApi" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSave">保存配置</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useDomainStore } from '@/stores/domain'
import { useMiningApi } from '@/api/mining'
import { useServingApi } from '@/api/serving'
import { useLlmApi } from '@/api/llm'
import type { DomainConfig } from '@/types'

const domainStore = useDomainStore()

const domainRows = computed(() =>
  Object.entries(domainStore.domains).map(([name, cfg]) => ({ name, ...cfg }))
)

const editConfig = ref<DomainConfig>({ ...domainStore.currentConfig })

watch(() => domainStore.currentDomain, () => {
  editConfig.value = { ...domainStore.currentConfig }
})

// Service health
type ServiceStatus = 'up' | 'down' | 'checking'

const services = ref([
  { name: '挖掘服务', key: 'miningApi', status: 'checking' as ServiceStatus },
  { name: '检索服务', key: 'servingApi', status: 'checking' as ServiceStatus },
  { name: 'LLM服务', key: 'llmApi', status: 'checking' as ServiceStatus },
])

const serviceList = computed(() =>
  services.value.map(s => ({
    ...s,
    url: domainStore.currentConfig[s.key as keyof DomainConfig] as string,
  }))
)

function healthLabel(s: ServiceStatus) {
  if (s === 'up') return '正常'
  if (s === 'down') return '不可用'
  return '检测中...'
}

async function checkHealth() {
  const miningApi = useMiningApi()
  const servingApi = useServingApi()
  const llmApi = useLlmApi()

  const checks = [
    miningApi.getHealth().then(() => 'up').catch(() => 'down'),
    servingApi.getHealth().then(() => 'up').catch(() => 'down'),
    llmApi.getHealth().then(() => 'up').catch(() => 'down'),
  ]

  const results = await Promise.all(checks)
  services.value[0].status = results[0] as ServiceStatus
  services.value[1].status = results[1] as ServiceStatus
  services.value[2].status = results[2] as ServiceStatus
}

function handleToggle(row: { name: string; active: boolean } & DomainConfig) {
  domainStore.updateDomain(row.name, {
    miningApi: row.miningApi,
    servingApi: row.servingApi,
    llmApi: row.llmApi,
    active: row.active,
  })
}

function handleSave() {
  domainStore.updateDomain(domainStore.currentDomain, { ...editConfig.value })
}

onMounted(checkHealth)
watch(() => domainStore.currentDomain, checkHealth)
</script>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.settings-view__header {
  display: flex;
  align-items: center;
}

.settings-view__title {
  font-size: 16px;
  font-weight: 650;
  color: var(--kb-text-primary);
  margin: 0;
  letter-spacing: -0.2px;
}

.settings-view__section {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
}

.section-heading {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 16px;
}

/* Health tiles */
.settings-view__health-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.health-tile {
  background: var(--kb-bg-base);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  overflow: hidden;
}

.health-tile__top {
  height: 3px;
}

.health-tile__top--up { background: var(--kb-success); }
.health-tile__top--down { background: var(--kb-danger); }
.health-tile__top--checking { background: var(--kb-warning); }

.health-tile__body {
  padding: 12px 14px 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.health-tile__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--kb-text-primary);
}

.health-tile__url {
  font-size: 11px;
  color: var(--kb-text-tertiary);
}

.health-tile__status {
  padding: 6px 14px 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.health-dot--up { background: var(--kb-success); }
.health-dot--down { background: var(--kb-danger); }
.health-dot--checking { background: var(--kb-warning); animation: pulse 1.5s infinite; }

.health-label {
  font-size: 12px;
  color: var(--kb-text-secondary);
}

/* Domain table */
.settings-view__table-wrap {
  overflow: hidden;
}

.domain-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--kb-text-primary);
}

.domain-name--active {
  color: var(--kb-accent);
  font-weight: 600;
}

.text-mono {
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  font-size: 12px;
  color: var(--kb-text-secondary);
}

/* Form */
.settings-view__hint {
  font-size: 13px;
  color: var(--kb-text-secondary);
  margin: 0 0 16px;
}

.settings-view__hint strong {
  color: var(--kb-accent);
}

.settings-view__form {
  max-width: 520px;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
