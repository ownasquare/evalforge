from __future__ import annotations

from typing import Any

import httpx
import pytest

from evalforge.dashboard.client import ApiClient, ApiError, collection_items, public_payload


def test_client_surfaces_request_id_on_api_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/meta"
        return httpx.Response(
            503,
            json={"detail": "not ready"},
            headers={"x-request-id": "req-7"},
        )

    with (
        ApiClient(
            "http://api",
            transport=httpx.MockTransport(handler),
            max_read_attempts=1,
        ) as client,
        pytest.raises(ApiError, match="req-7") as captured,
    ):
        client.meta()

    assert captured.value.status_code == 503
    assert captured.value.retryable is True


def test_client_parses_nested_fastapi_error_envelope() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": "validation_error",
                    "message": "The request did not match the API contract.",
                    "request_id": "req-body",
                    "retryable": False,
                }
            },
        )

    with (
        ApiClient(
            "http://api",
            transport=httpx.MockTransport(handler),
            max_read_attempts=1,
        ) as client,
        pytest.raises(ApiError, match="req-body") as captured,
    ):
        client.meta()

    assert captured.value.code == "validation_error"
    assert captured.value.retryable is False
    assert "did not match" in captured.value.message


def test_client_retries_and_caches_idempotent_reads() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"detail": "warming"})
        return httpx.Response(200, json={"total_runs": 3})

    with ApiClient(
        "http://api",
        transport=httpx.MockTransport(handler),
        max_read_attempts=2,
        sleeper=lambda _: None,
    ) as client:
        first = client.overview()
        first["total_runs"] = 99
        second = client.overview()

    assert calls == 2
    assert second == {"total_runs": 3}


def test_client_never_retries_mutations() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "POST"
        return httpx.Response(503, json={"detail": "busy"})

    with (
        ApiClient(
            "http://api",
            transport=httpx.MockTransport(handler),
            max_read_attempts=3,
            sleeper=lambda _: None,
        ) as client,
        pytest.raises(ApiError),
    ):
        client.create_run({"dataset_id": "dataset-1"}, idempotency_key="attempt-1")

    assert calls == 1


@pytest.mark.parametrize(
    "url",
    [
        "ftp://api.example.com",
        "http://user:password@api.example.com",
        "https://api.example.com?token=value",
        "api.example.com",
    ],
)
def test_client_rejects_unsafe_api_origins(url: str) -> None:
    with pytest.raises(ValueError):
        ApiClient(url)


def test_collection_items_handles_list_and_envelopes() -> None:
    assert collection_items([{"id": "one"}, "ignored"]) == [{"id": "one"}]
    assert collection_items({"items": [{"id": "two"}]}) == [{"id": "two"}]
    assert collection_items({"runs": [{"id": "three"}]}) == [{"id": "three"}]
    assert collection_items({"unexpected": []}) == []


def test_public_payload_removes_nested_credentials() -> None:
    payload: dict[str, Any] = {
        "provider": "openai",
        "api_key_configured": True,
        "nested": {"password": "never-render", "available": True},
        "items": [{"access_token": "never-render", "name": "demo"}],
        "total_tokens": 120,
    }

    assert public_payload(payload) == {
        "provider": "openai",
        "nested": {"available": True},
        "items": [{"name": "demo"}],
        "total_tokens": 120,
    }


def test_client_uses_concrete_mutation_routes() -> None:
    requests: list[tuple[str, str]] = []
    idempotency_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/v1/runs":
            idempotency_headers.append(request.headers["idempotency-key"])
        return httpx.Response(200, json={"id": "resource-1"})

    with ApiClient("http://api", transport=httpx.MockTransport(handler)) as client:
        client.update_test_case("case-1", {"input_text": "Updated"})
        client.update_prompt("prompt-1", {"user_template": "{input}"})
        client.preflight_run(
            {"dataset_id": "dataset-1", "prompt_ids": ["prompt-1"], "model_ids": ["model-1"]}
        )
        client.create_run(
            {"dataset_id": "dataset-1"},
            idempotency_key="session-attempt-1",
        )
        client.import_cases(
            filename="cases.json",
            content=b"[]",
            content_type="application/json",
            dataset_id="dataset-1",
        )

    assert requests == [
        ("PATCH", "/api/v1/cases/case-1"),
        ("PATCH", "/api/v1/prompts/prompt-1"),
        ("POST", "/api/v1/runs/preflight"),
        ("POST", "/api/v1/runs"),
        ("POST", "/api/v1/datasets/dataset-1/imports"),
    ]
    assert idempotency_headers == ["session-attempt-1"]


