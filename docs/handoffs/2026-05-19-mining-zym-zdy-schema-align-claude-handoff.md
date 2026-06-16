# mining_zym 对齐 agent_serving_zdy 的 domain schema 适配 — Claude Handoff

- 任务：TASK-20260421-v11-knowledge-mining
- 日期：2026-05-19
- 作者：Claude
- 关联计划：`docs/plans/2026-05-19-mining-zym-zdy-schema-align-impl-plan.md`

## 1. 任务目标

让 mining_zym 写出的 batch / build / release 与 `agent_serving_zdy/src/main/resources/db/migrate_v1_to_zdy.sql` 迁移后的 schema 严格一致，并真正按 `(domain, channel)` 隔离 active release。

## 2. 本次实现范围

- DDL：`databases/asset_core/schemas/002_asset_core_postgresql.sql` 加 `domain` 列、`channel DEFAULT 'prod'`、新增 `(domain, channel)` 索引；旧 `(channel)`-only 索引在底部 upgrade 块里 DROP，配合 ADD COLUMN IF NOT EXISTS 让 `ensure_schema()` 对 v1 现存库幂等升级。
- 适配器 `knowledge_mining_zym/mining/infra/db.py`：`upsert_source_batch` / `insert_build` / `insert_release` 加 `domain` 参数；`activate_release` 改为按 `(channel, domain)` 双键退役；`get_active_release` 加 `domain` 参数。
- 调用链：
  - `mining/jobs/run.py` 把 `profile.domain_id` 解为 `domain` 后注入 `upsert_source_batch`、`assemble_build`、`publish_release`。
  - `mining/jobs/run.py::publish()` 通过 `asset_db.get_build(build_id)["domain"]` 回查后再 `publish_release`。
  - `mining/stages/publishing.py::assemble_build`、`publish_release` 形参加 `domain`。
  - `mining/api/routes/builds.py::/api/releases/active` 加 `channel` 与 `domain` query 参数（都默认 `"default"`）。
- 测试：
  - `tests/test_v11_pipeline.py::TestAssetCoreDB.test_active_release_isolated_by_domain` 新增，覆盖跨 domain 共享 channel 不互相退役的回归。
  - `tests/test_v11_pipeline.py::TestEndToEndPipeline.test_publish_after_phase1` 更新查询方式（`run()` 默认 domain 是 `cloud_core_network`，不再是 `default`）。

## 3. 不在本次范围

- `serving_query_logs` 表：由 zdy serving 侧建表写入，mining_zym 不读不写，本次不加 DDL。
- `databases/asset_core/schemas/001_asset_core.sqlite.sql`：mining_zym 已切 PG，sqlite DDL 不同步。
- `/api/builds`、`/api/releases` 列表端点：未加 `domain` 过滤参数，当前依然返回全 domain（list 行为不变，避免破坏 UI）。
- `/api/knowledge/stats` 的 `active_release` 字段：仍是"任意一个 active"，未拆 domain；保留为已知差距。
- mining_ui.py：扫描后未发现直接的 build/release 写入；不动。
- agent_serving_zdy 代码：不在本仓库范围。

## 4. 改动文件清单

- `databases/asset_core/schemas/002_asset_core_postgresql.sql`
- `knowledge_mining_zym/mining/infra/db.py`
- `knowledge_mining_zym/mining/jobs/run.py`
- `knowledge_mining_zym/mining/stages/publishing.py`
- `knowledge_mining_zym/mining/api/routes/builds.py`
- `knowledge_mining_zym/tests/test_v11_pipeline.py`
- 新增：`docs/plans/2026-05-19-mining-zym-zdy-schema-align-impl-plan.md`
- 新增：`docs/handoffs/2026-05-19-mining-zym-zdy-schema-align-claude-handoff.md`（本文档）

## 5. 关键设计决策

### 5.1 DDL 同时支持"全新建库"与"v1 现存库升级"

- CREATE TABLE 内嵌的 `domain TEXT NOT NULL DEFAULT 'default'` 服务全新建库
- 文件末尾的 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 服务 v1 现存库
- 域相关索引（`uq_asset_publish_releases_domain_channel_active` 等）一律放在 ALTER TABLE 之后，避免在还没补列的现存库上创建索引失败
- 旧 `(channel)`-only 索引在末尾 DROP，保证两种入口下最终状态等价于 zdy migrate 结果

### 5.2 domain 单一来源

