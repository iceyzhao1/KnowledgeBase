# llm_service 审查与演进规划

> 审查日期：2026-06-01
> 自 05-28 以来变更：10 次 commit（主要是 bug 修复）

## 1. 目录结构

```
llm_service/
├── main.py                       # FastAPI 应用入口
├── __main__.py                   # Python 模块入口
├── config.py                     # 配置管理（从 main_control_service 加载）
├── pg_config.py                  # PostgreSQL 配置
├── pg_schema.py                  # Schema 定义
├── db.py                         # 数据库连接
├── models.py                     # 数据模型
├── client.py                     # 内部客户端
├── api/
│   ├── health.py                 # 健康检查
│   ├── tasks.py                  # 任务 CRUD（提交/查询/取消/重试）
│   ├── templates.py              # Prompt 模板管理
│   ├── results.py                # 结果查询
│   ├── stats.py                  # 统计（token 用量等）
│   ├── model_api.py              # 直接模型调用（embed/rerank）
│   └── admin.py                  # 管理端点（reload config）
├── providers/
│   ├── base.py                   # Provider 基类
│   ├── model_base.py             # 模型 Provider 基类
│   ├── openai_compatible.py      # OpenAI 兼容 Provider
│   ├── bigmodel_models.py        # 智谱 BigModel Provider
│   ├── mock.py                   # Mock Provider（测试）
│   └── utils.py                  # 工具函数
├── runtime/
│   ├── service.py                # 核心服务
│   ├── task_manager.py           # 任务管理器
│   ├── worker.py                 # Worker 并发执行
│   ├── template_registry.py      # 模板注册表
│   ├── event_bus.py              # 事件总线
│   ├── idempotency.py            # 幂等性处理
│   ├── parser.py                 # 输出解析器
│   └── model_service.py          # 模型服务
└── tests/（12个测试文件）
```

## 2. 相比 05-28 的关键变更

### 2.1 Double-Fail Bug 修复（重要）

**提交**: `e161efc, 1d83af1`

- Provider 错误（rate_limited, timeout）导致任务被标记为 failed 两次
- 修复后 Provider 错误正确传播到 TaskManager

### 2.2 Worker 异常处理修复

**提交**: `666f22a`

- Worker 吞掉异常导致任务卡在 `running` 状态
- 修复后异常正确传播

### 2.3 Stale Running Tasks 修复

**提交**: `b56aa27`

- 启动时自动回收 stale running tasks
- 添加 worker 诊断端点

### 2.4 代理绕过

**提交**: `30fbe7a, 5339b22, de7d7c3`

- httpx 客户端设置 `trust_env=False` + `proxy=None`

## 3. API 端点

| 路由 | 功能 | 状态 |
|------|------|------|
| `GET /health` | 健康检查（含 DB） | ✅ |
| **任务管理** | | |
| `POST /api/v1/tasks` | 提交 LLM 任务（异步） | ✅ |
| `POST /api/v1/tasks/embed` | 提交 Embedding 任务 | ✅ |
| `POST /api/v1/tasks/rerank` | 提交 Rerank 任务 | ✅ |
| `POST /api/v1/execute` | 同步执行（等结果） | ✅ |
| `GET /api/v1/tasks` | 任务列表（分页+过滤） | ✅ |
| `GET /api/v1/tasks/{id}` | 任务详情 | ✅ |
| `GET /api/v1/tasks/{id}/result` | 任务结果 | ✅ |
| `GET /api/v1/tasks/{id}/request` | 请求详情 | ✅ |
| `GET /api/v1/tasks/{id}/attempts` | 尝试记录 | ✅ |
| `GET /api/v1/tasks/{id}/events` | 事件日志 | ✅ |
| `POST /api/v1/tasks/{id}/cancel` | 取消任务 | ✅ |
| `POST /api/v1/tasks/{id}/retry` | 重试 | ✅ |
| `POST /api/v1/tasks/batch-cancel` | 批量取消 | ✅ |
| **Prompt 模板** | | |
| `POST /api/v1/templates` | 创建模板 | ✅ |
| `GET /api/v1/templates` | 模板列表 | ✅ |
| `GET /api/v1/templates/{key}` | 按 key 获取 | ✅ |
| `PUT /api/v1/templates/{id}` | 更新模板 | ✅ |
| `DELETE /api/v1/templates/{id}` | 归档模板 | ✅ |
| **统计** | | |
| `GET /api/v1/stats` | 全局统计 | ✅ |
| `GET /api/v1/stats/tokens` | Token 用量 | ✅ |
| **直接模型调用** | | |
| `POST /api/v1/models/embeddings` | Embedding（deprecated） | ⚠️ |
| `POST /api/v1/models/rerank` | Rerank（deprecated） | ⚠️ |
| **管理** | | |
| `POST /api/v1/admin/reload-config` | 重载配置 | ✅ |

## 4. 核心功能

- **Worker 并发执行**：可配置并发数（默认 2）
- **Lease Recovery**：自动回收超时 lease 的任务
- **幂等性**：支持 idempotency_key 去重
- **模板注册表**：从 DB 加载 prompt 模板，带缓存 TTL
- **事件总线**：通知任务状态变化
- **输出解析器**：支持 json_object / json_array / text
- **配置集中管理**：全部配置来自 main_control_service
- **多模型支持**：provider.models 配置 + active_model 切换
- **环境变量解析**：配置值支持 `${VAR}` 和 `${VAR:-default}` 语法

## 5. 工业级演进规划

### Phase 1: SSE 流式输出（高优先级）

当前不支持流式输出。SSE 是 LLM 服务的基本要求。

实现方案：
```python
from sse_starlette.sse import EventSourceResponse

@router.post("/api/v1/execute/stream")
async def execute_stream(request: LLMTaskRequest):
    async def event_generator():
        async for chunk in provider.stream(prompt):
            yield {"event": "token", "data": json.dumps({"text": chunk})}
        yield {"event": "done", "data": "{}}
    return EventSourceResponse(event_generator())
```

**预估工作量**：3-5 天

### Phase 2: 批量执行优化（中等优先级）

当前 `execute` 是同步等待单任务。高频场景需要批量执行。

实现方案：
- `POST /api/v1/execute/batch` — 批量提交 + 并行执行
- 内部使用 asyncio.gather 并发执行
- 返回聚合结果

**预估工作量**：2-3 天

### Phase 3: Token 预算控制（中等优先级）

当前无 token 预算限制，可能导致成本失控。

实现方案：
- 按 domain 设置每日/每月 token 预算
- 达到阈值后自动降级（切换到更便宜的模型）或拒绝请求
- 实时统计 + 告警

**预估工作量**：3-5 天

### Phase 4: 优先级队列（低优先级）

当前所有任务平等对待。高优先级场景需要 SLA 保证。

实现方案：
- 任务增加 `priority` 字段（high/medium/low）
- Worker 按优先级消费任务
- 高优先级任务插队执行

**预估工作量**：2-3 天

## 6. 完成度评估

| 维度 | 完成度 | 备注 |
|------|--------|------|
| 核心 CRUD | **98%** | 完整的任务生命周期管理 |
| Worker | **90%** | 并发执行、lease recovery 已实现 |
| 模板系统 | **95%** | 注册表+缓存+CRUD |
| 统计 | **85%** | Token 统计完善，缺预算控制 |
| 流式输出 | **0%** | 未实现 |
| 批量执行 | **0%** | 未实现 |
