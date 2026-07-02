<template>
  <div class="quality-report">
    <div class="quality-report__summary">
      <div class="quality-report__summary-label">本次结果解读</div>
      <div class="quality-report__summary-text">{{ interpretation }}</div>
    </div>

    <section v-for="group in groups" :key="group.key" class="metric-group">
      <div class="metric-group__head">
        <div>
          <h3>{{ group.title }}</h3>
          <p>{{ group.subtitle }}</p>
        </div>
        <span class="metric-group__count">{{ group.metrics.length }} 项指标</span>
      </div>

      <div class="metric-list">
        <article v-for="metric in group.metrics" :key="metric.rawId" class="metric-row">
          <div class="metric-row__identity">
            <div class="metric-row__title-line">
              <strong>{{ metric.label }}</strong>
              <code>{{ metric.rawId }}</code>
            </div>
            <div class="metric-row__type">{{ metric.testType }}</div>
          </div>

          <div class="metric-row__block">
            <span>含义</span>
            <p>{{ metric.description }}</p>
          </div>

          <div class="metric-row__block">
            <span>怎么看</span>
            <p>{{ metric.interpretation }}</p>
          </div>

          <div class="metric-row__side">
            <div class="metric-row__value">{{ metric.displayValue }}</div>
            <el-tag :type="tagType(metric.tone)" effect="light">{{ metric.verdict }}</el-tag>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { buildRunInterpretation, buildTesterMetricGroups } from '@/utils/metricCatalog.mjs'

const props = defineProps<{ summary: Record<string, Record<string, unknown>> }>()

const groups = computed(() => buildTesterMetricGroups(props.summary))
const interpretation = computed(() => buildRunInterpretation(props.summary))

function tagType(tone: string): 'success' | 'warning' | 'info' | 'danger' {
  if (tone === 'success') return 'success'
  if (tone === 'warning') return 'warning'
  if (tone === 'danger') return 'danger'
  return 'info'
}
</script>

<style scoped>
.quality-report {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.quality-report__summary {
  display: grid;
  grid-template-columns: 112px 1fr;
  gap: 12px;
  align-items: start;
  padding: 12px 14px;
  border: 1px solid #dbeafe;
  border-radius: 8px;
  background: #eff6ff;
}

.quality-report__summary-label {
  color: #1d4ed8;
  font-size: 13px;
  font-weight: 700;
}

.quality-report__summary-text {
  color: #1e3a8a;
  font-size: 13px;
  line-height: 1.7;
}

.metric-group {
  border: 1px solid var(--kb-border, #e2e8f0);
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.metric-group__head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  padding: 12px 14px;
  border-bottom: 1px solid var(--kb-border, #e2e8f0);
  background: #f8fafc;
}

.metric-group__head h3 {
  margin: 0;
  color: #0f172a;
  font-size: 16px;
}

.metric-group__head p {
  margin: 4px 0 0;
  color: var(--kb-text-secondary, #64748b);
  font-size: 13px;
}

.metric-group__count {
  flex: 0 0 auto;
  color: var(--kb-text-secondary, #64748b);
  font-size: 12px;
}

.metric-list {
  display: flex;
  flex-direction: column;
}

.metric-row {
  display: grid;
  grid-template-columns: 210px minmax(180px, 1fr) minmax(220px, 1.1fr) 118px;
  gap: 14px;
  padding: 14px;
  border-bottom: 1px solid #eef2f7;
}

.metric-row:last-child {
  border-bottom: none;
}

.metric-row__identity {
  min-width: 0;
}

.metric-row__title-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
}

.metric-row__title-line strong {
  color: #0f172a;
  font-size: 15px;
}

.metric-row__title-line code {
  padding: 1px 6px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #64748b;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.metric-row__block {
  min-width: 0;
}

.metric-row__block span {
  display: inline-block;
  margin-bottom: 4px;
  color: #0f172a;
  font-size: 12px;
  font-weight: 700;
}

.metric-row__block p {
  margin: 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.55;
}

.metric-row__side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  text-align: right;
}

.metric-row__value {
  color: #111827;
  font-size: 24px;
  font-weight: 750;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.metric-row__type {
  margin-top: 8px;
  color: var(--kb-text-tertiary, #94a3b8);
  font-size: 12px;
}

@media (max-width: 720px) {
  .quality-report__summary {
    grid-template-columns: 1fr;
  }

  .metric-group__head,
  .metric-row {
    grid-template-columns: 1fr;
  }

  .metric-group__head {
    align-items: flex-start;
  }

  .metric-row__side {
    align-items: flex-start;
    text-align: left;
  }
}
</style>
