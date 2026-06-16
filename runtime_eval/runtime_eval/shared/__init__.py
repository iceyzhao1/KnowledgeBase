"""Cross-service pure-domain models and utilities (no web dependency).

Imported by both eval_llm and eval_api. Holds the JSON-serialisable pydantic
models that flow between the three services plus best-effort JSON parsing for
LLM output.
"""
