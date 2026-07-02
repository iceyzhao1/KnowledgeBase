const PERCENT_METRICS = new Set([
  'retrieval.evidence_coverage',
  'retrieval.hit_at_k',
  'retrieval.mrr',
  'retrieval.recall_at_k',
  'retrieval.segment_resolve_rate',
  'retrieval.gold_phrase_coverage_at_k',
  'retrieval.gold_evidence_similarity_at_k',
  'e2e.citation_accuracy',
  'e2e.faithfulness',
  'e2e.key_point_coverage',
  'e2e.refusal_accuracy',
  'e2e.answer_correctness',
])

const METRIC_DEFINITIONS = {
  'retrieval.hit_at_k': {
    label: '检索命中率',
    testType: '检索测试',
    group: 'retrieval',
    description: '系统返回的前几条证据里，是否至少包含一条标准答案需要的材料。',
    interpretation: '越高越好。低分表示测试问题问到了知识库内容，但系统没有把关键材料找出来。',
  },
  'retrieval.recall_at_k': {
    label: '证据召回率',
    testType: '检索测试',
    group: 'retrieval',
    description: '标准答案要求的证据中，有多少被系统在前几条结果里找到了。',
    interpretation: '越高越好。低分表示材料找得不全，后续回答可能遗漏关键依据。',
  },
  'retrieval.mrr': {
    label: '首条正确证据排名',
    testType: '检索测试',
    group: 'retrieval',
    description: '第一条正确证据排得越靠前，分数越高。',
    interpretation: '越高越好。低分表示虽然找到了材料，但排序靠后，模型或测试人员不容易优先看到。',
  },
  'retrieval.evidence_coverage': {
    label: '证据覆盖度',
    testType: '检索测试',
    group: 'retrieval',
    description: '本次证据包是否覆盖回答所需的关键材料。',
    interpretation: '越高越好。低分表示回答依据不足，需要检查知识库、召回策略或样本标注。',
  },
  'e2e.faithfulness': {
    label: '回答有证据支撑',
    testType: '端到端回答测试',
    group: 'e2e',
    description: '模型回答中的关键陈述，有多少能被检索到的证据支持。',
    interpretation: '越高越好。低分表示回答可能有编造、过度推断或证据不足。',
  },
  'e2e.key_point_coverage': {
    label: '答案要点覆盖率',
    testType: '端到端回答测试',
    group: 'e2e',
    description: '标准答案中的关键要点，有多少在模型最终回答里出现了。',
    interpretation: '越高越好。低分表示回答不完整，可能漏掉测试人员关心的验收点。',
  },
  'e2e.citation_accuracy': {
    label: '引用准确率',
    testType: '端到端回答测试',
    group: 'e2e',
    description: '回答里标注的引用，是否真的指向支持该回答的证据。',
    interpretation: '越高越好。低于 80% 说明回答可能引用错材料，测试时要重点看引用来源。',
  },
  'e2e.refusal_accuracy': {
    label: '拒答正确率',
    testType: '端到端回答测试',
    group: 'e2e',
    description: '遇到不该回答或资料不足的问题时，系统是否按规则拒答。',
    interpretation: '越高越好。低分表示系统可能回答了不该答的问题，或把可答问题误判成不可答。',
  },
  'e2e.answer_correctness': {
    label: '答案正确性',
    testType: '端到端回答测试',
    group: 'e2e',
    description: '大模型裁判综合标准答案、关键要点和证据后，对最终回答正确性的判断。',
    interpretation: '越高越好。低分表示回答可能缺少关键要点、与标准答案不一致，或没有足够证据支撑。',
  },
  'retrieval.latency': {
    label: '检索耗时',
    testType: '效率与成本',
    group: 'efficiency',
    description: '完成一次检索平均花费的时间，单位为毫秒。',
    interpretation: '越低越好。数值变高时，要关注检索服务、索引规模和网络耗时。',
    unit: 'ms',
  },
  'retrieval.segment_resolve_rate': {
    label: '原文片段解析率',
    testType: '检索诊断',
    group: 'retrieval_diagnostics',
    description: '检索结果中的原文片段引用，有多少能被解析回可读文本。',
    interpretation: '越高越好。低分表示证据包只有 ID 或片段引用，评估和人工复核时可能看不到完整内容。',
  },
  'retrieval.gold_phrase_coverage_at_k': {
    label: '标准短语覆盖率',
    testType: '检索诊断',
    group: 'retrieval_diagnostics',
    description: '标准证据里的关键短语，有多少出现在前 K 条检索结果内容中。',
    interpretation: '越高越好。低分表示检索文本没有覆盖标准证据的关键表达。',
  },
  'retrieval.gold_evidence_similarity_at_k': {
    label: '标准证据相似度',
    testType: '检索诊断',
    group: 'retrieval_diagnostics',
    description: '前 K 条检索结果与标准证据内容的最高文本相似程度。',
    interpretation: '越高越好。低分表示检索结果与标准证据语义距离较远，需要检查召回内容。',
  },
  'efficiency.llm_call_count': {
    label: '大模型判分调用次数',
    testType: '效率与成本',
    group: 'efficiency',
    description: '本次评估中调用大模型作为裁判的次数。',
    interpretation: '用于评估成本和预算消耗，不直接代表质量好坏。',
  },
}

