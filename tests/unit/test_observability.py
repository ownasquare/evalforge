from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from evalforge.observability import (
    BodyLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodyLimitMiddleware, max_bytes=16)
    app.add_middleware(RequestContextMiddleware)

    @app.post("/echo")
    def echo() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/explode")
    def explode() -> None:
        raise RuntimeError("intentional test failure")

    return app


def test_request_id_and_security_headers_are_returned() -> None:
    response = TestClient(_app()).post("/echo", headers={"X-Request-ID": "test-request-7"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "test-request-7"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_unsafe_request_id_is_replaced() -> None:
    response = TestClient(_app()).post("/echo", headers={"X-Request-ID": "contains spaces"})
    assert response.status_code == 200
    assert response.headers["x-request-id"].startswith("req_")


def test_declared_oversized_body_is_rejected() -> None:
    response = TestClient(_app()).post("/echo", content=b"x" * 17)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_unexpected_errors_keep_safe_envelope_request_id_and_security_headers() -> None:
    response = TestClient(_app()).get("/explode", headers={"X-Request-ID": "failure-proof"})

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_error",
        "message": "The server could not complete the request.",
        "retryable": False,
        "request_id": "failure-proof",
        "details": [],
    }
    assert response.headers["x-request-id"] == "failure-proof"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_chunked_oversized_body_is_rejected_by_actual_bytes() -> None:
    messages = iter(
        [
            {"type": "http.request", "body": b"12345678", "more_body": True},
            {"type": "http.request", "body": b"123456789", "more_body": False},
        ]
    )
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return next(messages)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def consume_body(_scope: dict[str, Any], receive_body: Any, _send: Any) -> None:
        while True:
            message = await receive_body()
            if not message.get("more_body", False):
                return

    middleware = BodyLimitMiddleware(consume_body, max_bytes=16)  # type: ignore[arg-type]
    await middleware(
        {"type": "http", "method": "POST", "path": "/", "headers": []},  # type: ignore[arg-type]
        receive,  # type: ignore[arg-type]
        send,  # type: ignore[arg-type]
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
