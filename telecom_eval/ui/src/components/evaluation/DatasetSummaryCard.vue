<template>
  <el-card shadow="never" class="ds-card">
    <div class="ds-card__head">
      <router-link :to="`/datasets/${dataset.dataset_id}`" class="ds-card__name">{{ dataset.name }}</router-link>
      <div class="ds-card__actions">
        <el-tag :type="statusType(dataset.status)" size="small">{{ dataset.status }}</el-tag>
        <el-popconfirm
          title="确认删除这个测试集？测试集下的样本、导入记录、快照和评估运行都会清空。"
          confirm-button-text="删除"
          cancel-button-text="取消"
          @confirm="$emit('delete', dataset.dataset_id)"
        >
          <template #reference>
            <el-button link type="danger" size="small">删除</el-button>
          </template>
        </el-popconfirm>
      </div>
    </div>
    <div class="ds-card__meta">
      <span>类型 {{ dataset.dataset_type }}</span>
      <span>场景 {{ dataset.scenario_id || '-' }}</span>
    </div>
    <div class="ds-card__counts">
      <div><strong>{{ dataset.case_count }}</strong><small>样本</small></div>
      <div><strong>{{ dataset.confirmed_case_count }}</strong><small>已确认</small></div>
    </div>
    <div class="ds-card__tags">
      <el-tag v-for="t in dataset.tags" :key="t" size="small" effect="plain" class="chip">{{ t }}</el-tag>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import type { EvaluationDataset } from '@/types/evaluation'

defineProps<{ dataset: EvaluationDataset }>()
defineEmits<{ delete: [datasetId: string] }>()

function statusType(status: string): 'success' | 'info' | 'warning' {
  if (status === 'active') return 'success'
  if (status === 'archived') return 'info'
  return 'warning'
}
</script>

<style scoped>
.ds-card { height: 100%; }
.ds-card__head { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 8px; }
.ds-card__name { font-weight: 600; font-size: 15px; }
.ds-card__actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.ds-card__meta { display: flex; gap: 14px; color: var(--kb-text-secondary, #64748b); font-size: 12px; margin-bottom: 10px; }
.ds-card__counts { display: flex; gap: 24px; margin-bottom: 10px; }
.ds-card__counts strong { font-size: 20px; margin-right: 4px; }
.ds-card__counts small { color: var(--kb-text-secondary, #64748b); }
.ds-card__tags { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { margin: 0; }
</style>
