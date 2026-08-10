import base64
import json
from unittest.mock import Mock

from app import main, worker_auth
from fastapi.testclient import TestClient


def test_new_incident_step_publishes_triage_continuation_after_commit(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    firestore_client = object()
    order: list[object] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(main, "load_incident_workflow_state", lambda *_args: {"state": "NEW"})
    monkeypatch.setattr(
        main,
        "commit_new_incident_run",
        lambda *_args, **_kwargs: order.append("commit") or True,
    )
    monkeypatch.setattr(
        main,
        "publish_work",
        lambda project, topic, payload: order.append((project, topic, payload)),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(),
        )

    assert response.status_code == 204
    assert order == [
        "commit",
        (
            "project-test",
            "incident-work",
            {
                "incident_id": "incident-1",
                "evidence_revision": 2,
                "trigger": "workflow.triaging",
                "event_id": "event-1",
            },
        ),
    ]


def test_triaging_step_advances_once_and_publishes_collecting_continuation(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    firestore_client = object()
    transitions: list[dict[str, object]] = []
    published: list[dict[str, object]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(
        main,
        "load_incident_workflow_state",
        lambda *_args: {"state": "TRIAGING", "state_version": 2, "pending_command_id": None},
        raising=False,
    )

    def commit(*_args, **kwargs):
        transitions.append(kwargs)
        return True

    monkeypatch.setattr(main, "commit_transition_run", commit, raising=False)
    monkeypatch.setattr(main, "publish_work", lambda _project, _topic, payload: published.append(payload))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(trigger="workflow.triaging"),
        )

    assert response.status_code == 204
    assert transitions[0]["expected_state"] is main.IncidentState.TRIAGING
    assert transitions[0]["target_state"] is main.IncidentState.COLLECTING_EVIDENCE
    assert published == [{
        "incident_id": "incident-1",
        "evidence_revision": 2,
        "trigger": "workflow.collecting_evidence",
        "event_id": "event-1",
    }]


def test_collecting_step_advances_once_and_publishes_investigation_continuation(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    transitions: list[dict[str, object]] = []
    published: list[dict[str, object]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: object())
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(
        main,
        "commit_transition_run",
        lambda *_args, **kwargs: transitions.append(kwargs) or True,
        raising=False,
    )
    monkeypatch.setattr(main, "publish_work", lambda _project, _topic, payload: published.append(payload))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(trigger="workflow.collecting_evidence"),
        )

    assert response.status_code == 204
    assert transitions[0]["expected_state"] is main.IncidentState.COLLECTING_EVIDENCE
    assert transitions[0]["target_state"] is main.IncidentState.INVESTIGATING
    assert published[0]["trigger"] == "workflow.investigating"


def test_investigating_trigger_runs_one_investigator_step(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    firestore_client = object()
    calls: list[tuple[object, dict[str, object], str, str]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)

    async def run(client, *, work, run_key, model_id):
        calls.append((client, work, run_key, model_id))

    monkeypatch.setattr(main, "run_investigation_step", run, raising=False)

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(trigger="workflow.investigating"),
        )

    assert response.status_code == 204
    assert calls == [(
        firestore_client,
        {
            "incident_id": "incident-1",
            "evidence_revision": 2,
            "trigger": "workflow.investigating",
            "event_id": "event-1",
        },
        "incident-1:2:workflow.investigating",
        "gemini-3.5-flash-lite",
    )]


def test_investigating_finalize_publishes_report_continuation(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    published: list[dict[str, object]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: object())
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)

    async def run(*_args, **_kwargs):
        return main.IncidentState.REPORTING

    monkeypatch.setattr(main, "run_investigation_step", run, raising=False)
    monkeypatch.setattr(main, "publish_work", lambda _project, _topic, payload: published.append(payload))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(trigger="workflow.investigating"),
        )

    assert response.status_code == 204
    assert published == [{
        "incident_id": "incident-1",
        "evidence_revision": 2,
        "trigger": "workflow.report",
        "event_id": "event-1",
    }]


def test_terminal_reporting_step_advances_once_and_publishes_report(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    transitions: list[dict[str, object]] = []
    published: list[dict[str, object]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: object())
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(
        main,
        "commit_terminal_reporting_run",
        lambda *_args, **kwargs: transitions.append(kwargs) or True,
        raising=False,
    )
    monkeypatch.setattr(main, "publish_work", lambda _project, _topic, payload: published.append(payload))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(trigger="workflow.reporting"),
        )

    assert response.status_code == 204
    assert transitions[0]["incident_id"] == "incident-1"
    assert published == [{
        "incident_id": "incident-1",
        "evidence_revision": 2,
        "trigger": "workflow.report",
        "event_id": "event-1",
    }]


def test_report_step_runs_reporter_workflow_once(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    firestore_client = object()
    calls: list[tuple[object, dict[str, object], str, str]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)

    async def run(client, *, work, run_key, model_id):
        calls.append((client, work, run_key, model_id))
        return True

    monkeypatch.setattr(main, "run_reporting_step", run, raising=False)

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(trigger="workflow.report"),
        )

    assert response.status_code == 204
    assert calls == [(
        firestore_client,
        {
            "incident_id": "incident-1",
            "evidence_revision": 2,
            "trigger": "workflow.report",
            "event_id": "event-1",
        },
        "incident-1:2:workflow.report",
        "gemini-3.5-flash-lite",
    )]


def test_duplicate_delivery_republishes_current_continuation(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    firestore_client = object()
    published: list[dict[str, object]] = []
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: True)
    monkeypatch.setattr(
        main,
        "load_incident_workflow_state",
        lambda *_args: {"state": "TRIAGING", "state_version": 2, "pending_command_id": None},
        raising=False,
    )
    monkeypatch.setattr(main, "publish_work", lambda _project, _topic, payload: published.append(payload))
    commit = Mock(side_effect=AssertionError("duplicate must not commit again"))
    monkeypatch.setattr(main, "commit_new_incident_run", commit)

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(),
        )

    assert response.status_code == 204
    commit.assert_not_called()
    assert published[0]["trigger"] == "workflow.triaging"


