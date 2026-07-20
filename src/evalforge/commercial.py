"""Minimal server-authoritative contracts for the hosted commercialization pilot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalforge.audit import AuditRecorder
from evalforge.config import Settings
from evalforge.errors import (
    CapabilityError,
    ConflictError,
    EvalForgeError,
    LimitError,
    NotFoundError,
)
from evalforge.models import (
    ActivationEvent,
    ActivationEventName,
    BillingEvent,
    EntitlementStatus,
    EvaluationRun,
    PlanCode,
    RecordStatus,
    TeamPilotRequest,
    TeamPilotRequestStatus,
    User,
    Workspace,
    WorkspaceEntitlement,
    WorkspaceMembership,
    canonical_json_hash,
    utc_now,
)
from evalforge.schemas import (
    ClientActivationEventCreate,
    TeamPilotRequestCreate,
    WorkspaceEntitlementRead,
)
from evalforge.security.permissions import (
    AuthorizationError,
    WorkspaceContext,
    WorkspaceRole,
    role_allows,
)

_CLIENT_EVENT_NAMES: Final = frozenset(
    {
        ActivationEventName.LANDING,
        ActivationEventName.SIGNUP,
        ActivationEventName.UPGRADE_VIEW,
    }
)
_MAX_CLIENT_EVENTS_PER_ACTOR_PER_DAY: Final = 100
_MAX_HISTORY_ROWS: Final = 100
_FORBIDDEN_METADATA_FRAGMENTS: Final = (
    "token",
    "secret",
    "password",
    "authorization",
    "prompt",
    "context",
    "output",
    "email",
    "subject",
)


class EntitlementRequiredError(EvalForgeError):
    """A hosted workspace cannot start new work without active access."""

    def __init__(self) -> None:
        super().__init__(
            "entitlement_required",
            "Start or renew hosted workspace access before creating another evaluation.",
            status_code=402,
        )


def commercial_plan_catalog(settings: Settings) -> list[dict[str, object]]:
    """Return the intentionally small OSS-versus-hosted pilot offer."""

    hosted_available = settings.auth_mode == "oidc" and settings.commercial_pilot_enabled
    return [
        {
            "code": PlanCode.OPEN_SOURCE,
            "name": "Community self-hosted",
            "audience": "Individual builders and teams that operate EvalForge themselves.",
            "price_label": "Free and open source",
            "features": [
                "Complete deterministic evaluation workflow",
                "Local persistence and exports",
                "Bring your own infrastructure and model providers",
            ],
            "self_hosted": True,
            "available": True,
        },
        {
            "code": PlanCode.HOSTED_TRIAL,
            "name": f"Hosted team trial · {settings.hosted_trial_days} days",
            "audience": "Small AI engineering and product teams testing a shared workflow.",
            "price_label": "Invitation pilot",
            "features": [
                "No-install managed workspace",
                "Managed persistence",
                f"Up to {settings.hosted_trial_seat_limit} team seats",
                "Pilot support",
            ],
            "self_hosted": False,
            "available": hosted_available,
        },
        {
            "code": PlanCode.TEAM,
            "name": "Hosted team",
            "audience": "Teams with recurring evaluation, security, and support needs.",
            "price_label": "Team pilot request",
            "features": [
                "Shared hosted workspace",
                "Managed persistence and team access",
                "Security review and onboarding support",
                "Server-authoritative access",
            ],
            "self_hosted": False,
            "available": hosted_available,
        },
    ]


class ActivationRecorder:
    """Write deduplicated, content-minimized funnel events in a caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        workspace_id: str,
        actor_user_id: str | None,
        name: ActivationEventName,
        event_key: str,
        source: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[ActivationEvent, bool]:
        safe_key = _bounded_identifier(event_key, label="event key", maximum=255)
        safe_source = _bounded_slug(source, label="event source")
        safe_metadata = _safe_metadata(metadata)
        if run_id is not None:
            matching_run = self.session.scalar(
                select(EvaluationRun.id).where(
                    EvaluationRun.id == run_id,
                    EvaluationRun.workspace_id == workspace_id,
                )
            )
            if matching_run is None:
                raise NotFoundError("Evaluation run")
        existing = self.session.scalar(
            select(ActivationEvent).where(
                ActivationEvent.workspace_id == workspace_id,
                ActivationEvent.event_key == safe_key,
            )
        )
        if existing is not None:
            expected = (
                name,
                actor_user_id,
                safe_source,
                run_id,
                safe_metadata,
            )
            actual = (
                existing.name,
                existing.actor_user_id,
                existing.source,
                existing.run_id,
                dict(existing.metadata_json),
            )
            if actual != expected:
                raise ConflictError("Activation event key was already used for another event.")
            return existing, False
        event = ActivationEvent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            name=name,
            event_key=safe_key,
            source=safe_source,
            run_id=run_id,
            metadata_json=safe_metadata,
        )
        self.session.add(event)
        self.session.flush()
        return event, True

    def record_result_engagement_after_qualifying_completion(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        run_id: str,
        source: str,
    ) -> tuple[ActivationEvent, bool] | None:
        """Record export engagement only after this actor completed the run."""

        qualifying_completion = self.session.scalar(
            select(ActivationEvent.id).where(
                ActivationEvent.workspace_id == workspace_id,
                ActivationEvent.actor_user_id == actor_user_id,
                ActivationEvent.run_id == run_id,
                ActivationEvent.name == ActivationEventName.EVALUATION_COMPLETE,
            )
        )
        if qualifying_completion is None:
            return None
        return self.record(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            name=ActivationEventName.RESULT_ENGAGEMENT,
            event_key=f"result-engagement:{run_id}:{actor_user_id}",
            source=source,
            run_id=run_id,
            metadata={"surface": source},
        )


