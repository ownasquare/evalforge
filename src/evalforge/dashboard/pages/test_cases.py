"""Dataset, test-case, and prompt library management."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from evalforge.dashboard import state
from evalforge.dashboard.client import ApiError, JsonObject, collection_items
from evalforge.dashboard.components import (
    first_value,
    format_timestamp,
    page_header,
    render_api_error,
    render_empty_state,
    render_partial_state,
    resource_id,
    resource_label,
    safe_text_panel,
)
from evalforge.dashboard.pages.common import client, list_payload, load_resource, option_map

_MAX_IMPORT_BYTES = 2 * 1024 * 1024


def render() -> None:
    page_header(
        "Benchmarks",
        "Manage datasets, expected answers, scoring criteria, and prompt versions.",
        eyebrow="Benchmark library",
    )
    editable = state.can_edit()
    if not editable:
        st.info(
            "Viewer access is read-only. You can inspect and export benchmark evidence.",
            icon=":material/visibility:",
        )
    api = client()
    datasets_payload, dataset_error = load_resource("datasets", api.datasets)
    prompts_payload, prompt_error = load_resource("prompt library", api.prompts)
    if dataset_error and prompt_error:
        render_api_error(dataset_error)
        render_partial_state("The prompt library is also unavailable.")
        return

    dataset_tab, prompt_tab = st.tabs(
        [":material/dataset: Test cases", ":material/description: Prompt library"]
    )
    with dataset_tab:
        if dataset_error:
            render_api_error(dataset_error, title="Datasets could not be loaded")
        else:
            _render_datasets(list_payload(datasets_payload), editable=editable)
    with prompt_tab:
        if prompt_error:
            render_api_error(prompt_error, title="Prompt templates could not be loaded")
        else:
            _render_prompts(list_payload(prompts_payload), editable=editable)


def _render_datasets(datasets: list[JsonObject], *, editable: bool) -> None:
    api = client()
    st.subheader("Benchmark datasets")
    if datasets:
        rows = [
            {
                "Name": resource_label(item, fallback="Dataset"),
                "Cases": first_value(item, "case_count", "test_case_count", default="—"),
                "Version": first_value(item, "version", "revision", default="—"),
                "Updated": format_timestamp(first_value(item, "updated_at", "created_at")),
            }
            for item in datasets
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        render_empty_state(
            "No datasets yet",
            "Create a dataset or import JSON/CSV test cases to begin.",
            icon=":material/dataset:",
        )

    if editable:
        with st.expander("Create dataset", icon=":material/add_circle:"):
            with st.form("create-dataset"):
                name = st.text_input("Dataset name", max_chars=120)
                description = st.text_area("Description", max_chars=1000)
                submitted = st.form_submit_button("Create dataset", type="primary")
            if submitted:
                if not name.strip():
                    st.warning("Dataset name is required.")
                else:
                    try:
                        api.create_dataset(
                            {"name": name.strip(), "description": description.strip()}
                        )
                    except ApiError as error:
                        render_api_error(error, title="The dataset was not created")
                    else:
                        st.success("Dataset created.")
                        st.rerun()

    if not datasets:
        return
    options = option_map(datasets, fallback="Dataset")
    dataset_id = st.selectbox(
        "Manage dataset",
        options=list(options),
        format_func=lambda value: options.get(value, value),
    )
    detail, detail_error = load_resource("dataset cases", lambda: api.dataset(dataset_id))
    if detail_error:
        render_api_error(detail_error, title="Dataset cases could not be loaded")
        return
    detail_object = detail if isinstance(detail, dict) else {}
    cases = collection_items(detail_object.get("cases", []))
    if not cases and isinstance(detail_object.get("test_cases"), list):
        cases = [item for item in detail_object["test_cases"] if isinstance(item, dict)]
    _render_case_table(cases)
    if editable:
        with st.expander("Add or edit cases", icon=":material/edit:"):
            _render_case_forms(dataset_id, cases)
    with st.expander("Import or export", icon=":material/import_export:"):
        _render_import_export(dataset_id, editable=editable)


def _render_case_table(cases: list[JsonObject]) -> None:
    st.subheader("Cases in this dataset")
    if not cases:
        render_empty_state(
            "No cases in this dataset",
            "Add one manually or import a JSON/CSV fixture.",
        )
        return
    rows = [
        {
            "Case": resource_label(case, fallback=f"Case {index}"),
            "Input": _truncate(first_value(case, "input_text", "input", "question", default="")),
            "Reference": _truncate(
                first_value(case, "expected_output", "reference", "reference_output", default="")
            ),
            "Context": "Yes" if first_value(case, "context_text", "context") else "No",
            "Tags": _tags(case.get("tags")),
        }
        for index, case in enumerate(cases, start=1)
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_case_forms(dataset_id: str, cases: list[JsonObject]) -> None:
    api = client()
    create_tab, edit_tab = st.tabs(["Add case", "Edit case"])
    with create_tab:
        with st.form(f"create-case-{dataset_id}"):
            name = st.text_input("Case name", max_chars=160)
            input_text = st.text_area("Input", height=120, max_chars=20_000)
            expected = st.text_area(
                "Expected output / reference",
                height=120,
                max_chars=20_000,
                help=(
                    "A trusted answer used by correctness checks. Leave it blank when the case "
                    "should be judged only against source context or explicit criteria."
                ),
            )
            context = st.text_area(
                "Source context (optional)",
                height=120,
                max_chars=50_000,
                help=(
                    "Facts the answer must stay grounded in. Context enables groundedness and "
                    "hallucination checks."
                ),
            )
            tags = st.text_input("Tags (comma-separated)", max_chars=500)
            with st.expander("Advanced scoring criteria", icon=":material/tune:"):
                required_phrases = st.text_area(
                    "Required phrases",
                    placeholder="One phrase per line",
                    help="The answer must contain each phrase for phrase coverage to pass.",
                )
                relevance_keywords = st.text_area(
                    "Relevance keywords",
                    placeholder="refund, 30 days, unopened",
                    help="Keywords used by the relevance check instead of inferring them.",
                )
                expects_json = st.checkbox(
                    "Require valid JSON",
                    help="Enable the JSON-validity check for this case.",
                )
                json_schema_text = st.text_area(
                    "JSON Schema (optional)",
                    placeholder='{"type": "object", "required": ["answer"]}',
                    help="Optional inline schema. External references are not supported.",
                )
            submitted = st.form_submit_button("Add test case", type="primary")
        if submitted:
            if not input_text.strip():
                st.warning("Input is required.")
            else:
                try:
                    criteria = _criteria_payload(
                        required_phrases=required_phrases,
                        relevance_keywords=relevance_keywords,
                        expects_json=expects_json,
                        json_schema_text=json_schema_text,
                    )
                except ValueError as error:
                    st.warning(str(error))
                    return
                payload = {
                    "external_id": name.strip() or "untitled-case",
                    "position": len(cases),
                    "input_text": input_text,
                    "expected_output": expected or None,
                    "context_text": context or None,
                    "tags": _split_tags(tags),
                    **criteria,
                }
                try:
                    api.create_test_case(dataset_id, payload)
                except ApiError as error:
                    render_api_error(error, title="The test case was not created")
                else:
                    st.success("Test case added.")
                    st.rerun()

    with edit_tab:
        if not cases:
            st.info("Add a test case before editing.")
            return
        case_by_id = {resource_id(case): case for case in cases if resource_id(case)}
        case_options = {
            case_id: resource_label(case, fallback="Test case")
            for case_id, case in case_by_id.items()
        }
        case_id = st.selectbox(
            "Case to edit",
            options=list(case_options),
            format_func=lambda value: case_options.get(value, value),
        )
        selected = case_by_id[case_id]
        selected_constraints = first_value(
            selected,
            "constraints_json",
            "constraints",
            default={},
        )
        selected_constraints = (
            selected_constraints if isinstance(selected_constraints, dict) else {}
        )
        selected_metadata = first_value(selected, "metadata_json", "metadata", default={})
        selected_metadata = selected_metadata if isinstance(selected_metadata, dict) else {}
        selected_schema = selected_constraints.get("json_schema")
        with st.form(f"edit-case-{dataset_id}-{case_id}"):
            edit_name = st.text_input(
                "Case name",
                value=str(first_value(selected, "external_id", "name", "title", default="")),
                key=f"edit-case-name-{case_id}",
            )
            edit_input = st.text_area(
                "Input",
                value=str(first_value(selected, "input_text", "input", "question", default="")),
                key=f"edit-case-input-{case_id}",
            )
            edit_expected = st.text_area(
                "Expected output / reference",
                value=str(
                    first_value(
                        selected,
                        "expected_output",
                        "reference",
                        "reference_output",
                        default="",
                    )
                ),
                key=f"edit-case-reference-{case_id}",
            )
            edit_context = st.text_area(
                "Source context",
                value=str(first_value(selected, "context_text", "context", default="")),
                key=f"edit-case-context-{case_id}",
                help=(
                    "Facts the answer must stay grounded in. Context enables groundedness and "
                    "hallucination checks."
                ),
            )
            with st.expander("Advanced scoring criteria", icon=":material/tune:"):
                edit_required_phrases = st.text_area(
                    "Required phrases",
                    value="\n".join(
                        str(value)
                        for value in selected.get("required_phrases", [])
                        if isinstance(value, str)
                    ),
                    key=f"edit-case-required-{case_id}",
                )
                edit_relevance_keywords = st.text_area(
                    "Relevance keywords",
                    value="\n".join(
                        str(value)
                        for value in selected_metadata.get("relevance_keywords", [])
                        if isinstance(value, str)
                    ),
                    key=f"edit-case-relevance-{case_id}",
                )
                edit_expects_json = st.checkbox(
                    "Require valid JSON",
                    value=(
                        selected_constraints.get("expects_json") is True
                        or isinstance(selected_schema, dict)
                    ),
                    key=f"edit-case-json-{case_id}",
                )
                edit_json_schema_text = st.text_area(
                    "JSON Schema (optional)",
                    value=(
                        json.dumps(selected_schema, indent=2, ensure_ascii=False)
                        if isinstance(selected_schema, dict)
                        else ""
                    ),
                    key=f"edit-case-schema-{case_id}",
                )
            updated = st.form_submit_button("Save changes", type="primary")
        if updated:
            if not edit_input.strip():
                st.warning("Input is required.")
            else:
                try:
                    criteria = _criteria_payload(
                        required_phrases=edit_required_phrases,
                        relevance_keywords=edit_relevance_keywords,
                        expects_json=edit_expects_json,
                        json_schema_text=edit_json_schema_text,
                    )
                except ValueError as error:
                    st.warning(str(error))
                    return
                constraints = dict(selected_constraints)
                constraints.pop("expects_json", None)
                constraints.pop("json_schema", None)
                constraints.update(criteria["constraints_json"])
                metadata = dict(selected_metadata)
                metadata["relevance_keywords"] = criteria["metadata_json"]["relevance_keywords"]
                try:
                    api.update_test_case(
                        case_id,
                        {
                            "external_id": edit_name.strip() or "untitled-case",
                            "input_text": edit_input,
                            "expected_output": edit_expected or None,
                            "context_text": edit_context or None,
                            "tags": selected.get("tags", []),
                            "required_phrases": criteria["required_phrases"],
                            "constraints_json": constraints,
                            "metadata_json": metadata,
                        },
                    )
                except ApiError as error:
                    render_api_error(error, title="The test case was not updated")
                else:
                    st.success("Test case updated.")
                    st.rerun()


def _render_import_export(dataset_id: str, *, editable: bool) -> None:
    api = client()
    if not editable:
        _render_dataset_export(api, dataset_id)
        return
    import_column, export_column = st.columns(2)
    with import_column:
        st.subheader("Import cases")
        uploaded = st.file_uploader(
            "JSON or CSV file",
            type=["json", "csv"],
            accept_multiple_files=False,
            key=f"case-import-{dataset_id}",
            help=(
                "JSON may be a case list or an object with a cases list. CSV requires "
                "input_text; list and object columns use JSON text. See docs/api.md and the "
                "examples folder for copyable templates."
            ),
        )
        if uploaded is not None:
            size = getattr(uploaded, "size", None)
            if isinstance(size, int) and size > _MAX_IMPORT_BYTES:
                st.error("The import exceeds the 2 MB dashboard limit.")
            elif st.button("Import into dataset", type="primary", key=f"import-{dataset_id}"):
                content = uploaded.getvalue()
                if len(content) > _MAX_IMPORT_BYTES:
                    st.error("The import exceeds the 2 MB dashboard limit.")
                else:
                    content_type = (
                        "text/csv" if uploaded.name.lower().endswith(".csv") else "application/json"
                    )
                    try:
                        result = api.import_cases(
                            filename=uploaded.name,
                            content=content,
                            content_type=content_type,
                            dataset_id=dataset_id,
                        )
                    except ApiError as error:
                        render_api_error(error, title="The import failed")
                    else:
                        count = first_value(
                            result,
                            "imported",
                            "imported_count",
                            "created_count",
                            default="",
                        )
                        st.success("Import completed.")
                        if count != "":
                            st.text(f"Cases added: {count}")
                        st.rerun()
    with export_column:
        _render_dataset_export(api, dataset_id)


def _render_dataset_export(api: Any, dataset_id: str) -> None:
    st.subheader("Export cases")
    export_format = st.selectbox(
        "Export format",
        options=["json", "csv"],
        key=f"export-format-{dataset_id}",
    )
    export_key = f"export-data-{dataset_id}-{export_format}"
    if st.button("Prepare export", key=f"prepare-export-{dataset_id}"):
        try:
            st.session_state[export_key] = api.export_dataset(
                dataset_id, export_format=export_format
            )
        except ApiError as error:
            render_api_error(error, title="The export could not be prepared")
    export_data = st.session_state.get(export_key)
    if isinstance(export_data, bytes):
        mime = "text/csv" if export_format == "csv" else "application/json"
        st.download_button(
            "Download export",
            data=export_data,
            file_name=f"evalforge-{dataset_id}.{export_format}",
            mime=mime,
            type="primary",
            width="stretch",
        )


def _render_prompts(prompts: list[JsonObject], *, editable: bool) -> None:
    api = client()
    st.subheader("Prompt templates")
    st.caption(
        "Allowed placeholders: {input} and {context}. Reference answers are evaluator-only. "
        "Templates are validated before a run is created."
    )
    if prompts:
        rows = [
            {
                "Name": resource_label(prompt, fallback="Prompt"),
                "Version": first_value(prompt, "version", "revision", default="—"),
                "Created": format_timestamp(first_value(prompt, "created_at")),
                "Active": first_value(prompt, "active", "is_active", default=True),
            }
            for prompt in prompts
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        render_empty_state(
            "No prompt versions yet",
            "Create a strict prompt template before running an evaluation.",
            icon=":material/description:",
        )

    if not editable:
        _render_prompt_inspector(prompts)
        return

    with st.expander("Create, edit or inspect prompts", icon=":material/edit_document:"):
        create_tab, edit_tab, inspect_tab = st.tabs(["Create", "Edit", "Inspect"])
        with create_tab:
            with st.form("create-prompt"):
                name = st.text_input("Prompt name", max_chars=120)
                description = st.text_area("Description", max_chars=1000)
                system_template = st.text_area("System template", height=120, max_chars=20_000)
                user_template = st.text_area(
                    "User template",
                    value="{input}",
                    height=160,
                    max_chars=50_000,
                )
                submitted = st.form_submit_button("Create prompt", type="primary")
            if submitted:
                if not name.strip() or not user_template.strip():
                    st.warning("Prompt name and user template are required.")
                else:
                    try:
                        api.create_prompt(
                            {
                                "name": name.strip(),
                                "description": description.strip(),
                                "system_template": system_template,
                                "user_template": user_template,
                            }
                        )
                    except ApiError as error:
                        render_api_error(error, title="The prompt was not created")
                    else:
                        st.success("Prompt created.")
                        st.rerun()

        with edit_tab:
            if not prompts:
                st.info("Create a prompt before editing.")
            else:
                prompt_by_id = {
                    resource_id(prompt): prompt for prompt in prompts if resource_id(prompt)
                }
                options = {
                    prompt_id: resource_label(prompt, fallback="Prompt")
                    for prompt_id, prompt in prompt_by_id.items()
                }
                prompt_id = st.selectbox(
                    "Prompt to edit",
                    options=list(options),
                    format_func=lambda value: options.get(value, value),
                    key="prompt-edit-selector",
                )
                selected = prompt_by_id[prompt_id]
                with st.form(f"edit-prompt-{prompt_id}"):
                    edited_name = st.text_input(
                        "Prompt name",
                        value=str(first_value(selected, "name", "title", default="")),
                    )
                    edited_description = st.text_area(
                        "Description",
                        value=str(first_value(selected, "description", default="")),
                    )
                    edited_system = st.text_area(
                        "System template",
                        value=str(
                            first_value(
                                selected,
                                "system_template",
                                "system_prompt",
                                default="",
                            )
                        ),
                    )
                    edited_user = st.text_area(
                        "User template",
                        value=str(first_value(selected, "user_template", "template", default="")),
                    )
                    updated = st.form_submit_button("Save changes", type="primary")
                if updated:
                    try:
                        api.update_prompt(
                            prompt_id,
                            {
                                "name": edited_name.strip(),
                                "description": edited_description.strip(),
                                "system_template": edited_system,
                                "user_template": edited_user,
                            },
                        )
                    except ApiError as error:
                        render_api_error(error, title="The prompt was not updated")
                    else:
                        st.success("Prompt updated.")
                        st.rerun()

        with inspect_tab:
            _render_prompt_inspector(prompts)


def _render_prompt_inspector(prompts: list[JsonObject]) -> None:
    if not prompts:
        st.info("No prompt versions are available to inspect.")
        return
    prompt_by_id = {resource_id(prompt): prompt for prompt in prompts if resource_id(prompt)}
    options = {
        prompt_id: resource_label(prompt, fallback="Prompt")
        for prompt_id, prompt in prompt_by_id.items()
    }
    prompt_id = st.selectbox(
        "Prompt to inspect",
        options=list(options),
        format_func=lambda value: options.get(value, value),
        key="prompt-inspect-selector",
    )
    prompt = prompt_by_id[prompt_id]
    safe_text_panel(
        "System template",
        first_value(prompt, "system_template", "system_prompt", default=""),
    )
    safe_text_panel("User template", first_value(prompt, "user_template", "template", default=""))
    metadata = {
        key: prompt[key]
        for key in ("id", "version", "template_hash", "created_at", "updated_at")
        if key in prompt
    }
    st.code(json.dumps(metadata, indent=2, default=str), language="json")


def _truncate(value: Any, length: int = 100) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= length else f"{text[: length - 1]}…"


def _tags(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(tag) for tag in value)
    return str(value or "")


def _split_terms(value: str) -> list[str]:
    """Parse compact comma/newline criteria while preserving user order."""
    terms: list[str] = []
    for line in value.splitlines():
        for part in line.split(","):
            term = part.strip()
            if term and term not in terms:
                terms.append(term)
    return terms


def _criteria_payload(
    *,
    required_phrases: str,
    relevance_keywords: str,
    expects_json: bool,
    json_schema_text: str,
) -> dict[str, Any]:
    """Translate the advanced case disclosure into the published API fields."""
    schema_text = json_schema_text.strip()
    schema: dict[str, Any] | None = None
    if schema_text:
        try:
            decoded = json.loads(schema_text)
        except json.JSONDecodeError as error:
            raise ValueError("JSON Schema must be valid JSON.") from error
        if not isinstance(decoded, dict):
            raise ValueError("JSON Schema must be a JSON object.")
        schema = decoded

    constraints: dict[str, Any] = {}
    if expects_json or schema is not None:
        constraints["expects_json"] = True
    if schema is not None:
        constraints["json_schema"] = schema

    return {
        "required_phrases": _split_terms(required_phrases),
        "constraints_json": constraints,
        "metadata_json": {"relevance_keywords": _split_terms(relevance_keywords)},
    }


def _split_tags(value: str) -> list[str]:
    return sorted({tag.strip() for tag in value.split(",") if tag.strip()})