- run 时：从 `DomainProfile.domain_id` 解析，缺省回退 `"default"`
- publish 单独触发时（`publish()` 函数）：从 `asset_builds.domain` 行回查
- API 入口：query 参数 `domain="default"`
- 内部适配器全部用形参传入，**不**做"从 batch_id / build_id 反查"的隐式补偿

### 5.3 channel 默认值

- DDL 与 zdy 严格对齐：`channel TEXT NOT NULL DEFAULT 'prod'`
- 但 mining_zym 适配器与 stage 函数的形参默认值保持 `channel="default"` 不变，因为现有所有调用点都显式传 `"default"`；DB 默认值仅在"完全不传"的极端路径生效

### 5.4 `activate_release` 不再要求调用方传 channel/domain

- 内部先 `SELECT channel, domain FROM asset_publish_releases WHERE id = %s`，再用该 `(channel, domain)` 做退役过滤；调用方只传 `release_id` 即可，与改动前签名兼容

## 6. 已执行验证

- `pytest knowledge_mining_zym/tests/test_v11_pipeline.py` — **30 passed**（含新增 cross-domain 隔离用例）
- `pytest knowledge_mining_zym/tests` 除 `test_v12_e2e_live.py` 全量 — **96 passed**
- DDL 在已存在 v1 库上跑过一次：`ensure_schema()` 通过，无 UndefinedColumn 报错

## 7. 未验证项

- `test_v12_e2e_live.py`：依赖外部 LLM 服务，本次未启动；该测试不涉及 publish/release schema，预计不受影响
- `/api/releases/active` 在前端 UI 的实际调用：仅在后端做了 query 参数兼容，UI 端是否仍只传无参调用未核对（默认 `default/default`，对单 domain 默认部署不破）
- 远程 PG 主库（`121.89.90.178:5432/kb_db`）：本次只验证了测试 fixture 走 `ensure_schema()` 的路径，主库是否已被 zdy 的 migrate SQL 跑过未单独 SELECT 校验

## 8. 已知风险

- **风险 1：旧库未跑过 zdy migrate**——`ensure_schema()` 现在带 ALTER，会自动补；若调用方仍跑老版 mining_zym 写 release（不带 domain），列默认 `'default'`，对单 domain 部署等价于不变。
- **风险 2：`/api/releases/active` 默认 `domain="default"`**——若部署的实际 domain 是 `cloud_core_network`，调用方不传参数会查不到 active release。需在前端/集成方显式传 `domain` 参数。已在 § 7 未验证项中标注。
- **风险 3：跨 domain 复用同一 channel 命名**——这是新的语义，需要确认 zdy serving 侧 channel 命名空间是否真的允许 `alpha:default` 与 `beta:default` 同时存在 active。

## 9. 给 Codex 的审查重点

1. **DDL 等价性**：本仓库新 `002_asset_core_postgresql.sql` 跑完一遍后的 schema，是否与 zdy `migrate_v1_to_zdy.sql` 在 v1 上跑完一遍后的 schema **严格等价**（列、默认值、索引名、唯一约束 partial WHERE 子句都对得上）。重点核对：
   - `asset_publish_releases.channel DEFAULT 'prod'`
   - 唯一索引名 `uq_asset_publish_releases_domain_channel_active`
   - 非唯一索引名 `idx_asset_publish_releases_domain_channel_status`、`idx_asset_builds_domain_status`、`idx_asset_source_batches_domain`
2. **`activate_release` 退役范围**：现在按 `(channel, domain)` 退役。这是否符合 zdy serving 侧对多 domain release 的实际期望？是否存在"同 domain 不同 channel"或"同 channel 跨 domain"的真实切换场景被遗漏？
3. **DB 默认值兜底 vs 显式参数兜底**：mining_zym 适配器 `channel="default"` 默认参数与 DB `DEFAULT 'prod'` 不一致，仅在"完全不传 channel"时才暴露差异。是否能接受这个非严格等价点？
4. **API 兼容性**：`/api/releases/active` 加了 `domain` query 参数（默认 `default`）。如果 UI 当前默认配置实际部署到 `cloud_core_network` domain，是否需要在前端补这个参数才能恢复"找到 active release"？

## 10. 管理员本轮直接介入记录

**2026-05-19 管理员设计调整**：

> 不用管 channel，相同 domain 下只有一个 active 就行，channel 是预留的字段，现在不用，给一个默认值 prod 就行。

