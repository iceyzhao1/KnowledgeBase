"use strict";

/* ===========================================================================
   通用知识库测试框架 · eval-web 单页应用
   左侧导航：看板 / 项目 / 评估工作台（准备题目→标准答案→取证据→评分与报告）/ 对话
   前端仅调 eval-api，无构建步骤。
   =========================================================================== */

const TYPES = ["factoid", "conceptual", "procedural", "constraint", "troubleshooting", "navigational"];
const DIFFS = ["easy", "normal", "medium", "hard"];
const PAGES = {
  dashboard: { title: "看板总览", sub: "项目整体进度与关键指标一览" },
  projects: { title: "项目", sub: "选择或新建项目，进入评估工作台" },
  work: { title: "评估工作台", sub: "准备题目 → 标准答案 → 取证据 → 评分与报告" },
  chat: { title: "对话试问", sub: "输入问题，预览知识库会检索到哪些片段" },
};
// 旧 hash 兼容：老链接/书签 → 新页面
const PAGE_ALIAS = { flow: "work", suites: "work", gold: "work", reports: "work", retrieval: "work" };

const state = {
  projects: [],
  projectId: localStorage.getItem("kb_project") || null,
  projectName: null,
  page: "dashboard",
  suiteId: localStorage.getItem("kb_suite") || null,
  suite: null,
  chat: [],
  flowStep: null,          // 向导当前步（null=按 /state 自动定位）
  flow: null,              // 向导四步完成度快照（来自 /state）
  flowStale: new Set(),    // D4：上游改动后下游"需重跑"的步骤索引
  docSource: localStorage.getItem("kb_docsource") || "ai",   // 步骤①出题来源：ai/pull/import
};

/* ----------------------------- 基础工具 ----------------------------- */
function toast(msg, kind = "ok") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show " + (kind === "err" ? "err" : "ok");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 2600);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function splitLines(t) {
  return String(t || "").split("\n").map((s) => s.trim()).filter(Boolean);
}
function pct(v) { return `${((v || 0) * 100).toFixed(0)}%`; }
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];

/* ------------------- 测试集清单（服务端为准，localStorage 仅离线兜底） ------
   Phase 0：测试集列表 / 评估档案已落 SQLite，前端开页时从服务端拉一份覆盖到
   localStorage（kb_suites / kb_runsum），渲染仍读 localStorage —— 这样换设备也能
   看到全部测试集与历史评测，断网时退回上次缓存。 */
function regAll() { try { return JSON.parse(localStorage.getItem("kb_suites") || "{}"); } catch (e) { return {}; } }
function regSuites(pid) { return (regAll()[pid] || []); }
function regAdd(pid, summary) {
  const all = regAll();
  const list = (all[pid] || []).filter((s) => s.suite_id !== summary.suite_id);
  list.unshift(summary);
  all[pid] = list.slice(0, 200);
  localStorage.setItem("kb_suites", JSON.stringify(all));
}
function regSet(pid, list) {
  const all = regAll();
  all[pid] = list.slice(0, 200);
  localStorage.setItem("kb_suites", JSON.stringify(all));
}
function regRemove(pid, sid) {
  const all = regAll();
  all[pid] = (all[pid] || []).filter((s) => s.suite_id !== sid);
  localStorage.setItem("kb_suites", JSON.stringify(all));
}
function suiteSummary(suite, label) {
  const ev = suite.cases.filter((c) => (c.expected_evidence || []).length || c.expected_answer).length;
  return {
    suite_id: suite.suite_id, backend: suite.backend, created_at: suite.created_at,
    case_count: suite.cases.length, annotated: ev, label: label || suite.backend,
  };
}
function registerSuite(suite, label) { regAdd(suite.project_id, suiteSummary(suite, label)); }

/* 从服务端拉取该项目测试集清单 → 覆盖本地缓存（保留本地已知的 annotated/label） */
async function syncSuitesFromServer(pid, rerender) {
  if (!pid) return;
  try {
    const rows = await api(`/api/v1/projects/${pid}/suites`); // 最新在前
    const prev = {}; regSuites(pid).forEach((s) => { prev[s.suite_id] = s; });
    const list = rows.map((r) => {
      const old = prev[r.suite_id] || {};
      return {
        suite_id: r.suite_id, backend: r.backend, created_at: r.created_at,
        case_count: r.case_count, annotated: old.annotated || 0,
        label: old.label || r.backend,
      };
    });
    regSet(pid, list);
    if (rerender && state.projectId === pid) setPage(state.page);
  } catch (e) { /* 离线：保留 localStorage 兜底 */ }
}

/* 评估结果摘要缓存（看板用） */
function runSumAll() { try { return JSON.parse(localStorage.getItem("kb_runsum") || "{}"); } catch (e) { return {}; } }
function saveRunSum(sid, kind, m) {
  const all = runSumAll();
  const k = (m.k_values || []).slice(-1)[0] || 10;
  const key = String(k);
  all[sid] = {
    kind, run_id: m.run_id, k, at: new Date().toISOString(), has_gold: m.has_gold !== false,
    ndcg: m.ndcg[key], recall: m.recall[key], hit: m.hit_rate[key], mrr: m.mrr[key],
    ctx_recall: m.context_recall, judged: m.judged_cases, total: m.total_cases, kb_score: m.kb_score,
  };
  localStorage.setItem("kb_runsum", JSON.stringify(all));
}

/* 从服务端拉取该项目评估档案 → 每测试集取最近一条检索层 run，覆盖本地缓存 */
async function syncRunsFromServer(pid, rerender) {
  if (!pid) return;
  try {
    const rows = await api(`/api/v1/projects/${pid}/runs`); // 最新在前
    const all = runSumAll();
    const kindMap = { batch: "annotated", perq: "perq", live: "live" };
    const seen = {};
    for (const r of rows) {
      if (r.layer !== "retrieval") continue;   // 看板「最近检索评估」只看检索层
      if (seen[r.suite_id]) continue;          // 每测试集仅取最近一条
      seen[r.suite_id] = true;
      const m = r.metrics || {};
      all[r.suite_id] = {
        kind: kindMap[r.kind] || r.kind, run_id: r.run_id, k: r.k, at: r.created_at,
        has_gold: m.has_gold !== false, ndcg: m.ndcg, recall: m.recall,
        hit: m.hit_rate, mrr: m.mrr, ctx_recall: m.context_recall,
        judged: m.judged_cases, total: m.total_cases, kb_score: m.kb_score,
      };
    }
    localStorage.setItem("kb_runsum", JSON.stringify(all));
    if (rerender && state.projectId === pid) setPage(state.page);
  } catch (e) { /* 离线：保留 localStorage 兜底 */ }
}

/* ----------------------------- 路由 / 框架 ----------------------------- */
function setPage(page) {
  page = PAGE_ALIAS[page] || page;
  if (!PAGES[page]) page = "dashboard";
  state.page = page;
  $("#pageTitle").textContent = PAGES[page].title;
  $("#pageSub").textContent = PAGES[page].sub;
  $$("#nav .navitem").forEach((n) => n.classList.toggle("active", n.dataset.page === page));
  const renderers = { dashboard: renderDashboard, projects: renderProjects, work: renderWork, chat: renderChat };
  (renderers[page] || renderDashboard)();
}
function go(page) { location.hash = "#/" + page; }
window.addEventListener("hashchange", () => setPage((location.hash || "#/dashboard").replace("#/", "")));

