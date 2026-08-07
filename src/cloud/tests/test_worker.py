from fastapi.testclient import TestClient

from app.main import app


def test_worker_push_route_is_available_only_to_worker_role(monkeypatch) -> None:
    _set_environment(monkeypatch, "api")
    with TestClient(app) as client:
        api_response = client.post("/v1/work:push")

    _set_environment(monkeypatch, "worker")
    with TestClient(app) as client:
        worker_response = client.post("/v1/work:push")

    assert api_response.status_code == 404
    assert worker_response.status_code == 204


def _set_environment(monkeypatch, role: str) -> None:
    monkeypatch.setenv("SERVICE_ROLE", role)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
