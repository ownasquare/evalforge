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


def test_provider_postgresql_url_is_normalized_to_the_installed_driver() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql://evalforge@database/evalforge",
    )

    assert settings.database_url == "postgresql+psycopg://evalforge@database/evalforge"


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


def test_local_auth_is_the_loopback_default(database_url: str) -> None:
    settings = Settings(_env_file=None, environment="development", database_url=database_url)

    assert settings.auth_mode == "local"
    assert settings.api_host == "127.0.0.1"
    assert settings.dashboard_host == "127.0.0.1"


def test_local_auth_rejects_commercial_pilot_enforcement(database_url: str) -> None:
    with pytest.raises(ValidationError, match="commercial_pilot_enabled"):
        Settings(
            _env_file=None,
            environment="test",
            database_url=database_url,
            commercial_pilot_enabled=True,
        )


@pytest.mark.parametrize("field", ["api_host", "dashboard_host"])
def test_local_auth_rejects_non_loopback_bindings(database_url: str, field: str) -> None:
    with pytest.raises(ValidationError, match="local auth mode"):
        Settings(
            _env_file=None,
            environment="development",
            database_url=database_url,
            **{field: "192.0.2.1"},
        )


def test_oidc_production_configuration_fails_closed_when_incomplete(database_url: str) -> None:
    with pytest.raises(ValidationError, match="OIDC"):
        Settings(
            _env_file=None,
            environment="production",
            database_url=database_url,
            auth_mode="oidc",
        )


def test_oidc_production_requires_https_origins(database_url: str) -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(
            _env_file=None,
            environment="production",
            database_url=database_url,
            auth_mode="oidc",
            oidc_issuer="http://identity.example",
            oidc_audience="evalforge-api",
            oidc_jwks_url="http://identity.example/jwks.json",
            public_base_url="http://evalforge.example",
        )


def test_complete_oidc_production_configuration_is_accepted(database_url: str) -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        database_url=database_url,
        auth_mode="oidc",
        oidc_issuer="https://identity.example",
        oidc_audience="evalforge-api",
        oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        public_base_url="https://evalforge.example",
        api_url="https://evalforge.example",
        trusted_hosts=["evalforge.example"],
        oidc_algorithms=["RS256", "ES256"],
        oidc_clock_skew_seconds=30,
        oidc_jwks_cache_seconds=300,
        oidc_jwks_timeout_seconds=2.0,
        dashboard_oidc_provider="evalforge",
        dashboard_public_base_url="https://app.evalforge.example",
        commercial_pilot_enabled=True,
        metrics_bearer_token="metrics-token-value",
    )

    assert settings.auth_mode == "oidc"
    assert settings.oidc_issuer == "https://identity.example"
    assert settings.oidc_algorithms == ["RS256", "ES256"]
    assert settings.commercial_pilot_enabled is True


def test_production_oidc_rejects_plaintext_dashboard_public_url(database_url: str) -> None:
    with pytest.raises(ValidationError, match="dashboard public base URL must use HTTPS"):
        Settings(
            _env_file=None,
            environment="production",
            database_url=database_url,
            auth_mode="oidc",
            oidc_issuer="https://identity.example",
            oidc_audience="evalforge-api",
            oidc_jwks_url="https://identity.example/.well-known/jwks.json",
            public_base_url="https://evalforge.example",
            api_url="https://evalforge.example",
            trusted_hosts=["evalforge.example"],
            dashboard_public_base_url="http://app.evalforge.example",
            metrics_bearer_token="metrics-token-value",
        )


def test_non_test_oidc_rejects_plaintext_dashboard_api_transport(database_url: str) -> None:
    with pytest.raises(ValidationError, match="API URL must use HTTPS"):
        Settings(
            _env_file=None,
            environment="development",
            database_url=database_url,
            auth_mode="oidc",
            oidc_issuer="https://identity.example",
            oidc_audience="evalforge-api",
            oidc_jwks_url="https://identity.example/.well-known/jwks.json",
            public_base_url="https://evalforge.example",
            api_url="http://api:8000",
        )


def test_test_oidc_may_use_plaintext_loopback_harness(database_url: str) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        auth_mode="oidc",
        oidc_issuer="https://identity.test",
        oidc_audience="evalforge-api",
        oidc_jwks_url="https://identity.test/.well-known/jwks.json",
        public_base_url="http://evalforge.test",
        api_url="http://127.0.0.1:8000",
    )

    assert str(settings.api_url) == "http://127.0.0.1:8000/"


@pytest.mark.parametrize(
    ("issuer", "jwks_url"),
    [
        ("http://identity.test", "https://identity.test/jwks.json"),
        ("https://identity.test", "http://identity.test/jwks.json"),
    ],
)
def test_test_oidc_rejects_plaintext_identity_endpoints(
    database_url: str,
    issuer: str,
    jwks_url: str,
) -> None:
    with pytest.raises(ValidationError, match="issuer and JWKS URL must use HTTPS"):
        Settings(
            _env_file=None,
            environment="test",
            database_url=database_url,
            auth_mode="oidc",
            oidc_issuer=issuer,
            oidc_audience="evalforge-api",
            oidc_jwks_url=jwks_url,
            public_base_url="http://evalforge.test",
            api_url="http://127.0.0.1:8000",
        )


def test_oidc_production_requires_the_public_host_to_be_trusted(database_url: str) -> None:
    with pytest.raises(ValidationError, match="trusted_hosts"):
        Settings(
            _env_file=None,
            environment="production",
            database_url=database_url,
            auth_mode="oidc",
            oidc_issuer="https://identity.example",
            oidc_audience="evalforge-api",
            oidc_jwks_url="https://identity.example/.well-known/jwks.json",
            public_base_url="https://evalforge.example",
            api_url="https://evalforge.example",
        )


def test_streamlit_named_oidc_provider_rejects_underscores(database_url: str) -> None:
    with pytest.raises(ValidationError, match="dashboard_oidc_provider"):
        Settings(
            _env_file=None,
            environment="test",
            database_url=database_url,
            auth_mode="oidc",
            oidc_issuer="https://identity.test",
            oidc_audience="evalforge-api",
            oidc_jwks_url="https://identity.test/jwks.json",
            public_base_url="http://evalforge.test",
            dashboard_oidc_provider="company_provider",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("oidc_clock_skew_seconds", 301),
        ("oidc_jwks_cache_seconds", 29),
        ("oidc_jwks_timeout_seconds", 0.1),
    ],
)
def test_oidc_runtime_bounds_are_enforced(database_url: str, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="test",
            database_url=database_url,
            auth_mode="oidc",
            oidc_issuer="https://identity.test",
            oidc_audience="evalforge-api",
            oidc_jwks_url="https://identity.test/jwks.json",
            public_base_url="http://evalforge.test",
            **{field: value},
        )


def test_oidc_algorithms_reject_symmetric_signatures(database_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="test",
            database_url=database_url,
            auth_mode="oidc",
            oidc_issuer="https://identity.test",
            oidc_audience="evalforge-api",
            oidc_jwks_url="https://identity.test/jwks.json",
            public_base_url="http://evalforge.test",
            oidc_algorithms=["HS256"],
        )


def test_database_worker_rejects_sqlite_topology(database_url: str) -> None:
    with pytest.raises(ValidationError, match="requires PostgreSQL"):
        Settings(
            _env_file=None,
            environment="test",
            database_url=database_url,
            executor_mode="database_worker",
        )