/* ----------------------------- 项目 ----------------------------- */
async function loadProjects() {
  try {
    state.projects = await api("/api/v1/projects");
  } catch (e) { state.projects = []; }
  if (state.projectId && !state.projects.find((p) => p.project_id === state.projectId)) state.projectId = null;
  if (!state.projectId && state.projects.length) state.projectId = state.projects[0].project_id;
  syncProjectUI();
  if (state.projectId) { syncSuitesFromServer(state.projectId, true); syncRunsFromServer(state.projectId, true); }
}
function syncProjectUI() {
  const sel = $("#projSelect");
  const cur = state.projects.find((p) => p.project_id === state.projectId);
  state.projectName = cur ? cur.name : null;
  sel.innerHTML = '<option value="">— 选择项目 —</option>' +
    state.projects.map((p) => `<option value="${p.project_id}"${p.project_id === state.projectId ? " selected" : ""}>${esc(p.name)}</option>`).join("") +
    '<option value="__new__">+ 新建项目…</option>';
  const mini = $("#projMini");
  if (cur) { mini.className = "proj-mini"; $(".nm", mini).textContent = cur.name; }
  else { mini.className = "proj-mini none"; $(".nm", mini).textContent = "未选择项目"; }
}
function onProjectPick(v) {
  if (v === "__new__") { syncProjectUI(); go("projects"); setTimeout(() => $("#newProjName") && $("#newProjName").focus(), 60); return; }
  state.projectId = v || null;
  localStorage.setItem("kb_project", state.projectId || "");
  state.suiteId = null; state.suite = null; localStorage.removeItem("kb_suite");
  syncProjectUI();
  setPage(state.page);
  if (state.projectId) { syncSuitesFromServer(state.projectId, true); syncRunsFromServer(state.projectId, true); }
}
async function createProject(name) {
  if (!name) { toast("请填写项目名称", "err"); return; }
  try {
    const p = await api("/api/v1/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    await loadProjects();
    onProjectPick(p.project_id);
    toast("项目已创建：" + p.name);
  } catch (e) { toast("创建失败：" + e.message, "err"); }
}
async function deleteProject(pid, name) {
  if (!window.confirm(`确定删除项目「${name}」？\n其下所有测试集、评估结果、报告将一并删除，且无法恢复。`)) return;
  try {
    await api(`/api/v1/projects/${pid}`, { method: "DELETE" });
    if (state.projectId === pid) {
      state.projectId = null; state.projectName = ""; localStorage.removeItem("kb_project");
      state.suiteId = null; state.suite = null; localStorage.removeItem("kb_suite");
      syncProjectUI();
    }
    await loadProjects();
    renderProjectList();
    toast("项目已删除：" + name);
  } catch (e) { toast("删除失败：" + e.message, "err"); }
}

/* 选中测试集（gold/retrieval 共用），返回是否成功 */
async function selectSuite(sid) {
  if (!sid) { state.suiteId = null; state.suite = null; localStorage.removeItem("kb_suite"); return false; }
  try {
    const suite = await api(`/api/v1/suites/${sid}`);
    state.suiteId = sid; state.suite = suite;
    localStorage.setItem("kb_suite", sid);
    registerSuite(suite);
    return true;
  } catch (e) { toast("打开测试集失败：" + e.message, "err"); state.suiteId = null; state.suite = null; return false; }
}

/* 测试集下拉选择器（gold/retrieval 页顶部复用） */
function suitePickerHtml() {
  const list = regSuites(state.projectId);
  const opts = list.map((s) => {
    const when = (s.created_at || "").replace("T", " ").slice(0, 16);
    return `<option value="${s.suite_id}"${s.suite_id === state.suiteId ? " selected" : ""}>${esc(s.label)} · ${s.case_count}题 · ${when}</option>`;
  }).join("");
  return `<select id="suitePick" onchange="onSuitePick(this.value)">
      <option value="">${list.length ? "— 选择测试集 —" : "（该项目暂无测试集，去「测试集管理」生成）"}</option>${opts}
    </select>`;
}
async function onSuitePick(sid) {
  if (state.page === "work") {
    state.flowStep = null; state.flowStale = new Set();
    await selectSuite(sid); setPage("work"); return;
  }
  if (await selectSuite(sid)) setPage(state.page);
}

/* ============================================================================
   页面 1 · 看板总览
   ========================================================================== */
async function renderDashboard() {
  const v = $("#view"); v.className = "view";
  if (!state.projectId) { v.innerHTML = noProjectBlock(); bindNewProject(); return; }
  const pid = state.projectId;
  const suites = regSuites(pid);
  const totalCases = suites.reduce((a, s) => a + (s.case_count || 0), 0);
  const totalAnnot = suites.reduce((a, s) => a + (s.annotated || 0), 0);

  v.innerHTML = `
    <div class="stats" id="dashStats">
      <div class="stat accent"><div class="n">${suites.length}</div><div class="l">测试集</div><div class="sub">本项目已建</div></div>
      <div class="stat"><div class="n">${totalCases}</div><div class="l">累计用例</div><div class="sub">已标注 ${totalAnnot}</div></div>
      <div class="stat"><div class="n" id="docCount">…</div><div class="l">已解析文档</div></div>
    </div>
    <div class="grid cols-2">
      <div class="card">
        <div class="card-h"><h3>最近测试集</h3><span class="spacer"></span><button class="btn sm ghost" onclick="go('work')">去工作台 →</button></div>
        <div id="recentSuites"></div>
      </div>
      <div class="card">
        <div class="card-h"><h3>最近评估</h3><span class="spacer"></span><button class="btn sm ghost" onclick="openHistory()">全部报告 →</button></div>
        <div id="recentRuns"></div>
      </div>
    </div>`;

  // 最近测试集
  const rs = $("#recentSuites");
  if (!suites.length) rs.innerHTML = `<div class="muted small">还没有测试集。去「测试集管理」上传文档并出题。</div>`;
  else rs.innerHTML = `<div class="list">` + suites.slice(0, 6).map((s) => {
    const when = (s.created_at || "").replace("T", " ").slice(0, 16);
    return `<div class="litem" onclick="openSuiteWork('${s.suite_id}', 1)">
      <div class="grow"><div class="nm">${esc(s.label)}</div><div class="meta">${s.case_count} 题 · 已标注 ${s.annotated || 0} · ${when}</div></div>
      <span class="badge ${s.annotated >= s.case_count && s.case_count ? "ok" : "muted"}">${s.annotated || 0}/${s.case_count}</span>
    </div>`;
  }).join("") + `</div>`;

  // 最近评估
  const runs = runSumAll();
  const mine = suites.map((s) => runs[s.suite_id]).filter(Boolean).sort((a, b) => (b.at || "").localeCompare(a.at || ""));
  const rr = $("#recentRuns");
  if (!mine.length) rr.innerHTML = `<div class="muted small">还没有评估结果。在「评估工作台」跑一次。</div>`;
  else rr.innerHTML = mine.slice(0, 4).map((m) => `
    <div class="ev" style="background:var(--surface-2)">
      <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center">
        <b>NDCG@${m.k} ${pct(m.ndcg)}</b>
        <span class="muted small">HitRate ${pct(m.hit)} · MRR ${pct(m.mrr)} · ${m.has_gold ? "Recall " + pct(m.recall) : "无黄金"}</span>
        <span class="badge muted">${m.judged}/${m.total} 题</span>
      </div>
      <div class="src">${(m.at || "").replace("T", " ").slice(0, 16)} · ${m.kind === "annotated" ? "批量评估(已标注)" : m.kind === "perq" ? "逐题报告" : "实时日志"}</div>
    </div>`).join("");

  // 异步补两个计数
  api(`/api/v1/projects/${pid}/documents`).then((d) => { const el = $("#docCount"); if (el) el.textContent = d.length; }).catch(() => { const el = $("#docCount"); if (el) el.textContent = "—"; });
}
async function renderProjects() {
  const v = $("#view"); v.className = "view wide";
  v.innerHTML = `
    <div class="card tight">
      <div class="card-h"><h3>新建项目</h3><span class="hint">项目是文档、测试集与评估结果的容器</span></div>
      <div class="inline-fields">
        <div class="field grow"><input type="text" id="newProjName" placeholder="例如：客服知识库 v2" /></div>
        <button class="btn" id="newProjBtn">＋ 新建</button>
      </div>
    </div>
    <div class="card">
      <div class="card-h"><h3>全部项目</h3><span class="spacer"></span><span class="hint" id="projCount"></span></div>
      <div id="projList" class="list"><div class="muted small">加载中…</div></div>
    </div>`;
  bindNewProject();
  renderProjectList();
}
function renderProjectList() {
  const box = $("#projList"); if (!box) return;
  const ps = state.projects || [];
  const cnt = $("#projCount"); if (cnt) cnt.textContent = ps.length ? `${ps.length} 个` : "";
  if (!ps.length) { box.innerHTML = `<div class="muted small">还没有项目。用上面的输入框新建一个开始。</div>`; return; }
  box.innerHTML = ps.map((p) => {
    const active = p.project_id === state.projectId ? " active" : "";
    return `<div class="litem${active}">
      <div class="grow" onclick="openProject('${jsq(p.project_id)}')" style="cursor:pointer">
        <div class="nm">📁 ${esc(p.name)}</div>
        <div class="meta mono faint">${esc(p.project_id)}</div></div>
      <button class="btn sm" onclick="openProject('${jsq(p.project_id)}')">进入工作台 →</button>
      <button class="btn sm danger" onclick="deleteProject('${jsq(p.project_id)}', '${jsq(p.name)}')">删除</button>
    </div>`;
  }).join("");
}
function openProject(pid) {
  state.projectId = pid;
  localStorage.setItem("kb_project", pid);
  state.suiteId = null; state.suite = null; localStorage.removeItem("kb_suite");
  state.flowStep = null; state.flowStale = new Set();
  syncProjectUI();
  if (pid) { syncSuitesFromServer(pid, false); syncRunsFromServer(pid, false); }
  go("work");
}
async function renderWork() {
  const v = $("#view"); v.className = "view wide";
  if (!state.projectId) { v.innerHTML = noProjectBlock(); bindNewProject(); return; }
  v.innerHTML = `
    <div class="card tight">
      <div class="card-h"><h3>评估工作台 · ${esc(state.projectName || "")}</h3>
        <span class="hint">导入测试集 → 题库题目 → 检索取证 → 评分报告</span></div>
      <div class="inline-fields">
        <div class="field grow"><span class="lbl">当前测试集（出题后自动选中；可切换/留空开新）</span>${suitePickerHtml()}</div>
        <span id="flowStateLine" class="muted small"></span>
      </div>
    </div>
    <div id="flowStepper"></div>
    <div id="flowBody"></div>`;
  await flowRefresh();
}
async function openSuiteFrom(sid, page) {
  const stepMap = { gold: 1, retrieval: 2, reports: 3 };
  if (await selectSuite(sid)) { state.flowStep = stepMap[page] ?? 0; state.flowStale = new Set(); go("work"); }
}
async function openSuiteWork(sid, step) {
  if (await selectSuite(sid)) { state.flowStep = (step == null ? 0 : step); state.flowStale = new Set(); go("work"); }
}
// 看板「全部报告 →」：进工作台第④步（评分与报告，含报告历史）
function openHistory() { state.flowStep = 3; state.flowStale = new Set(); go("work"); }

function noProjectBlock() {
  return `<div class="card"><div class="empty">
      <div class="big">📁</div>
      <div class="h">先创建或选择一个项目</div>
      <div class="muted">项目是文档、测试集与评估结果的容器。</div>
      <div class="inline-fields" style="justify-content:center;margin-top:18px;max-width:420px;margin-left:auto;margin-right:auto">
        <div class="field grow"><input type="text" id="newProjName" placeholder="例如：客服知识库 v2" /></div>
        <button class="btn" id="newProjBtn">＋ 新建项目</button>
      </div>
    </div></div>`;
}
function bindNewProject() {
  const btn = $("#newProjBtn"), inp = $("#newProjName");
  if (btn) btn.onclick = () => createProject(inp.value.trim());
  if (inp) inp.onkeydown = (e) => { if (e.key === "Enter") createProject(inp.value.trim()); };
}

/* ============================================================================
   页面 · 评估报告（历史档案）
   读 /projects/{pid}/runs（服务端 run_summaries，最新在前）→ 列历次评测，
   每条可打开 HTML / Markdown 报告。换设备打开也能看到完整历史。
   ========================================================================== */
const RUN_KIND_LABEL = { batch: "批量评估", perq: "逐题报告", live: "实时日志" };
const RUN_LAYER_LABEL = { retrieval: "检索层", response: "应用层" };
function reportUrl(r, fmt) {
  const base = r.layer === "retrieval" ? "retrieval-runs" : "runs";
  return `/api/v1/${base}/${r.run_id}/report?format=${fmt}`;
}
function runScoreLine(r) {
  const m = r.metrics || {};
  if (r.layer === "retrieval") {
    const kt = r.k != null ? `@${r.k}` : "";
    const parts = [];
    if (m.ndcg != null) parts.push(`NDCG${kt} <b>${pct(m.ndcg)}</b>`);
    if (m.hit_rate != null) parts.push(`HitRate ${pct(m.hit_rate)}`);
    if (m.has_gold !== false && m.recall != null) parts.push(`Recall ${pct(m.recall)}`);
    if (m.kb_score != null) parts.push(`KB分 ${pct(m.kb_score)}`);
    return parts.join(" · ") || "—";
  }
  const parts = [];
  if (m.overall_accuracy != null) parts.push(`准确率 <b>${pct(m.overall_accuracy)}</b>`);
  if (m.overall_avg_score != null) parts.push(`均分 ${(m.overall_avg_score).toFixed(2)}`);
  return parts.join(" · ") || "—";
}
async function renderReportListInto(targetSel) {
  const box = $(targetSel); if (!box) return;
  const pid = state.projectId;
  box.innerHTML = `<div class="card"><div id="reportList"><div class="muted small">加载中…</div></div></div>`;
  try {
    const rows = await api(`/api/v1/projects/${pid}/runs`);
    const labels = {}; regSuites(pid).forEach((s) => { labels[s.suite_id] = s.label; });
    const list = $("#reportList"); if (!list) return;
    if (!rows.length) { list.innerHTML = `<div class="empty"><div class="h">还没有评估报告</div><div class="muted">在「取证据 / 打分与报告」走完一次评估，这里就会出现历史档案。</div></div>`; return; }
    list.innerHTML = `<div class="list">` + rows.map((r) => {
      const when = (r.created_at || "").replace("T", " ").slice(0, 16);
      const label = labels[r.suite_id] || r.suite_id || "—";
      const statusBadge = r.status === "done" ? `<span class="badge ok">完成</span>`
        : r.status === "partial" ? `<span class="badge muted">进行中</span>`
        : `<span class="badge muted">${esc(r.status || "")}</span>`;
      return `<div class="litem">
        <div class="grow">
          <div class="nm">${esc(label)} <span class="badge muted">${RUN_LAYER_LABEL[r.layer] || r.layer}</span> <span class="badge muted">${RUN_KIND_LABEL[r.kind] || r.kind}</span></div>
          <div class="meta">${runScoreLine(r)}</div>
          <div class="src">${when}</div>
        </div>
        ${statusBadge}
        <a class="btn sm" href="${reportUrl(r, "html")}" target="_blank">↗ HTML</a>
        <a class="btn sm ghost" href="${reportUrl(r, "md")}" target="_blank">MD</a>
      </div>`;
    }).join("") + `</div>`;
  } catch (e) {
    const list = $("#reportList"); if (list) list.innerHTML = `<div class="muted small" style="color:var(--red)">加载失败：${esc(e.message)}</div>`;
  }
}

/* ============================================================================
   页面 · 评估工作台（四步）
   工作流脊柱：① 准备题目 → ② 标准答案 → ③ 取证据 → ④ 评分与报告
   - 软门禁：未就绪的步骤灰显"待就绪"徽章但仍可点（不硬锁）
   - 进入时调 /suites/{sid}/state 定位该停的步骤（刷新/换设备恢复）
   - 复用各页已有接线（出题/黄金确认/取证/打分/报告）
   - D4：改了上游步骤 → 下游标记"需重跑"
   ========================================================================== */
const FLOW_STEPS = [
  { key: "import", t: "导入测试集", d: "AI 出题 / 拉真实日志 / 导入题目（可命名）" },
  { key: "overview", t: "题库题目", d: "查看并编辑本题库全部题目（改 / 增 / 删）" },
  { key: "evidence", t: "检索取证", d: "收被测系统答卷（serving 取证 / Claude 作答）" },
  { key: "report", t: "评分报告", d: "逐题打分 → 成绩单 + 增量价值(L4) + 报告历史" },
];

/* 拉取四步完成度快照（有测试集走 /state；无测试集仅看项目文档数）*/
async function flowRefresh(keepStep) {
  const f = {
    docCount: 0, caseCount: 0, goldConfirmed: 0, evidenceCount: 0,
    steps: { documents: false, questions: false, gold: false, evidence: false, report: false },
    latest: null, currentStep: "documents",
  };
  try {
    if (state.suiteId) {
      const st = await api(`/api/v1/suites/${state.suiteId}/state`);
      f.docCount = st.doc_count; f.caseCount = st.case_count;
      f.goldConfirmed = st.gold_confirmed; f.evidenceCount = st.evidence_count;
      f.steps = st.steps; f.latest = st.latest_run; f.currentStep = st.current_step;
    } else {
      const docs = await api(`/api/v1/projects/${state.projectId}/documents`);
      f.docCount = docs.length; f.steps.documents = docs.length > 0;
      f.currentStep = docs.length ? "questions" : "documents";
    }
  } catch (e) { /* 离线/未就绪：用空快照 */ }
  state.flow = f;
  // 决定当前步：未手动选过 → 跳到第一个未完成步；选过但被锁 → 回落到默认
  if (!keepStep && state.flowStep == null) state.flowStep = flowDefaultStep(f);
  if (state.flowStep == null || state.flowStep > 3) state.flowStep = flowDefaultStep(f);
  const line = $("#flowStateLine");
  if (line) line.textContent = state.suiteId
    ? `文档 ${f.docCount} · 题 ${f.caseCount} · 已确认黄金 ${f.goldConfirmed} · 证据 ${f.evidenceCount}`
    : `文档 ${f.docCount} · 尚未生成测试集`;
  renderFlowStepper();
  renderFlowStep();
}

function flowDefaultStep(f) {
  return ({ documents: 0, questions: 1, gold: 1, evidence: 2, report: 3 })[f.currentStep] ?? 0;
}
// 软门禁：flowReady 仅用于"提示/徽章"，导航不再硬锁（spec 要求可随便点回）
function flowReady(f, i) {
  if (!f) return i === 0;
  if (i === 0) return true;
  if (i === 1) return !!f.steps.questions;                        // 有题才好看总览/编辑
  if (i === 2) return !!f.steps.questions;                        // 有题即可取证（已无黄金门禁）
  if (i === 3) return !!(f.steps.questions && f.steps.evidence);  // 取到证据才好打分
  return false;
}
function flowDone(f, i) {
  if (!f) return false;
  return [f.steps.questions, f.steps.questions, f.steps.evidence, f.steps.report][i];
}
function flowMarkStale(fromStep) { for (let i = fromStep + 1; i < FLOW_STEPS.length; i++) state.flowStale.add(i); }
function flowGoStep(i) { state.flowStep = i; renderFlowStepper(); renderFlowStep(); }

function renderFlowStepper() {
  const box = $("#flowStepper"); if (!box) return;
  const f = state.flow;
  box.innerHTML = `<div class="stepper">` + FLOW_STEPS.map((s, i) => {
    const ready = flowReady(f, i);
    const done = flowDone(f, i);
    const active = i === state.flowStep;
    const stale = state.flowStale.has(i);
    const cls = ["stepper-item", active ? "active" : "", done ? "done" : ""].filter(Boolean).join(" ");
    const badge = stale ? `<span class="badge warn">需重跑</span>`
      : done ? `<span class="badge ok">✓</span>`
      : !ready ? `<span class="badge muted">待就绪</span>` : "";
    return `<div class="${cls}" onclick="flowGoStep(${i})">
        <div class="stepper-num">${i + 1}</div>
        <div class="stepper-txt"><div class="t">${s.t} ${badge}</div><div class="d">${s.d}</div></div>
      </div>${i < FLOW_STEPS.length - 1 ? `<div class="stepper-arrow">→</div>` : ""}`;
  }).join("") + `</div>`;
}

function renderFlowStep() {
  const fns = [workStepImport, workStepOverview, workStepEvidence, workStepReport];
  (fns[state.flowStep] || workStepImport)();
}
function workStepImport() {
  const box = $("#flowBody"); if (!box) return;
  const src = state.docSource || "ai";
  const tab = (s, label) => `<button class="tab ${src === s ? "active" : ""}" onclick="setDocSource('${s}')">${label}</button>`;
  box.innerHTML = `
    <div class="card tight"><div class="tabs">
      ${tab("ai", "AI 出题")}${tab("pull", "拉真实日志")}${tab("import", "导入测试集")}
    </div></div>
    ${docSourceForm(src)}
    ${docSharedBody()}
    <div class="row-actions" style="margin-top:4px">
      <button class="btn ghost" onclick="flowGoStep(1)">下一步：题库题目 →</button>
      <span class="muted small">${flowReady(state.flow, 1) ? "" : "出题 / 拉取 / 导入后即可查看与编辑题目"}</span>
    </div>`;
  if (src === "ai") { renderTypeChecks(); loadDocs(); }
  renderSuiteList();
}
function setDocSource(src) {
  state.docSource = src;
  localStorage.setItem("kb_docsource", src);
  workStepImport();
}
function docSourceForm(src) {
  if (src === "pull") return docSourcePull();
  if (src === "import") return docSourceImport();
  return docSourceAi();
}
/* —— 步骤② 题库题目总览：列本项目所有测试集的题，支持逐题编辑/增删 —— */
async function workStepOverview() {
  const box = $("#flowBody"); if (!box) return;
  const pid = state.projectId;
  box.innerHTML = `<div class="card"><div class="card-h">
      <h3>题库题目总览</h3><span class="hint">本题库（项目）下所有测试集的题目，可改 / 增 / 删</span>
      <span class="spacer"></span>
      <button class="btn sm ghost" onclick="workStepOverview()">刷新</button>
      <button class="btn sm" onclick="flowGoStep(0)">＋ 导入/出题</button>
    </div><div id="overviewBody"><div class="muted small">加载中…</div></div></div>`;
  try {
    const suites = await api(`/api/v1/projects/${pid}/suites`);
    const body = $("#overviewBody"); if (!body) return;
    if (!suites.length) { body.innerHTML = `<div class="empty"><div class="h">题库还没有题目</div><div class="muted">先去第①步导入或出题。</div></div>`; return; }
    const full = await Promise.all(suites.map((s) => api(`/api/v1/suites/${s.suite_id}`)));
    body.innerHTML = full.map(renderSuiteCard).join("");
  } catch (e) {
    const body = $("#overviewBody"); if (body) body.innerHTML = `<div class="muted small" style="color:var(--red)">加载失败：${esc(e.message)}</div>`;
  }
}

function suiteLabel(s) { return s.name || (s.corpus_files || []).join(",") || s.backend || s.suite_id; }

function renderSuiteCard(suite) {
  const editing = state.editSuiteId === suite.suite_id;
  const head = `<div class="ov-suite-h">
      <b>${esc(suiteLabel(suite))}</b>
      <span class="badge muted">${(editing ? state.editCases : suite.cases).length} 题</span>
      <span class="spacer"></span>
      ${editing
        ? `<button class="btn sm" onclick="overviewAddCase('${suite.suite_id}')">＋ 加题</button>
           <button class="btn sm" onclick="overviewSaveEdit('${suite.suite_id}')">保存</button>
           <button class="btn sm ghost" onclick="overviewCancelEdit()">取消</button>`
        : `<button class="btn sm ghost" onclick="overviewStartEdit('${suite.suite_id}')">编辑</button>`}
    </div>`;
  const rows = (editing ? state.editCases : suite.cases).map((c, i) => editing
    ? overviewEditRow(c, i)
    : `<tr><td>${esc(c.question)}</td><td>${esc(c.expected_answer)}</td><td>${esc((c.key_points || []).join(" / "))}</td><td>${esc(c.difficulty || "normal")}</td></tr>`
  ).join("");
  return `<div class="ov-suite" data-sid="${suite.suite_id}">${head}
    <table class="tbl ov-tbl"><thead><tr><th>问题</th><th>标准答案</th><th>要点</th><th>难度</th>${editing ? "<th></th>" : ""}</tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function overviewEditRow(c, i) {
  const diffOpts = ["easy", "normal", "hard"].map((d) =>
    `<option value="${d}"${(c.difficulty || "normal") === d ? " selected" : ""}>${d}</option>`).join("");
  return `<tr data-i="${i}">
    <td><textarea class="ov-in" data-f="question" rows="2">${esc(c.question)}</textarea></td>
    <td><textarea class="ov-in" data-f="expected_answer" rows="2">${esc(c.expected_answer)}</textarea></td>
    <td><textarea class="ov-in" data-f="key_points" rows="2">${esc((c.key_points || []).join("\n"))}</textarea></td>
    <td><select class="ov-in" data-f="difficulty">${diffOpts}</select></td>
    <td><button class="btn sm ghost" onclick="overviewDelCase(${i})">删</button></td>
  </tr>`;
}

function overviewStartEdit(sid) {
  api(`/api/v1/suites/${sid}`).then((suite) => {
    state.editSuiteId = sid;
    state.editCases = JSON.parse(JSON.stringify(suite.cases));
    workStepOverview();
  }).catch((e) => toast("打开编辑失败：" + e.message, "err"));
}
function overviewCancelEdit() { state.editSuiteId = null; state.editCases = null; workStepOverview(); }

function overviewSyncFromDom() {
  document.querySelectorAll('.ov-suite[data-sid] tbody tr[data-i]').forEach((tr) => {
    const i = +tr.getAttribute("data-i");
    const c = state.editCases[i]; if (!c) return;
    tr.querySelectorAll(".ov-in").forEach((el) => {
      const f = el.getAttribute("data-f");
      if (f === "key_points") c.key_points = el.value.split("\n").map((x) => x.trim()).filter(Boolean);
      else c[f] = el.value;
    });
  });
}
function overviewAddCase(sid) {
  overviewSyncFromDom();
  const n = state.editCases.length + 1;
  state.editCases.push({
    id: `c_new_${Date.now()}_${n}`, question: "", question_type: "factoid",
    expected_answer: "", key_points: [], expected_evidence: [], expected_entities: [],
    source: { doc: "" }, difficulty: "normal",
  });
  workStepOverview();
}
function overviewDelCase(i) {
  overviewSyncFromDom();
  state.editCases.splice(i, 1);
  workStepOverview();
}
async function overviewSaveEdit(sid) {
  overviewSyncFromDom();
  if (!state.editCases.length) { toast("至少保留一题", "err"); return; }
  try {
    await api(`/api/v1/suites/${sid}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cases: state.editCases }),
    });
    toast("已保存");
    state.editSuiteId = null; state.editCases = null;
    if (state.suiteId === sid) await selectSuite(sid);
    workStepOverview();
  } catch (e) { toast("保存失败：" + e.message, "err"); }
}

