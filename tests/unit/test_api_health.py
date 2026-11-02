from uuid import uuid4

from apps.api.main import app
from fastapi.testclient import TestClient


def test_liveness() -> None:
    response = TestClient(app).get("/api/v1/health/live")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "StockMarketAgent"
    assert response.headers["X-Request-ID"]


def test_capabilities_never_advertise_trading() -> None:
    response = TestClient(app).get("/api/v1/meta/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["trading_enabled"] is False
    assert payload["meta"]["contract_version"] == "1.0"


def test_not_found_uses_problem_details_without_internal_text() -> None:
    response = TestClient(app).get("/api/v1/missing")
    payload = response.json()

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert payload["code"] == "NOT_FOUND"
    assert payload["request_id"] == response.headers["X-Request-ID"]


def test_request_id_is_preserved_across_response_envelope_and_header() -> None:
    request_id = str(uuid4())
    response = TestClient(app).get(
        "/api/v1/meta/capabilities", headers={"X-Request-ID": request_id}
    )

    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["meta"]["request_id"] == request_id
