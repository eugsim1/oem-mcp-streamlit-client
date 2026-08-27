from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable

from .workspace import WorkspaceStore


class BackgroundJobManager:
    """Small in-process executor with durable, redacted job state."""

    def __init__(self, store: WorkspaceStore, max_workers: int = 2) -> None:
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, 8)), thread_name_prefix="oem-mcp-job")
        self._futures: dict[int, Future[Any]] = {}
        self._lock = Lock()

    def submit(self, kind: str, name: str, payload: Any, function: Callable[[], Any]) -> int:
        job_id = self.store.create_job(kind, name, payload)

        def wrapped() -> Any:
            self.store.update_job(job_id, "running")
            try:
                result = function()
            except Exception as exc:
                self.store.update_job(job_id, "failed", error=type(exc).__name__)
                raise
            self.store.update_job(job_id, "success", result=result)
            return result

        with self._lock:
            self._futures[job_id] = self.executor.submit(wrapped)
        return job_id

    def status(self, job_id: int) -> str:
        with self._lock:
            future = self._futures.get(job_id)
        if future is None:
            return "persisted"
        if future.cancelled():
            return "cancelled"
        if future.done():
            return "failed" if future.exception() else "success"
        return "running"

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
        if future and future.cancel():
            self.store.update_job(job_id, "cancelled")
            return True
        return False

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