function workStepEvidence() {
  const box = $("#flowBody"); if (!box) return;
  if (!state.suite) { box.innerHTML = pickHint("请先选择一个测试集（在「题库题目」里点对应集，或在第①步出题/导入）。"); return; }
  flowStepEvidence("#flowBody");
}
function workStepReport() {
  const box = $("#flowBody"); if (!box) return;
  if (!state.suite) { box.innerHTML = pickHint("请先选择测试集并在「检索取证」取到证据。"); return; }
  box.innerHTML = `<div id="reportMain"></div>
    <div class="card-h" style="margin-top:6px"><h3>报告历史</h3><span class="hint">本项目历次评测，可打开 HTML / MD 报告</span></div>
    <div id="reportHistory"></div>`;
  flowStepReport("#reportMain");
  renderReportListInto("#reportHistory");
}

/* —— 步骤③ 取证据包（默认 serving 批量取证；可选 Claude 自动跑批）—— */
function flowStepEvidence(targetSel) {
  const box = $(targetSel || "#flowBody"); if (!box) return;
  if (!state.suite) { box.innerHTML = pickHint("请先在上一步生成测试集。"); return; }
  box.innerHTML = `<div class="card">
      <div class="card-h"><h3>③ 取证据包</h3><span class="hint">默认从 serving 真实日志按问题取检索结果</span></div>
      <div class="inline-fields">
        <div class="field"><span class="lbl">检索器名</span><input type="text" id="retName" value="serving" style="width:120px" /></div>
        <div class="field"><span class="lbl">domain（可空）</span><input type="text" id="retDomain" placeholder="全部" style="width:120px" /></div>
        <button class="btn" id="pullAllBtn">批量取证（serving）</button>
      </div>
      <div class="muted small" style="margin:12px 0 4px">可选：让 Claude 挂 MCP 自动检索作答（无 serving 日志时用）。</div>
      <div class="inline-fields">
        <div class="field"><span class="lbl">Agent 名</span><input type="text" id="agentName" value="claude-agent" style="width:140px" /></div>
        <label class="field" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="agentOnlyMissing" /><span class="lbl" style="margin:0">仅补跑未完成的题</span></label>
        <button class="btn ghost" id="agentRunBtn">Claude 自动取证</button>
      </div>
      <div class="muted small" id="agentLine" style="margin-top:10px"></div>
      <div class="muted small" id="progressLine" style="margin-top:6px"></div>
      <div class="row-actions" style="margin-top:12px">
        <button class="btn ghost" onclick="flowGoStep(3)">去评分与报告 →</button>
      </div>
    </div>
    <div id="retCases"></div>`;
  renderRetrievalCases(); loadProgress(true);
}

