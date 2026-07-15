# cmkb0708 Deployment Gap Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `deploy/cmkb0708` 本地冻结分支上隐藏三个高级图谱页面、统一运行配置到 `main_control_service/config`，并让全部业务查询和修改按当前 domain 隔离。

**Architecture:** 前端显式把 `domainStore.currentDomain` 传给业务 API；Python 和 Java 后端按 domain 过滤列表，并用“资源 ID + domain”校验详情与修改。配置路径只保留 Main Control 一份，场景配置库仅用于人工替换且不进入 Docker 镜像。

**Tech Stack:** Vue 3、TypeScript、Pinia、Axios、FastAPI、Pydantic、psycopg、Spring Boot、JUnit、pytest、Docker、Git。

---

### Task 1: 隐藏三个页面并阻止直接访问

**Files:**
- Create: `tests/test_hidden_advanced_graph_pages.py`
- Modify: `kb-ui/src/components/layout/Sidebar.vue`
- Modify: `kb-ui/src/router/index.ts`

- [ ] **Step 1: 编写失败的前端源码契约测试**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "kb-ui/src/components/layout/Sidebar.vue").read_text(encoding="utf-8")
ROUTER = (ROOT / "kb-ui/src/router/index.ts").read_text(encoding="utf-8")


def test_advanced_graph_pages_are_absent_from_sidebar():
    assert "{ path: '/entities'" not in SIDEBAR
    assert "{ path: '/ontology'" not in SIDEBAR
    assert "{ path: '/ontology/graph'" not in SIDEBAR


def test_advanced_graph_urls_redirect_home():
    for path in ("entities", "ontology", "ontology/graph"):
        route = f"path: '{path}'"
        start = ROUTER.index(route)
        block = ROUTER[start : start + 160]
        assert "redirect: '/'" in block
        assert "component:" not in block


