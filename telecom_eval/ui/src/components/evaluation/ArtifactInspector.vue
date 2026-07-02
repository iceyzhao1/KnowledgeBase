<template>
  <div>
    <div v-if="!artifacts.length" class="empty">无评估产物</div>
    <el-collapse v-else>
      <el-collapse-item v-for="art in artifacts" :key="art.artifact_id" :name="art.artifact_id">
        <template #title>
          <el-tag size="small">{{ art.artifact_type }}</el-tag>
          <span class="art-id">{{ art.artifact_id }}</span>
          <el-tag v-if="art.cache_key" size="small" type="info" class="art-cache">缓存键：{{ short(art.cache_key) }}</el-tag>
        </template>
        <RawJsonViewer :data="art.payload" :collapsible="false" />
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import RawJsonViewer from './RawJsonViewer.vue'

defineProps<{ artifacts: Record<string, any>[] }>()

function short(key: string): string {
  return key && key.length > 16 ? key.slice(0, 16) + '…' : key
}
</script>

<style scoped>
.art-id { margin-left: 8px; font-size: 12px; color: var(--kb-text-secondary, #64748b); }
.art-cache { margin-left: 8px; }
.empty { color: var(--kb-text-tertiary, #94a3b8); padding: 12px 0; }
</style>
