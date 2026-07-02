"""HTTP retrieval subject adapter — calls the real knowledge-base search API.

把 ``case.question`` POST 到检索层 ``/api/v1/search``（样例见 data/query_result/result.txt），
再用 ``map_retrieval_response`` 把 contextPack 响应映射成标准 RetrievalTrace。

只有 Runner 调用 Adapter，只有 Adapter 触达外部系统（架构 5.3）。调用失败不抛出，
而是返回带 ``errors`` 的空证据 Trace，避免一条样本拖垮整次评估。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from telecom_eval.models.cases import EvaluationCase
from telecom_eval.models.traces import (
    EvidenceItem,
    RetrievalInput,
    RetrievalOutput,
    RetrievalTrace,
)
from telecom_eval.subjects.mapping import map_retrieval_response

logger = logging.getLogger(__name__)


@dataclass
class HttpRetrievalConfig:
    base_url: str = "http://121.89.90.178:8081"
    search_path: str = "/api/v1/search"
    domain: str = "cloud_core_network"
    timeout: float = 30.0
    debug: bool = False
    trust_env: bool = False


class HttpRetrievalAdapter:
    """检索 Evidence Package Provider 的真实 HTTP 适配器。"""

    def __init__(
        self,
        subject_id: str,
        config: HttpRetrievalConfig | None = None,
        *,
        client: Any | None = None,
    ):
        self.subject_id = subject_id
        self.config = config or HttpRetrievalConfig()
        self._client = client  # 注入用于测试；None 时延迟创建 httpx.Client
        self._owns_client = client is None

    def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=self.config.timeout, trust_env=self.config.trust_env)
        return self._client

    def _resolve_domain(self, case: EvaluationCase) -> str:
        return str(case.filters.get("domain") or self.config.domain)

    def _call(
        self,
        *,
        query: str,
        domain: str,
        case_id: str,
        run_id: str,
        scenario_id: str,
        filters: dict | None = None,
    ) -> RetrievalTrace:
        url = self.config.base_url.rstrip("/") + self.config.search_path
        payload = {"query": query, "domain": domain, "debug": self.config.debug}
        started = time.perf_counter()
        try:
            resp = self._get_client().post(url, json=payload, timeout=self.config.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # 网络/解析失败：返回错误 Trace，不中断评估
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.exception(
                "retrieval http call failed run_id=%s case_id=%s subject_id=%s url=%s latency_ms=%s",
                run_id,
                case_id,
                self.subject_id,
                url,
                latency_ms,
            )
            return RetrievalTrace(
                trace_id=f"trace_ret_{run_id}_{case_id}",
                case_id=case_id,
                run_id=run_id,
                subject_id=self.subject_id,
                input=RetrievalInput(query=query, scenario_id=scenario_id, filters=filters or {}),
                output=RetrievalOutput(evidence_package=[]),
                latency_ms=latency_ms,
                errors=[f"retrieval http call failed: {exc}"],
            )

        trace = map_retrieval_response(
            data,
            case_id=case_id,
            run_id=run_id,
            subject_id=self.subject_id,
            original_query=query,
            domain=domain,
        )
        if not trace.input.scenario_id:
            trace.input.scenario_id = scenario_id
        return trace

    def retrieve(self, case: EvaluationCase, *, run_id: str) -> RetrievalTrace:
        return self._call(
            query=case.question,
            domain=self._resolve_domain(case),
            case_id=case.case_id,
            run_id=run_id,
            scenario_id=case.scenario_id,
            filters=case.filters,
        )

    def search(self, query: str, *, domain: str | None = None) -> list[EvidenceItem]:
        """给案例编辑器挑原文用：按 query 取候选证据段落（含原文 text）。"""
        trace = self._call(
            query=query,
            domain=domain or self.config.domain,
            case_id="authoring",
            run_id="authoring",
            scenario_id="",
        )
        return trace.output.evidence_package

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
