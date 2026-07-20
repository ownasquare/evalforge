from __future__ import annotations

import stat
import tomllib
from pathlib import Path
from typing import Any

import pytest

from evalforge.config import Settings
from scripts import start_api, start_dashboard


def _container_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        auth_mode="oidc",
        oidc_issuer="https://identity.test/issuer",
        oidc_audience="evalforge-api",
        oidc_jwks_url="https://identity.test/jwks.json",
        public_base_url="http://evalforge.test",
        dashboard_public_base_url="http://dashboard.test",
        api_host="192.0.2.10",
        api_port=8_123,
        dashboard_host="192.0.2.11",
        dashboard_port=8_623,
    )


def _write_streamlit_auth_config(path: Path, *, expose_tokens: str = '["access"]') -> Path:
    path.write_text(
        "\n".join(
            (
                "[auth]",
                'redirect_uri = "http://dashboard.test/oauth2callback"',
                'cookie_secret = "0123456789abcdef0123456789abcdef"',
                f"expose_tokens = {expose_tokens}",
                "",
                "[auth.evalforge]",
                'client_id = "dashboard-client"',
                'client_secret = "dashboard-client-secret"',
                'server_metadata_url = "https://identity.test/.well-known/openid-configuration"',
            )
        ),
        encoding="utf-8",
    )
    return path


def test_api_launcher_uses_the_validated_bind_settings(database_url: str, monkeypatch: Any) -> None:
    settings = _container_settings(database_url)
    invocation: dict[str, object] = {}

    monkeypatch.setattr(start_api, "get_settings", lambda: settings)
    monkeypatch.setattr(
        start_api.uvicorn,
        "run",
        lambda application, **options: invocation.update({"application": application, **options}),
    )

    start_api.main()

    assert invocation == {
        "application": "evalforge.api.app:app",
        "host": "192.0.2.10",
        "port": 8_123,
        "workers": 1,
        "access_log": False,
    }


def test_dashboard_launcher_uses_the_validated_bind_settings(
    database_url: str, monkeypatch: Any, tmp_path: Path
) -> None:
    settings = _container_settings(database_url)
    auth_file = _write_streamlit_auth_config(tmp_path / "streamlit-auth.toml")
    invocation: dict[str, object] = {}

    monkeypatch.setenv("EVALFORGE_STREAMLIT_AUTH_FILE", str(auth_file))
    monkeypatch.setattr(start_dashboard, "get_settings", lambda: settings)
    monkeypatch.setattr(
        start_dashboard.streamlit_cli,
        "main",
        lambda **options: invocation.update(options),
    )

    start_dashboard.main()

    arguments = invocation["args"]
    assert isinstance(arguments, list)
    assert invocation["prog_name"] == "streamlit"
    assert arguments[0] == "run"
    assert (
        Path(arguments[1]).resolve()
        == (Path(__file__).parents[2] / "src" / "evalforge" / "streamlit_app.py").resolve()
    )
    assert "--server.address=192.0.2.11" in arguments
    assert "--server.port=8623" in arguments
    assert "--server.headless=true" in arguments
    assert f"--secrets.files={auth_file}" in arguments


def test_dashboard_launcher_drops_provider_secrets_and_cached_settings(
    database_url: str, monkeypatch: Any, tmp_path: Path
) -> None:
    settings = _container_settings(database_url)
    auth_file = _write_streamlit_auth_config(tmp_path / "streamlit-auth.toml")
    cache_clear_count = 0
    observed_secrets: dict[str, str | None] = {}

    class SettingsLoader:
        def __call__(self) -> Settings:
            return settings

        def cache_clear(self) -> None:
            nonlocal cache_clear_count
            cache_clear_count += 1

    monkeypatch.setenv("EVALFORGE_STREAMLIT_AUTH_FILE", str(auth_file))
    monkeypatch.setenv("EVALFORGE_OPENAI_API_KEY", "backend-only-openai")
    monkeypatch.setenv("EVALFORGE_COMPATIBLE_API_KEY", "backend-only-compatible")
    monkeypatch.setenv("EVALFORGE_METRICS_BEARER_TOKEN", "backend-only-metrics")
    monkeypatch.setenv(
        "EVALFORGE_DATABASE_URL",
        "postgresql+psycopg://evalforge:database-secret@db.test/evalforge",
    )
    monkeypatch.setattr(start_dashboard, "get_settings", SettingsLoader())
    monkeypatch.setattr(
        start_dashboard.streamlit_cli,
        "main",
        lambda **options: observed_secrets.update(
            {
                "openai": start_dashboard.os.environ.get("EVALFORGE_OPENAI_API_KEY"),
                "compatible": start_dashboard.os.environ.get("EVALFORGE_COMPATIBLE_API_KEY"),
                "metrics": start_dashboard.os.environ.get("EVALFORGE_METRICS_BEARER_TOKEN"),
                "database": start_dashboard.os.environ.get("EVALFORGE_DATABASE_URL"),
            }
        ),
    )

    start_dashboard.main()

    assert cache_clear_count == 2
    assert observed_secrets["openai"] is not None
    assert observed_secrets["compatible"] is not None
    assert observed_secrets["metrics"] is not None
    assert observed_secrets["database"] == "sqlite+pysqlite:///:memory:"
    assert observed_secrets["openai"].strip() == ""
    assert observed_secrets["compatible"].strip() == ""
    assert observed_secrets["metrics"].strip() == ""
    assert start_dashboard.os.environ["EVALFORGE_OPENAI_API_KEY"] == "backend-only-openai"
    assert start_dashboard.os.environ["EVALFORGE_COMPATIBLE_API_KEY"] == "backend-only-compatible"
    assert start_dashboard.os.environ["EVALFORGE_METRICS_BEARER_TOKEN"] == ("backend-only-metrics")
    assert start_dashboard.os.environ["EVALFORGE_DATABASE_URL"] == (
        "postgresql+psycopg://evalforge:database-secret@db.test/evalforge"
    )


