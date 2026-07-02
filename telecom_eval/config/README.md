# telecom_eval runtime configuration

配置文件：`telecom_eval/config/runtime.json`

修改 IP、端口、检索范式服务地址、评估 LLM provider 时，优先改这个文件。临时调试时也可以继续用环境变量覆盖，环境变量优先级高于 `runtime.json`。

## 地址与端口

| 字段 | 当前值                                    | 用途 | 环境变量覆盖 |
| --- |----------------------------------------| --- | --- |
| `ui.dev_port` | `5174`                                 | Vite 前端开发端口 | `TELECOM_EVAL_UI_PORT` |
| `ui.eval_api_base_url` | `http://127.0.0.1:8810`                | 前端 `/api` 代理到评估后端 | `TELECOM_EVAL_API` |
| `ui.paradigm_api_base_url` | `http://10.205.71.26:8081`             | 前端 `/paradigm-api` 代理到范式服务 | `TELECOM_PARADIGM_API` |
| `api.host` | `127.0.0.1`                            | 启动后端时建议监听地址 | 启动命令参数 |
| `api.port` | `8811`                                 | 启动后端时建议监听端口 | 启动命令参数 |
| `api.db_path` | `data/evaluation/telecom_eval_demo.db` | SQLite 评估库路径 | `TELECOM_EVAL_DB_PATH` |
| `subject.search_base_url` | `http://10.205.71.26:8081`             | 后端真实检索请求的 base URL | `TELECOM_EVAL_SEARCH_URL` |

范式查询接口由前端先调用：

```text
GET {ui.paradigm_api_base_url}/api/v1/paradigm/published
```

用户选择 `name` 后，后端实际检索使用该范式返回的 `url`，例如：

```text
POST {subject.search_base_url}/api/v1/paradigm/pd-1064589e/search
```

## 被测系统检索配置

| 字段 | 当前值 | 用途 | 环境变量覆盖 |
| --- | --- | --- | --- |
| `subject.provider` | `http` | 使用真实 HTTP 检索适配器；设为 `fake` 可离线跑假数据 | `TELECOM_EVAL_SUBJECT_PROVIDER` |
| `subject.search_domain` | `cloud_core_network` | 默认检索 domain | `TELECOM_EVAL_SEARCH_DOMAIN` |
| `subject.search_timeout` | `30` | 检索请求超时秒数 | `TELECOM_EVAL_SEARCH_TIMEOUT` |

## 评估 LLM 配置

现在框架里有两个可用 judge provider：

| Provider | 是否真实调用 LLM | 当前用途 | 什么时候用 |
| --- | --- | --- | --- |
| `mock` | 否 | 默认评估 judge，返回稳定的模拟判分，适合流程联调、UI 验证、离线 smoke test | 现在默认就是它，成本低、不会依赖外部模型 |
| `claude_cli` | 是 | 通过本机已登录的 `claude -p --output-format json` 做语义判分 | 需要真实 LLM 判断答案正确性、faithfulness、citation 等语义类指标时使用 |

预留但未实现的 provider：

| Provider | 状态 |
| --- | --- |
| `llm_service` | 工厂里保留扩展点，当前调用会报“未实现” |
| `openai_compat` | 工厂里保留扩展点，当前调用会报“未实现” |

真实 LLM 只通过 `JudgeService` 调用。前端、指标、诊断和报告不会直接调用模型。

### 当前配置

```json
{
  "judge": {
    "provider": "mock",
    "claude_cli": {
      "bin": "claude",
      "model": "",
      "timeout": 600,
      "extra_args": "",
      "proxy": ""
    }
  }
}
```

### 切换为真实 Claude CLI judge

把 `runtime.json` 改成：

```json
{
  "judge": {
    "provider": "claude_cli",
    "claude_cli": {
      "bin": "claude",
      "model": "",
      "timeout": 600,
      "extra_args": "",
      "proxy": ""
    }
  }
}
```

或者临时设置：

```powershell
$env:TELECOM_EVAL_JUDGE_PROVIDER="claude_cli"
$env:TELECOM_EVAL_CLAUDE_BIN="claude"
```

创建评估任务时还需要打开页面上的“允许 LLM 判分”，并设置预算，例如 `max_llm_calls`、`max_total_tokens`、`max_cases_with_llm`。如果页面上不允许 LLM 判分，即使 provider 配成 `claude_cli`，语义判分也不会真实调用模型。
