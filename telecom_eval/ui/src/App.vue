<template>
  <div class="app-shell">
    <aside class="app-shell__side">
      <div class="app-shell__logo">
        <span class="app-shell__logo-mark">EV</span>
        <div class="app-shell__logo-text">
          <strong>电信评估</strong>
          <small>评估工作台 · 开发者</small>
        </div>
      </div>
      <nav class="app-shell__nav">
        <router-link
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="app-shell__link"
          :class="{ 'app-shell__link--active': isActive(item.to) }"
        >
          <el-icon :size="17"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="app-shell__foot">
        <span class="dot" :class="{ 'dot--ok': healthy }" />
        <span>{{ healthy ? '后端在线' : '后端未连通' }}</span>
      </div>
    </aside>
    <main class="app-shell__main">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Collection, DataAnalysis, Plus, Setting } from '@element-plus/icons-vue'
import { useEvaluationApi } from '@/api/evaluation'

const route = useRoute()
const api = useEvaluationApi()
const healthy = ref(false)

const nav = [
  { to: '/', label: '评估总览', icon: DataAnalysis },
  { to: '/datasets', label: '测试集', icon: Collection },
  { to: '/runs/create', label: '新建评估', icon: Plus },
  { to: '/settings', label: '设置说明', icon: Setting },
]

function isActive(to: string): boolean {
  if (to === '/') return route.path === '/'
  return route.path.startsWith(to)
}

onMounted(async () => {
  try {
    const res = await api.getHealth()
    healthy.value = res.status === 'ok'
  } catch {
    healthy.value = false
  }
})
</script>

<style scoped>
.app-shell { display: flex; min-height: 100vh; overflow-x: hidden; }
.app-shell__side {
  width: 232px;
  background: #0f172a;
  color: #cbd5e1;
  display: flex;
  flex-direction: column;
  position: fixed;
  inset: 0 auto 0 0;
}
.app-shell__logo { display: flex; align-items: center; gap: 10px; padding: 18px 18px; border-bottom: 1px solid rgba(255, 255, 255, 0.07); }
.app-shell__logo-mark {
  width: 34px; height: 34px; border-radius: 8px; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #6366f1, #818cf8); color: #fff; font-weight: 800; font-size: 13px;
}
.app-shell__logo-text { display: flex; flex-direction: column; line-height: 1.3; }
.app-shell__logo-text strong { color: #f1f5f9; font-size: 14px; }
.app-shell__logo-text small { color: #64748b; font-size: 11px; }
.app-shell__nav { flex: 1; padding: 12px 10px; }
.app-shell__link {
  display: flex; align-items: center; gap: 10px; padding: 10px 14px; margin-bottom: 2px;
  border-radius: 8px; color: #cbd5e1; text-decoration: none; font-size: 13.5px;
}
.app-shell__link:hover { background: rgba(255, 255, 255, 0.06); color: #fff; }
.app-shell__link--active { background: rgba(99, 102, 241, 0.18); color: #fff; font-weight: 600; }
.app-shell__foot { padding: 14px 18px; border-top: 1px solid rgba(255, 255, 255, 0.07); font-size: 12px; display: flex; align-items: center; gap: 8px; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: #ef4444; }
.dot--ok { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
.app-shell__main {
  flex: 1;
  min-width: 0;
  margin-left: 232px;
  padding: 24px 28px;
  background: #f8fafc;
  min-height: 100vh;
  overflow-x: auto;
}
</style>
