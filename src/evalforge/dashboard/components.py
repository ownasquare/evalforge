"""Reusable, accessible Streamlit components and presentation helpers."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import plotly.graph_objects as go
import streamlit as st

from evalforge.dashboard.client import ApiError


@dataclass(frozen=True, slots=True)
class MetricCard:
    label: str
    value: str
    delta: str | None = None
    help_text: str | None = None


StatusColor = Literal["red", "orange", "yellow", "blue", "green", "violet", "gray"]

_STATUS_COLORS: dict[str, StatusColor] = {
    "completed": "green",
    "completed_with_errors": "orange",
    "passed": "green",
    "healthy": "green",
    "live": "green",
    "ready": "green",
    "running": "blue",
    "cancel_requested": "orange",
    "queued": "violet",
    "pending": "gray",
    "partial": "orange",
    "interrupted": "orange",
    "cancelled": "gray",
    "canceled": "gray",
    "failed": "red",
    "error": "red",
    "offline": "red",
    "not_applicable": "gray",
}

_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "completed_with_errors",
        "failed",
        "error",
        "cancelled",
        "canceled",
        "interrupted",
    }
)


def page_header(title: str, description: str, *, eyebrow: str | None = None) -> None:
    if eyebrow:
        st.caption(eyebrow.upper())
    st.title(title)
    st.caption(description)


def render_metric_cards(cards: Sequence[MetricCard], *, max_columns: int = 4) -> None:
    if not cards:
        return
    column_count = max(1, min(max_columns, len(cards)))
    columns = st.columns(column_count)
    for index, card in enumerate(cards):
        with columns[index % column_count], st.container(border=True):
            st.metric(
                card.label,
                card.value,
                delta=card.delta,
                help=card.help_text,
            )


def render_status_badge(status: str, *, prefix: str | None = None) -> None:
    normalized = status.strip().lower() if status else "unknown"
    label = normalized.replace("_", " ").title()
    if prefix:
        label = f"{prefix}: {label}"
    st.badge(label, color=status_color(normalized))


def status_color(status: str) -> StatusColor:
    return _STATUS_COLORS.get(status.strip().lower(), "gray")


def is_terminal_status(status: str) -> bool:
    return status.strip().lower() in _TERMINAL_STATUSES


def render_demo_banner(*, synthetic: bool = True) -> None:
    if synthetic:
        st.info(
            "Deterministic demo · Outputs, latency, usage, and cost are fixture-backed or "
            "synthetic. They are not live-provider measurements.",
            icon=":material/science:",
        )
    else:
        st.warning(
            "Real-provider run · This can send benchmark inputs to a configured model provider "
            "and may incur cost.",
            icon=":material/paid:",
        )


def render_empty_state(title: str, message: str, *, icon: str = ":material/inbox:") -> None:
    with st.container(border=True):
        st.subheader(title)
        st.info(message, icon=icon)


def render_loading_state(message: str) -> None:
    st.info(message, icon=":material/hourglass_top:")


def render_partial_state(message: str) -> None:
    st.warning(message, icon=":material/data_alert:")


def render_api_error(
    error: ApiError,
    *,
    title: str = "The dashboard could not load this data",
) -> None:
    st.error(title, icon=":material/cloud_off:")
    # st.text deliberately avoids interpreting API-controlled content as HTML or Markdown.
    st.text(str(error))
    if error.retryable:
        st.caption("This looks temporary. Retry after the API is ready.")


def render_flash() -> None:
    from evalforge.dashboard.state import pop_flash

    flash = pop_flash()
    if not flash:
        return
    tone = flash["tone"]
    message = flash["message"]
    if tone == "error":
        st.error(message)
    elif tone == "warning":
        st.warning(message)
    else:
        st.success(message)


def safe_text_panel(label: str, value: Any, *, language: str | None = None) -> None:
    st.caption(label)
    text = "Not provided" if value is None or value == "" else str(value)
    st.code(text, language=language, wrap_lines=True)


def safe_json_panel(label: str, value: Any) -> None:
    st.caption(label)
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = json.dumps(str(value))
    st.code(rendered, language="json", wrap_lines=True)


def format_percent(value: Any, *, digits: int = 1, unavailable: str = "—") -> str:
    number = as_float(value)
    if number is None:
        return unavailable
    if 0.0 <= number <= 1.0:
        number *= 100
    return f"{number:.{digits}f}%"


def format_score(value: Any, *, digits: int = 3, unavailable: str = "—") -> str:
    number = as_float(value)
    return unavailable if number is None else f"{number:.{digits}f}"


def format_currency(value: Any, *, unavailable: str = "—") -> str:
    number = as_float(value)
    if number is None:
        return unavailable
    if abs(number) < 0.01 and number != 0:
        return f"${number:.4f}"
    return f"${number:,.2f}"


def format_micro_usd(value: Any, *, unavailable: str = "—") -> str:
    number = as_float(value)
    if number is None:
        return unavailable
    dollars = number / 1_000_000
    if 0 < abs(dollars) < 0.1:
        return f"${dollars:.4f}"
    return f"${dollars:,.2f}"


def format_count(value: Any, *, unavailable: str = "—") -> str:
    number = as_float(value)
    return unavailable if number is None else f"{int(number):,}"


def format_duration_ms(value: Any, *, unavailable: str = "—") -> str:
    number = as_float(value)
    if number is None:
        return unavailable
    if number >= 1000:
        return f"{number / 1000:.2f} s"
    return f"{number:.0f} ms"


def format_timestamp(value: Any, *, unavailable: str = "Unknown time") -> str:
    if not value:
        return unavailable
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%b %d, %Y · %H:%M UTC")


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def first_value(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return default


def resource_id(item: Mapping[str, Any]) -> str:
    value = first_value(item, "id", "uuid", "run_id", "dataset_id", "prompt_id", "model_id")
    return str(value) if value is not None else ""


def resource_label(item: Mapping[str, Any], *, fallback: str = "Untitled") -> str:
    name = first_value(
        item,
        "name",
        "title",
        "display_name",
        "label",
        "external_id",
        default=fallback,
    )
    version = first_value(item, "version", "revision")
    label = str(name)
    if version not in {None, ""}:
        label = f"{label} · v{version}"
    return label


def is_demo_record(item: Mapping[str, Any]) -> bool:
    explicit = first_value(item, "is_demo", "deterministic", "synthetic")
    if isinstance(explicit, bool):
        return explicit
    provider = str(
        first_value(item, "api_mode", "provider", "provider_type", "kind", default="")
    ).lower()
    return provider in {"demo", "deterministic", "fixture", "offline"}


def normalized_metric_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        iterable: Iterable[tuple[str, Any]] = value.items()
        for name, metric in iterable:
            if isinstance(metric, dict):
                rows.append({"name": name, **metric})
            else:
                rows.append({"name": name, "score": metric})
    elif isinstance(value, list):
        rows.extend(item for item in value if isinstance(item, dict))
    return rows


def style_figure(figure: go.Figure, *, height: int = 340) -> go.Figure:
    figure.update_layout(
        height=height,
        margin={"l": 18, "r": 18, "t": 40, "b": 24},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#263247", "family": "Inter, ui-sans-serif, system-ui"},
        hoverlabel={"bgcolor": "#111827", "font_color": "#FFFFFF"},
        legend={"orientation": "h", "y": -0.18},
    )
    return figure


def render_progress(completed: Any, total: Any, *, status: str) -> None:
    completed_number = as_float(completed) or 0.0
    total_number = as_float(total) or 0.0
    fraction = min(1.0, max(0.0, completed_number / total_number)) if total_number else 0.0
    text = f"{int(completed_number):,} of {int(total_number):,} evaluations · {status}"
    st.progress(fraction, text=text)
