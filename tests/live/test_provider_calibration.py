"""Deliberately opt-in boundary for future paid-provider calibration proof.

This contract validates only the non-secret authorization envelope. EvalForge
does not yet ship an external judge implementation, so even a fully approved
configuration stops before a credential is read or a provider client exists.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest


@pytest.mark.live
def test_provider_calibration_requires_every_explicit_approval_gate() -> None:
    if os.getenv("EVALFORGE_RUN_LIVE_CALIBRATION") != "1":
        pytest.skip("set EVALFORGE_RUN_LIVE_CALIBRATION=1 to request paid calibration")

    provider = _required_value("EVALFORGE_LIVE_CALIBRATION_PROVIDER")
    model = _required_value("EVALFORGE_LIVE_CALIBRATION_MODEL")
    allowed_providers = _allowlist("EVALFORGE_LIVE_CALIBRATION_PROVIDER_ALLOWLIST")
    allowed_models = _allowlist("EVALFORGE_LIVE_CALIBRATION_MODEL_ALLOWLIST")
    if provider not in allowed_providers or model not in allowed_models:
        pytest.skip("the selected provider and model must be explicitly allowlisted")

    spend_limit = _positive_integer("EVALFORGE_LIVE_CALIBRATION_SPEND_LIMIT_MICRO_USD")
    benchmark_path = Path(_required_value("EVALFORGE_LIVE_CALIBRATION_BENCHMARK_PATH")).expanduser()
    approved_hash = _required_value("EVALFORGE_LIVE_CALIBRATION_BENCHMARK_SHA256").lower()
    if not benchmark_path.is_absolute() or not benchmark_path.is_file():
        pytest.skip("the approved calibration benchmark must be an existing absolute file")
    if len(approved_hash) != 64 or any(
        character not in "0123456789abcdef" for character in approved_hash
    ):
        pytest.skip("the approved calibration benchmark SHA-256 is invalid")
    actual_hash = hashlib.sha256(benchmark_path.read_bytes()).hexdigest()
    if actual_hash != approved_hash:
        pytest.skip("the calibration benchmark does not match its approved SHA-256")

    assert spend_limit > 0
    pytest.skip(
        "all authorization gates are valid, but no external judge implementation is "
        "registered; no credential was read and no provider client was created"
    )


def _required_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is required for paid calibration")
    return value


def _allowlist(name: str) -> frozenset[str]:
    values = frozenset(item.strip() for item in _required_value(name).split(",") if item.strip())
    if not values:
        pytest.skip(f"{name} must contain at least one exact value")
    return values


def _positive_integer(name: str) -> int:
    raw = _required_value(name)
    if not raw.isascii() or not raw.isdecimal():
        pytest.skip(f"{name} must be an exact positive integer")
    value = int(raw)
    if value <= 0:
        pytest.skip(f"{name} must be an exact positive integer")
    return value
