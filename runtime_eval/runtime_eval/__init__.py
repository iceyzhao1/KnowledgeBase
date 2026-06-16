"""runtime_eval — 通用知识库运行态测试框架（三服务）.

拆分为三个独立服务：

- eval-llm（runtime_eval.eval_llm）：大模型服务，暴露 /generate-cases、/judge，
  封装 Provider 与密钥。外接大模型以一线操作员身份阅读文档出题，并担任裁判打分。
- eval-api（runtime_eval.eval_api）：后端服务，REST 编排项目/文档/用例/回答/评测/
  报告，HTTP 调 eval-llm，持久化产物，并托管 eval-web 静态资源。
- eval-web（runtime_eval.eval_web）：纯前端 SPA，仅调 eval-api。

被测 copilot agent 的回答经前端人工上传，由 eval-llm 裁判打分，最终产出按问题
类型区分准确率、token 消耗、查询时长等指标的测试报告。
"""

__all__ = ["__version__"]

__version__ = "2.0.0"
