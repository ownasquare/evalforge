"""Secret-safe request context, structured logging, metrics, and security headers."""

from __future__ import annotations

import logging
import re
import time
from contextvars import ContextVar, Token
from typing import Any
from uuid import uuid4

import structlog
from fastapi import Request, Response
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REQUEST_ID: ContextVar[str] = ContextVar("evalforge_request_id", default="unknown")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")

REQUESTS = Counter(
    "evalforge_http_requests_total",
    "HTTP requests handled by EvalForge.",
    ("method", "route", "status"),
)
LATENCY = Histogram(
    "evalforge_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
)


def current_request_id() -> str:
    """Return the active request correlation identifier."""
    return _REQUEST_ID.get()


def get_logger(name: str) -> Any:
    """Return a bound structured logger without application data fields."""
    return structlog.get_logger(name)


def configure_logging(*, level: str, json_logs: bool) -> None:
    """Configure stdlib and structlog rendering once per application factory."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, format="%(message)s", force=True)
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a safe request ID and timing header to every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else f"req_{uuid4().hex}"
        token: Token[str] = _REQUEST_ID.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed = time.perf_counter() - started
            _REQUEST_ID.reset(token)
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={elapsed * 1000:.2f}"
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply conservative API response headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
        except Exception as exc:
            get_logger("api").error("unhandled_request_error", error_type=type(exc).__name__)
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "The server could not complete the request.",
                        "retryable": False,
                        "request_id": current_request_id(),
                        "details": [],
                    }
                },
            )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if request.url.path.startswith(("/docs", "/redoc")):
            content_security_policy = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "connect-src 'self'; frame-ancestors 'none'"
            )
        else:
            content_security_policy = "default-src 'none'; frame-ancestors 'none'"
        response.headers.setdefault("Content-Security-Policy", content_security_policy)
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record low-cardinality HTTP counters and duration histograms."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        route_object = request.scope.get("route")
        route = getattr(route_object, "path", "unmatched")
        elapsed = time.perf_counter() - started
        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        LATENCY.labels(request.method, route).observe(elapsed)
        return response


class _RequestBodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Reject request bodies based on actual received bytes, including chunked bodies."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._send_too_large(scope, receive, send)
            return

        received = 0

        async def receive_limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, receive_limited, send)
        except _RequestBodyTooLarge:
            await self._send_too_large(scope, receive, send)

    @staticmethod
    async def _send_too_large(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "request_too_large",
                    "message": "The request body exceeds the configured size limit.",
                    "retryable": False,
                    "request_id": current_request_id(),
                    "details": [],
                }
            },
        )
        await response(scope, receive, send)
