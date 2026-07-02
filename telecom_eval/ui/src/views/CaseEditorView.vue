<template>
  <div class="case-editor">
    <div class="case-editor__head">
      <h2>{{ isEdit ? '编辑样本' : '新增样本' }} · {{ datasetId }}</h2>
      <router-link :to="`/datasets/${datasetId}`"><el-button :icon="Back">返回</el-button></router-link>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />
    <el-alert
      v-if="isConfirmed"
      type="warning"
      show-icon
      :closable="false"
      class="mb"
      title="该样本已确认：修改标准答案/要点/证据需勾选下方“允许修改已确认样本”，以免静默改写历史评估基础。"
    />

    <el-card shadow="never" class="mb">
      <el-form :model="form" label-width="120px">
        <el-form-item label="问题" required>
          <el-input v-model="form.question" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="标准答案">
          <el-input v-model="form.expected_answer" type="textarea" :rows="3" placeholder="根据下方关联的原文撰写标准答案" />
        </el-form-item>
        <el-form-item label="关键要点">
          <div class="kp-list">
            <el-tag v-for="(kp, i) in form.expected_key_points" :key="i" closable @close="form.expected_key_points.splice(i, 1)" class="chip">{{ kp }}</el-tag>
            <el-input v-model="kpInput" size="small" style="width: 220px" placeholder="输入后回车添加" @keyup.enter="addKp" />
          </div>
        </el-form-item>
        <el-form-item label="可答性">
          <el-select v-model="form.answerability" style="width: 200px">
            <el-option label="可回答" value="answerable" />
            <el-option label="无法回答" value="unanswerable" />
            <el-option label="应拒答" value="should_refuse" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务类型">
          <el-input v-model="form.task_type" style="width: 240px" />
        </el-form-item>
        <el-form-item label="风险等级">
          <el-select v-model="form.risk_level" style="width: 160px">
            <el-option label="低" value="low" />
            <el-option label="中" value="medium" />
            <el-option label="高" value="high" />
            <el-option label="严重" value="critical" />
          </el-select>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header><span>关联原文证据（生成标准答案的依据）</span></template>
      <EvidencePicker v-model="form.expected_evidence" :default-query="form.question" />
    </el-card>

    <div class="case-editor__foot">
      <el-checkbox v-if="isConfirmed" v-model="allowConfirmedEdit">允许修改已确认样本</el-checkbox>
      <el-button type="primary" :loading="saving" @click="save">{{ isEdit ? '保存' : '创建样本' }}</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Back } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useEvaluationApi } from '@/api/evaluation'
import type { EvidenceRef } from '@/types/evaluation'
import EvidencePicker from '@/components/evaluation/EvidencePicker.vue'

const props = defineProps<{ datasetId: string; caseId?: string }>()
const router = useRouter()
const api = useEvaluationApi()

const isEdit = computed(() => !!props.caseId)
const isConfirmed = ref(false)
const allowConfirmedEdit = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const kpInput = ref('')

const form = reactive({
  question: '',
  expected_answer: '',
  expected_key_points: [] as string[],
  expected_evidence: [] as EvidenceRef[],
  answerability: 'answerable',
  task_type: 'fact_lookup',
  risk_level: 'medium',
})

function addKp() {
  const v = kpInput.value.trim()
  if (v) {
    form.expected_key_points.push(v)
    kpInput.value = ''
  }
}

async function save() {
  if (!form.question.trim()) {
    ElMessage.warning('请填写问题')
    return
  }
  if (!form.expected_key_points.length || !form.expected_evidence.length) {
    ElMessage.warning('至少需要 1 个关键要点和 1 条关联原文')
    return
  }
  saving.value = true
  error.value = null
  try {
    if (isEdit.value && props.caseId) {
      await api.updateCase(props.caseId, {
        question: form.question,
        expected_answer: form.expected_answer,
        expected_key_points: form.expected_key_points,
        expected_evidence: form.expected_evidence,
        answerability: form.answerability,
        task_type: form.task_type,
        risk_level: form.risk_level,
        allow_confirmed_edit: allowConfirmedEdit.value,
      })
    } else {
      await api.addCase(props.datasetId, {
        question: form.question,
        expected_answer: form.expected_answer,
        expected_key_points: form.expected_key_points,
        expected_evidence: form.expected_evidence,
        answerability: form.answerability,
        task_type: form.task_type,
        risk_level: form.risk_level,
        gold_status: 'draft',
      })
    }
    ElMessage.success('已保存')
    router.push(`/datasets/${props.datasetId}`)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || (e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  if (props.caseId) {
    try {
      const c = (await api.getCase(props.caseId)) as Record<string, any>
      form.question = c.question || ''
      form.expected_answer = c.expected_answer || ''
      form.expected_key_points = c.expected_key_points || []
      form.expected_evidence = c.expected_evidence || []
      form.answerability = c.answerability || 'answerable'
      form.task_type = c.task_type || 'fact_lookup'
      form.risk_level = c.risk_level || 'medium'
      isConfirmed.value = c.gold_status === 'confirmed'
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '加载失败'
    }
  }
})
</script>

<style scoped>
.case-editor__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.mb { margin-bottom: 16px; }
.kp-list { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip { margin: 0; }
.case-editor__foot { display: flex; gap: 16px; align-items: center; justify-content: flex-end; }
</style>
