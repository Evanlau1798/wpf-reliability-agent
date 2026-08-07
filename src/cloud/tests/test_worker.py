import base64
import io
import json

from fastapi.testclient import TestClient

from app import main
from app import worker
from app import worker_auth
from app.logging_config import configure_logging
from app.main import app


def test_worker_push_route_is_available_only_to_worker_role(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_auth.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {
            "email": "pubsub-invoker@example.test",
            "email_verified": True,
        },
    )
    _set_environment(monkeypatch, "api")
    with TestClient(app) as client:
        api_response = client.post("/v1/work:push")

    _set_environment(monkeypatch, "worker")
    with TestClient(app) as client:
        worker_response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
        )

    assert api_response.status_code == 404
    assert worker_response.status_code == 204


def test_worker_push_requires_authenticated_pubsub_identity(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")

    with TestClient(app) as client:
        response = client.post("/v1/work:push")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_worker_push_verifies_oidc_audience_and_invoker_email(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")
    calls: list[tuple[str, str]] = []

    def verify(token, _request, audience):
        calls.append((token, audience))
        return {"email": "pubsub-invoker@example.test", "email_verified": True}

    monkeypatch.setattr(worker_auth.id_token, "verify_oauth2_token", verify)

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
        )

    assert response.status_code == 204
    assert calls == [("signed-token", "https://worker.example.test")]


def test_worker_push_rejects_wrong_invoker_identity(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")
    monkeypatch.setattr(
        worker_auth.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {"email": "other@example.test", "email_verified": True},
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
        )

    assert response.status_code == 401


def test_worker_push_rejects_invalid_oidc_token(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")

    def reject_token(*_args, **_kwargs):
        raise ValueError("invalid token")

    monkeypatch.setattr(worker_auth.id_token, "verify_oauth2_token", reject_token)

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer invalid-token"},
        )

    assert response.status_code == 401


def test_decode_pubsub_envelope_returns_minimal_work_message() -> None:
    work = {
        "incident_id": "incident-1",
        "evidence_revision": 2,
        "trigger": "binding.aggregate",
        "event_id": "event-1",
    }
    data = base64.b64encode(json.dumps(work).encode("utf-8")).decode("ascii")

    assert worker.decode_pubsub_envelope(
        {"message": {"messageId": "message-1", "data": data}}
    ) == work


def test_run_key_is_stable_for_incident_revision_and_trigger() -> None:
    first = worker.build_run_key("incident-1", 2, "binding.aggregate")
    second = worker.build_run_key("incident-1", 2, "binding.aggregate")

    assert first == second == "incident-1:2:binding.aggregate"


def test_malformed_pubsub_message_is_audited_and_acked(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")
    _allow_identity(monkeypatch)
    output = io.StringIO()
    monkeypatch.setattr(main, "configure_logging", lambda role: configure_logging(role, output))

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json={"message": {"messageId": "message-bad", "data": "not-base64!"}},
        )

    assert response.status_code == 204
    log = output.getvalue()
    assert "worker_message_rejected" in log
    assert "message-bad" in log
    assert "not-base64!" not in log


def _set_environment(monkeypatch, role: str) -> None:
    monkeypatch.setenv("SERVICE_ROLE", role)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
    monkeypatch.setenv("PUBSUB_PUSH_AUDIENCE", "https://worker.example.test")
    monkeypatch.setenv("PUBSUB_INVOKER_EMAIL", "pubsub-invoker@example.test")


def _allow_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_auth.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {
            "email": "pubsub-invoker@example.test",
            "email_verified": True,
        },
    )
