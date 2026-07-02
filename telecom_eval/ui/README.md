# telecom_eval 评估工作台（开发者前端）

这是**评估框架自带的独立前端**，仅供开发期调试评估结果使用，与面向最终用户的 `kb-ui` 完全分离、互不依赖。最终用户不感知本界面。

## 运行

先起评估后端（默认端口 8810）：

```powershell
& "D:\software\anaconda\envs\kb\python.exe" -m uvicorn telecom_eval.api.app:create_app --factory --port 8810
```

再起前端开发服务器（端口 5174，`/api` 自动代理到 8810）：

```powershell
cd telecom_eval/ui
npm install      # 首次
npm run dev
```

浏览器打开 http://localhost:5174 。

如后端不在默认地址，设 `TELECOM_EVAL_API` 环境变量后再 `npm run dev`。

## 构建

```powershell
npm run build    # vue-tsc 类型检查 + vite 构建到 dist/
```

## 能力

- 评估运行列表 / 新建运行（含 LLM 判分预算开关）
- 单次运行报告：指标概览、明细、失败样本、LLM 用量
- 样本调试页：问题/标准答案/标准证据、检索证据包（含稳定 match_keys/provenance）、Trace 时间线、指标、Artifact、失败归因、Judge 调用与 token、原始 JSON
- 对比报告：胜率/退化率、样本级 delta、退化清单
