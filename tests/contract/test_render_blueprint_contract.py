from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[2]


def _environment(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["key"]): item
        for item in service.get("envVars", [])
        if isinstance(item, dict) and "key" in item
    }


def test_render_blueprint_defines_the_minimum_hosted_pilot_topology() -> None:
    blueprint_path = ROOT / "render.yaml"
    document = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))

    assert isinstance(document, dict)
    services = {service["name"]: service for service in document["services"]}
    assert set(services) == {
        "evalforge-pilot-api",
        "evalforge-pilot-dashboard",
        "evalforge-pilot-worker",
    }
    assert [service["type"] for service in services.values()] == ["web", "web", "worker"]
    assert all(service["runtime"] == "docker" for service in services.values())
    assert all(service["autoDeployTrigger"] == "checksPass" for service in services.values())

    api = services["evalforge-pilot-api"]
    assert api["dockerfilePath"] == "./Dockerfile.api"
    assert api["healthCheckPath"] == "/health/ready"
    assert api["preDeployCommand"] == "evalforge migrate"
    api_environment = _environment(api)
    assert api_environment["EVALFORGE_DATABASE_URL"]["fromDatabase"] == {
        "name": "evalforge-pilot-postgres",
        "property": "connectionString",
    }
    assert api_environment["EVALFORGE_EXECUTOR_MODE"]["value"] == "api_only"
    assert api_environment["EVALFORGE_AUTO_MIGRATE"]["value"] == "false"
    assert api_environment["EVALFORGE_COMMERCIAL_PILOT_ENABLED"]["value"] == "true"
    for key in (
        "EVALFORGE_API_URL",
        "EVALFORGE_PUBLIC_BASE_URL",
        "EVALFORGE_CORS_ORIGINS",
        "EVALFORGE_TRUSTED_HOSTS",
        "EVALFORGE_OIDC_ISSUER",
        "EVALFORGE_OIDC_AUDIENCE",
        "EVALFORGE_OIDC_JWKS_URL",
        "EVALFORGE_METRICS_BEARER_TOKEN",
    ):
        assert api_environment[key]["sync"] is False

    dashboard = services["evalforge-pilot-dashboard"]
    assert dashboard["dockerfilePath"] == "./Dockerfile.dashboard"
    assert dashboard["healthCheckPath"] == "/_stcore/health"
    dashboard_environment = _environment(dashboard)
    for key in (
        "EVALFORGE_DASHBOARD_OIDC_CLIENT_ID",
        "EVALFORGE_DASHBOARD_OIDC_CLIENT_SECRET",
        "EVALFORGE_DASHBOARD_OIDC_SERVER_METADATA_URL",
    ):
        assert dashboard_environment[key]["sync"] is False
    assert dashboard_environment["EVALFORGE_DASHBOARD_OIDC_COOKIE_SECRET"]["generateValue"] is True
    assert "EVALFORGE_METRICS_BEARER_TOKEN" not in dashboard_environment

    worker = services["evalforge-pilot-worker"]
    assert worker["dockerCommand"] == "evalforge worker"
    worker_environment = _environment(worker)
    assert worker_environment["EVALFORGE_EXECUTOR_MODE"]["value"] == "database_worker"
    assert worker_environment["EVALFORGE_AUTO_MIGRATE"]["value"] == "false"
    assert worker_environment["EVALFORGE_DATABASE_URL"]["fromDatabase"] == {
        "name": "evalforge-pilot-postgres",
        "property": "connectionString",
    }

    assert document["databases"] == [
        {
            "name": "evalforge-pilot-postgres",
            "plan": "basic-256mb",
            "region": "oregon",
            "postgresMajorVersion": "17",
            "databaseName": "evalforge",
            "user": "evalforge",
            "ipAllowList": [],
        }
    ]
    assert "stripe" not in blueprint_path.read_text(encoding="utf-8").casefold()
