<template>
  <div class="cmp-report">
    <div class="cmp-report__head">
      <h2>对比报告</h2>
      <router-link to="/"><el-button :icon="Back">返回</el-button></router-link>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <template v-if="report">
      <el-descriptions :column="3" border size="small" class="mb" title="对比概览">
        <el-descriptions-item label="基线运行">{{ report.baseline_run_id }}</el-descriptions-item>
        <el-descriptions-item label="候选运行">{{ report.candidate_run_id }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ report.status }}</el-descriptions-item>
        <el-descriptions-item label="胜率">{{ fmt(report.summary.win_rate) }}</el-descriptions-item>
        <el-descriptions-item label="退化率">{{ fmt(report.summary.regression_rate) }}</el-descriptions-item>
        <el-descriptions-item label="可比样本">{{ report.summary.comparable_cases ?? '-' }}</el-descriptions-item>
      </el-descriptions>

      <el-card shadow="never" class="mb">
        <template #header><span>样本级差异</span></template>
        <ComparisonDeltaTable :rows="report.delta_rows" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>退化样本（{{ report.regressed_cases.length }}）</span></template>
        <el-table :data="report.regressed_cases" size="small" border>
          <el-table-column prop="case_id" label="样本 ID" min-width="160" />
          <el-table-column prop="change_type" label="变化" width="140" />
        </el-table>
      </el-card>

      <el-row :gutter="16">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><span>新增通过样本</span></template>
            <el-tag v-for="c in report.newly_passed" :key="c" size="small" type="success" class="chip">{{ c }}</el-tag>
            <span v-if="!report.newly_passed.length">无</span>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><span>新增失败样本</span></template>
            <el-tag v-for="c in report.newly_failed" :key="c" size="small" type="danger" class="chip">{{ c }}</el-tag>
            <span v-if="!report.newly_failed.length">无</span>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <el-empty v-else-if="!loading" description="无对比数据" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Back } from '@element-plus/icons-vue'
import { useEvaluationApi } from '@/api/evaluation'
import type { ComparisonReportViewModel } from '@/types/evaluation'
import ComparisonDeltaTable from '@/components/evaluation/ComparisonDeltaTable.vue'

const props = defineProps<{ comparisonId: string }>()
const api = useEvaluationApi()

const report = ref<ComparisonReportViewModel | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

function fmt(value: unknown): string {
  if (typeof value === 'number') return value.toFixed(4)
  return value == null ? 'N/A' : String(value)
}

onMounted(async () => {
  loading.value = true
  try {
    report.value = await api.getComparisonReport(props.comparisonId)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.cmp-report__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.mb { margin-bottom: 16px; }
.chip { margin: 0 4px 4px 0; }
</style>
