<template>
  <div class="create-run">
    <!-- Header -->
    <div class="create-run__header">
      <el-button text @click="$router.push('/mining')">
        <el-icon><ArrowLeft /></el-icon> 返回列表
      </el-button>
      <h2 class="create-run__title">新建挖掘任务</h2>
    </div>

    <div class="create-run__body">
      <!-- Left: Upload -->
      <div class="create-run__upload-zone"
        :class="{ 'create-run__upload-zone--dragover': isDragOver }"
        @dragover.prevent="isDragOver = true"
        @dragleave.prevent="isDragOver = false"
        @drop.prevent="handleDrop"
      >
        <input
          ref="fileInput"
          type="file"
          multiple
          accept=".md,.txt,.pdf,.html,.docx"
          style="display: none"
          @change="handleFileSelect"
        />
        <template v-if="files.length === 0">
          <div class="upload-empty">
            <div class="upload-empty__icon">+</div>
            <div class="upload-empty__text">拖拽文件到此处或点击上传</div>
            <div class="upload-empty__hint">支持 .md, .txt, .pdf, .html, .docx，可多选</div>
          </div>
          <el-button @click="(fileInput as HTMLInputElement)?.click()">选择文件</el-button>
        </template>
        <template v-else>
          <div class="upload-file-list">
            <div v-for="(f, idx) in files" :key="idx" class="upload-file">
              <span class="upload-file__name">{{ f.name }}</span>
              <span class="upload-file__size">{{ formatFileSize(f.size) }}</span>
              <el-button text size="small" @click="removeFile(idx)">&times;</el-button>
            </div>
          </div>
          <div class="upload-actions">
            <el-button size="small" @click="(fileInput as HTMLInputElement)?.click()">添加更多</el-button>
            <el-button size="small" text type="danger" @click="files = []">清空</el-button>
          </div>
        </template>
      </div>

      <!-- Right: Config -->
      <div class="create-run__config">
        <div class="config-section">
          <h4 class="config-section__title">基本配置</h4>
          <el-form label-width="90px" label-position="top">
            <el-form-item label="知识域">
              <el-input :model-value="domainStore.currentDomain" disabled />
            </el-form-item>
            <el-form-item label="或输入路径">
              <el-input v-model="inputPath" placeholder="手动输入目录路径（可选，优先使用上传）" />
            </el-form-item>
          </el-form>
        </div>

        <div class="config-section">
          <h4 class="config-section__title">高级选项</h4>
          <el-form label-width="90px" label-position="top">
            <el-form-item label="并发数">
              <el-input-number v-model="maxWorkers" :min="1" :max="8" />
            </el-form-item>
            <el-form-item label="仅执行 Phase 1">
              <el-switch v-model="phase1Only" />
            </el-form-item>
          </el-form>
        </div>

        <div class="config-actions">
          <el-button @click="$router.push('/mining')">取消</el-button>
          <el-button type="primary" @click="handleCreate" :loading="creating">
            创建任务
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useMiningStore } from '@/stores/mining'
import { useMiningApi } from '@/api/mining'

const router = useRouter()
const domainStore = useDomainStore()
const miningStore = useMiningStore()
const miningApi = useMiningApi()

const files = ref<File[]>([])
const inputPath = ref('')
const maxWorkers = ref(2)
const phase1Only = ref(false)
const creating = ref(false)
const isDragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

function handleDrop(e: DragEvent) {
  isDragOver.value = false
  const dropped = e.dataTransfer?.files
  if (!dropped) return
  const allowed = ['.md', '.txt', '.pdf', '.html', '.docx']
  const newFiles = Array.from(dropped).filter(f =>
    allowed.some(ext => f.name.toLowerCase().endsWith(ext))
  )
  if (newFiles.length === 0) {
    ElMessage.warning('不支持的文件格式')
    return
  }
  files.value = [...files.value, ...newFiles]
}

function handleFileSelect(e: Event) {
  const target = e.target as HTMLInputElement
  if (!target.files) return
  files.value = [...files.value, ...Array.from(target.files)]
  target.value = ''
}

function removeFile(idx: number) {
  files.value = files.value.filter((_, i) => i !== idx)
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

async function handleCreate() {
  creating.value = true
  try {
    let path = inputPath.value

    if (files.value.length > 0) {
      const result = await miningApi.uploadFiles(domainStore.currentDomain, files.value)
      path = result.storage_path
    }

    if (!path) {
      ElMessage.warning('请上传文件或输入路径')
      creating.value = false
      return
    }

    await miningStore.createRun({
      domain: domainStore.currentDomain,
      input_path: path,
      max_workers: maxWorkers.value,
      phase1_only: phase1Only.value,
    })
    ElMessage.success('任务已创建')
    router.push('/mining')
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  } finally {
    creating.value = false
  }
}
</script>

<style scoped>
.create-run {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.create-run__header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.create-run__title {
  font-size: 16px;
  font-weight: 650;
  color: var(--kb-text-primary);
  margin: 0;
}

.create-run__body {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-items: start;
}

/* Upload zone */
.create-run__upload-zone {
  background: var(--kb-bg-card);
  border: 2px dashed var(--kb-border);
  border-radius: var(--kb-radius);
  padding: 32px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  min-height: 300px;
  transition: all var(--kb-duration) var(--kb-ease);
}

.create-run__upload-zone--dragover {
  border-color: var(--kb-accent);
  background: var(--kb-accent-soft);
}

.upload-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.upload-empty__icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--kb-border-light);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: var(--kb-text-tertiary);
}

.upload-empty__text {
  font-size: 14px;
  color: var(--kb-text-secondary);
  font-weight: 500;
}

.upload-empty__hint {
  font-size: 12px;
  color: var(--kb-text-tertiary);
}

.upload-file-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 200px;
  overflow-y: auto;
}

.upload-file {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
}

.upload-file__name {
  flex: 1;
  font-size: 13px;
  color: var(--kb-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-file__size {
  font-size: 11px;
  color: var(--kb-text-tertiary);
}

.upload-actions {
  display: flex;
  gap: 8px;
}

/* Config */
.create-run__config {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.config-section {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  padding: 20px;
}

.config-section__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 16px;
}

.config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 768px) {
  .create-run__body {
    grid-template-columns: 1fr;
  }
}
</style>
