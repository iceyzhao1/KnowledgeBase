<template>
  <div class="health-card" :class="`health-card--${status}`">
    <div class="health-card__header">
      <span class="health-card__name">{{ name }}</span>
      <span class="health-card__dot" />
    </div>
    <div v-if="detail" class="health-card__detail">{{ detail }}</div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  name: string
  status: 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
  detail?: string
}>()
</script>

<style scoped>
.health-card {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 14px 18px;
  box-shadow: var(--kb-shadow-card);
  border-left: 3px solid var(--kb-text-secondary);
}

.health-card--healthy { border-left-color: var(--kb-success); }
.health-card--degraded { border-left-color: var(--kb-warning); }
.health-card--unhealthy { border-left-color: var(--kb-danger); }
.health-card--unknown { border-left-color: var(--kb-text-secondary); }

.health-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.health-card__name {
  font-size: 14px;
  font-weight: 500;
  color: var(--kb-text-primary);
}

.health-card__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--kb-text-secondary);
}

.health-card--healthy .health-card__dot { background: var(--kb-success); }
.health-card--degraded .health-card__dot { background: var(--kb-warning); }
.health-card--unhealthy .health-card__dot { background: var(--kb-danger); }

.health-card__detail {
  font-size: 12px;
  color: var(--kb-text-secondary);
  margin-top: 6px;
}
</style>
