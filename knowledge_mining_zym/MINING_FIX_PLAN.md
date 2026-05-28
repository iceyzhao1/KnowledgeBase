# knowledge_mining_zym 修改计划

日期：2026-05-20

范围：`knowledge_mining_zym` 数据挖掘主链，重点覆盖 snapshot 写入、LLM 轮询、取消、API run_id、质量评估、embedding、stage event 和 partial failure 状态。

## 总体判断

当前问题的共同根因不是单点实现粗糙，而是几个关键事实源没有收口：

- snapshot 既被当作“可共享内容快照”，又被当作“每次运行可写入的产物容器”。
- run 生命周期由 API、runtime DB、后台线程共同推进，但 run_id 和取消信号没有统一入口。
- 质量评估仍保留 SQLite 时代接口，和 PostgreSQL v3 主链脱节。
- LLM/embedding 作为外部依赖被设计成“失败不阻塞”，但缺少 bounded timeout、可观测状态和质量门。

修改顺序应先保运行正确性，再收质量门，最后清理可观测性和文档。

## Phase 1：修复运行正确性

### 1. 共享 snapshot 写入冲突

涉及文件：

- `knowledge_mining_zym/mining/snapshot/__init__.py`
- `knowledge_mining_zym/mining/jobs/run.py`
- `knowledge_mining_zym/mining/infra/db.py`
- `databases/asset_core/schemas/002_asset_core_postgresql.sql`
- 相关测试：`knowledge_mining_zym/tests/test_incremental_run.py`、新增 snapshot 复用测试

问题解释：

`select_or_create_snapshot()` 会按 `normalized_content_hash` 复用已有 snapshot。之后 `run.py` 无条件向该 `snapshot_id` 插入 raw segments、relations、retrieval units。数据库约束里 `asset_raw_segments` 和 `asset_retrieval_units` 分别有 `(document_snapshot_id, segment_key)`、`(document_snapshot_id, unit_key)` 唯一约束。

这会导致两类问题：

- 同一文档回滚到旧内容时，旧 snapshot 已有同名 segment/unit，再插入会冲突。
- 不同 document_key 但内容完全一致时，共享 snapshot 会被写入多个 document_key 派生的 segment_key/unit_key，破坏“内容快照”的唯一语义。

根因：

系统把 snapshot 设计成内容共享层，但 segment_key/unit_key 仍包含 document_key，写入逻辑又没有判断 snapshot 是否已有完整派生产物。

修改方案：

1. 在 `select_or_create_snapshot()` 返回值中增加 `snapshot_existed` 或在 `run.py` 中查询该 snapshot 是否已有 segments。
2. 对已存在且已 materialized 的 snapshot，不再重复写 segments/relations/units/embeddings，只创建新的 document link 和 build snapshot 决策。
3. 如果 snapshot 存在但派生产物不完整，进入 repair 模式：先按 snapshot 清理该 snapshot 的 segments/relations/units/embeddings，再重建；这个模式必须显式记录 stage event。
4. 调整 retrieval unit 的 provenance：共享 snapshot 下 source refs 应指向 raw segment id，不再依赖 document_key 唯一性表达来源；document link 负责表达“哪个文档引用了这个内容快照”。
5. 增加回归测试：
   - 同一文档 A -> B -> A 回滚不报唯一约束。
   - 两个不同路径文件内容相同，只生成一份 snapshot 派生产物，两个 document link 指向同一 snapshot。
   - repair 模式能补齐缺失 units。

影响：

- 正向影响：消除增量/回滚/重复内容场景下的写入失败和资产污染。
- 兼容风险：如果 Serving 侧假设 unit_key 中的 document_key 就是唯一来源，需要改为通过 snapshot link 查询来源文档。
- 数据迁移：历史库里可能已经有同一 snapshot 下混入多个 document_key 的 segment_key，需要写一次审计 SQL，决定清理或重建。

优先级：P0。

### 2. LLM 批量轮询无总超时

涉及文件：

- `knowledge_mining_zym/mining/infra/llm_client.py`
- `knowledge_mining_zym/mining/stages/enrich/__init__.py`
- `knowledge_mining_zym/mining/stages/retrieval_units/__init__.py`
- `knowledge_mining_zym/mining/stages/relations/__init__.py`
- `knowledge_mining_zym/mining/infra/mining_config.py`
- 相关测试：`knowledge_mining_zym/tests/test_batch_polling.py`

问题解释：

`LlmClient.poll_all()` 没有 timeout，任何 task 一直处于 queued/running 或服务临时不可达，调用方都会无限等待。它影响 enrich、contextual retrieval、question generation、discourse relation 四条链。

根因：

代码把 LLM supervisor 当成最终会收敛的可靠系统，但挖掘主链没有自己的 deadline 和降级策略。

