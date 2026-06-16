# 评估工作台步骤重构（3步→4步）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把评估工作台从 3 步（文档与出题 → 黄金校对 → 评测与报告）拆成 4 步（准备题目 → 标准答案 → 取证据 → 评分与报告），让每一步只干一件事。

**Architecture:** 纯前端改动，全部集中在单个无构建 SPA 文件 `app.js`（外加一处后端测试断言同步）。复用现有的步骤条机器（`FLOW_STEPS`/`flowRefresh`/`renderFlowStepper`/软门禁 `flowReady`），只把步数从 3 扩到 4、把原步骤③ 内的「取证据/打分/报告历史」三个子标签拆成独立的步骤③（取证据）和步骤④（评分+报告历史），并在步骤① 增加「AI 出题 / 拉真实日志 / 导入题库」三选一来源切换器。不动任何后端接口。

**Tech Stack:** 原生 JavaScript（无模块、无构建）、FastAPI（仅改一个测试断言）、pytest、`node --check` 做语法校验。

---

## 重要约定（执行者必读）

- **不要 git commit。** 所有改动只留在工作区，由用户本人统一提交。每个任务用「验证」步骤代替「提交」步骤。
- **目标文件绝对路径**（注意是双层 `runtime_eval/runtime_eval`）：
  - 前端：`E:\MyProjects\KnowledgeBase\runtime_eval\runtime_eval\eval_web\static\app.js`
  - 测试：`E:\MyProjects\KnowledgeBase\runtime_eval\tests\test_eval_api.py`
- **语法校验命令**（在仓库根 `/e/MyProjects/KnowledgeBase` 下跑）：
  `node --check runtime_eval/runtime_eval/eval_web/static/app.js`
  期望：无输出（退出码 0）。
- **后端回归命令**（必须先 cd 进子项目）：
  `cd /e/MyProjects/KnowledgeBase/runtime_eval && python -m pytest -q`
  基线：76 passed。
- 所有面向用户的文案用中文。

## 文件结构与职责

只改两个文件：

- `runtime_eval/runtime_eval/eval_web/static/app.js`（约 1435 行，单页应用全部逻辑）——本计划主战场。
- `runtime_eval/tests/test_eval_api.py`——`test_serves_eval_web_spa` 这一个测试断言新结构，需同步。

新增的步骤④（评分与报告）= 复用现有 `flowStepReport`（打分+报告）+ `renderReportListInto`（报告历史），拼在同一个步骤里。步骤③（取证据）= 复用现有 `flowStepEvidence`。步骤① 的三选一来源 = 把现有 `suitesBody` 里的四张卡片拆成「AI 出题」「拉真实日志」「导入题库」三组片段，按所选来源只渲染一组。

---

## Task 1: 步骤机器扩到 4 步（FLOW_STEPS / 门禁 / 分发 / state 字段）

把驱动步骤条的几处常量和函数从「3 步」改成「4 步」。本任务结束后步骤条会显示 4 个步骤，但步骤③④ 的内容函数 `workStepEvidence`/`workStepReport` 要到 Task 3 才定义——`node --check` 只做语法解析，不解析运行期引用，所以本任务可独立通过校验。

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`

- [ ] **Step 1: 改 state 字段——删 `evalTab`，加 `docSource`**

把 `state` 对象里这一行（约 L31）：

```js
  evalTab: localStorage.getItem("kb_evaltab") || "evidence",  // 评测步的子标签
```

替换为：

```js
  docSource: localStorage.getItem("kb_docsource") || "ai",   // 步骤①出题来源：ai/pull/import
```

- [ ] **Step 2: 把 `FLOW_STEPS` 从 3 项改 4 项**

把（约 L447-451）：

```js
const FLOW_STEPS = [
  { key: "docgen", t: "文档与出题", d: "上传文档 + AI 出题 / 导入 / 批量拉取" },
  { key: "gold", t: "黄金校对", d: "复核 / 修正 / 确认黄金答案" },
  { key: "eval", t: "评测与报告", d: "取证据 → 逐题打分 → 报告与历史" },
];
```

替换为：

```js
const FLOW_STEPS = [
  { key: "docgen", t: "准备题目", d: "AI 出题 / 拉真实日志 / 导入题库" },
  { key: "gold", t: "标准答案", d: "逐题复核 / 修正 / 确认黄金答案" },
  { key: "evidence", t: "取证据", d: "收被测系统的答卷（serving 取证 / Claude 作答）" },
  { key: "report", t: "评分与报告", d: "逐题打 0–3 分 → 成绩单 + 报告历史" },
];
```

- [ ] **Step 3: `flowRefresh` 里步骤索引夹取上界 2→3**

把（约 L475）：

```js
  if (state.flowStep == null || state.flowStep > 2) state.flowStep = flowDefaultStep(f);
