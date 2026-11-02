from scripts.check_interface_registry import HttpEndpoint, _validate_http


def test_http_id_normalizes_handler_underscores_to_hyphens() -> None:
    endpoint = HttpEndpoint(
        method="GET",
        path="/api/v1/active-target",
        handler="apps.api.routes.active_target.get_active_target",
        file="apps/api/routes/active_target.py",
        response_contract=(
            "stock_assistant.contracts.api.ApiResponse"
            "[stock_assistant.contracts.active_targets.ActiveTargetState]"
        ),
    )
    item: dict[str, object] = {
        "id": "http:active-targets.active-target.get-active-target:v1",
        "version": "1.0",
        "method": "GET",
        "path": "/api/v1/active-target",
        "handler": endpoint.handler,
        "file": endpoint.file,
        "response_contract": endpoint.response_contract,
        "status": "implemented",
        "owner": "active-targets",
        "compatibility": "major-versioned",
    }

    assert _validate_http([item], {("GET", "/api/v1/active-target"): endpoint}) == []
