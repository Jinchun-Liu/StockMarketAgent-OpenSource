from apps.api.main import app
from fastapi.testclient import TestClient


def test_main_mounts_r1_reads_and_keeps_active_target_mutation_disabled() -> None:
    with TestClient(app) as client:
        active_target_response = client.get("/api/v1/active-target")
        briefing_response = client.get("/api/v1/briefings/premarket/latest")
        put_response = client.put("/api/v1/active-target", json={})

    assert active_target_response.status_code == 503
    assert active_target_response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert briefing_response.status_code == 503
    assert briefing_response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert put_response.status_code == 405
    assert "/api/v1/today/stream" in app.openapi()["paths"]