```

替换为：

```js
  if (state.flowStep == null || state.flowStep > 3) state.flowStep = flowDefaultStep(f);
```

- [ ] **Step 4: `flowDefaultStep` 默认步映射加 `report→3`**

把（约 L484-486）：

```js
function flowDefaultStep(f) {
  return ({ documents: 0, questions: 0, gold: 1, evidence: 2, report: 2 })[f.currentStep] ?? 0;
}
```

替换为：

```js
function flowDefaultStep(f) {
  return ({ documents: 0, questions: 0, gold: 1, evidence: 2, report: 3 })[f.currentStep] ?? 0;
}
```

- [ ] **Step 5: `flowReady` 软门禁加第④步就绪条件**

把（约 L488-494）：

```js
function flowReady(f, i) {
  if (!f) return i === 0;
  if (i === 0) return true;
  if (i === 1) return !!f.steps.questions;                  // 出题后才好校对
  if (i === 2) return !!(f.steps.questions && f.steps.gold); // 有题且至少一条确认黄金
  return false;
}
```

替换为：

```js
function flowReady(f, i) {
  if (!f) return i === 0;
  if (i === 0) return true;
  if (i === 1) return !!f.steps.questions;                       // 出题后才好校对
  if (i === 2) return !!(f.steps.questions && f.steps.gold);      // 有题且至少一条确认黄金
  if (i === 3) return !!(f.steps.questions && f.steps.gold && f.steps.evidence); // 还需至少一条证据
  return false;
}
```

- [ ] **Step 6: `flowDone` 完成度数组扩到 4 项**

把（约 L495-498）：

```js
function flowDone(f, i) {
  if (!f) return false;
  return [f.steps.questions, f.steps.gold, f.steps.report][i];
}
```

替换为：

```js
function flowDone(f, i) {
  if (!f) return false;
  return [f.steps.questions, f.steps.gold, f.steps.evidence, f.steps.report][i];
}
```

- [ ] **Step 7: `renderFlowStep` 分发数组改 4 个步骤函数**

把（约 L521-523）：

```js
function renderFlowStep() {
  const fns = [workStepDocGen, workStepGold, workStepEval];
  (fns[state.flowStep] || workStepDocGen)();
}
```

替换为：

```js
function renderFlowStep() {
  const fns = [workStepDocGen, workStepGold, workStepEvidence, workStepReport];
  (fns[state.flowStep] || workStepDocGen)();
}
```

- [ ] **Step 8: 语法校验**

Run: `node --check runtime_eval/runtime_eval/eval_web/static/app.js`（在仓库根目录）
Expected: 无输出，退出码 0。

---

## Task 2: 步骤① 三选一来源切换器

把步骤① 重写为顶部一个「AI 出题 / 拉真实日志 / 导入题库」三选一切换器（复用现有 `.tabs/.tab` 样式），选哪个只显示哪个表单，下方是共用的测试集列表 + 预览。并让「导入」「拉真实日志」成功后也像「AI 出题」一样推进到步骤②。

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`

- [ ] **Step 1: 重写 `workStepDocGen` + 新增 `setDocSource`**

把（约 L525-533）：

```js
function workStepDocGen() {
  const box = $("#flowBody"); if (!box) return;
  box.innerHTML = suitesBody() + `
    <div class="row-actions" style="margin-top:4px">
      <button class="btn ghost" onclick="flowGoStep(1)">下一步：黄金校对 →</button>
      <span class="muted small">${flowReady(state.flow, 1) ? "" : "出题或导入后即可校对黄金"}</span>
    </div>`;
  renderTypeChecks(); loadDocs(); renderSuiteList();
}
```

替换为：

