from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_compose_is_loopback_only_and_dashboard_has_no_data_volume() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    assert '"127.0.0.1:${EVALFORGE_API_PORT:-8000}:8000"' in compose
    assert '"127.0.0.1:${EVALFORGE_DASHBOARD_PORT:-8501}:8501"' in compose
    dashboard = compose.split("  dashboard:", maxsplit=1)[1].split("volumes:", maxsplit=1)[0]
    assert "/data" not in dashboard
    assert "OPENAI_API_KEY" not in dashboard
    assert "COMPATIBLE_API_KEY" not in dashboard


def test_containers_use_non_root_runtime_and_health_checks() -> None:
    for name in ("Dockerfile.api", "Dockerfile.dashboard"):
        dockerfile = (ROOT / name).read_text()
        assert "USER evalforge" in dockerfile
        assert "HEALTHCHECK" in dockerfile
        assert "python:3.11.9-slim-bookworm" in dockerfile


def test_only_api_container_receives_provider_configuration() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    dashboard = compose.split("  dashboard:", maxsplit=1)[1]
    assert "EVALFORGE_API_URL: http://api:8000" in dashboard
    assert "EVALFORGE_OPENAI_API_KEY" not in dashboard
