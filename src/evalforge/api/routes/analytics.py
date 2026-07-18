"""Dashboard overview and paired comparison routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from evalforge.analytics import build_overview, build_run_comparison
from evalforge.api.dependencies import SessionDep, ViewerWorkspaceDep
from evalforge.errors import NotFoundError

router = APIRouter(tags=["analytics"])


@router.get("/overview")
def overview(session: SessionDep, workspace: ViewerWorkspaceDep) -> dict[str, Any]:
    return build_overview(session, workspace.workspace_id)


@router.get("/runs/{run_id}/comparison")
def compare_run(run_id: str, session: SessionDep, workspace: ViewerWorkspaceDep) -> dict[str, Any]:
    try:
        return build_run_comparison(session, workspace.workspace_id, run_id)
    except LookupError as exc:
        raise NotFoundError("Evaluation run") from exc
