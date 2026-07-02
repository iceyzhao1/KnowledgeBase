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
            <el-option v-for="t in types" :key="t" :label="t" :value="t" />
          </el-select>
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
const types = ['retrieval', 'e2e', 'mixed', 'regression', 'adversarial', 'smoke']

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
</style>
