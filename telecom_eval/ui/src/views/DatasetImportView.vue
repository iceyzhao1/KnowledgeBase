<template>
  <div class="ds-import">
    <div class="ds-import__head">
      <h2>导入样本 · {{ datasetId }}</h2>
      <router-link :to="`/datasets/${datasetId}`"><el-button :icon="Back">返回</el-button></router-link>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <el-card v-if="!preview" shadow="never">
      <el-form label-width="100px">
        <el-form-item label="文件名">
          <el-input v-model="filename" placeholder="cases.jsonl / cases.csv / cases.json" style="max-width: 360px" />
          <el-upload :auto-upload="false" :show-file-list="false" :on-change="onFile" style="display: inline-block; margin-left: 12px">
            <el-button :icon="Upload">选择文件读入</el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="content" type="textarea" :rows="12" placeholder='每行一个 JSON（.jsonl），或 JSON 数组 / CSV 文本' />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="previewing" @click="doPreview">生成预览（不写入）</el-button>
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="导入必须先预览校验，绝不直接写入；已确认样本不会被覆盖。"
      />
    </el-card>

    <el-card v-else shadow="never">
      <template #header>
        <div class="ds-import__pvhead">
          <span>导入预览 · {{ preview.filename }}</span>
          <el-button link @click="preview = null">重新选择</el-button>
        </div>
      </template>
      <ImportPreviewPanel :preview="preview" :committing="committing" @commit="doCommit" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Back, Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { useEvaluationApi } from '@/api/evaluation'
import type { DatasetImportPreview } from '@/types/evaluation'
import ImportPreviewPanel from '@/components/evaluation/ImportPreviewPanel.vue'

const props = defineProps<{ datasetId: string }>()
const router = useRouter()
const api = useEvaluationApi()

const filename = ref('cases.jsonl')
const content = ref('')
const preview = ref<DatasetImportPreview | null>(null)
const previewing = ref(false)
const committing = ref(false)
const error = ref<string | null>(null)

async function onFile(file: UploadFile) {
  const raw = file.raw
  if (!raw) return
  filename.value = file.name
  content.value = await raw.text()
}

async function doPreview() {
  if (!content.value.trim()) {
    ElMessage.warning('请先粘贴或读入文件内容')
    return
  }
  previewing.value = true
  try {
    preview.value = await api.previewImport(props.datasetId, filename.value, content.value)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '预览失败'
  } finally {
    previewing.value = false
  }
}

async function doCommit(payload: { duplicate_policy: string; confirm_complete_rows: boolean }) {
  if (!preview.value) return
  committing.value = true
  try {
    const res = await api.commitImport(props.datasetId, {
      import_id: preview.value.import_id,
      duplicate_policy: payload.duplicate_policy as any,
      confirm_complete_rows: payload.confirm_complete_rows,
    })
    ElMessage.success(`导入完成：已确认 ${res.inserted_confirmed} / 草稿 ${res.inserted_draft} / 跳过 ${res.skipped}`)
    router.push(`/datasets/${props.datasetId}`)
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '提交失败')
  } finally {
    committing.value = false
  }
}
</script>

<style scoped>
.ds-import__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.ds-import__pvhead { display: flex; justify-content: space-between; align-items: center; }
.mb { margin-bottom: 16px; }
</style>
