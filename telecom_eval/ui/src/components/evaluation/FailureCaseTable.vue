<template>
  <el-table :data="failures" size="small" border>
    <el-table-column prop="case_id" label="样本 ID" min-width="160" />
    <el-table-column label="问题层级" width="140">
      <template #default="{ row }">
        <el-tag :type="layerType(row.failure_type)" size="small">{{ layerLabel(row.failure_type) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="诊断类型" min-width="180">
      <template #default="{ row }">
        <el-tooltip
          v-if="row.failure_type"
          :content="diagnosisDescription(row.failure_type)"
          placement="top"
        >
          <el-tag type="danger" size="small">{{ diagnosisLabel(row.failure_type) }}</el-tag>
        </el-tooltip>
        <span v-else>-</span>
      </template>
    </el-table-column>
    <el-table-column label="说明" min-width="260">
      <template #default="{ row }">
        {{ diagnosisDescription(row.failure_type) }}
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

function layerLabel(failureType: string | null): string {
  if (!failureType) return '未分类'
  if (failureType === 'evaluation_material_issue') return '评估材料层'
  if (failureType === 'retrieval_call_error') return '检索调用层'
  if (failureType.startsWith('retrieval_')) return '检索质量层'
  if (failureType === 'comparison_regression') return '版本对比层'
  if (failureType === 'unsupported_claim' || failureType === 'refusal_error') return '端到端回答层'
  return '综合诊断层'
}

function layerType(failureType: string | null): 'info' | 'warning' | 'danger' | 'success' {
  if (!failureType) return 'info'
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

function diagnosisDescription(failureType: string | null): string {
  const descriptions: Record<string, string> = {
    evaluation_material_issue: '测试样本、标准答案或标准证据本身不完整，导致判分依据不稳定。',
    retrieval_call_error: '检索接口调用失败，优先检查服务地址、网络、鉴权或后端异常。',
    retrieval_miss: 'Top K 内没有找到能支持标准答案的证据，属于召回问题。',
    retrieval_ranking_issue: 'Top K 内找到了相关证据，但位置靠后，属于排序或重排问题。',
    unsupported_claim: '答案中有内容没有被证据支撑，属于生成 grounding 问题。',
    refusal_error: '该答或不该答的判断不符合测试样本预期。',
    comparison_regression: '与基线运行相比，这个样本出现了新增失败或分数退化。',
    ambiguous: '现有指标和产物不足以稳定归因，需要查看详情人工复核。',
  }
  return failureType ? descriptions[failureType] ?? failureType : '-'
}

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
