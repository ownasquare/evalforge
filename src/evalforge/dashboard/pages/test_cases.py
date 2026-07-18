"""Dataset, test-case, and prompt library management."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

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
        "Test cases & prompt library",
        "Curate benchmark inputs and auditable prompt templates before running models.",
        eyebrow="Evaluation assets",
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
            _render_datasets(list_payload(datasets_payload))
    with prompt_tab:
        if prompt_error:
            render_api_error(prompt_error, title="Prompt templates could not be loaded")
        else:
            _render_prompts(list_payload(prompts_payload))


def _render_datasets(datasets: list[JsonObject]) -> None:
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

    with st.expander("Create dataset", icon=":material/add_circle:"):
        with st.form("create-dataset", clear_on_submit=True):
            name = st.text_input("Dataset name", max_chars=120)
            description = st.text_area("Description", max_chars=1000)
            submitted = st.form_submit_button("Create dataset", type="primary")
        if submitted:
            if not name.strip():
                st.warning("Dataset name is required.")
            else:
                try:
                    api.create_dataset({"name": name.strip(), "description": description.strip()})
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
    _render_case_forms(dataset_id, cases)
    _render_import_export(dataset_id)


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
        with st.form(f"create-case-{dataset_id}", clear_on_submit=True):
            name = st.text_input("Case name", max_chars=160)
            input_text = st.text_area("Input", height=120, max_chars=20_000)
            expected = st.text_area("Expected output / reference", height=120, max_chars=20_000)
            context = st.text_area("Source context (optional)", height=120, max_chars=50_000)
            tags = st.text_input("Tags (comma-separated)", max_chars=500)
            submitted = st.form_submit_button("Add test case", type="primary")
        if submitted:
            if not input_text.strip():
                st.warning("Input is required.")
            else:
                payload = {
                    "external_id": name.strip() or "untitled-case",
                    "position": len(cases),
                    "input_text": input_text,
                    "expected_output": expected or None,
                    "context_text": context or None,
                    "required_phrases": [],
                    "constraints_json": {},
                    "tags": _split_tags(tags),
                    "metadata_json": {},
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
            )
            updated = st.form_submit_button("Save changes", type="primary")
        if updated:
            if not edit_input.strip():
                st.warning("Input is required.")
            else:
                try:
                    api.update_test_case(
                        case_id,
                        {
                            "external_id": edit_name.strip() or "untitled-case",
                            "input_text": edit_input,
                            "expected_output": edit_expected or None,
                            "context_text": edit_context or None,
                            "tags": selected.get("tags", []),
                        },
                    )
                except ApiError as error:
                    render_api_error(error, title="The test case was not updated")
                else:
                    st.success("Test case updated.")
                    st.rerun()


def _render_import_export(dataset_id: str) -> None:
    api = client()
    import_column, export_column = st.columns(2)
    with import_column:
        st.subheader("Import cases")
        uploaded = st.file_uploader(
            "JSON or CSV file",
            type=["json", "csv"],
            accept_multiple_files=False,
            key=f"case-import-{dataset_id}",
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


def _render_prompts(prompts: list[JsonObject]) -> None:
    api = client()
    st.subheader("Prompt templates")
    st.info(
        "Allowed placeholders: {input} and {context}. Reference answers are evaluator-only. "
        "The API validates templates before a run is created.",
        icon=":material/verified_user:",
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

    create_tab, edit_tab, inspect_tab = st.tabs(["Create", "Edit", "Inspect"])
    with create_tab:
        with st.form("create-prompt", clear_on_submit=True):
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
                    "Description", value=str(first_value(selected, "description", default=""))
                )
                edited_system = st.text_area(
                    "System template",
                    value=str(
                        first_value(selected, "system_template", "system_prompt", default="")
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
        if not prompts:
            st.info("Create a prompt to inspect its exact bytes.")
        else:
            prompt_by_id = {
                resource_id(prompt): prompt for prompt in prompts if resource_id(prompt)
            }
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
            safe_text_panel(
                "User template", first_value(prompt, "user_template", "template", default="")
            )
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


def _split_tags(value: str) -> list[str]:
    return sorted({tag.strip() for tag in value.split(",") if tag.strip()})
