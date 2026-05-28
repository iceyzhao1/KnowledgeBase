# LLM Service 配置收口与调用方改造方案

> 日期：2026-05-28
> 状态：待审查
> 范围：llm_service、knowledge_mining、agent_serving_java

---

## 1. 背景

目标是让 llm_service 成为**所有模型调用的唯一配置中心**。调用方（knowledge_mining、agent_serving_java）不应传递任何模型级别的参数（模型名、维度、API Key 等），只需告诉 llm_service "我要做 embedding"或"我要做 rerank"。

当前问题：
- 调用方重复配置了模型名、维度、API Key
- llm_service 只支持单模型，不支持多模型切换
- 内网模型可能需要自定义 Header，已有支持但不完整

---

## 2. llm_service 配置参数全景

### 2.1 改造后的目标 YAML 结构

```yaml
host: "0.0.0.0"
port: 8900

provider:
  base_url: "https://api.deepseek.com/chat/completions"
  api_key: "sk-xxx"
  model: "deepseek-chat"
  headers: {}
  timeout: 30
  bypass_proxy: false

embedding:
  base_url: "https://open.bigmodel.cn/api/paas/v4/embeddings"
  api_key: "xxx"
  model: "embedding-3"
  dimensions: 1024          # 可选，不配就不传给提供商
  timeout: 60
  bypass_proxy: false
  headers: {}

rerank:
  base_url: "https://open.bigmodel.cn/api/paas/v4/rerank"
  api_key: "xxx"
  model: "rerank-pro"
  timeout: 60
  bypass_proxy: false
  headers: {}

worker:
  concurrency: 2
  poll_interval: 1.0

task:
  default_max_attempts: 3
  retry_backoff_base: 2.0
  retry_backoff_max: 60.0
  execute_timeout: 60
  lease_duration: 300
  lease_recovery_interval: 30.0

template:
  cache_ttl: 300.0
```

**对比当前结构的变化**：
- **删除 `model` 公共分组**：原来 `model.timeout`、`model.bypass_proxy`、`model.extra_headers` 职责混乱（同时给 Chat 和 Embedding/Rerank 用），现在各服务自己管自己的
- `model.extra_headers` → 拆分为 `embedding.headers` 和 `rerank.headers`
- `embedding` 新增 `timeout`、`bypass_proxy`、`headers`（原来藏在 `model` 分组里）
- `rerank` 新增 `timeout`、`bypass_proxy`、`headers`（同上）
- `embedding.dimensions` 改为可选（不配就不传给提供商）

### 2.2 各参数含义与用途

