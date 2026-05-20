<template>
  <div class="run-detail" v-loading="miningStore.loading">
    <div class="run-detail__back">
      <el-button text @click="$router.push('/mining')">
        <el-icon><ArrowLeft /></el-icon> 返回列表
      </el-button>
    </div>

    <template v-if="miningStore.currentRun">
      <!-- Run Meta Card -->
      <div class="run-detail__meta-card">
        <div class="run-detail__meta-left">
          <h3 class="run-detail__id">{{ miningStore.currentRun.id.slice(0, 8) }}</h3>
          <StatusBadge :status="miningStore.currentRun.status">{{ statusLabel(miningStore.currentRun.status) }}</StatusBadge>
        </div>
        <div class="run-detail__metrics">
          <div class="metric">
            <span class="metric__value">{{ miningStore.currentRun.total_documents }}</span>
            <span class="metric__label">文档</span>
          </div>
          <div class="metric" v-if="miningStore.currentRun.new_count">
            <span class="metric__value metric__value--success">{{ miningStore.currentRun.new_count }}</span>
            <span class="metric__label">新增</span>
          </div>
          <div class="metric" v-if="miningStore.currentRun.updated_count">
            <span class="metric__value metric__value--accent">{{ miningStore.currentRun.updated_count }}</span>
            <span class="metric__label">更新</span>
          </div>
          <div class="metric" v-if="miningStore.currentRun.failed_count">
            <span class="metric__value metric__value--danger">{{ miningStore.currentRun.failed_count }}</span>
            <span class="metric__label">失败</span>
          </div>
          <div class="metric">
            <span class="metric__value">{{ formatDuration(miningStore.currentRun.started_at, miningStore.currentRun.finished_at) }}</span>
            <span class="metric__label">耗时</span>
          </div>
          <div class="metric">
            <span class="metric__value">{{ formatTime(miningStore.currentRun.created_at) }}</span>
            <span class="metric__label">创建时间</span>
          </div>
        </div>
      </div>

      <!-- Error Banner -->
      <div v-if="miningStore.currentRun.error_message" class="run-detail__error-banner">
        {{ miningStore.currentRun.error_message }}
      </div>

      <!-- Pipeline Flow -->
      <div class="run-detail__section">
        <h4 class="section-label">Pipeline 阶段</h4>
        <PipelineFlow :stage-events="miningStore.stages" />
      </div>

      <!-- Documents Table -->
      <div class="run-detail__section">
        <h4 class="section-label">文档处理结果 ({{ miningStore.documents.length }})</h4>
        <el-table
          :data="miningStore.documents"
          class="kb-table"
          :header-cell-style="{ background: 'transparent' }"
        >
          <el-table-column label="文件名" min-width="200">
            <template #default="{ row }">
              <span class="doc-name">{{ row.document_name || row.document_id.slice(0, 8) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="处理状态" width="120">
            <template #default="{ row }">
              <StatusBadge :status="row.status" size="small">{{ docStatusLabel(row.status) }}</StatusBadge>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <span class="action-badge" :class="`action-badge--${row.action}`">{{ actionLabel(row.action) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="错误信息" min-width="200">
            <template #default="{ row }">
              <span class="text-error">{{ row.error_message || '-' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useDomainStore } from '@/stores/domain'
import { useMiningStore } from '@/stores/mining'
import StatusBadge from '@/components/common/StatusBadge.vue'
import PipelineFlow from '@/components/mining/PipelineFlow.vue'

const props = defineProps<{ runId: string }>()
const domainStore = useDomainStore()
const miningStore = useMiningStore()

let pollTimer: ReturnType<typeof setInterval> | null = null

function statusLabel(status: string) {
  const map: Record<string, string> = {
    running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', pending: '等待中',
  }
  return map[status] || status
}

function docStatusLabel(status: string) {
  const map: Record<string, string> = {
    completed: '完成', processing: '处理中', failed: '失败', pending: '等待', skipped: '跳过',
  }
  return map[status] || status
}

function actionLabel(action: string) {
  const map: Record<string, string> = { new: '新增', updated: '更新', unchanged: '无变化' }
  return map[action] || action
}

function formatTime(t: string | undefined) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

function formatDuration(start?: string, end?: string) {
  if (!start) return '-'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  const diff = Math.round((e - s) / 1000)
  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  miningStore.fetchRunDetail(props.runId).then(() => {
    if (miningStore.currentRun?.status === 'running') {
      pollTimer = setInterval(() => miningStore.fetchRunDetail(props.runId), 3000)
    }
  })
}

onMounted(startPolling)
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
watch(() => domainStore.currentDomain, () => {
  miningStore.clearCurrentRun()
  startPolling()
})
</script>

<style scoped>
.run-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.run-detail__back { margin-bottom: 0; }

/* Meta card */
.run-detail__meta-card {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
  border: 1px solid var(--kb-border-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
}

.run-detail__meta-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.run-detail__id {
  font-size: 18px;
  font-weight: 700;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  color: var(--kb-text-primary);
  margin: 0;
  letter-spacing: -0.5px;
}

.run-detail__metrics {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}

.metric {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.metric__value {
  font-size: 16px;
  font-weight: 700;
  color: var(--kb-text-primary);
  font-variant-numeric: tabular-nums;
}

.metric__value--success { color: var(--kb-success); }
.metric__value--accent { color: var(--kb-accent); }
.metric__value--danger { color: var(--kb-danger); }

.metric__label {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* Error banner */
.run-detail__error-banner {
  background: var(--kb-danger-soft);
  color: var(--kb-danger);
  padding: 12px 18px;
  border-radius: var(--kb-radius-sm);
  font-size: 13px;
  border-left: 3px solid var(--kb-danger);
}

/* Section */
.run-detail__section {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
  border: 1px solid var(--kb-border-light);
}

.section-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 16px;
}

/* Doc name */
.doc-name {
  font-size: 13px;
  color: var(--kb-text-primary);
}

/* Action badges */
.action-badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.action-badge--new {
  background: var(--kb-success-soft);
  color: var(--kb-success);
}

.action-badge--updated {
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
}

.action-badge--unchanged {
  background: var(--kb-border-light);
  color: var(--kb-text-tertiary);
}

.text-error {
  font-size: 12px;
  color: var(--kb-danger);
}
</style>
