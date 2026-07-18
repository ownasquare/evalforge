"""Secret-safe dashboard identity helpers.

The dashboard supports a zero-configuration local workspace and an optional
Streamlit OIDC session. Access tokens remain behind callable providers so they
are not copied into Streamlit session state, dataclass representations, or
cache keys.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from evalforge.config import Settings, get_settings

AuthMode = Literal["local", "oidc"]
WorkspaceRole = Literal["owner", "admin", "editor", "viewer"]

_WORKSPACE_ROLES = frozenset({"owner", "admin", "editor", "viewer"})
_MARKDOWN_SPECIAL_CHARACTERS = frozenset("\\`*_{}[]<>()#+-.!|>")


class MissingAccessTokenError(RuntimeError):
    """Raised when Streamlit authenticated a user without exposing an access token."""


@dataclass(frozen=True, slots=True)
class DashboardAuthConfig:
    """Public dashboard auth configuration derived from validated application settings."""

    mode: AuthMode
    provider: str | None


@dataclass(frozen=True, slots=True)
class DashboardAuthContext:
    """Current dashboard identity without a serializable plaintext credential."""

    mode: AuthMode
    identity_fingerprint: str
    _access_token_provider: Callable[[], str | None] = field(repr=False, compare=False)

    @property
    def access_token(self) -> str | None:
        """Return the live access token only at request time."""

        token = self._access_token_provider()
        if token is None:
            return None
        return _validated_token(token)


@dataclass(frozen=True, slots=True)
class DashboardAccount:
    """Small account projection safe to show in the dashboard shell."""

    id: str
    display_name: str
    email: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceOption:
    """Workspace membership safe to retain in Streamlit session state."""

    id: str
    name: str
    role: WorkspaceRole


def configured_auth(settings: Settings | None = None) -> DashboardAuthConfig:
    """Derive dashboard auth from the application's validated settings snapshot."""

    runtime = settings or get_settings()
    if runtime.auth_mode == "local":
        return DashboardAuthConfig(mode="local", provider=None)
    return DashboardAuthConfig(mode="oidc", provider=runtime.dashboard_oidc_provider)


def current_auth_context(
    config: DashboardAuthConfig,
    *,
    user: object | None = None,
) -> DashboardAuthContext | None:
    """Resolve local or Streamlit OIDC identity without retaining the token directly."""

    if config.mode == "local":
        return DashboardAuthContext(
            mode="local",
            identity_fingerprint="local",
            _access_token_provider=lambda: None,
        )

    if user is None:
        import streamlit as st

        user = st.user
    if not bool(getattr(user, "is_logged_in", False)):
        return None

    token = _access_token_from_user(user)
    if token is None:
        raise MissingAccessTokenError(
            "The identity provider did not expose an API access token to this session."
        )

    def access_token_provider() -> str | None:
        return _access_token_from_user(user)

    return DashboardAuthContext(
        mode="oidc",
        identity_fingerprint=_fingerprint(token),
        _access_token_provider=access_token_provider,
    )


def parse_account(payload: Mapping[str, object]) -> DashboardAccount:
    """Extract only the account fields the workbench is allowed to display."""

    candidate = _mapping_value(payload, "user", "account", "principal") or payload
    account_id = (
        _safe_text(candidate.get("id"), maximum=256)
        or _safe_text(candidate.get("user_id"), maximum=256)
        or "current-user"
    )
    email = _safe_text(candidate.get("email"), maximum=320)
    display_name = (
        _safe_text(candidate.get("display_name"), maximum=160)
        or _safe_text(candidate.get("name"), maximum=160)
        or email
        or "Signed-in user"
    )
    return DashboardAccount(id=account_id, display_name=display_name, email=email)


def parse_workspaces(payload: Mapping[str, object] | list[object]) -> list[WorkspaceOption]:
    """Normalize workspace membership envelopes and drop inactive or unknown roles."""

    candidates: list[object]
    if isinstance(payload, list):
        candidates = payload
    else:
        candidates = []
        for key in ("items", "workspaces", "memberships", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break

    workspaces: list[WorkspaceOption] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        workspace = _mapping_value(item, "workspace") or item
        active = item.get("active", workspace.get("active", True))
        status = _safe_text(item.get("status", workspace.get("status")), maximum=32)
        inactive_status = status and status.casefold() in {
            "inactive",
            "suspended",
            "disabled",
        }
        if active is False or inactive_status:
            continue
        workspace_id = _safe_text(workspace.get("id"), maximum=256)
        name = _safe_text(workspace.get("name"), maximum=160)
        role_value = item.get("role", workspace.get("role"))
        role = _safe_text(role_value, maximum=32)
        normalized_role = role.casefold() if role else ""
        if (
            not workspace_id
            or not name
            or workspace_id in seen
            or normalized_role not in _WORKSPACE_ROLES
        ):
            continue
        seen.add(workspace_id)
        workspaces.append(
            WorkspaceOption(
                id=workspace_id,
                name=name,
                role=normalized_role,  # type: ignore[arg-type]
            )
        )
    return workspaces


def safe_markdown_text(value: str) -> str:
    """Escape API-provided labels before placing them inside Markdown chrome."""

    return "".join(
        f"\\{character}" if character in _MARKDOWN_SPECIAL_CHARACTERS else character
        for character in value
    )


def _access_token_from_user(user: object) -> str | None:
    try:
        tokens = getattr(user, "tokens", None)
    except Exception:
        return None
    if not isinstance(tokens, Mapping):
        return None
    try:
        token = tokens.get("access")
    except Exception:
        return None
    if not isinstance(token, str) or not token.strip():
        return None
    return _validated_token(token)


def _validated_token(token: str) -> str:
    if not token or token != token.strip() or "\r" in token or "\n" in token:
        raise MissingAccessTokenError("The API access token has an invalid format.")
    return token


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mapping_value(payload: Mapping[str, object], *keys: str) -> Mapping[str, object] | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _safe_text(value: object, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or any(ord(character) < 32 for character in candidate):
        return None
    return candidate[:maximum]
