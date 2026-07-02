<template>
  <div>
    <div class="ipp__stats">
      <el-statistic title="总行数" :value="preview.total_rows" />
      <el-statistic title="可确认" :value="preview.confirmable_rows" />
      <el-statistic title="草稿" :value="preview.draft_rows" />
      <el-statistic title="拒绝" :value="preview.rejected_rows" />
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="预览不会写入任何样本。已确认样本不会被导入覆盖。"
      class="ipp__hint"
    />

    <CaseValidationTable :rows="preview.rows" />

    <div class="ipp__commit">
      <el-form inline>
        <el-form-item label="重复处理">
          <el-select v-model="duplicatePolicy" style="width: 160px">
            <el-option label="跳过" value="skip" />
            <el-option label="作为草稿导入" value="import_as_draft" />
            <el-option label="新建副本" value="copy" />
            <el-option label="更新草稿" value="update_draft" />
          </el-select>
        </el-form-item>
        <el-form-item label="完整行直接确认">
          <el-switch v-model="confirmComplete" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="committing" @click="emitCommit">提交导入</el-button>
        </el-form-item>
      </el-form>
      <p class="ipp__note">
        关闭“完整行直接确认”时，所有有效行先进草稿；打开后，标准答案完整的行才会写成已确认。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { DatasetImportPreview } from '@/types/evaluation'
import CaseValidationTable from './CaseValidationTable.vue'

defineProps<{ preview: DatasetImportPreview; committing?: boolean }>()
const emit = defineEmits<{
  (e: 'commit', payload: { duplicate_policy: string; confirm_complete_rows: boolean }): void
}>()

const duplicatePolicy = ref('skip')
const confirmComplete = ref(false)

function emitCommit() {
  emit('commit', { duplicate_policy: duplicatePolicy.value, confirm_complete_rows: confirmComplete.value })
}
</script>

<style scoped>
.ipp__stats { display: flex; gap: 36px; margin-bottom: 14px; }
.ipp__hint { margin-bottom: 14px; }
.ipp__commit { margin-top: 16px; }
.ipp__note { color: var(--kb-text-secondary, #64748b); font-size: 12px; margin: 4px 0 0; }
</style>
