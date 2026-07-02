<template>
  <el-table :data="rows" size="small" border>
    <el-table-column label="指标" min-width="260">
      <template #default="{ row }">
        <div class="metric-name">
          <strong>{{ explainMetric(row.metric_id, row.value).label }}</strong>
          <span>{{ row.metric_id }}</span>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="测试种类" width="160">
      <template #default="{ row }">{{ explainMetric(row.metric_id, row.value).testType }}</template>
    </el-table-column>
    <el-table-column label="分数" width="140">
      <template #default="{ row }">{{ displayMetricValue(row.metric_id, row.value) }}</template>
    </el-table-column>
    <el-table-column label="判断" width="130">
      <template #default="{ row }">
        <el-tag :type="tagType(metricTone(row.metric_id, row.value))" size="small">
          {{ metricVerdict(row.metric_id, row.value) }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column label="技术状态" width="120">
      <template #default="{ row }">
        <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column v-if="showDetailAction" label="详情" width="110" fixed="right">
      <template #default="{ row }">
        <el-button link type="primary" size="small" @click="$emit('detail', row.metric_id)">
          查看详情
        </el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import type { MetricRow } from '@/types/evaluation'
import {
  displayMetricValue,
  explainMetric,
  metricTone,
  metricVerdict,
} from '@/utils/metricCatalog.mjs'

withDefaults(defineProps<{ rows: MetricRow[]; showDetailAction?: boolean }>(), {
  showDetailAction: false,
})
defineEmits<{ detail: [metricId: string] }>()

function tagType(tone: string): 'success' | 'warning' | 'info' | 'danger' {
  if (tone === 'success') return 'success'
  if (tone === 'warning') return 'warning'
  if (tone === 'danger') return 'danger'
  return 'info'
}

function statusType(status: string): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'ok') return 'success'
  if (status === 'error') return 'danger'
  if (status === 'inconclusive' || status === 'missing_inputs') return 'warning'
  return 'info'
}
</script>

<style scoped>
.metric-name {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.metric-name strong {
  color: #0f172a;
  font-size: 13px;
}

.metric-name span {
  color: var(--kb-text-tertiary, #94a3b8);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}
</style>
