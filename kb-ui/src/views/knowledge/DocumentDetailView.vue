<template>
  <div class="doc-detail" v-loading="loading">
    <!-- Back -->
    <div class="doc-detail__back">
      <el-button text @click="$router.push('/knowledge')">
        <el-icon><ArrowLeft /></el-icon> 返回列表
      </el-button>
    </div>

    <!-- Meta -->
    <div class="doc-detail__meta" v-if="document">
      <h3 class="doc-detail__name">{{ document.document_name }}</h3>
      <div class="doc-detail__tags">
        <span class="type-badge">{{ document.document_type }}</span>
        <span class="doc-detail__date">创建于 {{ formatTime(document.created_at) }}</span>
      </div>
    </div>

    <!-- Tabs -->
    <el-tabs v-model="activeTab" v-if="document" class="doc-detail__tabs">
      <!-- Segments Tab -->
      <el-tab-pane name="segments">
        <template #label>
          段落 <span class="tab-count">{{ segments.length }}</span>
        </template>
        <div class="card-list">
          <div v-for="seg in segments" :key="seg.id" class="segment-card">
            <div class="segment-card__header">
              <span class="segment-card__idx">#{{ seg.segment_index }}</span>
              <span class="segment-card__type">{{ seg.block_type }}</span>
              <span class="segment-card__role" v-if="seg.semantic_role">{{ seg.semantic_role }}</span>
              <span class="segment-card__tokens">{{ seg.token_count }} tokens</span>
              <span class="segment-card__section" v-if="seg.section_title">{{ seg.section_title }}</span>
            </div>
            <p class="segment-card__text">{{ seg.raw_text }}</p>
          </div>
          <EmptyState v-if="!segments.length" text="无段落数据" />
        </div>
      </el-tab-pane>

      <!-- Units Tab -->
      <el-tab-pane name="units">
        <template #label>
          检索单元 <span class="tab-count">{{ units.length }}</span>
        </template>
        <div class="card-list">
          <div v-for="unit in units" :key="unit.id" class="unit-card">
            <div class="unit-card__header">
              <span class="unit-card__type">{{ unitTypeLabel(unit.unit_type) }}</span>
              <span class="unit-card__weight" v-if="unit.weight !== 1">w={{ unit.weight }}</span>
            </div>
            <div class="unit-card__title" v-if="unit.title">{{ unit.title }}</div>
            <p class="unit-card__text">{{ unit.text }}</p>
          </div>
          <EmptyState v-if="!units.length" text="无检索单元数据" />
        </div>
      </el-tab-pane>

      <!-- Relations Tab -->
      <el-tab-pane name="relations">
        <template #label>
          关系 <span class="tab-count">{{ relations.length }}</span>
        </template>
        <div class="relation-list">
          <div v-for="rel in relations" :key="rel.id" class="relation-row">
            <span class="relation-row__id">{{ rel.source_segment_id.slice(0, 6) }}</span>
            <span class="relation-row__type">{{ rel.relation_type }}</span>
            <span class="relation-row__id">{{ rel.target_segment_id.slice(0, 6) }}</span>
            <span class="relation-row__conf">conf={{ rel.confidence.toFixed(2) }}</span>
          </div>
          <EmptyState v-if="!relations.length" text="无关系数据" />
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useDomainStore } from '@/stores/domain'
import { useMiningApi } from '@/api/mining'
import type { KnowledgeDocument, KnowledgeSegment, KnowledgeUnit, KnowledgeRelation } from '@/types'
import EmptyState from '@/components/common/EmptyState.vue'

const props = defineProps<{ docId: string }>()
const domainStore = useDomainStore()
const miningApi = useMiningApi()

const loading = ref(false)
const document = ref<KnowledgeDocument | null>(null)
const segments = ref<KnowledgeSegment[]>([])
const units = ref<KnowledgeUnit[]>([])
const relations = ref<KnowledgeRelation[]>([])
const activeTab = ref('segments')

function unitTypeLabel(type: string) {
  const map: Record<string, string> = {
    raw_text: '原始文本', contextual_text: '上下文', summary: '摘要',
    generated_question: '生成问题', entity_card: '实体卡片',
  }
  return map[type] || type
}

function formatTime(t: string) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

async function loadData() {
  loading.value = true
  try {
    const [doc, segs, unts] = await Promise.all([
      miningApi.getDocument(props.docId),
      miningApi.getDocumentSegments(props.docId),
      miningApi.getDocumentUnits(props.docId),
    ])
    document.value = doc
    segments.value = segs
    units.value = unts
    // Relations are global, skip for now
    relations.value = []
  } catch {
    document.value = null
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
watch(() => domainStore.currentDomain, loadData)
</script>

<style scoped>
.doc-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.doc-detail__back { margin-bottom: 0; }

.doc-detail__meta {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
  border: 1px solid var(--kb-border-light);
}

.doc-detail__name {
  font-size: 17px;
  font-weight: 650;
  color: var(--kb-text-primary);
  margin: 0 0 8px;
}

.doc-detail__tags {
  display: flex;
  align-items: center;
  gap: 10px;
}

.type-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
  font-weight: 600;
}

.doc-detail__date {
  font-size: 12px;
  color: var(--kb-text-tertiary);
}

.doc-detail__tabs :deep(.el-tabs__header) {
  margin-bottom: 14px;
}

.tab-count {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  margin-left: 4px;
}

/* Card list */
.card-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Segment card */
.segment-card {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  padding: 12px 16px;
}

.segment-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.segment-card__idx {
  font-size: 11px;
  font-weight: 700;
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.segment-card__type {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
  font-weight: 600;
}

.segment-card__role {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--kb-warning-soft);
  color: var(--kb-warning);
  font-weight: 600;
}

.segment-card__tokens {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.segment-card__section {
  font-size: 11px;
  color: var(--kb-text-secondary);
}

.segment-card__text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--kb-text-primary);
  margin: 0;
  white-space: pre-wrap;
}

/* Unit card */
.unit-card {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  padding: 12px 16px;
}

.unit-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.unit-card__type {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--kb-success-soft);
  color: var(--kb-success);
  font-weight: 600;
}

.unit-card__weight {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.unit-card__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-primary);
  margin-bottom: 4px;
}

.unit-card__text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--kb-text-secondary);
  margin: 0;
  white-space: pre-wrap;
}

/* Relation list */
.relation-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.relation-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  font-size: 12px;
}

.relation-row__id {
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  color: var(--kb-accent);
  font-weight: 500;
}

.relation-row__type {
  color: var(--kb-text-primary);
  font-weight: 600;
  background: var(--kb-border-light);
  padding: 1px 8px;
  border-radius: 3px;
}

.relation-row__conf {
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}
</style>
