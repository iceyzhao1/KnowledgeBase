# cmkb0708 部署缺口补齐设计

## 目标

在本地冻结分支 `deploy/cmkb0708` 上补齐以下能力，同时保持挖掘算子化等其他开发功能不进入该分支：

1. 隐藏实体图谱、本体版本和本体图谱页面，并阻止直接 URL 访问。
2. 在共享 PostgreSQL 中对所有业务查询和修改实施 domain 隔离。
3. 让 `main_control_service/config` 成为唯一运行配置源。

本次修改继续保留在本地 `E:\MyProjects\KnowledgeBase-cmkb0708-recovery`，用户测试确认前不推送远端。

## 实现策略

采用选择性移植，不直接 merge 或 cherry-pick 其他功能分支：

- 复用 `feature/config-integration` 中已经验证过的路径调整和契约测试。
- 复用 `wip/config-domain-query-backup-20260713` 中的 Domain 隔离设计与实施计划。
- 参考算子化分支已有的导航隐藏思路，但只移植页面隐藏能力，不带入流程编排代码。
- 保留 `cmkb0708.tar` 恢复出的 Python 和配置源码作为当前部署基线。

## 页面隐藏

### 导航

从 `kb-ui/src/components/layout/Sidebar.vue` 删除以下三项导航及不再使用的图标导入：

- `/entities`：实体图谱
- `/ontology`：本体版本
- `/ontology/graph`：本体图谱

本次直接修改代码，不增加构建变量或运行配置开关。

### 路由

在 `kb-ui/src/router/index.ts` 中保留三个 URL 的明确路由记录，但将它们改成重定向到 `/`。这样直接输入地址、使用历史书签或从旧页面跳转时都会回到首页，而不是显示空页面。

实体确认 `/mentions/review` 和本体确认 `/candidates/review` 属于挖掘流程步骤，不在本次隐藏范围内。

原页面组件源码继续保留，不删除，方便以后重新开放。

## 配置整合

### 唯一运行配置源

运行时只允许读取：

```text
main_control_service/config/domain_registry.yaml
main_control_service/config/scenario_packs/<domain>/domain.yaml
main_control_service/config/system/*.yaml
```

删除项目根目录的：

```text
domain_registry.yaml
scenario_packs/
```

Mining 不再保留根目录回退；Java Serving、Docker Compose、Dockerfile、Supervisor 和部署脚本统一指向 `main_control_service/config`。

### 场景配置库

复用现有 `config_library` 结构保存站点和政企两套可替换配置：

```text
config_library/site/
config_library/enterprise/
```

`config_library` 继续排除在 Docker build context 之外。当前恢复 worktree 的激活配置保持不变，本次不自动切换成政企配置，也不改变用户服务器上的挂载配置。

### 配置测试

复用并适配以下契约测试：

- `tests/test_knowledge_mining_main_control_config.py`
- `tests/test_no_legacy_scenario_pack_runtime_refs.py`
- `tests/test_enterprise_config_library.py`

测试必须确认根目录配置已删除、运行代码没有旧路径回退、Docker/Java 使用主控配置路径、配置库不会进入镜像。

## Domain 隔离

### 数据流

1. Main Control 从激活的 `domain_registry.yaml` 返回已启用 domain。
2. 前端把用户选择保存在 `domainStore.currentDomain`。
3. 每个业务 API 方法显式接收并传递必填 `domain`。
4. GET/DELETE 使用查询参数；POST/PUT/PATCH 沿用接口现有请求体或查询参数约定。
5. 后端在 SQL 或资源归属守卫中使用 domain，不通过代理请求头隐式注入。

### 隔离范围

必须覆盖：

- 首页统计和最近任务。
- 挖掘任务列表、详情、进度、文档、产物、取消、发布和继续执行。
- 知识文档、快照、段落、检索单元和关系。
- 构建与发布版本。
- 本体版本、草稿、候选项、待确认实体、图实体和图关系。
- LLM 统计、任务、详情、请求、结果、尝试、事件、取消和模板。
- Serving 检索。

Domain 列表、Domain 配置、系统配置和健康检查继续保持全局。

### 后端规则

- 缺少或空 domain：参数校验失败，不能解释为全局查询。
- 列表、统计、计数和聚合：SQL 必须带 domain 条件，数据查询和计数查询使用相同条件。
- 详情和修改：按“资源 ID + domain”查询或先执行统一归属校验。
- 资源属于其他 domain：返回 HTTP 404，避免暴露其是否存在。
- 本体表的 `domain_id`、LLM 表的 `knowledge_domain` 和其他表的 `domain` 只在后端内部映射，对外参数统一叫 `domain`。
- 文档通过 `asset_document_snapshot_links -> asset_source_batches.domain` 继承归属；最新快照必须是当前 domain 内的最新链接。
- 历史 `NULL/default` 数据不自动归入其他 domain，历史迁移不在本次范围内。

### 前端规则

- `useMiningApi`、`useLlmApi` 和 `useServingApi.search` 的业务方法显式要求 domain。
- Store、首次加载、刷新、轮询、详情和修改操作都传 `domainStore.currentDomain`。
- 切换 domain 时先清理旧详情状态并重新加载。
- 旧请求响应返回时若 domain 已变化，不得覆盖新 domain 的页面状态。

## 错误处理

- 未注册或禁用 domain：HTTP 400。
- 缺少 domain：FastAPI/Pydantic 返回 422，Java 请求校验返回 400。
- 跨 domain 的资源 ID：HTTP 404。
- 缺少本地数据库测试配置：测试标记为 BLOCKED 并记录原因，不复制 `.env`、不伪造生产连接参数、不启用危险清库操作。

## 验证与提交顺序

修改按以下独立提交组织：

1. 隐藏三个页面并增加路由契约测试。
2. 整合运行配置并运行旧路径契约测试。
3. 实现 Mining、知识资产、本体和构建发布的 Domain 隔离。
4. 实现 LLM、Serving 和前端调用的 Domain 隔离。
5. 运行静态契约、Python 编译、可用单元测试、前端构建和 Java 测试，并更新恢复审计报告。

任何因本机缺少数据库、Maven 或前端工具链导致的验证阻塞都必须如实记录，不能声明为通过。

## 安全边界

- 不读取、显示或提交 `.env`。
- 不提交 JAR、前端 dist、`node_modules`、Python 缓存或 Docker tar。
- 当前本地恢复提交包含镜像带出的数据库凭据。功能测试阶段保持本地，不推送。
- 推送 GitHub 前必须从待推送历史中移除明文凭据，并更换已经暴露在镜像和本地提交中的相关凭据；不能直接推送当前提交链。

## 非目标

- 不合并挖掘算子化。
- 不增加鉴权系统或按用户授权 domain。
- 不为每个 domain 建独立数据库。
- 不迁移历史数据。
- 不切换当前激活配置为政企配置。
- 不推送远端、创建 tag 或 GitHub Release。
