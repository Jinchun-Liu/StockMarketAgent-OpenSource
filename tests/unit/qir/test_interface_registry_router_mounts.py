from pathlib import Path

from scripts.check_interface_registry import _discover_http


def test_http_discovery_supports_conditional_reuse_and_named_routers(tmp_path: Path) -> None:
    main_path = tmp_path / "apps/api/main.py"
    route_path = tmp_path / "apps/api/routes/sample.py"
    route_path.parent.mkdir(parents=True)
    main_path.write_text(
        """
from apps.api.routes.sample import legacy_router as sample_legacy_router
from apps.api.routes.sample import router as sample_router


def mount(application, *, preview):
    if preview:
        application.include_router(sample_router, prefix="/api/v2")
        return
    application.include_router(sample_router, prefix="/api/v2")
    application.include_router(sample_legacy_router, prefix="/api/v2")
""".strip(),
        encoding="utf-8",
    )
    route_path.write_text(
        """
router = APIRouter()
legacy_router = APIRouter()


@router.get("/items", response_model=ItemResponse)
async def list_items():
    pass


@legacy_router.get("/legacy", response_model=LegacyResponse)
async def get_legacy():
    pass
""".strip(),
        encoding="utf-8",
    )

    endpoints, errors = _discover_http(tmp_path)

    assert errors == []
    assert set(endpoints) == {
        ("GET", "/api/v2/items"),
        ("GET", "/api/v2/legacy"),
    }
