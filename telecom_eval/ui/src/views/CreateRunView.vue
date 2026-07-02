<template>
  <div class="create-run">
    <div class="create-run__head">
      <h2>新建评估运行</h2>
      <router-link to="/"><el-button :icon="Back">返回</el-button></router-link>
    </div>

    <el-card shadow="never">
      <el-form :model="form" label-width="160px" style="max-width: 760px">
        <el-form-item label="数据集">
          <el-select
            v-model="form.dataset_id"
            placeholder="选择数据集"
            filterable
            allow-create
            default-first-option
            style="width: 100%"
          >
            <el-option
              v-for="d in datasets"
              :key="d.dataset_id"
              :label="`${d.dataset_id}（已确认 ${d.confirmed_count}/${d.case_count}）`"
              :value="d.dataset_id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="检索范式">
          <el-select
            v-model="selectedParadigmName"
            placeholder="选择已发布范式，例如 A1_dense_raw_text"
            filterable
            style="width: 100%"
            :loading="loadingParadigms"
            @change="selectParadigm"
          >
            <el-option
              v-for="p in paradigms"
              :key="p.id || p.name"
              :label="p.name"
              :value="p.name"
            >
              <div class="paradigm-option">
                <strong>{{ p.name }}</strong>
                <span>v{{ p.version }} · {{ p.description || '暂无描述' }}</span>
              </div>
            </el-option>
          </el-select>
          <div class="field-hint">
            创建评估时会把选中的范式名称写入被测范式 ID，用于区分被测范式。
          </div>
        </el-form-item>

        <el-alert
          v-if="paradigmLoadError"
          type="warning"
          show-icon
          :closable="false"
          class="mb"
          title="范式列表加载失败，可以先手动填写范式名称。"
        />

        <el-form-item v-if="paradigmLoadError" label="手动范式名称">
          <el-input
            v-model="form.subject_id"
            placeholder="例如 A1_dense_raw_text"
            @input="selectedParadigmName = form.subject_id; form.subject_search_path = null"
          />
        </el-form-item>

        <el-form-item label="被测范式 ID 预览">
          <div class="subject-preview">
            <code>{{ form.subject_id || '（请选择范式）' }}</code>
            <el-tag v-if="duplicateWarning" type="warning" size="small" class="dup-tag">
              已有历史记录
            </el-tag>
          </div>
          <div v-if="selectedParadigm" class="selected-paradigm">
            {{ selectedParadigm.description || '暂无描述' }}
            <span v-if="selectedParadigm.url"> · {{ selectedParadigm.url }}</span>
          </div>
          <div v-if="duplicateWarning" class="dup-hint">
            该范式已有 {{ duplicateCount }} 条评估记录，继续将生成新的对比 run。
          </div>
        </el-form-item>

        <el-form-item label="评估类型">
          <el-radio-group v-model="form.eval_type">
            <el-radio-button label="retrieval">检索</el-radio-button>
            <el-radio-button label="e2e">端到端</el-radio-button>
            <el-radio-button label="mixed">混合</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="仅已确认样本">
          <el-switch v-model="form.confirmed_only" />
        </el-form-item>
        <el-form-item label="返回证据条数">
          <el-input-number v-model="form.top_k" :min="1" :max="50" />
        </el-form-item>

        <el-divider content-position="left">大模型判分治理</el-divider>
        <el-form-item label="允许大模型判分">
          <el-switch v-model="form.allow_llm_judge" />
        </el-form-item>
        <el-alert
          v-if="!form.allow_llm_judge"
          type="warning"
          :closable="false"
          show-icon
          title="关闭大模型判分时，需要语义判断的端到端指标可能无法判定。"
          class="mb"
        />
        <template v-if="form.allow_llm_judge">
          <el-form-item label="失败重试次数">
            <el-input-number v-model="form.judge_budget.max_llm_retries" :min="0" :max="5" />
          </el-form-item>
          <div class="field-hint budget-hint">
            这里只控制大模型调用失败后的重试次数。0 表示不重试，1 表示失败后再试一次。
            总调用次数和总令牌数默认不设上限；缓存命中不会重复调用。
          </div>
        </template>

        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="submit">创建并运行</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Back } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useEvaluationApi } from '@/api/evaluation'
