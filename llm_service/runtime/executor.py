from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from llm_service.db import LlmRuntimeDB
from llm_service.providers.base import ProviderError, ProviderProtocol
from llm_service.runtime.event_bus import EventBus
from llm_service.runtime.parser import ParseResult, parse_output
from llm_service.runtime.task_manager import TaskManager


class Executor:
    def __init__(
        self,
        db: LlmRuntimeDB,
        task_manager: TaskManager,
        event_bus: EventBus,
        provider: ProviderProtocol,
    ):
        self._db = db
        self._mgr = task_manager
        self._bus = event_bus
        self._provider = provider

    async def _reclaim(self, task_id: str) -> bool:
        """Re-claim a queued task for this executor. Returns False if Worker already took it."""
        now = datetime.now(timezone.utc)
        lease_dt = now + timedelta(seconds=300)
        row = await self._db.fetchone(
            """UPDATE agent_llm_tasks
               SET status = 'running', lease_expires_at = %s, updated_at = %s
               WHERE id = %s AND status = 'queued'
               RETURNING id""",
            (lease_dt.isoformat(), now.isoformat(), task_id),
        )
        return row is not None

    async def run(
        self,
        task_id: str,
        messages: list[dict],
        params: dict,
        expected_type: str = "json_object",
        schema: dict | None = None,
    ) -> ParseResult | None:
        """Execute task with retry loop. Returns ParseResult on success, None on exhaustion."""
        # Get or create request row for this task
        req_row = await self._db.fetchone("SELECT id FROM agent_llm_requests WHERE task_id = %s", (task_id,))
        request_id = req_row["id"] if req_row else ""

        while True:
            task_row = await self._db.fetchone("SELECT attempt_count FROM agent_llm_tasks WHERE id = %s", (task_id,))
            if task_row is None:
                return None
            attempt_no = task_row["attempt_count"] + 1

            attempt_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """INSERT INTO agent_llm_attempts
                   (id, task_id, request_id, attempt_no, status, started_at)
                   VALUES (%s, %s, %s, %s, 'running', %s)""",
                (attempt_id, task_id, request_id, attempt_no, now),
            )

            start = time.monotonic()
            try:
                # Build response_format hint from expected_type
                response_format = (
                    {"type": "json_object"}
                    if expected_type in ("json_object", "json_array")
                    else None
                )
                resp = await self._provider.complete(
                    messages=messages, params=params,
                    response_format=response_format,
                )
                latency = int((time.monotonic() - start) * 1000)
                finished = datetime.now(timezone.utc).isoformat()

                await self._db.execute(
                    """UPDATE agent_llm_attempts
                       SET status = 'succeeded', raw_output_text = %s, prompt_tokens = %s,
                           completion_tokens = %s, total_tokens = %s, latency_ms = %s, finished_at = %s,
                           raw_response_json = %s
                       WHERE id = %s""",
                    (
                        resp.output_text, resp.prompt_tokens, resp.completion_tokens,
                        resp.total_tokens, latency, finished,
                        json.dumps(resp.raw_response or {}), attempt_id,
                    ),
                )

                parse_result = parse_output(resp.output_text, expected_type, schema)

                result_id = str(uuid.uuid4())
                await self._db.execute(
                    """INSERT INTO agent_llm_results
                       (id, task_id, attempt_id, parse_status, parsed_output_json, text_output,
                        parse_error, validation_errors_json, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        result_id, task_id, attempt_id, parse_result.parse_status,
                        json.dumps(parse_result.parsed_output if parse_result.parsed_output is not None else {}),
                        parse_result.text_output, parse_result.parse_error,
                        json.dumps(parse_result.validation_errors),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

                await self._mgr.complete(task_id)
                return parse_result

            except ProviderError as e:
                latency = int((time.monotonic() - start) * 1000)
                finished = datetime.now(timezone.utc).isoformat()
                await self._db.execute(
                    """UPDATE agent_llm_attempts
                       SET status = 'failed', error_type = %s, error_message = %s, latency_ms = %s, finished_at = %s
                       WHERE id = %s""",
                    (e.error_type, e.message, latency, finished, attempt_id),
                )

                t = await self._db.fetchone("SELECT max_attempts FROM agent_llm_tasks WHERE id = %s", (task_id,))
                if t is None:
                    return None
                if attempt_no >= t["max_attempts"]:
                    await self._mgr.fail(task_id, e.error_type, e.message)
                    return None
                else:
                    await self._mgr.fail(task_id, e.error_type, e.message)
                    row = await self._db.fetchone("SELECT available_at FROM agent_llm_tasks WHERE id = %s", (task_id,))
                    if row is None:
                        return None
                    available_at = row["available_at"]
                    if isinstance(available_at, str):
                        available_at = datetime.fromisoformat(available_at)
                    delay = (available_at - datetime.now(timezone.utc)).total_seconds()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if not await self._reclaim(task_id):
                        return None
            except Exception as e:
                latency = int((time.monotonic() - start) * 1000)
                finished = datetime.now(timezone.utc).isoformat()
                await self._db.execute(
                    """UPDATE agent_llm_attempts
                       SET status = 'failed', error_type = %s, error_message = %s, latency_ms = %s, finished_at = %s
                       WHERE id = %s""",
                    ("unexpected_error", str(e), latency, finished, attempt_id),
                )

                t = await self._db.fetchone("SELECT max_attempts FROM agent_llm_tasks WHERE id = %s", (task_id,))
                if t is None:
                    return None
                if attempt_no >= t["max_attempts"]:
                    await self._mgr.fail(task_id, "unexpected_error", str(e))
                    return None
                else:
                    await self._mgr.fail(task_id, "unexpected_error", str(e))
                    row = await self._db.fetchone("SELECT available_at FROM agent_llm_tasks WHERE id = %s", (task_id,))
                    if row is None:
                        return None
                    available_at = row["available_at"]
                    if isinstance(available_at, str):
                        available_at = datetime.fromisoformat(available_at)
                    delay = (available_at - datetime.now(timezone.utc)).total_seconds()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if not await self._reclaim(task_id):
                        return None
