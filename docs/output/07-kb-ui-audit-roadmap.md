# kb-ui 审查与演进规划

> 审查日期：2026-06-01

## 1. 目录结构

```
kb-ui/
├── src/
│   ├── views/                    # 页面组件（12个）
│   │   ├── DashboardView.vue    # 仪表盘
│   │   ├── SearchView.vue       # 知识检索
│   │   ├── SettingsView.vue     # 系统设置
│   │   ├── LlmView.vue          # LLM 任务列表
│   │   ├── mining/              # 挖掘管理
│   │   │   ├── RunsView.vue
│   │   │   ├── CreateRunView.vue
│   │   │   ├── RunDetailView.vue
│   │   │   └── RunDocumentDetailView.vue
│   │   ├── knowledge/           # 知识库
│   │   │   ├── DocumentsView.vue
│   │   │   ├── DocumentDetailView.vue
│   │   │   └── GraphView.vue
│   │   └── llm/
│   │       └── LlmTaskDetailView.vue
│   ├── components/              # 通用组件（19个）
│   │   ├── charts/              # 图表（BarChart, LineChart, PieChart, ForceGraph）
│   │   ├── common/              # 通用（EmptyState, ServiceHealthCard, StatsCard, StatusBadge）
│   │   ├── layout/              # 布局（AppLayout, Header, Sidebar）
│   │   ├── mining/              # 挖掘（PipelineFlow）
│   │   ├── search/              # 搜索（EvidenceCard, PipelineTrace）
│   │   └── settings/            # 设置（SystemConfigTab, DomainManageTab, DomainDetailTab）
│   └── api/                     # API 层
│       ├── controlPlane.ts
│       ├── mining.ts
│       ├── serving.ts
│       └── llm.ts
```

## 2. 页面路由（12个）

| 路由 | 页面 | 功能 | 状态 |
|------|------|------|------|
| `/` | DashboardView | 仪表盘 | ✅ |
| `/mining` | RunsView | 挖掘 Run 列表 | ✅ |
| `/mining/create` | CreateRunView | 创建 Run | ✅ |
| `/mining/:runId` | RunDetailView | Run 详情 | ✅ |
| `/mining/:runId/documents/:docId` | RunDocumentDetailView | 文档处理详情 | ✅ |
| `/search` | SearchView | 知识检索 | ✅ |
| `/knowledge` | DocumentsView | 文档列表 | ✅ |
| `/knowledge/:docId` | DocumentDetailView | 文档详情 | ✅ |
| `/graph` | GraphView | 知识图谱 | ✅ |
| `/llm` | LlmView | LLM 任务列表 | ✅ |
| `/llm/:taskId` | LlmTaskDetailView | LLM 任务详情 | ✅ |
| `/settings` | SettingsView | 系统设置 | ✅ |

## 3. 组件清单（19个）

### 图表组件
- `BarChart.vue` — 柱状图
- `LineChart.vue` — 折线图
- `PieChart.vue` — 饼图
- `ForceGraph.vue` — 力导向图（D3.js）

### 通用组件
- `EmptyState.vue` — 空状态
- `ServiceHealthCard.vue` — 服务健康卡片
- `StatsCard.vue` — 统计卡片
- `StatusBadge.vue` — 状态徽章
- `YamlEditor.vue` — CodeMirror YAML 编辑器

### 业务组件
- `PipelineFlow.vue` — Pipeline 流程图
- `EvidenceCard.vue` — 证据卡片
- `PipelineTrace.vue` — Pipeline 追踪

### 设置组件
- `SystemConfigTab.vue` — 系统配置标签页
- `DomainManageTab.vue` — 域管理标签页
- `DomainDetailTab.vue` — 域详情标签页

## 4. 技术栈

- Vue 3 + TypeScript + Composition API
- Pinia（状态管理）
- Vue Router
- TailwindCSS
- ECharts（图表）
- D3.js（力导向图）
- CodeMirror（YAML 编辑器）
- Vite（构建工具）

## 5. 工业级演进规划

### Phase 1: 暗色主题支持（中等优先级）

当前仅支持亮色主题。企业应用通常需要暗色模式。

实现方案：
- TailwindCSS `dark:` 类
- `useTheme()` composable
- localStorage 持久化偏好

**预估工作量**：3-5 天

### Phase 2: 国际化 i18n（低优先级）

当前仅支持中文。如需国际化需要 i18n 支持。

实现方案：
- vue-i18n
- 提取所有硬编码中文到 locale 文件
- 支持 zh-CN / en-US

**预估工作量**：5-7 天

### Phase 3: 实时进度（中等优先级）

当前 Run 进度需要手动刷新。

实现方案：
- SSE（Server-Sent Events）推送 Run 进度
- 前端 EventSource 监听
- 进度条实时更新

**预估工作量**：2-3 天

### Phase 4: 键盘快捷键（低优先级）

企业应用需要键盘操作效率。

实现方案：
- `useHotkeys()` composable
- 常用操作快捷键（搜索 / 新建 / 导航）
- 快捷键帮助面板（? 键触发）

**预估工作量**：1-2 天

## 6. 完成度评估

| 维度 | 完成度 | 备注 |
|------|--------|------|
| 页面覆盖 | **95%** | 12 个页面，主要功能都有 |
| 组件复用 | **85%** | 19 个组件，部分可进一步抽象 |
| 响应式 | **80%** | TailwindCSS 响应式，但未全面测试移动端 |
| 暗色主题 | **0%** | 未实现 |
| 国际化 | **0%** | 未实现 |
