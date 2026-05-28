"""Test batch polling: verify poll_all for question gen & contextualizer."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from knowledge_mining_zym.mining.contracts.models import RawSegmentData


def _make_segments(n: int = 3) -> list[RawSegmentData]:
    """Create test segments."""
    return [
        RawSegmentData(
            document_key="doc:/test.md",
            segment_index=i,
            raw_text=f"Content for segment {i}. " * 10,
            section_title=f"Section {i}",
            block_type="paragraph",
            token_count=50,
        )
        for i in range(n)
    ]


class TestQuestionGeneratorBatchPolling:
    @patch("knowledge_mining_zym.mining.stages.retrieval_units.LlmQuestionGenerator.__init__", return_value=None)
    def test_generate_batch_uses_poll_all(self, mock_init):
        """generate_batch should call poll_all, not serial poll_result."""
        from knowledge_mining_zym.mining.stages.retrieval_units import LlmQuestionGenerator

        gen = LlmQuestionGenerator.__new__(LlmQuestionGenerator)
        gen._client = MagicMock()
        gen._timeout = 30
        gen._poll_interval = 1.0
        gen._status_error_limit = 5
        gen._cancel_checker = None
        gen._last_task_ids = {}
        gen._profile = None

        # Mock submit to return task_ids
        gen._client.submit_task.side_effect = [
            "task-1", "task-2", "task-3",
        ]

        # Mock poll_all to return results for all tasks
        gen._client.poll_all.return_value = {
            "doc:/test.md#0": [{"question": "What is segment 0?"}],
            "doc:/test.md#1": [{"question": "What is segment 1?"}],
            "doc:/test.md#2": [{"question": "What is segment 2?"}],
        }

        segments = _make_segments(3)
        results = gen.generate_batch(segments)

        # poll_all should have been called once
        gen._client.poll_all.assert_called_once()
        call_args = gen._client.poll_all.call_args[0][0]
        assert len(call_args) == 3

        # Results should map seg_key -> questions
        assert "doc:/test.md#0" in results
        assert results["doc:/test.md#0"] == ["What is segment 0?"]

        # task_ids should be stored for provenance
        assert "doc:/test.md#0" in gen.last_task_ids
        assert gen.last_task_ids["doc:/test.md#0"] == "task-1"

    def test_generate_batch_empty_segments(self):
        """generate_batch with empty input should return empty dict."""
        from knowledge_mining_zym.mining.stages.retrieval_units import LlmQuestionGenerator

        gen = LlmQuestionGenerator.__new__(LlmQuestionGenerator)
        gen._client = MagicMock()
        gen._timeout = 30
        gen._poll_interval = 1.0
        gen._status_error_limit = 5
        gen._cancel_checker = None
        gen._last_task_ids = {}
        gen._profile = None

        results = gen.generate_batch([])
        assert results == {}
        gen._client.poll_all.assert_not_called()


class TestContextualizerBatchPolling:
    @patch("knowledge_mining_zym.mining.stages.retrieval_units.LLMContextualizer.__init__", return_value=None)
    def test_contextualize_uses_poll_all(self, mock_init):
        """contextualize should call poll_all, not serial poll_result."""
        from knowledge_mining_zym.mining.stages.retrieval_units import LLMContextualizer

        ctxer = LLMContextualizer.__new__(LLMContextualizer)
        ctxer._client = MagicMock()
        ctxer._timeout = 30
        ctxer._poll_interval = 1.0
        ctxer._status_error_limit = 5
        ctxer._cancel_checker = None
        ctxer._last_task_ids = {}

        ctxer._client.submit_task.side_effect = ["task-a", "task-b"]
        ctxer._client.poll_all.return_value = {
            "doc:/test.md#0": [{"context": "Intro section of test document"}],
            "doc:/test.md#1": [{"context": "Body section covering details"}],
        }

        segments = _make_segments(2)
        results = ctxer.contextualize(segments, "Full document text here")

        ctxer._client.poll_all.assert_called_once()
        assert "doc:/test.md#0" in results
        assert results["doc:/test.md#0"] == "Intro section of test document"

        # task_ids stored for provenance
        assert ctxer.last_task_ids["doc:/test.md#0"] == "task-a"

    def test_contextualize_skips_empty_segments(self):
        """Empty segments should not be submitted."""
        from knowledge_mining_zym.mining.stages.retrieval_units import LLMContextualizer

        ctxer = LLMContextualizer.__new__(LLMContextualizer)
        ctxer._client = MagicMock()
        ctxer._timeout = 30
        ctxer._poll_interval = 1.0
        ctxer._status_error_limit = 5
        ctxer._cancel_checker = None
        ctxer._last_task_ids = {}

        segments = [
            RawSegmentData(document_key="doc:/a.md", segment_index=0, raw_text=""),
            RawSegmentData(document_key="doc:/a.md", segment_index=1, raw_text="  "),
        ]

        results = ctxer.contextualize(segments, "doc text")
        assert results == {}
        ctxer._client.submit_task.assert_not_called()


class TestLlmClientBoundedPollAll:
    def test_poll_all_timeout_returns_completed_results_and_cancels_pending(self):
        from knowledge_mining_zym.mining.infra.llm_client import LlmClient

        client = LlmClient.__new__(LlmClient)
        client.check_status = MagicMock(side_effect=lambda tid: "succeeded" if tid == "task-ok" else "running")
        client.fetch_result = MagicMock(return_value=[{"ok": True}])
        client.cancel_many = MagicMock(return_value=1)

        results = client.poll_all(
            {"ok": "task-ok", "slow": "task-slow"},
            poll_interval=0,
            timeout_seconds=0.01,
        )

        assert results == {"ok": [{"ok": True}]}
        client.cancel_many.assert_called_once()
        assert list(client.cancel_many.call_args[0][0]) == ["task-slow"]

    def test_poll_all_status_errors_abandon_task(self):
        from knowledge_mining_zym.mining.infra.llm_client import LlmClient

        client = LlmClient.__new__(LlmClient)
        client.check_status = MagicMock(return_value=None)
        client.fetch_result = MagicMock()
        client.cancel_many = MagicMock(return_value=1)

        results = client.poll_all(
            {"bad": "task-bad"},
            poll_interval=0,
            timeout_seconds=10,
            status_error_limit=2,
        )

        assert results == {}
        assert client.check_status.call_count == 2
        client.cancel_many.assert_called_once()

    def test_poll_all_cancel_checker_exits_and_cancels_pending(self):
        from knowledge_mining_zym.mining.infra.llm_client import LlmClient

        client = LlmClient.__new__(LlmClient)
        client.check_status = MagicMock()
        client.fetch_result = MagicMock()
        client.cancel_many = MagicMock(return_value=1)

        results = client.poll_all(
            {"pending": "task-pending"},
            poll_interval=0,
            timeout_seconds=10,
            cancel_checker=lambda: True,
        )

        assert results == {}
        client.check_status.assert_not_called()
        client.cancel_many.assert_called_once()


class TestEnrichBoundedPolling:
    def test_enrich_partial_llm_results_keep_unreturned_segments(self):
        from knowledge_mining_zym.mining.stages.enrich import LlmEnricher

        enricher = LlmEnricher.__new__(LlmEnricher)
        enricher._client = MagicMock()
        enricher._profile = None
        enricher._timeout_seconds = 30
        enricher._poll_interval = 0
        enricher._status_error_limit = 5
        enricher._cancel_checker = None

        enricher._client.submit_task.side_effect = ["task-0", "task-1"]
        enricher._client.poll_all.return_value = {
            "0": [{"semantic_role": "concept", "entities": []}],
        }

        segments = _make_segments(2)
        out = enricher.enrich_batch(segments)

        assert out[0].semantic_role == "concept"
        assert out[1] == segments[1]
        enricher._client.poll_all.assert_called_once()
