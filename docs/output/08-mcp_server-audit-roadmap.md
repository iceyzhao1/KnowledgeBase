# mcp_server 审查与演进规划

> 审查日期：2026-06-01
> 目录名：mcp_server（注意不是 mcp_service）

## 1. 目录结构

```
mcp_server/
├── __init__.py           # 版本号
├── __main__.py           # 入口
├── server.py             # FastMCP 服务（工具定义 + 指令）
├── client.py             # HTTP 客户端（纯透传到 serving）
├── schemas.py            # Pydantic 模型
└── README.md             # 文档
```

## 2. MCP 工具

| 工具名 | 功能 | 状态 |
|--------|------|------|
| `search_knowledge` | 检索云核心网知识库，返回证据包 | ✅ |
| `health_check` | 检查知识库可用性 | ⚠️ 已实现但**未对外暴露**（被注释掉） |

## 3. 架构

MCP Server 是纯透传层：
```
MCP Client → FastMCP server → httpx → agent_serving_java (POST /api/v1/search)
```

- `client.py`：纯 HTTP passthrough，不做任何业务逻辑
- `server.py`：FastMCP 工具定义 + 完整的推理护栏指令
- `schemas.py`：SearchInput, EntityRef, HealthResult

## 4. 指令系统

内嵌完整的推理护栏和回答行为指南：
- evidence_role 分类体系（direct_answer / support / contrast / background / missing）
- 回答行为规范（证据充分/部分/不足）
- 推理护栏（不编造、不脑补、不宣称因果关系）
- 三层内容区分

## 5. 工业级演进规划

### Phase 1: 暴露 health_check（高优先级）

health_check 已实现但被注释掉。应恢复并暴露。

```python
@mcp.tool()
def health_check() -> HealthResult:
    """检查知识库是否可用。不可用时不要编造知识，告知用户当前无法查询。"""
    return _health_check()
```

**预估工作量**：0.5 天（取消注释）

### Phase 2: 多域切换（中等优先级）

当前 domain 参数固定为 `cloud_core_network`。需要支持动态域切换。

实现方案：
1. `search_knowledge` 的 domain 参数已支持（默认 cloud_core_network）
2. 添加 `list_domains` 工具，从 main_control_service 获取可用域列表
3. 添加域描述信息，帮助 MCP Client 理解各域的内容范围

**预估工作量**：1-2 天

### Phase 3: 缓存层（低优先级）

MCP Server 当前无缓存，每次请求都打到 serving。

实现方案：
- 使用 `functools.lru_cache` 或 `cachetools.TTLCache`
- 查询哈希（query + domain + scope）作为 key
- TTL 5-10 分钟
- 减少对 serving 的重复请求

**预估工作量**：1 天

### Phase 4: 更多工具（低优先级）

可添加的工具：
- `get_document` — 获取指定文档内容
- `list_documents` — 列出知识库文档
- `get_segment` — 获取指定 segment

**预估工作量**：2-3 天

## 6. 完成度评估

| 维度 | 完成度 | 备注 |
|------|--------|------|
| 核心功能 | **85%** | search_knowledge 完整，health_check 未暴露 |
| 指令系统 | **95%** | 推理护栏完整 |
| 多域支持 | **70%** | domain 参数已支持，缺 list_domains |
| 缓存 | **0%** | 未实现 |