/* —— 步骤④ 评估报告（逐题打分 + 聚合报告）—— */
function flowStepReport(targetSel) {
  const box = $(targetSel || "#flowBody"); if (!box) return;
  if (!state.suite) { box.innerHTML = pickHint("请先在前面步骤生成测试集并取证。"); return; }
  const f = state.flow;
  box.innerHTML = `<div class="card" id="criteriaCard"><div class="card-h"><h3>评估标准</h3><span class="hint">加载中…</span></div></div>
    <div class="card">
      <div class="card-h"><h3>④ 评估报告</h3><span class="hint">大模型逐题打 0–3 相关度 → 聚合 HitRate/MRR/NDCG/召回</span></div>
      <div class="row-actions">
        <button class="btn ghost" id="judgeAllBtn">全部评估</button>
        <button class="btn" id="reportBtn" disabled>生成最终报告</button>
        <a class="btn sm ghost" id="retReport" href="#" target="_blank" style="display:none">↗ 打开报告</a>
      </div>
      <div class="muted small" id="progressLine" style="margin-top:10px"></div>
      <div class="stats" id="retSummary" style="margin-top:14px"></div>
    </div>
    ${f.latest ? `<div class="card"><div class="card-h"><h3>最近一次评估</h3></div>
      <div class="muted small">${(f.latest.created_at || "").replace("T", " ").slice(0, 16)} · ${f.latest.layer === "retrieval" ? "检索层" : "应用层"} · ${f.latest.status}</div></div>` : ""}
    <div class="card" id="upliftCard">
      <div class="card-h"><h3>增量价值（L4）</h3>
        <span class="hint">闭卷裸答(A) vs 用知识库(C)，逐题配对相减 → 这库到底值不值得建</span>
        <span class="spacer"></span>
        <button class="btn sm" id="upliftRunBtn">跑对照评测</button>
        <button class="btn sm ghost" id="upliftRefreshBtn">刷新</button>
      </div>
      <div class="muted small" id="upliftLine" style="margin-top:6px"></div>
      <div id="upliftBody" style="margin-top:12px"></div>
    </div>
    <div class="card" id="answerMatchCard">
      <div class="card-h"><h3>答案对照（L2）</h3>
        <span class="hint">焊死证据包，多个模型各整合答案 → 跟黄金答案比覆盖/准确/矛盾，出模型排行榜</span>
        <span class="spacer"></span>
        <button class="btn sm" id="amRunBtn">跑答案对照</button>
        <button class="btn sm ghost" id="amRefreshBtn">刷新</button>
      </div>
      <div class="muted small" style="margin-top:8px">参赛选手（勾选要跑哪些模型）：</div>
      <div id="amRoster" style="display:flex;flex-wrap:wrap;gap:12px;margin-top:6px"></div>
      <div class="muted small" id="amLine" style="margin-top:8px"></div>
      <div id="amBody" style="margin-top:12px"></div>
    </div>
    <div id="retCases"></div>`;
  loadCriteriaCard(); renderRetrievalCases(); loadProgress(true);
  const urb = $("#upliftRunBtn"); if (urb) urb.onclick = runUplift;
  const ufb = $("#upliftRefreshBtn"); if (ufb) ufb.onclick = loadUplift;
  loadUplift();
  const amr = $("#amRunBtn"); if (amr) amr.onclick = runAnswerMatch;
  const amf = $("#amRefreshBtn"); if (amf) amf.onclick = loadAnswerMatch;
  loadRoster(); loadAnswerMatch();
}

/* —— L4 增量价值：跑两路对照 + 渲染 —— */
const UPLIFT_BUCKETS = [
  ["exclusive", "不可替代", "ok"],
  ["boost", "锦上添花", "ok"],
  ["tie", "平局", "muted"],
  ["regression", "帮倒忙", "warn"],
  ["both_fail", "双输", "warn"],
];

async function loadUplift() {
  const body = $("#upliftBody"); if (!body || !state.suiteId) return;
  body.innerHTML = `<div class="muted small">加载中…</div>`;
  try {
    const r = await api(`/api/v1/suites/${state.suiteId}/uplift`);
    renderUplift(r.metrics || {});
  } catch (e) {
    body.innerHTML = `<div class="empty"><div class="h">还没有增量价值结果</div>
      <div class="muted">点「跑对照评测」让 Claude 分别闭卷裸答和用知识库各答一遍，再逐题对比。</div></div>`;
  }
}

/* L4 对照评测：SSE 订阅四阶段（闭卷作答/判分、用库作答/判分）逐题进度，
   进度行实时显示「在哪路·哪阶段、第几/共几题、题目」，done 时直接渲染报告。 */
function runUplift() {
  const btn = $("#upliftRunBtn"); if (!btn || !state.suiteId) return;
  const cases = (state.suite && state.suite.cases) || [];
  if (!cases.length) { toast("测试集为空，无题可跑", "err"); return; }
  const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spin"></span>跑批中`;
  const line = $("#upliftLine");
  const url = `/api/v1/suites/${state.suiteId}/uplift:run/stream`;

  let total = cases.length, errN = 0;
  const es = new EventSource(url);
  const finish = (msg, isErr) => {
    try { es.close(); } catch (e) {}
    btn.disabled = false; btn.innerHTML = old;
    if (msg) { if (line) line.textContent = msg; toast(msg, isErr ? "err" : "ok"); }
  };
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.event === "start") {
      total = d.total || total;
      if (line) line.innerHTML = `<span class="spin"></span> 准备跑两路对照（${total} 题 × 闭卷/用库 × 作答/判分）…`;
    } else if (d.event === "case_start") {
      const short = (d.question || "").slice(0, 28);
      if (line) line.innerHTML = `<span class="spin"></span> ${esc(d.label || "")}　第 ${d.i + 1}/${d.n} 题：${esc(short)}…` + (errN ? `　(失败 ${errN})` : "");
    } else if (d.event === "case_error") {
      errN++;
      console.warn("对照评测单题失败：", d.case_id, d.error);
    } else if (d.event === "done") {
      renderUplift(d.metrics || {});
      finish(`对照评测完成` + (errN ? `（${errN} 题失败）` : ""));
    } else if (d.event === "fatal") {
      finish("对照评测失败：" + (d.error || "未知错误"), true);
    }
  };
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) return;
    finish("对照评测连接中断：请确认 eval-api 已重启并在运行", true);
  };
}

function renderUplift(m) {
  const body = $("#upliftBody"); if (!body) return;
  if (!m || !m.n) { body.innerHTML = `<div class="muted small">尚无配对结果。</div>`; return; }
  const h = m.headline || {};
  const buckets = m.bucket_counts || {};
  const verdict = m.value_pass
    ? `<span class="badge ok">✅ 值得建</span>`
    : `<span class="badge warn">❌ 暂不达标</span>`;
  const signed = (v) => `${v >= 0 ? "+" : ""}${(v || 0).toFixed(3)}`;
  const tile = (n, l, accent) => `<div class="stat${accent ? " accent" : ""}"><div class="n">${n}</div><div class="l">${l}</div></div>`;

  const headline = `<div class="stats" style="margin-bottom:14px">
    ${tile(signed(h.net_uplift), "净增量", true)}
    ${tile(pct(h.win_rate), "胜率")}
    ${tile(pct(h.regression_rate), "负增量率")}
    ${tile(pct(h.exclusivity), "不可替代率")}
  </div>`;

  const bucketRow = `<div class="muted small" style="margin-bottom:6px">诊断桶分布 ${verdict}（配对 ${m.n} 题）</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px">
    ${UPLIFT_BUCKETS.map(([k, label, cls]) => `<span class="badge ${cls}">${label} ${buckets[k] || 0}</span>`).join("")}</div>`;

  const breakdown = (rows, title) => {
    if (!rows || !rows.length) return "";
    return `<div class="muted small" style="margin:10px 0 4px">${title}</div>
      <table class="tbl"><thead><tr><th>键</th><th class="r">题数</th><th class="r">平均增量</th></tr></thead><tbody>
      ${rows.map((b) => `<tr><td>${esc(b.key)}</td><td class="r">${b.count}</td><td class="r">${signed(b.mean_uplift)}</td></tr>`).join("")}
      </tbody></table>`;
  };

  const cases = (m.cases || []).slice();
  const bucketLabel = Object.fromEntries(UPLIFT_BUCKETS.map(([k, l]) => [k, l]));
  const caseTbl = cases.length ? `<div class="muted small" style="margin:14px 0 4px">逐题对照</div>
    <table class="tbl"><thead><tr><th>用例</th><th>题型</th><th>难度</th><th class="r">闭卷</th><th class="r">用库</th><th class="r">增量</th><th>诊断</th></tr></thead><tbody>
    ${cases.map((c) => `<tr>
      <td>${esc(c.case_id)}</td><td>${esc(c.question_type)}</td><td>${esc(c.difficulty)}</td>
      <td class="r">${(c.score_baseline || 0).toFixed(2)}</td><td class="r">${(c.score_treatment || 0).toFixed(2)}</td>
      <td class="r" style="color:${c.uplift >= 0 ? "var(--green)" : "var(--red)"}">${signed(c.uplift)}</td>
      <td>${bucketLabel[c.bucket] || c.bucket}</td></tr>`).join("")}
    </tbody></table>` : "";

  body.innerHTML = headline + bucketRow
    + breakdown(m.by_difficulty, "按难度分层增量")
    + breakdown(m.by_type, "按题型分层增量")
    + caseTbl;
}

/* —— L2 答案对照：花名册勾选 + 跑多模型 + 排行榜下钻 —— */
const amState = { roster: [] };

async function loadRoster() {
  const box = $("#amRoster"); if (!box) return;
  try {
    const r = await api(`/api/v1/eval-roster`);
    amState.roster = r.roster || [];
  } catch (e) { amState.roster = []; }
  if (!amState.roster.length) { box.innerHTML = `<span class="muted small">没读到花名册</span>`; return; }
  box.innerHTML = amState.roster.map((e) =>
    `<label class="muted small" style="display:inline-flex;align-items:center;gap:4px;cursor:pointer">
       <input type="checkbox" class="amPick" value="${esc(e.id)}" ${e.enabled ? "checked" : ""}>
       ${esc(e.label)} <span class="muted" style="opacity:.6">(${esc(e.channel)})</span>
     </label>`).join("");
}

function amSelectedIds() {
  return Array.from(document.querySelectorAll(".amPick:checked")).map((c) => c.value);
}

async function loadAnswerMatch() {
  const body = $("#amBody"); if (!body || !state.suiteId) return;
  body.innerHTML = `<div class="muted small">加载中…</div>`;
  try {
    const r = await api(`/api/v1/suites/${state.suiteId}/answer-match`);
    renderAnswerMatch(r.metrics || {});
  } catch (e) {
    body.innerHTML = `<div class="empty"><div class="h">还没有答案对照结果</div>
      <div class="muted">勾选要参赛的模型，点「跑答案对照」让它们各基于证据包答一版，再跟黄金答案逐题比。</div></div>`;
  }
}

