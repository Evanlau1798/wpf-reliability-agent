from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import firestore_client
from app.main import app


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


def _set_api_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "device-secret")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "operator-secret")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
