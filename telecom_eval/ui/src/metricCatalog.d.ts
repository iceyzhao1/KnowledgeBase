declare module '@/utils/metricCatalog.mjs' {
  export interface TesterMetric {
    label: string
    rawId: string
    testType: string
    group: string
    description: string
    interpretation: string
    value: unknown
    displayValue: string
    tone: string
    verdict: string
  }

  export interface TesterMetricGroup {
    key: string
    title: string
    subtitle: string
    metricIds: string[]
    metrics: TesterMetric[]
  }

  export function displayMetricValue(metricId: string, value: unknown): string
  export function metricTone(metricId: string, value: unknown): string
  export function metricVerdict(metricId: string, value: unknown): string
  export function explainMetric(metricId: string, value: unknown): TesterMetric
  export function flattenMetricSummary(summary: Record<string, unknown>): Record<string, unknown>
  export function buildTesterMetricGroups(summary: Record<string, unknown>): TesterMetricGroup[]
  export function buildRunInterpretation(summary: Record<string, unknown>): string
}
