<template>
  <div>
    <el-alert
      v-if="!hasJudgment"
      title="暂无检索内容判定过程"
      description="当前运行结果里没有 retrieval_content_judgment 产物。旧 run 不会自动补写这份判分过程，请重新运行评估后再进入样本详情查看。"
      type="info"
      show-icon
      :closable="false"
    />
    <template v-else>
      <el-descriptions :column="4" border size="small" class="rj-summary">
        <el-descriptions-item label="判定策略">{{ currentJudgment.judgment_policy || '-' }}</el-descriptions-item>
        <el-descriptions-item label="ID 使用">{{ currentJudgment.id_policy === 'ignored' ? '不参与评分' : '-' }}</el-descriptions-item>
        <el-descriptions-item label="Top K">{{ currentJudgment.top_k ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="候选数">{{ currentJudgment.candidate_count ?? 0 }}</el-descriptions-item>
      </el-descriptions>

      <el-alert
        v-if="!rows.length"
        title="没有可展示的标准证据点"
        description="本次判分过程已保存，但没有生成逐条证据点判断。请检查样本是否填写了标准证据、标准要点或标准答案。"
        type="warning"
        show-icon
        :closable="false"
        class="rj-empty"
      />

      <el-table v-else :data="rows" size="small" border>
        <el-table-column label="标准证据点" min-width="260">
          <template #default="{ row }">
            <div class="rj-gold">
              <strong>{{ row.gold_id }}</strong>
              <span>{{ row.gold_text || '-' }}</span>
              <div v-if="row.gold_phrases?.length" class="rj-phrases">
                <el-tag v-for="phrase in row.gold_phrases" :key="phrase" size="small">{{ phrase }}</el-tag>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="最佳证据" min-width="220">
          <template #default="{ row }">
            <div v-if="row.best_support" class="rj-best">
              <el-tag :type="supportType(row.best_support.support_label)" size="small">
                {{ supportLabel(row.best_support.support_label) }}
              </el-tag>
              <span>排名 {{ row.best_support.rank ?? '-' }}</span>
              <span>分数 {{ formatScore(row.best_support.score) }}</span>
              <code v-if="row.best_support.evidence_id">{{ row.best_support.evidence_id }}</code>
            </div>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="判断原因" min-width="260">
          <template #default="{ row }">
            <span>{{ row.best_support?.reason || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="命中短语" min-width="180">
          <template #default="{ row }">
            <el-tag
              v-for="phrase in (row.best_support?.matched_phrases || [])"
              :key="phrase"
              size="small"
              class="rj-hit"
            >
              {{ phrase }}
            </el-tag>
            <span v-if="!(row.best_support?.matched_phrases || []).length">-</span>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ judgment?: Record<string, any> | null }>()

const hasJudgment = computed(() => Boolean(props.judgment && Object.keys(props.judgment).length))
const currentJudgment = computed(() => props.judgment ?? {})
const rows = computed(() => (currentJudgment.value.judgments || []) as Record<string, any>[])

function supportType(label: string): 'success' | 'warning' | 'info' | 'danger' {
  if (label === 'supported') return 'success'
  if (label === 'partial') return 'warning'
  if (label === 'not_supported') return 'danger'
  return 'info'
}

function supportLabel(label: string): string {
  const labels: Record<string, string> = {
    supported: '支持',
    partial: '部分支持',
    not_supported: '不支持',
  }
  return labels[label] || label || '-'
}

function formatScore(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(2) : String(value ?? '-')
}
</script>

<style scoped>
.rj-summary { margin-bottom: 12px; }
.rj-empty { margin-bottom: 12px; }
.rj-gold, .rj-best { display: flex; flex-direction: column; gap: 5px; }
.rj-gold strong { color: #0f172a; font-size: 13px; }
.rj-gold span { color: #334155; line-height: 1.5; }
.rj-phrases { display: flex; flex-wrap: wrap; gap: 4px; }
.rj-best code {
  color: var(--kb-text-secondary, #64748b);
  background: var(--el-fill-color-light, #f5f7fa);
  padding: 1px 5px;
  border-radius: 4px;
}
.rj-hit { margin-right: 4px; }
</style>
