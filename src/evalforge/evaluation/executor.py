"""Replaceable persisted local run executor."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from evalforge.evaluation.service import EvaluationService
from evalforge.observability import get_logger


class RunExecutor(Protocol):
    async def start(self) -> None: ...

    async def submit(self, run_id: str) -> None: ...

    async def close(self) -> None: ...


class LocalRunExecutor:
    """Claim queued database runs through one in-process FIFO worker."""

    def __init__(self, service: EvaluationService) -> None:
        self.service = service
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        self._queued_ids: set[str] = set()

    async def start(self) -> None:
        if self._worker is not None:
            return
        self.service.recover_interrupted()
        for run_id in self.service.pending_run_ids():
            await self._enqueue_once(run_id)
        self._worker = asyncio.create_task(self._run(), name="evalforge-local-run-worker")

    async def submit(self, run_id: str) -> None:
        if self._closed:
            raise RuntimeError("run executor is closed")
        await self._enqueue_once(run_id)

    async def close(self) -> None:
        self._closed = True
        if self._worker is None:
            return
        self._worker.cancel()
        with suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None

    async def wait_idle(self) -> None:
        """Wait until queued work completes; intended for deterministic tests and CLI use."""
        await self._queue.join()

    @property
    def healthy(self) -> bool:
        """Report whether the single local worker can still accept queued work."""
        return not self._closed and self._worker is not None and not self._worker.done()

    async def _enqueue_once(self, run_id: str) -> None:
        if run_id in self._queued_ids:
            return
        self._queued_ids.add(run_id)
        await self._queue.put(run_id)

    async def _run(self) -> None:
        logger = get_logger("run_executor")
        while True:
            run_id = await self._queue.get()
            try:
                await self.service.execute_run(run_id)
            except Exception as exc:
                logger.error(
                    "run_execution_unhandled",
                    run_id=run_id,
                    error_type=type(exc).__name__,
                )
            finally:
                self._queued_ids.discard(run_id)
                self._queue.task_done()


async def run_with_executor(
    executor: LocalRunExecutor,
    work: Callable[[LocalRunExecutor], Awaitable[None]],
) -> None:
    """Small lifecycle helper for integration tests and command-line workflows."""
    await executor.start()
    try:
        await work(executor)
    finally:
        await executor.close()
