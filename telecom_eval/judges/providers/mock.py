"""Deterministic mock provider for tests — no real LLM call."""

from __future__ import annotations

import hashlib
import json

from .base import ProviderResult, ProviderUsage


class MockJudgeProvider:
    name = "mock"

    def __init__(self) -> None:
        self.call_count = 0

    def invoke(self, *, system: str, user: str) -> ProviderResult:
        self.call_count += 1
        digest = hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest()[:8]
        payload = {"score": 1, "reason": "mock_supported", "fingerprint": digest}
        text = json.dumps(payload, ensure_ascii=False)
        usage = ProviderUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        return ProviderResult(text=text, usage=usage, raw={"result": text, "mock": True})
