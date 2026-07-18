"""Explicit OpenAI Responses and Chat Completions provider adapter."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from evalforge.evaluation.types import (
    ApiMode,
    GenerationRequest,
    GenerationResponse,
    ProviderError,
)

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class OpenAICompatibleAdapter:
    """Use exactly one configured OpenAI API surface for each request."""

    def __init__(
        self,
        *,
        client: Any,
        provider: str = "openai-compatible",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not provider.strip():
            raise ValueError("provider cannot be blank")
        self._client = client
        self.provider = provider
        self._clock = clock

    @classmethod
    def from_backend_settings(
        cls,
        settings: Any,
        *,
        provider: str = "openai-compatible",
    ) -> OpenAICompatibleAdapter:
        """Build a client only from backend settings, never from a generation request."""

        from openai import AsyncOpenAI

        secret_value = getattr(settings, "openai_api_key", None)
        secret_getter = getattr(secret_value, "get_secret_value", None)
        if callable(secret_getter):
            secret_value = secret_getter()
        if not isinstance(secret_value, str) or not secret_value:
            raise ProviderError(
                "The provider credential is not configured.",
                code="provider_not_configured",
            )
        base_url = getattr(settings, "openai_base_url", None)
        if base_url is not None:
            validate_backend_base_url(str(base_url))
        timeout = float(
            getattr(
                settings,
                "provider_timeout_seconds",
                getattr(settings, "openai_timeout_seconds", 30.0),
            )
        )
        client_kwargs: dict[str, Any] = {
            "api_key": secret_value,
            "timeout": timeout,
            # Adapter-owned retries are observable; SDK retries are disabled.
            "max_retries": 0,
        }
        if base_url is not None:
            client_kwargs["base_url"] = str(base_url)
        return cls(
            client=AsyncOpenAI(**client_kwargs),
            provider=provider,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        if request.api_mode not in {ApiMode.RESPONSES, ApiMode.CHAT_COMPLETIONS}:
            raise ProviderError(
                "The OpenAI-compatible adapter requires an explicit real-provider API mode.",
                code="unsupported_api_mode",
            )
        started = self._clock()
        try:
            if request.api_mode == ApiMode.RESPONSES:
                raw = await self._create_response(request)
                normalized = self._normalize_responses(raw, request)
            else:
                raw = await self._create_chat_completion(request)
                normalized = self._normalize_chat(raw, request)
        except ProviderError:
            raise
        except Exception as exc:
            # Generation requests can be billable even when the response is an error. Without
            # a provider-specific idempotency contract, every logical evaluation item gets one
            # network attempt, including HTTP 429 responses.
            raise _classify_provider_error(exc, attempts=1) from None
        latency_ms = max(0, round((self._clock() - started) * 1000))
        return GenerationResponse(
            **normalized,
            latency_ms=latency_ms,
            retry_count=0,
        )

    async def _create_response(self, request: GenerationRequest) -> Any:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})
        return await self._client.responses.create(
            model=request.model,
            input=messages,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
        )

    async def _create_chat_completion(self, request: GenerationRequest) -> Any:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})
        return await self._client.chat.completions.create(
            model=request.model,
            messages=messages,
            temperature=request.temperature,
            max_tokens=request.max_output_tokens,
        )

    def _normalize_responses(
        self,
        raw: Any,
        request: GenerationRequest,
    ) -> dict[str, Any]:
        text = _responses_text(raw)
        usage = _read_value(raw, "usage")
        input_tokens = _coerce_non_negative_int(_read_value(usage, "input_tokens"))
        output_tokens = _coerce_non_negative_int(_read_value(usage, "output_tokens"))
        total_tokens = _coerce_non_negative_int(
            _read_value(usage, "total_tokens"),
            default=input_tokens + output_tokens,
        )
        return {
            "text": text,
            "provider": self.provider,
            "model": request.model,
            "api_mode": ApiMode.RESPONSES,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "request_id": _safe_request_id(_read_value(raw, "id"), request),
            "finish_reason": _optional_string(_read_value(raw, "status")),
            "metadata": {
                "usage_reported": _usage_is_reported(usage, "input_tokens", "output_tokens"),
                "response_status": _optional_string(_read_value(raw, "status")),
            },
        }

    def _normalize_chat(self, raw: Any, request: GenerationRequest) -> dict[str, Any]:
        choices = _read_value(raw, "choices") or []
        first_choice = choices[0] if choices else None
        message = _read_value(first_choice, "message")
        text = _chat_content_text(_read_value(message, "content"))
        usage = _read_value(raw, "usage")
        input_tokens = _coerce_non_negative_int(_read_value(usage, "prompt_tokens"))
        output_tokens = _coerce_non_negative_int(_read_value(usage, "completion_tokens"))
        total_tokens = _coerce_non_negative_int(
            _read_value(usage, "total_tokens"),
            default=input_tokens + output_tokens,
        )
        return {
            "text": text,
            "provider": self.provider,
            "model": request.model,
            "api_mode": ApiMode.CHAT_COMPLETIONS,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "request_id": _safe_request_id(_read_value(raw, "id"), request),
            "finish_reason": _optional_string(_read_value(first_choice, "finish_reason")),
            "metadata": {
                "usage_reported": _usage_is_reported(usage, "prompt_tokens", "completion_tokens")
            },
        }


def _read_value(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _coerce_non_negative_int(value: Any, *, default: int = 0) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _usage_is_reported(usage: Any, *field_names: str) -> bool:
    if usage is None:
        return False
    for field_name in field_names:
        value = _read_value(usage, field_name)
        if isinstance(value, bool) or value is None:
            return False
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return False
        if parsed < 0:
            return False
    return True


def _responses_text(raw: Any) -> str:
    output_text = _read_value(raw, "output_text")
    if isinstance(output_text, str):
        return output_text
    parts: list[str] = []
    for output_item in _read_value(raw, "output") or []:
        for content_item in _read_value(output_item, "content") or []:
            text = _read_value(content_item, "text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _chat_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        text = _read_value(item, "text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _safe_request_id(value: Any, request: GenerationRequest) -> str:
    if isinstance(value, str) and value:
        return value
    provenance = (
        f"{request.model}\x00{request.api_mode.value}\x00"
        f"{request.system_prompt}\x00{request.user_prompt}"
    ).encode()
    return f"unreported_{hashlib.sha256(provenance).hexdigest()[:16]}"


def _classify_provider_error(exc: Exception, *, attempts: int) -> ProviderError:
    status_value = getattr(exc, "status_code", None)
    try:
        status_code = int(status_value) if status_value is not None else None
    except (TypeError, ValueError):
        status_code = None
    if status_code in {401, 403}:
        code = "provider_authentication"
    elif status_code == 429:
        code = "provider_rate_limited"
    elif status_code is not None and status_code >= 500:
        code = "provider_upstream"
    elif isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.casefold():
        # Ambiguous timeouts are not automatically retried because a billable
        # request may already have reached the provider.
        code = "provider_timeout"
    else:
        code = "provider_error"
    return ProviderError(
        "Provider request failed.",
        code=code,
        retryable=status_code in _RETRYABLE_STATUS_CODES,
        status_code=status_code,
        attempts=attempts,
    )


def validate_backend_base_url(base_url: str, *, require_loopback: bool = False) -> None:
    parsed = urlsplit(base_url)
    if parsed.username is not None or parsed.password is not None:
        raise ProviderError(
            "Provider base URL must not contain credentials.",
            code="invalid_provider_base_url",
        )
    is_loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if require_loopback and not is_loopback:
        raise ProviderError(
            "No-auth provider URLs must use an explicit loopback address.",
            code="invalid_provider_base_url",
        )
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and is_loopback:
        return
    raise ProviderError(
        "Provider base URL must use HTTPS or an explicit loopback HTTP address.",
        code="invalid_provider_base_url",
    )
