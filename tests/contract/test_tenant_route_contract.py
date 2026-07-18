"""Static boundary preventing workspace filters from drifting into route modules."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
ROUTES = ROOT / "src" / "evalforge" / "api" / "routes"
WORKSPACE_SCOPED_MODELS = frozenset(
    {
        "AuditEvent",
        "Dataset",
        "EvaluationResult",
        "EvaluationRun",
        "ExecutionAttempt",
        "ModelProfile",
        "PromptTemplate",
        "RunCandidate",
        "TestCase",
        "User",
        "Workspace",
        "WorkspaceMembership",
    }
)


@pytest.mark.parametrize("route_path", sorted(ROUTES.glob("*.py")), ids=lambda path: path.name)
def test_user_routes_delegate_workspace_queries_to_scoped_boundaries(
    route_path: Path,
) -> None:
    tree = ast.parse(route_path.read_text(encoding="utf-8"), filename=str(route_path))
    direct_models: set[str] = set()
    direct_query_calls: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "evalforge.models":
            direct_models.update(
                alias.name for alias in node.names if alias.name in WORKSPACE_SCOPED_MODELS
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "select"
        ):
            direct_query_calls.append(f"select at line {node.lineno}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "session"
        ):
            direct_query_calls.append(f"session.get at line {node.lineno}")

    assert not direct_models, (
        f"{route_path.name} imports workspace-scoped ORM models directly: {sorted(direct_models)}"
    )
    assert not direct_query_calls, (
        f"{route_path.name} performs direct database queries: {direct_query_calls}"
    )
