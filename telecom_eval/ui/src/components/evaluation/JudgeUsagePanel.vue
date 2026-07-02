<template>
  <div>
    <el-descriptions :column="4" border size="small" title="大模型判分用量">
      <el-descriptions-item label="总调用">{{ usage.total_invocations ?? 0 }}</el-descriptions-item>
      <el-descriptions-item label="成功调用">{{ usage.ok_calls ?? 0 }}</el-descriptions-item>
      <el-descriptions-item label="预算跳过">{{ usage.skipped_calls ?? 0 }}</el-descriptions-item>
      <el-descriptions-item label="令牌数合计">{{ usage.total_tokens ?? 0 }}</el-descriptions-item>
      <el-descriptions-item label="参与大模型判分样本数">{{ usage.cases_with_llm ?? 0 }}</el-descriptions-item>
    </el-descriptions>

    <el-table v-if="invocations && invocations.length" :data="invocations" size="small" border style="margin-top: 12px">
      <el-table-column prop="case_id" label="样本 ID" min-width="140" />
      <el-table-column prop="judge_provider" label="模型提供方" width="120" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="row.status === 'ok' ? 'success' : (row.status === 'skipped' ? 'warning' : 'danger')" size="small">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="prompt_tokens" label="提示词令牌数" width="120" />
      <el-table-column prop="completion_tokens" label="生成令牌数" width="110" />
      <el-table-column prop="total_tokens" label="总令牌数" width="90" />
      <el-table-column prop="error" label="跳过/错误原因" min-width="160" />
    </el-table>
  </div>
</template>

<script setup lang="ts">
import type { JudgeUsage } from '@/types/evaluation'

withDefaults(
  defineProps<{ usage: Partial<JudgeUsage>; invocations?: Record<string, any>[] }>(),
  { invocations: () => [] },
)
</script>
