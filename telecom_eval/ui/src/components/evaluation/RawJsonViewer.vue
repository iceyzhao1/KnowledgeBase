<template>
  <el-collapse v-if="collapsible">
    <el-collapse-item :title="title">
      <pre class="raw-json">{{ pretty }}</pre>
    </el-collapse-item>
  </el-collapse>
  <pre v-else class="raw-json">{{ pretty }}</pre>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ data: unknown; title?: string; collapsible?: boolean }>(),
  { title: '原始数据', collapsible: true },
)

const pretty = computed(() => {
  try {
    return JSON.stringify(props.data, null, 2)
  } catch {
    return String(props.data)
  }
})
</script>

<style scoped>
.raw-json {
  background: var(--kb-bg-subtle, #f8fafc);
  border: 1px solid var(--kb-border, #e2e8f0);
  border-radius: 6px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.5;
  max-height: 480px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