修改方案：

1. 给 `poll_all()` 增加参数：
   - `timeout_seconds`
   - `max_consecutive_errors`
   - `cancel_checker: Callable[[], bool] | None`
2. 超时后返回已完成结果，并把未完成 task_id 写入 warning 日志和 stage metadata。
3. 各 LLM stage 从 `MiningConfig` 读取默认 timeout，比如：
   - `MINING_LLM_POLL_TIMEOUT_SECONDS=300`
   - `MINING_LLM_POLL_INTERVAL_SECONDS=1`
4. 对超时结果执行现有降级策略：
   - enrich：未返回的 segment 保持原样。
   - question generation：该 segment 不生成 generated_question。
   - contextual retrieval：该 segment 不添加 LLM context。
   - discourse relations：该 window 不生成 discourse relation。
5. 测试增加：
   - 一部分 task succeeded、一部分一直 running，最终返回 succeeded 部分。
   - status API 连续异常超过阈值后退出。
   - timeout 不导致整个 run hang。

影响：

- 正向影响：外部 LLM 故障不会拖死 mining run。
- 质量影响：超时场景下 generated_question、context、relation 数量会下降，必须在 run/build metadata 中显式暴露。
- 运维影响：需要给 UI 增加“LLM partial timeout”提示，否则用户只看到产物变少。

优先级：P0。

### 3. 取消机制不完整

涉及文件：

- `knowledge_mining_zym/mining/jobs/run.py`
- `knowledge_mining_zym/mining/pipeline.py`
- `knowledge_mining_zym/mining/infra/llm_client.py`
- `knowledge_mining_zym/mining/api/routes/runs.py`

问题解释：

当前 `_check_cancelled()` 只在 pipeline 开始前检查。进入 streaming pipeline 后，worker、LLM poll、embedding、DB 写入循环都不会持续检查取消状态。API 把 run 状态改为 `cancelled` 后，后台线程仍可能继续写数据。

根因：

取消被实现成 DB 状态字段，而不是贯穿 pipeline 的 cooperative cancellation token。

修改方案：

1. 定义 `CancellationToken` 或轻量 `cancel_checker()`：
   - 每次查询 `mining_runs.status`。
   - 可加本地 TTL cache，避免每个 segment 都打 DB。
2. 在以下位置插入检查：
   - 文档分类循环每个 doc 前。
   - streaming worker 每个 stage 前后。
   - `poll_all()` 每轮轮询前。
   - DB 写入每个文档前。
   - embedding batch 前后。
   - assemble/publish 前。
3. 取消后：
   - 未开始文档保持 `pending` 或标记 `skipped`，需要统一定义。
   - 正在处理文档标记 `failed` 不合适，建议增加或复用 `skipped` 并在 metadata 写 `cancelled=true`。
   - run 最终保持 `cancelled`，不得被后续 `complete_run()` 覆盖。
4. `runs.py cancel_run` 只负责请求取消；后台线程负责最终收尾，避免 API 提前写 `finished_at`。

影响：

- 正向影响：用户取消后系统行为可预期，不继续发布 release。
- DB schema 影响：如果要精确表达文档取消，可能需要扩展 `mining_run_documents.status` 增加 `cancelled`；如果不改 schema，就用 `skipped + metadata_json.cancelled`。
- UI 影响：取消状态需要区分“请求取消中”和“已取消完成”更好，但第一阶段可先保持单一 `cancelled`。

优先级：P0。

### 4. API 创建 run 返回 run_id 不可靠

涉及文件：

- `knowledge_mining_zym/mining/api/routes/runs.py`
- `knowledge_mining_zym/mining/jobs/run.py`
- `knowledge_mining_zym/mining/runtime/__init__.py`

问题解释：

API 启动后台线程后，通过 `ORDER BY started_at DESC LIMIT 1` 查询最新 run 作为本次 run。这个查询不绑定本次请求。并发、旧 run、启动慢、异常启动都会导致返回错误 run_id 或 `pending`。

根因：

run_id 由 `run()` 内部生成，API 层没有创建权，却又要立即返回 run_id。

修改方案：

1. 修改 `run()` 支持可选 `run_id` 参数，由 API 预生成并传入。
2. API 在启动线程前生成 run_id 并立即返回，不再轮询最新 run。
3. 后台线程使用该 run_id 创建 runtime row。
4. 为了避免“API 返回 run_id 但后台还没创建 row”造成前端短暂 404，可选方案：
   - API 先插入 `queued` run，再由 `run()` 接管并更新为 `running`。
   - 或 GET run 对最近创建的 pending run 做兼容。推荐前者。