```js
function workStepDocGen() {
  const box = $("#flowBody"); if (!box) return;
  const src = state.docSource || "ai";
  const tab = (s, label) => `<button class="tab ${src === s ? "active" : ""}" onclick="setDocSource('${s}')">${label}</button>`;
  box.innerHTML = `
    <div class="card tight"><div class="tabs">
      ${tab("ai", "AI 出题")}${tab("pull", "拉真实日志")}${tab("import", "导入题库")}
    </div></div>
    ${docSourceForm(src)}
    ${docSharedBody()}
    <div class="row-actions" style="margin-top:4px">
      <button class="btn ghost" onclick="flowGoStep(1)">下一步：标准答案 →</button>
      <span class="muted small">${flowReady(state.flow, 1) ? "" : "出题 / 拉取 / 导入后即可校对黄金"}</span>
    </div>`;
  if (src === "ai") { renderTypeChecks(); loadDocs(); }
  renderSuiteList();
}
function setDocSource(src) {
  state.docSource = src;
  localStorage.setItem("kb_docsource", src);
  workStepDocGen();
}
function docSourceForm(src) {
  if (src === "pull") return docSourcePull();
  if (src === "import") return docSourceImport();
  return docSourceAi();
}
```

- [ ] **Step 2: 用三个来源片段 + 共用区替换旧的 `suitesBody`**

把整个 `suitesBody` 函数（约 L710-755，从 `function suitesBody() {` 到对应的右花括号 `}`）：

```js
function suitesBody() {
  return `
  <div class="grid cols-2">
    <div class="card">
      <div class="card-h"><h3>① 上传文档</h3><span class="hint">出题的原料</span></div>
      <div class="inline-fields">
        <div class="field grow"><input type="file" id="docFile" accept=".md,.markdown,.txt,.text,.chm" /></div>
        <button class="btn ghost" id="uploadBtn">上传解析</button>
      </div>
      <div id="docList" class="list" style="margin-top:12px"></div>
    </div>
    <div class="card">
      <div class="card-h"><h3>② AI 出题（自带黄金）</h3><span class="hint">证据 / 实体由大模型生成</span></div>
      <div class="field"><span class="lbl">题型（默认全选）</span><div class="row-actions" id="typeChecks"></div></div>
      <div class="inline-fields">
        <div class="field"><span class="lbl">每类每文档</span><input type="number" id="perType" value="2" min="1" style="width:90px" /></div>
        <button class="btn" id="genBtn">生成测试集</button>
      </div>
    </div>
  </div>
  <div class="grid cols-2">
    <div class="card">
      <div class="card-h"><h3>导入已有用例</h3><span class="hint">YAML / JSON</span></div>
      <div class="muted small" style="margin-bottom:10px">已有用例集可直接导入，跳过出题。</div>
      <div class="inline-fields">
        <div class="field grow"><input type="file" id="suiteFile" accept=".yaml,.yml,.json" /></div>
        <button class="btn ghost" id="importBtn">导入</button>
      </div>
    </div>
    <div class="card">
      <div class="card-h"><h3>批量拉取（L1 真实日志）</h3><span class="hint">生成待标注草稿</span></div>
      <div class="muted small" style="margin-bottom:10px">从 serving 查询日志拉一批真实问题生成草稿，命中黄金库自动回填。</div>
      <div class="inline-fields">
        <div class="field"><span class="lbl">domain（可空）</span><input type="text" id="pullDomain" placeholder="全部" style="width:130px" /></div>
        <div class="field"><span class="lbl">检索器名</span><input type="text" id="pullAgent" value="serving" style="width:110px" /></div>
        <div class="field"><span class="lbl">limit</span><input type="number" id="pullLimit" value="20" min="1" style="width:80px" /></div>
        <button class="btn" id="pullBtn">批量拉取</button>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-h"><h3>本项目测试集</h3><span class="spacer"></span><span class="hint" id="suiteCount"></span></div>
    <div id="suiteList" class="list"></div>
  </div>
  <div id="casePreview"></div>`;
}
```

替换为下面四个函数（按来源拆分 + 共用区）：

