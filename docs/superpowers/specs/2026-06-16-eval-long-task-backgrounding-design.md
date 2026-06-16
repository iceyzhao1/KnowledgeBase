# 评测台长任务后台化（Long-Task Backgrounding）设计

> 状态：待用户评审
> 日期：2026-06-16
> 范围：runtime_eval（eval-web SPA + eval-api FastAPI）

## 目标（Goal）

让两个"要跑很久"的批处理任务——**serving 检索取证** 和 **Claude 自动跑批**——在用户切换步骤页 / 切换页面 / 临时离开时**继续在后台跑**，不被打断；用户回到该测试集时能**重新接上**实时进度，且**不会重复启动**同一个任务。

这是 A（L4 缓存）的地基：A 里"跑对照评测"同样是个长任务，复用本方案的后台 job 机制即可。所以**先做 B，再做 A**。

## 现状与问题（Why）

现在前端用 `EventSource` 直连后端 SSE 流（`/agent:run/stream`、`/retrieval/...stream`）。后端把"真正干活"写在 `StreamingResponse` 的生成器 `gen()` 里——**活是连接驱动的**：浏览器一断开连接，`gen()` 就停了。

前端路由是 `location.hash → setPage(page) → 替换 #view innerHTML`。切页**不会**主动关掉 EventSource，但：

1. **UI 没了**：新页面的 DOM 把进度条/日志区覆盖掉，事件还在来但没地方显示，用户以为"卡住了"。
2. **重复启动风险**：用户回到原页面看不到在跑，又点一次"开始"，于是**两条流同时对同一题落库**，互相打架。
3. **不可控**：跑了一半想停也没有干净的入口；服务端那条生成器还挂在旧连接上。

根因：**任务的生命周期绑死在 HTTP 连接上**，而 UI 是会切换、会刷新、会临时关掉的。要解耦，就得把"干活"从连接里搬到服务端的后台 job，连接只负责"观察"。

## 服务端设计（Server）

### 核心：JobRunner 注册表（内存）

新增一个进程内单例 `JobRunner`，维护 `job_id → JobState`：

```python
# eval_api/jobs.py（新文件）
@dataclass
class JobState:
    job_id: str
    suite_id: str
    kind: str            # "retrieval" | "agent"
    status: str          # "running" | "done" | "error" | "cancelled"
    total: int
    done: int            # 已完成题数
    ok: int
    err: int
    started_at: float
    finished_at: float | None
    error: str | None
    events: list[dict]   # 累积的进度事件（供后接的观察者回放）
    _cancel: threading.Event
```

`JobRunner` 职责：
- `start(suite_id, kind, target) -> JobState`：建 `JobState`、起 **后台 daemon 线程** 跑 `target`，登记到 `by_suite_kind[(suite_id, kind)]`。**若该 (suite_id, kind) 已有 running 的 job → 直接返回现有 job（不新建）**，这是"防重复启动"的服务端兜底。
- `current(suite_id, kind) -> JobState | None`：查"这个测试集这种任务现在/最近的一条 job"。
- `push(job_id, event)`：worker 调用，追加到 `events` 并更新计数。
- `cancel(job_id)`：置 `_cancel`，worker 每题循环开头检查，置 `cancelled` 后干净退出。

并发约束：**一个测试集、同一种任务，同一时刻只允许一条 job**（`by_suite_kind` 保证）。不同测试集、或同测试集的"取证 vs 跑批"可以并行。

线程安全：`JobRunner` 内部一把 `threading.Lock` 保护字典与 `events` 追加。落库走 `SqliteStore` 自带的 `_lock`，已线程安全。

### Worker：把 gen() 的活搬进来

把现有 `run_agent_suite_stream.gen()` 和取证流 `gen()` 里**逐题循环 + 落库**那段抽成普通函数 `_run_agent_job(job, ...)` / `_run_retrieval_job(job, ...)`，每题：

1. 循环开头 `if job._cancel.is_set(): job.status="cancelled"; return`
2. 调 `orchestrator.run_agent_for_case(...)`（取证调对应的 `pull_latest_for_question` 逻辑）——**复用现有 orchestrator 代码，逻辑不变**
3. 落库（和现在一样，逐题持久化）
4. `runner.push(job_id, {"event":"case_done", ...})`

