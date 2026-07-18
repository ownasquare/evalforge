"""Database-discovered execution workers and API-only signaling."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from evalforge.evaluation.service import EvaluationService
from evalforge.observability import get_logger


class RunExecutor(Protocol):
    async def start(self) -> None: ...

    async def submit(self, run_id: str) -> None: ...

    async def close(self) -> None: ...

    @property
    def healthy(self) -> bool: ...

    @property
    def role(self) -> str: ...

    @property
    def worker_observed(self) -> bool: ...


class LocalRunExecutor:
    """Poll and claim committed database work through one embedded worker."""

    def __init__(
        self,
        service: EvaluationService,
        *,
        poll_interval_seconds: float = 0.5,
        worker_id: str | None = None,
        role: str = "embedded_single",
    ) -> None:
        if not 0.1 <= poll_interval_seconds <= 30:
            raise ValueError("poll interval must be between 0.1 and 30 seconds")
        self.service = service
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex}"
        self._role = role
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        self._busy = False

    async def start(self) -> None:
        if self._worker is not None:
            return
        if self._closed:
            raise RuntimeError("run executor is closed")
        self._worker = asyncio.create_task(
            self._run(),
            name=f"evalforge-database-worker-{self.worker_id}",
        )
        self._wake.set()

    async def submit(self, run_id: str) -> None:
        """Accelerate polling; the committed database row remains queue authority."""

        del run_id
        if self._closed:
            raise RuntimeError("run executor is closed")
        self._wake.set()

    async def close(self) -> None:
        self._closed = True
        self._wake.set()
        if self._worker is None:
            return
        worker = self._worker
        done, _pending = await asyncio.wait({worker}, timeout=2)
        if not done:
            worker.cancel()
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(worker, timeout=2)
        self._worker = None

    async def wait_idle(self) -> None:
        """Wait for this worker and all currently queued persisted work."""

        while self._busy or self.service.pending_run_ids():
            self._wake.set()
            await asyncio.sleep(min(self.poll_interval_seconds, 0.1))

    @property
    def healthy(self) -> bool:
        return not self._closed and self._worker is not None and not self._worker.done()

    @property
    def role(self) -> str:
        return self._role

    @property
    def worker_observed(self) -> bool:
        return True

    async def _run(self) -> None:
        logger = get_logger("run_executor")
        while not self._closed:
            claim = self.service.claim_next(self.worker_id)
            if claim is None:
                self._wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                continue
            self._busy = True
            try:
                await self.service.execute_run(claim.run_id, claim)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "run_execution_unhandled",
                    run_id=claim.run_id,
                    error_type=type(exc).__name__,
                )
            finally:
                self._busy = False


class ApiOnlyRunExecutor:
    """Accept persisted submissions while leaving execution to another process."""

    def __init__(self) -> None:
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("run executor is closed")
        self._started = True

    async def submit(self, run_id: str) -> None:
        del run_id
        if self._closed:
            raise RuntimeError("run executor is closed")

    async def close(self) -> None:
        self._closed = True

    @property
    def healthy(self) -> bool:
        return self._started and not self._closed

    @property
    def role(self) -> str:
        return "api_only"

    @property
    def worker_observed(self) -> bool:
        """API-only mode cannot infer the health of a separate worker process."""

        return False


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
