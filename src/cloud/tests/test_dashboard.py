from datetime import UTC, datetime
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import firestore_client
from app.main import app


def test_console_incident_list_requires_operator_session_and_renders_fields(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    snapshot = Mock(id="incident-1")
    snapshot.to_dict.return_value = {
        "state": "MITIGATED",
        "summary": "Binding burst <script>alert(1)</script>",
        "updated_at": datetime(2026, 8, 9, 1, 0, tzinfo=UTC),
    }
    client = Mock()
    client.collection.return_value.stream.return_value = [snapshot]
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        unauthorized = test_client.get("/console/incidents")
        login = test_client.post("/console/login", json={"token": "operator-secret"})
        response = test_client.get("/console/incidents")

    assert unauthorized.status_code == 401
    assert login.status_code == 204
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "incident-1" in response.text
    assert "MITIGATED" in response.text
    assert "Binding burst" in response.text
    assert "2026-08-09T01:00:00+00:00" in response.text
    assert "<script>" not in response.text
    assert client.collection.call_args.args == (firestore_client.INCIDENTS_COLLECTION,)


def test_console_incident_detail_renders_incident_and_returns_404_when_missing(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    snapshot = Mock(exists=True)
    snapshot.to_dict.return_value = {
        "state": "INVESTIGATING",
        "summary": "Binding failures",
        "updated_at": datetime(2026, 8, 9, 1, 5, tzinfo=UTC),
    }
    client = Mock()
    incident_document = client.collection.return_value.document.return_value
    incident_document.get.return_value = snapshot
    incident_document.collection.return_value.stream.return_value = []
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-2")
        snapshot.exists = False
        missing = test_client.get("/console/incidents/missing")

    assert response.status_code == 200
    assert "incident-2" in response.text
    assert "INVESTIGATING" in response.text
    assert "Binding failures" in response.text
    assert "2026-08-09T01:05:00+00:00" in response.text
    assert missing.status_code == 404


def test_console_incident_detail_sorts_timeline_by_sequence_and_time(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    incident = Mock(exists=True)
    incident.to_dict.return_value = {"state": "MITIGATED", "summary": "Recovered"}
    audit = [
        _snapshot({"sequence": 3, "timestamp_utc": "2026-08-09T01:03:00Z", "type": "mutation.verification"}),
        _snapshot({"sequence": 1, "timestamp_utc": "2026-08-09T01:01:00Z", "type": "state.transition"}),
        _snapshot({"sequence": 2, "timestamp_utc": "2026-08-09T01:02:00Z", "type": "tool.request"}),
    ]
    client = Mock()
    incident_document = client.collection.return_value.document.return_value
    incident_document.get.return_value = incident
    incident_document.collection.return_value.stream.return_value = audit
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-3")

    assert response.status_code == 200
    first = response.text.index("state.transition")
    second = response.text.index("tool.request")
    third = response.text.index("mutation.verification")
    assert first < second < third
    assert response.text.index("2026-08-09T01:01:00Z") < response.text.index("2026-08-09T01:03:00Z")


def test_console_incident_detail_renders_safe_evidence_index_without_raw_secrets(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    incident = Mock(exists=True)
    incident.to_dict.return_value = {"state": "INVESTIGATING", "summary": "Exception burst"}
    evidence = _snapshot(
        {
            "event_type": "exception.summary",
            "evidence_hash": "a" * 64,
            "payload": {"message_template": "Authorization: Bearer private-secret"},
            "result": {"raw": "private-result-secret"},
        }
    )
    evidence.id = "evidence-1"
    client = Mock()
    incident_document = client.collection.return_value.document.return_value
    incident_document.get.return_value = incident
    audit_collection = Mock()
    audit_collection.stream.return_value = []
    evidence_collection = Mock()
    evidence_collection.stream.return_value = [evidence]
    incident_document.collection.side_effect = lambda name: {
        firestore_client.AUDIT_COLLECTION: audit_collection,
        firestore_client.EVIDENCE_COLLECTION: evidence_collection,
    }[name]
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-4")

    assert response.status_code == 200
    assert "Evidence" in response.text
    assert "evidence-1" in response.text
    assert "exception.summary" in response.text
    assert "a" * 64 in response.text
    assert "private-secret" not in response.text
    assert "private-result-secret" not in response.text


def _snapshot(data: dict[str, object]) -> Mock:
    snapshot = Mock()
    snapshot.to_dict.return_value = data
    return snapshot


def _set_api_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "device-secret")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "operator-secret")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
