import { readFileSync } from 'fs'
import { resolve } from 'path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

function readRuntimeConfig() {
  try {
    const path = resolve(__dirname, '..', 'config', 'runtime.json')
    return JSON.parse(readFileSync(path, 'utf-8'))
  } catch {
    return {}
  }
}

const runtimeConfig = readRuntimeConfig()
const uiConfig = runtimeConfig.ui || {}

// Dev server proxies:
// - /api -> telecom_eval FastAPI
// - /paradigm-api -> real paradigm service, e.g. http://10.205.71.26:8081
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: Number(process.env.TELECOM_EVAL_UI_PORT || uiConfig.dev_port || 5174),
    proxy: {
      '/api': {
        target: process.env.TELECOM_EVAL_API || uiConfig.eval_api_base_url || 'http://localhost:8810',
        changeOrigin: true,
      },
      '/paradigm-api': {
        target: process.env.TELECOM_PARADIGM_API || uiConfig.paradigm_api_base_url || 'http://10.205.71.26:8081',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/paradigm-api/, ''),
      },
    },
  },
})
