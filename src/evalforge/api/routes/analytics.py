"""Dashboard overview and paired comparison routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from evalforge.analytics import build_overview, build_run_comparison
from evalforge.api.dependencies import SessionDep
from evalforge.errors import NotFoundError

router = APIRouter(tags=["analytics"])


@router.get("/overview")
def overview(session: SessionDep) -> dict[str, Any]:
    return build_overview(session)


@router.get("/runs/{run_id}/comparison")
def compare_run(run_id: str, session: SessionDep) -> dict[str, Any]:
    try:
        return build_run_comparison(session, run_id)
    except LookupError as exc:
        raise NotFoundError("Evaluation run") from exc