class CommercialPilotService:
    """Commercial state transitions scoped to one authenticated workspace."""

    def __init__(
        self,
        session: Session,
        context: WorkspaceContext,
        settings: Settings,
    ) -> None:
        self.session = session
        self.context = context
        self.settings = settings

    def entitlement(self, *, at: datetime | None = None) -> WorkspaceEntitlementRead:
        now = _aware_utc(at or utc_now())
        row = self.session.scalar(
            select(WorkspaceEntitlement).where(
                WorkspaceEntitlement.workspace_id == self.context.workspace_id
            )
        )
        active_memberships = int(
            self.session.scalar(
                select(func.count())
                .select_from(WorkspaceMembership)
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(
                    WorkspaceMembership.workspace_id == self.context.workspace_id,
                    WorkspaceMembership.status == RecordStatus.ACTIVE,
                    User.status == RecordStatus.ACTIVE,
                )
            )
            or 0
        )
        hosted = self.settings.auth_mode == "oidc"
        if row is None:
            can_start = not hosted or not self.settings.commercial_pilot_enabled
            return WorkspaceEntitlementRead(
                workspace_id=self.context.workspace_id,
                plan_code=PlanCode.OPEN_SOURCE,
                status=EntitlementStatus.ACTIVE,
                seat_limit=max(1, active_memberships),
                active_memberships=active_memberships,
                source="oss_self_hosted" if not hosted else "hosted_trial_not_started",
                current_period_start=None,
                current_period_end=None,
                can_start_runs=can_start,
                hosted=hosted,
                commercial_pilot_enabled=self.settings.commercial_pilot_enabled,
            )
        effective_status = _effective_status(row, now)
        active = (
            effective_status in {EntitlementStatus.TRIALING, EntitlementStatus.ACTIVE}
            and active_memberships <= row.seat_limit
        )
        return WorkspaceEntitlementRead(
            workspace_id=row.workspace_id,
            plan_code=row.plan_code,
            status=effective_status,
            seat_limit=row.seat_limit,
            active_memberships=active_memberships,
            source=row.source,
            current_period_start=_aware_utc(row.current_period_start),
            current_period_end=(
                _aware_utc(row.current_period_end) if row.current_period_end is not None else None
            ),
            can_start_runs=(active or not hosted or not self.settings.commercial_pilot_enabled),
            hosted=hosted,
            commercial_pilot_enabled=self.settings.commercial_pilot_enabled,
        )

    def start_trial(
        self, *, idempotency_key: str, request_id: str | None
    ) -> WorkspaceEntitlementRead:
        self._require_hosted_pilot()
        _lock_workspace(self.session, self.context, required_role=WorkspaceRole.ADMIN)
        safe_key = _bounded_identifier(
            idempotency_key,
            label="idempotency key",
            maximum=128,
        )
        event_id = f"trial-start:{self.context.workspace_id}:{safe_key}"
        if self._billing_event(event_id) is not None:
            return self.entitlement()
        existing = self.session.scalar(
            select(WorkspaceEntitlement).where(
                WorkspaceEntitlement.workspace_id == self.context.workspace_id
            )
        )
        now = utc_now()
        if existing is not None:
            if _effective_status(existing, now) is EntitlementStatus.TRIALING:
                return self.entitlement(at=now)
            raise ConflictError("This workspace has already used or replaced its hosted trial.")
        entitlement = WorkspaceEntitlement(
            workspace_id=self.context.workspace_id,
            plan_code=PlanCode.HOSTED_TRIAL,
            status=EntitlementStatus.TRIALING,
            seat_limit=self.settings.hosted_trial_seat_limit,
            source="self_service_hosted_trial",
            current_period_start=now,
            current_period_end=now + timedelta(days=self.settings.hosted_trial_days),
            activated_by_user_id=self.context.user_id,
        )
        self.session.add(entitlement)
        self.session.flush()
        self._record_billing_event(
            provider_event_id=event_id,
            event_type="entitlement.trial_activated",
            metadata={
                "plan_code": PlanCode.HOSTED_TRIAL.value,
                "seat_limit": entitlement.seat_limit,
                "trial_days": self.settings.hosted_trial_days,
            },
        )
        ActivationRecorder(self.session).record(
            workspace_id=self.context.workspace_id,
            actor_user_id=self.context.user_id,
            name=ActivationEventName.ENTITLEMENT_ACTIVATION,
            event_key=f"entitlement-activation:{entitlement.id}",
            source="hosted_trial",
            metadata={"surface": "settings", "plan_code": PlanCode.HOSTED_TRIAL.value},
        )
        AuditRecorder(self.session).record(
            self.context,
            action="commercial.trial.start",
            resource_type="workspace_entitlement",
            resource_id=entitlement.id,
            outcome="success",
            request_id=request_id,
            metadata={"plan_code": PlanCode.HOSTED_TRIAL.value},
        )
        return self.entitlement(at=now)

    def cancel_trial(
        self,
        *,
        idempotency_key: str,
        request_id: str | None,
    ) -> WorkspaceEntitlementRead:
        self._require_hosted_pilot()
        _lock_workspace(self.session, self.context, required_role=WorkspaceRole.ADMIN)
        safe_key = _bounded_identifier(
            idempotency_key,
            label="idempotency key",
            maximum=128,
        )
        event_id = f"trial-cancel:{self.context.workspace_id}:{safe_key}"
        if self._billing_event(event_id) is not None:
            return self.entitlement()
        entitlement = self.session.scalar(
            select(WorkspaceEntitlement).where(
                WorkspaceEntitlement.workspace_id == self.context.workspace_id
            )
        )
        if entitlement is None or entitlement.plan_code is not PlanCode.HOSTED_TRIAL:
            raise ConflictError("No hosted trial is available to cancel.")
        if entitlement.status is EntitlementStatus.CANCELED:
            return self.entitlement()
        now = utc_now()
        entitlement.status = EntitlementStatus.CANCELED
        entitlement.current_period_end = now
        self.session.flush()
        self._record_billing_event(
            provider_event_id=event_id,
            event_type="entitlement.trial_canceled",
            metadata={"plan_code": entitlement.plan_code.value},
        )
        AuditRecorder(self.session).record(
            self.context,
            action="commercial.trial.cancel",
            resource_type="workspace_entitlement",
            resource_id=entitlement.id,
            outcome="success",
            request_id=request_id,
            metadata={"plan_code": entitlement.plan_code.value},
        )
        return self.entitlement(at=now)

    def create_team_request(
        self,
        data: TeamPilotRequestCreate,
        *,
        idempotency_key: str,
        request_id: str | None,
    ) -> tuple[TeamPilotRequest, bool]:
        self._require_hosted_pilot()
        _lock_workspace(self.session, self.context, required_role=WorkspaceRole.ADMIN)
        safe_key = _bounded_identifier(
            idempotency_key,
            label="idempotency key",
            maximum=128,
        )
        existing = self.session.scalar(
            select(TeamPilotRequest).where(
                TeamPilotRequest.workspace_id == self.context.workspace_id,
                TeamPilotRequest.idempotency_key == safe_key,
            )
        )
        if existing is not None:
            expected = (
                data.requested_seats,
                data.evaluation_frequency,
                data.security_review_required,
            )
            actual = (
                existing.requested_seats,
                existing.evaluation_frequency,
                existing.security_review_required,
            )
            if actual != expected:
                raise ConflictError("Idempotency key was already used for another team request.")
            return existing, False
        pending_request = self.session.scalar(
            select(TeamPilotRequest.id).where(
                TeamPilotRequest.workspace_id == self.context.workspace_id,
                TeamPilotRequest.status == TeamPilotRequestStatus.PENDING,
            )
        )
        if pending_request is not None:
            raise ConflictError("Cancel the pending team request before creating another one.")
        if self._activated_run_count() < 1:
            raise ConflictError(
                "Complete an evaluation and engage with its result before requesting a team pilot."
            )
        request = TeamPilotRequest(
            workspace_id=self.context.workspace_id,
            requested_by_user_id=self.context.user_id,
            requested_seats=data.requested_seats,
            evaluation_frequency=data.evaluation_frequency,
            security_review_required=data.security_review_required,
            status=TeamPilotRequestStatus.PENDING,
            idempotency_key=safe_key,
        )
        self.session.add(request)
        self.session.flush()
        self._record_billing_event(
            provider_event_id=f"team-request:{request.id}",
            event_type="team_request.created",
            metadata={
                "requested_seats": request.requested_seats,
                "evaluation_frequency": request.evaluation_frequency.value,
                "security_review_required": request.security_review_required,
            },
        )
        ActivationRecorder(self.session).record(
            workspace_id=self.context.workspace_id,
            actor_user_id=self.context.user_id,
            name=ActivationEventName.TEAM_REQUEST_SUBMITTED,
            event_key=f"team-request-submitted:{request.id}",
            source="team_request",
            metadata={"surface": "settings"},
        )
        AuditRecorder(self.session).record(
            self.context,
            action="commercial.team_request.create",
            resource_type="team_pilot_request",
            resource_id=request.id,
            outcome="success",
            request_id=request_id,
            metadata={"requested_seats": request.requested_seats},
        )
        return request, True

    def cancel_team_request(
        self,
        request_id_value: str,
        *,
        idempotency_key: str,
        request_id: str | None,
    ) -> TeamPilotRequest:
        self._require_hosted_pilot()
        _lock_workspace(self.session, self.context, required_role=WorkspaceRole.ADMIN)
        request = self.session.scalar(
            select(TeamPilotRequest).where(
                TeamPilotRequest.id == request_id_value,
                TeamPilotRequest.workspace_id == self.context.workspace_id,
            )
        )
        if request is None:
            raise NotFoundError("Team pilot request")
        safe_key = _bounded_identifier(
            idempotency_key,
            label="idempotency key",
            maximum=128,
        )
        event_id = f"team-request-cancel:{self.context.workspace_id}:{request.id}:{safe_key}"
        if self._billing_event(event_id) is not None:
            return request
        if request.status is not TeamPilotRequestStatus.PENDING:
            raise ConflictError("Only a pending team request can be canceled.")
        request.status = TeamPilotRequestStatus.CANCELED
        request.canceled_at = utc_now()
        self.session.flush()
        self._record_billing_event(
            provider_event_id=event_id,
            event_type="team_request.canceled",
            metadata={"request_id": request.id},
        )
        AuditRecorder(self.session).record(
            self.context,
            action="commercial.team_request.cancel",
            resource_type="team_pilot_request",
            resource_id=request.id,
            outcome="success",
            request_id=request_id,
        )
        return request

    def list_team_requests(self) -> list[TeamPilotRequest]:
        return list(
            self.session.scalars(
                select(TeamPilotRequest)
                .where(TeamPilotRequest.workspace_id == self.context.workspace_id)
                .order_by(TeamPilotRequest.created_at.desc(), TeamPilotRequest.id)
                .limit(_MAX_HISTORY_ROWS)
            )
        )

    def list_billing_events(self) -> list[BillingEvent]:
        return list(
            self.session.scalars(
                select(BillingEvent)
                .where(BillingEvent.workspace_id == self.context.workspace_id)
                .order_by(BillingEvent.created_at.desc(), BillingEvent.id)
                .limit(_MAX_HISTORY_ROWS)
            )
        )

    def list_activation_events(self) -> list[ActivationEvent]:
        return list(
            self.session.scalars(
                select(ActivationEvent)
                .where(ActivationEvent.workspace_id == self.context.workspace_id)
                .order_by(ActivationEvent.created_at.desc(), ActivationEvent.id)
                .limit(_MAX_HISTORY_ROWS)
            )
        )

    def record_client_event(
        self,
        data: ClientActivationEventCreate,
        *,
        idempotency_key: str,
    ) -> tuple[ActivationEvent, bool]:
        name = ActivationEventName(data.name)
        if name not in _CLIENT_EVENT_NAMES:
            raise CapabilityError("This activation event is recorded only by the server.")
        _lock_workspace(self.session, self.context, required_role=WorkspaceRole.VIEWER)
        safe_client_key = _bounded_identifier(
            idempotency_key,
            label="idempotency key",
            maximum=128,
        )
        event_key = f"client:{self.context.user_id}:{safe_client_key}"
        recorder = ActivationRecorder(self.session)
        existing_by_key = self.session.scalar(
            select(ActivationEvent).where(
                ActivationEvent.workspace_id == self.context.workspace_id,
                ActivationEvent.event_key == event_key,
            )
        )
        if existing_by_key is not None:
            return recorder.record(
                workspace_id=self.context.workspace_id,
                actor_user_id=self.context.user_id,
                name=name,
                event_key=event_key,
                source=data.source,
                metadata={"surface": data.surface},
            )
        if name in {ActivationEventName.SIGNUP, ActivationEventName.UPGRADE_VIEW}:
            first_event = self.session.scalar(
                select(ActivationEvent)
                .where(
                    ActivationEvent.workspace_id == self.context.workspace_id,
                    ActivationEvent.actor_user_id == self.context.user_id,
                    ActivationEvent.name == name,
                )
                .order_by(ActivationEvent.created_at, ActivationEvent.id)
                .limit(1)
            )
            if first_event is not None:
                return first_event, False
        day_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(ActivationEvent)
                .where(
                    ActivationEvent.workspace_id == self.context.workspace_id,
                    ActivationEvent.actor_user_id == self.context.user_id,
                    ActivationEvent.event_key.like(f"client:{self.context.user_id}:%"),
                    ActivationEvent.created_at >= day_start,
                )
            )
            or 0
        )
        if daily_count >= _MAX_CLIENT_EVENTS_PER_ACTOR_PER_DAY:
            raise LimitError(
                "Client activation event limit reached for this UTC day.", status_code=429
            )
        return recorder.record(
            workspace_id=self.context.workspace_id,
            actor_user_id=self.context.user_id,
            name=name,
            event_key=event_key,
            source=data.source,
            metadata={"surface": data.surface},
        )

    def funnel(self) -> dict[str, object]:
        rows = self.session.execute(
            select(
                ActivationEvent.name,
                func.count(ActivationEvent.id),
                func.count(func.distinct(ActivationEvent.actor_user_id)),
            )
            .where(ActivationEvent.workspace_id == self.context.workspace_id)
            .group_by(ActivationEvent.name)
        ).all()
        event_counts = {name: 0 for name in ActivationEventName}
        unique_actors = {name: 0 for name in ActivationEventName}
        for name, event_count, actor_count in rows:
            event_name = ActivationEventName(name)
            event_counts[event_name] = int(event_count)
            unique_actors[event_name] = int(actor_count)
        activated_run_engagements = self._activated_run_engagements()
        activation_duration_rows = self.session.execute(
            select(
                ActivationEvent.actor_user_id,
                ActivationEvent.created_at,
            )
            .where(
                ActivationEvent.workspace_id == self.context.workspace_id,
                ActivationEvent.actor_user_id.is_not(None),
                ActivationEvent.name == ActivationEventName.SIGNUP,
            )
            .order_by(ActivationEvent.created_at, ActivationEvent.id)
        ).all()
        first_signup_by_actor: dict[str, datetime] = {}
        for actor_user_id, created_at in activation_duration_rows:
            if actor_user_id is None:
                continue
            event_time = _aware_utc(created_at)
            first_signup_by_actor.setdefault(str(actor_user_id), event_time)
        first_engagement_by_actor: dict[str, datetime] = {}
        for (_run_id, actor_user_id), engagement_time in activated_run_engagements.items():
            current = first_engagement_by_actor.get(actor_user_id)
            if current is None or engagement_time < current:
                first_engagement_by_actor[actor_user_id] = engagement_time
        activation_durations = sorted(
            (engaged_at - signed_up_at).total_seconds()
            for actor_user_id, signed_up_at in first_signup_by_actor.items()
            if (engaged_at := first_engagement_by_actor.get(actor_user_id)) is not None
            and engaged_at >= signed_up_at
        )
        acquisition_rows = self.session.execute(
            select(ActivationEvent.source, func.count(func.distinct(ActivationEvent.actor_user_id)))
            .where(
                ActivationEvent.workspace_id == self.context.workspace_id,
                ActivationEvent.name == ActivationEventName.SIGNUP,
            )
            .group_by(ActivationEvent.source)
        ).all()
        acquisition_sources = {str(source): int(count) for source, count in acquisition_rows}
        request_rows = self.session.execute(
            select(TeamPilotRequest.status, func.count(TeamPilotRequest.id))
            .where(TeamPilotRequest.workspace_id == self.context.workspace_id)
            .group_by(TeamPilotRequest.status)
        ).all()
        request_counts = {
            TeamPilotRequestStatus(status): int(count) for status, count in request_rows
        }
        event_bounds = self.session.execute(
            select(
                func.min(ActivationEvent.created_at), func.max(ActivationEvent.created_at)
            ).where(ActivationEvent.workspace_id == self.context.workspace_id)
        ).one()
        return {
            "event_counts": event_counts,
            "unique_actors": unique_actors,
            "acquisition_sources": acquisition_sources,
            "activated_runs": len(activated_run_engagements),
            "activation_duration_sample_size": len(activation_durations),
            "activation_duration_excluded_actors": (
                len(first_signup_by_actor) - len(activation_durations)
            ),
            "activation_duration_p50_seconds": _nearest_rank(
                activation_durations,
                percentile=0.5,
            ),
            "activation_duration_p90_seconds": _nearest_rank(
                activation_durations,
                percentile=0.9,
            ),
            "pending_team_requests": request_counts.get(TeamPilotRequestStatus.PENDING, 0),
            "total_team_requests": sum(request_counts.values()),
            "first_event_at": event_bounds[0],
            "last_event_at": event_bounds[1],
        }

    def _activated_run_count(self) -> int:
        return len(self._activated_run_engagements())

    def _activated_run_engagements(self) -> dict[tuple[str, str], datetime]:
        rows = self.session.execute(
            select(
                ActivationEvent.run_id,
                ActivationEvent.actor_user_id,
                ActivationEvent.name,
                ActivationEvent.created_at,
            ).where(
                ActivationEvent.workspace_id == self.context.workspace_id,
                ActivationEvent.name.in_(
                    (
                        ActivationEventName.EVALUATION_COMPLETE,
                        ActivationEventName.RESULT_ENGAGEMENT,
                    )
                ),
                ActivationEvent.run_id.is_not(None),
                ActivationEvent.actor_user_id.is_not(None),
            )
        ).all()
        completed_at: dict[tuple[str, str], datetime] = {}
        first_engagement_at: dict[tuple[str, str], datetime] = {}
        for run_id, actor_user_id, name, created_at in rows:
            if run_id is None or actor_user_id is None:
                continue
            key = (str(run_id), str(actor_user_id))
            event_time = _aware_utc(created_at)
            if ActivationEventName(name) is ActivationEventName.EVALUATION_COMPLETE:
                current = completed_at.get(key)
                if current is None or event_time < current:
                    completed_at[key] = event_time
            else:
                current = first_engagement_at.get(key)
                if current is None or event_time < current:
                    first_engagement_at[key] = event_time
        return {
            key: engaged_time
            for key, completed_time in completed_at.items()
            if (engaged_time := first_engagement_at.get(key)) is not None
            and engaged_time >= completed_time
        }

    def _require_hosted_pilot(self) -> None:
        if self.settings.auth_mode != "oidc" or not self.settings.commercial_pilot_enabled:
            raise CapabilityError(
                "Hosted pilot actions are available only in an enabled shared OIDC deployment."
            )

    def _billing_event(self, provider_event_id: str) -> BillingEvent | None:
        return self.session.scalar(
            select(BillingEvent).where(
                BillingEvent.workspace_id == self.context.workspace_id,
                BillingEvent.provider == "evalforge",
                BillingEvent.provider_event_id == provider_event_id,
            )
        )

    def _record_billing_event(
        self,
        *,
        provider_event_id: str,
        event_type: str,
        metadata: dict[str, Any],
    ) -> BillingEvent:
        safe_metadata = _safe_metadata(metadata)
        payload = {
            "workspace_id": self.context.workspace_id,
            "event_type": event_type,
            "metadata": safe_metadata,
        }
        event = BillingEvent(
            workspace_id=self.context.workspace_id,
            actor_user_id=self.context.user_id,
            provider="evalforge",
            provider_event_id=_bounded_identifier(
                provider_event_id,
                label="provider event ID",
            ),
            event_type=_bounded_identifier(event_type, label="billing event type", maximum=100),
            payload_sha256=canonical_json_hash(payload),
            metadata_json=safe_metadata,
        )
        self.session.add(event)
        self.session.flush()
        return event