```js
function docSourceAi() {
  return `<div class="grid cols-2">
    <div class="card">
      <div class="card-h"><h3>① 上传文档</h3><span class="hint">出题的原料</span></div>
      <div class="inline-fields">
        <div class="field grow"><input type="file" id="docFile" accept=".md,.markdown,.txt,.text,.chm" /></div>
        <button class="btn ghost" id="uploadBtn">上传解析</button>
      </div>
      <div id="docList" class="list" style="margin-top:12px"></div>
    </div>
    <div class="card">
      <div class="card-h"><h3>② AI 出题（自带黄金）</h3><span class="hint">证据 / 实体由大模型生成</span></div>
      <div class="field"><span class="lbl">题型（默认全选）</span><div class="row-actions" id="typeChecks"></div></div>
      <div class="inline-fields">
        <div class="field"><span class="lbl">每类每文档</span><input type="number" id="perType" value="2" min="1" style="width:90px" /></div>
        <button class="btn" id="genBtn">生成测试集</button>
      </div>
    </div>
  </div>`;
}
function docSourcePull() {
  return `<div class="card">
      <div class="card-h"><h3>拉真实日志（L1）</h3><span class="hint">从 serving 查询日志拉一批真实问题为草稿</span></div>
      <div class="muted small" style="margin-bottom:10px">命中黄金库自动回填；拉来的题仍需到「标准答案」步确认。</div>
      <div class="inline-fields">
        <div class="field"><span class="lbl">domain（可空）</span><input type="text" id="pullDomain" placeholder="全部" style="width:130px" /></div>
        <div class="field"><span class="lbl">检索器名</span><input type="text" id="pullAgent" value="serving" style="width:110px" /></div>
        <div class="field"><span class="lbl">limit</span><input type="number" id="pullLimit" value="20" min="1" style="width:80px" /></div>
        <button class="btn" id="pullBtn">批量拉取</button>
      </div>
    </div>`;
}
function docSourceImport() {
  return `<div class="card">
      <div class="card-h"><h3>导入题库</h3><span class="hint">YAML / JSON 用例集</span></div>
      <div class="muted small" style="margin-bottom:10px">已有用例集可直接导入，跳过出题。</div>
      <div class="inline-fields">
        <div class="field grow"><input type="file" id="suiteFile" accept=".yaml,.yml,.json" /></div>
        <button class="btn ghost" id="importBtn">导入</button>
      </div>
    </div>`;
}
function docSharedBody() {
  return `<div class="card">
    <div class="card-h"><h3>本项目测试集</h3><span class="spacer"></span><span class="hint" id="suiteCount"></span></div>
    <div id="suiteList" class="list"></div>
  </div>
  <div id="casePreview"></div>`;
}
```

- [ ] **Step 3: 让「导入」成功后也推进到步骤②**

把 `importSuite` 成功分支（约 L856-858）：

```js
    const suite = await api(`/api/v1/projects/${state.projectId}/suites:import`, { method: "POST", body: fd });
    input.value = ""; registerSuite(suite, "导入"); renderSuiteList();
    toast(`已导入 ${suite.cases.length} 道题`); previewSuite(suite.suite_id);
```

替换为：

```js
    const suite = await api(`/api/v1/projects/${state.projectId}/suites:import`, { method: "POST", body: fd });
    input.value = ""; registerSuite(suite, "导入");
    await selectSuite(suite.suite_id); syncProjectUI();
    toast(`已导入 ${suite.cases.length} 道题`);
    if (state.page === "work") { state.flowStep = 1; state.flowStale = new Set(); await flowRefresh(true); }
    else { renderSuiteList(); previewSuite(suite.suite_id); }
```

- [ ] **Step 4: 让「拉真实日志」成功后也推进到步骤②**

把 `pullDraft` 成功分支（约 L867-870）：

```js
    const r = await api(`/api/v1/projects/${state.projectId}/retrieval/live:pull`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ limit, domain, agent_name }) });
    registerSuite(r.suite, "真实日志草稿"); renderSuiteList();
    toast(`已拉取 ${r.pulled_cases} 题 · 黄金库命中 ${r.gold_hits} · 待标注 ${r.pending_annotation}`);
    previewSuite(r.suite.suite_id);
```

替换为：

```js
    const r = await api(`/api/v1/projects/${state.projectId}/retrieval/live:pull`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ limit, domain, agent_name }) });
    registerSuite(r.suite, "真实日志草稿");
    await selectSuite(r.suite.suite_id); syncProjectUI();
    toast(`已拉取 ${r.pulled_cases} 题 · 黄金库命中 ${r.gold_hits} · 待标注 ${r.pending_annotation}`);
    if (state.page === "work") { state.flowStep = 1; state.flowStale = new Set(); await flowRefresh(true); }
    else { renderSuiteList(); previewSuite(r.suite.suite_id); }
```

- [ ] **Step 5: 语法校验**

Run: `node --check runtime_eval/runtime_eval/eval_web/static/app.js`
Expected: 无输出，退出码 0。

- [ ] **Step 6: 确认 `suitesBody` 已无残留引用**

Run（用 Grep 工具，pattern `suitesBody`，搜 `app.js`）
Expected: 0 处匹配（旧函数已被四个片段函数取代，且 `workStepDocGen` 不再调用它）。

---

## Task 3: 步骤③ 取证据 + 步骤④ 评分与报告（删子标签）

