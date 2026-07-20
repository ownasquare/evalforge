"""Start Streamlit with the validated EvalForge dashboard binding."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from streamlit.web import cli as streamlit_cli

from evalforge.config import Settings, get_settings
from evalforge.demo import DASHBOARD_SERVER_SECRET_OVERRIDES, dashboard_environment

_AUTH_FILE_ENV = "EVALFORGE_STREAMLIT_AUTH_FILE"
_REQUIRED_PROVIDER_KEYS = ("client_id", "client_secret", "server_metadata_url")
_DASHBOARD_AUTH_ENV_KEYS = {
    "client_id": "EVALFORGE_DASHBOARD_OIDC_CLIENT_ID",
    "client_secret": "EVALFORGE_DASHBOARD_OIDC_CLIENT_SECRET",
    "server_metadata_url": "EVALFORGE_DASHBOARD_OIDC_SERVER_METADATA_URL",
    "cookie_secret": "EVALFORGE_DASHBOARD_OIDC_COOKIE_SECRET",
}
_SERVER_ONLY_SECRET_ENV_KEYS = tuple(DASHBOARD_SERVER_SECRET_OVERRIDES)


def main() -> None:
    """Replace this process with Streamlit after settings validation succeeds."""

    settings = get_settings()
    auth_file, temporary_auth_file = _resolved_auth_file(settings)
    dashboard_host = settings.dashboard_host
    dashboard_port = settings.dashboard_port
    sanitized_environment = dashboard_environment(settings)
    withheld_keys = (*_SERVER_ONLY_SECRET_ENV_KEYS, *_DASHBOARD_AUTH_ENV_KEYS.values())
    previous_secrets = {key: os.environ.get(key) for key in withheld_keys}
    os.environ.update({key: sanitized_environment[key] for key in _SERVER_ONLY_SECRET_ENV_KEYS})
    os.environ.update({key: "" for key in _DASHBOARD_AUTH_ENV_KEYS.values()})
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
        if temporary_auth_file and auth_file is not None:
            auth_file.unlink(missing_ok=True)


def _resolved_auth_file(settings: Settings) -> tuple[Path | None, bool]:
    """Resolve mounted auth or materialize secret env values into a short-lived file."""

    if settings.auth_mode != "oidc":
        return None, False
    configured_path = os.getenv(_AUTH_FILE_ENV, "").strip()
    if configured_path:
        auth_file = Path(configured_path)
        _validate_auth_file(auth_file, settings)
        return auth_file, False
    return _materialize_environment_auth(settings), True


def _validate_auth_file(auth_file: Path, settings: Settings) -> None:
    if not auth_file.is_absolute() or not auth_file.is_file():
        raise RuntimeError("OIDC dashboard auth configuration is unavailable.")
    try:
        with auth_file.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("OIDC dashboard auth configuration is unreadable.") from exc
    _validate_auth_document(document, settings)


def _materialize_environment_auth(settings: Settings) -> Path:
    public_base_url = settings.dashboard_public_base_url
    if public_base_url is None:
        raise RuntimeError(
            "OIDC dashboard auth configuration is not mounted and its public URL is missing."
        )
    values = {
        name: _required_environment_secret(environment_key)
        for name, environment_key in _DASHBOARD_AUTH_ENV_KEYS.items()
    }
    document: dict[str, object] = {
        "auth": {
            "redirect_uri": f"{str(public_base_url).rstrip('/')}/oauth2callback",
            "cookie_secret": values["cookie_secret"],
            "expose_tokens": ["access"],
            settings.dashboard_oidc_provider: {
                "client_id": values["client_id"],
                "client_secret": values["client_secret"],
                "server_metadata_url": values["server_metadata_url"],
            },
        }
    }
    _validate_auth_document(document, settings)
    descriptor, filename = tempfile.mkstemp(
        prefix="evalforge-streamlit-auth-",
        suffix=".toml",
    )
    auth_file = Path(filename)
    try:
        os.chmod(auth_file, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_auth_document_toml(document, settings.dashboard_oidc_provider))
        _validate_auth_file(auth_file, settings)
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        auth_file.unlink(missing_ok=True)
        raise
    return auth_file


def _required_environment_secret(environment_key: str) -> str:
    value = os.getenv(environment_key, "")
    if not value or value != value.strip():
        raise RuntimeError(
            "OIDC dashboard auth configuration is not mounted and secret values are incomplete."
        )
    return value


def _auth_document_toml(document: Mapping[str, object], provider_name: str) -> str:
    auth = document["auth"]
    if not isinstance(auth, Mapping):  # pragma: no cover - internal invariant
        raise RuntimeError("OIDC dashboard auth configuration is invalid.")
    provider = auth[provider_name]
    if not isinstance(provider, Mapping):  # pragma: no cover - internal invariant
        raise RuntimeError("OIDC dashboard auth configuration is invalid.")
    return "\n".join(
        (
            "[auth]",
            f"redirect_uri = {json.dumps(auth['redirect_uri'])}",
            f"cookie_secret = {json.dumps(auth['cookie_secret'])}",
            'expose_tokens = ["access"]',
            "",
            f"[auth.{provider_name}]",
            f"client_id = {json.dumps(provider['client_id'])}",
            f"client_secret = {json.dumps(provider['client_secret'])}",
            f"server_metadata_url = {json.dumps(provider['server_metadata_url'])}",
            "",
        )
    )


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