/* L2 答案对照：SSE 订阅「每个模型一段作答/对照判」逐题进度，done 时渲染排行榜。 */
function runAnswerMatch() {
  const btn = $("#amRunBtn"); if (!btn || !state.suiteId) return;
  const cases = (state.suite && state.suite.cases) || [];
  if (!cases.length) { toast("测试集为空，无题可跑", "err"); return; }
  const ids = amSelectedIds();
  if (!ids.length) { toast("先勾选至少一个参赛模型", "err"); return; }
  const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spin"></span>跑批中`;
  const line = $("#amLine");
  const url = `/api/v1/suites/${state.suiteId}/answer-match:run/stream?models=${encodeURIComponent(ids.join(","))}`;

  let errN = 0;
  const es = new EventSource(url);
  const finish = (msg, isErr) => {
    try { es.close(); } catch (e) {}
    btn.disabled = false; btn.innerHTML = old;
    if (msg) { if (line) line.textContent = msg; toast(msg, isErr ? "err" : "ok"); }
  };
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.event === "start") {
      if (line) line.innerHTML = `<span class="spin"></span> 准备跑 ${(d.models || []).length} 个模型 × ${d.total} 题（作答 → 对照判）…`;
    } else if (d.event === "case_start") {
      const short = (d.question || "").slice(0, 28);
      if (line) line.innerHTML = `<span class="spin"></span> ${esc(d.label || "")}　第 ${d.i + 1}/${d.n} 题：${esc(short)}…` + (errN ? `　(失败 ${errN})` : "");
    } else if (d.event === "case_error") {
      errN++;
      console.warn("答案对照单题失败：", d.case_id, d.error);
    } else if (d.event === "done") {
      renderAnswerMatch(d.metrics || {});
      finish(`答案对照完成` + (errN ? `（${errN} 题失败）` : ""));
    } else if (d.event === "fatal") {
      finish("答案对照失败：" + (d.error || "未知错误"), true);
    }
  };
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) return;
    finish("答案对照连接中断：请确认 eval-api 已重启并在运行", true);
  };
}

function renderAnswerMatch(m) {
  const body = $("#amBody"); if (!body) return;
  const models = (m && m.models) || [];
  if (!models.length) { body.innerHTML = `<div class="muted small">尚无对照结果。</div>`; return; }
  const pctv = (v) => `${((v || 0) * 100).toFixed(1)}%`;

  const rows = models.map((mo, idx) => {
    const hard = mo.hard_errors
      ? `<span class="badge warn">${mo.hard_errors}</span>` : `<span class="muted">0</span>`;
    return `<tr class="amRow" data-i="${idx}" style="cursor:pointer">
      <td class="r">${idx + 1}</td>
      <td>${esc(mo.label)} <span class="muted" style="opacity:.6">${esc(mo.model || "")}</span></td>
      <td class="r"><b>${pctv(mo.f1)}</b></td>
      <td class="r">${pctv(mo.coverage)}</td>
      <td class="r">${pctv(mo.precision)}</td>
      <td class="r">${hard}</td>
      <td class="r">${mo.tokens || 0}</td>
      <td class="r">${((mo.elapsed_ms || 0) / 1000).toFixed(1)}s</td>
    </tr>
    <tr class="amDetail" data-i="${idx}" style="display:none"><td colspan="8">
      ${renderAmCases(mo.cases || [])}
    </td></tr>`;
  }).join("");

  body.innerHTML = `<div class="muted small" style="margin-bottom:6px">模型排行榜（按综合分 F1 降序，点行展开逐题）</div>
    <table class="tbl"><thead><tr>
      <th class="r">#</th><th>模型</th><th class="r">综合分</th><th class="r">覆盖度</th>
      <th class="r">准确度</th><th class="r">硬伤</th><th class="r">token</th><th class="r">时长</th>
    </tr></thead><tbody>${rows}</tbody></table>`;

  body.querySelectorAll(".amRow").forEach((tr) => {
    tr.onclick = () => {
      const det = body.querySelector(`.amDetail[data-i="${tr.dataset.i}"]`);
      if (det) det.style.display = det.style.display === "none" ? "" : "none";
    };
  });
}

function renderAmCases(cases) {
  if (!cases.length) return `<div class="muted small">无逐题明细。</div>`;
  const pctv = (v) => `${((v || 0) * 100).toFixed(0)}%`;
  const chips = (arr, cls) => (arr || []).length
    ? (arr || []).map((x) => `<span class="badge ${cls}" style="margin:1px">${esc(x)}</span>`).join("") : "";
  return cases.map((c) => `<div style="padding:8px 0;border-top:1px solid var(--line)">
    <div class="small"><b>${esc(c.question || c.case_id)}</b></div>
    <div class="muted small" style="margin:4px 0">答：${esc((c.answer || "").slice(0, 200))}</div>
    <div class="small">覆盖 ${pctv(c.coverage)}　准确 ${pctv(c.precision)}　综合 ${pctv(c.f1)}
      ${c.has_hard_error ? `<span class="badge warn">含矛盾硬伤</span>` : ""}</div>
    ${(c.missed_points || []).length ? `<div class="small" style="margin-top:3px">遗漏要点：${chips(c.missed_points, "warn")}</div>` : ""}
    ${(c.extra_claims || []).length ? `<div class="small" style="margin-top:3px">多余论断：${chips(c.extra_claims, "muted")}</div>` : ""}
    ${(c.contradictions || []).length ? `<div class="small" style="margin-top:3px">矛盾(硬伤)：${chips(c.contradictions, "warn")}</div>` : ""}
  </div>`).join("");
}

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
      <div class="card-h"><h3>导入测试集</h3><span class="hint">YAML / JSON 题目文件</span></div>
      <div class="muted small" style="margin-bottom:10px">已有题目文件可直接导入，跳过出题；导入时可命名以便区分。</div>
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

function renderTypeChecks() {
  const box = $("#typeChecks");
  if (box) box.innerHTML = TYPES.map((t) => `<label class="muted small" style="display:inline-flex;gap:5px;align-items:center"><input type="checkbox" class="typechk" value="${t}" checked style="width:auto" />${t}</label>`).join("");
}

async function loadDocs() {
  const list = $("#docList");
  try {
    const docs = await api(`/api/v1/projects/${state.projectId}/documents`);
    if (!docs.length) { list.innerHTML = '<div class="muted small">暂无文档</div>'; return; }
    list.innerHTML = docs.map((d) => `<label class="litem" style="cursor:default">
        <input type="checkbox" class="docchk" value="${d.document_id}" checked class="chk" style="width:auto" />
        <div class="grow"><div class="nm">${esc(d.name)}</div><div class="meta">${d.char_count} 字 · ${d.sections.length} 节</div></div>
      </label>`).join("");
  } catch (e) { list.innerHTML = `<div class="muted small">加载失败：${esc(e.message)}</div>`; }
}

function renderSuiteList() {
  const box = $("#suiteList");
  const list = regSuites(state.projectId);
  $("#suiteCount").textContent = list.length ? `${list.length} 个` : "";
  if (!list.length) { box.innerHTML = `<div class="muted small">还没有测试集。用上面的「AI 出题 / 导入 / 批量拉取」创建。</div>`; return; }
  box.innerHTML = list.map((s) => {
    const when = (s.created_at || "").replace("T", " ").slice(0, 16);
    const active = s.suite_id === state.suiteId ? " active" : "";
    return `<div class="litem${active}">
      <div class="grow" onclick="previewSuite('${s.suite_id}')">
        <div class="nm">${esc(s.label)} <span class="badge muted">${s.case_count} 题</span></div>
        <div class="meta">${s.suite_id} · 已标注 ${s.annotated || 0}/${s.case_count} · ${when}</div>
      </div>
      <button class="btn sm ghost" onclick="openSuiteWork('${s.suite_id}', 1)">查看/编辑</button>
      <button class="btn sm ghost" onclick="openSuiteWork('${s.suite_id}', 2)">取证评分</button>
      <button class="btn sm ghost danger" onclick="forgetSuite('${s.suite_id}')" title="从列表移除（不删服务端数据）">✕</button>
    </div>`;
  }).join("");
}
function forgetSuite(sid) { regRemove(state.projectId, sid); if (state.suiteId === sid) { state.suiteId = null; state.suite = null; } renderSuiteList(); $("#casePreview").innerHTML = ""; }

async function previewSuite(sid) {
  if (!(await selectSuite(sid))) return;
  renderSuiteList();
  const box = $("#casePreview");
  const s = state.suite;
  box.innerHTML = `<div class="card">
    <div class="card-h"><h3>用例预览 · ${esc(s.backend)}</h3><span class="hint">${s.cases.length} 题</span>
      <span class="spacer"></span>
      <button class="btn sm" onclick="flowGoStep(1)">去题库题目 →</button>
      <button class="btn sm" onclick="flowGoStep(2)">去取证 →</button>
    </div>
    ${s.cases.map((c) => `<div class="case">
      <div class="q-head"><span class="tag">${c.question_type}</span><span class="q">${esc(c.question)}</span>
        ${goldBadge(c)}</div>
      <details><summary>期望答案 / 评分要点 / 黄金证据 / 出处</summary>
        ${c.expected_answer ? `<div class="exp">${esc(c.expected_answer)}</div>` : ""}
        ${listBlock("评分要点", c.key_points)}${listBlock("黄金证据", c.expected_evidence)}${listBlock("黄金实体", c.expected_entities)}
        <div class="muted small">出处：${esc(c.source.doc)}${c.source.section ? " · " + esc(c.source.section) : ""}</div>
      </details>
    </div>`).join("")}
  </div>`;
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}
function listBlock(label, arr) {
  if (!arr || !arr.length) return "";
  return `<div class="muted small" style="margin-top:6px">${label}：</div><ul class="muted small" style="margin:4px 0">${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
}
function goldBadge(c) {
  const has = (c.expected_evidence || []).length > 0 || !!c.expected_answer;
  return `<span class="badge ${has ? "ok" : "warn"}">${has ? "已标注" : "待标注"}</span>`;
}

/* ----- 准备题目：动作（事件委托绑定在工作台①步内容渲染后） ----- */
async function uploadDoc() {
  const input = $("#docFile");
  if (!input.files.length) { toast("请选择文件", "err"); return; }
  const fd = new FormData(); fd.append("file", input.files[0]);
  try { await api(`/api/v1/projects/${state.projectId}/documents`, { method: "POST", body: fd }); input.value = ""; loadDocs(); toast("文档已解析"); }
  catch (e) { toast("上传失败：" + e.message, "err"); }
}
async function generateSuite() {
  const docIds = $$(".docchk:checked").map((c) => c.value);
  if (!docIds.length) { toast("请至少选择一个文档", "err"); return; }
  const types = $$(".typechk:checked").map((c) => c.value);
  const perType = Number($("#perType").value) || 1;
  const btn = $("#genBtn"); btn.disabled = true; const old = btn.innerHTML; btn.innerHTML = `<span class="spin"></span>出题中`;
  try {
    const suite = await api(`/api/v1/projects/${state.projectId}/suites:generate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ document_ids: docIds, types, per_type: perType }) });
    registerSuite(suite, "AI出题");
    await selectSuite(suite.suite_id); syncProjectUI();
    toast(`已生成 ${suite.cases.length} 道题（可到「题库题目」查看/编辑）`);
    if (state.page === "work") { state.flowStep = 1; state.flowStale = new Set(); await flowRefresh(true); }
    else { renderSuiteList(); previewSuite(suite.suite_id); }
  } catch (e) { toast("出题失败：" + e.message, "err"); }
  finally { btn.disabled = false; btn.innerHTML = old; }
}
async function importSuite() {
  const input = $("#suiteFile");
  if (!input.files.length) { toast("请选择 YAML/JSON 文件", "err"); return; }
  const file = input.files[0];
  const defaultName = (file.name || "测试集").replace(/\.[^.]+$/, "");
  const suiteName = (window.prompt("给这个测试集起个名字（便于区分）", defaultName) || "").trim();
  const fd = new FormData(); fd.append("file", file);
  if (suiteName) fd.append("name", suiteName);
  try {
    const suite = await api(`/api/v1/projects/${state.projectId}/suites:import`, { method: "POST", body: fd });
    input.value = ""; registerSuite(suite, suite.name || "导入");
    await selectSuite(suite.suite_id); syncProjectUI();
    toast(`已导入「${suite.name}」${suite.cases.length} 道题`);
    if (state.page === "work") { state.flowStep = 1; state.flowStale = new Set(); await flowRefresh(true); }
    else { renderSuiteList(); previewSuite(suite.suite_id); }
  } catch (e) { toast("导入失败：" + e.message, "err"); }
}
async function pullDraft() {
  const limit = Number($("#pullLimit").value) || 20;
  const domain = $("#pullDomain").value.trim() || null;
  const agent_name = $("#pullAgent").value.trim() || "serving";
  const btn = $("#pullBtn"); btn.disabled = true; const old = btn.innerHTML; btn.innerHTML = `<span class="spin"></span>拉取中`;
  try {
    const r = await api(`/api/v1/projects/${state.projectId}/retrieval/live:pull`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ limit, domain, agent_name }) });
    registerSuite(r.suite, "真实日志草稿");
    await selectSuite(r.suite.suite_id); syncProjectUI();
    toast(`已拉取 ${r.pulled_cases} 题 · 黄金库命中 ${r.gold_hits} · 待标注 ${r.pending_annotation}`);
    if (state.page === "work") { state.flowStep = 1; state.flowStale = new Set(); await flowRefresh(true); }
    else { renderSuiteList(); previewSuite(r.suite.suite_id); }
  } catch (e) { toast("拉取失败：" + e.message, "err"); }
  finally { btn.disabled = false; btn.innerHTML = old; }
}

