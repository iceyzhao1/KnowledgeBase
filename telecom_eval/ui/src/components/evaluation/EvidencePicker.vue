<template>
  <div class="picker">
    <div class="picker__search">
      <el-input v-model="query" placeholder="输入查询（默认用问题），从检索层取候选原文" clearable @keyup.enter="doSearch">
        <template #append>
          <el-button :loading="loading" @click="doSearch">检索原文</el-button>
        </template>
      </el-input>
    </div>

    <el-alert v-if="searchError" :title="searchError" type="warning" show-icon :closable="false" class="picker__hint" />

    <div v-if="results.length" class="picker__results">
      <div class="picker__results-title">候选段落（{{ results.length }}）—— 勾选关联到标准答案</div>
      <el-card v-for="r in results" :key="r.evidence_id" shadow="never" class="picker__cand">
        <el-checkbox :model-value="isSelected(r.evidence_id)" @change="(v:any) => toggle(r, v)">
          <span class="picker__cand-title">{{ r.title || r.evidence_id }}</span>
          <el-tag v-if="r.score != null" size="small" type="info" class="picker__score">分数 {{ r.score.toFixed(3) }}</el-tag>
        </el-checkbox>
        <div class="picker__cand-src">{{ r.source_id }} · <code>{{ r.evidence_id }}</code></div>
        <div class="picker__cand-text">{{ r.content }}</div>
      </el-card>
    </div>

    <div class="picker__selected">
      <div class="picker__results-title">已关联原文（{{ modelValue.length }}）</div>
      <el-empty v-if="!modelValue.length" description="尚未关联原文" :image-size="60" />
      <el-card v-for="(ev, i) in modelValue" :key="ev.evidence_id || i" shadow="never" class="picker__sel">
        <div class="picker__sel-head">
          <span><strong>{{ ev.title || ev.evidence_id }}</strong> <code>{{ ev.evidence_id }}</code></span>
          <el-button link type="danger" size="small" @click="remove(i)">移除</el-button>
        </div>
        <div class="picker__sel-text">{{ ev.text }}</div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useEvaluationApi } from '@/api/evaluation'
import type { EvidenceRef, PassageCandidate } from '@/types/evaluation'

const props = defineProps<{ modelValue: EvidenceRef[]; defaultQuery?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: EvidenceRef[]): void }>()

const api = useEvaluationApi()
const query = ref(props.defaultQuery || '')
const results = ref<PassageCandidate[]>([])
const loading = ref(false)
const searchError = ref<string | null>(null)

async function doSearch() {
  const q = query.value.trim() || props.defaultQuery || ''
  if (!q) {
    searchError.value = '请输入查询词'
    return
  }
  loading.value = true
  searchError.value = null
  try {
    results.value = await api.searchPassages(q, 10)
    if (!results.value.length) searchError.value = '未取到候选段落（检索服务可能未配置或无结果）'
  } catch (e: unknown) {
    searchError.value = e instanceof Error ? e.message : '检索失败'
  } finally {
    loading.value = false
  }
}

function isSelected(id: string): boolean {
  return props.modelValue.some((e) => e.evidence_id === id)
}

function toggle(cand: PassageCandidate, checked: boolean) {
  if (checked) {
    const prov = (cand.provenance || {}) as Record<string, unknown>
    const ref: EvidenceRef = {
      evidence_id: cand.evidence_id,
      raw_segment_ids: (prov.raw_segment_ids as string[]) || undefined,
      source_id: cand.source_id,
      title: cand.title,
      text: cand.content,
      score: cand.score,
    }
    emit('update:modelValue', [...props.modelValue, ref])
  } else {
    emit('update:modelValue', props.modelValue.filter((e) => e.evidence_id !== cand.evidence_id))
  }
}

function remove(i: number) {
  const next = [...props.modelValue]
  next.splice(i, 1)
  emit('update:modelValue', next)
}
</script>

<style scoped>
.picker__search { margin-bottom: 12px; }
.picker__hint { margin-bottom: 12px; }
.picker__results-title { font-weight: 600; font-size: 13px; margin: 8px 0; }
.picker__cand, .picker__sel { margin-bottom: 8px; }
.picker__cand-title { font-weight: 600; }
.picker__score { margin-left: 8px; }
.picker__cand-src { font-size: 12px; color: var(--kb-text-secondary, #64748b); margin: 4px 0; }
.picker__cand-text { font-size: 13px; line-height: 1.6; color: #334155; max-height: 120px; overflow: auto; }
.picker__selected { margin-top: 16px; }
.picker__sel-head { display: flex; justify-content: space-between; align-items: center; }
.picker__sel-text { font-size: 12px; color: #475569; line-height: 1.6; margin-top: 4px; }
</style>
