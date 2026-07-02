<template>
  <div>
    <div v-if="!items.length" class="empty">无标准证据</div>
    <el-table v-else :data="items" size="small" border>
      <el-table-column label="证据 ID" min-width="160">
        <template #default="{ row }"><code>{{ row.evidence_id ?? '-' }}</code></template>
      </el-table-column>
      <el-table-column label="原文片段 ID" min-width="200">
        <template #default="{ row }">
          <code>{{ Array.isArray(row.raw_segment_ids) ? row.raw_segment_ids.join(', ') : '-' }}</code>
        </template>
      </el-table-column>
      <el-table-column label="来源 ID" min-width="140">
        <template #default="{ row }"><code>{{ row.source_id ?? '-' }}</code></template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
defineProps<{ items: Record<string, any>[] }>()
</script>

<style scoped>
.empty { color: var(--kb-text-tertiary, #94a3b8); padding: 12px 0; }
</style>