5. `_run_lock` 目前是进程内锁，只能防同进程并发。若有多进程 uvicorn，需要用 DB advisory lock 或 runtime 表约束做全局互斥。

影响：

- 正向影响：前端、取消、日志、排障都能稳定绑定 run_id。
- API 兼容：响应字段不变，只是 run_id 从“猜测”变成“确定”。
- 实现影响：`run()` 当前会自己生成 `run_id`，改为参数兼容默认行为即可。

优先级：P0。

## Phase 2：质量门与数据合同收口

### 5. Data quality eval 与 PostgreSQL v3 不兼容

涉及文件：

- `knowledge_mining_zym/mining/stages/eval.py`
- `knowledge_mining_zym/mining/stages/publishing.py`
- `knowledge_mining_zym/mining/jobs/run.py`
- `knowledge_mining_zym/mining/infra/db.py`

问题解释：

`run_data_quality_eval()` 仍以 SQLite 文件路径为入口，内部 `AssetCoreDB(asset_db_path)` 已不匹配当前 PG adapter。SQL 里还使用 `json_extract()`，PostgreSQL 不支持。

根因：

v3 切 PostgreSQL 后，评估模块没有随 DB adapter 合同一起迁移，也没有接入 release gate。

修改方案：

1. 把 `run_data_quality_eval(profile, asset_db_path)` 改为 `run_data_quality_eval(profile, asset_db, build_id=None, snapshot_ids=None)`。
2. 所有 SQL 改成 PostgreSQL JSONB：
   - `json_extract(s.metadata_json, '$.content_assessment.is_navigation') = 1`
   - 改为 `(s.metadata_json::jsonb #>> '{content_assessment,is_navigation}')::boolean IS TRUE`
3. 评估范围从“全库”改为“本次 build 的 active snapshots”，避免历史脏数据影响当前发布。
4. 在 `assemble_build` 后、`publish_release` 前接入 hard gate：
   - P0 hard fail：source trace 缺失、LLM provenance 缺 task_id、navigation 生成问题。
   - warning：generated_question 数量为 0、discourse relation 数量为 0。
5. build metadata 写入 quality report summary。
6. `publish_on_partial_failure` 不应绕过 hard quality gate，除非新增显式参数 `bypass_quality_gate`，默认禁止。

影响：

- 正向影响：发布出来的 release 不再只保证结构存在，也保证基本可检索质量。
- 兼容风险：历史数据可能过不了新 gate，需要先以 warning 模式跑一次。
- 测试影响：需要 PG 测试 fixture 覆盖质量 SQL。

优先级：P1。

### 6. Embedding 写入静默降级和元数据不准

涉及文件：

- `knowledge_mining_zym/mining/jobs/run.py`
- `knowledge_mining_zym/mining/infra/embedding.py`
- `knowledge_mining_zym/mining/infra/db.py`
- `databases/asset_core/schemas/002_asset_core_postgresql.sql`

问题解释：

当前 embedding provider 固定写 `"zhipu"`，即使实际调用的是 LLM service。`content_hash` 写空。schema 中 native vector 列是 `vector(1024)`，但配置允许传入任意 dimensions。触发器转换失败会吞异常，把 `embedding_vector_vec` 置空。

根因：

embedding 既支持直连 provider，又支持 llm_service 代理，但 DB 元数据没有表达真实 provider；schema 又假设固定 1024 维。

修改方案：

1. 给 embedding generator 协议增加属性：
   - `provider_name`
   - `model_name`
   - `dimensions`
2. `ZhipuEmbeddingGenerator.provider_name = "zhipu"`。
3. `LLMServiceEmbeddingGenerator.provider_name = "llm_service"`，如果 llm_service 返回真实 provider，则透传。
4. `content_hash` 使用被 embed 文本的 hash，便于去重和审计。
5. 维度策略二选一：
   - 保守方案：配置强制 `embedding_dimensions == 1024`，否则不写 native vector，只写 text JSON 并 warning。
   - 完整方案：按不同维度拆表或去掉固定 vector 列，Serving 侧按模型维度选择索引。短期建议保守方案。
6. trigger 不应完全吞掉向量转换失败；至少写入 `metadata_json.vector_parse_error` 或让插入失败，由上层记录 embedding failed。

影响：

- 正向影响：向量检索可用性可验证，不再“看起来写了 embedding，实际没有索引向量”。
- 兼容风险：如果现有数据里 `embedding_vector_vec` 已大量为空，需要重建 embedding。
- Serving 影响：如果 Serving 使用 `embedding_provider='zhipu'` 过滤，需要同步支持 `llm_service` 或真实 provider 字段。

优先级：P1。

## Phase 3：可观测性与状态语义

### 7. Stage event 语义重复

涉及文件：

