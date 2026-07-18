"""Database-backed run claims with renewable ownership and fenced writes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from evalforge.database import SessionFactory, session_scope
from evalforge.models import EvaluationRun, ExecutionAttempt, RunStatus


class LeaseLostError(RuntimeError):
    """Raised when a stale worker attempts to mutate work it no longer owns."""


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    """Opaque ownership evidence that must accompany every worker mutation."""

    run_id: str
    workspace_id: str
    worker_id: str
    token: str
    epoch: int
    expires_at: datetime
    takeover: bool


class LeaseManager:
    """Claim, renew, fence, and close persisted execution attempts."""

    def __init__(self, session_factory: SessionFactory, *, lease_seconds: int) -> None:
        if not 10 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 10 and 3600")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds

    def claim_next(self, worker_id: str, *, run_id: str | None = None) -> LeaseClaim | None:
        """Atomically claim the oldest eligible run, optionally restricted by ID."""

        normalized_worker = _bounded_worker_id(worker_id)
        with session_scope(self.session_factory) as session:
            now = _database_now(session)
            eligible = _eligible_clause(now)
            statement = (
                select(
                    EvaluationRun.id,
                    EvaluationRun.workspace_id,
                    EvaluationRun.status,
                    EvaluationRun.lease_token,
                    EvaluationRun.lease_epoch,
                )
                .where(eligible)
                .order_by(EvaluationRun.next_claim_at, EvaluationRun.queued_at, EvaluationRun.id)
                .limit(16)
            )
            if run_id is not None:
                statement = statement.where(EvaluationRun.id == run_id)
            candidates = session.execute(statement).all()
            for candidate in candidates:
                token = uuid.uuid4().hex
                expires_at = now + timedelta(seconds=self.lease_seconds)
                claimed = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(EvaluationRun)
                        .where(
                            EvaluationRun.id == candidate.id,
                            EvaluationRun.lease_epoch == candidate.lease_epoch,
                            _eligible_clause(now),
                        )
                        .values(
                            lease_owner=normalized_worker,
                            lease_token=token,
                            lease_epoch=EvaluationRun.lease_epoch + 1,
                            lease_expires_at=expires_at,
                            claim_attempts=EvaluationRun.claim_attempts + 1,
                            heartbeat_at=now,
                        )
                    ),
                )
                if claimed.rowcount != 1:
                    session.expire_all()
                    continue
                epoch = int(candidate.lease_epoch) + 1
                if candidate.lease_token is not None:
                    session.execute(
                        update(ExecutionAttempt)
                        .where(
                            ExecutionAttempt.run_id == candidate.id,
                            ExecutionAttempt.lease_token == candidate.lease_token,
                            ExecutionAttempt.finished_at.is_(None),
                        )
                        .values(
                            heartbeat_at=now,
                            finished_at=now,
                            outcome="lease_expired",
                            error_type="LeaseExpired",
                        )
                    )
                session.add(
                    ExecutionAttempt(
                        workspace_id=str(candidate.workspace_id),
                        run_id=str(candidate.id),
                        lease_owner=normalized_worker,
                        lease_token=token,
                        lease_epoch=epoch,
                        started_at=now,
                        heartbeat_at=now,
                    )
                )
                session.flush()
                return LeaseClaim(
                    run_id=str(candidate.id),
                    workspace_id=str(candidate.workspace_id),
                    worker_id=normalized_worker,
                    token=token,
                    epoch=epoch,
                    expires_at=expires_at,
                    takeover=(
                        candidate.lease_token is not None
                        or RunStatus(candidate.status) is not RunStatus.QUEUED
                    ),
                )
        return None

    def renew(self, claim: LeaseClaim) -> LeaseClaim:
        """Extend one active claim and its attempt heartbeat."""

        with session_scope(self.session_factory) as session:
            now = _database_now(session)
            expires_at = now + timedelta(seconds=self.lease_seconds)
            renewed = cast(
                "CursorResult[Any]",
                session.execute(
                    update(EvaluationRun)
                    .where(*_claim_predicates(claim), EvaluationRun.lease_expires_at > now)
                    .values(lease_expires_at=expires_at, heartbeat_at=now)
                ),
            )
            if renewed.rowcount != 1:
                raise LeaseLostError("run lease is no longer active")
            session.execute(
                update(ExecutionAttempt)
                .where(
                    ExecutionAttempt.run_id == claim.run_id,
                    ExecutionAttempt.lease_token == claim.token,
                    ExecutionAttempt.lease_epoch == claim.epoch,
                    ExecutionAttempt.finished_at.is_(None),
                )
                .values(heartbeat_at=now)
            )
        return LeaseClaim(
            run_id=claim.run_id,
            workspace_id=claim.workspace_id,
            worker_id=claim.worker_id,
            token=claim.token,
            epoch=claim.epoch,
            expires_at=expires_at,
            takeover=claim.takeover,
        )

    def fence(self, session: Session, claim: LeaseClaim) -> datetime:
        """Acquire the run row for this transaction only if ownership is current."""

        now = _database_now(session)
        fenced = cast(
            "CursorResult[Any]",
            session.execute(
                update(EvaluationRun)
                .where(*_claim_predicates(claim), EvaluationRun.lease_expires_at > now)
                .values(heartbeat_at=EvaluationRun.heartbeat_at)
            ),
        )
        if fenced.rowcount != 1:
            raise LeaseLostError("run lease ownership was lost")
        return now

    def finish(
        self,
        claim: LeaseClaim,
        *,
        outcome: str,
        error_type: str | None = None,
    ) -> bool:
        """Close an attempt and release only the matching active lease."""

        with session_scope(self.session_factory) as session:
            now = _database_now(session)
            released = cast(
                "CursorResult[Any]",
                session.execute(
                    update(EvaluationRun)
                    .where(*_claim_predicates(claim), EvaluationRun.lease_expires_at > now)
                    .values(
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        heartbeat_at=now,
                    )
                ),
            )
            released_active_lease = released.rowcount == 1
            session.execute(
                update(ExecutionAttempt)
                .where(
                    ExecutionAttempt.run_id == claim.run_id,
                    ExecutionAttempt.lease_token == claim.token,
                    ExecutionAttempt.lease_epoch == claim.epoch,
                    ExecutionAttempt.finished_at.is_(None),
                )
                .values(
                    heartbeat_at=now,
                    finished_at=now,
                    outcome=(outcome if released_active_lease else "lease_lost")[:40],
                    error_type=(
                        (error_type[:100] if error_type else None)
                        if released_active_lease
                        else "LeaseLostError"
                    ),
                )
            )
            return released_active_lease

    def close_attempt(
        self,
        claim: LeaseClaim,
        *,
        outcome: str,
        error_type: str | None = None,
    ) -> bool:
        """Close attempt evidence without releasing a possibly ambiguous active lease.

        A worker that cannot renew must stop writing immediately, but clearing the run
        lease would make an active run ineligible for the normal expiry/takeover path.
        The run therefore keeps the same fenced lease until its recorded deadline while
        the execution attempt is finalized independently.
        """

        with session_scope(self.session_factory) as session:
            now = _database_now(session)
            closed = cast(
                "CursorResult[Any]",
                session.execute(
                    update(ExecutionAttempt)
                    .where(
                        ExecutionAttempt.run_id == claim.run_id,
                        ExecutionAttempt.lease_token == claim.token,
                        ExecutionAttempt.lease_epoch == claim.epoch,
                        ExecutionAttempt.finished_at.is_(None),
                    )
                    .values(
                        heartbeat_at=now,
                        finished_at=now,
                        outcome=outcome[:40],
                        error_type=error_type[:100] if error_type else None,
                    )
                ),
            )
            return closed.rowcount == 1


def _eligible_clause(now: datetime) -> ColumnElement[bool]:
    unclaimed_or_expired = or_(
        EvaluationRun.lease_token.is_(None),
        EvaluationRun.lease_expires_at.is_(None),
        EvaluationRun.lease_expires_at <= now,
    )
    queued = and_(
        EvaluationRun.status == RunStatus.QUEUED,
        EvaluationRun.next_claim_at <= now,
        unclaimed_or_expired,
    )
    abandoned_active = and_(
        EvaluationRun.status.in_([RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED]),
        EvaluationRun.lease_token.is_not(None),
        EvaluationRun.lease_expires_at.is_not(None),
        EvaluationRun.lease_expires_at <= now,
    )
    return or_(queued, abandoned_active)


def _claim_predicates(claim: LeaseClaim) -> tuple[ColumnElement[bool], ...]:
    return (
        EvaluationRun.id == claim.run_id,
        EvaluationRun.workspace_id == claim.workspace_id,
        EvaluationRun.lease_owner == claim.worker_id,
        EvaluationRun.lease_token == claim.token,
        EvaluationRun.lease_epoch == claim.epoch,
    )


def _database_now(session: Session) -> datetime:
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        raw_value = session.scalar(select(func.strftime("%Y-%m-%d %H:%M:%f", "now")))
        if not isinstance(raw_value, str):
            raise RuntimeError("SQLite did not return a timestamp")
        return datetime.fromisoformat(raw_value).replace(tzinfo=UTC)
    value = session.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        raise RuntimeError("database did not return a timestamp")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bounded_worker_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("worker_id must contain between 1 and 200 characters")
    return normalized
