# 本体图谱页（功能③）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有"本体版本"页（平铺卡片，没有图）旁边新增一个**只读、实时刷新**的本体图谱页：节点 = 本体类型，箭头 = 关系类型（按 allowed_pairs 从头类型指向尾类型），可叠加显示待审候选。

**Architecture:** 本体图谱的边是**人写的**（relation_types 的 allowed_pairs_json），不是算出来的——所以本页**没有任何 NPMI 重算**，纯读现成接口画图。新增一个纯函数把 `ActiveOntology` + 候选转成图数据，一个 ECharts 有向图组件，一个视图页（轮询刷新 + 只读详情面板 + 候选叠加开关），再挂路由和侧边栏。后端**不动**（`GET /api/ontology/active` 和 `GET /api/ontology/candidates` 已够用）。

**Tech Stack:** Vue 3 + Element Plus + TypeScript + ECharts（`echarts` ^6，已装；新建专用有向图组件，不复用 `ForceGraph.vue`——它不支持箭头/虚线候选/点击事件）。

**设计依据：** [docs/superpowers/specs/2026-06-15-ontology-graph-page-design.md](../specs/2026-06-15-ontology-graph-page-design.md)

---

## 关键现状（已核实，写代码前必读）

- **后端零改动。** 数据全来自两个现成只读接口：
  - `GET /api/ontology/active`（mining.ts 的 `getActiveOntology(domain)`）→ `ActiveOntology { domain, version, node_types[], relation_types[] }`。
  - `GET /api/ontology/candidates`（mining.ts 的 `getOntologyCandidates({domain, status})`）→ `{ items: OntologyCandidate[] }`，默认 `status='proposed'`（待审）。
- 前端类型**已具备**（`kb-ui/src/types/index.ts`），无需新增：
  - `OntologyNodeType { id, name, layer, is_strong, definition?, examples_json?: unknown[] }`
  - `OntologyRelationType { id, name, layer, is_directed, inverse_name?, allowed_pairs_json?: Array<{head;tail}>, definition? }`
  - `ActiveOntology { domain, version, node_types[], relation_types[] }`
  - `OntologyCandidate { id, domain_id, kind:'node_type'|'relation_type', layer, proposed_name, payload_json?, source?, status:'proposed'|..., duplicate_of?... }`
- **关系候选的 payload 形状**：`payload_json` 含 `head_type` / `tail_type`（已在 `graph_write/__init__.py:280` 核实：候选关系落点 `payload={"head_type":..., "tail_type":...}`）。**node_type 候选**的类型名 = `proposed_name`。
- `allowed_pairs_json` 里的 `head`/`tail` 是**类型名**（字符串），和 `node_types[].name` 对得上 → 类型名就是图里节点的 id。
- **一个 relation_type 可能有多对 head/tail** → 画成多条同名有向边（spec §3.2）。
- 路由在 `kb-ui/src/router/index.ts`，挂在 `AppLayout` 的 children 下；侧边栏导航在 `kb-ui/src/components/layout/Sidebar.vue` 的 `navItems` 数组。
- 现有"本体版本"页是 `OntologyView.vue`（路由 `/ontology`，侧边栏"本体版本"）——**本页不动它**，新增一个独立页 `/ontology/graph`，侧边栏加"本体图谱"。
- domain 来自 `useDomainStore().currentDomain`（见 `OntologyView.vue`）。
- **前端无测试框架（无 vitest）**，与功能①计划一致：纯函数靠类型检查（`npm run build` 的 vue-tsc）+ 在浏览器预览里实测验证，不引入新测试运行器（YAGNI）。

## 文件结构

- 新：`kb-ui/src/views/knowledge/ontologyGraph.ts`（纯函数：`ActiveOntology` + 候选 → 图数据；本页唯一有逻辑分量的部分，单独成文件便于阅读/复用）
- 新：`kb-ui/src/components/charts/OntologyDiGraph.vue`（ECharts 有向图组件：按 layer 上色、候选橙色虚线、点击发事件）
- 新：`kb-ui/src/views/knowledge/OntologyGraphView.vue`（视图页：加载 active+候选、候选开关、只读详情面板、轮询刷新、实时/只读标记）
- 改：`kb-ui/src/router/index.ts`（加 `/ontology/graph` 路由）
- 改：`kb-ui/src/components/layout/Sidebar.vue`（`navItems` 加"本体图谱"）

---

## Task 1: 图数据纯函数 `buildOntologyGraph`

把 `ActiveOntology`（+ 可选候选）转成"节点数组 + 边数组"。这是本页唯一的真逻辑：展开 allowed_pairs 成多条边、把候选并进来。单独成纯函数文件，便于看懂和后续复用。

**Files:**
- Create: `kb-ui/src/views/knowledge/ontologyGraph.ts`

