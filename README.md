# CoreMasterKB

## Docker 部署

### 服务架构

单容器 All-in-One，6 个服务通过 supervisord 管理：

| 服务 | 容器内端口 | 说明 |
|------|-----------|------|
| nginx | 80 | 前端静态文件 + API 反向代理 |
| knowledge_mining | 8901 | 知识挖掘 |
| llm_service | 8900 | LLM 运行时 |
| main_control_service | 8910 | 主控配置中心 |
| agent_serving_java | 8081 | 检索服务 |
| mcp_server | 9000 | MCP 服务 |

前端通过 nginx 代理访问后端 API（`/api/mining`、`/api/serving`、`/api/llm`、`/api/control-plane`），不依赖 localhost。

### 本机构建

```bash
bash deploy-build.sh
```

生成 `cmkb.tar`（约 203MB）。

### 服务器部署

**传文件**：将以下文件传到服务器同一目录：

- `cmkb.tar`
- `docker-compose.yml`
- `deploy-server.sh`

**docker-compose.yml 注意**：服务器上需要切换两处配置：

1. **镜像来源**：注释掉 `build` 块，取消注释 `image: coremasterkb-app:latest`
2. **volume 挂载**：取消注释代码和配置挂载（本地开发可保持注释，直接用镜像内置代码）

yml 文件内有详细注释标注哪几行是本地、哪几行是服务器。

**执行部署**：

```bash
# 首次部署 或 强制用镜像版本覆盖所有代码
bash deploy-server.sh --force

# 后续只更新镜像，不覆盖本地改过的代码
bash deploy-server.sh
```

### 服务器管理

```bash
# 查看服务状态
docker compose exec app supervisorctl status

# 重启某个服务
docker compose exec app supervisorctl restart mining

# 重启所有服务
docker compose exec app supervisorctl restart all

# 查看日志
docker compose logs -f --tail 50

# 进入容器
docker compose exec app bash

# 停止 / 启动
docker compose down
docker compose up -d
```

### 代码更新

**Python 代码**：通过 volume 挂载，改宿主机文件后重启服务即可：

```bash
docker compose exec app supervisorctl restart mining
```

**Java / 前端**：需要重新构建镜像：

```bash
# 本机
bash deploy-build.sh

# 传 cmkb0604.tar 到服务器后
bash deploy-server.sh --force
```

### 配置文件

- `.env` — 数据库连接、API Key 等
- `domain_registry.yaml` — 领域注册

部署脚本会从镜像拷出这些文件到宿主机，可直接编辑。

## 服务启动方式

### 服务总览

整套系统共 6 个常驻服务，调用关系如下：

```text
kb-ui (浏览器, nginx:80)
  └─> main_control_service :8910   （配置中心 + 按 domain 反向代理）
        ├─> llm_service        :8900   （LLM 运行时底座）
        ├─> knowledge_mining   :8901   （离线挖掘 API）
        └─> agent_serving      :8081   （Java/Spring Boot 在线检索）

mcp_server :9000  ──> agent_serving :8081   （给外部 Agent / Claude Code 用）
```

要点：

- 前端 **kb-ui** 只与 **main_control_service** 通信（`/api/control-plane/...`），由它统一反代到后端各服务；前端不直连 llm / mining / serving。
- **agent_serving 是 Java（Spring Boot, Java 21 + MyBatis + PostgreSQL）**，不是 Python。仓库里的 `agent_serving/`（Python）、`agent_serving_fzl/`、`agent_serving_zdy/` 是参考/分身实现，生产编排用的是 `agent_serving_java/`。
- 旧的 `mining_ui.py`（NiceGUI）已废弃，挖掘可视化统一在 kb-ui。

### 方式一：Docker（推荐，一次拉起全部）

```bash
docker compose up -d        # 6 个服务一起启动
docker compose logs -f      # 查看日志
docker compose down         # 停止
```

入口：浏览器打开 `http://<宿主机>`（nginx :80）。

### 方式二：本地逐服务启动（开发调试）

启动顺序：llm → mining → control → serving → mcp → 前端。命令与 `docker/supervisord.conf` 一致：

| 顺序 | 服务 | 启动命令 | 端口 | 技术栈 |
|------|------|---------|------|--------|
| 1 | llm_service | `python -m llm_service` | 8900 | Python / FastAPI |
| 2 | knowledge_mining | `python -m knowledge_mining.mining.api` | 8901 | Python / FastAPI |
| 3 | main_control_service | `python -m main_control_service.main` | 8910 | Python / FastAPI |
| 4 | agent_serving | `java -jar agent_serving.jar` | 8081 | Java / Spring Boot |
| 5 | mcp_server | `python -m mcp_server --transport streamable-http --port 9000` | 9000 | Python / MCP |
| 6 | kb-ui | `cd kb-ui && npm install && npm run dev` | 5173(dev) | Vue3 / Vite |

