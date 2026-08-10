import base64
import json
from pathlib import Path

from app import main, worker_auth
from app.main import app
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_approved_mutation_post_snapshot_enters_mitigated_without_manual_state_edit(
    monkeypatch,
) -> None:
    fixture = json.loads((FIXTURES / "post-action-mitigation.json").read_text(encoding="utf-8"))
    firestore_client = object()
    committed: list[dict[str, object]] = []
    monkeypatch.setenv("SERVICE_ROLE", "worker")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
    monkeypatch.setenv("PUBSUB_PUSH_AUDIENCE", "https://worker.example.test")
    monkeypatch.setenv("PUBSUB_INVOKER_EMAIL", "pubsub-invoker@example.test")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-east1")
    monkeypatch.setattr(
        worker_auth.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {
            "email": "pubsub-invoker@example.test",
            "email_verified": True,
        },
    )
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(main, "load_incident_evidence", lambda *_args: fixture["evidence"])
    monkeypatch.setattr(main, "publish_work", lambda *_args: "message-1")

    def commit(*_args, **kwargs):
        committed.append(kwargs)
        return True

    monkeypatch.setattr(main, "commit_verification_run", commit)
    work = {
        "incident_id": "incident-1",
        "evidence_revision": 7,
        "trigger": "recovery.result",
        "event_id": "post-1",
    }

    with TestClient(app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json={
                "message": {
                    "messageId": "message-gate-14",
                    "data": base64.b64encode(json.dumps(work).encode("utf-8")).decode("ascii"),
                }
            },
        )

    assert response.status_code == 204
    assert len(committed) == 1
    assert committed[0]["target_state"] is main.IncidentState.MITIGATED
    assert committed[0]["command_id"] == "command-1"
    assert committed[0]["action_id"] == "action-1"
    verification = committed[0]["verification"]
    assert verification["outcome"] == fixture["expected_outcome"] == "MITIGATED"
    assert verification["evidence_ids"] == [
        "binding-before",
        "performance-before",
        "post-1",
        "command-1",
    ]
