# 入库流程事务化与去重重构 实施方案

- **日期**: 2026-06-04
- **作者标识**: zym
- **目标副本**: `knowledge_mining`(主副本,**只改这一份**;`knowledge_mining_zym` / `knowledge_mining_fzl` 本次不动)
- **状态**: 待实施(已与用户确认设计,落地前需确认开放问题,见末尾)
- **范围说明**: 本文档除入库事务化重构(§1–8)外,还附带两处独立的 Docker 部署修复——后台日志缺时间戳(§9)、打包不更新依赖致服务缺依赖(§10)。用户要求这两处**与入库重构一起实施**。

---

## 0. 这次到底在改什么(给后续大模型的速读)

一句话:**把"一篇文档的入库写入"改成一个数据库事务(要么全成、要么全部回滚),并修掉由此暴露的"空快照"脏数据问题和重复入库去重问题。**

触发起因:运行时报错 `Snapshot <id> has no segments`(抛出点见 `mining/stages/publishing.py` 的 `validate_build`)。根因是入库流程**没有事务**,失败时会留下"有快照、无段落"的半截脏数据,再次入库时被误当作可复用而引爆校验失败。

---

## 1. 背景与根因分析

### 1.1 报错抛出点
`mining/stages/publishing.py` → `validate_build()`:
```python
for snap in active:
    count = asset_db.count_segments_by_snapshot(snap["document_snapshot_id"])
    if count == 0:
        raise ValueError(f"Snapshot {snap['document_snapshot_id']} has no segments")
```
即:某个被标为 `active` 的文档快照,在 `asset_raw_segments` 表里查不到任何段落,构建校验整体失败。

### 1.2 根因:入库流程没有事务
`mining/infra/db.py` 的基类 `_DB`:
```python
def _execute(self, sql, params=()):
    with self._pool.connection() as conn:   # 每条语句独立连接,退出即提交
        with conn.cursor() as cur:
            cur.execute(sql, params)

def commit(self):
    """No-op: each _execute auto-commits via context manager."""
```
**每条 INSERT 都是独立即时提交,`commit()` 是空操作。** 因此"一篇文档"的多步写入(快照 → link → 段落 → 关系 → 检索单元)之间没有任何原子性。

主副本写库集中在 `mining/pipeline.py` 的 `db_write_stage()`(单 worker,串行)。其流程:
1. `select_or_create_snapshot()` —— 建 document / snapshot / link,**已即时提交**;
2. 逐条插段落 / 关系 / 检索单元;
3. 插 embedding。

任何一步在第 1 步之后失败 → 快照和 link 已永久落库,但段落没写完 → 留下"空快照"脏数据。

### 1.3 脏数据如何引爆报错(两条路径)
- **新建空快照**:文档能解析出 parse tree 但分段产出 0 段落时,仍会建快照、并作为 active 进入构建 → `validate_build` 立刻报错。
- **SKIP 复用空快照**(重复入库典型路径):上次失败留下空快照后,再次入库同一文档,内容指纹未变 → 在 `mining/jobs/run.py` 被判为 SKIP → 直接复用那条空快照 → `validate_build` 报错。

### 1.4 数据模型关键事实(实施时须知)
两个独立数据库,各自独立连接池,都继承同一个 `_DB` 基类:
- **mining_runtime(流水账)**:`mining_run_documents` 已记录每篇文档每次运行的 `status`(pending/processing/committed/failed/skipped)、`error_message`、时间戳;`UNIQUE(run_id, document_key)`。→ **失败痕迹天然存在于此,无需额外字段。**
- **asset_core(成品库)**:
  - `asset_documents`:文档身份,`UNIQUE(document_key)`。
  - `asset_document_snapshots`:内容版本,`UNIQUE(normalized_content_hash)`(按内容指纹天然去重)。
  - `asset_document_snapshot_links`:文档→快照映射,**无状态字段**,`linked_at` 排序取最新。
  - `asset_raw_segments`:段落,`UNIQUE(document_snapshot_id, segment_key)`。
  - 发布层 `asset_build_document_snapshots.selection_status` 有 `active/removed`——"主动移除"(源文件被删)走这里,**与"挖掘失败"是两回事,勿混淆**。

主副本 `_execute/_fetchone/_fetchall` 额外带 `@_retry_on_op_error()` 装饰器(瞬时连接错误重试 3 次)。**事务化时须注意:事务内单条语句失败会让整个事务作废,不能简单重试**(见改动 1 第 4 点)。