结束 push `{"event":"done", ...}`，置 `status="done"`。异常整体兜底置 `status="error"` 并记 `error`。`only_missing` 的跳过判定逻辑原样保留。

### 端点（Endpoints）

三个新端点，老的直连 stream 端点保留一段时间或直接改造（见"迁移"）：

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/v1/suites/{sid}/jobs/{kind}:start` | 启动后台 job。kind ∈ {retrieval, agent}。Body 沿用原 `RunAgentSuiteIn` 等字段（agent_name/only_missing/system）。**已有 running 同类 job → 返回该 job（200，不报错），带 `already_running: true`** |
| `GET` | `/api/v1/suites/{sid}/jobs/{kind}/current` | 查当前/最近一条 job 的 `JobState` 快照（status/total/done/ok/err/error）。无则 `{job: null}` |
| `GET` | `/api/v1/jobs/{job_id}/observe` | **SSE 观察流**：先把 `events` 已有的回放一遍（断线重连不丢历史），再实时推后续事件；job 终态后推 `done`/`error` 并关闭。**关闭这条 SSE 不影响 job**——job 在后台继续 |
| `POST` | `/api/v1/jobs/{job_id}:cancel` | 主动取消（可选，给"停止"按钮用） |

`observe` 的实现要点：生成器维护一个本地游标 `i`，循环 `while True`：把 `events[i:]` 吐出去、`i = len(events)`；若 `status` 是终态且已吐完 → 吐终态事件、`return`；否则 `time.sleep(0.3)` 再轮询 `JobState`。**生成器只读 `JobState`，绝不驱动干活**，所以客户端断开只是这条观察流结束，job 不受影响。

### 服务重启行为（Restart）

`JobRunner` 是内存态——**进程重启后所有 in-flight job 丢失**。但每题结果是**逐题落库**的，已完成的部分**不丢**。

决策：**不做自动续跑**。重启后 `current` 返回 `null`，前端据 `/suites/{sid}/state` 的完成度提示"上次有 N 题已完成，点'继续'只跑剩余题"。用户手动点"继续"= 用 `only_missing=true` 起一条新 job。理由：自动续跑要持久化 job 队列 + 启动恢复逻辑，复杂度高、收益低；逐题落库 + `only_missing` 已经能无损接续，把"要不要继续"的决定权交给用户更稳。

### 错误处理（Errors）

- 单题失败：原样 `case_error` 事件 + `err++`，不打断整批。
- serving 不可用 / 表不存在（`serving_query_logs` 那类）：worker 捕获后整条 job 置 `status="error"`、`error` 写人话提示，`observe` 推 `error` 事件。前端展示清晰文案，不再是 500 白屏。
- job 不存在：`observe`/`cancel` 返回 404。

## 前端设计（Frontend）

### 把 EventSource 从"页面局部"提到"模块级管理器"

现在 `es` 是各 render 函数里的局部 `const`，页面一替换就没人持有。改成模块级单例管理器 `streamMgr`：

```js
// app.js 顶部
const streamMgr = {
  observers: {},                 // key=`${sid}:${kind}` → EventSource
  attach(sid, kind, onEvent) {   // 幂等：已 attach 同 key 则复用
    const key = `${sid}:${kind}`;
    if (this.observers[key]) return;
    // 先查 current job，有就接上 observe 流，没有就不连
    api(`/suites/${sid}/jobs/${kind}/current`).then(r => {
      if (!r.job) return;
      const es = new EventSource(`/api/v1/jobs/${r.job.job_id}/observe`);
      es.onmessage = (e) => onEvent(JSON.parse(e.data));
      es.onerror = () => { es.close(); delete this.observers[key]; };
      this.observers[key] = es;
    });
  },
  detach(sid, kind) { /* 关 observer，但 job 在后台继续 */ },
};
```

关键变化：
- **EventSource 连的是 `observe` 流，不是干活流**。关掉它（切页、刷新）只是停止观察，后台 job 照跑。
- **回到页面时调 `streamMgr.attach`**：先 `GET .../current`，有在跑的 job 就接上、回放历史事件把进度条恢复到当前，没有就显示空闲态。这就是"重新接上"。

### 按钮状态机：防重复启动（前端这一侧）

"开始取证 / 开始跑批"按钮渲染时先看 `current`：
- `running` → 按钮显示"运行中…"（禁用），旁边给"停止"（调 `:cancel`）。
- `done`/`error`/无 → 按钮可点"开始"/"重跑"。

点"开始" = `POST .../jobs/{kind}:start` → 拿到 `job_id` → `streamMgr.attach`。即使前端状态判断漏了、用户连点，**服务端 `by_suite_kind` 兜底**返回同一条 job（`already_running:true`），不会起第二条。

### 切页不打断

`setPage` 里**不再** close 任何干活流（本来也没干活流了，只有 observer）。observer 要不要保留可选：
- 简单做法：切走时 `detach`（省连接），切回时 `attach`（重新接上、回放历史）。后台 job 全程不受影响。
- 这满足用户诉求："检索取证和评分报告步骤页切换时不要打断 Claude 自动取证的过程"——因为干活的是后台 job，跟 observer 死活无关。

## 复用：两个长任务共用一套（Reuse）

`retrieval` 和 `agent` 两种 kind 共用 `JobRunner` / 三个端点 / `streamMgr`，只是 worker 函数和 start body 不同。新增 A（L4 对照评测）时，再加一种 `kind="uplift"` 即可，零额外基础设施。

## 测试（Testing）

- **JobRunner 单元**：start 起线程跑到 done；重复 start 同 (suite,kind) 返回同一 job；cancel 让 worker 在下一题前停下置 cancelled；push 累积 events 与计数。
- **端点集成**（FastAPI TestClient）：start → current 显示 running → observe 能收到 case_done/done；observe 客户端断开后 job 仍跑完（再查 current = done）；并发两次 start 只产生一条 job。
- **续跑**：起 job 跑完部分后模拟"重启"（新建 JobRunner），current=null，用 only_missing=true 起新 job 只跑剩余题。
- **错误**：serving 不可用时 job 置 error、observe 推 error。
- 前端：手测——开始跑批 → 切到别的步骤页 → 切回，进度条接上且题数继续涨；连点开始按钮不产生双跑。

## 迁移（Migration）

老的 `/agent:run/stream`、取证 stream 端点：worker 抽出后，老 stream 端点可改成"内部 start 一条 job + observe"的薄封装，或前端切到新端点后下线。倾向**前端直接切新端点**，老端点保留一个版本周期再删，避免一次动太多。

---

## 附录：Phase A（L4 缓存）已定决策（记录，B 完成后再做）

A 在 B 的后台 job 地基上实现，已和用户敲定的点：

- **缓存键 = 题目内容 hash**：对每题的 `question / answer / key_points / difficulty` 做哈希。**不含**生成参数（阈值等）。测试集这些内容没变 → 命中缓存，直接复用，不再烧 LLM。
- **拆成两块**：
  - **Block A「跑对照评测」**（单独小框）：闭卷(A) + 用库(C) 两路作答 + 判分，**全是 LLM 调用，结果存进临时库（sqlite）并按题目 hash 缓存**。这是要后台化的长任务（复用 B，`kind="uplift"`）。
  - **Block B「生成评估报告」**（单独按钮）：纯计算（compute_uplift 那套），**不调 LLM**，从缓存的判分结果直接算 PASS/四件套/五桶分布。
- **缓存条带三态**：`已缓存`（命中、可直接出报告）/ `题目已变`（hash 不匹配、需重跑 A）/ `未跑`（无缓存）。
- **阈值参数藏起来**：`tau_low` 等太专业，默认值跑通即可。收进"高级（默认即可）"折叠区，每项配一句大白话说明。主流程就一个"生成评估报告"按钮。
- 现状补充：L4 结果现在已作为 `RunSummary(layer='value', kind='uplift')` 落库，但每次跑都重烧 ~4×N 次 LLM；原始 A/C 回答**没单独持久化**。A 要补这块持久化以支撑缓存命中后直接出报告。