把原步骤③ 内的「取证据 / 打分与报告 / 报告历史」三个子标签拆掉：取证据单独成步骤③，打分+报告历史合成步骤④。删除子标签外壳逻辑 `workStepEval`/`workEvalTab`/`renderEvalTab`。

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`

- [ ] **Step 1: 删除子标签外壳，换成两个新步骤函数**

把（约 L561-580，从 `function workStepEval() {` 到 `renderEvalTab` 函数结尾的 `}`，整块）：

```js
function workStepEval() {
  const box = $("#flowBody"); if (!box) return;
  if (!state.suite) { box.innerHTML = pickHint("请先在「文档与出题」生成测试集。"); return; }
  const tab = state.evalTab || "evidence";
  box.innerHTML = `
    <div class="card tight"><div class="tabs">
      <button class="tab ${tab === "evidence" ? "active" : ""}" onclick="workEvalTab('evidence')">取证据</button>
      <button class="tab ${tab === "judge" ? "active" : ""}" onclick="workEvalTab('judge')">打分与报告</button>
      <button class="tab ${tab === "history" ? "active" : ""}" onclick="workEvalTab('history')">报告历史</button>
    </div></div>
    <div id="evalTabBody"></div>`;
  renderEvalTab();
}
function workEvalTab(t) { state.evalTab = t; localStorage.setItem("kb_evaltab", t); renderEvalTab(); }
function renderEvalTab() {
  const tab = state.evalTab || "evidence";
  if (tab === "judge") flowStepReport("#evalTabBody");
  else if (tab === "history") renderReportListInto("#evalTabBody");
  else flowStepEvidence("#evalTabBody");
}
```

替换为：

```js
function workStepEvidence() {
  const box = $("#flowBody"); if (!box) return;
  if (!state.suite) { box.innerHTML = pickHint("请先在「准备题目」生成或选择一个测试集。"); return; }
  flowStepEvidence("#flowBody");
}
function workStepReport() {
  const box = $("#flowBody"); if (!box) return;
  if (!state.suite) { box.innerHTML = pickHint("请先在「准备题目」生成测试集，并在「取证据」取到证据。"); return; }
  box.innerHTML = `<div id="reportMain"></div>
    <div class="card-h" style="margin-top:6px"><h3>报告历史</h3><span class="hint">本项目历次评测，可打开 HTML / MD 报告</span></div>
    <div id="reportHistory"></div>`;
  flowStepReport("#reportMain");
  renderReportListInto("#reportHistory");
}
```

- [ ] **Step 2: 步骤③ 取证据里的「去下一步」按钮改指向步骤④**

把 `flowStepEvidence` 里这一行（约 L681）：

```js
        <button class="btn ghost" onclick="workEvalTab('judge')">去打分与报告 →</button>
```

替换为：

```js
        <button class="btn ghost" onclick="flowGoStep(3)">去评分与报告 →</button>
```

- [ ] **Step 3: 语法校验**

Run: `node --check runtime_eval/runtime_eval/eval_web/static/app.js`
Expected: 无输出，退出码 0。

- [ ] **Step 4: 确认子标签逻辑已删干净**

Run（用 Grep 工具搜 `app.js`，pattern `workEvalTab|renderEvalTab|evalTabBody`）
Expected: 0 处匹配。

---

## Task 4: 删步骤② 批量评估按钮 + `batchEvaluateAnnotated`，修步骤②③ 接线

删掉步骤②（标准答案）里那个与步骤④ 打分重复的「批量评估（仅已标注）」按钮及其函数；顺手把步骤② 的「下一步」文案改成「取证据」，把步骤③ serving 取证按钮恢复成 SSE 流式（IA 重构后该按钮误退回非流式）。

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`

- [ ] **Step 1: `workStepGold` 的下一步按钮文案改为「取证据」**

把（约 L552）：

```js
      <button class="btn ghost" onclick="flowGoStep(2)">下一步：评测与报告 →</button>
      <span class="muted small">${flowReady(state.flow, 2) ? "" : "至少确认一条黄金后再评测"}</span>
```

替换为：

```js
      <button class="btn ghost" onclick="flowGoStep(2)">下一步：取证据 →</button>
      <span class="muted small">${flowReady(state.flow, 2) ? "" : "至少确认一条黄金后再取证"}</span>
```

- [ ] **Step 2: `goldBody` 删掉「批量评估」按钮行**

把整个 `goldBody`（约 L875-888）：

