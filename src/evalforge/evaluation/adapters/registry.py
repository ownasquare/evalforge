"""Explicit provider adapter registry with no implicit fallback behavior."""

from __future__ import annotations

from evalforge.evaluation.types import (
    GenerationRequest,
    GenerationResponse,
    ModelAdapter,
    ProviderError,
)


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ModelAdapter] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def register(self, name: str, adapter: ModelAdapter, *, replace: bool = False) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Adapter name cannot be blank")
        if normalized_name in self._adapters and not replace:
            raise ValueError(f"Adapter is already registered: {normalized_name}")
        self._adapters[normalized_name] = adapter

    def get(self, name: str) -> ModelAdapter:
        try:
            return self._adapters[name]
        except KeyError:
            raise ProviderError(
                f"No provider adapter is registered for '{name}'.",
                code="adapter_not_registered",
            ) from None

    async def generate(
        self,
        name: str,
        request: GenerationRequest,
    ) -> GenerationResponse:
        return await self.get(name).generate(request)