- [ ] **Step 1: 写纯函数与类型**

```typescript
// kb-ui/src/views/knowledge/ontologyGraph.ts
// 本体图谱的图数据构造：把 active 本体 + 待审候选转成节点/边。
// 本体的边是人写的（relation_types.allowed_pairs），不是算出来的——这里只做展开，不做任何重算。
import type { ActiveOntology, OntologyCandidate } from '@/types'

export interface OntoGraphNode {
  id: string            // = 类型名（allowed_pairs 里 head/tail 引用的就是类型名）
  name: string
  layer: string         // concept / instance / property
  isStrong: boolean
  isCandidate: boolean  // true=待审候选（橙色虚线）
  definition: string
  examples: string[]
}

export interface OntoGraphEdge {
  id: string            // 关系名 + head->tail，保证多对 head/tail 不撞 id
  source: string        // head 类型名
  target: string        // tail 类型名
  relationName: string
  isDirected: boolean
  isCandidate: boolean
  inverseName: string
  definition: string
}

export interface OntoGraphData {
  nodes: OntoGraphNode[]
  edges: OntoGraphEdge[]
}

function parseExamples(raw: unknown): string[] {
  let ex: unknown = raw
  if (typeof ex === 'string') {
    try { ex = JSON.parse(ex) } catch { ex = [] }
  }
  return Array.isArray(ex) ? ex.map(String).slice(0, 8) : []
}

export function buildOntologyGraph(
  active: ActiveOntology,
  candidates: OntologyCandidate[] = [],
  showCandidates = false,
): OntoGraphData {
  const nodes: OntoGraphNode[] = []
  const nodeIds = new Set<string>()

  // 1) active 节点类型 → 节点
  for (const n of active.node_types) {
    nodes.push({
      id: n.name,
      name: n.name,
      layer: n.layer || 'concept',
      isStrong: !!n.is_strong,
      isCandidate: false,
      definition: n.definition || '',
      examples: parseExamples(n.examples_json),
    })
    nodeIds.add(n.name)
  }

  // 2) active 关系类型 → 边（一个关系多对 head/tail = 多条同名边）
  const edges: OntoGraphEdge[] = []
  for (const r of active.relation_types) {
    const pairs = Array.isArray(r.allowed_pairs_json) ? r.allowed_pairs_json : []
    for (const p of pairs) {
      if (!p?.head || !p?.tail) continue
      edges.push({
        id: `${r.name}:${p.head}->${p.tail}`,
        source: p.head,
        target: p.tail,
        relationName: r.name,
        isDirected: r.is_directed !== false,
        isCandidate: false,
        inverseName: r.inverse_name || '',
        definition: r.definition || '',
      })
    }
  }

  if (!showCandidates) return { nodes, edges }

  // 3) 候选叠加：node_type 候选先进（橙虚线节点），relation_type 候选后进（橙虚线边）
  const proposed = candidates.filter(c => c.status === 'proposed')
  for (const c of proposed) {
    if (c.kind !== 'node_type') continue
    if (nodeIds.has(c.proposed_name)) continue // 已是 active 节点，不重复
    nodes.push({
      id: c.proposed_name,
      name: c.proposed_name,
      layer: c.layer || 'concept',
      isStrong: false,
      isCandidate: true,
      definition: '',
      examples: [],
    })
    nodeIds.add(c.proposed_name)
  }
  for (const c of proposed) {
    if (c.kind !== 'relation_type') continue
    const head = c.payload_json?.head_type as string | undefined
    const tail = c.payload_json?.tail_type as string | undefined
    if (!head || !tail) continue
    if (!nodeIds.has(head) || !nodeIds.has(tail)) continue // 端点类型不在图里，跳过
    edges.push({
      id: `cand:${c.id}`,
      source: head,
      target: tail,
      relationName: c.proposed_name,
      isDirected: true,
      isCandidate: true,
      inverseName: '',
      definition: '',
    })
  }

  return { nodes, edges }
}
```

- [ ] **Step 2: 类型检查通过**

Run: `cd kb-ui && npm run build`
Expected: 编译通过（vue-tsc 无报错）。若其它未完成任务里引用的文件还不存在导致整体 build 失败，可改跑 `npx vue-tsc --noEmit` 单看本文件无类型错。

- [ ] **Step 3: 提交**

```bash
git add kb-ui/src/views/knowledge/ontologyGraph.ts
git commit -m "feat(ui): 本体图谱数据构造纯函数（active+候选 → 节点/边）"
```

---

## Task 2: ECharts 有向图组件 `OntologyDiGraph.vue`

专用有向图组件：按 layer 上色、`is_directed` 决定箭头、候选用橙色虚线、点击节点/边向父组件发事件。不复用 `ForceGraph.vue`（它无箭头、无点击、无逐边虚线）。