```js
function goldBody() {
  const s = state.suite;
  return `<div class="steps">
      <div class="step"><span class="num">1</span>AI 出题自带黄金</div>
      <div class="step"><span class="num">2</span>逐题复核 / 修正并保存</div>
      <div class="step"><span class="num">3</span>保存即入黄金库，下次自动回填</div>
    </div>
    <div class="row-actions" style="margin-bottom:14px">
      <button class="btn" id="batchEvalBtn">批量评估（仅已标注）</button>
      <a class="btn sm ghost" id="goldReport" href="#" target="_blank" style="display:none">↗ 打开评估报告</a>
      <span class="muted small">${s.cases.length} 题</span>
    </div>
    <div id="goldCases"></div>`;
}
```

替换为：

```js
function goldBody() {
  const s = state.suite;
  return `<div class="steps">
      <div class="step"><span class="num">1</span>AI 出题自带黄金</div>
      <div class="step"><span class="num">2</span>逐题复核 / 修正并保存</div>
      <div class="step"><span class="num">3</span>保存即入黄金库，下次自动回填</div>
    </div>
    <div class="row-actions" style="margin-bottom:14px">
      <span class="muted small">${s.cases.length} 题 · 只有「已确认」的黄金参与打分</span>
    </div>
    <div id="goldCases"></div>`;
}
```

- [ ] **Step 3: 删除 `batchEvaluateAnnotated` 函数**

把整个函数（约 L941-950）删掉：

```js
async function batchEvaluateAnnotated() {
  const btn = $("#batchEvalBtn"); btn.disabled = true; const old = btn.innerHTML; btn.innerHTML = `<span class="spin"></span>评估中`;
  try {
    const r = await api(`/api/v1/suites/${state.suiteId}/retrieval:evaluate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ only_annotated: true }) });
    saveRunSum(state.suiteId, "annotated", r.metrics);
    const link = $("#goldReport"); link.style.display = "inline-flex"; link.href = `/api/v1/retrieval-runs/${r.run_id}/report?format=html`;
    toast(`已评估 ${r.evaluated_cases} 道已标注题 · NDCG ${pct(r.metrics.ndcg[String(r.metrics.k_values.slice(-1)[0])])}`);
  } catch (e) { toast("批量评估失败：" + e.message, "err"); }
  finally { btn.disabled = false; btn.innerHTML = old; }
}
```

（直接整段移除，不留空行堆叠。）

- [ ] **Step 4: 事件委托表里删 `batchEvalBtn`，并把 `pullAllBtn` 恢复成 SSE 流式**

把（约 L1419）：

```js
    batchEvalBtn: batchEvaluateAnnotated, pullAllBtn: (state.page === "flow" ? pullAllCasesStream : pullAllCases), judgeAllBtn: judgeAllCases, reportBtn: runFinalReport,
```

替换为：

```js
    pullAllBtn: pullAllCasesStream, judgeAllBtn: judgeAllCases, reportBtn: runFinalReport,
```

- [ ] **Step 5: 语法校验**

Run: `node --check runtime_eval/runtime_eval/eval_web/static/app.js`
Expected: 无输出，退出码 0。

- [ ] **Step 6: 确认批量评估残留已删干净**

Run（用 Grep 工具搜 `app.js`，pattern `batchEvalBtn|batchEvaluateAnnotated|goldReport`）
Expected: 0 处匹配。

---

## Task 5: 跳转索引校准 + 文案 / 注释收尾

把看板/列表/预览里跳到工作台的步骤索引按新 4 步校准（原「评测」step 2 → 现「取证据」step 2、报告历史 → step 3），并把页面标题、提示、文件头注释等文案改成 4 步说法。

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`

- [ ] **Step 1: `openHistory` 跳到步骤④（报告历史所在），去掉 evalTab**

把（约 L362-363）：

```js
// 看板「全部报告 →」：进工作台第③步并切到报告历史子标签
function openHistory() { state.flowStep = 2; state.evalTab = "history"; localStorage.setItem("kb_evaltab", "history"); go("work"); }
```

替换为：

```js
// 看板「全部报告 →」：进工作台第④步（评分与报告，含报告历史）
function openHistory() { state.flowStep = 3; state.flowStale = new Set(); go("work"); }
```

- [ ] **Step 2: `openSuiteFrom` 的步骤映射里 reports 改 3**

把（约 L356）：

```js
  const stepMap = { gold: 1, retrieval: 2, reports: 2 };
```

替换为：

```js
  const stepMap = { gold: 1, retrieval: 2, reports: 3 };
```

- [ ] **Step 3: 测试集列表里的「评测」按钮改文案为「取证」（仍跳 step 2）**

