from fastapi.testclient import TestClient
from pathlib import Path

from app import main
from app.main import app
from app.models import DiagnosticCommand


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
            json={"app_session_id": "session-1", "wait_seconds": 0, "max_commands": 1},
        )

    assert response.status_code == 204
    assert leased == [(firestore_client, "session-1", "device-test")]


def test_command_lease_waits_for_pending_command(monkeypatch) -> None:
    _set_environment(monkeypatch)
    firestore_client = object()
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    command = DiagnosticCommand.model_validate_json(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    attempts = 0

    def lease(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return command if attempts == 3 else None

    sleeps: list[float] = []
    monkeypatch.setattr(main, "lease_next_command", lease, raising=False)
    monkeypatch.setattr(main, "sleep", sleeps.append, raising=False)

    with TestClient(app) as client:
        response = client.post(
            "/v1/devices/device-test/commands:lease",
            headers={"Authorization": "Bearer secret-token"},
            json={"app_session_id": "session-1", "wait_seconds": 2, "max_commands": 1},
        )

    assert response.status_code == 200
    assert response.json()["command_id"] == command.command_id
    assert attempts == 3
    assert sleeps == [1, 1]


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


def test_command_complete_reports_idempotent_replay(monkeypatch) -> None:
    _set_environment(monkeypatch)
    firestore_client = object()
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    completed: list[tuple[object, str, str]] = []

    def complete(client, *, command_id, lease_owner, result):
        completed.append((client, command_id, lease_owner))
        return True, 6

    monkeypatch.setattr(main, "complete_command_once", complete, raising=False)
    published: list[dict[str, object]] = []
    monkeypatch.setattr(
        main,
        "_publish_after_commit",
        lambda _request, payload: published.append(payload),
    )
    result = (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")

    with TestClient(app) as client:
        response = client.post(
            "/v1/commands/command-read-1:complete",
            content=result,
            headers={
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "idempotent": True}
    assert completed == [(firestore_client, "command-read-1", "device-test")]
    assert published == [
        {
            "incident_id": "incident-1",
            "evidence_revision": 6,
            "trigger": "TOOL_RESULT_RECEIVED",
            "event_id": "command-read-1",
        }
    ]


def test_command_result_publish_occurs_after_completion_transaction(monkeypatch) -> None:
    _set_environment(monkeypatch)
    firestore_client = object()
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    order: list[str] = []

    def complete(_client, *, command_id, lease_owner, result):
        order.append("commit")
        return False, 7

    def publish(_request, payload):
        order.append("publish")
        assert payload["evidence_revision"] == 7
        assert payload["trigger"] == "TOOL_RESULT_RECEIVED"

    monkeypatch.setattr(main, "complete_command_once", complete, raising=False)
    monkeypatch.setattr(main, "_publish_after_commit", publish)
    result = (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")

    with TestClient(app) as client:
        response = client.post(
            "/v1/commands/command-read-1:complete",
            content=result,
            headers={
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 200
    assert order == ["commit", "publish"]


def _set_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
    monkeypatch.setenv("PUBSUB_PUSH_AUDIENCE", "https://worker.example.test")
    monkeypatch.setenv("PUBSUB_INVOKER_EMAIL", "pubsub-invoker@example.test")
