import assert from 'node:assert/strict'
import { normalizeParadigms, paradigmSubjectId } from './paradigmCatalog.mjs'

const options = normalizeParadigms({
  paradigms: [
    {
      id: 'pd-1064589e',
      name: 'A1_dense_raw_text',
      description: '主基线：纯向量检索(raw_text)召回上限',
      version: 1,
      url: '/api/v1/paradigm/pd-1064589e/search',
    },
    { id: 'bad-no-name', version: 1 },
  ],
})

assert.equal(options.length, 1)
assert.equal(options[0].label, 'A1_dense_raw_text')
assert.equal(options[0].value, 'A1_dense_raw_text')
assert.equal(options[0].description, '主基线：纯向量检索(raw_text)召回上限')
assert.equal(options[0].url, '/api/v1/paradigm/pd-1064589e/search')
assert.equal(paradigmSubjectId(options[0]), 'A1_dense_raw_text')
