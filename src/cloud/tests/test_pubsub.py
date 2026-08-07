import io
import json

from fastapi.testclient import TestClient

from app import main
from app import pubsub
from app.logging_config import configure_logging


def test_publisher_client_provider_reuses_client(monkeypatch) -> None:
    created: list[object] = []

    def create_client() -> object:
        client = object()
        created.append(client)
        return client

    monkeypatch.setattr(pubsub.pubsub_v1, "PublisherClient", create_client)
    pubsub.get_publisher_client.cache_clear()
    try:
        first = pubsub.get_publisher_client()
        second = pubsub.get_publisher_client()
    finally:
        pubsub.get_publisher_client.cache_clear()

    assert first is second
    assert len(created) == 1


def test_publish_work_sends_only_minimal_data_and_string_attributes(monkeypatch) -> None:
    calls: list[tuple[str, bytes, dict[str, str]]] = []

    class Future:
        def result(self, *, timeout: int) -> str:
            assert timeout == 10
            return "message-1"

    class Publisher:
        def topic_path(self, project_id: str, topic_name: str) -> str:
            return f"projects/{project_id}/topics/{topic_name}"

        def publish(self, topic_path: str, data: bytes, **attributes: str) -> Future:
            calls.append((topic_path, data, attributes))
            return Future()

    monkeypatch.setattr(pubsub, "get_publisher_client", lambda: Publisher())
    message_id = pubsub.publish_work(
        "project-test",
        "incident-work",
        {
            "incident_id": "incident-1",
            "evidence_revision": 2,
            "trigger": "binding.aggregate",
            "event_id": "event-1",
            "raw_evidence": {"message": "must-not-publish"},
        },
    )

    assert message_id == "message-1"
    assert len(calls) == 1
    topic_path, data, attributes = calls[0]
    assert topic_path == "projects/project-test/topics/incident-work"
    assert json.loads(data) == {
        "incident_id": "incident-1",
        "evidence_revision": 2,
        "trigger": "binding.aggregate",
        "event_id": "event-1",
    }
    assert attributes == {
        "incident_id": "incident-1",
        "evidence_revision": "2",
        "trigger": "binding.aggregate",
        "event_id": "event-1",
    }


def test_telemetry_publishes_work_after_durable_ingest(monkeypatch) -> None:
    _set_required_environment(monkeypatch)
    order: list[object] = []
    payload = {
        "incident_id": "incident-1",
        "evidence_revision": 2,
        "trigger": "binding.aggregate",
        "event_id": "event-1",
    }

    def ingest(*_args) -> tuple[bool, str, dict[str, object]]:
        order.append("commit")
        return True, "incident-1", payload

    def publish(project_id: str, topic_name: str, work: dict[str, object]) -> None:
        order.append(("publish", project_id, topic_name, work))

    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: object())
    monkeypatch.setattr(main, "ingest_binding_event", ingest)
    monkeypatch.setattr(main, "publish_work", publish, raising=False)

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={"events": [_binding_event()]},
        )

    assert response.status_code == 200
    assert order == ["commit", ("publish", "project-test", "incident-work", payload)]


def test_publish_failure_keeps_ingest_accepted_and_logs_recovery_identifiers(monkeypatch) -> None:
    _set_required_environment(monkeypatch)
    output = io.StringIO()
    payload = {
        "incident_id": "incident-1",
        "evidence_revision": 2,
        "trigger": "binding.aggregate",
        "event_id": "event-1",
    }
    monkeypatch.setattr(main, "configure_logging", lambda role: configure_logging(role, output))
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: object())
    monkeypatch.setattr(
        main,
        "ingest_binding_event",
        lambda *_args: (True, "incident-1", payload),
    )

    def fail_publish(*_args) -> None:
        raise RuntimeError("publisher unavailable")

    monkeypatch.setattr(main, "publish_work", fail_publish, raising=False)

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={"events": [_binding_event()]},
        )

    log = output.getvalue()
    assert response.status_code == 200
    assert response.json()["accepted_event_ids"] == ["event-1"]
    assert "pubsub_publish_failed" in log
    assert "incident-1" in log
    assert "event-1" in log
    assert "evidence_revision=2" in log


def _set_required_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")


def _binding_event() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": "event-1",
        "event_type": "binding.aggregate",
        "severity": "ERROR",
        "timestamp_utc": "2026-08-07T00:00:00Z",
        "device_id": "device-test",
        "application_id": "demo-broken-wpf-app",
        "application_version": "0.1.0",
        "app_session_id": "session-test",
        "sequence_no": 1,
        "correlation": {"binding_path": "DisplayNmae"},
        "payload": {"fingerprint": "binding-1", "occurrence_count": 1},
        "redaction_profile": "default-v1",
        "evidence_hash": "1" * 64,
    }