把（约 L788）：

```js
      <button class="btn sm ghost" onclick="openSuiteWork('${s.suite_id}', 2)">评测</button>
```

替换为：

```js
      <button class="btn sm ghost" onclick="openSuiteWork('${s.suite_id}', 2)">取证评分</button>
```

- [ ] **Step 4: 用例预览里的「去评测」按钮改文案为「去取证」（仍跳 step 2）**

把（约 L804）：

```js
      <button class="btn sm" onclick="flowGoStep(2)">去评测 →</button>
```

替换为：

```js
      <button class="btn sm" onclick="flowGoStep(2)">去取证 →</button>
```

- [ ] **Step 5: `generateSuite` 成功提示文案改为「标准答案」**

把（约 L845）：

```js
    toast(`已生成 ${suite.cases.length} 道题（自带黄金草稿，请到「黄金校对」确认）`);
```

替换为：

```js
    toast(`已生成 ${suite.cases.length} 道题（自带黄金草稿，请到「标准答案」确认）`);
```

- [ ] **Step 6: `renderWork` 外壳里的流程提示语改 4 步**

把（约 L345）：

```js
        <span class="hint">文档与出题 → 黄金校对 → 评测与报告</span></div>
```

替换为：

```js
        <span class="hint">准备题目 → 标准答案 → 取证据 → 评分与报告</span></div>
```

- [ ] **Step 7: `PAGES.work.sub` 副标题改 4 步**

把（约 L14）：

```js
  work: { title: "评估工作台", sub: "文档与出题 → 黄金校对 → 评测与报告" },
```

替换为：

```js
  work: { title: "评估工作台", sub: "准备题目 → 标准答案 → 取证据 → 评分与报告" },
```

- [ ] **Step 8: 文件头注释改 4 步**

把（约 L5）：

```js
   左侧导航：看板 / 项目 / 评估工作台（文档与出题→黄金校对→评测与报告）/ 对话
```

替换为：

```js
   左侧导航：看板 / 项目 / 评估工作台（准备题目→标准答案→取证据→评分与报告）/ 对话
```

- [ ] **Step 9: `FLOW_STEPS` 上方的块注释改 4 步**

把（约 L440-446）：

```js
/* ============================================================================
   页面 · 评估流程（四步向导）
   工作流脊柱：① 上传文档 → ② 生成黄金集 → ③ 取证据包 → ④ 评估报告
   - 逐步解锁门禁；进入时调 /suites/{sid}/state 定位该停的步骤（刷新/换设备恢复）
   - 复用各页已有接线（上传/出题/黄金确认/取证/评估）
   - D4：改了上游步骤 → 下游标记"需重跑"
   ========================================================================== */
```

替换为：

```js
/* ============================================================================
   页面 · 评估工作台（四步）
   工作流脊柱：① 准备题目 → ② 标准答案 → ③ 取证据 → ④ 评分与报告
   - 软门禁：未就绪的步骤灰显"待就绪"徽章但仍可点（不硬锁）
   - 进入时调 /suites/{sid}/state 定位该停的步骤（刷新/换设备恢复）
   - 复用各页已有接线（出题/黄金确认/取证/打分/报告）
   - D4：改了上游步骤 → 下游标记"需重跑"
   ========================================================================== */
```

- [ ] **Step 10: 语法校验**

Run: `node --check runtime_eval/runtime_eval/eval_web/static/app.js`
Expected: 无输出，退出码 0。

- [ ] **Step 11: 确认 evalTab / kb_evaltab 已彻底清除**

Run（用 Grep 工具搜 `app.js`，pattern `evalTab|kb_evaltab`）
Expected: 0 处匹配。

---

## Task 6: 同步 `test_serves_eval_web_spa` 断言 + 全量回归

后端 SPA 自检测试断言了旧 IA / 旧函数名，需同步成 4 步新结构（去掉已删的 `only_annotated`，加上 4 步名与新步骤函数、断言子标签逻辑已下线）。然后跑全量 pytest 保持绿。

**Files:**
- Modify: `runtime_eval/tests/test_eval_api.py`

- [ ] **Step 1: 删掉已失效的 `only_annotated` 断言**

把（约 L622，在 `test_serves_eval_web_spa` 里）：

```python
    assert "only_annotated" in app_js.text
```

整行删除（`batchEvaluateAnnotated` 已删，前端不再出现该字符串）。

- [ ] **Step 2: 把「报告历史收进第③步」的注释与第④步对齐**

把（约 L657）：

