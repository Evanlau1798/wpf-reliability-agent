from datetime import UTC, datetime
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import auth, firestore_client
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
    client.collection.return_value.where.return_value.stream.return_value = []
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
    client.collection.return_value.where.return_value.stream.return_value = []
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
        firestore_client.APPROVALS_COLLECTION: audit_collection,
        firestore_client.REPORTS_COLLECTION: audit_collection,
    }[name]
    client.collection.return_value.where.return_value.stream.return_value = []
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


def test_console_incident_detail_renders_hypotheses_with_evidence_and_confidence(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    incident = Mock(exists=True)
    incident.to_dict.return_value = {
        "state": "INVESTIGATING",
        "summary": "Binding failures",
        "current_hypotheses": [
            {
                "claim": "DisplayNmae is incorrect <script>alert(1)</script>",
                "confidence": "HIGH",
                "evidence_ids": ["binding-1", "ui-1"],
                "counter_evidence_ids": ["performance-1"],
            }
        ],
    }
    client = Mock()
    incident_document = client.collection.return_value.document.return_value
    incident_document.get.return_value = incident
    incident_document.collection.return_value.stream.return_value = []
    client.collection.return_value.where.return_value.stream.return_value = []
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-5")

    assert response.status_code == 200
    assert "Hypotheses" in response.text
    assert "DisplayNmae is incorrect" in response.text
    assert "HIGH" in response.text
    assert "binding-1, ui-1" in response.text
    assert "performance-1" in response.text
    assert "<script>" not in response.text


def test_console_incident_detail_renders_tool_ledger_with_hashed_args_and_duration(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    incident = Mock(exists=True)
    incident.to_dict.return_value = {"state": "INVESTIGATING", "summary": "Binding failures"}
    command = _snapshot(
        {
            "tool": "ui.get_subtree",
            "arguments": {"element_id": "private-element"},
            "arguments_hash": "b" * 64,
            "status": "COMPLETED",
            "completion_result": {
                "started_at_utc": "2026-08-09T01:00:00Z",
                "completed_at_utc": "2026-08-09T01:00:00.250000Z",
            },
        }
    )
    client = Mock()
    incident_collection = Mock()
    command_collection = Mock()
    client.collection.side_effect = lambda name: {
        firestore_client.INCIDENTS_COLLECTION: incident_collection,
        firestore_client.COMMANDS_COLLECTION: command_collection,
    }[name]
    incident_document = incident_collection.document.return_value
    incident_document.get.return_value = incident
    incident_document.collection.return_value.stream.return_value = []
    command_collection.where.return_value.stream.return_value = [command]
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-6")

    assert response.status_code == 200
    assert "Tool Ledger" in response.text
    assert "ui.get_subtree" in response.text
    assert "b" * 64 in response.text
    assert "COMPLETED" in response.text
    assert "250 ms" in response.text
    assert "private-element" not in response.text


def test_console_incident_detail_renders_exact_approval_card(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    incident = Mock(exists=True)
    incident.to_dict.return_value = {"state": "AWAITING_APPROVAL", "summary": "Rollback proposed"}
    approval = _snapshot(
        {
            "approval_id": "approval-1",
            "status": "PENDING",
            "action_id": "action-1",
            "tool": "recovery.set_feature_flag",
            "canonical_arguments": {"feature": "ExperimentalPeopleGrid", "enabled": False},
            "canonical_arguments_hash": "c" * 64,
            "evidence_snapshot_hash": "d" * 64,
            "rollback_plan": "Re-enable the feature <script>alert(1)</script>",
            "expires_at_utc": "2026-08-09T02:00:00Z",
        }
    )
    client = Mock()
    incident_document = client.collection.return_value.document.return_value
    incident_document.get.return_value = incident
    empty_collection = Mock()
    empty_collection.stream.return_value = []
    approval_collection = Mock()
    approval_collection.stream.return_value = [approval]
    incident_document.collection.side_effect = lambda name: (
        approval_collection if name == firestore_client.APPROVALS_COLLECTION else empty_collection
    )
    client.collection.return_value.where.return_value.stream.return_value = []
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-7")

    assert response.status_code == 200
    assert "Approval" in response.text
    assert "action-1" in response.text
    assert "recovery.set_feature_flag" in response.text
    assert '{&quot;enabled&quot;:false,&quot;feature&quot;:&quot;ExperimentalPeopleGrid&quot;}' in response.text
    assert "c" * 64 in response.text
    assert "d" * 64 in response.text
    assert "Re-enable the feature" in response.text
    assert "2026-08-09T02:00:00Z" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_console_pending_approval_controls_use_existing_csrf_contract(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    incident = Mock(exists=True)
    incident.to_dict.return_value = {"state": "AWAITING_APPROVAL", "summary": "Rollback proposed"}
    pending = _snapshot(
        {
            "approval_id": "approval-pending",
            "status": "PENDING",
            "action_id": "action-1",
            "tool": "recovery.set_feature_flag",
            "canonical_arguments": {"feature": "ExperimentalPeopleGrid", "enabled": False},
            "canonical_arguments_hash": "e" * 64,
            "evidence_snapshot_hash": "f" * 64,
            "rollback_plan": "Re-enable the feature.",
            "expires_at_utc": "2026-08-09T02:00:00Z",
        }
    )
    approved = _snapshot({**pending.to_dict(), "approval_id": "approval-approved", "status": "APPROVED"})
    client = Mock()
    incident_document = client.collection.return_value.document.return_value
    incident_document.get.return_value = incident
    empty_collection = Mock()
    empty_collection.stream.return_value = []
    approval_collection = Mock()
    approval_collection.stream.return_value = [pending, approved]
    incident_document.collection.side_effect = lambda name: (
        approval_collection if name == firestore_client.APPROVALS_COLLECTION else empty_collection
    )
    client.collection.return_value.where.return_value.stream.return_value = []
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-8")

    assert response.status_code == 200
    assert ">Approve</button>" in response.text
    assert ">Reject</button>" in response.text
    assert response.text.count('data-approval-id="approval-pending"') == 2
    assert 'data-approval-id="approval-approved"' not in response.text
    assert auth.OPERATOR_CSRF_COOKIE in response.text
    assert auth.OPERATOR_CSRF_HEADER in response.text
    assert "/v1/approvals/" in response.text
    assert ":decide" in response.text


def test_console_incident_detail_renders_before_after_verification_metrics(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    incident = Mock(exists=True)
    incident.to_dict.return_value = {"state": "MITIGATED", "summary": "Mitigation verified"}
    verification = _snapshot(
        {
            "sequence": 12,
            "timestamp_utc": "2026-08-09T01:12:00Z",
            "type": "state.transition",
            "verification": {
                "outcome": "MITIGATED",
                "metrics": {
                    "binding_errors_per_second": {
                        "before": 12.0,
                        "after": 0.2,
                        "delta": -11.8,
                        "unit": "errors_per_second",
                    },
                    "frame_p95_ms": {
                        "before": 42.5,
                        "after": 18.0,
                        "delta": -24.5,
                        "unit": "milliseconds",
                    },
                    "visual_count": {
                        "before": 500.0,
                        "after": 120.0,
                        "delta": -380.0,
                        "unit": "nodes",
                    },
                },
            },
        }
    )
    client = Mock()
    incident_document = client.collection.return_value.document.return_value
    incident_document.get.return_value = incident
    incident_document.collection.return_value.stream.return_value = [verification]
    client.collection.return_value.where.return_value.stream.return_value = []
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-9")

    assert response.status_code == 200
    assert "Before / After" in response.text
    assert "Binding rate" in response.text
    assert "12.0" in response.text and "0.2" in response.text
    assert "Frame p95" in response.text
    assert "42.5" in response.text and "18.0" in response.text
    assert "Visual count" in response.text
    assert "500.0" in response.text and "120.0" in response.text


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