据此修订设计：active release 唯一性退化为 per-domain 单键，`channel` 退化为预留字段（默认 `'prod'`，不再参与查询/唯一约束）。详见 § 11。

## 11. 修订说明（2026-05-19 管理员决策落地）

### 11.1 修订后的 DDL（vs § 5.1 原始设计）

- 保留：`channel TEXT NOT NULL DEFAULT 'prod'`、`domain TEXT NOT NULL DEFAULT 'default'`、所有 ADD COLUMN IF NOT EXISTS / DROP INDEX IF EXISTS 兜底
- **变更**：唯一索引由 `(domain, channel)` 改为 `(domain)`：
  - 新建：`uq_asset_publish_releases_domain_active ON (domain) WHERE status = 'active'`
  - 新建：`idx_asset_publish_releases_domain_status ON (domain, status)`
- **删除**（在末尾 ALTER 块的 DROP IF EXISTS 中一并清掉，无论上一版是否建过都安全）：
  - `uq_asset_publish_releases_domain_channel_active`
  - `idx_asset_publish_releases_domain_channel_status`
  - `uq_asset_publish_releases_channel_active`（旧 v1）
  - `idx_asset_publish_releases_channel_status`（旧 v1）

### 11.2 修订后的适配器签名

- `insert_release(..., channel: str = "prod", ..., domain: str = "default")` — 默认值从 `"default"` 改为 `"prod"`，与 DB 列默认值对齐
- `activate_release(release_id)` — 内部 `SELECT domain FROM asset_publish_releases WHERE id = %s`，再按 `WHERE domain = %s AND status = 'active'` 退役；**不再读 channel**
- `get_active_release(domain: str = "default")` — **移除 `channel` 参数**，SQL 不再 `AND channel = %s`

### 11.3 修订后的 stages / jobs / API

- `publish_release(..., channel: str = "prod", ..., domain: str = "default")`：默认值改 `"prod"`；内部 `asset_db.get_active_release(domain)` 不传 channel
- `jobs/run.py::publish(..., channel: str = "prod", ...)`：默认值改 `"prod"`
- `/api/releases/active`：**移除 `channel` query 参数**，仅按 `domain` 过滤；返回体仍带 `channel` 字段（值固定 `'prod'`）

### 11.4 修订后的测试

- `test_active_release_isolated_by_domain`：删掉显式 `channel="default"`，断言仅验证 per-domain 隔离（语义不变，更贴近修订后设计）
- `test_publish_after_phase1`：`db.get_active_release(domain="cloud_core_network")` 不再传 channel

### 11.5 与 zdy migrate 后 schema 的差异说明

- zdy `migrate_v1_to_zdy.sql` 建的是 `(domain, channel)` 唯一；本修订建的是 `(domain)` 唯一，**更严格**
- 在 mining_zym 唯一写入路径都强制 `channel='prod'` 的前提下，两者对单 channel 部署等价
- 若未来 mining_zym 需要重启多 channel 语义，需要：
  1. 把 `uq_asset_publish_releases_domain_active` 改回 `uq_asset_publish_releases_domain_channel_active`
  2. 恢复 `get_active_release(channel, domain)`、`activate_release` 内部读 channel
  3. 同步确认 zdy serving 侧是否也回到 `(domain, channel)` 唯一

### 11.6 给 Codex 的审查重点（修订后）

1. **唯一约束严格度**：`(domain)` WHERE active vs zdy migrate 的 `(domain, channel)` WHERE active — 在所有 channel 都为 `'prod'` 的部署下严格等价；如果 zdy serving 侧未来引入 `channel='preview'` 之类，本修订会冲突。是否能接受这个"未来风险但当前等价"的设计？
2. **`/api/releases/active` 移除 `channel` 参数**：这是 breaking change（移除已发布的 query 参数）。前端/集成方是否需要同步调整？mining_ui 默认调用 path 是否仍工作？
3. **DDL DROP INDEX 列表**：末尾同时 DROP 了 v1 旧索引、上一版 `(domain, channel)` 索引、本版无关索引。是否漏 DROP 任何已建出的 stale 索引？
4. **mining_zym 适配器 channel 默认值 `"prod"` 与 DDL `DEFAULT 'prod'`**：现在两端一致，调用方完全不传 channel 也不会出现"DB 写 prod、内存当 default"的拼接歧义。确认无回归。