def require_run_entitlement(
    session: Session,
    context: WorkspaceContext,
    settings: Settings,
    *,
    lock: bool = False,
) -> WorkspaceContext:
    """Block only new hosted work; preserve all local and historical read/export paths."""

    if lock:
        _lock_workspace(session, context, required_role=WorkspaceRole.EDITOR)
    entitlement = CommercialPilotService(session, context, settings).entitlement()
    if not entitlement.can_start_runs:
        raise EntitlementRequiredError
    return context


def _lock_workspace(
    session: Session,
    context: WorkspaceContext,
    *,
    required_role: WorkspaceRole,
) -> None:
    """Serialize tenant mutations and revalidate the caller inside that transaction."""

    workspace = session.scalar(
        select(Workspace).where(Workspace.id == context.workspace_id).with_for_update()
    )
    membership = session.scalar(
        select(WorkspaceMembership)
        .join(User, User.id == WorkspaceMembership.user_id)
        .where(
            WorkspaceMembership.workspace_id == context.workspace_id,
            WorkspaceMembership.user_id == context.user_id,
            User.status == RecordStatus.ACTIVE,
        )
        .with_for_update()
    )
    if (
        workspace is None
        or membership is None
        or workspace.status is not RecordStatus.ACTIVE
        or membership.status is not RecordStatus.ACTIVE
        or not role_allows(WorkspaceRole(membership.role), required_role)
    ):
        raise AuthorizationError


