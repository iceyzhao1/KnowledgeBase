# L2 忠实度层 · 多模型对照评测设计

> 状态：方案（待用户评审 → 转实现计划）
> 日期：2026-06-16
> 适用：runtime_eval（eval-web SPA + eval-api + eval-llm）
> 关联：[分层评估框架设计](2026-06-04-layered-eval-framework-design.md)（本文落实其中的「L2 生成层」）、L4 增量价值层（uplift，已实现）

---

## 0. 一句话

把"证据包"焊死不动，让**花名册里的多个模型**各基于它写一版答案，再用**同一个固定裁判**给每版打 **忠实度 + 覆盖度** 两个分，最后出一张模型排行榜 + 逐题下钻。

**打比方**：同一份《案情卷宗》（证据包）发给几个律师（参赛模型）各写一份辩护词；一个固定法官（裁判模型）只看两件事——① 你写的每句话卷宗里有没有依据（**忠实度**，防瞎编）；② 卷宗里该提的关键点你提全没（**覆盖度**，防漏）。换律师不换法官，比的才是律师本事。

## 1. 这层在分层框架里的位置

| 层 | 评什么 | 现状 |
|---|---|---|
| L4 价值层 | 用库 vs 不用库强多少 | ✅ 已实现（uplift） |
| **L2 忠实度层** | **固定证据下，答案是否忠于证据 + 覆盖黄金** | ❌ **本设计** |
| L1 检索层 | 召回找得到/排得准/够答全 | ✅ 已实现（retrieval_metrics） |

**与 L1/L4 的边界**：
- L1 测"捞得对不对"，L2 把捞到的东西**焊死**，只测"拿着对的料、答得忠不忠实、全不全" —— 隔离掉检索变量，单独考"生成"这道工序。
- L4 测"用库比不用库强多少"（端到端价值），L2 测"强的那部分是不是真靠证据撑的、哪个模型最会用证据"（过程质量）。两者一个测结果、一个测工序。

## 2. 核心数据流（每道题）

```
证据包 = L1 检索回来的那份片段（RetrievalSet.for_case(case_id)，固定不换）
对花名册里每个模型 m：
   答案_m   = m 基于(问题 + 证据包) 综合一版
   忠实度_m = 固定裁判判「答案_m 每个论断能否在证据里找到出处」→ 忠实度分 + 疑似编造清单
   覆盖度_m = 固定裁判判「答案_m 覆盖了黄金几个关键信息点」→ 覆盖度分 + 覆盖/遗漏清单
全套题跑完 → 每个模型聚合：平均忠实度 / 平均覆盖度 / token / 时长 → 排行榜（可逐题下钻）
```

**两个分的精确定义**：
- **忠实度（faithfulness / grounding）**：答案 vs **证据包**。把答案拆成若干论断，逐条判"能否被证据支撑"；分数 = 被支撑论断占比。证据里没有、却被答案断言的内容 → 进"疑似编造"清单。**这是新增能力**（现有 judge 不做这件事）。
- **覆盖度（coverage）**：答案 vs **黄金信息点**（`key_points` / `expected_evidence`，仅 `confirmed` 黄金）。分数 = 覆盖的黄金点占比。**复用现有 judge**（已返回 `covered_points` / `missed_points`）。

两个分**正交**：忠实度高、覆盖度低 = "没瞎编但答得不全"；忠实度低、覆盖度高 = "点都提到了但掺了编造"。分开看才有诊断价值。

## 3. 模型花名册（可插拔，首发少接）

### 3.1 花名册结构
一张配置化的"花名册"，每个条目描述一个**参赛模型**：
```
RosterEntry:
  id:        稳定标识（如 "claude-sonnet"）
  label:     显示名（如 "Claude Sonnet 4.5"）
  channel:   调用通道，枚举：claude_cli | llm_service | anthropic
  model:     该通道下的具体模型名（如 claude_cli 的 --model 档位）
  enabled:   是否参赛
```
花名册存为配置（环境变量 / 配置文件），eval-api 启动时加载；前端可读取展示"本次有哪些选手参赛"。

### 3.2 调用通道改造（关键 plumbing）
现状：`LLMClient.run_agent(question, system, route)` 只认 `route`，模型由 provider 的全局配置（`claude_cli_model` 等）决定，**不能按请求点名换模型**。

改造：给作答链路加一个**按请求覆盖模型**的入口——`run_agent(..., model=<roster.model>, channel=<roster.channel>)`，由 eval-llm 侧据此选 provider + 模型档位执行单题作答。这是"可插拔"的落点：新增一个 vendor 只需实现一个 channel + 在花名册加一行。

### 3.3 首发接入范围
- **首发内置（确定能白嫖）**：Claude 的 `haiku` / `sonnet` / `opus` 三档，走 `claude_cli --model`，复用已登录订阅，不花 API 钱。
- **serving 不进花名册（已定）**：serving（agent_serving_java）**只做检索、不写答案**；写答案那一步是 agent 干的。所以 serving 当不了"答题选手"，**不进花名册**；它检索回来的片段仍是 L1/L2 的证据包来源（`RetrievalSet.for_case` 的上游）。
- **其余厂商（GPT / 通义 / DeepSeek 等）**：留 channel 接口，以后按需加，不在首发。

## 4. 三个已定决策

