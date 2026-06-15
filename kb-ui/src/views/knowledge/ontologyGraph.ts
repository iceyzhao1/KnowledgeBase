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
