"""Deterministic offline provider for tests and dry-runs.

It inspects the prompt for the ``MODE:`` marker the prompts embed and returns
schema-valid JSON, so the full generate -> judge pipeline can run with no network
and no API key.
"""

from __future__ import annotations

import json
import re

from ...shared.models import TokenUsage
from .base import ChatResult


class MockProvider:
    name = "mock"

    def __init__(self, config=None):
        self.config = config

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> ChatResult:
        usage = TokenUsage(prompt_tokens=120, completion_tokens=80, total_tokens=200)
        if "MODE: RETRIEVAL_JUDGE" in user:
            return ChatResult(text=self._judge_retrieval(user), usage=usage)
        if "MODE: ANSWER_MATCH_JUDGE" in user:
            return ChatResult(text=self._judge_answer_match(user), usage=usage)
        if "MODE: JUDGE" in user:
            return ChatResult(text=self._judge(user), usage=usage)
        if "MODE: ANSWER" in user:
            return ChatResult(text=self._answer(user), usage=usage)
        if "MODE: OVERALL_SUMMARY" in user:
            return ChatResult(text=self._overall_summary(user), usage=usage)
        return ChatResult(text=self._generate(user), usage=usage)

    def _answer(self, user: str) -> str:
        # L2 作答占位：把证据片段拼成一段「最终答案」，便于裁判/路由测试有内容可比。
        question = self._field(user, "QUESTION")
        evidence = self._numbered_block(user, "EVIDENCE")
        body = "；".join(evidence) if evidence else "证据不足，无法回答。"
        return f"[mock-answer] 针对「{question}」，依据证据整合如下：{body}"

    def _overall_summary(self, user: str) -> str:
        # 离线确定性总评：统计 user 里有几层标了「未评测」，回显一段中文评语，
        # 便于端到端测试有可断言的自然语言内容（不触网、不调真模型）。
        missing = user.count("未评测")
        return (
            "[mock-总评] 这个知识库整体表现一般。"
            f"三层评测中有 {missing} 层尚未评测，建议补齐后再做整体判断。"
            "已评测的层里，检索与答案质量是后续优化重点。"
        )

    def _generate(self, user: str) -> str:
        # Pull the requested types from the request section only (before the
        # JSON schema, which itself enumerates every type name).
        segment = user.split("输出 JSON schema", 1)[0]
        types = re.findall(
            r"factoid|conceptual|procedural|constraint|troubleshooting|navigational",
            segment,
        )
        types = list(dict.fromkeys(types)) or ["factoid", "conceptual"]
        doc_match = re.search(r"FILE:\s*(\S+)", user)
        doc = doc_match.group(1) if doc_match else "mock/doc.md"
        cases = []
        for i, qtype in enumerate(types):
            cases.append(
                {
                    "question": f"[mock-{qtype}] 关于该文档的{qtype}问题{i + 1}？",
                    "question_type": qtype,
                    "expected_answer": f"这是 {qtype} 问题的期望答案示例。",
                    "key_points": ["要点A", "要点B"],
                    "expected_evidence": [f"{qtype} 黄金事实1", f"{qtype} 黄金事实2"],
                    "expected_entities": [f"{qtype}实体"],
                    "source": {"doc": doc, "section": None, "quote": None},
                    "difficulty": "normal",
                }
            )
        return json.dumps({"cases": cases}, ensure_ascii=False)

    def _judge_answer_match(self, user: str) -> str:
        # 确定性对照判（答案 A vs 黄金 G）占位规则，便于测试：
        #   覆盖：G 的要点子串出现在 A → covered，否则 missed。
        #   准确：把 A 按 。；\n 切成论断；含"矛盾"二字 → contradictions（硬伤），
        #         否则命中任一要点 → correct，其余 → extra（多余/跑题）。
        key_points = self._numbered_block(user, "KEY POINTS")
        answer = self._section(user, "MODEL ANSWER")

        covered = [p for p in key_points if p and p in answer]
        missed = [p for p in key_points if p and p not in answer]

        claims = [c.strip() for c in re.split(r"[。；\n]+", answer) if c.strip()]
        correct: list[str] = []
        extra: list[str] = []
        contradictions: list[str] = []
        for claim in claims:
            if "矛盾" in claim:
                contradictions.append(claim)
            elif any(p and p in claim for p in key_points):
                correct.append(claim)
            else:
                extra.append(claim)

        return json.dumps(
            {
                "covered_points": covered,
                "missed_points": missed,
                "correct_claims": correct,
                "extra_claims": extra,
                "contradictions": contradictions,
                "rationale": "mock 对照判：基于要点子串 + 论断切分的占位判定。",
            },
            ensure_ascii=False,
        )

    def _judge(self, user: str) -> str:
        # Crude lexical overlap so the mock score varies with the answer.
        ans = ""
        exp = ""
        m_ans = re.search(r"AGENT_ANSWER:\s*(.+?)\nEXPECTED", user, re.S)
        m_exp = re.search(r"EXPECTED_ANSWER:\s*(.+?)\nKEY_POINTS", user, re.S)
        if m_ans:
            ans = m_ans.group(1)
        if m_exp:
            exp = m_exp.group(1)
        score = 1.0 if exp and exp.strip()[:6] in ans else (0.5 if ans.strip() else 0.0)
        verdict = (
            "correct" if score >= 0.8 else "partial" if score > 0 else "incorrect"
        )
        return json.dumps(
            {
                "verdict": verdict,
                "score": score,
                "rationale": "mock 评分：基于词面重叠的占位打分。",
                "covered_points": ["要点A"] if score > 0 else [],
                "missed_points": [] if score >= 0.8 else ["要点B"],
            },
            ensure_ascii=False,
        )

    def _judge_retrieval(self, user: str) -> str:
        # Deterministic relevance: an item is graded 3 if it contains a gold
        # fact substring (else 0); a fact is covered_by the first item that
        # contains it. Mirrors the substring approximation so tests are stable.
        # When there are no gold facts (live/precision-only runs), fall back to
        # question-term overlap so item grades still reflect question relevance.
        facts = self._numbered_block(user, "GOLD FACTS")
        items = self._numbered_block(user, "RETRIEVED ITEMS")

        q_shingles = self._shingles(self._field(user, "QUESTION")) if not facts else set()

        item_out = []
        for i, item in enumerate(items, start=1):
            if facts:
                grade = 3 if any(f and f in item for f in facts) else 0
            else:
                grade = 3 if q_shingles & self._shingles(item) else 0
            item_out.append({"index": i, "grade": grade})

        fact_out = []
        for j, fact in enumerate(facts, start=1):
            covered_by = 0
            for i, item in enumerate(items, start=1):
                if fact and fact in item:
                    covered_by = i
                    break
            fact_out.append({"index": j, "covered_by": covered_by})

        return json.dumps(
            {
                "items": item_out,
                "facts": fact_out,
                "rationale": "mock 检索裁判：基于子串包含的占位判断。",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _field(user: str, key: str) -> str:
        m = re.search(rf"^{re.escape(key)}:\s*(.+)$", user, re.M)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _shingles(text: str) -> set[str]:
        # 2-char shingles over alphanumerics + CJK, lowercased; good enough as a
        # deterministic stand-in for question/item term overlap.
        toks = re.findall(r"[0-9a-zA-Z一-鿿]+", (text or "").lower())
        out: set[str] = set()
        for tok in toks:
            if len(tok) == 1:
                out.add(tok)
            for i in range(len(tok) - 1):
                out.add(tok[i : i + 2])
        return out

    @staticmethod
    def _section(user: str, header: str) -> str:
        # Grab the free-text body under a "==== <header> ... ====" marker, up to
        # the next "====" marker or the trailing "请按..." schema instruction.
        lines = user.splitlines()
        start = None
        for i, line in enumerate(lines):
            if line.startswith("====") and header in line:
                start = i + 1
                break
        if start is None:
            return ""
        out: list[str] = []
        for line in lines[start:]:
            if line.startswith("====") or line.startswith("请按"):
                break
            out.append(line)
        return "\n".join(out).strip()

    @staticmethod
    def _numbered_block(user: str, header: str) -> list[str]:
        # Extract the "1. ...\n2. ..." lines under a "==== <header> ... ====" marker.
        lines = user.splitlines()
        start = None
        for i, line in enumerate(lines):
            if line.startswith("====") and header in line:
                start = i + 1
                break
        if start is None:
            return []
        out: list[str] = []
        for line in lines[start:]:
            stripped = line.strip()
            if stripped.startswith("===="):
                break
            if not stripped:
                continue
            m = re.match(r"^\d+\.\s*(.+)$", stripped)
            if m:
                text = m.group(1).strip()
                if text and text != "（无黄金事实）" and text != "（无检索条目）":
                    out.append(text)
        return out
