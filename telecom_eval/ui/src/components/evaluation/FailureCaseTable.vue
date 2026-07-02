<template>
  <el-table :data="failures" size="small" border>
    <el-table-column prop="case_id" label="样本 ID" min-width="160" />
    <el-table-column label="失败类型" min-width="180">
      <template #default="{ row }">
        <el-tag v-if="row.failure_type" type="danger" size="small">{{ row.failure_type }}</el-tag>
        <span v-else>-</span>
      </template>
    </el-table-column>
    <el-table-column label="严重度" width="120">
      <template #default="{ row }">
        <el-tag v-if="row.severity" :type="severityType(row.severity)" size="small">{{ severityLabel(row.severity) }}</el-tag>
        <span v-else>-</span>
      </template>
    </el-table-column>
    <el-table-column label="操作" width="180">
      <template #default="{ row }">
        <router-link :to="`/runs/${runId}/cases/${row.case_id}`">
          <el-button link type="primary" size="small">查看判分过程/详情</el-button>
        </router-link>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import type { FailureRow } from '@/types/evaluation'

defineProps<{ failures: FailureRow[]; runId: string }>()

function severityType(severity: string): 'info' | 'warning' | 'danger' {
  if (severity === 'critical' || severity === 'high') return 'danger'
  if (severity === 'medium') return 'warning'
  return 'info'
}

function severityLabel(severity: string): string {
  const labels: Record<string, string> = {
    low: '低',
    medium: '中',
    high: '高',
    critical: '严重',
  }
  return labels[severity] ?? severity
}
</script>
