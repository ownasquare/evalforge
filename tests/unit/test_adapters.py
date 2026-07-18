from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
from typing import Any

import pytest

from evalforge.evaluation.adapters import (
    AdapterRegistry,
    DeterministicAdapter,
    OpenAICompatibleAdapter,
)
from evalforge.evaluation.types import (
    ApiMode,
    GenerationRequest,
    ProviderError,
)


def make_request(
    *,
    api_mode: ApiMode = ApiMode.DEMO,
    model: str = "demo-balanced",
) -> GenerationRequest:
    return GenerationRequest(
        model=model,
        api_mode=api_mode,
        system_prompt="Answer from the supplied context.",
        user_prompt="Where is Paris?",
        expected_output="Paris is in France.",
        context="Paris is the capital of France.",
        seed=17,
        max_output_tokens=128,
    )


@pytest.mark.asyncio
async def test_deterministic_adapter_is_repeatable() -> None:
    first_adapter = DeterministicAdapter(profile="balanced")
    second_adapter = DeterministicAdapter(profile="balanced")
    request = make_request()

    first = await first_adapter.generate(request)
    second = await first_adapter.generate(request)
    from_new_instance = await second_adapter.generate(request)

    assert first == second == from_new_instance
    assert first.text == "Paris is in France."
    assert first.request_id.startswith("demo_")
    assert first.api_mode == ApiMode.DEMO
    assert first.total_tokens == first.input_tokens + first.output_tokens


@pytest.mark.asyncio
async def test_deterministic_profiles_are_explicit_and_explainable() -> None:
    concise = await DeterministicAdapter(profile="concise").generate(make_request())
    hallucinating = await DeterministicAdapter(profile="hallucinating").generate(make_request())
    slow = await DeterministicAdapter(profile="slow").generate(make_request())

    assert len(concise.text) <= len("Paris is in France.")
    assert "https://demo.invalid/" in hallucinating.text
    assert slow.latency_ms == 1250
    with pytest.raises(ProviderError, match="deterministic failure"):
        await DeterministicAdapter(profile="failing").generate(make_request())


@pytest.mark.asyncio
async def test_deterministic_adapter_rejects_real_mode_and_bounds_output() -> None:
    with pytest.raises(ProviderError, match="only supports explicit demo mode"):
        await DeterministicAdapter().generate(make_request(api_mode=ApiMode.RESPONSES))
    request = GenerationRequest(
        model="demo-balanced",
        api_mode=ApiMode.DEMO,
        system_prompt="",
        user_prompt="Question",
        context="one two three four five",
        max_output_tokens=3,
    )
    result = await DeterministicAdapter().generate(request)

    assert result.text == "one two three"
    with pytest.raises(ValueError, match="Unknown deterministic profile"):
        DeterministicAdapter(profile="missing")


class FakeEndpoint:
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeClient:
    def __init__(
        self,
        *,
        responses_result: Any = None,
        responses_error: Exception | None = None,
        chat_result: Any = None,
        chat_error: Exception | None = None,
    ) -> None:
        self.responses = FakeEndpoint(result=responses_result, error=responses_error)
        self.chat = SimpleNamespace(completions=FakeEndpoint(result=chat_result, error=chat_error))


class StatusError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__("sanitized fake status")
        self.status_code = status_code


class FlakyEndpoint:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise StatusError(429)
        return self.result


