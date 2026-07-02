<template>
  <el-table :data="rows" size="small" border max-height="460">
    <el-table-column prop="row_number" label="#" width="56" />
    <el-table-column label="状态" width="120">
      <template #default="{ row }">
        <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="问题" min-width="240">
      <template #default="{ row }">
        {{ (row.mapped_case && row.mapped_case.question) || row.normalized_question || '-' }}
      </template>
    </el-table-column>
    <el-table-column label="重复于" min-width="140">
      <template #default="{ row }">
        <el-tag v-if="row.duplicate_of_case_id" type="warning" size="small">{{ row.duplicate_of_case_id }}</el-tag>
        <span v-else>-</span>
      </template>
    </el-table-column>
    <el-table-column label="提示 / 错误" min-width="220">
      <template #default="{ row }">
        <div v-for="(e, i) in row.errors" :key="`e${i}`" class="msg msg--err">✗ {{ e }}</div>
        <div v-for="(w, i) in row.warnings" :key="`w${i}`" class="msg msg--warn">! {{ w }}</div>
        <span v-if="!row.errors.length && !row.warnings.length">-</span>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import type { DatasetImportPreviewRow } from '@/types/evaluation'

defineProps<{ rows: DatasetImportPreviewRow[] }>()

function statusType(status: string): 'success' | 'warning' | 'danger' {
  if (status === 'confirmable') return 'success'
  if (status === 'draft') return 'warning'
  return 'danger'
}
function statusLabel(status: string): string {
  return { confirmable: '可确认', draft: '草稿', rejected: '拒绝' }[status] || status
}
</script>

<style scoped>
.msg { font-size: 12px; line-height: 1.5; }
.msg--err { color: #dc2626; }
.msg--warn { color: #d97706; }
</style>
