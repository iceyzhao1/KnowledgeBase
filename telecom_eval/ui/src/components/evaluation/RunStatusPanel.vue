<template>
  <el-descriptions :title="title" :column="3" border size="small">
    <el-descriptions-item label="运行 ID">{{ run.run_id }}</el-descriptions-item>
    <el-descriptions-item label="被测系统">{{ run.subject_id }}</el-descriptions-item>
    <el-descriptions-item label="数据集">{{ run.dataset_id }}</el-descriptions-item>
    <el-descriptions-item label="评估类型">{{ run.eval_type }}</el-descriptions-item>
    <el-descriptions-item label="状态">
      <el-tag :type="statusType(String(run.status))" size="small">{{ run.status }}</el-tag>
    </el-descriptions-item>
    <el-descriptions-item label="大模型判分">
      <el-tag :type="run.allow_llm_judge ? 'warning' : 'info'" size="small">
        {{ run.allow_llm_judge ? '已开启' : '关闭' }}
      </el-tag>
    </el-descriptions-item>
    <el-descriptions-item label="创建时间">{{ run.created_at || '-' }}</el-descriptions-item>
    <el-descriptions-item label="完成时间">{{ run.completed_at || '-' }}</el-descriptions-item>
  </el-descriptions>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{ run: Record<string, any>; title?: string }>(),
  { title: '运行概览' },
)

function statusType(status: string): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}
</script>
