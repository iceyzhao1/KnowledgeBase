declare module '@/utils/paradigmCatalog.mjs' {
  import type { PublishedParadigm } from '@/types/evaluation'

  export function normalizeParadigms(payload: unknown): PublishedParadigm[]
  export function paradigmSubjectId(option: PublishedParadigm | null | undefined): string
}
