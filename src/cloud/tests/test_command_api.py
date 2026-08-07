from fastapi.testclient import TestClient

from app.main import app


def test_command_lease_requires_device_auth(monkeypatch) -> None:
    _set_environment(monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/v1/devices/device-test/commands:lease",
            json={"app_session_id": "session-1", "wait_seconds": 20, "max_commands": 1},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def _set_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
    monkeypatch.setenv("PUBSUB_PUSH_AUDIENCE", "https://worker.example.test")
    monkeypatch.setenv("PUBSUB_INVOKER_EMAIL", "pubsub-invoker@example.test")
