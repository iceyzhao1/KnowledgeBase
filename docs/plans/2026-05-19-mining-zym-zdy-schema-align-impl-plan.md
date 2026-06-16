# mining_zym 对齐 agent_serving_zdy 的 domain schema 适配计划

- 任务：TASK-20260421-v11-knowledge-mining
- 日期：2026-05-19
- 作者：Claude

## 1. 任务目标

`agent_serving_zdy/src/main/resources/db/migrate_v1_to_zdy.sql` 已经把共享的 asset_core 库从 v1 迁到 zdy（多 domain 支持）。mining_zym 目前还停留在 v1，存在三处不一致：

1. DDL 文件 `databases/asset_core/schemas/002_asset_core_postgresql.sql` 仍是 v1，新部署或 `ensure_schema()` 会建出与 zdy 不兼容的结构。
2. `AssetCoreDB` 适配器接口不感知 `domain`，写入只能依赖 DB 默认值 `'default'`，无法真正承载多 domain。
3. publish/release 关键操作 `activate_release` 仍按 `channel` 单维度退役旧 active，会跨 domain 误退役其它租户的 active release。

目标：让 mining_zym 写出来的 batch / build / release 与 zdy 迁移后的 schema 完全一致，并能正确隔离 domain。

## 2. 当前实现回顾

### 2.1 DDL 差异（vs migrate 后的 zdy）

`databases/asset_core/schemas/002_asset_core_postgresql.sql` 缺：
- `asset_source_batches.domain TEXT NOT NULL DEFAULT 'default'`
- `asset_builds.domain TEXT NOT NULL DEFAULT 'default'`
- `asset_publish_releases.domain TEXT NOT NULL DEFAULT 'default'`
- `asset_publish_releases.channel` 缺 `DEFAULT 'prod'`
- 旧索引仍是 `uq_asset_publish_releases_channel_active`、`idx_asset_publish_releases_channel_status`
- 缺 `uq_asset_publish_releases_domain_channel_active`、`idx_asset_publish_releases_domain_channel_status`、`idx_asset_builds_domain_status`、`idx_asset_source_batches_domain`

### 2.2 适配器接口缺口（`knowledge_mining_zym/mining/infra/db.py`）

- `upsert_source_batch` (L130) 不写 `domain`
- `insert_build` (L456) 不写 `domain`
- `insert_release` (L537) 不写 `domain`；默认参数 `channel="default"`，与 zdy 新默认 `'prod'` 不一致
- `activate_release` (L561) 退役旧 active 时仅 `WHERE channel = %s`，需要追加 `AND domain = %s`
- `get_active_release(channel)` (L576) 缺 `domain` 参数

### 2.3 调用方（已经持有 domain）

- `mining/jobs/run.py:398` — `profile.domain_id` 在同函数内已可用
- `mining/stages/publishing.py:131 / 227 / 233 / 245` — `build_build`、`publish_release` 还没把 domain 当形参
- `mining/api/routes/builds.py:97` — `/api/releases/active` 无 `domain` 入参，永远拿"第一个 active"

### 2.4 不在本次范围

- `serving_query_logs` 表：由 serving 侧负责创建与写入，mining_zym 既不读也不写。本次 DDL 不补这张表，避免越权；如未来需要由 mining_zym 侧的 `ensure_schema` 承担"建库一次到位"职责，再单独评估。
- `agent_serving_zdy` 自身代码：不在本仓库的 mining 范围内。
- v1 → zdy 的数据迁移脚本：迁移已由 zdy 侧执行；mining_zym 这边只保证"对齐后的 DDL/写入逻辑"一致。

## 3. 设计决策

### 3.1 domain 的来源：单一来源 `domain_profile.domain_id`

- `MiningRun` 启动时已加载 `DomainProfile`，`profile.domain_id` 是该 run 的 domain 唯一标识。
- 决策：把 `domain` 作为 run 级参数从 `jobs/run.py` 沿调用链注入到 `build_build → publish_release → AssetCoreDB.*`，**不**新增"从 batch / build 反查 domain"的额外查询路径。
- 兜底：所有适配器方法把 `domain` 默认为 `"default"`，保证旧测试代码与本地实验脚本不破。

### 3.2 channel 默认值

