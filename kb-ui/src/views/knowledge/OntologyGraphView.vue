<!-- kb-ui/src/views/knowledge/OntologyGraphView.vue -->
<template>
  <div class="og-view">
    <div class="og-view__header">
      <div class="og-view__header-left">
        <h2 class="og-view__title">本体图谱</h2>
        <span class="og-pill og-pill--live"><span class="og-dot" />实时</span>
        <span class="og-pill og-pill--ro">只读</span>
        <span class="og-view__sub" v-if="active.version">
          v{{ active.version.version_no }} · {{ active.node_types.length }} 类型 · {{ graph.edges.length }} 边
        </span>
        <span class="og-view__sub" v-else>尚未引种本体</span>
      </div>
      <div class="og-view__actions">
        <el-switch v-model="showCandidates" active-text="显示待审候选" inline-prompt />
        <el-button @click="loadAll" :loading="loading"><el-icon><Refresh /></el-icon></el-button>
      </div>
    </div>

    <div class="og-body">
      <div class="og-card og-graph">
        <OntologyDiGraph
          :nodes="graph.nodes" :edges="graph.edges"
          @node-click="onNodeClick" @edge-click="onEdgeClick"
        />
      </div>

      <div class="og-card og-panel">
        <!-- 节点详情 -->
        <template v-if="selectedNode">
          <div class="og-panel__head">
            <span class="og-panel__name">{{ selectedNode.name }}</span>
            <span class="og-tag">{{ layerLabel(selectedNode.layer) }}</span>
            <span class="og-tag" :class="{ 'og-tag--strong': selectedNode.isStrong }">
              {{ selectedNode.isStrong ? '强' : '弱' }}
            </span>
            <span v-if="selectedNode.isCandidate" class="og-tag og-tag--cand">待审候选</span>
          </div>
          <div class="og-panel__sec" v-if="selectedNode.definition">
            <div class="og-panel__label">定义</div>
            <div class="og-panel__text">{{ selectedNode.definition }}</div>
          </div>
          <div class="og-panel__sec" v-if="selectedNode.examples.length">
            <div class="og-panel__label">示例</div>
            <div class="og-chips">
              <span v-for="(ex, i) in selectedNode.examples" :key="i" class="og-chip">{{ ex }}</span>
            </div>
          </div>
          <div class="og-panel__sec" v-if="outEdges.length">
            <div class="og-panel__label">出边（作为头类型）</div>
            <div v-for="e in outEdges" :key="e.id" class="og-edge-row">
              <span class="og-rel">{{ e.relationName }}</span> → {{ e.target }}
            </div>
          </div>
          <div class="og-panel__sec" v-if="inEdges.length">
            <div class="og-panel__label">入边（作为尾类型）</div>
            <div v-for="e in inEdges" :key="e.id" class="og-edge-row">
              {{ e.source }} <span class="og-rel">{{ e.relationName }}</span> →
            </div>
          </div>
        </template>

        <!-- 边详情 -->
        <template v-else-if="selectedEdge">
          <div class="og-panel__head">
            <span class="og-panel__name">{{ selectedEdge.relationName }}</span>
            <span v-if="selectedEdge.isCandidate" class="og-tag og-tag--cand">待审候选</span>
          </div>
          <div class="og-panel__sec">
            <div class="og-panel__label">连接</div>
            <div class="og-panel__text">
              {{ selectedEdge.source }} {{ selectedEdge.isDirected ? '→' : '—' }} {{ selectedEdge.target }}
            </div>
          </div>
          <div class="og-panel__sec">
            <div class="og-panel__label">方向</div>
            <div class="og-panel__text">{{ selectedEdge.isDirected ? '有向' : '无向' }}</div>
          </div>
          <div class="og-panel__sec" v-if="selectedEdge.inverseName">
            <div class="og-panel__label">反向名</div>
            <div class="og-panel__text">{{ selectedEdge.inverseName }}</div>
          </div>
          <div class="og-panel__sec" v-if="selectedEdge.definition">
            <div class="og-panel__label">定义</div>
            <div class="og-panel__text">{{ selectedEdge.definition }}</div>
          </div>
        </template>

        <div v-else class="og-panel__empty">单击节点或箭头查看定义、示例、连接约束</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useMiningApi } from '@/api/mining'
import type { ActiveOntology, OntologyCandidate } from '@/types'
import OntologyDiGraph from '@/components/charts/OntologyDiGraph.vue'
import { buildOntologyGraph, type OntoGraphData, type OntoGraphNode, type OntoGraphEdge } from './ontologyGraph'

const domainStore = useDomainStore()
const miningApi = useMiningApi()

