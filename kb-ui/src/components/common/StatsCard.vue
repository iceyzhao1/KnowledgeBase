<template>
  <div class="stats-card">
    <div class="stats-card__label">{{ label }}</div>
    <div class="stats-card__value">{{ value }}</div>
    <div v-if="sub" class="stats-card__sub">{{ sub }}</div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  sub?: string
}>()
</script>

<style scoped>
.stats-card {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 16px 20px;
  box-shadow: var(--kb-shadow-card);
}

.stats-card__label {
  font-size: 12px;
  color: var(--kb-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-bottom: 6px;
}

.stats-card__value {
  font-size: 24px;
  font-weight: 700;
  color: var(--kb-text-primary);
  font-variant-numeric: tabular-nums;
}

.stats-card__sub {
  font-size: 12px;
  color: var(--kb-text-secondary);
  margin-top: 4px;
}
</style>
