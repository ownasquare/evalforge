from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from evalforge.security.auth import (
    AuthenticationError,
    CachedJwksResolver,
    LocalAuthenticator,
    OidcJwtAuthenticator,
)
from evalforge.security.permissions import LOCAL_ISSUER, LOCAL_SUBJECT, LOCAL_USER_ID


class StaticKeyResolver:
    def __init__(self, key: Any) -> None:
        self.key = key
        self.calls: list[tuple[str, str]] = []

    def resolve(self, key_id: str, algorithm: str) -> Any:
        self.calls.append((key_id, algorithm))
        return self.key


def _claims(**updates: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": "https://identity.example",
        "aud": "evalforge-api",
        "sub": "subject-123",
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "name": "Ada Lovelace",
        "email": "ada@example.com",
    }
    claims.update(updates)
    return claims


def _rsa_token(private_key: Any, **updates: object) -> str:
    return jwt.encode(
        _claims(**updates),
        private_key,
        algorithm="RS256",
        headers={"kid": "rsa-key-1"},
    )


def _authenticator(key: Any, *, algorithms: tuple[str, ...] = ("RS256",)) -> OidcJwtAuthenticator:
    return OidcJwtAuthenticator(
        issuer="https://identity.example",
        audience="evalforge-api",
        jwks_url="https://identity.example/.well-known/jwks.json",
        algorithms=algorithms,
        clock_skew_seconds=30,
        jwks_cache_seconds=300,
        jwks_timeout_seconds=2.0,
        key_resolver=StaticKeyResolver(key),
    )


def test_local_authenticator_returns_stable_secret_free_principal() -> None:
    principal = LocalAuthenticator().authenticate(None)

    assert principal.user_id == LOCAL_USER_ID
    assert principal.issuer == LOCAL_ISSUER
    assert principal.subject == LOCAL_SUBJECT
    assert principal.display_name == "Local owner"
    assert principal.email is None
    assert principal.is_local is True
    assert "token" not in repr(principal).lower()


def test_oidc_authenticator_validates_a_pinned_rsa_token() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resolver = StaticKeyResolver(private_key.public_key())
    authenticator = OidcJwtAuthenticator(
        issuer="https://identity.example",
        audience="evalforge-api",
        jwks_url="https://identity.example/.well-known/jwks.json",
        algorithms=("RS256",),
        clock_skew_seconds=30,
        jwks_cache_seconds=300,
        jwks_timeout_seconds=2.0,
        key_resolver=resolver,
    )
    token = _rsa_token(private_key)

    principal = authenticator.authenticate(f"Bearer {token}")

    assert principal.user_id is None
    assert principal.issuer == "https://identity.example"
    assert principal.subject == "subject-123"
    assert principal.display_name == "Ada Lovelace"
    assert principal.email == "ada@example.com"
    assert principal.is_local is False
    assert resolver.calls == [("rsa-key-1", "RS256")]