- mining_zym 侧适配器 `insert_release` 和 `publish_release` 的形参默认值保持 `channel="default"` 不变（mining 内部历史口径），但允许调用方覆盖。
- 决策理由：zdy 把 SQL 列默认值改为 `'prod'`，那是"调用方完全不传 channel"时的兜底；mining_zym 当前所有调用点都显式传 `channel="default"`，行为与 zdy migrate 后的现有 row 完全一致（migrate 不改老 row 的 channel）。把 mining 默认值也强改 `'prod'` 反而会让现有调用静默切 channel，风险更大。
- 副作用确认：DDL 里也不强加 `DEFAULT 'prod'`（保持 mining_zym DDL 与 zdy 迁移后状态等价：列允许默认值，但 mining 调用方总显式传值即可）。这是与 zdy SQL 的一个非严格等价点，记入 handoff。

> **修订**：经评审决定与 zdy 严格等价，DDL 加上 `DEFAULT 'prod'`；详见 § 3.2-revised。

### 3.2-revised channel 默认值（最终采用）

- 与 zdy 严格对齐：DDL 给 `asset_publish_releases.channel` 加 `DEFAULT 'prod'`。
- 适配器 `insert_release(channel="default")` 不变；调用方显式传值，DB 默认值仅在"完全不传"的极端路径生效。

### 3.3 activate_release 的退役范围

- 决策：`UPDATE ... SET status='retired' ... WHERE channel = %s AND domain = %s AND status = 'active'`。
- 实现：`activate_release` 先 `SELECT channel, domain FROM asset_publish_releases WHERE id = %s`，再用查到的 `(channel, domain)` 作为退役过滤条件，避免调用方再传一次。

### 3.4 DDL 升级方式

- 直接在 `002_asset_core_postgresql.sql` 原地修订：
  - 三张表加 `domain TEXT NOT NULL DEFAULT 'default'`
  - `channel TEXT NOT NULL DEFAULT 'prod'`
  - 新增 4 个索引
  - 旧索引保留 `DROP INDEX IF EXISTS` 步骤？**否**——这是初始建表 DDL，不是迁移脚本，旧索引语句直接换成新索引语句即可。
- 不新增 `003_*.sql`：DDL 历来在 `002` 上原地演进（与 `docs/plans/...mining-ui-pg-migration` 风格一致）。
- 已存在的旧库由 zdy 的 migrate SQL 负责升级，mining_zym 的 `ensure_schema()` 走 `IF NOT EXISTS`，对已迁移的库幂等。

### 3.5 API 层 `/api/releases/active`

- 加 `domain: str = "default"` query 参数，过滤条件追加 `AND domain = %s`。
- 返回体增加 `domain` 字段。

## 4. 改动清单

### 4.1 DDL

- `databases/asset_core/schemas/002_asset_core_postgresql.sql`
  - `asset_source_batches`：加 `domain TEXT NOT NULL DEFAULT 'default'`、`CREATE INDEX idx_asset_source_batches_domain`
  - `asset_builds`：加 `domain` 列、`CREATE INDEX idx_asset_builds_domain_status (domain, status)`
  - `asset_publish_releases`：加 `domain` 列；`channel` 加 `DEFAULT 'prod'`；旧 `uq_asset_publish_releases_channel_active`、`idx_asset_publish_releases_channel_status` 替换为 `uq_asset_publish_releases_domain_channel_active` 与 `idx_asset_publish_releases_domain_channel_status`

### 4.2 适配器

- `knowledge_mining_zym/mining/infra/db.py`
  - `upsert_source_batch(..., domain: str = "default")` — SQL 加 `domain` 列
  - `insert_build(..., domain: str = "default")` — SQL 加 `domain` 列
  - `insert_release(..., domain: str = "default")` — SQL 加 `domain` 列
  - `activate_release(release_id)` — 改为先查 `(channel, domain)` 再退役
  - `get_active_release(channel="default", domain="default")` — 加参数

### 4.3 调用方

- `knowledge_mining_zym/mining/jobs/run.py:398` `upsert_source_batch(..., domain=profile.domain_id or "default")`
- `knowledge_mining_zym/mining/stages/publishing.py`
  - `build_build(..., domain: str)` 形参；`insert_build(..., domain=domain)`
  - `publish_release(..., domain: str = "default")` 形参；`get_active_release(channel, domain)`、`insert_release(..., domain=domain)`
  - 上游调用点同步加参
- `knowledge_mining_zym/mining/api/routes/builds.py:97` `/api/releases/active` 加 `domain` query 参数

### 4.4 测试

- `knowledge_mining_zym/tests/test_v11_pipeline.py`
  - 现有 publish/activate 用例改成显式传 `domain="default"`，确认行为不变
  - 新增 1 条用例：在同 `channel="default"` 下，`domain="alpha"` 的 active release 不会因为激活 `domain="beta"` 的新 release 而被退役

## 5. 验证计划

