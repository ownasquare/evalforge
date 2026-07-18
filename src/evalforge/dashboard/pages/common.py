"""Shared helpers for dashboard page renderers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

import streamlit as st

from evalforge.dashboard.client import ApiClient, ApiError, JsonObject, collection_items
from evalforge.dashboard.components import first_value, resource_id, resource_label
from evalforge.dashboard.state import get_client

T = TypeVar("T")


def client() -> ApiClient:
    return get_client()


def load_resource(
    label: str,
    loader: Callable[[], T],
) -> tuple[T | None, ApiError | None]:
    with st.spinner(f"Loading {label}…", show_time=False):
        try:
            return loader(), None
        except ApiError as error:
            return None, error


def object_payload(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}


def list_payload(value: Any) -> list[JsonObject]:
    return collection_items(value)


def load_all_runs(
    api: Any,
    *,
    page_size: int = 200,
) -> tuple[list[JsonObject], int | None, ApiError | None]:
    """Load complete run history so older evaluations remain selectable."""

    try:
        payload = api.runs(limit=page_size, page=1)
    except ApiError as error:
        return [], None, error
    runs = list_payload(payload)
    total_value = payload.get("total") if isinstance(payload, dict) else len(runs)
    total = (
        total_value
        if isinstance(total_value, int) and not isinstance(total_value, bool) and total_value >= 0
        else len(runs)
    )
    page = 2
    while len(runs) < total:
        try:
            page_payload = api.runs(limit=page_size, page=page)
        except ApiError as error:
            return runs, total, error
        page_runs = list_payload(page_payload)
        if not page_runs:
            return (
                runs,
                total,
                ApiError("The API returned fewer runs than its pagination total"),
            )
        runs.extend(page_runs)
        page += 1
    return runs, total, None


def option_map(items: list[JsonObject], *, fallback: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for item in items:
        item_id = resource_id(item)
        if item_id:
            options[item_id] = resource_label(item, fallback=fallback)
    return options


def run_label(run: Mapping[str, Any]) -> str:
    run_id = resource_id(run)
    name = str(first_value(run, "name", "title", default=f"Run {run_id[:8]}"))
    status = str(first_value(run, "status", default="unknown")).replace("_", " ").title()
    created = str(first_value(run, "created_at", "started_at", default=""))[:16]
    suffix = f" · {created}" if created else ""
    return f"{name} · {status}{suffix}"


def nested_summary(payload: Mapping[str, Any]) -> JsonObject:
    for key in ("summary", "analytics", "totals", "metrics", "aggregates"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return dict(payload)


def safe_status(payload: Mapping[str, Any]) -> str:
    return str(first_value(payload, "status", "state", default="unknown"))
