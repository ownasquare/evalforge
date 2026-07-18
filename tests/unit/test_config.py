from __future__ import annotations

import pytest
from pydantic import ValidationError

from evalforge.config import Settings
from evalforge.models import ApiMode
from evalforge.schemas import ModelProfileCreate


def test_settings_never_expose_provider_secret(settings: Settings) -> None:
    dumped = settings.model_dump_json()
    assert "OPENAI_API_KEY" not in dumped
    assert "openai_api_key" not in dumped
    assert "secret-value" not in dumped
    assert "**********" not in dumped


def test_provider_capabilities_only_expose_readiness(settings: Settings) -> None:
    capabilities = settings.provider_capabilities()
    assert capabilities["openai"] == {"configured": True, "models": ["gpt-4.1-mini"]}
    assert "secret-value" not in str(capabilities)


def test_empty_provider_secret_becomes_unconfigured(database_url: str) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        openai_api_key="   ",
    )
    assert settings.openai_api_key is None


def test_postgresql_url_is_accepted_without_connecting() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://evalforge@database/evalforge",
    )
    assert settings.database_backend == "postgresql"
    assert settings.is_sqlite is False


def test_postgresql_url_requires_the_installed_psycopg_driver() -> None:
    with pytest.raises(ValidationError, match="postgresql\\+psycopg"):
        Settings(
            _env_file=None,
            environment="test",
            database_url="postgresql://evalforge@database/evalforge",
        )


def test_unsupported_database_backend_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SQLite or PostgreSQL"):
        Settings(_env_file=None, database_url="mysql+pymysql://localhost/evalforge")


def test_model_allowlists_are_trimmed_and_deduplicated(database_url: str) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        openai_model_allowlist=["gpt-4.1-mini", " gpt-4.1-mini ", ""],
    )
    assert settings.openai_model_allowlist == ["gpt-4.1-mini"]


def test_pricing_distinguishes_unknown_from_known_zero() -> None:
    unknown = ModelProfileCreate(
        name="Unknown pricing",
        provider="local",
        model_name="local-model",
        api_mode=ApiMode.CHAT_COMPLETIONS,
    )
    known_zero = ModelProfileCreate(
        name="Free local model",
        provider="local",
        model_name="free-model",
        api_mode=ApiMode.CHAT_COMPLETIONS,
        input_price_micro_usd_per_million_tokens=0,
        output_price_micro_usd_per_million_tokens=0,
        pricing_source="operator-confirmed",
    )

    assert unknown.input_price_micro_usd_per_million_tokens is None
    assert unknown.pricing_source is None
    assert known_zero.input_price_micro_usd_per_million_tokens == 0
    assert known_zero.pricing_source == "operator-confirmed"

    with pytest.raises(ValidationError, match="pricing_source"):
        ModelProfileCreate(
            name="Ambiguous free model",
            provider="local",
            model_name="free-model",
            api_mode=ApiMode.CHAT_COMPLETIONS,
            input_price_micro_usd_per_million_tokens=0,
        )
