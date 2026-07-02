<template>
  <div>
    <div v-if="!diagnosis" class="empty">无质量诊断问题（该样本未被标记为需关注）。</div>
    <el-descriptions v-else :column="2" border size="small">
      <el-descriptions-item label="问题层级">
        <el-tag :type="layerType(String(diagnosis.failure_type || ''))" size="small">
          {{ layerLabel(String(diagnosis.failure_type || '')) }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="诊断类型">
        <el-tag type="danger" size="small">{{ diagnosisLabel(String(diagnosis.failure_type || '')) }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="严重度">
        <el-tag :type="severityType(String(diagnosis.severity))" size="small">{{ severityLabel(String(diagnosis.severity)) }}</el-tag>
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

function severityLabel(severity: string): string {
  const labels: Record<string, string> = {
    low: '低',
    medium: '中',
    high: '高',
    critical: '严重',
  }
  return labels[severity] ?? severity
}

function layerLabel(failureType: string): string {
  if (failureType === 'evaluation_material_issue') return '评估材料层'
  if (failureType === 'retrieval_call_error') return '检索调用层'
  if (failureType.startsWith('retrieval_')) return '检索质量层'
  if (failureType === 'comparison_regression') return '版本对比层'
  if (failureType === 'unsupported_claim' || failureType === 'refusal_error') return '端到端回答层'
  return '综合诊断层'
}

function layerType(failureType: string): 'info' | 'warning' | 'danger' | 'success' {
  if (failureType === 'retrieval_call_error' || failureType === 'evaluation_material_issue') return 'danger'
  if (failureType === 'ambiguous') return 'info'
  return 'warning'
}

function diagnosisLabel(failureType: string): string {
  const labels: Record<string, string> = {
    evaluation_material_issue: '评估材料异常',
    retrieval_call_error: '检索调用异常',
    retrieval_miss: '检索未命中',
    retrieval_ranking_issue: '检索排序靠后',
    unsupported_claim: '回答缺少证据支撑',
    refusal_error: '拒答判断异常',
    comparison_regression: '版本对比退化',
    ambiguous: '需人工复核',
  }
  return labels[failureType] ?? failureType
}
</script>

<style scoped>
.diag-ev { margin-right: 4px; }
.empty { color: var(--kb-text-tertiary, #94a3b8); padding: 12px 0; }
</style>
