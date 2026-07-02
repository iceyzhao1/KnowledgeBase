import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('@/views/EvalDashboardView.vue') },
    { path: '/datasets', name: 'dataset-list', component: () => import('@/views/DatasetListView.vue') },
    { path: '/datasets/create', name: 'dataset-create', component: () => import('@/views/DatasetCreateView.vue') },
    {
      path: '/datasets/:datasetId',
      name: 'dataset-detail',
      component: () => import('@/views/DatasetDetailView.vue'),
      props: true,
    },
    {
      path: '/datasets/:datasetId/import',
      name: 'dataset-import',
      component: () => import('@/views/DatasetImportView.vue'),
      props: true,
    },
    {
      path: '/datasets/:datasetId/cases/new',
      name: 'case-create',
      component: () => import('@/views/CaseEditorView.vue'),
      props: true,
    },
    {
      path: '/datasets/:datasetId/cases/:caseId/edit',
      name: 'case-edit',
      component: () => import('@/views/CaseEditorView.vue'),
      props: true,
    },
    { path: '/runs/create', name: 'run-create', component: () => import('@/views/CreateRunView.vue') },
    {
      path: '/runs/:runId',
      name: 'run-report',
      component: () => import('@/views/RunReportView.vue'),
      props: true,
    },
    {
      path: '/runs/:runId/cases/:caseId',
      name: 'case-debug',
      component: () => import('@/views/CaseDebugView.vue'),
      props: true,
    },
    {
      path: '/comparisons/:comparisonId',
      name: 'comparison-report',
      component: () => import('@/views/ComparisonReportView.vue'),
      props: true,
    },
    { path: '/settings', name: 'settings', component: () => import('@/views/EvalSettingsView.vue') },
  ],
})

export default router