class CountingErrorEndpoint:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = 0

    async def create(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise StatusError(self.status_code)


@pytest.mark.asyncio
async def test_responses_mode_maps_response_and_never_calls_chat() -> None:
    response = SimpleNamespace(
        id="resp_123",
        output_text="Paris is in France.",
        status="completed",
        usage=SimpleNamespace(input_tokens=11, output_tokens=5, total_tokens=16),
    )
    client = FakeClient(responses_result=response)
    adapter = OpenAICompatibleAdapter(client=client, provider="openai")

    result = await adapter.generate(make_request(api_mode=ApiMode.RESPONSES, model="gpt-test"))

    assert result.text == "Paris is in France."
    assert result.api_mode == ApiMode.RESPONSES
    assert result.input_tokens == 11
    assert result.output_tokens == 5
    assert result.total_tokens == 16
    assert len(client.responses.calls) == 1
    assert client.chat.completions.calls == []


@pytest.mark.asyncio
async def test_responses_mode_extracts_nested_text_and_handles_missing_usage() -> None:
    response = {
        "output": [{"content": [{"text": "Nested answer"}]}],
        "status": "completed",
    }
    client = FakeClient(responses_result=response)
    adapter = OpenAICompatibleAdapter(client=client)

    result = await adapter.generate(make_request(api_mode=ApiMode.RESPONSES, model="gpt-test"))

    assert result.text == "Nested answer"
    assert result.input_tokens == result.output_tokens == result.total_tokens == 0
    assert result.request_id.startswith("unreported_")
    assert result.metadata["usage_reported"] is False


@pytest.mark.asyncio
async def test_chat_completions_mode_maps_response_and_never_calls_responses() -> None:
    response = SimpleNamespace(
        id="chat_123",
        model="gpt-test",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Paris is in France."),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=9, completion_tokens=5, total_tokens=14),
    )
    client = FakeClient(chat_result=response)
    adapter = OpenAICompatibleAdapter(client=client, provider="compatible")

    result = await adapter.generate(
        make_request(api_mode=ApiMode.CHAT_COMPLETIONS, model="gpt-test")
    )

    assert result.text == "Paris is in France."
    assert result.api_mode == ApiMode.CHAT_COMPLETIONS
    assert result.finish_reason == "stop"
    assert result.total_tokens == 14
    assert client.responses.calls == []
    assert len(client.chat.completions.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("api_mode", [ApiMode.RESPONSES, ApiMode.CHAT_COMPLETIONS])
async def test_rate_limit_is_never_retried_without_provider_idempotency(
    api_mode: ApiMode,
) -> None:
    response = SimpleNamespace(
        id="resp_retry",
        output_text="Recovered",
        status="completed",
        usage=None,
    )
    client = FakeClient(responses_result=response)
    endpoint = FlakyEndpoint(response)
    if api_mode is ApiMode.RESPONSES:
        client.responses = endpoint
    else:
        client.chat.completions = endpoint
    adapter = OpenAICompatibleAdapter(client=client)

    with pytest.raises(ProviderError) as raised:
        await adapter.generate(make_request(api_mode=api_mode, model="gpt-test"))

    assert raised.value.code == "provider_rate_limited"
    assert raised.value.retryable is True
    assert raised.value.attempts == 1
    assert len(endpoint.calls) == 1


@pytest.mark.asyncio
async def test_ambiguous_upstream_failure_is_never_retried_automatically() -> None:
    client = FakeClient()
    endpoint = CountingErrorEndpoint(503)
    client.responses = endpoint
    adapter = OpenAICompatibleAdapter(client=client)

    with pytest.raises(ProviderError) as raised:
        await adapter.generate(make_request(api_mode=ApiMode.RESPONSES, model="gpt-test"))

    assert raised.value.code == "provider_upstream"
    assert raised.value.retryable is True
    assert raised.value.attempts == 1
    assert endpoint.calls == 1


@pytest.mark.asyncio
async def test_authentication_error_is_sanitized_and_not_retried() -> None:
    client = FakeClient(responses_error=StatusError(401))
    adapter = OpenAICompatibleAdapter(client=client)

    with pytest.raises(ProviderError) as raised:
        await adapter.generate(make_request(api_mode=ApiMode.RESPONSES, model="gpt-test"))

    assert raised.value.code == "provider_authentication"
    assert raised.value.retryable is False
    assert raised.value.status_code == 401
    assert len(client.responses.calls) == 1


@pytest.mark.asyncio
async def test_api_mode_failure_never_silently_falls_back() -> None:
    client = FakeClient(
        responses_error=RuntimeError("responses unsupported secret-value"),
        chat_result=SimpleNamespace(),
    )
    adapter = OpenAICompatibleAdapter(client=client, provider="openai")

    with pytest.raises(ProviderError) as raised:
        await adapter.generate(make_request(api_mode=ApiMode.RESPONSES, model="gpt-test"))

    assert "secret-value" not in str(raised.value)
    assert len(client.responses.calls) == 1
    assert client.chat.completions.calls == []


@pytest.mark.asyncio
async def test_chat_failure_never_calls_responses() -> None:
    client = FakeClient(chat_error=RuntimeError("chat unsupported"))
    adapter = OpenAICompatibleAdapter(client=client, provider="compatible")

    with pytest.raises(ProviderError):
        await adapter.generate(make_request(api_mode=ApiMode.CHAT_COMPLETIONS, model="gpt-test"))

    assert client.responses.calls == []
    assert len(client.chat.completions.calls) == 1


@pytest.mark.asyncio
async def test_adapter_registry_has_no_missing_adapter_fallback() -> None:
    registry = AdapterRegistry()
    registry.register("demo", DeterministicAdapter(profile="balanced"))

    with pytest.raises(ProviderError, match="No provider adapter"):
        await registry.generate("missing", make_request())


def test_adapter_registry_rejects_implicit_replacement() -> None:
    registry = AdapterRegistry()
    registry.register("demo", DeterministicAdapter())

    assert registry.names == ("demo",)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("demo", DeterministicAdapter(profile="concise"))
    registry.register("demo", DeterministicAdapter(profile="concise"), replace=True)
    assert isinstance(registry.get("demo"), DeterministicAdapter)


@pytest.mark.asyncio
async def test_openai_adapter_rejects_demo_mode() -> None:
    adapter = OpenAICompatibleAdapter(client=FakeClient())

    with pytest.raises(ProviderError, match="explicit real-provider API mode"):
        await adapter.generate(make_request())


def test_backend_factory_requires_configured_credential() -> None:
    with pytest.raises(ProviderError, match="credential is not configured"):
        OpenAICompatibleAdapter.from_backend_settings(SimpleNamespace(openai_api_key=None))


def test_generation_request_cannot_carry_credentials_or_provider_url() -> None:
    field_names = {field.name for field in fields(GenerationRequest)}

    assert "api_key" not in field_names
    assert "base_url" not in field_names
    assert "authorization" not in field_names