def test_review_routes_remain_available():
    assert "path: 'candidates/review'" in ROUTER
    assert "path: 'mentions/review'" in ROUTER
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_hidden_advanced_graph_pages.py -q`

Expected: 两个隐藏/重定向测试失败，审核路由测试通过。

- [ ] **Step 3: 修改导航和路由**

从 `Sidebar.vue` 删除三项导航并删除未使用的 `Collection`、`Connection`、`DataLine` 图标导入。把三个路由改为：

```typescript
{
  path: 'entities',
  redirect: '/',
},
{
  path: 'ontology',
  redirect: '/',
},
{
  path: 'ontology/graph',
  redirect: '/',
},
```

- [ ] **Step 4: 运行测试并提交**

Run: `D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_hidden_advanced_graph_pages.py -q`

Expected: `3 passed`。

Commit:

```powershell
git add kb-ui/src/components/layout/Sidebar.vue kb-ui/src/router/index.ts tests/test_hidden_advanced_graph_pages.py
git commit -m "fix(ui): hide advanced graph pages"
```

### Task 2: 恢复场景配置库并排除 Docker 打包

**Files:**
- Create: `config_library/README.md`
- Create: `config_library/site/domain_registry.yaml`
- Create: `config_library/site/scenario_packs/civil_engineering/domain.yaml`
- Create: `config_library/site/scenario_packs/odn/domain.yaml`
- Create: `config_library/enterprise/domain_registry.yaml`
- Create: `config_library/enterprise/scenario_packs/tender_rfp/domain.yaml`
- Modify: `.dockerignore`
- Modify: `.gitignore`
- Test: `tests/test_enterprise_config_library.py`

- [ ] **Step 1: 复用已审核的配置库提交**

Run:

```powershell
git cherry-pick 92df44e
```

Expected: 只新增配置库、README、忽略规则和配置库测试，不修改运行配置。

- [ ] **Step 2: 验证配置库内容与 Docker 排除规则**

Run:

```powershell
D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_enterprise_config_library.py -q
git check-ignore -v config_library
Select-String -Path .dockerignore -Pattern '^config_library/$'
```

Expected: 配置库测试通过；`.dockerignore` 明确包含 `config_library/`。若提交中的测试路径因恢复基线不同而失败，只调整测试路径，不改变两套配置内容。

### Task 3: 统一运行配置到 Main Control

**Files:**
- Delete: `domain_registry.yaml`
- Delete: `scenario_packs/**`
- Modify: `knowledge_mining/mining/api/routes/config.py`
- Modify: `knowledge_mining/mining/infra/domain_pack.py`
- Modify: `agent_serving_java/src/main/java/com/coremasterkb/serving/config/ServingProperties.java`
- Modify: `agent_serving_java/src/main/resources/application.yml`
- Modify: `agent_serving_fzl/src/main/java/com/coremasterkb/serving/config/ServingProperties.java`
- Modify: `agent_serving_fzl/src/main/resources/application.yml`
- Modify: `agent_serving_zdy/src/main/java/com/coremasterkb/serving/config/ServingProperties.java`
- Modify: `agent_serving_zdy/src/main/resources/application.yml`
- Modify: `docker-compose.yml`
- Modify: `docker/Dockerfile`
- Modify: `docker/supervisord.conf`
- Modify: `deploy-server.sh`
- Create: `tests/test_knowledge_mining_main_control_config.py`
- Create: `tests/test_no_legacy_scenario_pack_runtime_refs.py`

- [ ] **Step 1: 从配置整合分支恢复失败测试**

Run:

```powershell
git show feature/config-integration:tests/test_knowledge_mining_main_control_config.py
git show feature/config-integration:tests/test_no_legacy_scenario_pack_runtime_refs.py
```

用输出中的完整测试内容创建同名文件，不修改断言。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_knowledge_mining_main_control_config.py tests/test_no_legacy_scenario_pack_runtime_refs.py -q
```

Expected: 根目录配置仍存在、Java/Docker 仍引用旧路径的断言失败。

- [ ] **Step 3: 删除旧配置并移植路径调整**

Mining 的固定路径为：

```python
_MAIN_CONTROL_CONFIG_ROOT = _REPO_ROOT / "main_control_service" / "config"
_REGISTRY_PATH = _MAIN_CONTROL_CONFIG_ROOT / "domain_registry.yaml"
_SCENARIO_PACKS_DIR = _MAIN_CONTROL_CONFIG_ROOT / "scenario_packs"
```

删除所有 root fallback。Java 默认值统一为：

```yaml
domain-registry-path: ${DOMAIN_REGISTRY_PATH:../main_control_service/config/domain_registry.yaml}
scenario-packs-dir: ${SCENARIO_PACKS_DIR:../main_control_service/config/scenario_packs}
```

容器环境统一为：

```text
MAIN_CONTROL_CONFIG_DIR=/app/main_control_service/config
DOMAIN_REGISTRY_PATH=/app/main_control_service/config/domain_registry.yaml
SCENARIO_PACKS_DIR=/app/main_control_service/config/scenario_packs
```

Dockerfile 不再复制根目录 `domain_registry.yaml` 和 `scenario_packs/`。

- [ ] **Step 4: 运行配置测试并提交**

Run:

```powershell
D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_enterprise_config_library.py tests/test_knowledge_mining_main_control_config.py tests/test_no_legacy_scenario_pack_runtime_refs.py -q
```

Expected: 全部通过。

Commit: `refactor(config): use main control as sole runtime config source`

### Task 4: 建立 Domain 公共校验并隔离挖掘任务

**Files:**
- Create: `knowledge_mining/mining/api/domain_scope.py`
- Modify: `knowledge_mining/mining/api/routes/runs.py`
- Create: `tests/test_mining_domain_scope.py`
- Create: `tests/test_mining_runs_domain_contract.py`

- [ ] **Step 1: 编写公共校验失败测试**

```python
from fastapi import HTTPException
from knowledge_mining.mining.api.domain_scope import ensure_same_domain, require_domain


def test_require_domain_rejects_blank():
    try:
        require_domain("  ")
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("blank domain must fail")


def test_cross_domain_resource_is_hidden():
    try:
        ensure_same_domain("civil_engineering", "odn", "run")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("cross-domain resource must be hidden")
```

- [ ] **Step 2: 实现公共校验**

```python
from fastapi import HTTPException


def require_domain(domain: str) -> str:
    normalized = domain.strip()
    if not normalized:
        raise HTTPException(422, "domain is required")
    return normalized


def ensure_same_domain(actual: str | None, requested: str, resource: str) -> None:
    if actual != requested:
        raise HTTPException(404, f"{resource} not found")
```

- [ ] **Step 3: 编写 run 路由契约测试**

测试要求列表和详情接收 `domain: str = Query(...)`，详情 SQL 使用 `id = %s AND domain = %s`，并要求 progress、stages、documents、artifacts、trace、cancel、publish、resume 在访问前调用 `_require_run_domain`。

- [ ] **Step 4: 实现 run 归属守卫**

```python
async def _require_run_domain(pool, run_id: str, domain: str) -> dict:
    requested = require_domain(domain)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, domain, status FROM mining_runs WHERE id = %s AND domain = %s",
            [run_id, requested],
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"Run {run_id} not found")
    return dict(row)
```

所有 run 子路由接收必填 domain 并先调用守卫；发布和继续执行使用守卫返回的原 domain。

- [ ] **Step 5: 验证并提交**

Run: `D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_mining_domain_scope.py tests/test_mining_runs_domain_contract.py -q`

Expected: 全部通过。

Commit: `feat(mining): isolate runs by domain`

### Task 5: 隔离知识资产、构建发布和本体

**Files:**
- Modify: `knowledge_mining/mining/api/routes/knowledge.py`
- Modify: `knowledge_mining/mining/api/routes/builds.py`
- Modify: `knowledge_mining/mining/api/routes/ontology.py`
- Create: `tests/test_knowledge_domain_contract.py`
- Create: `tests/test_ontology_api_domain_contract.py`

- [ ] **Step 1: 编写失败契约测试**

知识测试要求每个业务路由有必填 domain，并通过：

```sql
asset_document_snapshot_links
JOIN asset_source_batches ON ...
WHERE asset_source_batches.domain = %s
```

过滤文档、快照、段落、检索单元和关系。构建/发布要求 `WHERE id = %s AND domain = %s`。本体 ID 操作要求匹配 `domain_id` 或调用统一归属守卫。

- [ ] **Step 2: 运行并确认失败**

Run: `D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_knowledge_domain_contract.py tests/test_ontology_api_domain_contract.py -q`

Expected: 尚未覆盖的详情和修改路由失败。

- [ ] **Step 3: 实现知识和构建归属校验**

复用镜像已经恢复的 `knowledge.py` domain/channel 查询，补齐所有详情和子资源。新增 `_require_document_domain`，只在请求 domain 的来源批次链接中选择最新快照。构建和发布的列表、详情和修改使用表自身的 `domain` 字段。

- [ ] **Step 4: 实现本体归属校验**

所有 `/ontology`、`/mentions`、`/graph` 业务路由接收对外参数 `domain`，内部映射到 `domain_id`。候选、mention、entity、evidence 的详情和修改必须按 ID 与 `domain_id` 同时查询；批量请求含跨 domain ID 时整体返回 404。

- [ ] **Step 5: 验证并提交**

Run: `D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_knowledge_domain_contract.py tests/test_ontology_api_domain_contract.py -q`

Expected: 全部通过。

Commit: `feat(mining): isolate knowledge and ontology by domain`

### Task 6: 隔离 LLM 任务与模板

**Files:**
- Create: `llm_service/api/domain_scope.py`
- Modify: `llm_service/api/stats.py`
- Modify: `llm_service/api/tasks.py`
- Modify: `llm_service/api/results.py`
- Modify: `llm_service/api/templates.py`
- Create: `llm_service/tests/test_domain_scope_api.py`

- [ ] **Step 1: 编写失败测试**

在测试数据库创建两个 `knowledge_domain` 的任务，验证列表和统计只返回请求 domain；详情、request、result、attempts、events 和 cancel 在 domain 不匹配时返回 404；缺少 domain 返回参数校验错误。

- [ ] **Step 2: 实现任务守卫**

```python
async def require_task_domain(db, task_id: str, domain: str) -> dict:
    row = await db.fetchone(
        "SELECT * FROM agent_llm_tasks WHERE id = %s AND knowledge_domain = %s",
        (task_id, require_domain(domain)),
    )
    if not row:
        raise HTTPException(404, "task not found")
    return dict(row)
```

统计和列表按 `knowledge_domain` 过滤；模板只返回当前 domain 加全局回退模板，不返回其他命名 domain 的模板。

- [ ] **Step 3: 验证并提交**

Run: `D:\software\anaconda\envs\kb\python.exe -m pytest llm_service/tests/test_domain_scope_api.py llm_service/tests/test_api.py -q`

Expected: 使用安全测试库时通过；若缺少测试库配置，先运行不连接数据库的契约子集并记录 BLOCKED，不使用生产数据库。

Commit: `feat(llm): isolate task APIs by domain`

### Task 7: 前端显式传 Domain 并隔离切换状态

**Files:**
- Modify: `kb-ui/src/api/mining.ts`
- Modify: `kb-ui/src/api/llm.ts`
- Modify: `kb-ui/src/api/serving.ts`
- Modify: `kb-ui/src/stores/mining.ts`
- Modify: `kb-ui/src/views/DashboardView.vue`
- Modify: `kb-ui/src/views/mining/RunsView.vue`
- Modify: `kb-ui/src/views/mining/CreateRunView.vue`
- Modify: `kb-ui/src/views/mining/RunDetailView.vue`
- Modify: `kb-ui/src/views/mining/RunDocumentDetailView.vue`
- Modify: `kb-ui/src/views/knowledge/DocumentsView.vue`
- Modify: `kb-ui/src/views/knowledge/DocumentDetailView.vue`
- Modify: `kb-ui/src/views/knowledge/GraphView.vue`
- Modify: `kb-ui/src/views/knowledge/EntityGraphView.vue`
- Modify: `kb-ui/src/views/knowledge/MentionReviewView.vue`
- Modify: `kb-ui/src/views/knowledge/OntologyView.vue`
- Modify: `kb-ui/src/views/knowledge/OntologyReviewView.vue`
- Modify: `kb-ui/src/views/knowledge/OntologyGraphView.vue`
- Modify: `kb-ui/src/views/LlmView.vue`
- Modify: `kb-ui/src/views/llm/LlmTaskDetailView.vue`
- Modify: `kb-ui/src/views/SearchView.vue`
- Create: `tests/test_kb_ui_domain_contract.py`

- [ ] **Step 1: 编写失败的前端契约测试**

测试读取三个 API 文件，确认除 health 外的业务方法签名都接收 domain；扫描 store 和页面，禁止不带 `domainStore.currentDomain` 或函数参数的 mining/LLM/search 调用。

- [ ] **Step 2: 修改 API 方法签名**

GET/DELETE 示例：

```typescript
async getRun(runId: string, domain: string): Promise<MiningRun> {
  const { data } = await client.get(`/api/runs/${runId}`, { params: { domain } })
  return extractOne<MiningRun>(data)
}
```

POST 示例：

```typescript
async cancelRun(runId: string, domain: string): Promise<void> {
  await client.post(`/api/runs/${runId}/cancel`, undefined, { params: { domain } })
}
```

- [ ] **Step 3: 修改所有调用点与切换处理**

每次加载、刷新、轮询、详情和修改都传当前 domain。请求开始时保存 `const requestedDomain = domainStore.currentDomain`；赋值前比较当前 domain，变化时丢弃旧响应。详情 watcher 切换 domain 时先清空旧对象。

- [ ] **Step 4: 验证并提交**

Run:

```powershell
D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_kb_ui_domain_contract.py tests/test_hidden_advanced_graph_pages.py -q
npm run build
```

Expected: 契约测试通过；前端构建通过。若本机 npm 仍不生成 `.bin`，记录工具链 BLOCKED，不改源码规避环境。

Commit: `feat(ui): scope business requests by domain`

### Task 8: Serving 强制 Domain 并做最终审计

**Files:**
- Modify: `agent_serving_java/src/main/java/com/coremasterkb/serving/application/SearchService.java`
- Modify: `agent_serving_java/src/test/java/com/coremasterkb/serving/api/SearchControllerTest.java`
- Modify: `docs/deployment/cmkb0708-recovery-report.md`

- [ ] **Step 1: 编写缺少 domain 的失败测试**

```java
mockMvc.perform(post("/api/v1/search")
        .contentType(MediaType.APPLICATION_JSON)
        .content("{\"query\":\"test\"}"))
    .andExpect(status().isBadRequest());
```

- [ ] **Step 2: 删除检索默认 domain 回退**

`SearchService.search()` 对 null/blank domain 抛出参数错误，并继续使用现有 DomainRegistry 和 active release 查询合法 domain；不引入请求头回退。

- [ ] **Step 3: 运行 Java 测试**

Run: `mvn -q -Dtest=SearchControllerTest test`

Workdir: `agent_serving_java`

Expected: PASS；本机没有 Maven/JDK 时记录 BLOCKED。

- [ ] **Step 4: 运行全量可用验证**

Run:

```powershell
D:\software\anaconda\envs\kb\python.exe -m pytest tests/test_hidden_advanced_graph_pages.py tests/test_enterprise_config_library.py tests/test_knowledge_mining_main_control_config.py tests/test_no_legacy_scenario_pack_runtime_refs.py tests/test_mining_domain_scope.py tests/test_mining_runs_domain_contract.py tests/test_knowledge_domain_contract.py tests/test_ontology_api_domain_contract.py tests/test_kb_ui_domain_contract.py -q
D:\software\anaconda\envs\kb\python.exe -m compileall -q knowledge_mining llm_service main_control_service mcp_server reset_db.py
git diff --check
git status --short
```

Expected: 静态契约和编译通过；状态只包含本任务计划内文件。

- [ ] **Step 5: 更新审计报告并提交**

报告记录每条验证的 PASS、FAIL、SKIP 或 BLOCKED，不记录凭据值。确认 staged 文件不含 `.env`、JAR、WAR、dist、`node_modules`、Docker tar 或 Python 缓存。

Commit: `chore(deploy): audit cmkb0708 domain isolation`

### Task 9: 保持本地交付并阻止危险推送

**Files:**
- Inspect only: Git history and tracked configuration files.

- [ ] **Step 1: 检查本地状态和远端跟踪**

Run:

```powershell
git status --short
git branch -vv
git config --get-regexp '^branch\.deploy/cmkb0708\.(remote|merge)$'
```

Expected: 工作区干净，`deploy/cmkb0708` 没有 upstream。

- [ ] **Step 2: 检查待推送历史的敏感配置风险**

只输出命中文件路径，不输出值。若历史包含明文凭据，保持本地并在报告中标记“禁止直接 push”；凭据轮换和干净历史重建必须在用户测试确认后的独立步骤完成。

- [ ] **Step 3: 交付用户测试**

测试目录保持：

```text
E:\MyProjects\KnowledgeBase-cmkb0708-recovery
```

不执行 `git push`、不创建 tag、PR 或 Release。
