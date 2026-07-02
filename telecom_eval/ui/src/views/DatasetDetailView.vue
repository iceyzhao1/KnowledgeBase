<template>
  <div class="ds-detail">
    <div class="ds-detail__head">
      <h2>{{ dataset?.name || datasetId }}</h2>
      <div class="ds-detail__actions">
        <router-link :to="`/datasets/${datasetId}/import`">
          <el-button type="primary" :icon="Upload">导入样本</el-button>
        </router-link>
        <el-popconfirm
          :title="`确认将 ${draftCount} 条草稿样本设为已确认？`"
          width="260"
          @confirm="confirmDraftCases"
        >
          <template #reference>
            <el-button :icon="CircleCheck" :loading="confirming" :disabled="draftCount === 0">
              确认草稿样本
            </el-button>
          </template>
        </el-popconfirm>
        <el-button :icon="Camera" @click="makeSnapshot" :loading="snapping">建快照</el-button>
        <router-link to="/datasets"><el-button :icon="Back">返回</el-button></router-link>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <template v-if="dataset">
      <el-descriptions :column="3" border size="small" class="mb">
        <el-descriptions-item label="状态">
          <el-select v-model="dataset.status" size="small" style="width: 120px" @change="onStatusChange">
            <el-option label="草稿" value="draft" />
            <el-option label="启用" value="active" />
            <el-option label="归档" value="archived" />
          </el-select>
        </el-descriptions-item>
        <el-descriptions-item label="场景">{{ dataset.scenario_id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="用途">{{ dataset.dataset_type }}</el-descriptions-item>
        <el-descriptions-item label="样本数">{{ dataset.case_count }}</el-descriptions-item>
        <el-descriptions-item label="已确认">{{ dataset.confirmed_case_count }}</el-descriptions-item>
        <el-descriptions-item label="说明">{{ dataset.description || '-' }}</el-descriptions-item>
      </el-descriptions>

      <el-alert v-if="lastSnapshot" type="success" :closable="false" show-icon class="mb"
        :title="`已创建快照 ${lastSnapshot}（仅含已确认样本），可用于可复现评估`" />

      <el-card shadow="never">
        <template #header>
          <div class="cases-header">
            <span>样本（{{ filteredCases.length }} / {{ cases.length }}）</span>
            <div class="cases-header__tools">
              <el-input
                v-model="search"
                placeholder="搜索问题或标准答案"
                clearable
                size="small"
                style="width: 260px"
                :prefix-icon="Search"
              />
              <router-link :to="`/datasets/${datasetId}/cases/new`">
                <el-button type="primary" size="small" :icon="Plus">新增样本</el-button>
              </router-link>
            </div>
          </div>
        </template>
        <el-table :data="filteredCases" size="small" border max-height="560">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="case-gold">
                <div class="case-gold__row">
                  <span class="case-gold__label">标准答案</span>
                  <span class="case-gold__val">{{ row.expected_answer || '（缺）' }}</span>
                </div>
                <div class="case-gold__row">
                  <span class="case-gold__label">关键要点（黄金短语）</span>
                  <span class="case-gold__val">
                    <el-tag v-for="(kp, i) in (row.expected_key_points || [])" :key="i" size="small" class="chip">{{ kp }}</el-tag>
                    <em v-if="!(row.expected_key_points || []).length">（缺）</em>
                  </span>
                </div>
                <div class="case-gold__row">
                  <span class="case-gold__label">标准证据 / 关联原文</span>
                  <span class="case-gold__val" style="flex: 1">
                    <el-table :data="row.expected_evidence || []" size="small" border>
                      <el-table-column label="标题" min-width="140">
                        <template #default="{ row: ev }">{{ ev.title ?? '-' }}</template>
                      </el-table-column>
                      <el-table-column label="原文片段" min-width="320">
                        <template #default="{ row: ev }">
                          <span v-if="ev.text" class="ev-text">{{ ev.text }}</span>
                          <em v-else class="ev-text--missing">（未关联原文）</em>
                        </template>
                      </el-table-column>
                      <el-table-column label="证据 ID" min-width="150">
                        <template #default="{ row: ev }"><code>{{ ev.evidence_id ?? '-' }}</code></template>
                      </el-table-column>
                      <el-table-column label="来源 ID" min-width="150">
                        <template #default="{ row: ev }"><code>{{ ev.source_id ?? '-' }}</code></template>
                      </el-table-column>
                    </el-table>
                  </span>
                </div>
                <div v-if="(row.tags || []).length" class="case-gold__row">
                  <span class="case-gold__label">标签</span>
                  <span class="case-gold__val">{{ row.tags.join(', ') }}</span>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="编号" width="64" type="index" :index="1" />
          <el-table-column prop="case_id" label="样本 ID" min-width="150" />
          <el-table-column prop="question" label="问题" min-width="240" />
          <el-table-column label="要点数" width="80">
            <template #default="{ row }">{{ (row.expected_key_points || []).length }}</template>
          </el-table-column>
          <el-table-column label="证据数" width="80">
            <template #default="{ row }">{{ (row.expected_evidence || []).length }}</template>
          </el-table-column>
          <el-table-column label="标准状态" width="110">
            <template #default="{ row }">
              <el-tag :type="row.gold_status === 'confirmed' ? 'success' : 'warning'" size="small">
                {{ row.gold_status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="answerability" label="可答性" width="120" />
          <el-table-column prop="risk_level" label="风险" width="80" />
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <router-link :to="`/datasets/${datasetId}/cases/${row.case_id}/edit`">
                <el-button link type="primary" size="small">编辑</el-button>
              </router-link>
              <el-popconfirm
                title="确认从测试集删除该样本？历史评估按快照固化，不受影响。"
                width="240"
                @confirm="removeCase(row.case_id)"
              >
                <template #reference>
                  <el-button link type="danger" size="small">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Back, Camera, CircleCheck, Plus, Search, Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useEvaluationApi } from '@/api/evaluation'
import type { EvaluationDataset } from '@/types/evaluation'

const props = defineProps<{ datasetId: string }>()
const api = useEvaluationApi()

const dataset = ref<EvaluationDataset | null>(null)
const cases = ref<Record<string, any>[]>([])
const search = ref('')
const confirming = ref(false)

const filteredCases = computed(() => {
  const kw = search.value.trim().toLowerCase()
  if (!kw) return cases.value
  return cases.value.filter((c) => {
    const q = String(c.question || '').toLowerCase()
    const a = String(c.expected_answer || '').toLowerCase()
    return q.includes(kw) || a.includes(kw)
  })
})
const draftCount = computed(() => cases.value.filter((c) => c.gold_status === 'draft').length)
const error = ref<string | null>(null)
const snapping = ref(false)
const lastSnapshot = ref<string | null>(null)

async function load() {
  try {
    dataset.value = await api.getDataset(props.datasetId)
    cases.value = (await api.listDatasetCases(props.datasetId)) as Record<string, any>[]
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '加载失败'
  }
}

async function onStatusChange(status: string) {
  try {
    await api.updateDataset(props.datasetId, { status })
    ElMessage.success('状态已更新')
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '更新失败')
  }
}

async function removeCase(caseId: string) {
  try {
    await api.deleteCase(caseId)
    ElMessage.success('已删除')
    await load()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败')
  }
}

async function confirmDraftCases() {
  confirming.value = true
  try {
    const result = await api.confirmDatasetCases(props.datasetId)
    ElMessage.success(`已确认 ${result.confirmed} 条样本`)
    await load()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '确认失败')
  } finally {
    confirming.value = false
  }
}

async function makeSnapshot() {
  snapping.value = true
  try {
    const snap = await api.createSnapshot(props.datasetId, true)
    lastSnapshot.value = snap.dataset_snapshot_id
    ElMessage.success(`快照含 ${snap.case_ids.length} 条已确认样本`)
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '建快照失败')
  } finally {
    snapping.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.ds-detail__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.ds-detail__actions { display: flex; gap: 8px; }
.mb { margin-bottom: 16px; }
.case-gold { padding: 8px 16px; display: flex; flex-direction: column; gap: 10px; }
.case-gold__row { display: flex; gap: 12px; align-items: flex-start; }
.case-gold__label { width: 150px; flex-shrink: 0; color: var(--kb-text-secondary, #64748b); font-size: 12px; }
.case-gold__val { font-size: 13px; line-height: 1.6; }
.chip { margin: 0 4px 4px 0; }
.cases-header { display: flex; justify-content: space-between; align-items: center; }
.cases-header__tools { display: flex; gap: 10px; align-items: center; }
.ev-text { display: block; max-height: 110px; overflow: auto; line-height: 1.6; white-space: pre-wrap; }
.ev-text--missing { color: var(--kb-text-tertiary, #94a3b8); }
</style>
