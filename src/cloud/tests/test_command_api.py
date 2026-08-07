from fastapi.testclient import TestClient
from pathlib import Path

from app import main
from app.main import app


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_command_lease_requires_device_auth(monkeypatch) -> None:
    _set_environment(monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/v1/devices/device-test/commands:lease",
            json={"app_session_id": "session-1", "wait_seconds": 20, "max_commands": 1},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_command_lease_returns_204_when_no_pending_command(monkeypatch) -> None:
    _set_environment(monkeypatch)
    firestore_client = object()
    leased: list[tuple[object, str, str]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)

    def lease(client, *, app_session_id, lease_owner, now, duration):
        leased.append((client, app_session_id, lease_owner))
        return None

    monkeypatch.setattr(main, "lease_next_command", lease, raising=False)

    with TestClient(app) as client:
        response = client.post(
            "/v1/devices/device-test/commands:lease",
            headers={"Authorization": "Bearer secret-token"},
            json={"app_session_id": "session-1", "wait_seconds": 20, "max_commands": 1},
        )

    assert response.status_code == 204
    assert leased == [(firestore_client, "session-1", "device-test")]


def test_command_complete_requires_device_auth(monkeypatch) -> None:
    _set_environment(monkeypatch)
    result = (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")

    with TestClient(app) as client:
        response = client.post(
            "/v1/commands/command-read-1:complete",
            content=result,
            headers={"Content-Type": "application/json"},
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