| 配置路径 | 含义 | 消费位置 | 现状 |
|---|---|---|---|
| `host` | 服务监听地址 | `__main__.py` → uvicorn.bind | ✅ 正常 |
| `port` | 服务监听端口 | `__main__.py` → uvicorn.bind | ✅ 正常 |
| **provider（Chat LLM 配置）** | | | |
| `provider.base_url` | Chat LLM 接口地址 | `main.py` → OpenAICompatibleProvider | ✅ 正常 |
| `provider.api_key` | Chat LLM API Key | `main.py` → OpenAICompatibleProvider | ✅ 正常 |
| `provider.model` | Chat 默认模型名 | `service.py` → self._default_model | ⚠️ 只支持单个模型 |
| `provider.timeout` | Chat 请求超时(秒) | `main.py` → OpenAICompatibleProvider | ✅ 正常 |
| `provider.bypass_proxy` | Chat 是否绕过代理 | `main.py` → OpenAICompatibleProvider | ✅ 正常 |
| `provider.headers` | Chat 自定义请求头 | `main.py` → OpenAICompatibleProvider | ✅ 正常 |
| **embedding（向量模型配置）** | | | |
| `embedding.base_url` | Embedding 接口地址 | `main.py` → BigModelProvider | ✅ 正常 |
| `embedding.api_key` | Embedding API Key | `main.py` → BigModelProvider | ✅ 正常 |
| `embedding.model` | Embedding 模型名 | `main.py` → BigModelProvider / ModelService | ⚠️ 只支持单个模型 |
| `embedding.dimensions` | 向量维度（可选） | `model_service.py` → BigModelProvider.embed | ⚠️ 配了但未被 ModelService 消费；改为：不配就不传给提供商，配了就作为默认值 |
| `embedding.timeout` | Embedding 请求超时(秒) | `main.py` → BigModelProvider | 🔄 从 `model.timeout` 迁移过来 |
| `embedding.bypass_proxy` | Embedding 是否绕过代理 | `main.py` → BigModelProvider | 🔄 从 `model.bypass_proxy` 迁移过来 |
| `embedding.headers` | Embedding 自定义请求头 | `main.py` → BigModelProvider | 🔄 从 `model.extra_headers` 迁移过来 |
| **rerank（重排模型配置）** | | | |
| `rerank.base_url` | Rerank 接口地址 | `main.py` → BigModelProvider | ✅ 正常 |
| `rerank.api_key` | Rerank API Key | `main.py` → BigModelProvider | ✅ 正常 |
| `rerank.model` | Rerank 模型名 | `main.py` → BigModelProvider / ModelService | ⚠️ 只支持单个模型 |
| `rerank.timeout` | Rerank 请求超时(秒) | `main.py` → BigModelProvider | 🔄 从 `model.timeout` 迁移过来 |
| `rerank.bypass_proxy` | Rerank 是否绕过代理 | `main.py` → BigModelProvider | 🔄 从 `model.bypass_proxy` 迁移过来 |
| `rerank.headers` | Rerank 自定义请求头 | `main.py` → BigModelProvider | 🔄 从 `model.extra_headers` 迁移过来 |
| **worker（异步任务 Worker）** | | | |
| `worker.concurrency` | 并发 Worker 数 | `main.py` → Worker | ✅ 正常 |
| `worker.poll_interval` | Worker 轮询间隔(秒) | `main.py` → Worker | ✅ 正常 |
| **task（任务管理）** | | | |
| `task.default_max_attempts` | 任务最大重试次数 | `service.py` → TaskManager | ✅ 正常 |
| `task.retry_backoff_base` | 退避基数(秒) | `service.py` → TaskManager | ✅ 正常 |
| `task.retry_backoff_max` | 退避上限(秒) | `service.py` → TaskManager | ✅ 正常 |
| `task.execute_timeout` | 任务执行超时(秒) | — | ⚠️ 声明但**未被消费** |
| `task.lease_duration` | 任务租约时长(秒) | `service.py` → TaskManager / execute | ✅ 正常 |
| `task.lease_recovery_interval` | 租约回收间隔(秒) | `main.py` → LeaseRecovery | ✅ 正常 |
| **template（模板缓存）** | | | |
| `template.cache_ttl` | 模板缓存 TTL(秒) | `main.py` → TemplateRegistry | ✅ 正常 |

### 2.3 残留默认值

| 文件 | 位置 | 默认值 | 严重性 | 说明 |
|---|---|---|---|---|
| `model_service.py:32` | 构造函数 | `default_embedding_model="embedding-3"` | 中 | 死代码，生产路径通过 dig() 传入 |
| `model_service.py:33` | 构造函数 | `default_rerank_model=""` | 中 | 死代码 |
| `worker.py:65-66` | 构造函数 | `concurrency=4, poll_interval=1.0` | 低 | 死代码 |
| `worker.py:340` | 构造函数 | `interval=30.0` (LeaseRecovery) | 低 | 死代码 |
| `config.py` | 模块级 | `CONTROL_PLANE_BASE_URL="http://localhost:8910"` | 可接受 | 唯一引导参数 |

---

## 3. 调用方冗余分析

### 3.1 knowledge_mining（Python）

#### 冗余参数清单

| 冗余参数 | 文件位置 | 传递方式 | 应该怎样 |
|---|---|---|---|
| `model="embedding-3"` | `mining/infra/embedding.py` → `/api/v1/models/embeddings` | HTTP payload 字段 | 不传，llm_service 用自己配置的 |
| `dimensions=1024` | `mining/infra/embedding.py` → `/api/v1/models/embeddings` | HTTP payload 字段 | 不传，llm_service 用自己配置的 |
| `embedding_model` | `mining/infra/mining_config.py` | 配置项 | 删除，由 llm_service 管理 |
| `embedding_dimensions` | `mining/infra/mining_config.py` | 配置项 | 删除，由 llm_service 管理 |
| `mining_llm_bypass_proxy` | `mining/infra/mining_config.py` | 配置项 | 删除，llm_service 内部已配置 |
| `base_url` | 3 个 stage + llm_client + embedding | 构造函数参数 | 保留 `llm_service_url`（调用方需知道服务地址），但删除 `bypass_proxy` |
| `bypass_proxy` | `enrich/__init__.py`, `relations/__init__.py`, `retrieval_units/__init__.py` | 构造函数参数 | 删除，llm_service 内部已配置 |