def test_oidc_authenticator_accepts_only_configured_asymmetric_algorithms() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    resolver = StaticKeyResolver(private_key.public_key())
    authenticator = OidcJwtAuthenticator(
        issuer="https://identity.example",
        audience="evalforge-api",
        jwks_url="https://identity.example/.well-known/jwks.json",
        algorithms=("ES256",),
        clock_skew_seconds=0,
        jwks_cache_seconds=300,
        jwks_timeout_seconds=2.0,
        key_resolver=resolver,
    )
    token = jwt.encode(
        _claims(),
        private_key,
        algorithm="ES256",
        headers={"kid": "ec-key-1"},
    )

    assert authenticator.authenticate(f"Bearer {token}").subject == "subject-123"
    assert resolver.calls == [("ec-key-1", "ES256")]


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic value", "Bearer", "Bearer one two"],
)
def test_oidc_authenticator_rejects_missing_or_malformed_bearer(
    authorization: str | None,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(AuthenticationError) as captured:
        _authenticator(private_key.public_key()).authenticate(authorization)

    assert captured.value.status_code == 401
    assert captured.value.code == "authentication_required"
    assert captured.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize(
    "claim_updates",
    [
        {"aud": "another-api"},
        {"iss": "https://attacker.example"},
        {"exp": datetime.now(UTC) - timedelta(minutes=5)},
        {"sub": ""},
    ],
)
def test_oidc_authenticator_fails_closed_for_invalid_claims(
    claim_updates: dict[str, object],
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _rsa_token(private_key, **claim_updates)

    with pytest.raises(AuthenticationError) as captured:
        _authenticator(private_key.public_key()).authenticate(f"Bearer {token}")

    assert str(captured.value) == "Authentication credentials are invalid."
    assert token not in str(captured.value)


def test_oidc_authenticator_rejects_unpinned_algorithm_before_key_resolution() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _rsa_token(private_key)
    resolver = StaticKeyResolver(private_key.public_key())
    authenticator = OidcJwtAuthenticator(
        issuer="https://identity.example",
        audience="evalforge-api",
        jwks_url="https://identity.example/.well-known/jwks.json",
        algorithms=("ES256",),
        clock_skew_seconds=0,
        jwks_cache_seconds=300,
        jwks_timeout_seconds=2.0,
        key_resolver=resolver,
    )

    with pytest.raises(AuthenticationError):
        authenticator.authenticate(f"Bearer {token}")

    assert resolver.calls == []


def test_oidc_authenticator_rejects_non_https_jwks_url() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(ValueError, match="HTTPS"):
        OidcJwtAuthenticator(
            issuer="https://identity.example",
            audience="evalforge-api",
            jwks_url="http://identity.example/jwks.json",
            algorithms=("RS256",),
            clock_skew_seconds=30,
            jwks_cache_seconds=300,
            jwks_timeout_seconds=2.0,
            key_resolver=StaticKeyResolver(private_key.public_key()),
        )


def test_jwks_resolver_caches_supported_public_keys_until_expiry() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "rsa-key-1", "alg": "RS256", "use": "sig"})
    calls: list[tuple[str, float]] = []
    now = [100.0]

    def fetcher(url: str, timeout_seconds: float) -> dict[str, object]:
        calls.append((url, timeout_seconds))
        return {"keys": [public_jwk]}

    resolver = CachedJwksResolver(
        jwks_url="https://identity.example/.well-known/jwks.json",
        algorithms=("RS256",),
        cache_seconds=30,
        timeout_seconds=2.0,
        fetcher=fetcher,
        clock=lambda: now[0],
    )

    assert resolver.resolve("rsa-key-1", "RS256") is not None
    assert resolver.resolve("rsa-key-1", "RS256") is not None
    assert calls == [("https://identity.example/.well-known/jwks.json", 2.0)]

    now[0] = 131.0
    assert resolver.resolve("rsa-key-1", "RS256") is not None
    assert len(calls) == 2


def test_jwks_resolver_rate_limits_unknown_key_refreshes() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "known-key", "alg": "RS256", "use": "sig"})
    calls = 0
    now = [100.0]

    def fetcher(_url: str, _timeout_seconds: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"keys": [public_jwk]}

    resolver = CachedJwksResolver(
        jwks_url="https://identity.example/.well-known/jwks.json",
        algorithms=("RS256",),
        cache_seconds=300,
        timeout_seconds=2.0,
        fetcher=fetcher,
        clock=lambda: now[0],
    )

    assert resolver.resolve("known-key", "RS256") is not None
    for key_id in ("unknown-1", "unknown-2", "unknown-3"):
        with pytest.raises(RuntimeError, match="not found"):
            resolver.resolve(key_id, "RS256")
    assert calls == 1

    now[0] = 130.0
    with pytest.raises(RuntimeError, match="not found"):
        resolver.resolve("unknown-after-window", "RS256")
    assert calls == 2


def test_oidc_issuer_is_an_exact_identifier_including_trailing_slash() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resolver = StaticKeyResolver(private_key.public_key())
    authenticator = OidcJwtAuthenticator(
        issuer="https://identity.example/",
        audience="evalforge-api",
        jwks_url="https://identity.example/.well-known/jwks.json",
        algorithms=("RS256",),
        key_resolver=resolver,
    )
    exact_token = _rsa_token(private_key, iss="https://identity.example/")

    assert authenticator.authenticate(f"Bearer {exact_token}").issuer == (
        "https://identity.example/"
    )
    with pytest.raises(AuthenticationError):
        authenticator.authenticate(f"Bearer {_rsa_token(private_key)}")
