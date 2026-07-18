"""Local and OIDC bearer authentication without token persistence."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import jwt

from evalforge.errors import EvalForgeError
from evalforge.security.permissions import LOCAL_ISSUER, LOCAL_SUBJECT, LOCAL_USER_ID

_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "ES256"})
_MAX_BEARER_LENGTH = 16_384
_MAX_JWKS_BYTES = 256 * 1024
_MAX_JWKS_KEYS = 100
_UNKNOWN_KEY_REFRESH_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Validated identity attributes safe to retain in request-local state."""

    user_id: str | None
    issuer: str
    subject: str
    display_name: str | None = None
    email: str | None = None
    is_local: bool = False


class AuthenticationError(EvalForgeError):
    """A stable 401 that never echoes credentials or verifier internals."""

    def __init__(self, *, invalid: bool = False) -> None:
        message = (
            "Authentication credentials are invalid." if invalid else "Authentication is required."
        )
        super().__init__("authentication_required", message, status_code=401)
        self.headers = {"WWW-Authenticate": "Bearer"}


class AuthBackend(Protocol):
    """Authenticate one optional HTTP Authorization header."""

    def authenticate(self, authorization: str | None) -> AuthenticatedPrincipal: ...


class SigningKeyResolver(Protocol):
    """Resolve a verified-provider public key without receiving the token text."""

    def resolve(self, key_id: str, algorithm: str) -> Any: ...


JwksFetcher = Callable[[str, float], Mapping[str, Any]]


class _KeyResolutionError(RuntimeError):
    pass


