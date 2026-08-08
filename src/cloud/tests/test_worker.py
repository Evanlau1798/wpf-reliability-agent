import base64
import io
import json
from unittest.mock import Mock

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


def test_duplicate_work_delivery_checks_processed_run_and_noops(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")
    _allow_identity(monkeypatch)
    firestore_client = object()
    checked: list[tuple[object, str]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)

    def is_processed(client, run_key):
        checked.append((client, run_key))
        return True

    monkeypatch.setattr(main, "is_run_processed", is_processed, raising=False)

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(),
        )

    assert response.status_code == 204
    assert checked == [(firestore_client, "incident-1:2:binding.aggregate")]


def test_new_work_commits_durable_step_before_ack(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")
    _allow_identity(monkeypatch)
    firestore_client = object()
    committed: list[tuple[object, str, str, int, str, str]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)

    def commit(client, *, run_key, incident_id, evidence_revision, trigger, model_id):
        committed.append((client, run_key, incident_id, evidence_revision, trigger, model_id))
        return True

    monkeypatch.setattr(main, "commit_new_incident_run", commit, raising=False)

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(),
        )

    assert response.status_code == 204
    assert committed == [
        (
            firestore_client,
            "incident-1:2:binding.aggregate",
            "incident-1",
            2,
            "binding.aggregate",
            "gemini-3.5-flash-lite",
        )
    ]


def test_recovery_work_uses_deterministic_verification_path(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")
    _allow_identity(monkeypatch)
    firestore_client = object()
    verification = Mock(
        binding=Mock(),
        performance=Mock(),
        visual=Mock(),
        command_id="command-1",
        action_id="action-1",
    )
    committed: list[dict[str, object]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(main, "load_incident_evidence", lambda *_args: [{"evidence_id": "post-1"}])
    monkeypatch.setattr(main, "evaluate_post_action_verification", lambda *_args: verification)
    monkeypatch.setattr(main, "meets_mitigation_thresholds", lambda *_args: True)
    monkeypatch.setattr(main, "build_verification_audit", lambda *_args: {"outcome": "MITIGATED"})
    monkeypatch.setattr(main, "commit_new_incident_run", Mock(side_effect=AssertionError("wrong path")))

    def commit(*_args, **kwargs):
        committed.append(kwargs)
        return True

    monkeypatch.setattr(main, "commit_verification_run", commit, raising=False)

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(trigger="recovery.result", evidence_revision=7, event_id="post-1"),
        )

    assert response.status_code == 204
    assert committed == [
        {
            "run_key": "incident-1:7:recovery.result",
            "incident_id": "incident-1",
            "evidence_revision": 7,
            "command_id": "command-1",
            "action_id": "action-1",
            "target_state": main.IncidentState.MITIGATED,
            "verification": {"outcome": "MITIGATED"},
        }
    ]


def test_recovery_work_returns_inconclusive_evidence_to_investigating(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")
    _allow_identity(monkeypatch)
    firestore_client = object()
    committed: list[dict[str, object]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(main, "load_incident_evidence", lambda *_args: [{"evidence_id": "post-1"}])
    monkeypatch.setattr(main, "evaluate_post_action_verification", lambda *_args: None)
    monkeypatch.setattr(
        main,
        "recovery_evidence_binding",
        lambda *_args: ("command-1", "action-1"),
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "build_inconclusive_verification_audit",
        lambda *_args: {"outcome": "INCONCLUSIVE"},
        raising=False,
    )

    def commit(*_args, **kwargs):
        committed.append(kwargs)
        return True

    monkeypatch.setattr(main, "commit_verification_run", commit)

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(trigger="recovery.result", evidence_revision=7, event_id="post-1"),
        )

    assert response.status_code == 204
    assert committed[0]["target_state"] is main.IncidentState.INVESTIGATING
    assert committed[0]["verification"] == {"outcome": "INCONCLUSIVE"}


def test_recovery_regression_enters_failed_safe_with_rollback_guidance(monkeypatch) -> None:
    _set_environment(monkeypatch, "worker")
    _allow_identity(monkeypatch)
    firestore_client = object()
    verification = Mock(
        binding=Mock(),
        performance=Mock(),
        visual=Mock(),
        command_id="command-1",
        action_id="action-1",
    )
    committed: list[dict[str, object]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(main, "load_incident_evidence", lambda *_args: [{"evidence_id": "post-1"}])
    monkeypatch.setattr(main, "evaluate_post_action_verification", lambda *_args: verification)
    monkeypatch.setattr(main, "meets_mitigation_thresholds", lambda *_args: False)
    monkeypatch.setattr(main, "is_regression", lambda *_args: True, raising=False)
    monkeypatch.setattr(
        main,
        "load_rollback_guidance",
        lambda *_args: "Re-enable the feature.",
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "build_regression_verification_audit",
        lambda *_args: {"outcome": "FAILED_SAFE", "rollback_guidance": "Re-enable the feature."},
        raising=False,
    )

    def commit(*_args, **kwargs):
        committed.append(kwargs)
        return True

    monkeypatch.setattr(main, "commit_verification_run", commit)

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(trigger="recovery.result", evidence_revision=7, event_id="post-1"),
        )

    assert response.status_code == 204
    assert committed[0]["target_state"] is main.IncidentState.FAILED_SAFE
    assert committed[0]["verification"]["rollback_guidance"] == "Re-enable the feature."


def test_rollback_guidance_is_loaded_from_the_command_approval() -> None:
    client = Mock()
    command_collection = Mock()
    incident_collection = Mock()
    command_document = Mock()
    incident_document = Mock()
    approval_document = Mock()
    client.collection.side_effect = lambda name: {
        main.firestore_client.COMMANDS_COLLECTION: command_collection,
        main.firestore_client.INCIDENTS_COLLECTION: incident_collection,
    }[name]
    command_collection.document.return_value = command_document
    incident_collection.document.return_value = incident_document
    incident_document.collection.return_value.document.return_value = approval_document
    command_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"approval_id": "approval-1"},
    )
    approval_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"rollback_plan": "Re-enable the feature."},
    )

    assert main.load_rollback_guidance(client, "incident-1", "command-1") == "Re-enable the feature."
    incident_document.collection.assert_called_once_with(main.firestore_client.APPROVALS_COLLECTION)


def _set_environment(monkeypatch, role: str) -> None:
    monkeypatch.setenv("SERVICE_ROLE", role)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
    monkeypatch.setenv("PUBSUB_PUSH_AUDIENCE", "https://worker.example.test")
    monkeypatch.setenv("PUBSUB_INVOKER_EMAIL", "pubsub-invoker@example.test")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-east1")


def _allow_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_auth.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {
            "email": "pubsub-invoker@example.test",
            "email_verified": True,
        },
    )


def _push_envelope(
    *,
    trigger: str = "binding.aggregate",
    evidence_revision: int = 2,
    event_id: str = "event-1",
) -> dict[str, object]:
    work = {
        "incident_id": "incident-1",
        "evidence_revision": evidence_revision,
        "trigger": trigger,
        "event_id": event_id,
    }
    return {
        "message": {
            "messageId": "message-1",
            "data": base64.b64encode(json.dumps(work).encode("utf-8")).decode("ascii"),
        }
    }