#### 涉及文件

- `knowledge_mining/mining/infra/embedding.py` — `LLMServiceEmbeddingGenerator` 类
- `knowledge_mining/mining/infra/mining_config.py` — `MiningConfig` 类
- `knowledge_mining/mining/infra/llm_client.py` — `LlmClient` 类
- `knowledge_mining/mining/stages/enrich/__init__.py` — `LlmEnricher`
- `knowledge_mining/mining/stages/relations/__init__.py` — `DiscourseRelationBuilder`
- `knowledge_mining/mining/stages/retrieval_units/__init__.py` — `LlmQuestionGenerator`
- `knowledge_mining/mining/jobs/run.py` — pipeline 组装
- `knowledge_mining/mining/api/routes/config.py` — 配置 API 暴露

### 3.2 agent_serving_java（Java）

#### 冗余参数清单

| 冗余参数 | 文件位置 | 传递方式 | 应该怎样 |
|---|---|---|---|
| `"embedding-3"` 硬编码 | `EmbeddingClient.java` | 构造函数参数 | 不传，llm_service 用默认 |
| `dimensions=1024` 硬编码 | `ServingProperties.java:37` | 配置属性 | 不传，llm_service 用默认 |
| `"rerank-pro"` 硬编码 | `LlmServiceReranker.java:31` | 构造函数参数 | 不传，llm_service 用默认 |
| `"rerank-pro"` 默认值 | `ServingProperties.java:28,38` | 配置默认值 | 删除 |
| `topN` 参数 | `LlmServiceReranker.java:99` | API 调用参数 | 改为可选，不传用 llm_service 默认 |
| ZhipuClient 独立 API Key | `ZhipuClient.java:28` | 直连大模型 | 删除直连路径，统一走 llm_service |

#### 涉及文件

- `agent_serving_java/.../EmbeddingClient.java` — embedding 调用
- `agent_serving_java/.../LlmServiceReranker.java` — rerank 调用
- `agent_serving_java/.../LlmClient.java` — LLM HTTP 客户端
- `agent_sending_java/.../ServingProperties.java` — 配置属性
- `agent_serving_java/.../ServingBeans.java` — Spring Bean 配置
- `agent_serving_java/.../ZhipuClient.java` — Zhipu 直连客户端

---

## 4. 改造方案

### 4.1 Phase 1 — llm_service 接口改造（收口）

#### Task 1: Embedding API 参数可选化

**文件**: `llm_service/api/model_api.py`, `llm_service/runtime/model_service.py`, `llm_service/config.py`

**核心原则**：调用方不传 → llm_service 查自己配置 → 配置有就传给提供商，配置没有就不传。

**改造内容**:
- `/api/v1/models/embeddings` 请求体中 `model` 和 `dimensions` 改为可选
- 不传时使用 YAML 配置的 `embedding.model` 和 `embedding.dimensions`
- `embedding.dimensions` 从 _REQUIRED_PATHS 移到可选配置（不配就不传给提供商）
- 调用方只需传 `input`（文本列表），不再需要传 model/dimensions

**改前** (`model_service.py`):
```python
async def embed(self, body: EmbeddingRequest):
    model = body.model or self._default_embedding_model
    # dimensions 直接透传 body.dimensions（None 就不传给提供商）
    # 但如果调用方不传，YAML 配的 dimensions 也没被使用
    raw = await self._provider.embed(texts, model=model, dimensions=body.dimensions)
```

**改后**:
```python
async def embed(self, body: EmbeddingRequest):
    model = body.model or self._default_embedding_model
    # 不传时用 YAML 配置；YAML 也没配就不传给提供商
    dimensions = body.dimensions or self._default_embedding_dimensions  # 可能为 None
    raw = await self._provider.embed(texts, model=model, dimensions=dimensions)
```

**dimensions 参数传递逻辑**：

| 调用方是否传 | YAML 是否配 | 最终传给提供商 | 说明 |
|---|---|---|---|
| 不传 | 不配 | **不传** | 提供商用模型默认维度 |
| 不传 | 配了 1024 | **传 1024** | llm_service 用自己的配置 |
| 传了 768 | 任意 | **传 768** | 调用方显式覆盖（兼容旧调用） |

#### Task 2: Rerank API 参数可选化

