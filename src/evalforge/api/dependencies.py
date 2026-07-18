"""FastAPI dependencies with request-scoped SQLAlchemy sessions."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from evalforge.container import AppContainer
from evalforge.evaluation.service import EvaluationService


def get_container(request: Request) -> AppContainer:
    return cast("AppContainer", request.app.state.container)


def get_session(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Generator[Session, None, None]:
    session = container.session_factory()
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_evaluation_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> EvaluationService:
    return container.evaluation_service


ContainerDep = Annotated[AppContainer, Depends(get_container)]
SessionDep = Annotated[Session, Depends(get_session)]
EvaluationServiceDep = Annotated[EvaluationService, Depends(get_evaluation_service)]
