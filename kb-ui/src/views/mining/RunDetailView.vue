<template>
  <div class="run-detail" v-loading="miningStore.loading">
    <div class="run-detail__back">
      <el-button text @click="$router.push('/mining')">
        <el-icon><ArrowLeft /></el-icon> 返回列表
      </el-button>
    </div>

    <template v-if="miningStore.currentRun">
      <!-- Run Info -->
      <div class="run-detail__info">
        <div class="run-detail__info-header">
          <h3>{{ miningStore.currentRun.id }}</h3>
          <el-tag :type="statusTagType(miningStore.currentRun.status)" effect="plain">
            {{ statusLabel(miningStore.currentRun.status) }}
          </el-tag>
        </div>
        <div class="run-detail__meta">
          <span>文档数: {{ miningStore.currentRun.document_count }}</span>
          <span>创建: {{ formatTime(miningStore.currentRun.created_at) }}</span>
          <span v-if="miningStore.currentRun.error_message" class="run-detail__error">
            错误: {{ miningStore.currentRun.error_message }}
          </span>
        </div>
      </div>

      <!-- Pipeline Timeline -->
      <div class="run-detail__section">
        <h4>Pipeline 阶段</h4>
        <el-steps :active="activeStep" align-center>
          <el-step
            v-for="stage in miningStore.stages"
            :key="stage.name"
            :title="stage.name"
            :description="stageDescription(stage)"
            :status="stepStatus(stage.status)"
          />
        </el-steps>
      </div>

      <!-- Documents Table -->
      <div class="run-detail__section">
        <h4>文档处理结果</h4>
        <el-table :data="miningStore.documents" stripe size="small">
          <el-table-column prop="filename" label="文件名" />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="docStatusType(row.status)" size="small" effect="plain">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="action" label="操作" width="100">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.action }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="error_message" label="错误" />
        </el-table>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useDomainStore } from '@/stores/domain'
import { useMiningStore } from '@/stores/mining'
import type { MiningRunStage } from '@/types'

const props = defineProps<{ runId: string }>()
const domainStore = useDomainStore()
const miningStore = useMiningStore()

let pollTimer: ReturnType<typeof setInterval> | null = null

const activeStep = computed(() => {
  const stages = miningStore.stages
  for (let i = stages.length - 1; i >= 0; i--) {
    if (stages[i].status === 'completed') return i + 1
    if (stages[i].status === 'running') return i
  }
  return 0
})

function stageDescription(stage: MiningRunStage) {
  if (stage.status === 'running' && stage.progress !== undefined) {
    return `${Math.round(stage.progress * 100)}%`
  }
  if (stage.duration_seconds !== undefined) {
    return `${stage.duration_seconds.toFixed(1)}s`
  }
  return stage.status
}

function stepStatus(status: string) {
  const map: Record<string, string> = {
    completed: 'success', running: 'process', failed: 'error', pending: 'wait', skipped: 'wait',
  }
  return map[status] || 'wait'
}

function statusTagType(status: string) {
  const map: Record<string, string> = {
    running: 'warning', completed: 'success', failed: 'danger', cancelled: 'info', pending: 'info',
  }
  return map[status] || 'info'
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', pending: '等待中',
  }
  return map[status] || status
}

function docStatusType(status: string) {
  const map: Record<string, string> = {
    completed: 'success', processing: 'warning', failed: 'danger', pending: 'info', skipped: 'info',
  }
  return map[status] || 'info'
}

function formatTime(t: string | undefined) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  miningStore.fetchRunDetail(props.runId)
  const run = miningStore.currentRun
  if (run?.status === 'running') {
    pollTimer = setInterval(() => miningStore.fetchRunDetail(props.runId), 3000)
  }
}

onMounted(startPolling)
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
watch(() => domainStore.currentDomain, () => {
  miningStore.clearCurrentRun()
  startPolling()
})
</script>

<style scoped>
.run-detail__back { margin-bottom: 16px; }

.run-detail__info {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px;
  box-shadow: var(--kb-shadow-card);
  margin-bottom: 20px;
}

.run-detail__info-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.run-detail__info-header h3 {
  margin: 0;
  font-size: 18px;
  color: var(--kb-text-primary);
}

.run-detail__meta {
  display: flex;
  gap: 20px;
  font-size: 13px;
  color: var(--kb-text-secondary);
}

.run-detail__error {
  color: var(--kb-danger);
}

.run-detail__section {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px;
  box-shadow: var(--kb-shadow-card);
  margin-bottom: 20px;
}

.run-detail__section h4 {
  margin: 0 0 16px;
  font-size: 15px;
  font-weight: 600;
  color: var(--kb-text-primary);
}
</style>