def _validate_https_origin(value: str, *, label: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be an absolute HTTPS URL without credentials")
    return candidate


def _fetch_jwks(url: str, timeout_seconds: float) -> Mapping[str, Any]:
    try:
        timeout = httpx.Timeout(timeout_seconds)
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            if len(response.content) > _MAX_JWKS_BYTES:
                raise _KeyResolutionError("JWKS response exceeds the supported size")
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise _KeyResolutionError("JWKS could not be loaded") from exc
    if not isinstance(payload, dict):
        raise _KeyResolutionError("JWKS has an invalid shape")
    return payload


class CachedJwksResolver:
    """Bounded, thread-safe JWKS retrieval with a monotonic cache lifetime."""

    def __init__(
        self,
        *,
        jwks_url: str,
        algorithms: tuple[str, ...],
        cache_seconds: int,
        timeout_seconds: float,
        fetcher: JwksFetcher = _fetch_jwks,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._jwks_url = _validate_https_origin(jwks_url, label="JWKS URL")
        self._algorithms = _validated_algorithms(algorithms)
        if not 30 <= cache_seconds <= 86_400:
            raise ValueError("JWKS cache lifetime must be between 30 and 86400 seconds")
        if not 0.5 <= timeout_seconds <= 30:
            raise ValueError("JWKS timeout must be between 0.5 and 30 seconds")
        self._cache_seconds = cache_seconds
        self._timeout_seconds = timeout_seconds
        self._fetcher = fetcher
        self._clock = clock
        self._keys: dict[tuple[str, str], Any] = {}
        self._expires_at = 0.0
        self._refresh_not_before = 0.0
        self._lock = threading.Lock()

    def resolve(self, key_id: str, algorithm: str) -> Any:
        if not key_id or algorithm not in self._algorithms:
            raise _KeyResolutionError("signing key is not allowed")
        cache_key = (key_id, algorithm)
        with self._lock:
            now = self._clock()
            cached = self._keys.get(cache_key)
            if cached is not None and now < self._expires_at:
                return cached
            if now < self._refresh_not_before:
                raise _KeyResolutionError("signing key was not found")
            self._refresh(now)
            resolved = self._keys.get(cache_key)
            if resolved is None:
                raise _KeyResolutionError("signing key was not found")
            return resolved

    def _refresh(self, now: float) -> None:
        self._refresh_not_before = now + min(
            _UNKNOWN_KEY_REFRESH_SECONDS, float(self._cache_seconds)
        )
        payload = self._fetcher(self._jwks_url, self._timeout_seconds)
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= _MAX_JWKS_KEYS:
            raise _KeyResolutionError("JWKS keys have an invalid shape")
        parsed: dict[tuple[str, str], Any] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, dict):
                continue
            key_id = raw_key.get("kid")
            declared_algorithm = raw_key.get("alg")
            if not isinstance(key_id, str) or not key_id:
                continue
            try:
                public_key = jwt.PyJWK.from_dict(raw_key)
            except (jwt.PyJWTError, ValueError):
                continue
            algorithm = (
                declared_algorithm
                if isinstance(declared_algorithm, str)
                else public_key.algorithm_name
            )
            if algorithm in self._algorithms and public_key.algorithm_name == algorithm:
                parsed[(key_id, algorithm)] = public_key.key
        if not parsed:
            raise _KeyResolutionError("JWKS contains no supported signing keys")
        self._keys = parsed
        self._expires_at = now + self._cache_seconds


class LocalAuthenticator:
    """Return one deterministic owner identity for loopback-only operation."""

    def authenticate(self, authorization: str | None) -> AuthenticatedPrincipal:
        del authorization
        return AuthenticatedPrincipal(
            user_id=LOCAL_USER_ID,
            issuer=LOCAL_ISSUER,
            subject=LOCAL_SUBJECT,
            display_name="Local owner",
            is_local=True,
        )


class OidcJwtAuthenticator:
    """Validate OIDC access tokens against an exact issuer and audience."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: tuple[str, ...] = ("RS256", "ES256"),
        clock_skew_seconds: int = 30,
        jwks_cache_seconds: int = 3_600,
        jwks_timeout_seconds: float = 5.0,
        key_resolver: SigningKeyResolver | None = None,
    ) -> None:
        self._issuer = _validate_https_origin(issuer, label="OIDC issuer")
        self._audience = audience.strip()
        if not self._issuer or not self._audience:
            raise ValueError("OIDC issuer and audience are required")
        self._jwks_url = _validate_https_origin(jwks_url, label="JWKS URL")
        self._algorithms = _validated_algorithms(algorithms)
        if not 0 <= clock_skew_seconds <= 300:
            raise ValueError("OIDC clock skew must be between 0 and 300 seconds")
        if not 30 <= jwks_cache_seconds <= 86_400:
            raise ValueError("JWKS cache lifetime must be between 30 and 86400 seconds")
        if not 0.5 <= jwks_timeout_seconds <= 30:
            raise ValueError("JWKS timeout must be between 0.5 and 30 seconds")
        self._clock_skew_seconds = clock_skew_seconds
        self._key_resolver = key_resolver or CachedJwksResolver(
            jwks_url=self._jwks_url,
            algorithms=self._algorithms,
            cache_seconds=jwks_cache_seconds,
            timeout_seconds=jwks_timeout_seconds,
        )

    def authenticate(self, authorization: str | None) -> AuthenticatedPrincipal:
        token = _bearer_token(authorization)
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            key_id = header.get("kid")
            if algorithm not in self._algorithms or not isinstance(key_id, str) or not key_id:
                raise AuthenticationError(invalid=True)
            key = self._key_resolver.resolve(key_id, str(algorithm))
            claims = jwt.decode(
                token,
                key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._clock_skew_seconds,
                options={"require": ["iss", "aud", "sub", "exp"]},
            )
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject.strip() or len(subject) > 512:
                raise AuthenticationError(invalid=True)
            return AuthenticatedPrincipal(
                user_id=None,
                issuer=self._issuer,
                subject=subject.strip(),
                display_name=_optional_claim(claims, "name", max_length=200),
                email=_optional_claim(claims, "email", max_length=320),
                is_local=False,
            )
        except AuthenticationError:
            raise
        except (jwt.PyJWTError, _KeyResolutionError, TypeError, ValueError) as exc:
            raise AuthenticationError(invalid=True) from exc


def _validated_algorithms(algorithms: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(algorithm.strip() for algorithm in algorithms))
    if not normalized or any(item not in _ASYMMETRIC_ALGORITHMS for item in normalized):
        raise ValueError("OIDC algorithms must be selected from RS256 and ES256")
    return normalized


def _bearer_token(authorization: str | None) -> str:
    if not isinstance(authorization, str):
        raise AuthenticationError
    parts = authorization.strip().split()
    if (
        len(parts) != 2
        or parts[0].casefold() != "bearer"
        or not parts[1]
        or len(parts[1]) > _MAX_BEARER_LENGTH
    ):
        raise AuthenticationError
    return parts[1]


def _optional_claim(
    claims: Mapping[str, Any],
    key: str,
    *,
    max_length: int,
) -> str | None:
    value = claims.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:max_length] or None
