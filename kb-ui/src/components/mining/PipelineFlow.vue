<template>
  <div class="pipeline-flow">
    <div
      v-for="(stage, idx) in stages"
      :key="stage.key"
      class="pipeline-stage"
      :class="[`pipeline-stage--${getStageStatus(stage.key)}`]"
    >
      <div class="pipeline-stage__node">
        <span class="pipeline-stage__icon">{{ stageIcons[stage.key] || '⚙' }}</span>
      </div>
      <div class="pipeline-stage__info">
        <span class="pipeline-stage__name">{{ stage.label }}</span>
        <span class="pipeline-stage__meta">
          <template v-if="getStageStatus(stage.key) === 'completed'">
            {{ formatMs(getStageDuration(stage.key)) }}
          </template>
          <template v-else-if="getStageStatus(stage.key) === 'running'">
            运行中...
          </template>
          <template v-else-if="getStageStatus(stage.key) === 'failed'">
            失败
          </template>
          <template v-else>
            等待中
          </template>
        </span>
      </div>
      <div v-if="idx < stages.length - 1" class="pipeline-stage__connector" />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { MiningRunStage } from '@/types'

const props = defineProps<{
  stageEvents: MiningRunStage[]
}>()

const stages = [
  { key: 'ingest', label: 'Ingest' },
  { key: 'parse', label: 'Parse' },
  { key: 'segment', label: 'Segment' },
  { key: 'enrich', label: 'Enrich' },
  { key: 'relations', label: 'Relations' },
  { key: 'discourse', label: 'Discourse' },
  { key: 'units', label: 'Retrieval Units' },
  { key: 'snapshot', label: 'Snapshot' },
  { key: 'build', label: 'Build' },
]

const stageIcons: Record<string, string> = {
  ingest: '📂', parse: '🔍', segment: '✂️', enrich: '🧠',
  relations: '🔗', discourse: '💬', units: '🔎', snapshot: '📸', build: '📦',
}

function findStage(key: string): MiningRunStage | undefined {
  return props.stageEvents.find(s => s.stage === key)
}

function getStageStatus(key: string): string {
  const s = findStage(key)
  return s?.status || 'pending'
}

function getStageDuration(key: string): number {
  const s = findStage(key)
  return s?.duration_ms || 0
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
</script>

<style scoped>
.pipeline-flow {
  display: flex;
  align-items: flex-start;
  overflow-x: auto;
  padding: 8px 0;
  gap: 0;
}

.pipeline-stage {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.pipeline-stage__node {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid var(--kb-border);
  background: var(--kb-bg-card);
  transition: all var(--kb-duration) var(--kb-ease);
}

.pipeline-stage--completed .pipeline-stage__node {
  border-color: var(--kb-success);
  background: var(--kb-success-soft);
}

.pipeline-stage--running .pipeline-stage__node {
  border-color: var(--kb-warning);
  background: var(--kb-warning-soft);
  animation: pulse-border 1.5s ease-in-out infinite;
}

.pipeline-stage--failed .pipeline-stage__node {
  border-color: var(--kb-danger);
  background: var(--kb-danger-soft);
}

.pipeline-stage__icon {
  font-size: 16px;
}

.pipeline-stage__info {
  display: flex;
  flex-direction: column;
  margin-left: 8px;
  min-width: 70px;
}

.pipeline-stage__name {
  font-size: 12px;
  font-weight: 600;
  color: var(--kb-text-primary);
}

.pipeline-stage__meta {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.pipeline-stage--completed .pipeline-stage__meta { color: var(--kb-success); }
.pipeline-stage--running .pipeline-stage__meta { color: var(--kb-warning); }
.pipeline-stage--failed .pipeline-stage__meta { color: var(--kb-danger); }

.pipeline-stage__connector {
  width: 24px;
  height: 2px;
  background: var(--kb-border);
  margin: 0 4px;
  align-self: center;
  flex-shrink: 0;
}

.pipeline-stage--completed + .pipeline-stage .pipeline-stage__connector,
.pipeline-stage--completed ~ .pipeline-stage__connector {
  background: var(--kb-success);
}

@keyframes pulse-border {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.3); }
  50% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); }
}
</style>
