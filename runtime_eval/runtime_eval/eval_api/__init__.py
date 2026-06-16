"""eval-api: the orchestration backend.

Owns REST endpoints (projects / documents / suites / responses / runs / reports),
document parsing, persistence, metrics and reporting. Reaches eval-llm over HTTP
for the two LLM operations (generate-cases / judge). Holds no model credentials.
"""
