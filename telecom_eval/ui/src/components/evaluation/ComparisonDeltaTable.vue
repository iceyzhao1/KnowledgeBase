<template>
  <el-table :data="rows" size="small" border>
    <el-table-column prop="case_id" label="样本 ID" min-width="160" />
    <el-table-column label="变更前" width="110">
      <template #default="{ row }">{{ fmt(row.before) }}</template>
    </el-table-column>
    <el-table-column label="变更后" width="110">
      <template #default="{ row }">{{ fmt(row.after) }}</template>
    </el-table-column>
    <el-table-column label="差值" width="110">
      <template #default="{ row }">{{ fmt(row.absolute_delta) }}</template>
    </el-table-column>
    <el-table-column label="变化" width="140">
      <template #default="{ row }">
        <el-tag :type="changeType(row.change_type)" size="small">{{ changeLabel(row.change_type) }}</el-tag>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import type { ComparisonDeltaRow } from '@/types/evaluation'

defineProps<{ rows: ComparisonDeltaRow[] }>()

function fmt(value: unknown): string {
  if (value === null || value === undefined) return 'N/A'
  if (typeof value === 'number') return value.toFixed(4)
  return String(value)
}

function changeType(change: string): 'success' | 'danger' | 'info' | 'warning' {
  if (change === 'improved' || change === 'newly_passed') return 'success'
  if (change === 'regressed' || change === 'newly_failed') return 'danger'
  if (change === 'unchanged') return 'info'
  return 'warning'
}

function changeLabel(change: string): string {
  const labels: Record<string, string> = {
    improved: '提升',
    regressed: '退化',
    newly_passed: '新增通过',
    newly_failed: '新增失败',
    unchanged: '无变化',
  }
  return labels[change] ?? change
}
</script>
