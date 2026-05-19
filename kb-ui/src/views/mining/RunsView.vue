<template>
  <div class="runs-view">
    <div class="runs-view__header">
      <div class="runs-view__actions">
        <el-button type="primary" @click="showCreateDialog = true">新建 Run</el-button>
        <el-button @click="miningStore.fetchRuns()" :loading="miningStore.loading">刷新</el-button>
      </div>
    </div>

    <div class="runs-view__table">
      <el-table :data="miningStore.runs" stripe v-loading="miningStore.loading" size="default">
        <el-table-column prop="id" label="Run ID" width="140">
          <template #default="{ row }">
            <router-link :to="`/mining/${row.id}`" class="link">{{ row.id }}</router-link>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="plain">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="document_count" label="文档数" width="100" />
        <el-table-column label="耗时" width="120">
          <template #default="{ row }">
            {{ formatDuration(row.started_at, row.finished_at) }}
          </template>
        </el-table-column>
        <el-table-column prop="build_id" label="Build" width="120">
          <template #default="{ row }">
            {{ row.build_id || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'running'"
              type="warning"
              size="small"
              text
              @click="miningStore.cancelRun(row.id)"
            >取消</el-button>
            <el-button
              v-if="row.status === 'completed' && !row.build_id"
              type="success"
              size="small"
              text
              @click="miningStore.publishRun(row.id)"
            >发布</el-button>
            <el-button
              v-if="row.status === 'failed'"
              type="primary"
              size="small"
              text
            >重试</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Create Run Dialog -->
    <el-dialog v-model="showCreateDialog" title="新建 Mining Run" width="500px">
      <el-form :model="createForm" label-width="100px">
        <el-form-item label="Domain">
          <el-input v-model="createForm.domain" :value="domainStore.currentDomain" disabled />
        </el-form-item>
        <el-form-item label="配置">
          <el-input
            v-model="createForm.configJson"
            type="textarea"
            :rows="6"
            placeholder='{"document_paths": [...], "pipeline": "full"}'
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="handleCreate" :loading="creating">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useDomainStore } from '@/stores/domain'
import { useMiningStore } from '@/stores/mining'

const domainStore = useDomainStore()
const miningStore = useMiningStore()

const showCreateDialog = ref(false)
const creating = ref(false)
const createForm = ref({
  domain: domainStore.currentDomain,
  configJson: '{}',
})

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

function formatTime(t: string) {
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

async function handleCreate() {
  creating.value = true
  try {
    let config = {}
    try { config = JSON.parse(createForm.value.configJson) } catch { /* use empty */ }
    await miningStore.createRun({ domain: domainStore.currentDomain, ...config })
    showCreateDialog.value = false
    createForm.value.configJson = '{}'
  } finally {
    creating.value = false
  }
}

onMounted(() => miningStore.fetchRuns())
watch(() => domainStore.currentDomain, () => miningStore.fetchRuns())
</script>

<style scoped>
.runs-view__header {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 16px;
}

.link {
  color: var(--kb-primary);
  text-decoration: none;
}
.link:hover { text-decoration: underline; }
</style>
