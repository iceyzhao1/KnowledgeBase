<template>
  <div class="search-view">
    <div class="search-view__input">
      <el-input
        v-model="query"
        placeholder="输入检索查询..."
        size="large"
        clearable
        @keyup.enter="handleSearch"
      >
        <template #append>
          <el-button @click="handleSearch" :loading="searching" type="primary">检索</el-button>
        </template>
      </el-input>
    </div>

    <div v-if="result" class="search-view__results">
      <div class="search-view__summary">
        找到 {{ result.total_items }} 条结果, {{ result.total_relations }} 条关系,
        耗时 {{ result.elapsed_ms }}ms
      </div>

      <div class="search-view__items">
        <h4>Seed Items</h4>
        <div v-for="(item, idx) in result.items" :key="item.id" class="search-view__item">
          <div class="search-view__item-header">
            <span class="search-view__item-idx">{{ idx + 1 }}</span>
            <el-tag size="small" effect="plain">{{ item.item_type }}</el-tag>
            <span class="search-view__item-score">{{ item.score.toFixed(2) }}</span>
          </div>
          <div class="search-view__item-content">{{ item.content }}</div>
          <div class="search-view__item-source">来源: {{ item.source }}</div>
        </div>
      </div>

      <div v-if="result.relations.length" class="search-view__relations">
        <h4>关系</h4>
        <div v-for="rel in result.relations" :key="`${rel.from_entity}-${rel.to_entity}`" class="search-view__rel">
          <el-tag size="small">{{ rel.from_entity }}</el-tag>
          <span class="search-view__rel-arrow">{{ rel.relation_type }}</span>
          <el-tag size="small">{{ rel.to_entity }}</el-tag>
        </div>
      </div>

      <div v-if="result.debug" class="search-view__debug">
        <el-collapse>
          <el-collapse-item title="Debug 信息">
            <pre class="search-view__debug-content">{{ JSON.stringify(result.debug, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useDomainStore } from '@/stores/domain'
import { useServingApi } from '@/api/serving'
import type { SearchResult } from '@/types'

const domainStore = useDomainStore()
const servingApi = useServingApi()

const query = ref('')
const searching = ref(false)
const result = ref<SearchResult | null>(null)

async function handleSearch() {
  if (!query.value.trim()) return
  searching.value = true
  try {
    result.value = await servingApi.search(query.value, {
      domain: domainStore.currentDomain,
      debug: true,
    })
  } catch (e) {
    console.error('Search failed:', e)
  } finally {
    searching.value = false
  }
}
</script>

<style scoped>
.search-view__input { margin-bottom: 20px; }

.search-view__summary {
  font-size: 13px;
  color: var(--kb-text-secondary);
  margin-bottom: 16px;
}

.search-view__items,
.search-view__relations {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 16px 20px;
  box-shadow: var(--kb-shadow-card);
  margin-bottom: 16px;
}

.search-view__items h4,
.search-view__relations h4 {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: var(--kb-text-primary);
}

.search-view__item {
  padding: 12px 0;
  border-bottom: 1px solid var(--kb-border-light);
}

.search-view__item:last-child { border-bottom: none; }

.search-view__item-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.search-view__item-idx {
  font-weight: 600;
  color: var(--kb-primary);
  min-width: 20px;
}

.search-view__item-score {
  font-size: 12px;
  color: var(--kb-text-secondary);
  font-variant-numeric: tabular-nums;
}

.search-view__item-content {
  font-size: 13px;
  color: var(--kb-text-primary);
  line-height: 1.5;
}

.search-view__item-source {
  font-size: 12px;
  color: var(--kb-text-secondary);
  margin-top: 4px;
}

.search-view__rel {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.search-view__rel-arrow {
  font-size: 12px;
  color: var(--kb-text-secondary);
}

.search-view__debug {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  box-shadow: var(--kb-shadow-card);
}

.search-view__debug-content {
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
}
</style>
