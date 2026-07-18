from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from evalforge.config import Settings
from evalforge.dashboard.auth import (
    DashboardAuthConfig,
    MissingAccessTokenError,
    configured_auth,
    current_auth_context,
    parse_account,
    parse_workspaces,
    safe_markdown_text,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "environment": "test",
        "database_url": "sqlite+pysqlite:///:memory:",
    }
    values.update(overrides)
    return Settings(**values)


def _oidc_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "auth_mode": "oidc",
        "oidc_issuer": "https://identity.test",
        "oidc_audience": "evalforge-api",
        "oidc_jwks_url": "https://identity.test/jwks.json",
        "public_base_url": "http://evalforge.test",
    }
    values.update(overrides)
    return _settings(**values)


def test_local_auth_is_zero_configuration_and_does_not_read_user_tokens() -> None:
    config = configured_auth(_settings())
    user = SimpleNamespace(is_logged_in=False, tokens={"access": "must-not-be-read"})

    context = current_auth_context(config, user=user)

    assert config == DashboardAuthConfig(mode="local", provider=None)
    assert context is not None
    assert context.mode == "local"
    assert context.access_token is None
    assert context.identity_fingerprint == "local"


def test_dashboard_auth_consumes_validated_application_settings() -> None:
    with pytest.raises(ValidationError, match="dashboard_oidc_provider"):
        _oidc_settings(dashboard_oidc_provider="unsupported/provider")

    assert configured_auth(_oidc_settings(dashboard_oidc_provider="company-provider")) == (
        DashboardAuthConfig(mode="oidc", provider="company-provider")
    )


def test_oidc_requires_login_and_an_exposed_access_token() -> None:
    config = configured_auth(_oidc_settings(dashboard_oidc_provider="company"))

    assert current_auth_context(config, user=SimpleNamespace(is_logged_in=False)) is None
    with pytest.raises(MissingAccessTokenError):
        current_auth_context(
            config,
            user=SimpleNamespace(is_logged_in=True, tokens={}),
        )


def test_oidc_handles_an_unexposed_streamlit_token_property_safely() -> None:
    class UserWithoutExposedTokens:
        is_logged_in = True

        @property
        def tokens(self):
            raise RuntimeError("tokens are not exposed")

    with pytest.raises(MissingAccessTokenError):
        current_auth_context(
            DashboardAuthConfig(mode="oidc", provider="company"),
            user=UserWithoutExposedTokens(),
        )


def test_oidc_context_keeps_plaintext_token_out_of_repr_and_serialized_fields() -> None:
    config = DashboardAuthConfig(mode="oidc", provider="company")
    context = current_auth_context(
        config,
        user=SimpleNamespace(
            is_logged_in=True,
            tokens={"access": "private-access-token"},
        ),
    )

    assert context is not None
    assert context.access_token == "private-access-token"
    assert "private-access-token" not in repr(context)
    assert "private-access-token" not in repr(asdict(context))


def test_account_and_workspace_parsing_accepts_safe_api_envelopes() -> None:
    account = parse_account(
        {
            "user": {
                "id": "user-1",
                "display_name": "Morgan Lee",
                "email": "morgan@example.com",
            }
        }
    )
    workspaces = parse_workspaces(
        {
            "items": [
                {
                    "id": "workspace-1",
                    "name": "Quality team",
                    "role": "editor",
                    "active": True,
                },
                {
                    "id": "workspace-2",
                    "name": "Suspended",
                    "role": "owner",
                    "active": False,
                },
                {
                    "id": "workspace-3",
                    "name": "Administration",
                    "role": "admin",
                },
                {
                    "id": "workspace-4",
                    "name": "Unknown role",
                    "role": "superuser",
                },
            ]
        }
    )

    assert account.display_name == "Morgan Lee"
    assert account.email == "morgan@example.com"
    assert [(workspace.id, workspace.name, workspace.role) for workspace in workspaces] == [
        ("workspace-1", "Quality team", "editor"),
        ("workspace-3", "Administration", "admin"),
    ]

    direct_account = parse_account({"user_id": "user-2", "display_name": "Avery"})
    assert direct_account.id == "user-2"


def test_api_labels_are_escaped_before_markdown_rendering() -> None:
    rendered = safe_markdown_text("![Remote](https://example.com/pixel)")

    assert rendered == r"\!\[Remote\]\(https://example\.com/pixel\)"