function goldBody() {
  const s = state.suite;
  return `<div class="steps">
      <div class="step"><span class="num">1</span>AI 出题自带黄金</div>
      <div class="step"><span class="num">2</span>逐题复核 / 修正并保存</div>
      <div class="step"><span class="num">3</span>保存即入黄金库，下次自动回填</div>
    </div>
    <div class="row-actions" style="margin-bottom:14px">
      <button class="btn sm" id="goldSaveAllBtn" onclick="saveAllGold()">批量保存（确认全部黄金）</button>
      <span class="muted small">${s.cases.length} 题 · 只有「已确认」的黄金参与打分</span>
    </div>
    <div id="goldCases"></div>`;
}
function pickHint(msg) { return `<div class="card"><div class="empty"><div class="h">尚未选择测试集</div><div class="muted">${esc(msg)}</div></div></div>`; }

function renderGoldCases() {
  const box = $("#goldCases"); if (!box) return;
  box.innerHTML = state.suite.cases.map((c) => `<div class="case" data-cid="${c.id}">
    <div class="q-head"><span class="tag">${c.question_type}</span><span class="q">${esc(c.question)}</span>${goldBadge(c)}</div>
    <div class="field" style="margin-top:12px"><span class="lbl">期望答案</span><textarea class="an-answer">${esc(c.expected_answer)}</textarea></div>
    <div class="grid cols-2">
      <div class="field"><span class="lbl">黄金证据（每行一条，召回族指标基准）</span><textarea class="an-evidence">${esc((c.expected_evidence || []).join("\n"))}</textarea></div>
      <div class="field"><span class="lbl">黄金关键实体（每行一个）</span><textarea class="an-entities">${esc((c.expected_entities || []).join("\n"))}</textarea></div>
    </div>
    <div class="inline-fields">
      <div class="field"><span class="lbl">出处文档</span><input type="text" class="an-doc" value="${esc(c.source ? c.source.doc : "")}" style="width:160px" /></div>
      <div class="field"><span class="lbl">章节</span><input type="text" class="an-section" value="${esc(c.source && c.source.section ? c.source.section : "")}" style="width:140px" /></div>
      <div class="field"><span class="lbl">难度</span><select class="an-diff" style="width:110px">${DIFFS.map((d) => `<option value="${d}"${c.difficulty === d ? " selected" : ""}>${d}</option>`).join("")}</select></div>
      <div class="field"><span class="lbl">类型</span><select class="an-type" style="width:140px">${TYPES.map((t) => `<option value="${t}"${c.question_type === t ? " selected" : ""}>${t}</option>`).join("")}</select></div>
      <button class="btn sm" onclick="saveGold('${c.id}')">保存黄金</button>
    </div>
  </div>`).join("");
}

