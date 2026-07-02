<template>
  <div class="case-debug">
    <div class="case-debug__head">
      <h2>样本调试 · {{ caseId }}</h2>
      <router-link :to="`/runs/${runId}`"><el-button :icon="Back">返回报告</el-button></router-link>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />

    <template v-if="view">
      <el-card shadow="never" class="mb">
        <template #header><span>问题与标准答案</span></template>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="问题">{{ view.case.question }}</el-descriptions-item>
          <el-descriptions-item label="标准答案">{{ view.case.expected_answer }}</el-descriptions-item>
          <el-descriptions-item label="关键要点">
            <el-tag v-for="(kp, i) in (view.case.expected_key_points as string[] || [])" :key="i" size="small" class="kp">{{ kp }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>标准证据</span></template>
        <ExpectedEvidencePanel :items="view.expected_evidence" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>检索证据包</span></template>
        <EvidencePackageViewer :items="evidencePackage" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>检索内容判定过程</span></template>
        <RetrievalJudgmentPanel :judgment="retrievalJudgment" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>调用链时间线</span></template>
        <TraceTimeline :retrieval-trace="view.retrieval_trace" :answer-trace="view.answer_trace" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>指标</span></template>
        <MetricTable :rows="metricRows" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>评估产物</span></template>
        <ArtifactInspector :artifacts="view.artifacts" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>失败归因</span></template>
        <DiagnosisPanel :diagnosis="view.diagnosis" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>大模型判分调用</span></template>
        <JudgeUsagePanel :usage="judgeUsage" :invocations="view.judge_invocations" />
      </el-card>

      <el-card shadow="never" class="mb">
        <template #header><span>原始数据（调用链 / 评估产物 / 全量）</span></template>
        <RawJsonViewer :data="view.raw" />
      </el-card>
    </template>

    <el-empty v-else-if="!loading" description="无调试数据" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Back } from '@element-plus/icons-vue'
import { useEvaluationApi } from '@/api/evaluation'
import type { CaseDebugViewModel, MetricRow } from '@/types/evaluation'
import ExpectedEvidencePanel from '@/components/evaluation/ExpectedEvidencePanel.vue'
import EvidencePackageViewer from '@/components/evaluation/EvidencePackageViewer.vue'
import RetrievalJudgmentPanel from '@/components/evaluation/RetrievalJudgmentPanel.vue'
import TraceTimeline from '@/components/evaluation/TraceTimeline.vue'
import MetricTable from '@/components/evaluation/MetricTable.vue'
import ArtifactInspector from '@/components/evaluation/ArtifactInspector.vue'
import DiagnosisPanel from '@/components/evaluation/DiagnosisPanel.vue'
import JudgeUsagePanel from '@/components/evaluation/JudgeUsagePanel.vue'
import RawJsonViewer from '@/components/evaluation/RawJsonViewer.vue'

const props = defineProps<{ runId: string; caseId: string }>()
const api = useEvaluationApi()

const view = ref<CaseDebugViewModel | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const evidencePackage = computed<Record<string, any>[]>(() => {
  const trace = view.value?.retrieval_trace as Record<string, any> | undefined
  return (trace?.output?.evidence_package as Record<string, any>[]) ?? []
})

const retrievalJudgment = computed<Record<string, any> | null>(() => {
  if (view.value?.retrieval_content_judgment) return view.value.retrieval_content_judgment
  const artifact = (view.value?.artifacts ?? []).find(
    (item) => item.artifact_type === 'retrieval_content_judgment',
  ) as Record<string, any> | undefined
  const payload = artifact?.payload as Record<string, any> | undefined
  return (payload?.payload as Record<string, any> | undefined) ?? null
})

const metricRows = computed<MetricRow[]>(() =>
  (view.value?.metrics ?? []).map((m) => ({
    metric_id: String(m.metric_id),
    level: String(m.level),
    value: m.value,
    status: String(m.status),
  })),
)

const judgeUsage = computed(() => {
  const inv = view.value?.judge_invocations ?? []
  const okCaseIds = new Set(
    inv
      .filter((i: Record<string, any>) => i.status === 'ok' && i.case_id)
      .map((i: Record<string, any>) => String(i.case_id)),
  )
  return {
    total_invocations: inv.length,
    ok_calls: inv.filter((i: Record<string, any>) => i.status === 'ok').length,
    skipped_calls: inv.filter((i: Record<string, any>) => i.status === 'skipped').length,
    total_tokens: inv.reduce((s: number, i: Record<string, any>) => s + (Number(i.total_tokens) || 0), 0),
    cases_with_llm: okCaseIds.size,
  }
})

onMounted(async () => {
  loading.value = true
  try {
    view.value = await api.getCaseDebug(props.runId, props.caseId)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.case-debug__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.mb { margin-bottom: 16px; }
.kp { margin-right: 4px; }
</style>