```python
    # 报告历史：收进工作台第③步，旧独立「评估报告」导航已下线
```

替换为：

```python
    # 报告历史：收进工作台第④步（评分与报告），旧独立「评估报告」导航已下线
```

- [ ] **Step 3: 追加 4 步新结构断言**

在 `test_serves_eval_web_spa` 末尾（约 L662 `assert "/projects/${pid}/runs" in app_js.text` 之后）追加：

```python
    # 工作台 4 步：准备题目 / 标准答案 / 取证据 / 评分与报告
    assert "准备题目" in app_js.text
    assert "标准答案" in app_js.text
    assert "取证据" in app_js.text
    assert "评分与报告" in app_js.text
    # 步骤③④ 拆成独立步骤函数；步骤① 三选一来源切换器
    assert "workStepEvidence" in app_js.text
    assert "workStepReport" in app_js.text
    assert "setDocSource" in app_js.text
    # 旧子标签外壳 + 步骤② 重复的批量评估按钮已下线
    assert "workEvalTab" not in app_js.text
    assert "renderEvalTab" not in app_js.text
    assert "kb_evaltab" not in app_js.text
    assert "batchEvaluateAnnotated" not in app_js.text
```

- [ ] **Step 4: 先单独跑这个测试**

Run: `cd /e/MyProjects/KnowledgeBase/runtime_eval && python -m pytest -q tests/test_eval_api.py::test_serves_eval_web_spa`
Expected: 1 passed。

- [ ] **Step 5: 全量回归**

Run: `cd /e/MyProjects/KnowledgeBase/runtime_eval && python -m pytest -q`
Expected: 76 passed（与基线一致）。

- [ ] **Step 6: 前端最终语法校验**

Run: `node --check runtime_eval/runtime_eval/eval_web/static/app.js`
Expected: 无输出，退出码 0。

---

## 手动验收清单（全部任务完成后，浏览器核对）

打开 `http://localhost:8800/`（eval-api 已起；改 JS 后浏览器加版本号或强刷）：

1. 工作台步骤条显示 4 步：准备题目 / 标准答案 / 取证据 / 评分与报告。
2. 步骤① 顶部三选一切换器可切；`AI 出题` / `拉真实日志` / `导入题库` 三种表单各自正常显示，切换时下方「本项目测试集」列表不丢。
3. 出题 / 导入 / 拉取成功后自动选中测试集并跳到步骤②。
4. 步骤② 校对 / 确认黄金正常；原「批量评估（仅已标注）」按钮已消失。
5. 步骤③ serving 批量取证（实时进度）+ Claude 自动取证都在；「去评分与报告 →」跳到步骤④。
6. 步骤④ 评估标准卡 → 全部评分 → 生成报告 → 成绩单；下方「报告历史」可打开 HTML / MD。
7. 步骤条软门禁：未就绪步骤灰显「待就绪」徽章但仍可点。
8. 刷新后按 localStorage 恢复到当前项目 / 步骤。
9. 看板「全部报告 →」落到步骤④；测试集列表「取证评分」落到步骤③。

---

## Self-Review（写计划后对照 spec 自查）

- **Spec 覆盖：** §3.1 三选一来源→Task 2；§3.2 删批量评估按钮→Task 4；§3.3 取证据独立步骤→Task 3；§3.4 评分+报告历史合一→Task 3；§3.5 软门禁就绪条件→Task 1 Step 5；§4 路由/索引（FLOW_STEPS 3→4、默认步映射、docSource、删 evalTab/kb_evaltab）→Task 1+5；§5 复用/新写/删除清单→Task 2/3/4；看板列表跳转校准→Task 5；后端不改接口→已遵守（仅改测试断言）；§7 测试同步→Task 6。无遗漏。
- **占位符扫描：** 每个改代码的步骤都给了完整 old→new 代码块，无 TBD / “类似上面” / 模糊描述。
- **命名一致性：** 新函数 `workStepEvidence` / `workStepReport` / `setDocSource` / `docSourceForm` / `docSourceAi` / `docSourcePull` / `docSourceImport` / `docSharedBody` 在 Task 1（分发数组引用）、Task 2/3（定义）、Task 6（测试断言）中拼写一致；`state.docSource` / `kb_docsource` 一致；步骤索引 0/1/2/3 与 `flowReady`/`flowDone`/`flowDefaultStep` 四元一致。
- **`.tabs/.tab` 样式：** 不删（步骤① 三选一切换器复用），故无 styles.css 改动——与 spec §5「若不再用」条件一致。
