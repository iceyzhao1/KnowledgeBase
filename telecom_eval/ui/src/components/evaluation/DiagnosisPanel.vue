<template>
  <div>
    <div v-if="!diagnosis" class="empty">无失败归因（该 case 未被诊断为失败）。</div>
    <el-descriptions v-else :column="2" border size="small">
      <el-descriptions-item label="失败类型">
        <el-tag type="danger" size="small">{{ diagnosis.failure_type }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="严重度">
        <el-tag :type="severityType(String(diagnosis.severity))" size="small">{{ diagnosis.severity }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="置信度">{{ diagnosis.confidence }}</el-descriptions-item>
      <el-descriptions-item label="建议修复">{{ diagnosis.suggested_action }}</el-descriptions-item>
      <el-descriptions-item label="归因依据" :span="2">
        <el-tag v-for="(e, i) in (diagnosis.evidence || [])" :key="i" size="small" class="diag-ev">{{ e }}</el-tag>
        <span v-if="!(diagnosis.evidence || []).length">-</span>
      </el-descriptions-item>
      <el-descriptions-item label="相关指标" :span="2">
        {{ (diagnosis.related_metrics || []).join(', ') || '-' }}
      </el-descriptions-item>
    </el-descriptions>
  </div>
</template>

<script setup lang="ts">
defineProps<{ diagnosis?: Record<string, any> | null }>()

function severityType(severity: string): 'info' | 'warning' | 'danger' {
  if (severity === 'critical' || severity === 'high') return 'danger'
  if (severity === 'medium') return 'warning'
  return 'info'
}
</script>

<style scoped>
.diag-ev { margin-right: 4px; }
.empty { color: var(--kb-text-tertiary, #94a3b8); padding: 12px 0; }
</style>