---

## 2. 设计决策(已与用户敲定)

1. **原子单元 = 一篇文档**。不是一个阶段,也不是整批。每篇文档一个独立事务,写完即提交并归还连接。
2. **失败文档在成品库不留痕**(靠事务回滚);痕迹只记在流水账(已有 status=failed + error_message),**不在成品库新增"废弃"字段**。理由:成品库只放干净可用内容;保留半截+废弃标记会让所有查询都要排除废弃、易漏、还要额外清理。
3. **大模型调用与事务无关**:LLM 在并发的流式管线阶段(parse/segment/enrich/discourse/retrieval_units)早已算完,结果在内存;事务只包"把内存结果落库"这段串行快操作。所以"LLM 慢 / 多线程"都在事务之外,不受影响。
4. **embedding 留在事务外、且失败不连累文档**:它是外部/附加产物,文档没 embedding 仍然有效。
5. **去重统一以内容指纹为准**;**身份(document_key)按来源选择**:本地同步用路径(可识别改名/删除),网页上传用内容指纹(满足"同内容不重挖",文件名不可靠故不参与判重,仅作显示标题)。
6. **复用已存在的完整快照时不重写段落**(只补 link),否则撞 `UNIQUE(document_snapshot_id, segment_key)`。

---

## 3. 事务边界(精确)

以 `db_write_stage` 为准:

```
db_write_stage(一篇文档):
  if ctx.error: 记失败; return
  if ctx.tree is None: 记跳过; return
  segments = ctx.segments
  if not segments: 记跳过(不建快照); return          # 改动2-1

  ┌──────────── with asset_db.transaction():  (BEGIN) ────────────┐
  │  document_id, snapshot_id, link_id = select_or_create_snapshot │
  │  if action == UPDATE: 删除旧快照的 seg/rel/ru                   │  删旧+写新须同事务
  │  existing_count = count_segments_by_snapshot(snapshot_id)      │  改动2-3
  │  if existing_count == 0:        # 全新 or 空壳 → 写            │
  │       for seg: insert_raw_segment                              │
  │       for rel: insert_segment_relation                        │
  │       for ru:  insert_retrieval_unit (填 ru_id_map)           │
  │  else:                          # 复用完整快照 → 只保留 link   │
  │       pass(段落已存在,不重写)                                 │
  └──────────── 离开 with → COMMIT;异常 → ROLLBACK 并向外抛 ──────┘

  # 事务外、best-effort:
  try: 插 embedding(用 ru_id_map) except: 记日志,不判失败    # 改动2-4
  tracker.commit_document(...)   # 写的是 runtime 库
```

**排除在事务外的**:LLM 计算(早已在管线算完)、embedding 插入、runtime 流水账写入。

---

## 4. 逐文件改动

### 改动 1 — DB 适配器加事务支持
**文件**: `knowledge_mining/mining/infra/db.py`

1. `_DB.__init__` 中为**每个实例**建独立 ContextVar(隔离 asset/runtime 两库,避免写错库):
   ```python
   import contextvars
   # in __init__:
   self._tx_conn = contextvars.ContextVar("tx_conn", default=None)
   ```
2. 新增:
   ```python
   from contextlib import contextmanager
   @contextmanager
   def transaction(self):
       if self._tx_conn.get() is not None:   # 嵌套则复用外层事务
           yield; return
       with self._pool.connection() as conn:  # 正常退出整体 commit,异常 rollback
           if conn.autocommit:
               raise RuntimeError("transaction() requires non-autocommit connection")
           token = self._tx_conn.set(conn)
           try:
               yield
           finally:
               self._tx_conn.reset(token)
   ```
3. `_execute / _fetchone / _fetchall` 改为**事务感知**:当 `self._tx_conn.get()` 非空,直接在该连接上执行(读写同一连接,保证读到未提交的写),不另开池连接、不额外提交;否则维持原行为。
4. **重试只保留给非事务路径**:重构使 `@_retry_on_op_error()` 只包裹"非事务"分支。事务内瞬时错误直接上抛 → 整篇回滚记失败,下次运行重处理。建议拆出内部 `_run_on(conn, sql, params, fetch=...)`,由 `_execute/_fetchone/_fetchall` 分派:有事务连接→直接跑(无重试);无→重试包裹的 `with pool.connection()`。
5. `commit()` 保留为 no-op(兼容现有大量调用点)。

