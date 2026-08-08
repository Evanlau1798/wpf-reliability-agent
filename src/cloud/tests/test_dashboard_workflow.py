import re
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import firestore_client
from app.main import app


FORMAL_UI_SOURCES = (
    Path(__file__).parents[1] / "app" / "dashboard.py",
    Path(__file__).parents[1] / "app" / "reporting.py",
)
CJK_PATTERN = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def test_console_incident_detail_renders_log_filter_workflow_identifiers(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    incident = Mock(exists=True)
    incident.to_dict.return_value = {"state": "MITIGATED", "summary": "Workflow complete"}
    incident_document = Mock()
    incident_document.get.return_value = incident
    incident_document.collection.return_value.stream.return_value = []
    incident_collection = Mock()
    incident_collection.document.return_value = incident_document

    command = Mock(id="command-1")
    command.to_dict.return_value = {
        "incident_id": "incident-11",
        "command_id": "command-1",
        "tool": "recovery.set_feature_flag",
        "arguments_hash": "a" * 64,
        "status": "COMPLETED",
    }
    command_collection = Mock()
    command_collection.where.return_value.stream.return_value = [command]

    run = Mock(id="incident-11:7:recovery.result")
    run.to_dict.return_value = {"incident_id": "incident-11", "trigger": "recovery.result"}
    run_collection = Mock()
    run_collection.where.return_value.stream.return_value = [run]

    client = Mock()
    client.collection.side_effect = lambda name: {
        firestore_client.INCIDENTS_COLLECTION: incident_collection,
        firestore_client.COMMANDS_COLLECTION: command_collection,
        firestore_client.PROCESSED_RUNS_COLLECTION: run_collection,
    }[name]
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-11")

    assert response.status_code == 200
    assert "Workflow IDs" in response.text
    assert "incident-11" in response.text
    assert "incident-11:7:recovery.result" in response.text
    assert "command-1" in response.text


def test_formal_dashboard_ui_sources_do_not_require_cjk() -> None:
    for path in FORMAL_UI_SOURCES:
        assert CJK_PATTERN.search(path.read_text(encoding="utf-8")) is None


def test_console_incident_detail_traces_complete_auditable_workflow(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    incident = Mock(exists=True)
    incident.to_dict.return_value = {"state": "MITIGATED", "summary": "Mitigation verified"}
    audit = _snapshot(
        "12",
        {
            "sequence": 12,
            "timestamp_utc": "2026-08-09T01:12:00Z",
            "type": "state.transition",
            "entry_hash": "1" * 64,
            "verification": {
                "outcome": "MITIGATED",
                "metrics": {
                    "binding_errors_per_second": {"before": 12.0, "after": 0.2, "unit": "errors_per_second"},
                    "frame_p95_ms": {"before": 42.5, "after": 18.0, "unit": "milliseconds"},
                    "visual_count": {"before": 500.0, "after": 120.0, "unit": "nodes"},
                },
            },
        },
    )
    evidence = _snapshot("evidence-1", {"event_type": "binding.aggregate", "evidence_hash": "2" * 64})
    approval = _snapshot(
        "approval-1",
        {
            "approval_id": "approval-1",
            "status": "APPROVED",
            "action_id": "action-1",
            "tool": "recovery.set_feature_flag",
            "canonical_arguments": {"feature": "ExperimentalPeopleGrid", "enabled": False},
            "canonical_arguments_hash": "3" * 64,
            "evidence_snapshot_hash": "4" * 64,
            "rollback_plan": "Re-enable the feature.",
            "expires_at_utc": "2026-08-09T02:00:00Z",
        },
    )
    report = _snapshot("1", {"metadata": {"report_sha256": "5" * 64}})
    collections = {
        firestore_client.AUDIT_COLLECTION: _collection([audit]),
        firestore_client.EVIDENCE_COLLECTION: _collection([evidence]),
        firestore_client.APPROVALS_COLLECTION: _collection([approval]),
        firestore_client.REPORTS_COLLECTION: _collection([report]),
    }
    incident_document = Mock()
    incident_document.get.return_value = incident
    incident_document.collection.side_effect = collections.__getitem__
    incident_collection = Mock()
    incident_collection.document.return_value = incident_document

    command = _snapshot(
        "command-1",
        {
            "incident_id": "incident-gate-15",
            "command_id": "command-1",
            "tool": "recovery.set_feature_flag",
            "arguments_hash": "6" * 64,
            "status": "COMPLETED",
            "completion_result": {
                "started_at_utc": "2026-08-09T01:10:00Z",
                "completed_at_utc": "2026-08-09T01:10:00.250000Z",
            },
        },
    )
    command_collection = _collection([command])
    run_collection = _collection([_snapshot("incident-gate-15:7:recovery.result", {})])
    client = Mock()
    client.collection.side_effect = lambda name: {
        firestore_client.INCIDENTS_COLLECTION: incident_collection,
        firestore_client.COMMANDS_COLLECTION: command_collection,
        firestore_client.PROCESSED_RUNS_COLLECTION: run_collection,
    }[name]
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-gate-15")

    assert response.status_code == 200
    for expected in (
        "evidence-1",
        "command-1",
        "approval-1",
        "action-1",
        "Before / After",
        "5" * 64,
        "1" * 64,
    ):
        assert expected in response.text


def _snapshot(document_id: str, data: dict[str, object]) -> Mock:
    snapshot = Mock(id=document_id)
    snapshot.to_dict.return_value = data
    return snapshot


def _collection(snapshots: list[Mock]) -> Mock:
    collection = Mock()
    collection.stream.return_value = snapshots
    collection.where.return_value.stream.return_value = snapshots
    return collection


def _set_api_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "device-secret")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "operator-secret")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
