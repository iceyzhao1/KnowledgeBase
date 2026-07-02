<template>
  <div class="ds-list">
    <div class="ds-list__head">
      <h2>测试集</h2>
      <div class="ds-list__actions">
        <el-button :icon="Download" @click="downloadTemplate">下载模板</el-button>
        <router-link to="/datasets/create">
          <el-button type="primary" :icon="Plus">新建测试集</el-button>
        </router-link>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <div v-loading="loading">
      <el-empty v-if="!datasets.length && !loading" description="还没有测试集，先新建或导入" />
      <el-row :gutter="16">
        <el-col v-for="d in datasets" :key="d.dataset_id" :span="8" class="mb">
          <DatasetSummaryCard :dataset="d" @delete="removeDataset" />
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Download, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useEvaluationApi } from '@/api/evaluation'
import type { EvaluationDataset } from '@/types/evaluation'
import DatasetSummaryCard from '@/components/evaluation/DatasetSummaryCard.vue'

const api = useEvaluationApi()
const datasets = ref<EvaluationDataset[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const templateHeaders = [
  'case_id',
  'question',
  'expected_answer',
  'expected_key_points',
  'expected_evidence_contains',
  'expected_evidence',
  'answerability',
  'task_type',
  'risk_level',
  'tags',
  'source_ref',
]

const templateRows = [
  {
    case_id: 'case_001',
    question: '示例：某业务感知能力适用于哪些网络场景？',
    expected_answer: '示例：该能力适用于云核心网、用户面转发和业务识别等场景。',
    expected_key_points: '云核心网;用户面转发;业务识别',
    expected_evidence_contains: '云核心网;用户面转发;业务识别',
    expected_evidence: '[{"content":"资料中明确说明该能力适用于云核心网、用户面转发和业务识别场景。","match_text":"适用于云核心网、用户面转发和业务识别"}]',
    answerability: 'answerable',
    task_type: 'retrieval_or_e2e',
    risk_level: 'medium',
    tags: '模板示例;需替换',
    source_ref: '请填写来源文档或章节',
  },
]

function csvCell(value: unknown): string {
  const text = String(value ?? '')
  return `"${text.replace(/"/g, '""')}"`
}

function downloadTemplate() {
  const lines = [
    templateHeaders.map(csvCell).join(','),
    ...templateRows.map((row) => templateHeaders.map((key) => csvCell(row[key as keyof typeof row])).join(',')),
  ]
  const blob = new Blob([`\uFEFF${lines.join('\n')}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'telecom_eval_测试集模板.csv'
  link.click()
  URL.revokeObjectURL(url)
}

async function loadDatasets() {
  loading.value = true
  try {
    datasets.value = await api.listDatasetsFull()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function removeDataset(datasetId: string) {
  try {
    await api.deleteDataset(datasetId)
    ElMessage.success('测试集已删除')
    await loadDatasets()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败')
  }
}

onMounted(loadDatasets)
</script>

<style scoped>
.ds-list__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.ds-list__actions { display: flex; gap: 10px; align-items: center; }
.mb { margin-bottom: 16px; }
</style>