import {
  defaultCreateRunRequest,
  type DatasetSummary,
  type EvaluationRunSummary,
  type PublishedParadigm,
} from '@/types/evaluation'

const router = useRouter()
const api = useEvaluationApi()

const form = reactive(defaultCreateRunRequest())
const datasets = ref<DatasetSummary[]>([])
const allRuns = ref<EvaluationRunSummary[]>([])
const paradigms = ref<PublishedParadigm[]>([])
const loadingParadigms = ref(false)
const paradigmLoadError = ref(false)
const selectedParadigmName = ref('')
const submitting = ref(false)

const duplicateWarning = ref(false)
const duplicateCount = ref(0)

const selectedParadigm = computed(() =>
  paradigms.value.find((p) => p.name === selectedParadigmName.value) || null
)

const selectedDataset = computed(() =>
  datasets.value.find((d) => d.dataset_id === form.dataset_id) || null
)

const selectedDatasetCaseCount = computed(() => {
  const dataset = selectedDataset.value
  if (!dataset) return 0
  return form.confirmed_only ? (dataset.confirmed_count ?? dataset.case_count) : dataset.case_count
})

function selectParadigm(name: string) {
  const selected = paradigms.value.find((p) => p.name === name)
  form.subject_id = name
  form.subject_search_path = selected?.url || null
}

watch(() => form.subject_id, (id) => {
  if (!id) {
    duplicateWarning.value = false
    duplicateCount.value = 0
    return
  }
  const matches = allRuns.value.filter((r) => r.subject_id === id)
  duplicateCount.value = matches.length
  duplicateWarning.value = matches.length > 0
})

async function submit() {
  if (!form.dataset_id) {
    ElMessage.warning('请选择数据集')
    return
  }
  if (!form.subject_id) {
    ElMessage.warning('请选择检索范式')
    return
  }
  form.judge_budget.allow_llm_judge = form.allow_llm_judge
  form.judge_budget.max_llm_calls = 0
  form.judge_budget.max_total_tokens = 0
  form.judge_budget.max_cases_with_llm = selectedDatasetCaseCount.value
  submitting.value = true
  try {
    const run = await api.createRun(form)
    ElMessage.success('评估任务已创建，正在排队运行')
    router.push(`/runs/${run.run_id}`)
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    datasets.value = await api.listDatasets()
  } catch {
    // 用户仍可手动输入 dataset_id。
  }
  try {
    allRuns.value = await api.listRuns()
  } catch {
    // 历史记录查询失败不影响创建。
  }
  loadingParadigms.value = true
  try {
    paradigms.value = await api.listPublishedParadigms()
  } catch {
    paradigmLoadError.value = true
  } finally {
    loadingParadigms.value = false
  }
})
</script>

<style scoped>
.create-run__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.mb { margin-bottom: 16px; }

.field-hint {
  margin-top: 6px;
  color: var(--kb-text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.5;
}

.field-hint code {
  padding: 1px 5px;
  border-radius: 4px;
  background: #f1f5f9;
}

.paradigm-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.35;
}

.paradigm-option strong {
  color: #0f172a;
  font-size: 13px;
}

.paradigm-option span {
  color: var(--kb-text-secondary, #64748b);
  font-size: 12px;
}

.subject-preview {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.subject-preview code {
  background: var(--el-fill-color-light, #f5f7fa);
  padding: 2px 8px;
  border-radius: 4px;
}

.selected-paradigm {
  margin-top: 6px;
  color: var(--kb-text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.5;
}

.dup-tag {
  flex-shrink: 0;
}

.dup-hint {
  margin-top: 4px;
  color: var(--el-color-warning, #e6a23c);
  font-size: 12px;
}
</style>