**文件**: `llm_service/api/model_api.py`, `llm_service/runtime/model_service.py`

**改造内容**:
- `/api/v1/models/rerank` 请求体中 `model` 改为可选
- 不传时使用 YAML 配置的 `rerank.model`
- `top_n` 保留为可选参数（这是业务参数，不是模型参数）
- 逻辑同 Task 1：调用方不传 → 用配置；配置也没有 → 用提供商默认

#### Task 3: 支持多 LLM 模型配置 + 激活切换

**文件**: `llm_service/config.py`, `main_control_service/config/system/llm_service.yaml`

**改造内容**:

YAML 新增多模型配置结构：
```yaml
provider:
  base_url: "https://api.deepseek.com/chat/completions"
  api_key: "sk-xxx"
  active_model: "deepseek-chat"   # 当前激活的模型
  headers: {}
  timeout: 30
  bypass_proxy: false
  models:
    deepseek-chat:
      model: "deepseek-chat"
    qwen-plus:
      model: "qwen-plus"
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
      api_key: "sk-xxx"
      headers:
        X-Custom-Header: "value"
```

- `provider.active_model` 指定当前使用的模型
- `provider.models` 是可选的模型列表，每个模型可覆盖 base_url/api_key/headers
- 如果 `provider.models` 不存在，则走当前的 `provider.model` 单模型模式（向后兼容）
- 调用方不传模型名时使用 active_model

### 4.2 Phase 2 — knowledge_mining 清理

#### Task 4: 移除冗余配置和传参

**改造内容**:
- `MiningConfig` 中删除 `embedding_model`、`embedding_dimensions`、`mining_llm_bypass_proxy`
- `LLMServiceEmbeddingGenerator` 构造函数中删除 `model`、`dimensions` 参数
- embedding API 调用不再传 `model` 和 `dimensions`
- 3 个 stage 类（enrich/relations/retrieval_units）中删除 `bypass_proxy` 参数
- `LlmClient` 中删除 `bypass_proxy`
- 保留 `llm_service_url`（调用方需要知道服务地址）

### 4.3 Phase 3 — agent_serving_java 清理

#### Task 5: EmbeddingClient 清理

- `EmbeddingClient` 构造函数中删除 `model` 和 `dimensions` 参数
- API 调用不再传 `model` 和 `dimensions`
- `ServingProperties` 中删除 embedding 相关的 model/dimensions 属性

#### Task 6: LlmServiceReranker 清理

- 删除 `model` 硬编码，API 调用不再传 `model`
- `top_n` 保留（业务参数）

#### Task 7: 移除 ZhipuClient 直连路径

- 删除 `ZhipuClient` 类（独立 API Key + 直连 Zhipu）
- 统一通过 `LlmClient` → llm_service 路径

### 4.4 Phase 4 — 清理验证

#### Task 8: 清理 llm_service 残留默认值

- 移除 `model_service.py` 构造函数的默认值
- 移除 `worker.py` 构造函数的默认值
- 消费 `task.execute_timeout` 或从 REQUIRED_PATHS 中移除

#### Task 9: 全量验证

- 本地启动主系统 + llm_service，验证 embedding/rerank 不传模型参数
- knowledge_mining 执行一次挖掘 pipeline
- Docker compose 重建并验证
- 热更新测试：改 YAML → reload-config → 验证新配置生效

---

## 5. 风险与注意事项

1. **向后兼容**：embedding/rerank API 的 model/dimensions 参数改为可选，不传用默认，传了仍然生效。不会破坏现有调用。
2. **多模型切换**：新增 models 配置是可选的，不配置时走单模型模式，完全向后兼容。
3. **Java 测试文件**：agent_serving_java 的集成测试硬编码了模型名，需要一并更新。
4. **knowledge_mining_zym**：不在本次改造范围，属于旧代码。
5. **热更新**：多模型配置变更后通过 `/api/v1/admin/reload-config` 热更新生效。

---

## 6. 执行顺序

```
Phase 1 (llm_service 改造)
  Task 1 → Task 2 → Task 3
  ↓
Phase 2 (knowledge_mining 清理)
  Task 4
  ↓
Phase 3 (agent_serving_java 清理)
  Task 5 → Task 6 → Task 7
  ↓
Phase 4 (清理验证)
  Task 8 → Task 9
```

Phase 1 和 Phase 2/3 可以并行准备，但 Phase 2/3 依赖 Phase 1 的接口改完。