### 改动 2 — 写库阶段:包事务 + 空段跳过 + 复用不重写 + embedding 隔离
**文件**: `knowledge_mining/mining/pipeline.py`(`db_write_stage`,约 436–616 行)

- **2-1 空内容跳过**:取得 `segments`(约 476 行)后,若 `not segments` → `tracker.skip_document(rd_id)` + `runtime_db.commit()`,`return ctx`(**不带 document_id/snapshot_id**)。run.py 汇总(约 549–568 行)会因无 document_id 归入 skipped,不进 `snapshot_decisions`。
- **2-2 包事务**:把 `select_or_create_snapshot`(约 488)到检索单元写完(约 574)整段套进 `with asset_db.transaction():`;删除其间无用的 `asset_db.commit()`(约 491/507/576)。
- **2-3 复用完整快照不重写**:`select_or_create_snapshot` 返回后,`existing_count = asset_db.count_segments_by_snapshot(snapshot_id)`;`==0` 才写段落/关系/检索单元(全新或空壳自愈),`>0` 跳过写入(仅保留已建的 link)。注意:UPDATE 走的是新指纹→新快照,count 为 0,正常写入。
- **2-4 embedding 移出事务且失败不连累**:嵌入插入(约 579–594)移到 `with` 之后,包独立 `try/except`,失败仅 `logger.warning`,不调用 `fail_document`。`ru_id_map` 需在 `with` 之前声明以便事务外引用。

### 改动 3 — SKIP 路径加"非空壳"校验(存量自愈)
**文件**: `knowledge_mining/mining/jobs/run.py`(SKIP 分支,约 488–503 行)

把跳过快捷路径改为:仅当 `existing_link` 且 `asset_db.count_segments_by_snapshot(existing_link["document_snapshot_id"]) > 0` 时才真正 SKIP、复用、进 `snapshot_decisions`;否则**不跳**——将 `action` 改为 `UPDATE`(并相应更新流水账中该记录的 action),让文档落入 `work_items` 走正常重挖。→ 存量空壳在下次入库时被自动覆盖修复。

### 改动 4 — 网页上传身份用内容指纹
**文件**: `knowledge_mining/mining/jobs/run.py`(约 458 行 `doc_key = f"doc:/{doc.relative_path}"`)

按来源选择身份:
- 本地同步来源 → 仍 `doc:/{relative_path}`(保留改名/删除识别)。
- 网页上传来源 → `content:/{doc.normalized_content_hash}`。同内容重传 → 身份+指纹皆同 → SKIP 复用、不重挖;不同内容 → 新文档。文件名仅作显示标题,不参与判重。

> **开放问题(须先确认)**:上传入口用什么字段标记"网页上传 vs 本地同步"(如 `doc.source_type`)?实施前需查上传链路代码确认。建议封装 `build_document_key(doc)` 统一这段逻辑。

### 改动 5 — 清理存量空壳(一次性)
**大白话**:事务只防未来,已存的空壳要单独清一次,当前报错才会立刻消失。

先只读诊断:
```sql
SELECT s.id
FROM asset_document_snapshots s
LEFT JOIN asset_raw_segments seg ON seg.document_snapshot_id = s.id
WHERE seg.id IS NULL;
```
确认数量后再清理(外键 `ON DELETE RESTRICT`:须先删指向空快照的 link,再删快照;并检查 `asset_build_document_snapshots` 是否引用)。清理脚本依诊断结果再定,确保不误删被正常引用的快照。

### 改动 6 — 测试
新增/调整覆盖:
1. 写库中途失败 → 整篇回滚,库中不留半截(快照/link/段落均无);
2. 0 段落文档 → 被跳过,不建快照,不进构建;
3. 同内容不同文档 → 共用快照,不重写段落、不报唯一约束;
4. SKIP 命中空壳 → 触发重挖而非复用;
5. 回归:跑现有 `tests/`(注意可能断言旧"逐条提交"行为的用例,如 pipeline / batch 相关)。

---

## 5. 风险清单(实施时对照)

| # | 风险 | 处理 |
|---|---|---|
| 1 | ContextVar 若全局共享 → 写错库 | 改动1-1:每实例独立 ContextVar |
| 2 | 只让写感知事务、读不感知 → 读不到未提交数据/池争用 | 改动1-3:读写都感知 |
| 3 | 存量脏数据不会自愈,仅加事务**报错不会消失** | 改动3(自愈)+ 改动5(清理) |
| 4 | 行为变化:0 段落文档从此被跳过、不留档 | 已与用户确认可接受 |
| 5 | 并发入库时共享快照行锁持有变长,极端可死锁 | 单 run 串行不受影响;并发入库为已知回归点,暂不处理 |
| 6 | 事务依赖连接非 autocommit,被改则静默失效 | 改动1-2:开事务时断言报警 |
| 7 | 重试装饰器与事务冲突 | 改动1-4:重试仅用于非事务路径 |
| 8 | 测试断言旧提交行为 | 改动6 回归 + 调整 |

