"""Operator CLI for deterministic setup, readiness, and run export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from evalforge.config import get_settings
from evalforge.container import apply_migrations
from evalforge.database import (
    check_database_readiness,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from evalforge.repositories import EvaluationRunRepository
from evalforge.schemas import EvaluationRunDetail
from evalforge.seed import exportable_seed_manifest, seed_demo

app = typer.Typer(
    name="evalforge",
    help="Operate the EvalForge local evaluation workbench.",
    no_args_is_help=True,
)


@app.command()
def seed() -> None:
    """Apply migrations and idempotently install the offline demo."""
    settings = get_settings()
    apply_migrations(settings)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as session:
            counts = seed_demo(session)
            manifest = exportable_seed_manifest(session)
        typer.echo(json.dumps({"status": "ready", "counts": counts, **manifest}, indent=2))
    finally:
        engine.dispose()


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


@app.command("export-run")
def export_run(
    run_id: Annotated[str, typer.Argument(help="Evaluation run UUID")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Destination JSON path")],
) -> None:
    """Export immutable run provenance and results to one JSON file."""
    settings = get_settings()
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            run = EvaluationRunRepository(session).get(run_id, with_detail=True)
            payload = EvaluationRunDetail.model_validate(run).model_dump(mode="json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        typer.echo(f"Exported run {run_id} to {output}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    app()
