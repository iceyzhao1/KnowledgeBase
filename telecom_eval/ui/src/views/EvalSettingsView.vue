<template>
  <div class="eval-settings">
    <div class="eval-settings__head">
      <h2>设置说明</h2>
      <router-link to="/"><el-button :icon="Back">返回</el-button></router-link>
    </div>

    <el-card shadow="never" class="mb">
      <template #header><span>运行配置</span></template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="接口地址、端口、被测检索服务和评估大模型都集中在 telecom_eval/config/runtime.json；环境变量仍可临时覆盖。"
        class="mb"
      />
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="配置文件">telecom_eval/config/runtime.json</el-descriptions-item>
        <el-descriptions-item label="配置说明">telecom_eval/config/README.md</el-descriptions-item>
        <el-descriptions-item label="当前前端端口">5174</el-descriptions-item>
        <el-descriptions-item label="当前评估后端">http://127.0.0.1:8811</el-descriptions-item>
        <el-descriptions-item label="当前范式/检索服务">http://10.205.71.26:8081</el-descriptions-item>
        <el-descriptions-item label="最大并发评估任务数">telecom_eval/config/runtime.json 中的 runner.max_concurrent_runs，默认 2</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header><span>大模型判分治理</span></template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="所有真实大模型调用只能经判分服务、判分缓存、模型提供方执行，前端、指标、诊断、报告都不直接调用大模型。"
        class="mb"
      />
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="当前默认模型提供方">mock</el-descriptions-item>
        <el-descriptions-item label="真实模型提供方">claude_cli，通过本机 claude 命令做语义判分</el-descriptions-item>
        <el-descriptions-item label="缓存键">task_type + case + answer/evidence/key_points + rubric + provider + model</el-descriptions-item>
        <el-descriptions-item label="前端可配置项">大模型失败重试次数</el-descriptions-item>
        <el-descriptions-item label="默认预算">总调用次数和总令牌数不设上限，参与大模型判分样本数按当前测试集自动设置</el-descriptions-item>
        <el-descriptions-item label="超预算行为">写入“已跳过”的判分结果产物，相关指标置为“无法判定”</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never">
      <template #header><span>常用环境变量覆盖</span></template>
      <pre class="env">TELECOM_EVAL_CONFIG=telecom_eval/config/runtime.json
TELECOM_EVAL_DB_PATH=data/evaluation/telecom_eval_demo.db
TELECOM_EVAL_SUBJECT_PROVIDER=http | fake
TELECOM_EVAL_SEARCH_URL=http://10.205.71.26:8081
TELECOM_EVAL_JUDGE_PROVIDER=mock | claude_cli
TELECOM_EVAL_CLAUDE_BIN=claude
TELECOM_EVAL_CLAUDE_MODEL=
TELECOM_EVAL_CLAUDE_TIMEOUT=600
TELECOM_EVAL_CLAUDE_PROXY=
TELECOM_EVAL_MAX_CONCURRENT_RUNS=2

TELECOM_EVAL_UI_PORT=5174
TELECOM_EVAL_API=http://127.0.0.1:8811
TELECOM_PARADIGM_API=http://10.205.71.26:8081</pre>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { Back } from '@element-plus/icons-vue'
</script>

<style scoped>
.eval-settings__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.mb { margin-bottom: 16px; }
.env { background: var(--kb-bg-subtle, #f8fafc); border: 1px solid var(--kb-border, #e2e8f0); border-radius: 6px; padding: 12px; font-size: 12px; line-height: 1.6; }
</style>