const GROUPS = [
  {
    key: 'retrieval',
    title: '检索测试结果',
    subtitle: '看系统有没有找到该找的证据。',
    metricIds: [
      'retrieval.hit_at_k',
      'retrieval.recall_at_k',
      'retrieval.mrr',
      'retrieval.evidence_coverage',
    ],
  },
  {
    key: 'e2e',
    title: '端到端回答测试结果',
    subtitle: '看最终回答是否可靠、完整，并且引用正确。',
    metricIds: [
      'e2e.faithfulness',
      'e2e.key_point_coverage',
      'e2e.citation_accuracy',
      'e2e.refusal_accuracy',
      'e2e.answer_correctness',
    ],
  },
  {
    key: 'efficiency',
    title: '效率与成本',
    subtitle: '看评估运行耗时和大模型判分消耗。',
    metricIds: ['retrieval.latency', 'efficiency.llm_call_count'],
  },
]

function rawNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function displayMetricValue(metricId, value) {
  const number = rawNumber(value)
  if (number === null) return value == null ? 'N/A' : String(value)
  if (PERCENT_METRICS.has(metricId)) return `${(number * 100).toFixed(1)}%`
  if (METRIC_DEFINITIONS[metricId]?.unit === 'ms') return `${number.toFixed(0)} ms`
  return Number.isInteger(number) ? String(number) : number.toFixed(2)
}

export function metricTone(metricId, value) {
  const number = rawNumber(value)
  if (number === null) return 'info'
  if (metricId === 'retrieval.latency' || metricId === 'efficiency.llm_call_count') return 'info'
  if (number >= 0.9) return 'success'
  if (number >= 0.8) return 'warning'
  return 'danger'
}

export function metricVerdict(metricId, value) {
  const number = rawNumber(value)
  if (number === null) return '无数据'
  if (metricId === 'retrieval.latency') return '记录耗时'
  if (metricId === 'efficiency.llm_call_count') return '记录成本'
  if (number >= 0.9) return '表现良好'
  if (number >= 0.8) return '需要关注'
  return '重点检查'
}

export function explainMetric(metricId, value) {
  const definition = METRIC_DEFINITIONS[metricId] || {
    label: metricId,
    testType: '技术指标',
    group: 'technical',
    description: '该指标暂无业务解释，请联系研发补充指标说明。',
    interpretation: '作为技术明细保留。',
  }
  return {
    ...definition,
    rawId: metricId,
    value,
    displayValue: displayMetricValue(metricId, value),
    tone: metricTone(metricId, value),
    verdict: metricVerdict(metricId, value),
  }
}

export function flattenMetricSummary(summary) {
  const result = {}
  for (const [key, metrics] of Object.entries(summary || {})) {
    if (key.includes('.')) {
      result[key] = metrics
      continue
    }
    if (metrics && typeof metrics === 'object') {
      for (const [metricId, value] of Object.entries(metrics)) {
        result[metricId] = value
      }
    }
  }
  return result
}

export function buildTesterMetricGroups(summary) {
  const flat = flattenMetricSummary(summary)
  return GROUPS.map((group) => ({
    ...group,
    metrics: group.metricIds
      .filter((metricId) => Object.prototype.hasOwnProperty.call(flat, metricId))
      .map((metricId) => explainMetric(metricId, flat[metricId])),
  })).filter((group) => group.metrics.length > 0)
}

export function buildRunInterpretation(summary) {
  const flat = flattenMetricSummary(summary)
  const retrievalGood = ['retrieval.hit_at_k', 'retrieval.recall_at_k', 'retrieval.mrr']
    .every((metricId) => rawNumber(flat[metricId]) !== null && rawNumber(flat[metricId]) >= 0.9)
  const weakE2e = ['e2e.faithfulness', 'e2e.key_point_coverage', 'e2e.citation_accuracy', 'e2e.answer_correctness']
    .filter((metricId) => rawNumber(flat[metricId]) !== null && rawNumber(flat[metricId]) < 0.8)
    .map((metricId) => METRIC_DEFINITIONS[metricId].label)
  const refusal = rawNumber(flat['e2e.refusal_accuracy'])

  const parts = []
  if (retrievalGood) parts.push('检索测试表现稳定，关键证据基本都能找出来。')
  if (weakE2e.length) parts.push(`端到端回答的 ${weakE2e.join('、')} 偏低，需要重点复核回答依据和引用。`)
  if (refusal !== null && refusal >= 0.9) parts.push('拒答行为正确，说明系统能识别不该回答的问题。')
  if (!parts.length) parts.push('本次评估已生成指标，请结合各指标解释查看质量风险。')
  return parts.join('')
}
