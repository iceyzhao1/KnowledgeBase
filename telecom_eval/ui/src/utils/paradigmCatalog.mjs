export function normalizeParadigms(payload) {
  const items = Array.isArray(payload) ? payload : payload?.paradigms
  if (!Array.isArray(items)) return []

  return items
    .filter((item) => item && typeof item.name === 'string' && item.name.trim())
    .map((item) => ({
      id: String(item.id || ''),
      name: item.name.trim(),
      label: item.name.trim(),
      value: item.name.trim(),
      description: item.description ?? '',
      version: Number(item.version || 0),
      url: String(item.url || ''),
    }))
}

export function paradigmSubjectId(option) {
  return option?.name || option?.value || ''
}