**Files:**
- Create: `kb-ui/src/components/charts/OntologyDiGraph.vue`

- [ ] **Step 1: 写组件**

```vue
<!-- kb-ui/src/components/charts/OntologyDiGraph.vue -->
<template>
  <div ref="chartRef" :style="{ width: '100%', height }" />
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { OntoGraphNode, OntoGraphEdge } from '@/views/knowledge/ontologyGraph'

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const props = withDefaults(defineProps<{
  nodes: OntoGraphNode[]
  edges: OntoGraphEdge[]
  height?: string
}>(), { height: '600px' })

const emit = defineEmits<{
  (e: 'node-click', id: string): void
  (e: 'edge-click', id: string): void
}>()

const chartRef = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

// layer → 颜色（concept 蓝 / instance 绿 / property 紫）
const LAYER_COLOR: Record<string, string> = {
  concept: '#0891b2', instance: '#10b981', property: '#8b5cf6',
}
const CANDIDATE_COLOR = '#f59e0b'

function colorOf(layer: string): string {
  return LAYER_COLOR[layer] || LAYER_COLOR.concept
}

function render() {
  if (!chart) return

  const nodes = props.nodes.map(n => ({
    id: n.id,
    name: n.name,
    symbolSize: n.isStrong ? 46 : 36,
    symbol: 'roundRect',
    itemStyle: n.isCandidate
      ? { color: '#fff7ed', borderColor: CANDIDATE_COLOR, borderWidth: 2, borderType: 'dashed' as const }
      : { color: colorOf(n.layer), borderColor: colorOf(n.layer), borderWidth: 1 },
    label: {
      show: true, fontSize: 12,
      color: n.isCandidate ? CANDIDATE_COLOR : '#fff',
      fontWeight: n.isStrong ? 700 : 400 as const,
    },
  }))

  const edges = props.edges.map(e => ({
    id: e.id,
    source: e.source,
    target: e.target,
    symbol: e.isDirected ? ['none', 'arrow'] as [string, string] : ['none', 'none'] as [string, string],
    symbolSize: 8,
    label: { show: true, formatter: e.relationName, fontSize: 10, color: e.isCandidate ? CANDIDATE_COLOR : '#64748b' },
    lineStyle: e.isCandidate
      ? { color: CANDIDATE_COLOR, width: 1.5, type: 'dashed' as const, curveness: 0.15 }
      : { color: '#94a3b8', width: 1.5, curveness: 0.15 },
  }))

  chart.setOption({
    tooltip: {
      trigger: 'item',
      formatter(p: any) {
        if (p.dataType === 'edge') return `${p.data.source} → ${p.data.target}`
        return p.name
      },
    },
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      edgeSymbolSize: 8,
      force: { repulsion: 320, gravity: 0.06, edgeLength: [120, 240], friction: 0.6 },
      emphasis: { focus: 'adjacency' },
      data: nodes,
      links: edges,
    }],
  }, true)
}

function onChartClick(params: any) {
  if (params.dataType === 'node') emit('node-click', params.data.id as string)
  else if (params.dataType === 'edge') emit('edge-click', params.data.id as string)
}

onMounted(() => {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value)
  chart.on('click', onChartClick)
  render()
})

onUnmounted(() => {
  chart?.dispose()
  chart = null
})

watch(() => [props.nodes, props.edges], render, { deep: true })

if (typeof window !== 'undefined') {
  window.addEventListener('resize', () => chart?.resize())
}
</script>
```

- [ ] **Step 2: 类型检查通过**

Run: `cd kb-ui && npm run build`
Expected: 编译通过。

- [ ] **Step 3: 提交**

```bash
git add kb-ui/src/components/charts/OntologyDiGraph.vue
git commit -m "feat(ui): 本体有向图组件（layer 上色/箭头/候选虚线/点击事件）"
```

---

## Task 3: 视图页 `OntologyGraphView.vue`

组装：加载 active + 候选 → 调 `buildOntologyGraph` → 喂 `OntologyDiGraph` → 点击节点/边在右侧只读面板显示详情 → 候选开关 → 轮询刷新（实时）。

**Files:**
- Create: `kb-ui/src/views/knowledge/OntologyGraphView.vue`

- [ ] **Step 1: 写视图页**

```vue
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
```

- [ ] **Step 2: 类型检查通过**

Run: `cd kb-ui && npm run build`
Expected: 编译通过。

- [ ] **Step 3: 提交**

```bash
git add kb-ui/src/views/knowledge/OntologyGraphView.vue
git commit -m "feat(ui): 本体图谱视图页（只读详情面板/候选开关/轮询实时刷新）"
```

---

## Task 4: 挂路由 + 侧边栏导航

让页面可达：`/ontology/graph` 路由 + 侧边栏"本体图谱"入口。

