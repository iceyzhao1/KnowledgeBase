<template>
  <div class="ds-create">
    <div class="ds-create__head">
      <h2>新建测试集</h2>
      <router-link to="/datasets"><el-button :icon="Back">返回</el-button></router-link>
    </div>

    <el-card shadow="never">
      <el-form :model="form" label-width="120px" style="max-width: 640px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如 云核回归集" />
        </el-form-item>
        <el-form-item label="场景">
          <el-input v-model="form.scenario_id" placeholder="如 cloud_core / ip_network" />
        </el-form-item>
        <el-form-item label="用途">
          <el-select v-model="form.dataset_type" style="width: 100%">
            <el-option
              v-for="t in types"
              :key="t.value"
              :label="t.label"
              :value="t.value"
            >
              <div class="type-option">
                <strong>{{ t.label }}</strong>
                <span>{{ t.description }}</span>
              </div>
            </el-option>
          </el-select>
          <div class="field-hint">
            回归、冒烟、对抗等用途建议写入标签；这里仅选择评估方式。
          </div>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="tagsText" placeholder="逗号分隔，如 云核,回归" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Back } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useEvaluationApi } from '@/api/evaluation'
import type { CreateDatasetRequest } from '@/types/evaluation'

const router = useRouter()
const api = useEvaluationApi()
const types = [
  { label: '检索测试', value: 'retrieval', description: '只评估检索证据是否找得准、找得全。' },
  { label: '端到端测试', value: 'e2e', description: '评估基于证据包生成的最终回答质量。' },
  { label: '混合测试', value: 'mixed', description: '同时评估检索与最终回答，推荐默认使用。' },
] as const

const form = reactive<CreateDatasetRequest>({
  name: '',
  scenario_id: 'cloud_core',
  dataset_type: 'mixed',
  description: '',
  tags: [],
})
const tagsText = ref('')
const submitting = ref(false)

async function submit() {
  if (!form.name || !form.scenario_id) {
    ElMessage.warning('请填写名称与场景')
    return
  }
  form.tags = tagsText.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
  submitting.value = true
  try {
    const ds = await api.createDataset(form)
    ElMessage.success('已创建')
    router.push(`/datasets/${ds.dataset_id}`)
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.ds-create__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.field-hint {
  margin-top: 6px;
  color: var(--kb-text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.5;
}
.type-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.35;
}
.type-option strong {
  color: #0f172a;
  font-size: 13px;
}
.type-option span {
  color: var(--kb-text-secondary, #64748b);
  font-size: 12px;
}
</style>