def test_case_import_invalidates_cached_dataset_detail() -> None:
    imported = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal imported
        if request.method == "POST":
            imported = True
            return httpx.Response(200, json={"imported_count": 1})
        return httpx.Response(
            200,
            json={
                "id": "dataset-1",
                "cases": [{"id": "case-1"}] if imported else [],
            },
        )

    with ApiClient("http://api", transport=httpx.MockTransport(handler)) as client:
        assert client.dataset("dataset-1")["cases"] == []
        client.import_cases(
            filename="cases.json",
            content=b"[]",
            content_type="application/json",
            dataset_id="dataset-1",
        )
        refreshed = client.dataset("dataset-1")

    assert refreshed["cases"] == [{"id": "case-1"}]


def test_client_exports_run_evidence_in_raw_and_versioned_formats() -> None:
    requests: list[tuple[str, str, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.method,
                request.url.path,
                request.url.params.get("format"),
                request.url.params.get("disclosure_profile"),
            )
        )
        return httpx.Response(200, content=b"evidence")

    with ApiClient("http://api", transport=httpx.MockTransport(handler)) as client:
        json_export = client.export_run("run-1", export_format="json")
        csv_export = client.export_run("run-1", export_format="csv")
        package_export = client.export_run(
            "run-1",
            export_format="package",
            disclosure_profile="full_evidence",
        )

    assert json_export == b"evidence"
    assert csv_export == b"evidence"
    assert package_export == b"evidence"
    assert requests == [
        ("GET", "/api/v1/runs/run-1/export", "json", "content_redacted"),
        ("GET", "/api/v1/runs/run-1/export", "csv", "content_redacted"),
        ("GET", "/api/v1/runs/run-1/export", "package", "full_evidence"),
    ]


def test_client_rejects_unknown_export_disclosure_profile() -> None:
    with (
        ApiClient(
            "http://api", transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ) as client,
        pytest.raises(ValueError, match="disclosure profile"),
    ):
        client.export_run(
            "run-1",
            export_format="package",
            disclosure_profile="send_everything",
        )


def test_client_attaches_identity_and_workspace_headers_without_exposing_token() -> None:
    observed_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_headers.update(request.headers)
        return httpx.Response(200, json={"user": {"display_name": "Morgan"}})

    with ApiClient(
        "http://api",
        access_token="private-access-token",
        workspace_id="workspace-1",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.session() == {"user": {"display_name": "Morgan"}}
        representation = repr(client)

    assert observed_headers["authorization"] == "Bearer private-access-token"
    assert observed_headers["x-evalforge-workspace-id"] == "workspace-1"
    assert "private-access-token" not in representation
    assert "private-access-token" not in str(client.identity_fingerprint)


def test_client_partitions_cache_by_fingerprint_and_workspace() -> None:
    client = ApiClient(
        "http://api",
        access_token="private-access-token",
        workspace_id="workspace-1",
    )

    key = client._cache_key("/api/v1/runs", {"page": 1})

    assert client.identity_fingerprint in key
    assert "workspace-1" in key
    assert "private-access-token" not in repr(key)
    client.close()


def test_client_resets_identity_on_401_but_not_403() -> None:
    unauthorized_events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = 401 if request.url.path.endswith("session") else 403
        return httpx.Response(status, json={"detail": "Access denied"})

    with ApiClient(
        "http://api",
        access_token="private-access-token",
        workspace_id="workspace-1",
        transport=httpx.MockTransport(handler),
        max_read_attempts=1,
        on_unauthorized=lambda: unauthorized_events.append("reset"),
    ) as client:
        with pytest.raises(ApiError) as unauthorized:
            client.session()
        with pytest.raises(ApiError) as forbidden:
            client.workspaces()

    assert unauthorized.value.status_code == 401
    assert forbidden.value.status_code == 403
    assert unauthorized_events == ["reset"]


def test_client_redacts_echoed_bearer_token_from_api_errors() -> None:
    token = "private-access-token"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": f"Rejected Bearer {token}"})

    with (
        ApiClient(
            "http://api",
            access_token=token,
            transport=httpx.MockTransport(handler),
            max_read_attempts=1,
        ) as client,
        pytest.raises(ApiError) as captured,
    ):
        client.session()

    assert token not in str(captured.value)
    assert "[credential]" in str(captured.value)


def test_client_treats_a_missing_live_token_as_reauthentication() -> None:
    unauthorized_events: list[str] = []
    client = ApiClient(
        "http://api",
        access_token_provider=lambda: None,
        identity_fingerprint="known-fingerprint",
        on_unauthorized=lambda: unauthorized_events.append("reset"),
    )

    with pytest.raises(ApiError) as captured:
        client.session()

    assert captured.value.status_code == 401
    assert captured.value.code == "reauthentication_required"
    assert unauthorized_events == ["reset"]
    client.close()
