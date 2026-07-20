from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from evalforge import cli
from evalforge.config import Settings


class _DisposableEngine:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def dispose(self) -> None:
        self._events.append("dispose")


def test_migrate_applies_schema_and_reports_only_safe_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    settings = Settings(environment="test")
    engine = _DisposableEngine(events)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "apply_migrations", lambda value: events.append("migrate"))
    monkeypatch.setattr(cli, "create_database_engine", lambda value: engine)
    monkeypatch.setattr(
        cli,
        "check_database_readiness",
        lambda value: events.append("readiness") or True,
    )

    result = CliRunner().invoke(cli.app, ["migrate"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "status": "ready",
        "database_backend": "sqlite",
    }
    assert events == ["migrate", "readiness", "dispose"]
    assert "database_url" not in result.output


def test_migrate_fails_closed_without_exposing_the_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="test")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def fail(_: Settings) -> None:
        raise RuntimeError("postgresql://user:secret@example.invalid/database")

    monkeypatch.setattr(cli, "apply_migrations", fail)

    result = CliRunner().invoke(cli.app, ["migrate"])

    assert result.exit_code == 1
    assert result.output == "EvalForge database migration failed.\n"
    assert "secret" not in result.output