// 保存单题黄金：写库（默认 status=confirmed）+ 回填本地缓存 + 翻徽章为「已确认」。
// 失败时抛出，由调用方决定提示（逐题 toast / 批量汇总）。
async function saveGoldOne(cid) {
  const div = $(`.case[data-cid="${CSS.escape(cid)}"]`);
  if (!div) return false;
  const val = (sel) => (($(sel, div) || {}).value || "");
  const body = {
    expected_answer: val(".an-answer").trim(),
    expected_evidence: splitLines(val(".an-evidence")),
    expected_entities: splitLines(val(".an-entities")),
    source_doc: val(".an-doc").trim() || null,
    source_section: val(".an-section").trim() || null,
    difficulty: val(".an-diff"), question_type: val(".an-type"),
  };
  await api(`/api/v1/suites/${state.suiteId}/cases/${cid}/gold`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  // 更新本地 suite 缓存 + 徽章（保存即确认 → 显示「已确认」）
  const c = state.suite.cases.find((x) => x.id === cid);
  if (c) { c.expected_answer = body.expected_answer; c.expected_evidence = body.expected_evidence; c.expected_entities = body.expected_entities; c.question_type = body.question_type; c.difficulty = body.difficulty; }
  const badge = $(".q-head .badge", div);
  const has = body.expected_evidence.length || body.expected_answer;
  if (badge) { badge.className = "badge " + (has ? "ok" : "warn"); badge.textContent = has ? "已确认" : "待标注"; }
  return true;
}

async function saveGold(cid) {
  try {
    await saveGoldOne(cid);
    registerSuite(state.suite);
    toast("黄金已保存并确认");
    loadGoldProgress(); loadGoldLib();
  } catch (e) { toast("保存失败：" + e.message, "err"); }
}

// 批量保存：把当前所有题目的编辑结果逐条写库并确认。
async function saveAllGold() {
  const cases = (state.suite && state.suite.cases) || [];
  if (!cases.length) { toast("没有可保存的题目", "err"); return; }
  const btn = $("#goldSaveAllBtn"); let old = "";
  if (btn) { old = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spin"></span>保存中`; }
  let ok = 0, fail = 0;
  for (const c of cases) {
    try { await saveGoldOne(c.id); ok++; } catch (e) { fail++; }
  }
  registerSuite(state.suite);
  if (btn) { btn.disabled = false; btn.innerHTML = old; }
  toast(fail ? `已保存 ${ok} 题，${fail} 题失败` : `已保存并确认全部 ${ok} 题`, fail ? "err" : "ok");
  loadGoldProgress(); loadGoldLib();
}
async function loadGoldProgress() {
  if (!state.suiteId) return;
  try {
    const p = await api(`/api/v1/suites/${state.suiteId}/gold/progress`);
    const el = $("#goldProg"); if (el) el.textContent = `标注进度 ${p.annotated}/${p.total} · 待标注 ${p.pending}`;
  } catch (e) {}
}
function goldStatusBadge(s) {
  return s === "confirmed"
    ? `<span class="badge ok">已确认</span>`
    : `<span class="badge warn">草稿</span>`;
}
function goldSourceLabel(k) {
  return ({ manual: "人工", llm_generated: "AI 生成", imported: "导入" })[k] || k || "—";
}

async function loadGoldLib() {
  const box = $("#goldLib"); if (!box) return;
  const filter = ($("#goldFilter") || {}).value || "";
  box.innerHTML = `<div class="muted small"><span class="spin"></span> 加载中</div>`;
  try {
    const qs = filter ? `?status=${filter}` : "";
    const g = await api(`/api/v1/projects/${state.projectId}/gold${qs}`);
    if (!g.records.length) { box.innerHTML = `<div class="muted small">${filter ? "该状态下暂无条目。" : "黄金库为空。AI 出题或人工标注后会自动入库。"}</div>`; return; }
    box.innerHTML = `<table class="tbl"><thead><tr><th>问题</th><th>类型</th><th>证据/实体</th><th>来源</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>` +
      g.records.map((r) => `<tr data-fp="${r.fingerprint}">
        <td>${esc(r.question)}</td><td><span class="tag">${r.question_type}</span></td>
        <td class="num">${(r.expected_evidence || []).length} / ${(r.expected_entities || []).length}</td>
        <td class="small muted">${goldSourceLabel(r.source_kind)}</td>
        <td>${goldStatusBadge(r.status)}</td>
        <td class="small muted">${(r.updated_at || "").replace("T", " ").slice(0, 16)}</td>
        <td class="row-actions">
          ${r.status === "draft" ? `<button class="btn xs" onclick="confirmGold('${r.fingerprint}')">确认</button>` : ""}
          <button class="btn xs ghost" onclick="editGold('${r.fingerprint}')">编辑</button>
          <button class="btn xs ghost danger" onclick="deleteGold('${r.fingerprint}')">删除</button>
        </td></tr>
        <tr class="gold-edit-row" data-edit="${r.fingerprint}" style="display:none"><td colspan="7"></td></tr>`).join("") +
      `</tbody></table><div class="muted small" style="margin-top:8px">共 ${g.records.length} 条 · 仅「已确认」参与打分</div>`;
    // 缓存当前列表，供编辑面板回填
    state._goldCache = Object.fromEntries(g.records.map((r) => [r.fingerprint, r]));
  } catch (e) { box.innerHTML = `<div class="muted small">加载失败：${esc(e.message)}</div>`; }
}

function goldEditFormHtml(r, { isNew = false } = {}) {
  const v = r || {};
  return `<div class="gold-form">
    ${isNew ? `<div class="field"><span class="lbl">问题（必填，指纹由此计算）</span><textarea class="gf-question">${esc(v.question || "")}</textarea></div>` : ""}
    <div class="field"><span class="lbl">期望答案</span><textarea class="gf-answer">${esc(v.expected_answer || "")}</textarea></div>
    <div class="grid cols-2">
      <div class="field"><span class="lbl">黄金证据（每行一条）</span><textarea class="gf-evidence">${esc((v.expected_evidence || []).join("\n"))}</textarea></div>
      <div class="field"><span class="lbl">黄金关键实体（每行一个）</span><textarea class="gf-entities">${esc((v.expected_entities || []).join("\n"))}</textarea></div>
    </div>
    <div class="inline-fields">
      <div class="field"><span class="lbl">出处文档</span><input type="text" class="gf-doc" value="${esc(v.source ? v.source.doc : "")}" style="width:150px" /></div>
      <div class="field"><span class="lbl">章节</span><input type="text" class="gf-section" value="${esc(v.source && v.source.section ? v.source.section : "")}" style="width:130px" /></div>
      <div class="field"><span class="lbl">难度</span><select class="gf-diff" style="width:100px">${DIFFS.map((d) => `<option value="${d}"${(v.difficulty || "normal") === d ? " selected" : ""}>${d}</option>`).join("")}</select></div>
      <div class="field"><span class="lbl">类型</span><select class="gf-type" style="width:130px">${TYPES.map((t) => `<option value="${t}"${(v.question_type || "factoid") === t ? " selected" : ""}>${t}</option>`).join("")}</select></div>
    </div>
    <div class="row-actions" style="margin-top:8px">
      <button class="btn sm" id="gfSaveBtn">${isNew ? "新建并入库（默认已确认）" : "保存修改"}</button>
      <button class="btn sm ghost" id="gfCancelBtn">取消</button>
    </div>
  </div>`;
}
function readGoldForm(scope) {
  const val = (sel) => (($(sel, scope) || {}).value || "");
  return {
    expected_answer: val(".gf-answer").trim(),
    expected_evidence: splitLines(val(".gf-evidence")),
    expected_entities: splitLines(val(".gf-entities")),
    source_doc: val(".gf-doc").trim() || null,
    source_section: val(".gf-section").trim() || null,
    difficulty: val(".gf-diff"), question_type: val(".gf-type"),
  };
}

function toggleGoldNewForm() {
  const box = $("#goldNewForm"); if (!box) return;
  if (box.style.display === "block") { box.style.display = "none"; box.innerHTML = ""; return; }
  box.style.display = "block";
  box.innerHTML = goldEditFormHtml({}, { isNew: true });
  $("#gfCancelBtn").onclick = toggleGoldNewForm;
  $("#gfSaveBtn").onclick = createGold;
}
async function createGold() {
  const scope = $("#goldNewForm");
  const question = (($(".gf-question", scope) || {}).value || "").trim();
  if (!question) { toast("请填写问题", "err"); return; }
  const body = { question, ...readGoldForm(scope) };
  try {
    await api(`/api/v1/projects/${state.projectId}/gold`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast("已新建并入库（已确认）"); toggleGoldNewForm(); loadGoldLib();
  } catch (e) { toast("新建失败：" + e.message, "err"); }
}

function editGold(fp) {
  const row = $(`tr.gold-edit-row[data-edit="${CSS.escape(fp)}"]`); if (!row) return;
  const cell = row.firstElementChild;
  if (row.style.display === "table-row") { row.style.display = "none"; cell.innerHTML = ""; return; }
  // 关闭其它已展开的编辑行
  document.querySelectorAll("tr.gold-edit-row").forEach((r) => { r.style.display = "none"; r.firstElementChild.innerHTML = ""; });
  const rec = (state._goldCache || {})[fp] || {};
  row.style.display = "table-row";
  cell.innerHTML = goldEditFormHtml(rec);
  $("#gfCancelBtn", cell).onclick = () => editGold(fp);
  $("#gfSaveBtn", cell).onclick = () => saveGoldEdit(fp, cell);
}
async function saveGoldEdit(fp, scope) {
  try {
    await api(`/api/v1/gold/${fp}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(readGoldForm(scope)) });
    toast("已保存（人工修改）"); loadGoldLib();
  } catch (e) { toast("保存失败：" + e.message, "err"); }
}
async function confirmGold(fp) {
  try {
    await api(`/api/v1/gold/${fp}:confirm`, { method: "POST" });
    toast("已确认，可参与打分"); loadGoldLib();
  } catch (e) { toast("确认失败：" + e.message, "err"); }
}
async function deleteGold(fp) {
  if (!confirm("确定删除这条黄金？删除后不可恢复。")) return;
  try {
    await api(`/api/v1/gold/${fp}`, { method: "DELETE" });
    toast("已删除"); loadGoldLib();
  } catch (e) { toast("删除失败：" + e.message, "err"); }
}
async function confirmSuiteGold() {
  if (!state.suiteId) return;
  try {
    const r = await api(`/api/v1/suites/${state.suiteId}/gold:confirm-all`, { method: "POST" });
    toast(`已确认 ${r.confirmed} 条草稿（共 ${r.total} 题）`); loadGoldLib(); loadGoldProgress();
    if (state.page === "work") flowRefresh(true);
  } catch (e) { toast("批量确认失败：" + e.message, "err"); }
}

/* ============================================================================
   页面 4 · 检索评估与报告
   ========================================================================== */
function renderRetrievalCases() {
  const box = $("#retCases"); if (!box) return;
  box.innerHTML = state.suite.cases.map((c) => `<div class="case" data-cid="${c.id}">
    <div class="q-head"><span class="tag">${c.question_type}</span><span class="q">${esc(c.question)}</span>${goldBadge(c)}</div>
    <div class="row-actions" style="margin-top:12px">
      <button class="btn sm ghost btn-pull" onclick="pullCase('${c.id}')">检索数据库</button>
      <button class="btn sm ghost btn-judge" onclick="judgeCase('${c.id}')" disabled>评估</button>
      <span class="casestatus"></span>
    </div>
    <div class="evidence"></div><div class="result"></div>
  </div>`).join("");
}
const caseDiv = (cid) => $(`.case[data-cid="${CSS.escape(cid)}"]`);
const retName = () => ($("#retName") || {}).value || "serving";
const retDomain = () => (($("#retDomain") || {}).value || "").trim() || null;
function setStatus(div, text, done) { const s = $(".casestatus", div); if (s) { s.textContent = text; s.style.color = done ? "var(--green)" : "var(--muted)"; } }

function renderEvidence(div, items) {
  const box = $(".evidence", div);
  if (!items || !items.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="muted small" style="margin:8px 0 2px">检索结果（${items.length} 条，按排名）：</div>` +
    items.map((it) => `<div class="ev"><span class="erank">#${it.rank}</span>${esc(it.text)}${it.source ? `<div class="src">出处：${esc(it.source)}</div>` : ""}</div>`).join("");
}
function renderCaseResult(div, res) {
  const box = $(".result", div); if (!res) { box.innerHTML = ""; return; }
  const evs = $$(".evidence .ev", div);
  (res.item_grades || []).forEach((g, i) => {
    if (evs[i]) { const old = $(".gr", evs[i]); if (old) old.remove(); const span = document.createElement("span"); span.className = `gr gr${g}`; span.textContent = `相关度 ${g}`; evs[i].insertBefore(span, evs[i].firstChild); }
  });
  const goldLine = res.gold_count > 0 ? `命中黄金 ${res.covered_count}/${res.gold_count} · ` : "无黄金（仅精确率族） · ";
  box.innerHTML = `<div class="res"><b>评估：</b>相关条目 ${res.relevant_count}/${res.retrieved_count} · ${goldLine}判定 token ${res.judge_tokens}` +
    (res.rationale ? `<div class="muted small" style="margin-top:5px">${esc(res.rationale)}</div>` : "") + `</div>`;
}
function renderCandidates(div, cands, cid) {
  const box = $(".evidence", div); $(".result", div).innerHTML = "";
  if (!cands || !cands.length) { box.innerHTML = '<div class="muted small" style="margin-top:8px">查询日志中无匹配或相似问题，该问题可能尚未被检索过。</div>'; return; }
  box.innerHTML = '<div class="muted small" style="margin:8px 0 2px">未精确匹配，以下为相似问题，确认后再拉取：</div>' +
    cands.map((c) => `<div class="ev"><span class="gr gr2">相似 ${((c.similarity || 0) * 100).toFixed(0)}%</span>${esc(c.query_text)}
      <div class="src">${esc(c.domain || "")}${c.queried_at ? " · " + esc(c.queried_at) : ""}</div>
      <button class="btn sm ghost" style="margin-top:6px" onclick="pickCandidate('${jsq(cid)}','${jsq(c.query_id)}')">确认拉取</button></div>`).join("");
}
function jsq(s) { return String(s == null ? "" : s).replace(/\\/g, "\\\\").replace(/'/g, "\\'"); }

async function pullInto(cid, body) {
  const div = caseDiv(cid); const btn = $(".btn-pull", div); btn.disabled = true; setStatus(div, "拉取中…");
  try {
    const r = await api(`/api/v1/suites/${state.suiteId}/cases/${cid}/retrieval:pull`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.matched === false) { renderCandidates(div, r.candidates, cid); $(".btn-judge", div).disabled = true; const n = (r.candidates || []).length; setStatus(div, n ? `未精确匹配，请在下方确认（${n} 候选）` : "未找到匹配问题"); return 0; }
    renderEvidence(div, r.items); $(".result", div).innerHTML = ""; $(".btn-judge", div).disabled = r.items.length === 0;
    setStatus(div, `已拉取 ${r.items.length} 条${body.query_id ? "（已确认）" : ""}${r.queried_at ? " · " + r.queried_at : ""}`); return r.items.length;
  } catch (e) { setStatus(div, "拉取失败：" + e.message); toast("拉取失败：" + e.message, "err"); return 0; }
  finally { btn.disabled = false; }
}
function pullCase(cid) { return pullInto(cid, { domain: retDomain(), agent_name: retName() }); }
function pickCandidate(cid, qid) { return pullInto(cid, { agent_name: retName(), query_id: qid }); }
async function judgeCase(cid) {
  const div = caseDiv(cid); const btn = $(".btn-judge", div); btn.disabled = true; setStatus(div, "评估中…");
  try {
    const r = await api(`/api/v1/suites/${state.suiteId}/cases/${cid}/retrieval:judge`, { method: "POST", headers: { "Content-Type": "application/json" } });
    renderCaseResult(div, r.result); setStatus(div, "已评估", true); await loadProgress(true); return true;
  } catch (e) { setStatus(div, "评估失败：" + e.message); toast("评估失败：" + e.message, "err"); return false; }
  finally { btn.disabled = false; }
}
async function pullAllCases() {
  const ids = state.suite.cases.map((c) => c.id); toast("逐题拉取中…"); let ok = 0;
  for (const cid of ids) { if (await pullCase(cid)) ok++; } toast(`已拉取 ${ok}/${ids.length} 题`);
  if (state.page === "work") { flowMarkStale(2); state.flowStale.delete(2); flowRefresh(true); }
}
/* serving 批量取证：SSE（EventSource）实时进度，复用 agent 跑批的事件组件。
   命中→落库并渲染证据；未命中→标“库里没查到”，仍可单题去找相似候选。 */
function pullAllCasesStream() {
  const btn = $("#pullAllBtn"); if (!btn) return;
  const cases = (state.suite && state.suite.cases) || [];
  if (!cases.length) { toast("测试集为空，无题可取证", "err"); return; }
  btn.disabled = true; const old = btn.innerHTML; btn.innerHTML = `<span class="spin"></span>取证中`;
  const line = $("#progressLine");
  const qs = new URLSearchParams();
  const dom = retDomain(); if (dom) qs.set("domain", dom);
  qs.set("agent_name", retName());
  const url = `/api/v1/suites/${state.suiteId}/retrieval:pull/stream?${qs.toString()}`;

  let okN = 0, missN = 0, errN = 0;
  const es = new EventSource(url);
  const finish = (msg, isErr) => {
    try { es.close(); } catch (e) {}
    btn.disabled = false; btn.innerHTML = old;
    if (msg) { if (line) line.textContent = msg; toast(msg, isErr ? "err" : "ok"); }
    loadProgress(true);
    if (state.page === "work") { flowMarkStale(2); state.flowStale.delete(2); flowRefresh(true); }
  };
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.event === "start") {
      if (line) line.innerHTML = `<span class="spin"></span> 准备从知识库取 ${d.todo} 题证据…`;
    } else if (d.event === "case_start") {
      const short = (d.question || "").slice(0, 28);
      if (line) line.innerHTML = `<span class="spin"></span> 第 ${d.i + 1}/${d.n} 题：${esc(short)}…　(命中 ${okN} · 未命中 ${missN})`;
      const div = caseDiv(d.case_id); if (div) setStatus(div, "取证中…");
    } else if (d.event === "case_done") {
      const div = caseDiv(d.case_id);
      if (d.matched) {
        okN++;
        if (div) {
          renderEvidence(div, d.items || []);
          const jb = $(".btn-judge", div); if (jb) jb.disabled = (d.retrieved_count || 0) === 0;
          setStatus(div, `已取证 ${d.retrieved_count || 0} 条`, true);
        }
      } else {
        missN++;
        if (div) setStatus(div, "库里没查到（可单题找相似候选）");
      }
    } else if (d.event === "case_error") {
      errN++;
      const div = caseDiv(d.case_id); if (div) setStatus(div, "失败：" + d.error);
      console.warn("批量取证失败：", d.case_id, d.error);
    } else if (d.event === "done") {
      finish(`取证完成：命中 ${okN} 题 · 未命中 ${missN} 题` + (errN ? ` · ${errN} 题失败` : ""));
    }
  };
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) return;
    finish("取证连接中断：请确认 eval-api 已重启并在运行", true);
  };
}
async function judgeAllCases() {
  const divs = $$(".case").filter((d) => !$(".btn-judge", d).disabled || $(".evidence .ev", d)); toast("逐题评估中…"); let ok = 0;
  for (const d of divs) { if (await judgeCase(d.dataset.cid)) ok++; } toast(`已评估 ${ok}/${divs.length} 题`);
}
async function loadProgress(silent) {
  try {
    const p = await api(`/api/v1/suites/${state.suiteId}/retrieval/progress`);
    const pl = $("#progressLine"); if (pl) pl.textContent = `进度：已拉取 ${p.pulled}/${p.total} · 已评估 ${p.judged}/${p.total}` + (p.all_judged ? " · 全部完成，可生成报告" : "");
    const rb = $("#reportBtn"); if (rb) rb.disabled = !p.all_judged;
    p.cases.forEach((c) => {
      const div = caseDiv(c.case_id); if (!div) return;
      if (c.pulled) { renderEvidence(div, c.items); $(".btn-judge", div).disabled = c.pulled_count === 0; }
      if (c.judged && c.result) { renderCaseResult(div, c.result); setStatus(div, "已评估", true); }
      else if (c.pulled) setStatus(div, `已拉取 ${c.pulled_count} 条`);
    });
  } catch (e) { if (!silent) toast("加载进度失败：" + e.message, "err"); }
}
async function runFinalReport() {
  const btn = $("#reportBtn"); btn.disabled = true; const old = btn.innerHTML; btn.innerHTML = `<span class="spin"></span>生成中`;
  try {
    const r = await api(`/api/v1/suites/${state.suiteId}/retrieval:report`, { method: "POST", headers: { "Content-Type": "application/json" } });
    saveRunSum(state.suiteId, "perq", r.metrics);
    renderRetSummary(r.metrics);
    const link = $("#retReport"); link.style.display = "inline-flex"; link.href = `/api/v1/retrieval-runs/${r.run_id}/report?format=html`;
    toast("最终报告已生成");
    if (state.page === "work") flowRefresh(true);
  } catch (e) { toast("生成报告失败：" + e.message, "err"); }
  finally { btn.disabled = false; btn.innerHTML = old; }
}
/* Claude 自动跑批：SSE（EventSource）订阅后端逐题进度，边跑边实时刷新。
   「仅补跑」由后端判定已检索/已回答的题并跳过，前端只负责渲染事件。 */
function runAgentSuite() {
  const btn = $("#agentRunBtn"); if (!btn) return;
  const name = (($("#agentName") || {}).value || "claude-agent").trim() || "claude-agent";
  const onlyMissing = !!($("#agentOnlyMissing") || {}).checked;
  const cases = (state.suite && state.suite.cases) || [];
  if (!cases.length) { toast("测试集为空，无题可跑", "err"); return; }

  const tip = onlyMissing ? "（已检索/已回答的题会自动跳过）" : "";
  if (!confirm(`将对测试集逐题调用 claude 自动检索+作答${tip}，可能耗时较长。确认开始？`)) return;

  btn.disabled = true; const old = btn.innerHTML; btn.innerHTML = `<span class="spin"></span>跑批中`;
  const line = $("#agentLine");
  const qs = new URLSearchParams({ agent_name: name, only_missing: onlyMissing ? "true" : "false" });
  const url = `/api/v1/suites/${state.suiteId}/agent:run/stream?${qs.toString()}`;

  let okN = 0, retN = 0, tokN = 0, errN = 0, skipped = 0;
  const es = new EventSource(url);
  const finish = (msg, isErr) => {
    try { es.close(); } catch (e) {}
    btn.disabled = false; btn.innerHTML = old;
    if (msg) { if (line) line.textContent = msg; toast(msg, isErr ? "err" : "ok"); }
    loadProgress();
  };
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.event === "start") {
      skipped = d.skipped || 0;
      if (!d.todo) { finish(`没有需要补跑的题（${skipped} 道已检索/已回答）`); return; }
      if (line) line.innerHTML = `<span class="spin"></span> 准备跑 ${d.todo} 题${skipped ? `（已跳过 ${skipped} 道已完成）` : ""}…`;
    } else if (d.event === "case_start") {
      const short = (d.question || "").slice(0, 28);
      if (line) line.innerHTML = `<span class="spin"></span> 第 ${d.i + 1}/${d.n} 题：${esc(short)}…　(已完成 ${okN} · 失败 ${errN}${skipped ? " · 跳过 " + skipped : ""})`;
      const div = caseDiv(d.case_id); if (div) setStatus(div, "Claude 检索作答中…");
    } else if (d.event === "case_done") {
      if (d.answered) okN++;
      tokN += d.tokens || 0;
      if (d.retrieved_count) retN++;
      const div = caseDiv(d.case_id);
      if (div) {
        renderEvidence(div, d.items || []);
        const jb = $(".btn-judge", div); if (jb) jb.disabled = (d.retrieved_count || 0) === 0;
        setStatus(div, `已作答 · 检索 ${d.retrieved_count || 0} 条`, true);
      }
    } else if (d.event === "case_error") {
      errN++;
      const div = caseDiv(d.case_id); if (div) setStatus(div, "失败：" + d.error);
      console.warn("agent 跑批失败：", d.case_id, d.error);
    } else if (d.event === "done") {
      finish(`完成：作答 ${okN}/${d.todo} 题 · 带回检索 ${retN} 题 · token ${tokN}` +
        (errN ? ` · ${errN} 题失败` : "") + (skipped ? ` · 跳过 ${skipped} 道已完成` : ""));
    }
  };
  es.onerror = () => {
    // 服务端正常关流后 EventSource 也会触发 error；done 处理里已 close，readyState=CLOSED 即正常结束。
    if (es.readyState === EventSource.CLOSED) return;
    finish("跑批连接中断：请确认 eval-api 已重启并在运行", true);
  };
}
/* 评估标准快照：出报告前把 K 值 / 通过线 / 权重 / 难度系数摊开给人看，
   做到“按什么标准打分”透明可追溯（数据来自后端 /eval-criteria，即 config）。 */
async function loadCriteriaCard() {
  const card = $("#criteriaCard"); if (!card) return;
  try {
    const c = await api("/api/v1/eval-criteria");
    const w = c.weights || {}, p = c.pass || {}, dw = c.difficulty_weights || {};
    const pctn = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
    const chip = (l, v) => `<span class="crit-chip"><b>${esc(v)}</b><span>${esc(l)}</span></span>`;
    card.innerHTML = `
      <div class="card-h"><h3>评估标准</h3><span class="hint">本次按这套标准打分（出报告前先过目）</span></div>
      <div class="crit-row">
        <div class="crit-grp"><div class="crit-t">检索深度 K</div><div class="crit-chips">
          ${chip("评分基准 K", c.score_k)}${(c.k_values || []).map((k) => chip("K", k)).join("")}</div></div>
        <div class="crit-grp"><div class="crit-t">综合分权重</div><div class="crit-chips">
          ${chip("找到", pctn(w.find))}${chip("排名", pctn(w.rank))}${chip("质量", pctn(w.quality))}</div></div>
        <div class="crit-grp"><div class="crit-t">单题通过线</div><div class="crit-chips">
          ${chip("召回≥", pctn(p.recall))}${chip("命中排名≤", p.rank)}${chip("上下文召回≥", pctn(p.context_recall))}</div></div>
        <div class="crit-grp"><div class="crit-t">难度加权</div><div class="crit-chips">
          ${Object.entries(dw).map(([k, v]) => chip(k, "×" + v)).join("")}</div></div>
      </div>`;
  } catch (e) {
    card.innerHTML = `<div class="card-h"><h3>评估标准</h3><span class="hint" style="color:var(--red)">加载失败：${esc(e.message)}</span></div>`;
  }
}
function renderRetSummary(m) {
  const el = $("#retSummary"); if (!el) return;
  if (!m) { el.innerHTML = ""; return; }
  const k = String((m.k_values || []).slice(-1)[0] || 10), top = (m.k_values || []).slice(-1)[0] || 10;
  const hasGold = m.has_gold !== false;
  const tile = (n, l, accent) => `<div class="stat${accent ? " accent" : ""}"><div class="n">${n}</div><div class="l">${l}</div></div>`;
  el.innerHTML =
    tile(hasGold ? pct(m.context_recall) : "N/A", "Context Recall") +
    tile(pct(m.ndcg[k]), `NDCG@${top}`, true) +
    tile(hasGold ? pct(m.recall[k]) : "N/A", `Recall@${top}`) +
    tile(pct(m.hit_rate[k]), `HitRate@${top}`) +
    tile(pct(m.mrr[k]), `MRR@${top}`) +
    tile(`${m.judged_cases}/${m.total_cases}`, "已判 / 总数");
}

/* ============================================================================
   页面 5 · 对话试问
   ========================================================================== */
function renderChat() {
  const v = $("#view"); v.className = "view";
  // 用整页高度的对话布局，覆盖 view 的内边距
  v.removeAttribute("style");
  v.innerHTML = `<div class="chat-wrap" style="margin:-26px -28px -60px">
    <div class="chat-scroll" id="chatScroll"><div class="chat-thread" id="chatThread"></div></div>
    <div class="chat-input">
      <div class="box">
        <textarea id="chatBox" placeholder="输入问题，预览知识库会检索到哪些片段…（Enter 发送，Shift+Enter 换行）"></textarea>
        <button class="btn" id="chatSend">发送</button>
      </div>
      <div class="chat-opts"><span>domain（可空）</span><input type="text" id="chatDomain" placeholder="全部" /><span class="faint">· 数据来源：serving 查询日志</span></div>
    </div>
  </div>`;
  const box = $("#chatBox"), send = $("#chatSend");
  send.onclick = sendChat;
  box.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } };
  renderChatThread();
  box.focus();
}
function renderChatThread() {
  const t = $("#chatThread"); if (!t) return;
  if (!state.chat.length) {
    t.innerHTML = `<div class="empty"><div class="big">💬</div><div class="h">向知识库试问</div>
      <div class="muted">输入一个问题，看看知识库目前会检索到哪些片段——用来快速体感检索质量。</div></div>`;
    return;
  }
  t.innerHTML = state.chat.map(renderBubble).join("");
  const sc = $("#chatScroll"); if (sc) sc.scrollTop = sc.scrollHeight;
}
function renderBubble(m) {
  if (m.role === "me") return `<div class="bubble me"><div class="av">我</div><div class="body"><div class="text">${esc(m.text)}</div></div></div>`;
  // kb
  let inner = "";
  if (m.loading) inner = `<div class="text"><span class="dots">检索中</span></div>`;
  else if (m.error) inner = `<div class="text" style="color:var(--red)">${esc(m.error)}</div>`;
  else if (m.matched === false) {
    const cands = (m.candidates || []);
    inner = `<div class="text">未精确匹配到这个问题。${cands.length ? "下面是相似的历史问题，点选一个查看它的检索结果：" : "查询日志里没有相似问题，可能还没被检索过。"}</div>` +
      (cands.length ? `<div class="kb-items">${cands.map((c, i) => `<div class="ev"><span class="gr gr2">相似 ${((c.similarity || 0) * 100).toFixed(0)}%</span>${esc(c.query_text)}
        <div class="src">${esc(c.domain || "")}${c.queried_at ? " · " + esc(c.queried_at) : ""}</div>
        <button class="btn sm ghost" style="margin-top:6px" onclick="pickChat('${jsq(c.query_id)}','${jsq(c.query_text)}')">查看这条</button></div>`).join("")}</div>` : "");
  } else {
    const items = m.items || [];
    inner = `<div class="text">命中：<b>${esc(m.matched_question || "")}</b>，检索到 ${items.length} 条片段：</div>` +
      `<div class="kb-items">${items.map((it) => `<div class="ev"><span class="erank">#${it.rank}</span>${esc(it.text)}${it.source ? `<div class="src">出处：${esc(it.source)}</div>` : ""}</div>`).join("")}</div>` +
      `<div class="meta">${m.queried_at ? "检索于 " + esc(m.queried_at) : ""}${m.duration_ms != null ? " · 耗时 " + m.duration_ms + "ms" : ""}${m.source_domain ? " · " + esc(m.source_domain) : ""}</div>`;
  }
  return `<div class="bubble kb"><div class="av">库</div><div class="body">${inner}</div></div>`;
}
async function sendChat() {
  const box = $("#chatBox"); const q = box.value.trim();
  if (!q) return;
  box.value = "";
  state.chat.push({ role: "me", text: q });
  const kb = { role: "kb", loading: true }; state.chat.push(kb);
  renderChatThread();
  await doChat({ question: q, domain: ($("#chatDomain").value || "").trim() || null }, kb);
}
async function pickChat(qid, qtext) {
  state.chat.push({ role: "me", text: qtext + "（确认）" });
  const kb = { role: "kb", loading: true }; state.chat.push(kb);
  renderChatThread();
  await doChat({ query_id: qid }, kb);
}
async function doChat(body, kb) {
  try {
    const r = await api("/api/v1/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    Object.assign(kb, { loading: false }, r);
  } catch (e) { kb.loading = false; kb.error = "检索失败：" + e.message; }
  renderChatThread();
}

/* ============================================================================
   事件委托：用一处统一绑定动态生成的按钮（避免内联 onclick 找不到函数）
   —— 大部分按钮已用 onclick 指向全局函数，这里补 id 型按钮的绑定
   ========================================================================== */
document.addEventListener("click", (e) => {
  const id = e.target.id || (e.target.closest && e.target.closest("button") && e.target.closest("button").id);
  const map = {
    uploadBtn: uploadDoc, genBtn: generateSuite, importBtn: importSuite, pullBtn: pullDraft,
    pullAllBtn: pullAllCasesStream, judgeAllBtn: judgeAllCases, reportBtn: runFinalReport,
    agentRunBtn: runAgentSuite,
  };
  if (id && map[id]) { map[id](); }
});

/* ----------------------------- 启动 ----------------------------- */
$("#projSelect").addEventListener("change", (e) => onProjectPick(e.target.value));
$$("#nav .navitem").forEach((n) => (n.onclick = () => go(n.dataset.page)));

(async function boot() {
  await loadProjects();
  const page = (location.hash || "#/dashboard").replace("#/", "");
  setPage(page);  // setPage 内部处理旧 hash 别名（flow/suites/gold/reports/retrieval→work）+ 未知回落 dashboard
})();
