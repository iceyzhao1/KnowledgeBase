<template>
  <div class="run-report">
    <div class="run-report__head">
      <div>
        <h2>运行报告</h2>
        <p>按测试种类解释质量分数，保留原始指标用于排查。</p>
      </div>
      <router-link to="/"><el-button :icon="Back">返回</el-button></router-link>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <template v-if="report">
      <RunStatusPanel :run="report.run" class="mb" />

      <el-card shadow="never" class="mb">
        <template #header><span>质量指标解读</span></template>
        <MetricSummaryGrid :summary="report.metric_summary" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>技术指标明细</span></template>
        <MetricTable :rows="report.metrics" show-detail-action @detail="selectMetric" />
      </el-card>

      <el-card v-if="selectedMetricId" shadow="never" class="mb">
        <template #header>
          <div class="metric-detail__head">
            <span>{{ selectedMetricInfo.label }} · 评分样本明细</span>
            <el-button link @click="selectedMetricId = null">收起</el-button>
          </div>
        </template>
        <el-descriptions :column="3" border size="small" class="mb">
          <el-descriptions-item label="指标 ID">{{ selectedMetricId }}</el-descriptions-item>
          <el-descriptions-item label="聚合分数">{{ selectedMetricDisplayValue }}</el-descriptions-item>
          <el-descriptions-item label="判断">{{ selectedMetricVerdict }}</el-descriptions-item>
        </el-descriptions>
        <p class="metric-detail__hint">{{ selectedMetricInfo.interpretation }}</p>
        <el-table :data="selectedMetricCases" size="small" border>
          <el-table-column prop="case_id" label="样本 ID" min-width="180" />
          <el-table-column label="样本分数" width="140">
            <template #default="{ row }">{{ displayMetricValue(row.metric_id, row.value) }}</template>
          </el-table-column>
          <el-table-column label="判断" width="130">
            <template #default="{ row }">
              <el-tag :type="tagType(metricTone(row.metric_id, row.value))" size="small">
                {{ metricVerdict(row.metric_id, row.value) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="技术状态" width="120">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <router-link :to="`/runs/${runId}/cases/${row.case_id}`">
                <el-button link type="primary" size="small">查看判分过程/详情</el-button>
              </router-link>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!selectedMetricCases.length" description="该指标暂无样本级明细" :image-size="64" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>失败样本（{{ report.failures.length }}）</span></template>
        <FailureCaseTable :failures="report.failures" :run-id="runId" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>大模型判分用量</span></template>
        <JudgeUsagePanel :usage="report.judge_usage" />
      </el-card>
    </template>

    <el-empty v-else-if="!loading" description="暂无报告" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Back } from '@element-plus/icons-vue'
import { useEvaluationApi } from '@/api/evaluation'
import type { MetricCaseRow, MetricRow, RunCaseRow, RunReportViewModel } from '@/types/evaluation'
import RunStatusPanel from '@/components/evaluation/RunStatusPanel.vue'
import MetricSummaryGrid from '@/components/evaluation/MetricSummaryGrid.vue'
import MetricTable from '@/components/evaluation/MetricTable.vue'
import FailureCaseTable from '@/components/evaluation/FailureCaseTable.vue'
import JudgeUsagePanel from '@/components/evaluation/JudgeUsagePanel.vue'
import {
  displayMetricValue,
  explainMetric,
  metricTone,
  metricVerdict,
} from '@/utils/metricCatalog.mjs'

const props = defineProps<{ runId: string }>()
const api = useEvaluationApi()

const report = ref<RunReportViewModel | null>(null)
const runCases = ref<RunCaseRow[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const selectedMetricId = ref<string | null>(null)

const selectedMetric = computed<MetricRow | null>(() =>
  report.value?.metrics.find((m) => m.metric_id === selectedMetricId.value) ?? null,
)

const selectedMetricInfo = computed(() =>
  selectedMetric.value
    ? explainMetric(selectedMetric.value.metric_id, selectedMetric.value.value)
    : explainMetric('', null),
)

const selectedMetricCases = computed<MetricCaseRow[]>(() => {
  if (!selectedMetricId.value) return []
  const fromReport = report.value?.metric_cases?.[selectedMetricId.value] ?? []
  if (fromReport.length) return fromReport
  return fallbackMetricCases(selectedMetricId.value)
})

const selectedMetricDisplayValue = computed(() =>
  selectedMetric.value ? displayMetricValue(selectedMetric.value.metric_id, selectedMetric.value.value) : '-',
)

const selectedMetricVerdict = computed(() =>
  selectedMetric.value ? metricVerdict(selectedMetric.value.metric_id, selectedMetric.value.value) : '-',
)

function selectMetric(metricId: string) {
  selectedMetricId.value = metricId
}

function fallbackMetricCases(metricId: string): MetricCaseRow[] {
  const level = selectedMetric.value?.level ?? 'retrieval'
  return runCases.value
    .filter((row) => Object.prototype.hasOwnProperty.call(row.metrics || {}, metricId))
    .map((row) => ({
      case_id: row.case_id,
      metric_id: metricId,
      level,
      value: row.metrics[metricId],
      status: row.metric_statuses?.[metricId] ?? 'ok',
    }))
    .sort((a, b) => metricCaseSortValue(a) - metricCaseSortValue(b) || a.case_id.localeCompare(b.case_id))
}

function metricCaseSortValue(row: MetricCaseRow): number {
  const value = Number(row.value)
  return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY
}

function tagType(tone: string): 'success' | 'warning' | 'info' | 'danger' {
  if (tone === 'success') return 'success'
  if (tone === 'warning') return 'warning'
  if (tone === 'danger') return 'danger'
  return 'info'
}

function statusType(status: string): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'ok') return 'success'
  if (status === 'error') return 'danger'
  if (status === 'inconclusive' || status === 'missing_inputs') return 'warning'
  return 'info'
}

onMounted(async () => {
  loading.value = true
  try {
    report.value = await api.getRunReport(props.runId)
    runCases.value = await api.listRunCases(props.runId)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.run-report__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}

.run-report__head p {
  margin: 4px 0 0;
  color: var(--kb-text-secondary, #64748b);
  font-size: 13px;
}

.mb { margin-bottom: 16px; }
.metric-detail__head { display: flex; justify-content: space-between; align-items: center; }
.metric-detail__hint {
  margin: 0 0 12px;
  color: var(--kb-text-secondary, #64748b);
  font-size: 13px;
  line-height: 1.6;
}
</style>