**Files:**
- Modify: `kb-ui/src/router/index.ts:64-68`
- Modify: `kb-ui/src/components/layout/Sidebar.vue:35-54`

- [ ] **Step 1: 加路由**

在 `kb-ui/src/router/index.ts` 的 `ontology` 路由块之后插入：

```typescript
        {
          path: 'ontology',
          name: 'ontology',
          component: () => import('@/views/knowledge/OntologyView.vue'),
        },
        {
          path: 'ontology/graph',
          name: 'ontology-graph',
          component: () => import('@/views/knowledge/OntologyGraphView.vue'),
        },
```

- [ ] **Step 2: 加侧边栏入口**

在 `kb-ui/src/components/layout/Sidebar.vue` 的 import 里补 `Share2` 图标（Element Plus 有 `Share`，本入口复用一个图标即可——用 `Histogram` 或现成 `Share`；为避免与"知识图谱"的 `Share` 重复，导入 `DataLine`）：

```typescript
import {
  Monitor, Management, Search, FolderOpened, Share,
  Cpu, Setting, Collection, Connection, DataLine,
} from '@element-plus/icons-vue'
```

在 `navItems` 的"本体版本"项之后插入：

```typescript
  { path: '/ontology', label: '本体版本', icon: Collection },
  { path: '/ontology/graph', label: '本体图谱', icon: DataLine },
```

注意：`isActive` 用的是 `route.path === path || route.path.startsWith(path + '/')`。`/ontology` 会因 `startsWith('/ontology/')` 在子路由下也点亮——这与现状一致，可接受；如需互斥可后续微调，本计划不处理。

- [ ] **Step 3: 类型检查通过**

Run: `cd kb-ui && npm run build`
Expected: 编译通过。

- [ ] **Step 4: 提交**

```bash
git add kb-ui/src/router/index.ts kb-ui/src/components/layout/Sidebar.vue
git commit -m "feat(ui): 本体图谱页挂路由 /ontology/graph + 侧边栏入口"
```

---

## Task 5: 浏览器实测验证（按设计稿验收要点）

前端无单测框架，靠预览实测。用 preview 工具启动 dev server 跑通验收（spec §8）。

**Files:** 无（仅验证；如发现问题回到对应任务的源文件改）

- [ ] **Step 1: 启动预览**

用 `preview_start`（配置 `kb-ui`，端口 5173；见 `.claude/launch.json`），导航到 `/ontology/graph`。

- [ ] **Step 2: 查错**

`preview_console_logs`（level=error）应无报错；`preview_network` 确认 `/api/ontology/active` 与 `/api/ontology/candidates` 均 200。

- [ ] **Step 3: 验收 active 图**

`preview_snapshot` 确认：所有 active node_types 成节点（按 layer 上色、强/弱标注）、所有 relation_types 按 allowed_pairs 成有向箭头。单击一个节点 → 右侧面板出现定义/示例/出边/入边；单击一条边 → 面板出现 head→tail/方向/反向名/定义。

- [ ] **Step 4: 验收候选叠加**

`preview_click` 打开"显示待审候选"开关 → `preview_snapshot` 确认待审 node_type/relation_type 候选以橙色虚线出现；关掉则隐藏。（若当前 domain 无待审候选，可先跑一次挖掘产生候选，或在评审页确认/驳回前观察。）

- [ ] **Step 5: 验收实时**

页面停留 >5s，确认轮询在跑（`preview_network` 周期性出现 active/candidates 请求）；如条件允许，跑一次挖掘产生新候选，开着候选叠加，确认数秒内新虚线节点/边自动出现。

- [ ] **Step 6: 留证 + 提交（无代码改动则跳过提交）**

`preview_screenshot` 留一张图谱截图给用户看。若验证中改了源文件，按对应任务重新 build 并提交修复。

---

## 非目标（写代码时别越界）

- 不在图上增删改本体（仍走引种 / 审候选流程）——本页**只读**。
- 无 NPMI / 边重算（本体边是人写的，这是与功能①的根本区别）。
- 不动 `OntologyView.vue`（本体版本页）和两道关的既有流程。
- 不引入新前端测试框架。
- 不加后端端点（active + candidates 已够用）。

## 自检（对照 spec §8 验收要点）

- [x] active 全部 node_types 成节点、全部 relation_types 按 allowed_pairs 成有向边、颜色/强弱/方向正确 → Task 1（数据）+ Task 2（渲染）+ Task 5 Step 3。
- [x] 单击节点/边右侧显示定义/示例/连接约束/出入边 → Task 3 面板 + Task 5 Step 3。
- [x] "显示待审候选"开关：橙色虚线候选显隐 → Task 1（候选并入）+ Task 2（虚线样式）+ Task 3（开关）+ Task 5 Step 4。
- [x] 实时：跑挖掘后数秒内自动反映 → Task 3 轮询 + Task 5 Step 5。
