"""In-process queue for evaluation runs."""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock


class EvaluationQueueService:
    def __init__(self, *, store, evaluation_service, max_workers: int = 1):
        self.store = store
        self.evaluation_service = evaluation_service
        self.max_workers = max(1, int(max_workers or 1))
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="telecom-eval")
        self._futures: set[Future] = set()
        self._lock = Lock()

    def enqueue(self, run_id: str) -> None:
        future = self._executor.submit(self._run_one, run_id)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._discard_future)

    def recover_pending_runs(self) -> None:
        for run in self.store.list_runs(limit=1000):
            if run["status"] == "queued":
                self.enqueue(run["run_id"])
            elif run["status"] == "running":
                self.store.update_run_status(run["run_id"], "failed", completed_at=None)

    def wait_for_idle(self, timeout: float = 30.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                futures = list(self._futures)
            if not futures:
                return
            for future in futures:
                future.result(timeout=max(0.01, deadline - time.time()))
        raise TimeoutError("evaluation queue did not become idle")

    def _run_one(self, run_id: str) -> None:
        self.evaluation_service.execute_queued_run(run_id)

    def _discard_future(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)
