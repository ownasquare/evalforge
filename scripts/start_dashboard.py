"""Start Streamlit with the validated EvalForge dashboard binding."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from streamlit.web import cli as streamlit_cli

from evalforge.config import Settings, get_settings
from evalforge.demo import DASHBOARD_PROVIDER_SECRET_KEYS, dashboard_environment

_AUTH_FILE_ENV = "EVALFORGE_STREAMLIT_AUTH_FILE"
_REQUIRED_PROVIDER_KEYS = ("client_id", "client_secret", "server_metadata_url")


def main() -> None:
    """Replace this process with Streamlit after settings validation succeeds."""

    settings = get_settings()
    auth_file = _validated_auth_file(settings)
    dashboard_host = settings.dashboard_host
    dashboard_port = settings.dashboard_port
    sanitized_environment = dashboard_environment(settings)
    previous_secrets = {key: os.environ.get(key) for key in DASHBOARD_PROVIDER_SECRET_KEYS}
    os.environ.update({key: sanitized_environment[key] for key in DASHBOARD_PROVIDER_SECRET_KEYS})
    cache_clear = getattr(get_settings, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()
    del settings
    launcher = Path(__file__).parents[1] / "src/evalforge/streamlit_app.py"
    arguments = [
        "run",
        str(launcher),
        f"--server.address={dashboard_host}",
        f"--server.port={dashboard_port}",
        "--server.headless=true",
    ]
    if auth_file is not None:
        arguments.append(f"--secrets.files={auth_file}")
    try:
        streamlit_cli.main(args=arguments, prog_name="streamlit")
    finally:
        for key, value in previous_secrets.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cache_clear = getattr(get_settings, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()


def _validated_auth_file(settings: Settings) -> Path | None:
    """Validate the mounted Streamlit OIDC secret before starting the dashboard."""

    if settings.auth_mode != "oidc":
        return None
    configured_path = os.getenv(_AUTH_FILE_ENV, "").strip()
    if not configured_path:
        raise RuntimeError("OIDC dashboard auth configuration is not mounted.")
    auth_file = Path(configured_path)
    if not auth_file.is_absolute() or not auth_file.is_file():
        raise RuntimeError("OIDC dashboard auth configuration is unavailable.")
    try:
        with auth_file.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("OIDC dashboard auth configuration is unreadable.") from exc
    _validate_auth_document(document, settings)
    return auth_file


def _validate_auth_document(document: Mapping[str, object], settings: Settings) -> None:
    auth = document.get("auth")
    if not isinstance(auth, Mapping):
        raise RuntimeError("OIDC dashboard auth configuration is missing the auth section.")

    redirect_uri = _required_secret_text(auth, "redirect_uri")
    cookie_secret = _required_secret_text(auth, "cookie_secret")
    if auth.get("expose_tokens") != ["access"]:
        raise RuntimeError("OIDC dashboard auth must expose only the access token.")
    if len(cookie_secret.encode("utf-8")) < 32:
        raise RuntimeError("OIDC dashboard cookie secret must contain at least 32 bytes.")
    _validate_auth_url(
        redirect_uri,
        label="redirect URI",
        require_https=settings.environment != "test",
        required_suffix="/oauth2callback",
    )

    provider = auth.get(settings.dashboard_oidc_provider)
    if not isinstance(provider, Mapping):
        raise RuntimeError("OIDC dashboard provider configuration is missing.")
    for key in _REQUIRED_PROVIDER_KEYS:
        _required_secret_text(provider, key)
    _validate_auth_url(
        _required_secret_text(provider, "server_metadata_url"),
        label="provider metadata URL",
        require_https=settings.environment != "test",
    )


def _required_secret_text(section: Mapping[str, object], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RuntimeError(f"OIDC dashboard auth configuration is missing {key}.")
    return value


def _validate_auth_url(
    value: str,
    *,
    label: str,
    require_https: bool,
    required_suffix: str | None = None,
) -> None:
    parsed = urlsplit(value)
    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if (
        parsed.scheme not in allowed_schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (required_suffix is not None and not parsed.path.endswith(required_suffix))
    ):
        raise RuntimeError(f"OIDC dashboard {label} is invalid.")


if __name__ == "__main__":
    main()
