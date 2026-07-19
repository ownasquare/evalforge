"""Operator CLI for deterministic setup, readiness, and run export."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from evalforge import __version__
from evalforge.config import Settings, get_settings
from evalforge.container import apply_migrations, build_container
from evalforge.database import (
    check_database_readiness,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from evalforge.demo import (
    LauncherError,
    api_command,
    dashboard_environment,
    run_demo,
    run_foreground,
    ui_command,
)
from evalforge.evaluation.calibration_io import (
    CalibrationInputError,
    LocalCalibrationReportSink,
    build_calibration_report,
    load_calibration_manifest,
)
from evalforge.exports import DisclosureProfile, LocalFileSink, build_export_package
from evalforge.models import AuditEvent, RecordStatus, User, Workspace, WorkspaceMembership
from evalforge.repositories import EvaluationRunRepository, NotFoundError
from evalforge.schemas import EvaluationRunDetail
from evalforge.security.permissions import (
    WorkspaceContext,
    WorkspaceRole,
    local_workspace_context,
)
from evalforge.seed import exportable_seed_manifest, seed_demo

app = typer.Typer(
    name="evalforge",
    help="Operate the EvalForge local evaluation workbench.",
    no_args_is_help=True,
)

_WORKSPACE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _shared_operator_settings() -> Settings:
    settings = get_settings()
    if settings.auth_mode != "oidc":
        raise typer.BadParameter(
            "Workspace identity commands are available only in shared OIDC mode."
        )
    return settings


@contextmanager
def _operator_session(settings: Settings) -> Iterator[Session]:
    try:
        engine = create_database_engine(settings)
    except SQLAlchemyError:
        raise typer.BadParameter(
            "The identity database operation could not be completed."
        ) from None
    try:
        if not check_database_readiness(engine):
            raise typer.BadParameter("The database is not ready. Apply migrations first.")
        factory = create_session_factory(engine)
        with session_scope(factory) as session:
            yield session
    except SQLAlchemyError:
        raise typer.BadParameter(
            "The identity database operation could not be completed."
        ) from None
    finally:
        engine.dispose()


def _validated_workspace_slug(value: str) -> str:
    if value == "local":
        raise typer.BadParameter("The local workspace slug is reserved.")
    if len(value) > 100 or _WORKSPACE_SLUG.fullmatch(value) is None:
        raise typer.BadParameter(
            "Workspace slug must be 1-100 lowercase letters or numbers separated by hyphens."
        )
    return value


def _validated_text(value: str, *, label: str, max_length: int) -> str:
    if value != value.strip() or not value or len(value) > max_length:
        raise typer.BadParameter(
            f"{label} must be 1-{max_length} characters without surrounding whitespace."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise typer.BadParameter(f"{label} cannot contain control characters.")
    return value


def _validated_issuer(settings: Settings, issuer: str) -> str:
    normalized = _validated_text(issuer, label="Issuer", max_length=500)
    configured = settings.oidc_issuer if settings.oidc_issuer is not None else ""
    if normalized != configured:
        raise typer.BadParameter("Issuer does not match the configured identity provider.")
    return configured


def _validated_workspace_option(settings: Settings, workspace_slug: str | None) -> str | None:
    """Validate mode-specific workspace input before any database mutation."""

    if settings.auth_mode == "oidc":
        if workspace_slug is None:
            raise typer.BadParameter("--workspace is required in shared OIDC mode.")
        return _validated_workspace_slug(workspace_slug)
    if workspace_slug not in {None, "local"}:
        raise typer.BadParameter("Local mode operates only on the local workspace.")
    return None


def _workspace_by_slug(session: Session, workspace_slug: str) -> Workspace:
    workspace = session.scalar(select(Workspace).where(Workspace.slug == workspace_slug))
    if workspace is None:
        raise typer.BadParameter("Workspace was not found.")
    return workspace


def _operator_workspace_context(session: Session, workspace_slug: str) -> WorkspaceContext:
    workspace = _workspace_by_slug(session, _validated_workspace_slug(workspace_slug))
    if workspace.status != RecordStatus.ACTIVE:
        raise typer.BadParameter("Workspace is not active.")
    row = session.execute(
        select(WorkspaceMembership, User)
        .join(User, User.id == WorkspaceMembership.user_id)
        .where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.status == RecordStatus.ACTIVE,
            User.status == RecordStatus.ACTIVE,
        )
        .order_by(WorkspaceMembership.created_at, WorkspaceMembership.id)
        .limit(1)
    ).first()
    if row is None:
        raise typer.BadParameter("Workspace has no active membership for operator attribution.")
    membership, user = row
    return WorkspaceContext(
        workspace_id=workspace.id,
        user_id=user.id,
        role=membership.role,
        workspace_name=workspace.name,
        display_name=user.display_name or "Workspace operator",
    )


def _record_operator_event(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="succeeded",
            metadata_json=dict(metadata or {}),
        )
    )


def _echo_identity_result(payload: Mapping[str, object]) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@app.command()
def seed(
    workspace_slug: Annotated[
        str | None,
        typer.Option("--workspace", help="Required shared-workspace slug in OIDC mode"),
    ] = None,
) -> None:
    """Apply migrations and idempotently install the offline demo."""
    settings = get_settings()
    normalized_workspace = _validated_workspace_option(settings, workspace_slug)
    apply_migrations(settings)
    if settings.auth_mode == "oidc":
        if normalized_workspace is None:
            raise typer.BadParameter("--workspace is required in shared OIDC mode.")
        with _operator_session(settings) as session:
            context = _operator_workspace_context(session, normalized_workspace)
            counts = seed_demo(session, context)
            manifest = exportable_seed_manifest(session, context)
            _record_operator_event(
                session,
                workspace_id=context.workspace_id,
                action="operator.demo.seed",
                resource_type="workspace",
                resource_id=context.workspace_id,
                metadata={"counts": counts},
            )
        typer.echo(
            json.dumps(
                {
                    "status": "ready",
                    "workspace_id": context.workspace_id,
                    "workspace_slug": normalized_workspace,
                    "counts": counts,
                    **manifest,
                },
                indent=2,
            )
        )
        return
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as session:
            context = local_workspace_context()
            counts = seed_demo(session, context)
            manifest = exportable_seed_manifest(session, context)
        typer.echo(json.dumps({"status": "ready", "counts": counts, **manifest}, indent=2))
    finally:
        engine.dispose()


@app.command()
def api() -> None:
    """Start the FastAPI service with the validated local settings."""

    returncode = run_foreground(api_command(get_settings()))
    if returncode:
        raise typer.Exit(code=returncode)


@app.command()
def ui() -> None:
    """Start the Streamlit dashboard with the validated local settings."""

    settings = get_settings()
    returncode = run_foreground(
        ui_command(settings),
        environment=dashboard_environment(settings),
    )
    if returncode:
        raise typer.Exit(code=returncode)


@app.command()
def demo() -> None:
    """Prepare and run the complete offline demo with one command."""

    try:
        run_demo(get_settings(), emit=typer.echo)
    except LauncherError as exc:
        typer.echo(f"EvalForge could not start: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def doctor() -> None:
    """Check safe local readiness without displaying credential values."""
    settings = get_settings()
    engine = create_database_engine(settings)
    try:
        database_ready = check_database_readiness(engine)
    except Exception:
        database_ready = False
    finally:
        engine.dispose()
    payload = {
        "status": "ready" if database_ready else "needs_migration_or_database",
        "database_backend": settings.database_backend,
        "database_ready": database_ready,
        "real_runs_enabled": settings.real_runs_enabled,
        "executor_mode": settings.executor_mode,
        "providers": settings.provider_capabilities(),
        "limits": {
            "cases": settings.max_cases_per_dataset,
            "variants": settings.max_variants_per_run,
            "calls": settings.max_calls_per_run,
            "concurrency": settings.max_concurrent_generations,
        },
    }
    typer.echo(json.dumps(payload, indent=2))
    if not database_ready:
        raise typer.Exit(code=1)


@app.command()
def calibrate(
    labels_file: Annotated[
        Path,
        typer.Argument(help="Versioned EvalForge JSON or CSV labels"),
    ],
    selected_threshold: Annotated[
        float,
        typer.Option(
            "--threshold",
            min=0.0,
            max=1.0,
            help="Metric threshold to review",
        ),
    ],
    output_directory: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            "-o",
            help="Private report destination directory",
        ),
    ],
) -> None:
    """Create deterministic offline calibration evidence without contacting a provider.

    This permanently offline workflow reads only the supplied labels file and writes one
    local report. It does not load settings or credentials, open a database, or contact a
    model provider.
    """

    try:
        manifest = load_calibration_manifest(labels_file)
        package = build_calibration_report(
            manifest,
            selected_threshold=selected_threshold,
        )
        receipt = LocalCalibrationReportSink(output_directory).export(package)
    except CalibrationInputError as exc:
        raise typer.BadParameter(str(exc)) from None
    except (OSError, ValueError):
        raise typer.BadParameter("The offline calibration report could not be created.") from None

    payload = package.payload
    typer.echo(
        json.dumps(
            {
                "status": receipt.status,
                "schema_version": payload["schema_version"],
                "dataset_id": payload["dataset"]["id"],
                "dataset_version": payload["dataset"]["version"],
                "metric_name": payload["metric"]["name"],
                "metric_version": payload["metric"]["version"],
                "sample_size": payload["sample_size"],
                "human_pass_count": payload["human_pass_count"],
                "human_fail_count": payload["human_fail_count"],
                "selected_threshold": payload["selected_threshold"],
                "direction": payload["metric"]["direction"],
                "precision": payload["precision"],
                "recall": payload["recall"],
                "f1": payload["f1"],
                "payload_sha256": receipt.payload_sha256,
                "label_manifest_sha256": receipt.label_manifest_sha256,
                "calibration_set_sha256": payload["calibration_set_sha256"],
                "production_validated": False,
                "location": receipt.location,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@app.command("workspace-create")
def workspace_create(
    slug: Annotated[str, typer.Option("--slug", help="Stable workspace slug")],
    name: Annotated[str, typer.Option("--name", help="Workspace display name")],
) -> None:
    """Create one active shared workspace without provisioning access."""

    settings = _shared_operator_settings()
    normalized_slug = _validated_workspace_slug(slug)
    normalized_name = _validated_text(name, label="Workspace name", max_length=200)
    with _operator_session(settings) as session:
        workspace = session.scalar(select(Workspace).where(Workspace.slug == normalized_slug))
        if workspace is not None:
            if workspace.name != normalized_name or workspace.status != RecordStatus.ACTIVE:
                raise typer.BadParameter(
                    "Workspace slug already exists with different settings or inactive status."
                )
            operation = "already_exists"
        else:
            workspace = Workspace(slug=normalized_slug, name=normalized_name)
            session.add(workspace)
            session.flush()
            _record_operator_event(
                session,
                workspace_id=workspace.id,
                action="operator.workspace.create",
                resource_type="workspace",
                resource_id=workspace.id,
            )
            operation = "created"

        result = {
            "status": operation,
            "workspace_id": workspace.id,
            "workspace_slug": workspace.slug,
            "workspace_name": workspace.name,
        }
    _echo_identity_result(result)


@app.command("membership-provision")
def membership_provision(
    workspace_slug: Annotated[str, typer.Option("--workspace", help="Existing workspace slug")],
    issuer: Annotated[str, typer.Option("--issuer", help="Exact configured OIDC issuer")],
    subject: Annotated[
        str, typer.Option("--subject", help="Stable subject claim from that issuer")
    ],
    role: Annotated[WorkspaceRole, typer.Option("--role", help="Workspace role")],
    display_name: Annotated[
        str | None, typer.Option("--display-name", help="Optional account display name")
    ] = None,
    email: Annotated[
        str | None, typer.Option("--email", help="Optional account email metadata")
    ] = None,
) -> None:
    """Create or reactivate one issuer-and-subject workspace membership."""

    settings = _shared_operator_settings()
    normalized_workspace = _validated_workspace_slug(workspace_slug)
    normalized_issuer = _validated_issuer(settings, issuer)
    normalized_subject = _validated_text(subject, label="Subject", max_length=500)
    normalized_display_name = (
        _validated_text(display_name, label="Display name", max_length=200)
        if display_name is not None
        else None
    )
    normalized_email = (
        _validated_text(email, label="Email", max_length=320) if email is not None else None
    )

    with _operator_session(settings) as session:
        workspace = _workspace_by_slug(session, normalized_workspace)
        if workspace.status != RecordStatus.ACTIVE:
            raise typer.BadParameter("Workspace is not active.")

        user = session.scalar(
            select(User).where(
                User.issuer == normalized_issuer,
                User.subject == normalized_subject,
            )
        )
        user_created = user is None
        user_updated = False
        if user is None:
            user = User(
                issuer=normalized_issuer,
                subject=normalized_subject,
                display_name=normalized_display_name,
                email=normalized_email,
            )
            session.add(user)
            session.flush()
        elif user.status != RecordStatus.ACTIVE:
            raise typer.BadParameter("Identity is suspended and cannot be provisioned.")
        else:
            if normalized_display_name is not None and user.display_name != normalized_display_name:
                user.display_name = normalized_display_name
                user_updated = True
            if normalized_email is not None and user.email != normalized_email:
                user.email = normalized_email
                user_updated = True

        membership = session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace.id,
                WorkspaceMembership.user_id == user.id,
            )
        )
        membership_created = membership is None
        if membership is None:
            membership = WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=role,
            )
            session.add(membership)
            session.flush()
            operation = "created"
        else:
            changed = (
                membership.status != RecordStatus.ACTIVE or membership.role != role or user_updated
            )
            membership.status = RecordStatus.ACTIVE
            membership.role = role
            operation = "updated" if changed else "already_active"

        _record_operator_event(
            session,
            workspace_id=workspace.id,
            action="operator.membership.provision",
            resource_type="workspace_membership",
            resource_id=membership.id,
            metadata={
                "role": role.value,
                "operation": operation,
                "user_created": user_created,
                "membership_created": membership_created,
            },
        )
        session.flush()
        result = {
            "status": operation,
            "workspace_id": workspace.id,
            "workspace_slug": workspace.slug,
            "user_id": user.id,
            "membership_id": membership.id,
            "role": role.value,
        }
    _echo_identity_result(result)


@app.command("membership-revoke")
def membership_revoke(
    workspace_slug: Annotated[str, typer.Option("--workspace", help="Existing workspace slug")],
    issuer: Annotated[str, typer.Option("--issuer", help="Exact configured OIDC issuer")],
    subject: Annotated[
        str, typer.Option("--subject", help="Stable subject claim from that issuer")
    ],
) -> None:
    """Suspend one issuer-and-subject membership without deleting history."""

    settings = _shared_operator_settings()
    normalized_workspace = _validated_workspace_slug(workspace_slug)
    normalized_issuer = _validated_issuer(settings, issuer)
    normalized_subject = _validated_text(subject, label="Subject", max_length=500)
    with _operator_session(settings) as session:
        workspace = _workspace_by_slug(session, normalized_workspace)
        membership = session.scalar(
            select(WorkspaceMembership)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace.id,
                User.issuer == normalized_issuer,
                User.subject == normalized_subject,
            )
        )
        if membership is None:
            raise typer.BadParameter("No membership matched the supplied workspace and identity.")

        if membership.status == RecordStatus.SUSPENDED:
            operation = "already_revoked"
        else:
            membership.status = RecordStatus.SUSPENDED
            operation = "revoked"
            _record_operator_event(
                session,
                workspace_id=workspace.id,
                action="operator.membership.revoke",
                resource_type="workspace_membership",
                resource_id=membership.id,
                metadata={"operation": operation, "role": membership.role},
            )
        session.flush()
        result = {
            "status": operation,
            "workspace_id": workspace.id,
            "workspace_slug": workspace.slug,
            "user_id": membership.user_id,
            "membership_id": membership.id,
        }
    _echo_identity_result(result)


@app.command()
def worker() -> None:
    """Run a database-backed worker that discovers committed evaluation work."""

    base_settings = get_settings()
    if base_settings.database_backend != "postgresql":
        raise typer.BadParameter("The database worker requires PostgreSQL.")
    settings = base_settings.model_copy(
        update={"executor_mode": "database_worker", "seed_demo": False}
    )

    async def serve() -> None:
        container = build_container(settings, migrate=True)
        try:
            await container.executor.start()
            typer.echo("EvalForge database worker is ready.")
            await asyncio.Event().wait()
        finally:
            await container.close()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        typer.echo("EvalForge database worker stopped.")


@app.command("export-run")
def export_run(
    run_id: Annotated[str, typer.Argument(help="Evaluation run UUID")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Destination JSON path")],
    workspace_slug: Annotated[
        str | None,
        typer.Option("--workspace", help="Required shared-workspace slug in OIDC mode"),
    ] = None,
) -> None:
    """Export immutable run provenance and results to one JSON file."""
    settings = get_settings()
    normalized_workspace = _validated_workspace_option(settings, workspace_slug)
    if settings.auth_mode == "oidc":
        if normalized_workspace is None:
            raise typer.BadParameter("--workspace is required in shared OIDC mode.")
        with _operator_session(settings) as session:
            context = _operator_workspace_context(session, normalized_workspace)
            payload = _run_detail(session, context, run_id).model_dump(mode="json")
            _record_operator_event(
                session,
                workspace_id=context.workspace_id,
                action="operator.run.export",
                resource_type="evaluation_run",
                resource_id=run_id,
                metadata={"format": "json"},
            )
        _write_run_export(run_id, output, payload)
        return
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as session:
            context = local_workspace_context()
            payload = _run_detail(session, context, run_id).model_dump(mode="json")
            _record_operator_event(
                session,
                workspace_id=context.workspace_id,
                action="operator.run.export",
                resource_type="evaluation_run",
                resource_id=run_id,
                metadata={"format": "json"},
            )
        _write_run_export(run_id, output, payload)
    finally:
        engine.dispose()


@app.command("export-package")
def export_package(
    run_id: Annotated[str, typer.Argument(help="Evaluation run UUID")],
    output_directory: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Private destination directory"),
    ],
    disclosure_profile: Annotated[
        DisclosureProfile,
        typer.Option(
            "--disclosure-profile",
            help="Required evidence disclosure profile for this package",
        ),
    ],
    workspace_slug: Annotated[
        str | None,
        typer.Option("--workspace", help="Required shared-workspace slug in OIDC mode"),
    ] = None,
) -> None:
    """Create a versioned, integrity-hashed run-evidence package locally."""

    settings = get_settings()
    normalized_workspace = _validated_workspace_option(settings, workspace_slug)
    with _operator_session(settings) as session:
        context = (
            _operator_workspace_context(session, normalized_workspace)
            if normalized_workspace is not None
            else local_workspace_context()
        )
        run_detail = _run_detail(session, context, run_id)
        run_evidence = run_detail.model_dump(mode="json")
        package = build_export_package(
            run_evidence,
            application_version=run_detail.application_version or __version__,
            metric_versions=_metric_versions(run_detail),
            disclosure_profile=disclosure_profile,
        )
        receipt = LocalFileSink(output_directory).export(package)
        _record_operator_event(
            session,
            workspace_id=context.workspace_id,
            action="operator.run.export_package",
            resource_type="evaluation_run",
            resource_id=run_id,
            metadata={
                "created": receipt.created,
                "disclosure_profile": disclosure_profile.value,
                "format": package.schema_version,
                "package_sha256": receipt.package_sha256,
                "sink": receipt.sink,
            },
        )

    _echo_identity_result(
        {
            "status": "created" if receipt.created else "already_exists",
            "run_id": run_id,
            "workspace_id": context.workspace_id,
            "schema_version": package.schema_version,
            "disclosure_profile": disclosure_profile.value,
            "package_sha256": receipt.package_sha256,
            "location": receipt.location,
            "exported_at": receipt.exported_at.isoformat(),
            "idempotency_key": receipt.idempotency_key,
        }
    )


def _run_detail(
    session: Session,
    context: WorkspaceContext,
    run_id: str,
) -> EvaluationRunDetail:
    try:
        run = EvaluationRunRepository(session, context).get(run_id, with_detail=True)
    except NotFoundError:
        raise typer.BadParameter(
            "Evaluation run was not found in the selected workspace."
        ) from None
    return EvaluationRunDetail.model_validate(run)


def _metric_versions(run: EvaluationRunDetail) -> dict[str, str]:
    versions: dict[str, str] = {}
    configured = run.metric_configuration_snapshot.get("versions")
    if isinstance(configured, Mapping):
        versions.update(
            {
                str(name): version
                for name, version in configured.items()
                if isinstance(version, str) and str(name).strip() and version.strip()
            }
        )
    for result in run.results:
        versions.update(
            {
                str(name): version
                for name, version in result.metric_versions.items()
                if isinstance(version, str) and str(name).strip() and version.strip()
            }
        )
    return versions


def _write_run_export(run_id: str, output: Path, payload: Mapping[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(f"Exported run {run_id} to {output}")


if __name__ == "__main__":
    app()
