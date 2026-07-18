"""Network-free SHA-256-based provider profiles for reproducible demonstrations."""

from __future__ import annotations

import hashlib
import json
from typing import Final

from evalforge.evaluation.text import split_sentences, tokenize
from evalforge.evaluation.types import (
    ApiMode,
    GenerationRequest,
    GenerationResponse,
    ProviderError,
)

DEMO_PROFILES: Final[frozenset[str]] = frozenset(
    {"balanced", "concise", "hallucinating", "slow", "failing"}
)
DEMO_MODEL_ALIASES: Final[dict[str, str]] = {
    "reliable": "balanced",
    "fast": "concise",
    "risky": "hallucinating",
}


def resolve_demo_profile(model_name: str) -> str:
    normalized = model_name.removeprefix("demo-")
    profile = DEMO_MODEL_ALIASES.get(normalized, normalized)
    if profile not in DEMO_PROFILES:
        raise ValueError("unsupported deterministic demo model")
    return profile


class DeterministicAdapter:
    """Generate stable synthetic responses without accessing a model or network."""

    provider = "deterministic-demo"

    def __init__(self, *, profile: str = "balanced") -> None:
        if profile not in DEMO_PROFILES:
            allowed = ", ".join(sorted(DEMO_PROFILES))
            raise ValueError(f"Unknown deterministic profile; expected one of: {allowed}")
        self.profile = profile

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        if request.api_mode != ApiMode.DEMO:
            raise ProviderError(
                "The deterministic adapter only supports explicit demo mode.",
                code="unsupported_api_mode",
            )
        if self.profile == "failing":
            raise ProviderError(
                "deterministic failure profile requested",
                code="deterministic_failure",
            )
        digest = self._digest(request)
        balanced = self._balanced_text(request, digest)
        if self.profile == "concise":
            sentences = split_sentences(balanced)
            output = sentences[0] if sentences else balanced
        elif self.profile == "hallucinating":
            invented_number = 100 + (int(digest[:8], 16) % 900)
            output = (
                f"{balanced} Unverified demo statistic: {invented_number}. "
                f"Source: https://demo.invalid/{digest[:12]}"
            )
        else:
            output = balanced
        output = self._bound_output(output, request.max_output_tokens)
        input_tokens = max(1, len(tokenize(f"{request.system_prompt} {request.user_prompt}")))
        output_tokens = max(1, len(tokenize(output)))
        latency_ms = {
            "balanced": 180,
            "concise": 80,
            "hallucinating": 220,
            "slow": 1250,
        }[self.profile]
        return GenerationResponse(
            text=output,
            provider=self.provider,
            model=request.model,
            api_mode=ApiMode.DEMO,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            latency_ms=latency_ms,
            request_id=f"demo_{digest[:24]}",
            finish_reason="stop",
            retry_count=0,
            metadata={
                "synthetic": True,
                "profile": self.profile,
                "seed": request.seed,
                "reproducibility_hash": digest,
            },
        )

    def _digest(self, request: GenerationRequest) -> str:
        payload = {
            "api_mode": request.api_mode.value,
            "context": request.context,
            "expected_output": request.expected_output,
            "max_output_tokens": request.max_output_tokens,
            "model": request.model,
            "profile": self.profile,
            "seed": request.seed,
            "system_prompt": request.system_prompt,
            "temperature": request.temperature,
            "user_prompt": request.user_prompt,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _balanced_text(request: GenerationRequest, digest: str) -> str:
        if request.expected_output is not None and request.expected_output.strip():
            return request.expected_output.strip()
        if request.context is not None and request.context.strip():
            sentences = split_sentences(request.context)
            return sentences[0] if sentences else request.context.strip()
        return f"Deterministic demo response {digest[:12]}."

    @staticmethod
    def _bound_output(output: str, max_output_tokens: int) -> str:
        words = output.split()
        if len(words) <= max_output_tokens:
            return output
        return " ".join(words[:max_output_tokens])