const loading = ref(false)
const showCandidates = ref(false)
const active = reactive<ActiveOntology>({ domain: '', version: null, node_types: [], relation_types: [] })
const candidates = ref<OntologyCandidate[]>([])
const graph = ref<OntoGraphData>({ nodes: [], edges: [] })

const selectedNode = ref<OntoGraphNode | null>(null)
const selectedEdge = ref<OntoGraphEdge | null>(null)

const outEdges = computed(() =>
  selectedNode.value ? graph.value.edges.filter(e => e.source === selectedNode.value!.id) : [])
const inEdges = computed(() =>
  selectedNode.value ? graph.value.edges.filter(e => e.target === selectedNode.value!.id) : [])

function rebuild() {
  graph.value = buildOntologyGraph(active, candidates.value, showCandidates.value)
  // 选中项可能已不在新图里，做一次校正
  if (selectedNode.value && !graph.value.nodes.some(n => n.id === selectedNode.value!.id)) selectedNode.value = null
  if (selectedEdge.value && !graph.value.edges.some(e => e.id === selectedEdge.value!.id)) selectedEdge.value = null
}

async function loadAll() {
  loading.value = true
  try {
    const [a, c] = await Promise.all([
      miningApi.getActiveOntology(domainStore.currentDomain),
      miningApi.getOntologyCandidates({ domain: domainStore.currentDomain, status: 'proposed' }),
    ])
    Object.assign(active, a)
    candidates.value = c.items
    rebuild()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function onNodeClick(id: string) {
  selectedEdge.value = null
  selectedNode.value = graph.value.nodes.find(n => n.id === id) || null
}
function onEdgeClick(id: string) {
  selectedNode.value = null
  selectedEdge.value = graph.value.edges.find(e => e.id === id) || null
}

function layerLabel(l: string) {
  return ({ concept: '概念层', instance: '实例层', property: '属性层' } as Record<string, string>)[l] || l
}

// ── 实时：轮询 + 重新聚焦刷新 ──
let timer: ReturnType<typeof setInterval> | null = null
function onFocus() { loadAll() }

onMounted(() => {
  loadAll()
  timer = setInterval(loadAll, 5000)
  window.addEventListener('focus', onFocus)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('focus', onFocus)
})

watch(showCandidates, rebuild)
watch(() => domainStore.currentDomain, loadAll)
</script>

<style scoped>
.og-view { display: flex; flex-direction: column; gap: 14px; height: 100%; }
.og-view__header { display: flex; align-items: center; justify-content: space-between; }
.og-view__header-left { display: flex; align-items: center; gap: 10px; }
.og-view__title { font-size: 16px; font-weight: 650; color: var(--kb-text-primary); margin: 0; letter-spacing: -0.2px; }
.og-view__sub { font-size: 12px; color: var(--kb-text-tertiary); }
.og-view__actions { display: flex; gap: 10px; align-items: center; }
.og-pill { font-size: 11px; padding: 2px 9px; border-radius: 11px; display: inline-flex; align-items: center; gap: 5px; }
.og-pill--live { background: #ecfdf5; color: #059669; }
.og-pill--ro { background: var(--kb-border-light); color: var(--kb-text-secondary); }
.og-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981; }
.og-body { display: grid; grid-template-columns: 1fr 320px; gap: 14px; flex: 1; min-height: 0; }
.og-card { background: var(--kb-bg-card); border-radius: var(--kb-radius); box-shadow: var(--kb-shadow-card); border: 1px solid var(--kb-border-light); padding: 12px; }
.og-graph { min-height: 600px; }
.og-panel { display: flex; flex-direction: column; gap: 12px; overflow: auto; }
.og-panel__head { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.og-panel__name { font-size: 14px; font-weight: 650; color: var(--kb-text-primary); }
.og-tag { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: var(--kb-border-light); color: var(--kb-text-secondary); }
.og-tag--strong { background: var(--kb-accent-soft); color: var(--kb-accent); }
.og-tag--cand { background: #fff7ed; color: #f59e0b; border: 1px solid #fed7aa; }
.og-panel__sec { display: flex; flex-direction: column; gap: 5px; }
.og-panel__label { font-size: 11px; color: var(--kb-text-tertiary); }
.og-panel__text { font-size: 12px; color: var(--kb-text-secondary); line-height: 1.5; }
.og-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.og-chip { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: var(--kb-accent-soft); color: var(--kb-accent); }
.og-edge-row { font-size: 12px; color: var(--kb-text-secondary); padding: 2px 0; }
.og-rel { color: var(--kb-accent); }
.og-panel__empty { font-size: 12px; color: var(--kb-text-tertiary); text-align: center; padding-top: 40px; }
</style>
