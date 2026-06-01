# main_control_service 审查与演进规划

> 审查日期：2026-06-01
> 文件数量：6 个 Python 文件（非常精简）

## 1. 目录结构

```
main_control_service/
├── __init__.py
├── __main__.py                   # 模块入口
├── main.py                       # FastAPI 应用
├── config.py                     # 配置管理
├── service.py                    # 核心 CRUD 服务
├── proxy.py                      # 反向代理
└── config/                       # 配置文件目录
    ├── system/
    │   ├── database.yaml         # 数据库配置
    │   └── llm_service.yaml      # LLM 服务配置
    ├── domain_registry.yaml      # 域注册表
    └── scenario_packs/
        └── cloud_core_network/
            └── domain.yaml       # 领域配置包
```

## 2. API 端点

| 路由 | 功能 | 状态 |
|------|------|------|
| `GET /health` | 健康检查 | ✅ |
| **系统配置** | | |
| `GET /api/v1/system` | 列出系统配置文件 | ✅ |
| `GET /api/v1/system/{name}` | 获取系统配置（JSON） | ✅ |
| `GET /api/v1/system/{name}/raw` | 获取系统配置（YAML 原文） | ✅ |
| `PUT /api/v1/system/{name}/raw` | 更新系统配置（原子写） | ✅ |
| **领域管理** | | |
| `GET /api/v1/domains` | 列出所有领域 | ✅ |
| `GET /api/v1/domains/{id}` | 获取领域详情 | ✅ |
| `GET /api/v1/domains/{id}/raw` | 获取领域配置（YAML 原文） | ✅ |
| `POST /api/v1/domains` | 创建领域（含默认 scenario_pack） | ✅ |
| `PUT /api/v1/domains/{id}/raw` | 更新领域配置 | ✅ |
| `DELETE /api/v1/domains/{id}` | 删除领域 | ✅ |
| `GET /api/v1/domains/{id}/scenario` | 获取 scenario pack | ✅ |
| `GET /api/v1/domains/{id}/scenario/raw` | 获取 scenario pack（YAML） | ✅ |
| `PUT /api/v1/domains/{id}/scenario/raw` | 更新 scenario pack | ✅ |
| **反向代理** | | |
| `POST /api/v1/proxy/{domain_id}/{service}/{path}` | 反向代理到下游服务 | ✅ |

## 3. 核心功能

- **YAML CRUD**：原子写入（tempfile + os.replace）
- **YAML 校验**：写入前 parse 验证
- **自动创建默认 scenario_pack**：新建域时自动生成
- **反向代理**：支持所有 HTTP 方法，代理到 mining/serving/llm 服务
- **配置集中管理**：作为所有服务的配置中心

## 4. 相比 05-28 的变更

- LLM 模板格式修复（json_array → json_object）
- 代理绕过修复（trust_env=False）

## 5. 工业级演进规划

### Phase 1: 配置变更通知（高优先级）

**问题**：修改配置后无法自动通知其他服务 reload

实现方案：
1. 配置变更时发送 webhook/事件通知
2. 各服务监听通知，调用 `/admin/reload-config`
3. 可选方案：
   - 简单：修改后返回 `{ "notify": ["llm_service", "mining"] }` 前端提示用户手动触发
   - 完整：内嵌消息队列（Redis Pub/Sub）

**预估工作量**：2-3 天

### Phase 2: 配置版本控制（中等优先级）

**问题**：YAML 文件无版本历史，误操作无法回滚

实现方案：
1. 每次写入前备份当前版本到 `config/history/` 目录
2. 添加 `GET /api/v1/system/{name}/history` 端点查看历史
3. 添加 `POST /api/v1/system/{name}/rollback` 端点回滚
4. 保留最近 10 个版本

**预估工作量**：2-3 天

### Phase 3: 配置校验增强（中等优先级）

**问题**：YAML 校验只检查语法，不检查语义

实现方案：
1. 为每种配置定义 JSON Schema
2. 写入前验证 schema 合规性
3. 验证关键配置项（数据库 URL 格式、模型名称等）
4. 返回详细的校验错误信息

**预估工作量**：2-3 天

### Phase 4: 服务健康聚合（低优先级）

**问题**：无法在控制面查看所有服务的健康状态

实现方案：
1. `GET /api/v1/health/all` — 聚合所有服务健康状态
2. 定期轮询各服务 `/health` 端点
3. 缓存结果（TTL 30s）
4. 前端 Dashboard 展示

**预估工作量**：1-2 天

## 6. 完成度评估

| 维度 | 完成度 | 备注 |
|------|--------|------|
| YAML CRUD | **98%** | 原子写入+校验 |
| 反向代理 | **90%** | 基本功能完整 |
| 配置通知 | **0%** | 未实现 |
| 版本控制 | **0%** | 未实现 |
| Schema 校验 | **20%** | 仅 YAML 语法校验 |
