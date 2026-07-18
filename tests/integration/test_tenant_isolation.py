from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from evalforge.models import User, Workspace, WorkspaceMembership
from evalforge.repositories import (
    DatasetRepository,
    EvaluationRunRepository,
    ModelProfileRepository,
    NotFoundError,
    PromptTemplateRepository,
)
from evalforge.schemas import (
    DatasetCreate,
    EvaluationRunCreate,
    ModelProfileCreate,
    PromptTemplateCreate,
)
from evalforge.schemas import (
    TestCaseCreate as CaseCreateSchema,
)
from evalforge.security.permissions import WorkspaceContext, WorkspaceRole


def _workspace_context(session: Session, suffix: str) -> WorkspaceContext:
    workspace = Workspace(slug=f"workspace-{suffix}", name=f"Workspace {suffix}")
    user = User(
        issuer="https://identity.example.test",
        subject=f"subject-{suffix}",
        display_name=f"Owner {suffix}",
    )
    membership = WorkspaceMembership(
        workspace=workspace,
        user=user,
        role=WorkspaceRole.OWNER,
    )
    session.add_all([workspace, user, membership])
    session.flush()
    return WorkspaceContext(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.OWNER,
        workspace_name=workspace.name,
        display_name=user.display_name or "Owner",
    )


def _create_matrix(session: Session, context: WorkspaceContext, *, key: str) -> str:
    dataset = DatasetRepository(session, context).create(
        DatasetCreate(
            name="Shared benchmark name",
            cases=[CaseCreateSchema(external_id="case-1", input_text="Say hello")],
        )
    )
    prompt = PromptTemplateRepository(session, context).create(
        PromptTemplateCreate(name="Shared prompt name", user_template="{input}")
    )
    model = ModelProfileRepository(session, context).create(
        ModelProfileCreate(
            name="Shared model name",
            provider="deterministic",
            model_name="balanced",
            api_mode="deterministic",
            input_price_micro_usd_per_million_tokens=0,
            output_price_micro_usd_per_million_tokens=0,
            pricing_source="deterministic",
        )
    )
    run = EvaluationRunRepository(session, context).create(
        EvaluationRunCreate(
            dataset_id=UUID(dataset.id),
            prompt_ids=[UUID(prompt.id)],
            model_ids=[UUID(model.id)],
            idempotency_key=key,
        ),
        application_version="test",
        requested_by_user_id=context.user_id,
        requested_by=context.display_name,
    )
    return run.id


@pytest.mark.integration
def test_names_and_idempotency_are_workspace_local(session: Session) -> None:
    first = _workspace_context(session, "a")
    second = _workspace_context(session, "b")

    first_run_id = _create_matrix(session, first, key="same-request")
    second_run_id = _create_matrix(session, second, key="same-request")
    session.commit()

    assert first_run_id != second_run_id
    assert DatasetRepository(session, first).list()[1] == 1
    assert DatasetRepository(session, second).list()[1] == 1
    assert EvaluationRunRepository(session, first).find_by_idempotency_key("same-request")
    assert EvaluationRunRepository(session, second).find_by_idempotency_key("same-request")


@pytest.mark.integration
def test_cross_workspace_ids_are_indistinguishable_from_missing(session: Session) -> None:
    first = _workspace_context(session, "a")
    second = _workspace_context(session, "b")
    first_run_id = _create_matrix(session, first, key="first-request")
    first_dataset = DatasetRepository(session, first).list()[0][0]
    session.commit()

    with pytest.raises(NotFoundError):
        DatasetRepository(session, second).get(first_dataset.id)
    with pytest.raises(NotFoundError):
        EvaluationRunRepository(session, second).get(first_run_id)


@pytest.mark.integration
def test_database_rejects_mixed_workspace_parentage(session: Session) -> None:
    first = _workspace_context(session, "a")
    second = _workspace_context(session, "b")
    dataset = DatasetRepository(session, first).create(
        DatasetCreate(
            name="First benchmark",
            cases=[CaseCreateSchema(external_id="case-1", input_text="One")],
        )
    )
    session.flush()

    # The tenant-preserving foreign key must reject a child claiming another
    # workspace while referencing this dataset.
    dataset.cases[0].workspace_id = second.workspace_id
    with pytest.raises(IntegrityError):
        session.commit()
