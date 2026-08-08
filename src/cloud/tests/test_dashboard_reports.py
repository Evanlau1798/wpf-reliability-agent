from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import firestore_client
from app.main import app
from app.models import IncidentReport


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_console_incident_detail_shows_report_downloads_and_hash(monkeypatch) -> None:
    client, _report = _report_client()
    _set_api_environment(monkeypatch)
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        response = test_client.get("/console/incidents/incident-1")

    assert response.status_code == 200
    assert "Reports" in response.text
    assert "f" * 64 in response.text
    assert 'href="/console/incidents/incident-1/reports/1.md"' in response.text
    assert 'href="/console/incidents/incident-1/reports/1.html"' in response.text


def test_console_report_download_requires_operator_session_and_serves_both_formats(monkeypatch) -> None:
    client, _report = _report_client()
    _set_api_environment(monkeypatch)
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client)

    with TestClient(app, base_url="https://testserver") as test_client:
        unauthorized = test_client.get("/console/incidents/incident-1/reports/1.md")
        assert test_client.post("/console/login", json={"token": "operator-secret"}).status_code == 204
        markdown = test_client.get("/console/incidents/incident-1/reports/1.md")
        html = test_client.get("/console/incidents/incident-1/reports/1.html")

    assert unauthorized.status_code == 401
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "attachment" in markdown.headers["content-disposition"]
    assert markdown.text.startswith("# Incident incident-1\n")
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert "attachment" in html.headers["content-disposition"]
    assert html.text.startswith("<!doctype html>\n")


def _report_client() -> tuple[Mock, IncidentReport]:
    report = IncidentReport.model_validate_json(
        (FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8")
    )
    payload = report.model_dump(mode="json")
    payload["metadata"]["report_sha256"] = "f" * 64
    report_snapshot = Mock(id="1", exists=True)
    report_snapshot.to_dict.return_value = payload
    incident_snapshot = Mock(exists=True)
    incident_snapshot.to_dict.return_value = {"state": "MITIGATED", "summary": "Report ready"}
    empty_collection = Mock()
    empty_collection.stream.return_value = []
    report_collection = Mock()
    report_collection.stream.return_value = [report_snapshot]
    report_collection.document.return_value.get.return_value = report_snapshot
    incident_document = Mock()
    incident_document.get.return_value = incident_snapshot
    incident_document.collection.side_effect = lambda name: (
        report_collection if name == firestore_client.REPORTS_COLLECTION else empty_collection
    )
    incident_collection = Mock()
    incident_collection.document.return_value = incident_document
    command_collection = Mock()
    command_collection.where.return_value.stream.return_value = []
    run_collection = Mock()
    run_collection.where.return_value.stream.return_value = []
    client = Mock()
    client.collection.side_effect = lambda name: {
        firestore_client.INCIDENTS_COLLECTION: incident_collection,
        firestore_client.COMMANDS_COLLECTION: command_collection,
        firestore_client.PROCESSED_RUNS_COLLECTION: run_collection,
    }[name]
    return client, report


def _set_api_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "device-secret")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "operator-secret")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