1. `pytest knowledge_mining_zym/tests -x` 全量回归
2. 重点关注：
   - `test_v11_pipeline.py` 中 publish/activate/get_active_release 链路
   - `test_pipeline_operators.py` 中 `upsert_source_batch` 调用
3. 真实库一次手动验证（如需）：在 PG 实例上 `DROP DATABASE && ensure_schema()` → 对比 `\d+ asset_publish_releases` 与 zdy migrate 后的结构是否完全一致

## 6. 风险与边界

- **风险 1：旧库已经被 zdy migrate SQL 改过**——`ensure_schema()` 走 `IF NOT EXISTS`，重复执行是幂等的；新 DDL 中"已存在的列/索引"不会被覆盖。已确认。
- **风险 2：sqlite 版本 DDL（`001_asset_core.sqlite.sql`）不同步**——mining_zym 已切到 PG，sqlite DDL 仅做向后兼容。**不在本次范围**，留待单独评估。
- **风险 3：UI/脚本中硬编码 domain**——`scripts/mining_ui.py` 不直接写 batch/build/release，但需扫一遍是否有 `get_active_release()` 不带 domain 的调用；本计划执行时一并 grep 一次确认。

## 7. 交接重点（给 Codex）

- DDL 修订是否真的与 `migrate_v1_to_zdy.sql` 跑过之后的 schema 严格等价（含索引名、列默认值、`channel` 默认值）
- `activate_release` 改为按 `(channel, domain)` 退役后，多 domain 共用 channel 的语义是否符合 zdy 侧 serving 的期望
- API `/api/releases/active` 加 `domain` 参数是否破坏现有调用方

## 8. 修订说明（2026-05-19，管理员决策）

**背景**：在 § 3 设计基础上，管理员明确：`channel` 字段在 mining_zym 当前阶段不参与业务语义，仅作为预留字段，所有写入统一为 `'prod'`；active release 的唯一性约束按 `(domain)` 单键、不再按 `(domain, channel)`。

**最终设计调整**：

1. **DDL（`002_asset_core_postgresql.sql`）**
   - `asset_publish_releases.channel TEXT NOT NULL DEFAULT 'prod'`（不变）
   - 唯一索引：`uq_asset_publish_releases_domain_active ON (domain) WHERE status = 'active'`
   - 非唯一索引：`idx_asset_publish_releases_domain_status ON (domain, status)`
   - 旧 `uq_asset_publish_releases_domain_channel_active` 与 `idx_asset_publish_releases_domain_channel_status` 在 ALTER 块底部 `DROP INDEX IF EXISTS` 一并删除（兼容上一次实现中已建出的索引）

2. **适配器 (`db.py`)**
   - `insert_release(channel="prod", ...)`：默认值由 `"default"` 改为 `"prod"`，与 DB 列默认值对齐
   - `activate_release(release_id)`：内部仅查 `domain`；退役范围 `WHERE domain = %s AND status = 'active'`
   - `get_active_release(domain: str = "default")`：**移除 `channel` 参数**，SQL 不再按 channel 过滤

3. **stages / jobs / api**
   - `publish_release(channel="prod", ...)`：默认值改为 `"prod"`，对 `get_active_release` 的调用改为只传 `domain`
   - `jobs/run.py::publish(channel="prod", ...)`：默认值改为 `"prod"`
   - `/api/releases/active`：**移除 `channel` query 参数**，仅按 `domain` 过滤

4. **测试**
   - `test_active_release_isolated_by_domain` 调整：不再显式传 `channel`，仅验证 `(domain)` 维度的 active 隔离
   - `test_publish_after_phase1` 中 `get_active_release` 改为 `db.get_active_release(domain="cloud_core_network")`

**与 zdy migrate 后 schema 的差异说明**：zdy 的 migrate SQL 建的是 `uq_asset_publish_releases_domain_channel_active (domain, channel)` 唯一索引。本修订选择更严格的 `(domain)` 唯一约束，等价于"在 channel 全部为 'prod' 的前提下，按 domain 唯一 active"。如未来 mining_zym 需要重新启用多 channel 语义，再恢复 `(domain, channel)` 复合索引；届时需要确认 zdy 侧是否也回到 `(domain, channel)` 唯一。

**已知风险（修订后）**：
- 若 zdy serving 侧实际写过 `channel != 'prod'` 的 active row，mining_zym 这边的 `(domain)` 唯一约束会拒绝建库的"同 domain 多 channel"快照；当前管理员已确认 mining_zym 唯一写入路径都用 `'prod'`，不会出现这种冲突。
- `/api/releases/active` 移除 `channel` 参数是 breaking change：调用方如果之前显式传 `channel="default"`，会得到 422。需要在前端/集成方同步移除该参数。
