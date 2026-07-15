# cmkb0708 源码恢复报告

## 恢复对象

- 镜像归档：`E:\MyProjects\KnowledgeBase\cmkb0708.tar`
- 文件大小：`665736192` 字节
- 文件修改时间：`2026-07-08 15:21:15 +08:00`
- SHA-256：`F20E7F432C6A276E0E9125091ECCCAA8E9A9E61F834678E410EC2E69D75BFF72`
- OCI 镜像：`coremasterkb-app:latest`
- 镜像创建时间：`2026-07-08T07:19:53.36401416Z`
- 镜像平台：`linux/amd64`
- OCI layer 数：`26`
- Git 基线：`03d8a1243bde1a9f815594a4eb54113d461f6c67`
- 本地分支：`deploy/cmkb0708`
- 独立 worktree：`E:\MyProjects\KnowledgeBase-cmkb0708-recovery`

## 恢复边界

以下内容从 OCI layer 中按白名单直接恢复：

- `pyproject.toml`
- `.env.example`
- `knowledge_mining/**`
- `llm_service/**`
- `main_control_service/**`
- `mcp_server/**`
- `domain_registry.yaml`
- `reset_db.py`
- `scenario_packs/**`
- `databases/**`
- `/etc/supervisor/conf.d/cmkb.conf` 映射为 `docker/supervisord.conf`

以下源码无法从镜像中直接确认，保留 Git 基线 `03d8a12` 的版本：

- `agent_serving_java/**`：镜像中只有 `/app/agent_serving.jar`；`docker/Dockerfile` 表明构建输入来自该目录
- `kb-ui/**`：镜像中只有 `/app/kb-ui-dist`

因此，本分支是“可恢复源码快照”，不是对 Java/Vue 源码与部署二进制逐字节对应的证明。

## 明确排除

以下内容没有复制或提交：

- `/app/.env` 及其任何内容
- `/app/agent_serving.jar`
- `/app/kb-ui-dist/**`
- Python `__pycache__/**` 和 `*.pyc`
- `cmkb0708.tar` 本身

`main_control_service/config/scenario_packs.zip` 是镜像中实际存在的场景配置包，不属于上述排除项，随恢复源码保留。

## 一致性检查

- 安全过滤后的 `/app` 文件数：`211`
- 恢复 worktree 缺失文件数：`0`
- SHA-256 不一致文件数：`0`
- `docker/supervisord.conf` 与镜像 `/etc/supervisor/conf.d/cmkb.conf`：一致
- `.env`、JAR、WAR、`kb-ui-dist`、`__pycache__`、`*.pyc` 禁止项检查：未发现

## 验证结果

| 范围 | 命令 | 状态 | 结果 |
|---|---|---|---|
| Python 编译 | `python -m compileall -q knowledge_mining llm_service main_control_service mcp_server reset_db.py` | PASS | 使用 Codex Python 3.12.13，退出码 0 |
| Python 测试 | `D:\software\anaconda\envs\kb\python.exe -m pytest knowledge_mining/tests -x -vv --tb=long` | BLOCKED | 收集 305 项；首项在 session fixture 初始化时缺少 `PG_HOST`、`PG_DBNAME`、`PG_USER`、`PG_PASSWORD`。恢复过程按安全要求未复制 `.env`，未伪造连接参数，也未启用清库开关 |
| Java 基线 | 在 `agent_serving_java` 中运行 `mvn test` | BLOCKED | 当前终端找不到 Maven；未修改 Java 源码 |
| Vue 基线 | 在 `kb-ui` 中运行 `npm ci`、`npm run build` | BLOCKED | npm 未生成 `node_modules/.bin` 启动脚本；直接调用已安装包内 `vue-tsc` 入口超过约 9 分钟无输出后终止。未修改前端源码 |

测试生成的 `node_modules`、`dist`、`.pytest_cache`、`__pycache__` 已在验证后清理。

## 分支定位

`deploy/cmkb0708` 用于保存和复现 0708 公共部署版本，是只接受审计性修订的冻结部署分支，不作为功能分支合并回 `master`。当前阶段只创建本地分支和本地提交；用户测试确认前，不设置 upstream、不推送 `origin`、不创建远端 tag 或 Release。