def test_stale_work_does_not_advance_while_evidence_command_is_pending(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    published: list[dict[str, object]] = []
    commit = Mock(return_value=True)
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: object())
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(
        main,
        "load_incident_workflow_state",
        lambda *_args: {
            "state": "COLLECTING_EVIDENCE",
            "evidence_revision": 7,
            "pending_command_id": "cmd-read-1",
        },
    )
    monkeypatch.setattr(main, "commit_new_incident_run", commit)
    monkeypatch.setattr(main, "publish_work", lambda _project, _topic, payload: published.append(payload))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/work:push",
            headers={"Authorization": "Bearer signed-token"},
            json=_push_envelope(),
        )

    assert response.status_code == 204
    commit.assert_not_called()
    assert published == []


def test_stale_external_work_republishes_durable_continuation(monkeypatch) -> None:
    _set_worker_environment(monkeypatch)
    _allow_identity(monkeypatch)
    states = [
        {"state": "INVESTIGATING", "evidence_revision": 7},
        {"state": "REJECTED", "evidence_revision": 7},
        {"state": "REPORTING", "evidence_revision": 7},
    ]
    published: list[dict[str, object]] = []
    commit = Mock(return_value=True)
    monkeypatch.setattr(main, "get_firestore_client", lambda _project_id: object())
    monkeypatch.setattr(main, "is_run_processed", lambda *_args: False)
    monkeypatch.setattr(main, "load_incident_workflow_state", lambda *_args: states.pop(0))
    monkeypatch.setattr(main, "commit_new_incident_run", commit)
    monkeypatch.setattr(main, "publish_work", lambda _project, _topic, payload: published.append(payload))

    with TestClient(main.app) as client:
        responses = [
            client.post(
                "/v1/work:push",
                headers={"Authorization": "Bearer signed-token"},
                json=_push_envelope(),
            )
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [204, 204, 204]
    commit.assert_not_called()
    assert [payload["trigger"] for payload in published] == [
        "workflow.investigating",
        "workflow.reporting",
        "workflow.report",
    ]
    assert [payload["evidence_revision"] for payload in published] == [7, 7, 7]


def _set_worker_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "worker")
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


def _push_envelope(*, trigger: str = "binding.aggregate") -> dict[str, object]:
    work = {
        "incident_id": "incident-1",
        "evidence_revision": 2,
        "trigger": trigger,
        "event_id": "event-1",
    }
    return {
        "message": {
            "messageId": "message-1",
            "data": base64.b64encode(json.dumps(work).encode("utf-8")).decode("ascii"),
        }
    }
