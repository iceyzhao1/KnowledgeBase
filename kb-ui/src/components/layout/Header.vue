<template>
  <header class="header">
    <div class="header__left">
      <h2 class="header__title">{{ pageTitle }}</h2>
    </div>

    <div class="header__right">
      <el-select
        v-model="domainStore.currentDomain"
        class="header__domain-select"
        size="default"
        @change="onDomainChange"
      >
        <el-option
          v-for="name in domainStore.activeDomains"
          :key="name"
          :label="name"
          :value="name"
        />
      </el-select>

      <div class="header__health">
        <span
          class="header__health-dot"
          :class="{
            'header__health-dot--healthy': allHealthy,
            'header__health-dot--degraded': !allHealthy && someHealthy,
            'header__health-dot--unhealthy': !someHealthy,
          }"
        />
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useDomainStore } from '@/stores/domain'

const route = useRoute()
const domainStore = useDomainStore()

const pageTitles: Record<string, string> = {
  dashboard: '概览',
  mining: '挖掘管理',
  'mining-detail': 'Run 详情',
  search: '检索测试',
  knowledge: '知识资产',
  'knowledge-detail': '文档详情',
  graph: '知识图谱',
  llm: 'LLM 服务',
  settings: '系统设置',
}

const pageTitle = computed(() => pageTitles[route.name as string] || 'CoreMasterKB')

const allHealthy = ref(true)
const someHealthy = ref(true)

function onDomainChange() {
  allHealthy.value = true
  someHealthy.value = true
}
</script>

<script lang="ts">
import { ref } from 'vue'
</script>

<style scoped>
.header {
  height: var(--kb-header-height);
  background: var(--kb-bg-card);
  border-bottom: 1px solid var(--kb-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
}

.header__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--kb-text-primary);
  margin: 0;
}

.header__right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header__domain-select {
  width: 200px;
}

.header__health {
  display: flex;
  align-items: center;
}

.header__health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--kb-text-secondary);
}

.header__health-dot--healthy {
  background: var(--kb-success);
}

.header__health-dot--degraded {
  background: var(--kb-warning);
}

.header__health-dot--unhealthy {
  background: var(--kb-danger);
}
</style>
