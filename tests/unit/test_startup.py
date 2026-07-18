from __future__ import annotations

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
