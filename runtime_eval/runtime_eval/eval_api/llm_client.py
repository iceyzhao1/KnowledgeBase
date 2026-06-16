"""HTTP client from eval-api to the eval-llm service.

A ``poster`` callable can be injected so tests drive an in-process eval-llm
``TestClient`` instead of a real network call.
"""

from __future__ import annotations

from typing import Callable

Poster = Callable[[str, dict], dict]


class LLMClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8801",
        *,
        timeout: int = 180,
        poster: Poster | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._poster = poster

    def _post(self, path: str, payload: dict, *, timeout: int | None = None) -> dict:
        if self._poster is not None:
            return self._poster(path, payload)
        import httpx  # noqa: PLC0415

        resp = httpx.post(
            self.base_url + path, json=payload, timeout=timeout or self.timeout
        )
        if resp.status_code >= 400:
            # 把 eval-llm 的 {"detail": "..."} 抽成可读消息，方便上层透传给用户。
            detail = None
            try:
                detail = resp.json().get("detail")
            except Exception:  # noqa: BLE001 - 非 JSON 响应时退回原文
                detail = None
            raise RuntimeError(
                f"eval-llm {path} 返回 {resp.status_code}：{detail or resp.text[:300]}"
            )
        return resp.json()

    def health(self) -> dict:
        if self._poster is not None:
            return self._poster("/health", {})
        import httpx  # noqa: PLC0415

        resp = httpx.get(self.base_url + "/health", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def generate_cases(
        self,
        *,
        document_text: str,
        doc_ref: str,
        types: list[str] | None = None,
        per_type: int = 3,
        persona: str | None = None,
        timeout: int | None = None,
    ) -> dict:
        # 出题让 claude 通读整篇文档、按多类型逐题产出，耗时长（尤其走代理时），
        # 远超默认 180s 的 HTTP 等待。这里放宽到至少 900s，避免 eval-api 先于
        # eval-llm 把还在出题的请求掐断（典型现象：前端 ReadTimeout，eval-llm 无日志）。
        return self._post(
            "/generate-cases",
            {
                "document_text": document_text,
                "doc_ref": doc_ref,
                "types": types,
                "per_type": per_type,
                "persona": persona,
            },
            timeout=timeout or max(self.timeout, 900),
        )

    def generate_cases_preview(
        self,
        *,
        document_text: str,
        doc_ref: str,
        types: list[str] | None = None,
        per_type: int = 3,
        persona: str | None = None,
    ) -> dict:
        """只取「将发给大模型的提示词」，不真正调用模型。"""
        return self._post(
            "/generate-cases/preview",
            {
                "document_text": document_text,
                "doc_ref": doc_ref,
                "types": types,
                "per_type": per_type,
                "persona": persona,
            },
        )

    def judge(
        self,
        *,
        question: str,
        question_type: str,
        expected_answer: str,
        key_points: list[str],
        agent_answer: str,
    ) -> dict:
        return self._post(
            "/judge",
            {
                "question": question,
                "question_type": question_type,
                "expected_answer": expected_answer,
                "key_points": key_points,
                "agent_answer": agent_answer,
            },
        )

    def judge_retrieval(
        self,
        *,
        question: str,
        expected_answer: str,
        gold_facts: list[str],
        items: list[str],
    ) -> dict:
        return self._post(
            "/judge-retrieval",
            {
                "question": question,
                "expected_answer": expected_answer,
                "gold_facts": gold_facts,
                "items": items,
            },
        )

    def answer(
        self,
        *,
        question: str,
        evidence: list[str],
        channel: str = "claude_cli",
        model: str = "",
        timeout: int | None = None,
    ) -> dict:
        """L2 作答：让花名册里某个模型只读固定证据包整合一版最终答案。

        ``channel``/``model`` 指定走哪个通道、点名哪个档位（claude_cli 的 haiku/
        sonnet/opus，或 deepseek/minimax 等 OpenAI 兼容通道）。返回 ``{answer, usage}``。
        作答含模型推理、可能走代理，耗时长，默认放宽超时（至少 900s）。
        """

        return self._post(
            "/answer",
            {
                "question": question,
                "evidence": evidence,
                "channel": channel,
                "model": model,
            },
            timeout=timeout or max(self.timeout, 900),
        )

    def judge_answer_match(
        self,
        *,
        question: str,
        expected_answer: str,
        key_points: list[str],
        answer: str,
    ) -> dict:
        """L2 对照判：把模型答案 A 跟黄金最终答案 G 比，出覆盖/准确/矛盾 + 综合分。"""

        return self._post(
            "/judge-answer",
            {
                "question": question,
                "expected_answer": expected_answer,
                "key_points": key_points,
                "answer": answer,
            },
        )

    def run_agent(
        self,
        *,
        question: str,
        system: str | None = None,
        route: str = "kb",
        timeout: int | None = None,
    ) -> dict:
        """让 eval-llm 用 claude -p 回答单题。

        ``route``：``kb`` 用库（MCP 检索）/ ``closed_book`` 闭卷裸答 / ``web`` 联网。
        agent 含多轮工具调用、耗时长，所以默认放宽超时（至少 900s）。
        """

        return self._post(
            "/run-agent",
            {"question": question, "system": system, "route": route},
            timeout=timeout or max(self.timeout, 900),
        )
