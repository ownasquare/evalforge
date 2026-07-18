"""Minimal source adapter example with no network access."""

from __future__ import annotations

from evalforge.evaluation.adapters import AdapterRegistry
from evalforge.evaluation.types import (
    ApiMode,
    GenerationRequest,
    GenerationResponse,
    ProviderError,
)


class ExampleEchoAdapter:
    """Return a deterministic response while demonstrating the adapter contract."""

    provider = "example-echo"

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        if request.api_mode is not ApiMode.DEMO:
            raise ProviderError(
                "The example adapter supports demo mode only.",
                code="unsupported_api_mode",
            )
        # Gold/reference answers are evaluator evidence, never generation input.
        text = f"Echo: {request.user_prompt}"
        input_tokens = max(1, len(request.user_prompt.split()))
        output_tokens = max(1, len(text.split()))
        return GenerationResponse(
            text=text,
            provider=self.provider,
            model=request.model,
            api_mode=request.api_mode,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            latency_ms=1,
            request_id="example-local-request",
            finish_reason="stop",
            metadata={"synthetic": True},
        )


def register_example_adapter(registry: AdapterRegistry) -> None:
    """Register the example under the provider name used by a model profile."""

    registry.register(ExampleEchoAdapter.provider, ExampleEchoAdapter())