各服务关键环境变量（详见 `docker/supervisord.conf` 与各自 README）：

```bash
# knowledge_mining
MINING_API_PORT=8901

# main_control_service
MAIN_CONTROL_HOST=0.0.0.0
MAIN_CONTROL_PORT=8910
MAIN_CONTROL_DB_PATH=./data/main_control_service/control_plane.db

# agent_serving (Java)
SCENARIO_PACKS_DIR=./scenario_packs
DOMAIN_REGISTRY_PATH=./domain_registry.yaml

# mcp_server
SERVING_URL=http://localhost:8081
```

agent_serving 需先构建 jar：

```bash
cd agent_serving_java
mvn -DskipTests package          # 产物在 target/agent-serving-*.jar
java -jar target/agent-serving-*.jar
```

kb-ui 生产构建（Docker 内由 nginx 提供静态文件）：

```bash
cd kb-ui
npm install
npm run build                    # 产物 dist/，对应 nginx root /app/kb-ui-dist
```

### 评估工具 runtime_eval（独立，可选）

`runtime_eval/` 是独立的运行态质量评测框架，不属于上面 6 个生产服务，用于评估
知识库查询 agent 的运行态效果。它**与被测服务解耦**：被测 agent 的回答经前端人工
上传，由框架内置大模型出题 + 裁判打分，最终产出按问题类型区分准确率、token、
时长的报告。被测服务是 Java 还是 Python、本地还是远程，都不影响本框架。

拆成两个常驻服务（要求 **Python ≥ 3.10**；默认 `py` 可能是 3.9，需显式 `py -3.10`）：

| 服务 | 模块 | 端口 | 说明 |
|------|------|------|------|
| eval-llm | `runtime_eval.eval_llm` | 8801 | 大模型服务：出题 `/generate-cases` + 裁判 `/judge`，封装 Provider 与密钥 |
| eval-api | `runtime_eval.eval_api` | 8800 | 后端 REST 编排 + 持久化，并托管前端 eval-web |

**启动前端 + 后端**（前端由 eval-api 自带托管，无需单独启动前端）。开两个终端，均在
仓库根的 `runtime_eval/` 目录下执行：

```bash
cd runtime_eval

# 终端 1：大模型服务（默认 8801）。mock provider 无需网络与 API key
py -3.10 -m runtime_eval.eval_llm --host 127.0.0.1 --port 8801

# 终端 2：后端 + 前端（默认 8800，自动托管 eval-web）
py -3.10 -m runtime_eval.eval_api --host 127.0.0.1 --port 8800
```

打开 <http://127.0.0.1:8800> 进入前端，按流程操作：
**建项目 → 上传测试文档 → 生成用例（或导入已有 YAML/JSON 用例）→ 人工上传被测
agent 的回答 → 评测 → 看报告**。

- 首次使用：复制 `runtime_eval/.env.example` 为 `runtime_eval/.env` 后按需改。
- Provider 通过 `runtime_eval/.env` 切换：`mock`（默认，零依赖跑通）/ `anthropic`（填
  `ANTHROPIC_API_KEY`）/ `llm_service`（复用仓库内 :8900）/ `claude_cli`（复用本机已登录的
  `claude -p` 命令做出题与裁判，本仓库无需任何 API Key）。详见 `runtime_eval/.env.example`。
- 文档解析支持 `.md`/`.txt` 与 `.chm`（微软编译帮助文档，需 Windows `hh.exe` 或 PATH
  上的 `7-Zip` 解压）；`.pdf`/`.docx` 等暂不支持，请先转 markdown。
- 除"生成层"（回答质量准确率）外，还支持"**检索层**"评测：人工上传被测检索器返回的
  证据，由框架 LLM 裁判相关性，产出 HitRate@K / Recall@K / MRR@K / NDCG@K / Context
  Precision / Context Recall 等 IR 指标。
- **检索层·拉真实查询日志**（无需人工上传/黄金标注）：在 `.env` 配 `SERVING_PG_*`
  连上 serving 库（`serving_query_logs`）后，调
  `POST /api/v1/projects/{pid}/retrieval/live:evaluate` 直接拉被测检索器的真实流量评测。
  真实日志无黄金标注，仅产出**精确率族**指标（HitRate / MRR / NDCG / ContextPrecision +
  查询时长）；召回率族（Recall / ContextRecall）标记为 `N/A（需黄金标注，后续开发）`。
- 跑测试：`cd runtime_eval && py -3.10 -m pytest`。
- 更多细节（API、报告字段、token/时长采集说明）见 `runtime_eval/README.md`。
