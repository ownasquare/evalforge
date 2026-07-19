"""Typed, secret-safe application configuration."""

from __future__ import annotations

import re
from functools import lru_cache
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from evalforge import __version__

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AuthMode = Literal["local", "oidc"]
OidcAlgorithm = Literal["RS256", "ES256"]
ExecutorMode = Literal["embedded_single", "api_only", "database_worker"]


def _default_oidc_algorithms() -> list[OidcAlgorithm]:
    return ["RS256", "ES256"]


class Settings(BaseSettings):
    """Runtime settings loaded from ``EVALFORGE_*`` environment variables.

    Provider credentials are backend-only fields. They are deliberately excluded
    from all Pydantic dumps so a settings object can be logged or returned by a
    capability endpoint without leaking a credential, even in masked form.
    """

    model_config = SettingsConfigDict(
        env_prefix="EVALFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    environment: Environment = "development"
    database_url: str = "sqlite:///./.data/evalforge.db"
    sqlite_busy_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8_000, ge=1, le=65_535)
    api_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = Field(default=8_501, ge=1, le=65_535)
    cors_origins: list[AnyHttpUrl] = Field(
        default_factory=lambda: [
            AnyHttpUrl("http://127.0.0.1:8501"),
            AnyHttpUrl("http://localhost:8501"),
        ]
    )
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "[::1]", "testserver", "api"]
    )
    log_level: LogLevel = "INFO"
    json_logs: bool = False

    auth_mode: AuthMode = "local"
    oidc_issuer: str | None = None
    oidc_audience: str | None = Field(default=None, min_length=1, max_length=500)
    oidc_jwks_url: AnyHttpUrl | None = None
    public_base_url: AnyHttpUrl | None = None
    oidc_algorithms: list[OidcAlgorithm] = Field(default_factory=_default_oidc_algorithms)
    oidc_clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    oidc_jwks_cache_seconds: int = Field(default=3_600, ge=30, le=86_400)
    oidc_jwks_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30)
    dashboard_oidc_provider: str = Field(default="evalforge", min_length=1, max_length=64)

    real_runs_enabled: bool = False
    executor_mode: ExecutorMode = "embedded_single"
    worker_poll_interval_seconds: float = Field(default=0.5, ge=0.1, le=30)
    worker_lease_seconds: int = Field(default=120, ge=10, le=3_600)
    worker_heartbeat_seconds: int = Field(default=20, ge=1, le=1_200)
    max_concurrent_generations: int = Field(default=4, ge=1, le=64)
    max_cases_per_dataset: int = Field(default=500, ge=1, le=500)
    max_variants_per_run: int = Field(default=12, ge=1, le=100)
    max_calls_per_run: int = Field(default=1_000, ge=1, le=100_000)
    max_output_tokens: int = Field(default=2_048, ge=1, le=131_072)
    max_input_chars_per_case: int = Field(default=20_000, ge=100, le=20_000)
    max_context_chars_per_case: int = Field(default=100_000, ge=100, le=100_000)
    max_prompt_chars: int = Field(default=50_000, ge=100, le=50_000)
    max_rendered_prompt_chars_per_call: int = Field(default=250_000, ge=1_000, le=10_000_000)
    max_estimated_input_tokens_per_run: int = Field(default=2_000_000, ge=1_000, le=1_000_000_000)
    input_token_overhead_per_request: int = Field(default=128, ge=0, le=4_096)
    max_estimated_cost_micro_usd_per_run: int = Field(
        default=10_000_000, ge=0, le=1_000_000_000_000
    )
    provider_timeout_seconds: float = Field(default=45.0, gt=0, le=600)

    auto_migrate: bool = True
    seed_demo: bool = False
    application_version: str = Field(default=__version__, min_length=1, max_length=64)

    openai_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    openai_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    openai_model_allowlist: list[str] = Field(default_factory=lambda: ["gpt-4.1-mini"])

    compatible_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    compatible_auth_mode: Literal["api_key", "none"] = "api_key"
    compatible_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434/v1")
    compatible_model_allowlist: list[str] = Field(default_factory=list)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Accept SQLite and PostgreSQL SQLAlchemy URLs without opening a connection."""

        candidate = value.strip()
        try:
            url = make_url(candidate)
        except Exception as exc:  # SQLAlchemy exposes multiple parse error subclasses.
            raise ValueError("database_url must be a valid SQLAlchemy URL") from exc

        backend = url.get_backend_name()
        if backend not in {"sqlite", "postgresql"}:
            raise ValueError("database_url must use SQLite or PostgreSQL")
        if backend == "postgresql" and url.drivername != "postgresql+psycopg":
            raise ValueError("PostgreSQL database_url must use the postgresql+psycopg driver")
        return candidate

    @field_validator("openai_api_key", "compatible_api_key", mode="before")
    @classmethod
    def normalize_empty_secret(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("openai_model_allowlist", "compatible_model_allowlist")
    @classmethod
    def normalize_model_allowlist(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(model.strip() for model in value if model.strip()))
        return normalized

    @field_validator("trusted_hosts")
    @classmethod
    def normalize_trusted_hosts(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(host.strip().casefold() for host in value if host.strip()))
        if not normalized or any("/" in host or "://" in host for host in normalized):
            raise ValueError("trusted_hosts must contain hostnames without schemes or paths")
        return normalized

    @field_validator("oidc_algorithms")
    @classmethod
    def normalize_oidc_algorithms(cls, value: list[OidcAlgorithm]) -> list[OidcAlgorithm]:
        normalized = list(dict.fromkeys(value))
        if not normalized:
            raise ValueError("oidc_algorithms must contain RS256 or ES256")
        return normalized

    @field_validator("dashboard_oidc_provider")
    @classmethod
    def validate_dashboard_oidc_provider(cls, value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"[A-Za-z0-9-]+", normalized) is None:
            raise ValueError("dashboard_oidc_provider contains unsupported characters")
        return normalized

    @field_validator("oidc_issuer")
    @classmethod
    def validate_oidc_issuer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("oidc_issuer must be an absolute HTTP(S) URL without credentials")
        return normalized

    @model_validator(mode="after")
    def validate_identity_mode(self) -> Settings:
        if self.worker_heartbeat_seconds * 2 >= self.worker_lease_seconds:
            raise ValueError("worker heartbeat must be less than half the lease duration")
        if self.executor_mode == "database_worker" and self.database_backend == "sqlite":
            raise ValueError("database_worker executor mode requires PostgreSQL")
        if self.auth_mode == "local":
            if not _is_loopback_host(self.api_host) or not _is_loopback_host(self.dashboard_host):
                raise ValueError("local auth mode requires loopback API and dashboard bindings")
            if self.public_base_url is not None and not _is_loopback_host(
                self.public_base_url.host or ""
            ):
                raise ValueError("local auth mode cannot use a non-loopback public URL")
            return self

        public_base_url = self.public_base_url
        required = {
            "oidc_issuer": self.oidc_issuer,
            "oidc_audience": self.oidc_audience,
            "oidc_jwks_url": self.oidc_jwks_url,
            "public_base_url": public_base_url,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"OIDC configuration is incomplete: {', '.join(sorted(missing))}")
        assert public_base_url is not None
        issuer_scheme = urlsplit(self.oidc_issuer or "").scheme
        if (
            issuer_scheme != "https"
            or self.oidc_jwks_url is None
            or self.oidc_jwks_url.scheme != "https"
        ):
            raise ValueError("OIDC issuer and JWKS URL must use HTTPS")
        if self.environment != "test":
            if public_base_url.scheme != "https" or self.api_url.scheme != "https":
                raise ValueError("OIDC public base URL and API URL must use HTTPS")
            public_host = (public_base_url.host or "").casefold()
            if not _trusted_host_matches(public_host, self.trusted_hosts):
                raise ValueError("trusted_hosts must allow the public base URL host")
        if self.seed_demo:
            raise ValueError("seed_demo must be disabled in OIDC auth mode")
        return self

    @property
    def database_backend(self) -> str:
        return make_url(self.database_url).get_backend_name()

    @property
    def is_sqlite(self) -> bool:
        return self.database_backend == "sqlite"

    @property
    def cors_origin_strings(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.cors_origins]

    def provider_capabilities(self) -> dict[str, object]:
        """Return UI-safe provider readiness without returning credential fields."""

        return {
            "real_runs_enabled": self.real_runs_enabled,
            "openai": {
                "configured": self.openai_api_key is not None,
                "models": list(self.openai_model_allowlist),
            },
            "openai_compatible": {
                "configured": (
                    self.compatible_api_key is not None or self.compatible_auth_mode == "none"
                ),
                "auth_mode": self.compatible_auth_mode,
                "models": list(self.compatible_model_allowlist),
            },
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable-by-convention settings snapshot per process."""

    return Settings()


def _is_loopback_host(value: str) -> bool:
    candidate = value.strip().strip("[]").casefold()
    if candidate == "localhost":
        return True
    try:
        return ip_address(candidate).is_loopback
    except ValueError:
        return False


def _trusted_host_matches(host: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern == "*" or pattern == host:
            return True
        if pattern.startswith("*.") and host.endswith(pattern[1:]):
            return True
    return False
