"""Authentication and workspace-authorization contracts."""

from evalforge.security.auth import (
    AuthBackend,
    AuthenticatedPrincipal,
    AuthenticationError,
    CachedJwksResolver,
    LocalAuthenticator,
    OidcJwtAuthenticator,
    SigningKeyResolver,
)
from evalforge.security.permissions import (
    LOCAL_ISSUER,
    LOCAL_MEMBERSHIP_ID,
    LOCAL_SUBJECT,
    LOCAL_USER_ID,
    LOCAL_WORKSPACE_ID,
    LOCAL_WORKSPACE_SLUG,
    AuthorizationError,
    WorkspaceContext,
    WorkspaceRole,
    local_workspace_context,
    require_role,
    role_allows,
)

__all__ = [
    "LOCAL_ISSUER",
    "LOCAL_MEMBERSHIP_ID",
    "LOCAL_SUBJECT",
    "LOCAL_USER_ID",
    "LOCAL_WORKSPACE_ID",
    "LOCAL_WORKSPACE_SLUG",
    "AuthBackend",
    "AuthenticatedPrincipal",
    "AuthenticationError",
    "AuthorizationError",
    "CachedJwksResolver",
    "LocalAuthenticator",
    "OidcJwtAuthenticator",
    "SigningKeyResolver",
    "WorkspaceContext",
    "WorkspaceRole",
    "local_workspace_context",
    "require_role",
    "role_allows",
]
