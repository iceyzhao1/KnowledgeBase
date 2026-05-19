<template>
  <div class="settings-view">
    <div class="settings-view__section">
      <h3>Domain 配置</h3>
      <el-table :data="domainRows" stripe size="default">
        <el-table-column prop="name" label="Domain" width="200" />
        <el-table-column prop="miningApi" label="Mining API" />
        <el-table-column prop="servingApi" label="Serving API" />
        <el-table-column prop="llmApi" label="LLM API" />
        <el-table-column prop="active" label="启用" width="80">
          <template #default="{ row }">
            <el-switch v-model="row.active" @change="handleToggle(row)" />
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="settings-view__section">
      <h3>服务地址</h3>
      <p class="settings-view__hint">当前 Domain: <strong>{{ domainStore.currentDomain }}</strong></p>
      <el-form label-width="120px">
        <el-form-item label="Mining API">
          <el-input v-model="editConfig.miningApi" />
        </el-form-item>
        <el-form-item label="Serving API">
          <el-input v-model="editConfig.servingApi" />
        </el-form-item>
        <el-form-item label="LLM API">
          <el-input v-model="editConfig.llmApi" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSave">保存</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useDomainStore } from '@/stores/domain'
import type { DomainConfig } from '@/types'

const domainStore = useDomainStore()

const domainRows = computed(() =>
  Object.entries(domainStore.domains).map(([name, cfg]) => ({ name, ...cfg }))
)

const editConfig = ref<DomainConfig>({ ...domainStore.currentConfig })

watch(() => domainStore.currentDomain, () => {
  editConfig.value = { ...domainStore.currentConfig }
})

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
</script>

<style scoped>
.settings-view__section {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px;
  box-shadow: var(--kb-shadow-card);
  margin-bottom: 20px;
}

.settings-view__section h3 {
  margin: 0 0 16px;
  font-size: 16px;
  font-weight: 600;
  color: var(--kb-text-primary);
}

.settings-view__hint {
  font-size: 13px;
  color: var(--kb-text-secondary);
  margin-bottom: 16px;
}
</style>