---

## 6. 落地顺序
1 → 2 → 3(核心:装事务 + 三道闸)→ 5(清存量,使报错立即消失)→ 4(网页上传身份,**需先确认标记字段**)→ 6(测试)。

## 7. 开放问题(实施前须答)
1. **改动 4**:上传入口用哪个字段区分"网页上传 vs 本地同步"?(需查上传链路)
2. **改动 5**:先只跑诊断给用户看数量,还是诊断+清理一并执行?

---

## 8. 关键文件索引(主副本)
- `knowledge_mining/mining/infra/db.py` — `_DB` 基类、`_execute/_fetchone/_fetchall`、`commit`、`count_segments_by_snapshot`
- `knowledge_mining/mining/pipeline.py` — `db_write_stage`(写库)、`segment_stage`
- `knowledge_mining/mining/snapshot/__init__.py` — `select_or_create_snapshot`
- `knowledge_mining/mining/jobs/run.py` — Phase 1a 分类与 SKIP、`doc_key` 构造、结果汇总
- `knowledge_mining/mining/stages/publishing.py` — `validate_build`(报错点)、`classify_documents`、`assemble_build`
- 库结构:`databases/asset_core/schemas/002_asset_core_postgresql.sql`、`databases/mining_runtime/schemas/002_mining_runtime_postgresql.sql`

---

# 附带修复(Docker 部署,随入库重构一起实施)

> 以下两项与入库重构相互独立,但用户要求合并到本次一起改。注意 Docker 镜像打包的是**主副本**的服务代码(`knowledge_mining` / `llm_service` / `main_control_service` / `mcp_server`),与本方案目标副本一致。

## 9. Docker 后台日志缺时间戳

### 9.1 现状与根因
- 所有服务经 supervisor 把日志原样转发到容器 stdout(`docker/supervisord.conf`,各 program 用 `stdout_logfile=/dev/fd/1` + `redirect_stderr=true`),**supervisor 不会加时间戳**。
- 4 个 Python 服务入口直接 `uvicorn.run(...)`,**未配置日志格式**,沿用 uvicorn 默认(只有级别,无日期时间):
  - `llm_service/__main__.py:18`
  - `knowledge_mining/mining/api/__main__.py:19` (`uvicorn.run(app, host="0.0.0.0", port=port)`)
  - `main_control_service/main.py:158`
  - `mcp_server/__main__.py`
- 仅 `knowledge_mining/demo_run.py:21` 配了带 `%(asctime)s` 的格式,但该脚本不在 Docker 中运行。
- Java 服务(`agent_serving.jar`,Spring Boot)默认日志通常**自带**时间戳——实施时确认一次,大概率不用改。

### 9.2 修法
每个 Python 服务入口统一做两件事:
1. 启动早期调用 `logging.basicConfig(level=..., format="%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")`,覆盖应用/库自身日志。
2. 给 `uvicorn.run(...)` 传 `log_config`:在 uvicorn 默认 `LOGGING_CONFIG` 基础上,给 `default` 与 `access` 两个 formatter 加上 `asctime`,使 uvicorn 自己的访问/错误日志也带时间戳(只做 basicConfig 不影响 uvicorn 自带 handler)。

建议抽一个共享小工具(如 `shared/logging_setup.py`,或各服务各放一份 `setup_logging()`),避免 4 处重复、再次漂移。

**时区注意**:容器默认 UTC。若需本地时间,在 `Dockerfile` 设 `ENV TZ=Asia/Shanghai` 并安装 `tzdata`,或在 formatter 里处理。

### 9.3 涉及文件
- `llm_service/__main__.py`
- `knowledge_mining/mining/api/__main__.py`
- `main_control_service/main.py`
- `mcp_server/__main__.py`
- (可选)`docker/Dockerfile`(TZ + tzdata)

## 10. Docker 打包不更新依赖(服务缺依赖)

### 10.1 现状与根因
`docker/Dockerfile` 第 49–62 行用**写死的包清单**安装 Python 依赖,**完全不读任何 `requirements.txt`**。任何服务往自己依赖清单里新增的库,镜像都拿不到 → 运行时缺依赖。

