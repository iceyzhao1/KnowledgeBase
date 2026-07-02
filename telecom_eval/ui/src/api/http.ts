import axios from 'axios'

// 开发者前端直连评估 FastAPI（Vite 把 /api 代理到 8810）。
export const http = axios.create({ baseURL: '' })

export function extractItems<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[]
  const obj = (data ?? {}) as Record<string, unknown>
  for (const key of ['items', 'data']) {
    const val = obj[key]
    if (Array.isArray(val)) return val as T[]
  }
  return []
}

export function extractOne<T>(data: unknown): T {
  const obj = (data ?? {}) as Record<string, unknown>
  return (obj.data ?? obj) as T
}
