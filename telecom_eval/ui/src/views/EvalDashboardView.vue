<template>
  <div class="eval-dash">
    <div class="eval-dash__head">
      <h2>评估总览</h2>
      <router-link to="/runs/create">
        <el-button type="primary" :icon="Plus">新建评估</el-button>
      </router-link>
    </div>

    <el-alert v-if="store.error" :title="store.error" type="error" show-icon :closable="false" class="mb" />

    <el-row :gutter="16" class="mb">
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat__label">评估运行数</div>
          <div class="stat__value">{{ store.runs.length }}</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat__label">数据集数</div>
          <div class="stat__value">{{ store.datasets.length }}</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat__label">已完成运行</div>
          <div class="stat__value">{{ completedCount }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <template #header><span>最近评估运行</span></template>
      <el-table v-loading="store.loading" :data="store.runs" size="small" border>
        <el-table-column label="运行 ID" min-width="160">
          <template #default="{ row }">
            <router-link :to="`/runs/${row.run_id}`">{{ row.run_id }}</router-link>
          </template>
        </el-table-column>
        <el-table-column prop="subject_id" label="被测系统" min-width="120" />
        <el-table-column prop="dataset_id" label="数据集" min-width="120" />
        <el-table-column prop="eval_type" label="类型" width="100" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="180" />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-popconfirm
              title="确认删除这次评估运行？相关报告、指标、判分过程和调用记录都会清空。"
              confirm-button-text="删除"
              cancel-button-text="取消"
              @confirm="removeRun(row.run_id)"
            >
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useEvaluationApi } from '@/api/evaluation'
import { useEvaluationStore } from '@/stores/evaluation'

const store = useEvaluationStore()
const api = useEvaluationApi()

const completedCount = computed(() => store.runs.filter((r) => r.status === 'completed').length)

function statusType(status: string): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

async function removeRun(runId: string) {
  try {
    await api.deleteRun(runId)
    ElMessage.success('评估运行已删除')
    await store.loadRuns()
    await store.loadDatasets()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败')
  }
}

onMounted(() => {
  store.loadRuns()
  store.loadDatasets()
})
</script>

<style scoped>
.eval-dash__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.mb { margin-bottom: 16px; }
.stat__label { color: var(--kb-text-secondary, #64748b); font-size: 13px; }
.stat__value { font-size: 28px; font-weight: 700; margin-top: 6px; }
</style>
