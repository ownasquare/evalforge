"""Typed, credential-free HTTP client for the EvalForge dashboard.

The Streamlit process is deliberately an API client only.  Provider credentials,
database connections, and model execution remain exclusively in FastAPI.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias
from urllib.parse import urlsplit

import httpx

JsonObject: TypeAlias = dict[str, Any]
QueryValue: TypeAlias = str | int | float | bool | Sequence[str]

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


class ApiError(RuntimeError):
    """Safe dashboard-facing representation of an API or transport failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        code: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.code = code
        self.retryable = retryable
        super().__init__(self.__str__())

    def __str__(self) -> str:
        qualifiers: list[str] = []
        if self.status_code is not None:
            qualifiers.append(f"HTTP {self.status_code}")
        if self.request_id:
            qualifiers.append(f"request {self.request_id}")
        suffix = f" ({', '.join(qualifiers)})" if qualifiers else ""
        return f"{self.message}{suffix}"


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    value: Any


class ApiClient:
    """Small synchronous API client suited to Streamlit's rerun model."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        timeout_seconds: float = 8.0,
        connect_timeout_seconds: float = 2.0,
        max_read_attempts: int = 2,
        cache_ttl_seconds: float = 4.0,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        access_token: str | None = None,
        access_token_provider: Callable[[], str | None] | None = None,
        identity_fingerprint: str | None = None,
        workspace_id: str | None = None,
        on_unauthorized: Callable[[], None] | None = None,
    ) -> None:
        self.base_url = self.validate_base_url(base_url)
        if access_token is not None and access_token_provider is not None:
            raise ValueError("Provide either access_token or access_token_provider, not both")
        if max_read_attempts < 1:
            raise ValueError("max_read_attempts must be at least one")
        if timeout_seconds <= 0 or connect_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        validated_token = _validated_header_value(
            access_token,
            name="access token",
            maximum=16_384,
        )
        self.workspace_id = _validated_header_value(
            workspace_id,
            name="workspace ID",
            maximum=256,
        )
        self._access_token_provider: Callable[[], str | None] | None
        if access_token_provider is not None:
            self._access_token_provider = access_token_provider
        elif validated_token is not None:

            def static_token_provider() -> str | None:
                return validated_token

            self._access_token_provider = static_token_provider
        else:
            self._access_token_provider = None
        fingerprint_token = validated_token
        if (
            fingerprint_token is None
            and access_token_provider is not None
            and identity_fingerprint is None
        ):
            try:
                fingerprint_token = _validated_header_value(
                    access_token_provider(),
                    name="access token",
                    maximum=16_384,
                )
            except Exception as error:
                raise ValueError("access_token_provider did not return a valid token") from error
            if fingerprint_token is None:
                raise ValueError(
                    "identity_fingerprint is required when the token provider has no token"
                )
        derived_fingerprint = (
            _token_fingerprint(fingerprint_token) if fingerprint_token is not None else "local"
        )
        self.identity_fingerprint = _validated_fingerprint(
            identity_fingerprint or derived_fingerprint
        )
        self._on_unauthorized = on_unauthorized
        self._unauthorized_notified = False
        self._max_read_attempts = max_read_attempts
        self._cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self._sleeper = sleeper
        self._clock = clock
        self._cache: dict[
            tuple[str, str | None, str, tuple[tuple[str, str], ...]], _CacheEntry
        ] = {}
        timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "EvalForge-Dashboard/0.1",
            },
        )

    def __repr__(self) -> str:
        """Return useful connection context without credential material."""

        return (
            f"ApiClient(base_url={self.base_url!r}, "
            f"identity_fingerprint={self.identity_fingerprint!r}, "
            f"workspace_id={self.workspace_id!r})"
        )

    @staticmethod
    def validate_base_url(base_url: str) -> str:
        """Accept only an HTTP(S) origin, never embedded credentials or query data."""

        candidate = base_url.strip().rstrip("/")
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("API URL must be an absolute HTTP or HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("API URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("API URL must not contain a query or fragment")
        return candidate

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def clear_cache(self) -> None:
        self._cache.clear()

    def health_live(self) -> JsonObject:
        return self._get_object("/health/live", cache_ttl=1.0)

    def health_ready(self) -> JsonObject:
        return self._get_object("/health/ready", cache_ttl=1.0)

    def meta(self) -> JsonObject:
        return self._get_object("/api/v1/meta")

    def capabilities(self) -> JsonObject:
        return self._get_object("/api/v1/capabilities")

    def session(self) -> JsonObject:
        return self._get_object("/api/v1/session", cache_ttl=1.0)

    def workspaces(self) -> JsonObject | list[Any]:
        return self._get_json("/api/v1/workspaces", cache_ttl=1.0)

    def overview(self) -> JsonObject:
        return self._get_object("/api/v1/overview", cache_ttl=3.0)

    def datasets(self, *, limit: int = 100, page: int = 1) -> JsonObject | list[Any]:
        return self._get_json(
            "/api/v1/datasets",
            params={"limit": limit, "page": page},
        )

    def dataset(self, dataset_id: str) -> JsonObject:
        return self._get_object(f"/api/v1/datasets/{dataset_id}")

    def create_dataset(self, payload: Mapping[str, Any]) -> JsonObject:
        return self._request_object("POST", "/api/v1/datasets", json_payload=payload)

    def create_test_case(self, dataset_id: str, payload: Mapping[str, Any]) -> JsonObject:
        return self._request_object(
            "POST",
            f"/api/v1/datasets/{dataset_id}/cases",
            json_payload=payload,
        )

    def update_test_case(
        self,
        case_id: str,
        payload: Mapping[str, Any],
    ) -> JsonObject:
        return self._request_object(
            "PATCH",
            f"/api/v1/cases/{case_id}",
            json_payload=payload,
        )

    def prompts(self, *, limit: int = 100, page: int = 1) -> JsonObject | list[Any]:
        return self._get_json(
            "/api/v1/prompts",
            params={"limit": limit, "page": page},
        )

    def create_prompt(self, payload: Mapping[str, Any]) -> JsonObject:
        return self._request_object("POST", "/api/v1/prompts", json_payload=payload)

    def update_prompt(self, prompt_id: str, payload: Mapping[str, Any]) -> JsonObject:
        return self._request_object(
            "PATCH",
            f"/api/v1/prompts/{prompt_id}",
            json_payload=payload,
        )

    def models(self, *, limit: int = 100, page: int = 1) -> JsonObject | list[Any]:
        return self._get_json(
            "/api/v1/models",
            params={"limit": limit, "page": page},
        )

    def create_model(self, payload: Mapping[str, Any]) -> JsonObject:
        return self._request_object("POST", "/api/v1/models", json_payload=payload)

    def update_model(self, model_id: str, payload: Mapping[str, Any]) -> JsonObject:
        return self._request_object(
            "PATCH",
            f"/api/v1/models/{model_id}",
            json_payload=payload,
        )

    def runs(
        self,
        *,
        limit: int = 50,
        page: int = 1,
        status: str | None = None,
    ) -> JsonObject | list[Any]:
        params: dict[str, QueryValue] = {"limit": limit, "page": page}
        if status:
            params["status"] = status
        return self._get_json("/api/v1/runs", params=params, cache_ttl=1.0)

    def create_run(
        self,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> JsonObject:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required for run creation")
        return self._request_object(
            "POST",
            "/api/v1/runs",
            json_payload=payload,
            headers={"Idempotency-Key": idempotency_key},
        )

    def preflight_run(self, payload: Mapping[str, Any]) -> JsonObject:
        return self._request_object("POST", "/api/v1/runs/preflight", json_payload=payload)

    def run(self, run_id: str) -> JsonObject:
        return self._get_object(f"/api/v1/runs/{run_id}", cache_ttl=1.0)

    def run_results(
        self,
        run_id: str,
        *,
        limit: int = 100,
        page: int = 1,
    ) -> JsonObject | list[Any]:
        return self._get_json(
            f"/api/v1/runs/{run_id}/results",
            params={"limit": limit, "page": page},
            cache_ttl=1.0,
        )

    def run_comparison(self, run_id: str) -> JsonObject:
        return self._get_object(f"/api/v1/runs/{run_id}/comparison", cache_ttl=2.0)

    def calibration_template(
        self,
        run_id: str,
        *,
        candidate_id: str,
        metric_name: str,
        template_format: str = "csv",
    ) -> bytes:
        """Download a server-derived human-label template for one stored score set."""

        if template_format not in {"csv", "json"}:
            raise ValueError("calibration template format must be csv or json")
        candidate = _required_form_value(candidate_id, name="candidate_id")
        metric = _required_form_value(metric_name, name="metric_name")
        response = self._request_response(
            "GET",
            f"/api/v1/runs/{run_id}/calibrations/template",
            params={
                "candidate_id": candidate,
                "metric_name": metric,
                "format": template_format,
            },
        )
        return response.content

    def calibration_reports(
        self,
        run_id: str,
        *,
        candidate_id: str | None = None,
        metric_name: str | None = None,
        limit: int = 100,
        page: int = 1,
    ) -> JsonObject | list[Any]:
        """List immutable calibration summaries for an evaluation run."""

        params: dict[str, str | int] = {"limit": limit, "page": page}
        if candidate_id is not None:
            params["candidate_id"] = _required_form_value(candidate_id, name="candidate_id")
        if metric_name is not None:
            params["metric_name"] = _required_form_value(metric_name, name="metric_name")
        return self._get_json(
            f"/api/v1/runs/{run_id}/calibrations",
            params=params,
            cache_ttl=2.0,
        )

    def import_calibration(
        self,
        run_id: str,
        *,
        candidate_id: str,
        metric_name: str,
        selected_threshold: float,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> JsonObject:
        """Upload labels once; mutation requests are intentionally never retried."""

        candidate = _required_form_value(candidate_id, name="candidate_id")
        metric = _required_form_value(metric_name, name="metric_name")
        upload_name = _required_form_value(filename, name="filename")
        upload_type = _required_form_value(content_type, name="content_type")
        normalized_name = upload_name.lower()
        if normalized_name.endswith(".json"):
            file_format = "json"
            expected_content_types = {"application/json"}
        elif normalized_name.endswith(".csv"):
            file_format = "csv"
            expected_content_types = {"text/csv", "application/csv"}
        else:
            raise ValueError("calibration filename must end in .csv or .json")
        if upload_type.split(";", 1)[0].strip().lower() not in expected_content_types:
            raise ValueError("calibration content type does not match the filename")
        if (
            isinstance(selected_threshold, bool)
            or not isinstance(selected_threshold, (int, float))
            or not math.isfinite(float(selected_threshold))
            or not 0.0 <= float(selected_threshold) <= 1.0
        ):
            raise ValueError("selected threshold must be a finite number between 0 and 1")
        payload = self._request_json(
            "POST",
            f"/api/v1/runs/{run_id}/calibrations",
            params={
                "candidate_id": candidate,
                "metric_name": metric,
                "selected_threshold": str(float(selected_threshold)),
                "format": file_format,
            },
            raw_content=content,
            headers={"Content-Type": upload_type},
        )
        self.clear_cache()
        return _expect_object(payload)

    def export_run(
        self,
        run_id: str,
        *,
        export_format: str = "json",
        disclosure_profile: str = "content_redacted",
    ) -> bytes:
        if export_format not in {"json", "csv", "package"}:
            raise ValueError("run export format must be json, csv, or package")
        if disclosure_profile not in {"content_redacted", "full_evidence"}:
            raise ValueError("run export disclosure profile is invalid")
        response = self._request_response(
            "GET",
            f"/api/v1/runs/{run_id}/export",
            params={
                "format": export_format,
                "disclosure_profile": disclosure_profile,
            },
        )
        return response.content

    def cancel_run(self, run_id: str) -> JsonObject:
        return self._request_object("POST", f"/api/v1/runs/{run_id}/cancel")

    def import_cases(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        dataset_id: str | None = None,
    ) -> JsonObject:
        if not dataset_id:
            raise ValueError("dataset_id is required for case imports")
        payload = self._request_json(
            "POST",
            f"/api/v1/datasets/{dataset_id}/imports",
            files={"file": (filename, content, content_type)},
        )
        self.clear_cache()
        return _expect_object(payload)

    def export_dataset(self, dataset_id: str, *, export_format: str = "json") -> bytes:
        response = self._request_response(
            "GET",
            f"/api/v1/datasets/{dataset_id}/export",
            params={"format": export_format},
        )
        return response.content

    def _get_object(
        self,
        path: str,
        *,
        params: Mapping[str, QueryValue] | None = None,
        cache_ttl: float | None = None,
    ) -> JsonObject:
        return _expect_object(self._get_json(path, params=params, cache_ttl=cache_ttl))

    def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, QueryValue] | None = None,
        cache_ttl: float | None = None,
    ) -> JsonObject | list[Any]:
        payload = self._request_json(
            "GET",
            path,
            params=params,
            cache_ttl=self._cache_ttl_seconds if cache_ttl is None else cache_ttl,
        )
        if isinstance(payload, (dict, list)):
            return payload
        raise ApiError("The API returned an unexpected response shape")

    def _request_object(
        self,
        method: str,
        path: str,
        *,
        json_payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonObject:
        payload = self._request_json(
            method,
            path,
            json_payload=json_payload,
            headers=headers,
        )
        self.clear_cache()
        return _expect_object(payload)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, QueryValue] | None = None,
        json_payload: Mapping[str, Any] | None = None,
        data: Mapping[str, str | None] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        raw_content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl: float = 0.0,
    ) -> Any:
        cache_key = self._cache_key(path, params)
        if method == "GET" and cache_ttl > 0:
            cached = self._cache.get(cache_key)
            if cached and cached.expires_at > self._clock():
                return copy.deepcopy(cached.value)

        response = self._request_response(
            method,
            path,
            params=params,
            json_payload=json_payload,
            data=data,
            files=files,
            raw_content=raw_content,
            headers=headers,
        )
        if response.status_code == 204 or not response.content:
            payload: Any = {}
        else:
            try:
                payload = response.json()
            except ValueError as exc:
                raise ApiError(
                    "The API returned an unreadable response",
                    status_code=response.status_code,
                    request_id=_request_id(response),
                ) from exc
        if method == "GET" and cache_ttl > 0:
            self._cache[cache_key] = _CacheEntry(
                expires_at=self._clock() + cache_ttl,
                value=copy.deepcopy(payload),
            )
        return payload

    def _request_response(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, QueryValue] | None = None,
        json_payload: Mapping[str, Any] | None = None,
        data: Mapping[str, str | None] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        raw_content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        read_only = method.upper() in {"GET", "HEAD"}
        attempts = self._max_read_attempts if read_only else 1
        last_error: ApiError | None = None

        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_payload,
                    data=data,
                    files=files,
                    content=raw_content,
                    headers=self._request_headers(headers),
                )
            except httpx.TimeoutException:
                last_error = ApiError(
                    "The API request timed out",
                    code="timeout",
                    retryable=read_only,
                )
            except httpx.RequestError:
                last_error = ApiError(
                    "The API is unreachable",
                    code="network_error",
                    retryable=read_only,
                )
            else:
                if response.is_success:
                    return response
                if response.status_code == 401:
                    self._notify_unauthorized()
                last_error = _error_from_response(response)
                if response.status_code not in _RETRYABLE_STATUS_CODES or not read_only:
                    raise last_error

            if attempt < attempts - 1:
                self._sleeper(0.08 * (2**attempt))

        if last_error is None:  # pragma: no cover - defensive invariant
            raise ApiError("The API request failed")
        raise last_error

    def _cache_key(
        self,
        path: str,
        params: Mapping[str, QueryValue] | None,
    ) -> tuple[str, str | None, str, tuple[tuple[str, str], ...]]:
        query = httpx.QueryParams(params or {}).multi_items()
        return (
            self.identity_fingerprint,
            self.workspace_id,
            path,
            tuple(sorted(query)),
        )

    def _request_headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        request_headers = dict(headers or {})
        protected = {key.casefold() for key in request_headers}
        if "authorization" in protected or "x-evalforge-workspace-id" in protected:
            raise ValueError("Identity and workspace headers are managed by ApiClient")

        if self._access_token_provider is not None:
            try:
                token = _validated_header_value(
                    self._access_token_provider(),
                    name="access token",
                    maximum=16_384,
                )
            except Exception as error:
                self._notify_unauthorized()
                raise ApiError(
                    "Your sign-in session is no longer available",
                    status_code=401,
                    code="reauthentication_required",
                ) from error
            if token is None:
                self._notify_unauthorized()
                raise ApiError(
                    "Your sign-in session is no longer available",
                    status_code=401,
                    code="reauthentication_required",
                )
            request_headers["Authorization"] = f"Bearer {token}"
        if self.workspace_id is not None:
            request_headers["X-EvalForge-Workspace-ID"] = self.workspace_id
        return request_headers

    def _notify_unauthorized(self) -> None:
        if self._unauthorized_notified:
            return
        self._unauthorized_notified = True
        if self._on_unauthorized is not None:
            self._on_unauthorized()


def collection_items(payload: Any) -> list[JsonObject]:
    """Normalize direct lists and common paginated envelopes."""

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        "items",
        "data",
        "results",
        "runs",
        "datasets",
        "prompts",
        "models",
        "cases",
        "candidates",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def public_payload(value: Any) -> Any:
    """Remove credential-like fields before rendering backend capability data."""

    if isinstance(value, dict):
        public: dict[str, Any] = {}
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if _is_sensitive_key(normalized):
                continue
            public[str(key)] = public_payload(item)
        return public
    if isinstance(value, list):
        return [public_payload(item) for item in value]
    if isinstance(value, tuple):
        return [public_payload(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    return (
        "api_key" in key
        or "apikey" in key
        or "password" in key
        or "secret" in key
        or "credential" in key
        or key == "authorization"
        or key.endswith("_authorization")
        or key == "token"
        or key.endswith("_token")
    )


def _expect_object(payload: Any) -> JsonObject:
    if not isinstance(payload, dict):
        raise ApiError("The API returned an unexpected response shape")
    return payload


def _required_form_value(value: str, *, name: str) -> str:
    candidate = value.strip()
    if not candidate or len(candidate) > 512 or "\x00" in candidate:
        raise ValueError(f"{name} has an invalid format")
    return candidate


def _request_id(response: httpx.Response, payload: Mapping[str, Any] | None = None) -> str | None:
    header_value = response.headers.get("x-request-id") or response.headers.get("x-correlation-id")
    if isinstance(header_value, str) and header_value:
        return header_value[:128]
    if payload:
        body_value = payload.get("request_id")
        if isinstance(body_value, str):
            return body_value[:128]
    return None


def _error_from_response(response: httpx.Response) -> ApiError:
    payload: Mapping[str, Any] = {}
    try:
        candidate = response.json()
        if isinstance(candidate, dict):
            payload = candidate
    except ValueError:
        pass

    nested = payload.get("error")
    error_payload = nested if isinstance(nested, dict) else payload
    detail = error_payload.get("detail") or error_payload.get("message")
    if not detail:
        detail = "The API request failed"
    if isinstance(detail, (dict, list)):
        detail_text = json.dumps(public_payload(detail), ensure_ascii=False, sort_keys=True)
    else:
        detail_text = str(detail)
    detail_text = _redact_request_credentials(response, detail_text)
    detail_text = detail_text.strip()[:600] or "The API request failed"
    code = error_payload.get("code")
    code_text = _redact_request_credentials(response, str(code))[:128] if code is not None else None
    request_id = _request_id(response, error_payload)
    if request_id is not None:
        request_id = _redact_request_credentials(response, request_id)
    retryable = error_payload.get("retryable")
    return ApiError(
        detail_text,
        status_code=response.status_code,
        request_id=request_id,
        code=code_text,
        retryable=(
            retryable
            if isinstance(retryable, bool)
            else response.status_code in _RETRYABLE_STATUS_CODES
        ),
    )


def _redact_request_credentials(response: httpx.Response, value: str) -> str:
    try:
        authorization = response.request.headers.get("authorization", "")
    except RuntimeError:
        return value
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.casefold() == "bearer" and token:
        if token in value:
            return value.replace(token, "[credential]")
        if len(token) >= 16 and (token[:16] in value or token[-16:] in value):
            return "[credential]"
    return value


def _validated_header_value(
    value: str | None,
    *,
    name: str,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    if (
        not value
        or len(value) > maximum
        or value != value.strip()
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise ValueError(f"{name} has an invalid format")
    return value


def _validated_fingerprint(value: str) -> str:
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 128
        or any(
            not (character.isascii() and (character.isalnum() or character in {"-", "_", ":"}))
            for character in candidate
        )
    ):
        raise ValueError("identity_fingerprint has an invalid format")
    return candidate


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
