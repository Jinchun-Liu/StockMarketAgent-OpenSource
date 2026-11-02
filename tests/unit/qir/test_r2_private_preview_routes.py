"""Private-preview routing must expose only the owner-scoped research API."""

from typing import cast

from apps.api.main import _framework_endpoint_urls, _mount_routers
from fastapi import FastAPI


def _paths(application: FastAPI) -> set[str]:
    return set(cast(dict[str, object], application.openapi()["paths"]))


def _route_paths(application: FastAPI) -> set[str]:
    return {
        path
        for route in application.routes
        if isinstance(path := getattr(route, "path", None), str)
    }


def test_private_preview_does_not_mount_legacy_research_routes() -> None:
    application = FastAPI()

    _mount_routers(application, private_preview=True)

    paths = _paths(application)
    assert "/api/v2/research-sessions" in paths
    assert "/api/v2/instruments/directory-search" in paths
    assert "/api/v2/research-sessions/{session_id}/report" not in paths
    assert "/api/v2/research-sessions/{session_id}/report-v1.1" in paths
    assert "/api/v2/research-sessions/{session_id}/report-v1.2" in paths
    assert {path for path in paths if path.startswith("/api/v1/")} == {
        "/api/v1/health/live",
        "/api/v1/health/ready",
    }


def test_local_compatibility_keeps_legacy_research_routes() -> None:
    application = FastAPI()

    _mount_routers(application, private_preview=False)

    paths = _paths(application)
    assert "/api/v1/instruments/search" in paths
    assert "/api/v1/research-sessions" in paths
    assert "/api/v2/research-sessions" in paths
    assert "/api/v2/research-sessions/{session_id}/report" in paths


def test_private_preview_disables_framework_documentation_routes() -> None:
    docs_url, openapi_url = _framework_endpoint_urls(
        private_preview=True,
        app_env="preview",
    )
    application = FastAPI(
        docs_url=docs_url,
        openapi_url=openapi_url,
        redoc_url=None,
    )

    _mount_routers(application, private_preview=True)

    paths = _route_paths(application)
    assert "/docs" not in paths
    assert "/openapi.json" not in paths
    assert "/redoc" not in paths