def test_dashboard_launcher_fails_closed_without_mounted_oidc_auth(
    database_url: str, monkeypatch: Any
) -> None:
    settings = _container_settings(database_url)
    invoked = False

    def record_invocation(**_options: object) -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.delenv("EVALFORGE_STREAMLIT_AUTH_FILE", raising=False)
    monkeypatch.setattr(start_dashboard, "get_settings", lambda: settings)
    monkeypatch.setattr(start_dashboard.streamlit_cli, "main", record_invocation)

    with pytest.raises(RuntimeError, match="not mounted"):
        start_dashboard.main()

    assert invoked is False


def test_dashboard_launcher_requires_access_only_token_exposure(
    database_url: str, monkeypatch: Any, tmp_path: Path
) -> None:
    settings = _container_settings(database_url)
    auth_file = _write_streamlit_auth_config(
        tmp_path / "streamlit-auth.toml",
        expose_tokens='["id", "access"]',
    )
    monkeypatch.setenv("EVALFORGE_STREAMLIT_AUTH_FILE", str(auth_file))
    monkeypatch.setattr(start_dashboard, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="only the access token"):
        start_dashboard.main()


def test_dashboard_launcher_materializes_and_removes_hosted_oidc_auth(
    database_url: str,
    monkeypatch: Any,
) -> None:
    settings = _container_settings(database_url)
    observed: dict[str, Any] = {}
    environment_values = {
        "EVALFORGE_DASHBOARD_OIDC_CLIENT_ID": "hosted-dashboard-client",
        "EVALFORGE_DASHBOARD_OIDC_CLIENT_SECRET": "hosted-dashboard-secret",
        "EVALFORGE_DASHBOARD_OIDC_SERVER_METADATA_URL": (
            "https://identity.test/.well-known/openid-configuration"
        ),
        "EVALFORGE_DASHBOARD_OIDC_COOKIE_SECRET": ("0123456789abcdef0123456789abcdef"),
    }
    monkeypatch.delenv("EVALFORGE_STREAMLIT_AUTH_FILE", raising=False)
    for key, value in environment_values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(start_dashboard, "get_settings", lambda: settings)

    def inspect_invocation(**options: object) -> None:
        arguments = options["args"]
        assert isinstance(arguments, list)
        secrets_argument = next(
            argument for argument in arguments if argument.startswith("--secrets.files=")
        )
        auth_file = Path(secrets_argument.split("=", 1)[1])
        observed["auth_file"] = auth_file
        observed["mode"] = stat.S_IMODE(auth_file.stat().st_mode)
        with auth_file.open("rb") as stream:
            observed["document"] = tomllib.load(stream)
        observed["runtime_values"] = {
            key: start_dashboard.os.environ.get(key) for key in environment_values
        }

    monkeypatch.setattr(start_dashboard.streamlit_cli, "main", inspect_invocation)

    start_dashboard.main()

    auth_file = observed["auth_file"]
    assert isinstance(auth_file, Path)
    assert auth_file.exists() is False
    assert observed["mode"] == 0o600
    assert observed["runtime_values"] == {key: "" for key in environment_values}
    document = observed["document"]
    assert document["auth"]["redirect_uri"] == "http://dashboard.test/oauth2callback"
    assert document["auth"]["expose_tokens"] == ["access"]
    assert document["auth"]["evalforge"]["client_id"] == "hosted-dashboard-client"
    assert document["auth"]["evalforge"]["client_secret"] == "hosted-dashboard-secret"
    for key, value in environment_values.items():
        assert start_dashboard.os.environ[key] == value


def test_dashboard_launcher_rejects_incomplete_hosted_oidc_environment(
    database_url: str,
    monkeypatch: Any,
) -> None:
    settings = _container_settings(database_url)
    monkeypatch.delenv("EVALFORGE_STREAMLIT_AUTH_FILE", raising=False)
    for key in start_dashboard._DASHBOARD_AUTH_ENV_KEYS.values():
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(start_dashboard, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="secret values are incomplete"):
        start_dashboard.main()