经核对实锤缺失:
- **`python-multipart`** —— `knowledge_mining/mining/api/routes/uploads.py` 使用 `UploadFile`(即网页上传接口)。镜像缺它 → **上传一调即报错**。优先级最高,且与本方案"网页上传去重"(改动 4)是同一条链路。
- **`python-dotenv`** —— 根 `requirements.txt` 与各服务均列出;镜像缺(影响 `.env` 加载)。
- 反例:`nicegui` / `pandas` 仅 `scripts/mining_ui.py` 用,**不应进部署镜像**(避免臃肿)。

关键洞察:`pyproject.toml` 本就是依赖声明的标准位置,`Dockerfile:48` 也确实把它 COPY 进镜像了——**却不读它**,而是另抄了一份写死的清单(第 49–62 行)。**两份清单各自维护 = 脱节根源**。佐证:`pyproject.toml` 里有 `python-docx>=1.1`,而 Docker 那份漏了。

### 10.2 设计目标(用户明确要求)
用户不接受"再建一份需手动维护的清单"(如单独的 `requirements.txt`),要求**依赖能随代码改动自动跟上、不脱节**。

须诚实说明的前提:**"改代码就 100% 自动进清单"无可靠方案**——Python 无法从 `import xxx` 稳定反推包名+版本(import 名≠包名、版本范围、可选依赖等),故**总需一份"声明"**。可达成的是:① 只维护**一份**声明、Docker 直接读它(消除第二份漂移);② 忘了声明时**自动报警**。

### 10.3 修法(确立 pyproject.toml 为唯一来源 + 自动体检)
**第 1 步:Docker 直接读 `pyproject.toml` 装依赖**(撤销旧"方案 A 新建 txt")。
镜像基底 `python:3.11-slim` 自带 `tomllib`,无需额外依赖。把 `Dockerfile` 第 49–62 行替换为:
```dockerfile
COPY pyproject.toml ./
RUN python -c "import tomllib;open('/tmp/req.txt','w').write('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" \
 && pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /tmp/req.txt
```
- 依赖逐行写入临时文件再 `-r` 安装,避开 `>=` 在 shell 被当作重定向的坑。
- 放在 COPY 业务代码之前,保留镜像分层缓存。
- 效果:**以后加依赖只改 `pyproject.toml` 一处**(标准做法),重新打包自动带上,**不再有第二份清单**。

**第 2 步:一次性补齐 `pyproject.toml` 缺的依赖**:`[project.dependencies]` 增加
```
"python-multipart>=0.0.9",   # 上传接口 uploads.py 必需
"python-dotenv>=1.0",        # .env 加载
```
(`python-docx` 已在,补全后 Docker 自动包含。)

**第 3 步:加"自动体检"兜底,防止漏声明**。引入 `deptry`(扫描代码 import 与已声明依赖的差异,报出"用了但没声明 / 声明了没用"),挂到 CI 或提供一条本地命令(如 `deptry .`)。这样即便有人忘在 pyproject 声明,也会被自动抓出,而非线上才崩。
> 这是实务上的"自动不脱节":**唯一来源(pyproject)+ 自动体检(deptry)**。

**注意**:`pyproject.toml` 是全仓库统一声明,Docker 装的是各服务依赖的并集——对 all-in-one 镜像合适。UI 专用重包(`nicegui`/`pandas`)不在 `[project.dependencies]` 中,故不会进镜像,无需额外处理。

### 10.4 系统依赖(无需改动)
`p7zip-full` 已在 `Dockerfile:42` 安装到位,确认无需改动。

### 10.5 涉及文件
- `docker/Dockerfile`(第 49–62 行依赖安装段,改为读 `pyproject.toml`)
- `pyproject.toml`(补 `python-multipart`、`python-dotenv`)
- (可选,体检)CI 配置或开发脚本引入 `deptry`

## 11. 待确认 / 实施时核对
- 用户已确认**无第三个问题**。
- §10 依赖方案已敲定:**以 `pyproject.toml` 为唯一来源 + `deptry` 自动体检**(已撤销"新建 requirements-runtime.txt"的旧方案 A)。
- §9 Java 服务是否已自带时间戳,实施时确认。
- §10 实施时再 `grep` 一遍 4 个部署服务的实际 import,核对 `pyproject.toml` 声明无遗漏(或直接用 `deptry` 跑一次)。
