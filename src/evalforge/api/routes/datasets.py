"""Dataset, test-case, atomic import, and safe export routes."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Annotated, Any

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from pydantic import ValidationError as PydanticValidationError

from evalforge.api.dependencies import ContainerDep, SessionDep
from evalforge.errors import EvalForgeError, LimitError
from evalforge.repositories import DatasetRepository
from evalforge.schemas import (
    DatasetCreate,
    DatasetDetail,
    DatasetRead,
    DatasetUpdate,
    Page,
    TestCaseCreate,
    TestCaseRead,
    TestCaseUpdate,
)

router = APIRouter(tags=["datasets"])


@router.get("/datasets", response_model=Page[DatasetRead])
def list_datasets(
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    rows, total = DatasetRepository(session).list(page=page, limit=limit)
    return {
        "items": [DatasetRead.model_validate(row).model_dump(mode="json") for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.post("/datasets", status_code=status.HTTP_201_CREATED, response_model=DatasetDetail)
def create_dataset(
    data: DatasetCreate, session: SessionDep, container: ContainerDep
) -> dict[str, Any]:
    if len(data.cases) > container.settings.max_cases_per_dataset:
        raise LimitError("The dataset exceeds the configured case limit.")
    dataset = DatasetRepository(session).create(data)
    session.commit()
    dataset = DatasetRepository(session).get(dataset.id, with_cases=True)
    return DatasetDetail.model_validate(dataset).model_dump(mode="json")


@router.get("/datasets/{dataset_id}", response_model=DatasetDetail)
def get_dataset(dataset_id: str, session: SessionDep) -> dict[str, Any]:
    dataset = DatasetRepository(session).get(dataset_id, with_cases=True)
    return DatasetDetail.model_validate(dataset).model_dump(mode="json")


@router.patch("/datasets/{dataset_id}", response_model=DatasetRead)
def update_dataset(dataset_id: str, data: DatasetUpdate, session: SessionDep) -> dict[str, Any]:
    dataset = DatasetRepository(session).update(dataset_id, data)
    session.commit()
    return DatasetRead.model_validate(dataset).model_dump(mode="json")


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(dataset_id: str, session: SessionDep) -> Response:
    DatasetRepository(session).delete(dataset_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/datasets/{dataset_id}/cases", response_model=Page[TestCaseRead])
def list_cases(dataset_id: str, session: SessionDep) -> dict[str, Any]:
    dataset = DatasetRepository(session).get(dataset_id, with_cases=True)
    return {
        "items": [
            TestCaseRead.model_validate(case).model_dump(mode="json") for case in dataset.cases
        ],
        "total": len(dataset.cases),
        "page": 1,
        "limit": max(1, len(dataset.cases)),
    }


@router.post(
    "/datasets/{dataset_id}/cases",
    status_code=status.HTTP_201_CREATED,
    response_model=TestCaseRead,
)
def create_case(
    dataset_id: str, data: TestCaseCreate, session: SessionDep, container: ContainerDep
) -> dict[str, Any]:
    dataset = DatasetRepository(session).get(dataset_id, with_cases=True)
    if len(dataset.cases) >= container.settings.max_cases_per_dataset:
        raise LimitError("The dataset has reached the configured case limit.")
    case = DatasetRepository(session).add_case(dataset_id, data)
    session.commit()
    return TestCaseRead.model_validate(case).model_dump(mode="json")


@router.patch("/cases/{case_id}", response_model=TestCaseRead)
def update_case(case_id: str, data: TestCaseUpdate, session: SessionDep) -> dict[str, Any]:
    case = DatasetRepository(session).update_case(case_id, data)
    session.commit()
    return TestCaseRead.model_validate(case).model_dump(mode="json")


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(case_id: str, session: SessionDep) -> Response:
    DatasetRepository(session).delete_case(case_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/datasets/{dataset_id}/imports", status_code=status.HTTP_201_CREATED)
async def import_cases(
    dataset_id: str,
    session: SessionDep,
    container: ContainerDep,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise LimitError("Import exceeds the 10 MB upload limit.", status_code=413)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvalForgeError(
            "invalid_encoding", "Imports must use UTF-8 encoding.", status_code=422
        ) from exc
    rows = _decode_import(file.filename or "upload.json", text)
    repository = DatasetRepository(session)
    dataset = repository.get(dataset_id, with_cases=True)
    if len(dataset.cases) + len(rows) > container.settings.max_cases_per_dataset:
        raise LimitError("Import would exceed the configured dataset case limit.")
    next_position = max((case.position for case in dataset.cases), default=-1) + 1
    parsed: list[TestCaseCreate] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        try:
            parsed.append(_case_from_mapping(row, position=next_position + index - 1))
        except (PydanticValidationError, TypeError, ValueError) as exc:
            errors.append(
                {
                    "row": index,
                    "message": "Row does not match the test-case contract.",
                    "error_type": type(exc).__name__,
                }
            )
    if errors:
        raise EvalForgeError(
            "import_validation_failed",
            "No cases were imported because one or more rows are invalid.",
            status_code=422,
            details=errors,
        )
    created = [repository.add_case(dataset_id, case) for case in parsed]
    session.commit()
    return {
        "imported": len(created),
        "items": [TestCaseRead.model_validate(case).model_dump(mode="json") for case in created],
    }


@router.get("/datasets/{dataset_id}/export")
def export_dataset(
    dataset_id: str,
    session: SessionDep,
    format: str = Query(default="json", pattern="^(json|csv)$"),
) -> Response:
    dataset = DatasetRepository(session).get(dataset_id, with_cases=True)
    if format == "json":
        payload = DatasetDetail.model_validate(dataset).model_dump(mode="json")
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="dataset-{dataset.id}.json"'},
        )
    buffer = StringIO()
    fieldnames = [
        "external_id",
        "input_text",
        "context_text",
        "context_chunks",
        "expected_output",
        "required_phrases",
        "constraints_json",
        "tags",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for case in sorted(dataset.cases, key=lambda item: item.position):
        writer.writerow(
            {
                "external_id": _csv_safe(case.external_id),
                "input_text": _csv_safe(case.input_text),
                "context_text": _csv_safe(case.context_text or ""),
                "context_chunks": _csv_safe(json.dumps(case.context_chunks, ensure_ascii=False)),
                "expected_output": _csv_safe(case.expected_output or ""),
                "required_phrases": json.dumps(case.required_phrases, ensure_ascii=False),
                "constraints_json": json.dumps(case.constraints_json, ensure_ascii=False),
                "tags": json.dumps(case.tags, ensure_ascii=False),
            }
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="dataset-{dataset.id}.csv"'},
    )


def _decode_import(filename: str, text: str) -> list[dict[str, Any]]:
    lowered = filename.casefold()
    if lowered.endswith(".csv"):
        return [dict(row) for row in csv.DictReader(StringIO(text))]
    if not lowered.endswith(".json"):
        raise EvalForgeError(
            "unsupported_import", "Only UTF-8 JSON and CSV imports are supported.", status_code=422
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvalForgeError(
            "invalid_json", "The import is not valid JSON.", status_code=422
        ) from exc
    rows = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise EvalForgeError(
            "invalid_import_shape",
            "JSON import must be a list of cases or an object with a cases list.",
            status_code=422,
        )
    return rows


def _json_field(value: Any, *, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _case_from_mapping(row: dict[str, Any], *, position: int) -> TestCaseCreate:
    context = row.get("context_text", row.get("context"))
    if isinstance(context, list):
        context_chunks = [str(item) for item in context]
        context = "\n\n".join(context_chunks)
    else:
        parsed_chunks = _json_field(row.get("context_chunks"), default=[])
        context_chunks = [str(item) for item in parsed_chunks] if parsed_chunks else []
        if context_chunks:
            context = "\n\n".join(context_chunks)
    metadata = _json_field(row.get("metadata_json", row.get("metadata")), default={})
    keywords = _json_field(row.get("relevance_keywords"), default=[])
    if keywords:
        metadata = {**dict(metadata), "relevance_keywords": list(keywords)}
    return TestCaseCreate(
        external_id=str(row.get("external_id") or row.get("name") or f"case-{position + 1}"),
        position=position,
        input_text=str(row.get("input_text", row.get("input", ""))),
        context_text=str(context) if context is not None else None,
        context_chunks=context_chunks,
        expected_output=row.get("expected_output", row.get("reference")),
        required_phrases=list(_json_field(row.get("required_phrases"), default=[])),
        constraints_json=dict(
            _json_field(row.get("constraints_json", row.get("criteria")), default={})
        ),
        tags=list(_json_field(row.get("tags"), default=[])),
        metadata_json=dict(metadata),
    )


def _csv_safe(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