- `knowledge_mining_zym/mining/pipeline.py`
- `knowledge_mining_zym/mining/jobs/run.py`
- `knowledge_mining_zym/mining/runtime/__init__.py`

问题解释：

streaming worker 已经记录 `parse/segment/enrich/discourse_relations/build_retrieval_units` 的计算阶段事件。之后 DB 写入阶段又记录 `segment/build_retrieval_units` 等同名事件。这样同一个 stage name 同时代表“计算”和“落库”，耗时、失败归因都会混乱。

根因：

stage event 没有区分 logical stage、compute stage、persist stage。

修改方案：

1. 保留计算阶段事件名：
   - `parse`
   - `segment`
   - `enrich`
   - `discourse_relations`
   - `build_retrieval_units`
2. DB 落库阶段改名或合并：
   - `persist_segments`
   - `persist_relations`
   - `persist_retrieval_units`
   - `persist_embeddings`
3. 如果 schema CHECK 不允许新增 stage，需要先扩 `VALID_STAGE_NAMES` 和 PostgreSQL CHECK constraint。
4. API timeline 展示时可把 compute/persist 分组。

影响：

- 正向影响：排障可以明确知道是 LLM/解析慢，还是 DB 写入慢。
- Schema 影响：新增 stage 枚举需要迁移。
- 测试影响：`test_stage_events.py` 需要更新期望。

优先级：P2。

### 8. Partial failure 被标成 completed

涉及文件：

- `knowledge_mining_zym/mining/jobs/run.py`
- `knowledge_mining_zym/mining/contracts/models.py`
- `databases/mining_runtime/schemas/002_mining_runtime_postgresql.sql`
- `knowledge_mining_zym/mining/api/routes/runs.py`

问题解释：

部分文档失败但有文档成功时，run status 仍写 `completed`，只在 metadata 中写 `has_failures=true`。调度、告警、UI 如果只看 status，会把带失败的 run 当成完全成功。

根因：

run status 缺少 `completed_with_failures` 或等价状态，导致失败信息被塞进 metadata。

修改方案：

1. 增加状态 `completed_with_failures`，并更新：
   - Python constants。
   - PostgreSQL CHECK constraint。
   - API 文档和 UI 展示。
2. 状态规则：
   - `failed_count == 0` -> `completed`
   - `failed_count > 0 and committed_count > 0` -> `completed_with_failures`
   - `failed_count > 0 and committed_count == 0` -> `failed`
   - cancelled 不允许被覆盖。
3. `publish_on_partial_failure` 只控制是否发布 release，不控制 run status。

影响：

- 正向影响：调度和告警能正确识别“部分失败”。
- 兼容风险：已有查询只枚举 `completed/failed` 的地方需要补状态。
- 发布影响：允许部分失败发布时，release notes 应明确包含失败文档数。

优先级：P2。

## 建议实施顺序

1. API run_id 确定化。
2. LLM poll timeout。
3. cancellation token 贯穿主链。
4. snapshot 复用/派生产物写入合同重构。
5. PostgreSQL data quality eval 迁移并先 warning 接入。
6. quality gate 从 warning 切 hard gate。
7. embedding 元数据和维度收口。
8. stage event 重命名和 partial failure 状态扩展。
9. 更新 README/architecture，把已经修复或过期的成熟度结论删掉。

## 验证计划

环境前提：

当前机器没有可用 Python 解释器，`python` 不存在，`py -0p` 显示 `No Installed Pythons Found`。执行验证前需要先安装或配置项目 Python 运行环境。

最小验证集：

1. 静态验证：
   - `python -m compileall -q knowledge_mining_zym`
2. 单元测试：
   - `python -m pytest knowledge_mining_zym/tests/test_batch_polling.py -q`
   - `python -m pytest knowledge_mining_zym/tests/test_stage_events.py -q`
   - `python -m pytest knowledge_mining_zym/tests/test_incremental_run.py -q`
3. 新增回归测试：
   - snapshot rollback test。
   - duplicate content shared snapshot test。
   - LLM poll timeout partial result test。
   - API create_run returns pre-generated run_id test。
   - cancellation stops before publish test。
   - PG data quality eval SQL test。
4. 集成测试：
   - 用小型 markdown corpus 跑一次 full run。
   - 再改一个文件跑 incremental run。
   - 再恢复旧内容跑 rollback run。
   - 验证 active release、build snapshots、retrieval units、embeddings、stage events 一致。

## 不建议现在做的事

- 不要继续增加新的 retrieval unit 类型。
- 不要继续加 LLM stage。
- 不要先优化 UI 展示。
- 不要把质量问题继续藏在 metadata warning 里。

先把事实源和失败边界收口，否则新增能力只会放大错误产物和排障成本。
