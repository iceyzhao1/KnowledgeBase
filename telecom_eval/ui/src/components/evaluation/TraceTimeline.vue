<template>
  <el-timeline>
    <el-timeline-item v-if="retrievalTrace" type="primary" timestamp="检索阶段">
      <div class="trace-line">
        <el-tag size="small">耗时 {{ retrievalTrace.latency_ms }} ms</el-tag>
        <el-tag size="small" type="info">证据 {{ evidenceCount(retrievalTrace) }} 条</el-tag>
        <el-tag v-if="hasErrors(retrievalTrace)" size="small" type="danger">有错误</el-tag>
      </div>
      <div v-if="warnings(retrievalTrace).length" class="trace-warn">
        告警：
        <el-tag v-for="(w, i) in warnings(retrievalTrace)" :key="i" size="small" type="warning">{{ w.type }}</el-tag>
      </div>
    </el-timeline-item>
    <el-timeline-item v-if="answerTrace" type="success" timestamp="回答阶段">
      <div class="trace-line">
        <el-tag size="small">耗时 {{ answerTrace.latency_ms }} ms</el-tag>
        <el-tag size="small" :type="refused(answerTrace) ? 'warning' : 'success'">
          {{ refused(answerTrace) ? '拒答' : '已回答' }}
        </el-tag>
        <el-tag v-if="hasErrors(answerTrace)" size="small" type="danger">有错误</el-tag>
      </div>
    </el-timeline-item>
    <el-timeline-item v-if="!retrievalTrace && !answerTrace" timestamp="无调用链">
      未找到该样本的检索/回答调用链。
    </el-timeline-item>
  </el-timeline>
</template>

<script setup lang="ts">
defineProps<{
  retrievalTrace?: Record<string, any> | null
  answerTrace?: Record<string, any> | null
}>()

function evidenceCount(trace: Record<string, any>): number {
  return trace?.output?.evidence_package?.length ?? 0
}
function warnings(trace: Record<string, any>): Record<string, any>[] {
  return trace?.output?.warnings ?? []
}
function hasErrors(trace: Record<string, any>): boolean {
  return (trace?.errors?.length ?? 0) > 0
}
function refused(trace: Record<string, any>): boolean {
  return trace?.output?.refusal?.refused ?? false
}
</script>

<style scoped>
.trace-line { display: flex; gap: 8px; align-items: center; }
.trace-warn { margin-top: 6px; display: flex; gap: 6px; align-items: center; font-size: 12px; }
</style>