def _effective_status(row: WorkspaceEntitlement, at: datetime) -> EntitlementStatus:
    if (
        row.status is EntitlementStatus.TRIALING
        and row.current_period_end is not None
        and _aware_utc(row.current_period_end) <= at
    ):
        return EntitlementStatus.EXPIRED
    return row.status


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bounded_identifier(value: str, *, label: str, maximum: int = 255) -> str:
    if value != value.strip() or not value or len(value) > maximum:
        raise ConflictError(f"{label.title()} must contain 1-{maximum} trimmed characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ConflictError(f"{label.title()} contains unsupported characters.")
    return value


def _bounded_slug(value: str, *, label: str) -> str:
    normalized = _bounded_identifier(value, label=label, maximum=64)
    if any(
        not (character.islower() or character.isdigit() or character in "_-")
        for character in normalized
    ):
        raise ConflictError(f"{label.title()} must be a lowercase slug.")
    return normalized


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    safe = dict(metadata or {})
    if len(safe) > 12:
        raise ConflictError("Commercial metadata contains too many fields.")
    for key, value in safe.items():
        normalized = "".join(character for character in key.casefold() if character.isalnum())
        if any(fragment in normalized for fragment in _FORBIDDEN_METADATA_FRAGMENTS):
            raise ConflictError("Commercial metadata contains a disallowed field.")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise ConflictError("Commercial metadata values must be scalar.")
    return safe


def _nearest_rank(values: list[float], *, percentile: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int(len(values) * percentile + 0.999999) - 1))
    return values[index]
