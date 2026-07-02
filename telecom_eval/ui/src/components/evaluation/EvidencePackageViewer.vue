<template>
  <div>
    <div v-if="!items.length" class="empty">无证据项</div>
    <el-card v-for="(item, idx) in items" :key="idx" shadow="never" class="ev-card">
      <div class="ev-head">
        <el-tag size="small">排名 {{ item.rank }}</el-tag>
        <el-tag v-if="item.score != null" size="small" type="info">分数 {{ formatScore(item.score) }}</el-tag>
        <span class="ev-id">证据 ID：<code>{{ item.evidence_id }}</code></span>
      </div>
      <div class="ev-meta">
        <div>观测项 ID：<code>{{ item.observed_item_id ?? '-' }}</code></div>
        <div>来源：<code>{{ item.source_id }}</code>（{{ item.source_type }}）</div>
        <div class="ev-keys">
          匹配键：
          <el-tag v-for="key in (item.match_keys || [])" :key="key" size="small" class="ev-key">{{ key }}</el-tag>
        </div>
      </div>
      <div class="ev-content">{{ item.content }}</div>
      <el-collapse>
        <el-collapse-item title="来源信息 / 元数据">
          <RawJsonViewer :data="{ provenance: item.provenance, metadata: item.metadata }" :collapsible="false" />
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import RawJsonViewer from './RawJsonViewer.vue'

defineProps<{ items: Record<string, any>[] }>()

function formatScore(score: number): string {
  return typeof score === 'number' ? score.toFixed(4) : String(score)
}
</script>

<style scoped>
.ev-card { margin-bottom: 12px; }
.ev-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ev-id { font-size: 12px; color: var(--kb-text-secondary, #64748b); }
.ev-meta { font-size: 12px; color: var(--kb-text-secondary, #64748b); display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; }
.ev-keys { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.ev-key { font-family: monospace; }
.ev-content { font-size: 13px; line-height: 1.6; white-space: pre-wrap; margin-bottom: 8px; }
.empty { color: var(--kb-text-tertiary, #94a3b8); padding: 12px 0; }
</style>
