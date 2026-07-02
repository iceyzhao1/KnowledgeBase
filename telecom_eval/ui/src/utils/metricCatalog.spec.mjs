import assert from 'node:assert/strict'
import { buildTesterMetricGroups, explainMetric } from './metricCatalog.mjs'

const citation = explainMetric('e2e.citation_accuracy', 0.667)

assert.equal(citation.label, '引用准确率')
assert.equal(citation.rawId, 'e2e.citation_accuracy')
assert.equal(citation.testType, '端到端回答测试')
assert.match(citation.description, /引用/)
assert.match(citation.interpretation, /越高越好/)
assert.equal(citation.displayValue, '66.7%')

const groups = buildTesterMetricGroups({
  'retrieval.hit_at_k': 1,
  'e2e.citation_accuracy': 0.667,
  'efficiency.llm_call_count': 3,
})

assert.deepEqual(
  groups.map((group) => group.title),
  ['检索测试结果', '端到端回答测试结果', '效率与成本']
)
assert.equal(groups[0].metrics[0].label, '检索命中率')
assert.equal(groups[1].metrics[0].label, '引用准确率')
assert.equal(groups[2].metrics[0].displayValue, '3')
