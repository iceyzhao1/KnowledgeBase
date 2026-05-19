<template>
  <aside class="sidebar">
    <div class="sidebar__logo">
      <span class="sidebar__logo-text">CoreMasterKB</span>
    </div>

    <nav class="sidebar__nav">
      <router-link
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="sidebar__link"
        active-class="sidebar__link--active"
      >
        <el-icon :size="18"><component :is="item.icon" /></el-icon>
        <span>{{ item.label }}</span>
      </router-link>
    </nav>

    <div class="sidebar__footer">
      <div class="sidebar__domain">
        <el-icon :size="14"><Connection /></el-icon>
        <span class="sidebar__domain-name">{{ domainStore.currentDomain }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import {
  Monitor, Management, Search, FolderOpened, Share,
  Cpu, Setting, Connection,
} from '@element-plus/icons-vue'
import { useDomainStore } from '@/stores/domain'

const domainStore = useDomainStore()

const navItems = [
  { path: '/', label: '概览', icon: Monitor },
  { path: '/mining', label: '挖掘管理', icon: Management },
  { path: '/search', label: '检索测试', icon: Search },
  { path: '/knowledge', label: '知识资产', icon: FolderOpened },
  { path: '/graph', label: '知识图谱', icon: Share },
  { path: '/llm', label: 'LLM 服务', icon: Cpu },
  { path: '/settings', label: '系统设置', icon: Setting },
]
</script>

<style scoped>
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: var(--kb-sidebar-width);
  background: var(--kb-bg-sidebar);
  display: flex;
  flex-direction: column;
  z-index: 100;
}

.sidebar__logo {
  height: var(--kb-header-height);
  display: flex;
  align-items: center;
  padding: 0 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.sidebar__logo-text {
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.3px;
}

.sidebar__nav {
  flex: 1;
  padding: 8px 0;
  overflow-y: auto;
}

.sidebar__link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  color: var(--kb-text-sidebar);
  text-decoration: none;
  font-size: 14px;
  transition: all 0.15s ease;
}

.sidebar__link:hover {
  background: var(--kb-bg-sidebar-hover);
  color: var(--kb-text-sidebar-active);
}

.sidebar__link--active {
  background: var(--kb-bg-sidebar-active);
  color: var(--kb-text-sidebar-active);
  font-weight: 500;
}

.sidebar__footer {
  padding: 12px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.sidebar__domain {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--kb-text-sidebar);
  font-size: 12px;
}

.sidebar__domain-name {
  opacity: 0.8;
}
</style>