1. **裁判固定**：忠实度、覆盖度都用 config 里那个**固定 judge 模型**给所有参赛模型打分，只换选手不换裁判 —— 同 L4「同题同判官」原则，否则比较不公平。
2. **复用 L4 的 SSE 进度条**：多模型 × 全题 = 大量 LLM 调用，必然慢。套用刚落地的流式进度（`iter_value_uplift` 同款）：阶段 = 每个模型一段「作答 → 忠实判 → 覆盖判」，逐题推 `case_start/case_done`，前端进度行实时刷 `⟳ sonnet·作答 第3/8题…`。
3. **门禁**：L2 依赖证据包，**没跑过 L1 检索的题不能跑 L2**。进入前检查每题是否有 `RetrievalSet` 片段；缺的提示"先去补检索"，不静默跳过。

## 5. 落库与缓存

- 新增一层快照：`RunSummary(layer="faithfulness", kind="multi_model", run_id="faith_<sid>")`，`metrics` 含：每个模型的聚合分、排行榜、逐题明细（答案 + 忠实度 + 覆盖度 + 疑似编造/遗漏清单）。
- 每个模型的原始答案 + 双判结果留底，支持前端逐题下钻。
- **缓存（对接此前 A 期诉求）**：缓存键 = `(证据包指纹 + 模型 id + 题目内容指纹)`。三者没变 → 命中直接复用，不重跑该(模型,题)。证据包或题目一变 → 该项失效需重跑。

## 6. 后端组件

- **eval-llm**：新增**忠实度裁判** prompt + 端点（如 `POST /judge-grounding`，入参 `answer + evidence_snippets`，出参 `grounding_score + unsupported_claims[]`）；作答端点 `/run-agent` 支持按请求 `model/channel` 覆盖。
- **eval-api**：
  - `orchestrator.iter_faithfulness(...)`：仿 `iter_value_uplift` 的生成器，按"模型 × (作答/忠实判/覆盖判)"逐题 yield 进度，跑完落 `RunSummary`。
  - `run_faithfulness(...)`：drain 生成器取回快照（阻塞版，给非流式调用/测试用）。
  - 端点：`GET /api/v1/suites/{sid}/faithfulness:run/stream`（SSE）、`GET /api/v1/suites/{sid}/faithfulness`（读最近快照）、`GET /api/v1/eval-roster`（读花名册）。
- **复用**：`gold_facts(case)`（黄金信息点）、现有 `judge`（覆盖度）、`RetrievalSet.for_case`（证据包）。

## 7. 前端

- 评测台新增 **L2 卡片**（与 L4 uplift 卡片并列）：
  - 顶部"参赛选手"= 花名册里 enabled 的模型；一个「跑忠实度对照」按钮 + SSE 进度行（复用 L4 进度条样式）。
  - 主体 = **排行榜表格**：`模型 / 平均忠实度 / 平均覆盖度 / token / 时长`，按忠实度（或综合）排序。
  - 点某行展开 = 该模型逐题：题目 / 它的答案 / 忠实度（+疑似编造高亮）/ 覆盖度（+遗漏点）。
- 静态资源版本号照例 bump，避免浏览器缓存旧 JS。

## 8. 测试

- **orchestrator**：`iter_faithfulness` 用 duck-typed 多模型 fake，断言事件序列（每个模型一段作答/忠实判/覆盖判）、聚合排行榜、落库 `faith_<sid>`；`run_faithfulness` drain 后取回快照。
- **忠实度裁判**：给一段含"证据外断言"的答案，断言 `unsupported_claims` 非空、忠实度分下降。
- **门禁**：未检索的题跑 L2 → 清晰报错/提示，不静默。
- **缓存**：同(证据包+模型+题)二次跑命中、不重复调模型；题目变 → 失效重跑。
- **SPA 断言**：`faithfulness:run/stream` / `eval-roster` 接线存在；旧断言回归。
- 全量 `pytest` 绿灯。

## 9. 阶段拆分（供写实现计划参考）

1. **花名册 + 按请求换模型**：RosterEntry 配置加载 + `/run-agent` 支持 model/channel 覆盖 + `GET /eval-roster`。（地基，先验证 claude_cli 换档真生效）
2. **忠实度裁判**：eval-llm `/judge-grounding` prompt + 端点 + 单测。
3. **编排 + SSE**：`iter_faithfulness` / `run_faithfulness` / `faithfulness:run/stream` / `faithfulness` 读取端点 + 落库 + 门禁。
4. **缓存**：按 `(证据包+模型+题)` 指纹命中复用。
5. **前端**：L2 卡片 + 排行榜 + 逐题下钻 + SSE 进度（复用 L4 组件）。

## 10. 待确认 / 风险

- ~~serving 是否算答题模型~~ —— **已定：serving 只检索不作答，不进花名册**（见 §3.3）。
- **claude_cli 多档真实可用性**：`--model haiku|sonnet|opus` 在订阅下是否都可调、是否额外耗额度，写代码第一步先用真实命令验证（同 L4 风险栏的"先验证再编排"原则）。
- **成本/时长**：模型数 × 题数 × 3 次 LLM（作答+忠实+覆盖）。先小批量试跑，靠缓存 + SSE 进度缓解体感。
- **忠实度判的稳定性**：论断切分 + grounding 判定对 prompt 敏感，先定简单版（整段判 + 列疑似编造）再迭代到逐论断。
