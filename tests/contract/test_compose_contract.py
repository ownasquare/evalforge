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


def test_api_image_includes_postgresql_runtime_and_public_host_healthcheck() -> None:
    dockerfile = (ROOT / "Dockerfile.api").read_text()

    assert "--extra postgres" in dockerfile
    assert "EVALFORGE_PUBLIC_BASE_URL" in dockerfile
    assert "headers={'Host': host}" in dockerfile


def test_only_api_container_receives_provider_configuration() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    dashboard = compose.split("  dashboard:", maxsplit=1)[1]
    assert "EVALFORGE_API_URL: ${EVALFORGE_PUBLIC_BASE_URL:?" in dashboard
    assert "EVALFORGE_API_URL: http://" not in dashboard
    assert "EVALFORGE_OPENAI_API_KEY" not in dashboard


def test_compose_requires_oidc_before_using_routable_container_bindings() -> None:
    compose = (ROOT / "compose.yaml").read_text()

    assert "EVALFORGE_AUTH_MODE: oidc" in compose
    for variable in (
        "EVALFORGE_OIDC_ISSUER",
        "EVALFORGE_OIDC_AUDIENCE",
        "EVALFORGE_OIDC_JWKS_URL",
        "EVALFORGE_PUBLIC_BASE_URL",
        "EVALFORGE_TRUSTED_HOSTS",
    ):
        assert f"${{{variable}:?" in compose
    assert "EVALFORGE_API_HOST: 0.0.0.0" in compose
    assert "EVALFORGE_DASHBOARD_HOST: 0.0.0.0" in compose


def test_compose_mounts_a_required_streamlit_oidc_secret() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    dashboard = compose.split("  dashboard:", maxsplit=1)[1].split("volumes:", maxsplit=1)[0]

    assert "EVALFORGE_STREAMLIT_AUTH_FILE: /run/secrets/evalforge_streamlit_auth.toml" in dashboard
    assert "source: streamlit_auth" in dashboard
    assert "target: evalforge_streamlit_auth.toml" in dashboard
    assert "EVALFORGE_STREAMLIT_AUTH_SOURCE_FILE:?" in compose


def test_container_entry_points_delegate_bindings_to_validated_settings() -> None:
    api_launcher = (ROOT / "scripts" / "start_api.py").read_text()
    dashboard_launcher = (ROOT / "scripts" / "start_dashboard.py").read_text()
    dashboard_dockerfile = (ROOT / "Dockerfile.dashboard").read_text()

    assert "host=settings.api_host" in api_launcher
    assert "port=settings.api_port" in api_launcher
    assert "apply_migrations" not in api_launcher
    assert "settings.dashboard_host" in dashboard_launcher
    assert "settings.dashboard_port" in dashboard_launcher
    assert "--secrets.files=" in dashboard_launcher
    assert 'CMD ["python", "scripts/start_dashboard.py"]' in dashboard_dockerfile
    assert "--server.address=0.0.0.0" not in dashboard_dockerfile
