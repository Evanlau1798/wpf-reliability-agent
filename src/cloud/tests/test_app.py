import asyncio
import io
from typing import Annotated
from unittest.mock import Mock

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import firestore_client as firestore_store
from app.auth import authenticate_device_token, parse_bearer_token
from app.config import Settings
from app.logging_config import configure_logging
from app.main import app


def test_cloud_app_exposes_health_route() -> None:
    assert isinstance(app, FastAPI)
    health = next(route for route in app.routes if route.path == "/healthz")
    assert health.endpoint() == {"status": "ok"}


def test_same_app_starts_in_api_and_worker_roles(monkeypatch) -> None:
    for role in ("api", "worker"):
        _set_required_environment(monkeypatch, role)

        assert asyncio.run(_startup_role()) == role


def test_health_endpoint_returns_200_without_firestore(monkeypatch) -> None:
    _set_required_environment(monkeypatch, "api")

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_bearer_parser_rejects_missing_and_malformed_headers() -> None:
    auth_app = FastAPI()

    @auth_app.get("/protected")
    def protected(token: Annotated[str, Depends(parse_bearer_token)]) -> dict[str, str]:
        return {"token": token}

    with TestClient(auth_app) as client:
        for headers in ({}, {"Authorization": "Basic value"}, {"Authorization": "Bearer"}):
            response = client.get("/protected", headers=headers)

            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"


def test_device_token_uses_constant_time_compare(monkeypatch) -> None:
    auth_app = FastAPI()
    auth_app.state.settings = Settings(
        service_role="api",
        google_cloud_project="project-test",
        demo_device_id="device-test",
        demo_device_token="secret-token",
        pubsub_topic="incident-work",
    )
    calls: list[tuple[str, str]] = []

    def compare_digest(candidate: str, expected: str) -> bool:
        calls.append((candidate, expected))
        return True

    monkeypatch.setattr("app.auth.hmac.compare_digest", compare_digest)

    @auth_app.get("/protected")
    def protected(_: Annotated[None, Depends(authenticate_device_token)]) -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(auth_app) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer candidate-token"})

    assert response.status_code == 200
    assert calls == [("candidate-token", "secret-token")]


def test_device_token_binds_configured_device_id() -> None:
    auth_app = FastAPI()
    auth_app.state.settings = Settings(
        service_role="api",
        google_cloud_project="project-test",
        demo_device_id="device-test",
        demo_device_token="secret-token",
        pubsub_topic="incident-work",
    )

    @auth_app.post("/protected")
    def protected(
        payload: dict[str, str],
        device_id: Annotated[str, Depends(authenticate_device_token)],
    ) -> dict[str, str]:
        return {"device_id": device_id, "requested_device_id": payload["device_id"]}

    with TestClient(auth_app) as client:
        response = client.post(
            "/protected",
            headers={"Authorization": "Bearer secret-token"},
            json={"device_id": "impersonated-device"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "device_id": "device-test",
        "requested_device_id": "impersonated-device",
    }


def test_invalid_device_token_returns_401_without_logging_token() -> None:
    output = io.StringIO()
    auth_app = FastAPI()
    auth_app.state.settings = Settings(
        service_role="api",
        google_cloud_project="project-test",
        demo_device_id="device-test",
        demo_device_token="secret-token",
        pubsub_topic="incident-work",
    )
    auth_app.state.logger = configure_logging("api", output)

    @auth_app.get("/protected")
    def protected(_: Annotated[str, Depends(authenticate_device_token)]) -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(auth_app) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer invalid-token"},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "invalid-token" not in output.getvalue()


def test_telemetry_batch_route_requires_authenticated_post(monkeypatch) -> None:
    _set_required_environment(monkeypatch, "api")

    with TestClient(app) as client:
        missing_auth = client.post("/v1/telemetry:batch", json={"events": []})
        wrong_method = client.get(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
        )
        accepted = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={"events": []},
        )

    assert missing_auth.status_code == 401
    assert wrong_method.status_code == 405
    assert accepted.status_code == 200
    assert accepted.json() == {
        "accepted_event_ids": [],
        "duplicate_event_ids": [],
        "rejected": [],
    }


def test_telemetry_batch_rejects_body_over_512_kib(monkeypatch) -> None:
    _set_required_environment(monkeypatch, "api")
    oversized = b'{"events":[],"padding":"' + (b"x" * (512 * 1024)) + b'"}'

    with TestClient(app) as client:
        response = client.post(
            "/v1/telemetry:batch",
            headers={
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/json",
            },
            content=oversized,
        )

    assert response.status_code == 413


def test_telemetry_batch_rejects_more_than_50_events(monkeypatch) -> None:
    _set_required_environment(monkeypatch, "api")

    with TestClient(app) as client:
        response = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={"events": [{"event_id": f"event-{index}"} for index in range(51)]},
        )

    assert response.status_code == 422


def test_telemetry_batch_rejects_invalid_event_without_rejecting_batch(monkeypatch) -> None:
    _set_required_environment(monkeypatch, "api")
    invalid = _valid_telemetry_event("event-invalid")
    invalid["timestamp_utc"] = "not-a-timestamp"

    with TestClient(app) as client:
        response = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={"events": [invalid]},
        )

    assert response.status_code == 200
    assert response.json() == {
        "accepted_event_ids": [],
        "duplicate_event_ids": [],
        "rejected": [{"event_id": "event-invalid", "code": "INVALID_EVENT"}],
    }


def test_telemetry_batch_reports_duplicate_event_id(monkeypatch) -> None:
    _set_required_environment(monkeypatch, "api")
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: object())
    outcomes = iter(
        [
            (True, "incident-1", {"incident_id": "incident-1"}),
            (False, "incident-1", None),
        ]
    )
    monkeypatch.setattr(
        "app.main.ingest_binding_event",
        lambda _client, _event, _device_id: next(outcomes),
    )
    event = _valid_telemetry_event("event-1")

    with TestClient(app) as client:
        first = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={"events": [event]},
        )
        second = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={"events": [event]},
        )

    assert first.json()["accepted_event_ids"] == ["event-1"]
    assert first.json()["duplicate_event_ids"] == []
    assert second.json()["accepted_event_ids"] == []
    assert second.json()["duplicate_event_ids"] == ["event-1"]


def test_telemetry_batch_reports_mixed_accepted_duplicate_and_invalid_events(monkeypatch) -> None:
    _set_required_environment(monkeypatch, "api")
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: object())
    outcomes = iter(
        [
            (True, "incident-1", {"incident_id": "incident-1"}),
            (False, "incident-2", None),
        ]
    )
    monkeypatch.setattr(
        "app.main.ingest_binding_event",
        lambda _client, _event, _device_id: next(outcomes),
    )
    invalid = _valid_telemetry_event("invalid-event")
    invalid["timestamp_utc"] = "not-a-timestamp"

    with TestClient(app) as client:
        response = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "events": [
                    _valid_telemetry_event("accepted-event"),
                    _valid_telemetry_event("duplicate-event"),
                    invalid,
                ]
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "accepted_event_ids": ["accepted-event"],
        "duplicate_event_ids": ["duplicate-event"],
        "rejected": [{"event_id": "invalid-event", "code": "INVALID_EVENT"}],
    }


def test_telemetry_batch_persists_binding_incident_and_evidence(monkeypatch) -> None:
    _set_required_environment(monkeypatch, "api")
    firestore_client = Mock()
    transaction = Mock()
    dedup_collection = Mock()
    incident_collection = Mock()
    dedup_document = Mock()
    incident_document = Mock()
    evidence_document = Mock()
    firestore_client.transaction.return_value = transaction
    firestore_client.collection.side_effect = lambda name: (
        dedup_collection if name == "event_dedup" else incident_collection
    )
    dedup_collection.document.return_value = dedup_document
    incident_collection.document.return_value = incident_document
    incident_document.collection.return_value.document.return_value = evidence_document
    dedup_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(exists=False)
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: firestore_client)
    monkeypatch.setattr("app.firestore_client.firestore.transactional", lambda callback: callback)

    with TestClient(app) as client:
        response = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={"events": [_valid_telemetry_event("binding-event")]},
        )

    assert response.status_code == 200
    assert response.json() == {
        "accepted_event_ids": ["binding-event"],
        "duplicate_event_ids": [],
        "rejected": [],
    }
    assert [call.args[0] for call in transaction.create.call_args_list] == [
        dedup_document,
        incident_document,
        evidence_document,
    ]
    incident_id = incident_collection.document.call_args.args[0]
    assert transaction.create.call_args_list[0].args[1] == {
        "created_at": firestore_store.firestore.SERVER_TIMESTAMP,
        "incident_id": incident_id,
    }
    incident = transaction.create.call_args_list[1].args[1]
    assert incident["state"] == "NEW"
    assert incident["evidence_revision"] == 1
    evidence = transaction.create.call_args_list[2].args[1]
    assert evidence["event_id"] == "binding-event"
    assert evidence["device_id"] == "device-test"


def test_telemetry_batch_correlates_performance_to_unique_binding_candidate(monkeypatch) -> None:
    _set_required_environment(monkeypatch, "api")
    client_object = object()
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client_object)
    monkeypatch.setattr(
        "app.main.ingest_binding_event",
        lambda _client, _event, _device_id: (
            True,
            "incident-1",
            {"incident_id": "incident-1"},
        ),
    )
    related: list[tuple[object, str, str]] = []

    def ingest_performance(_client, event, device_id, incident_id):
        related.append((event, device_id, incident_id))
        return True, {"incident_id": incident_id}

    monkeypatch.setattr("app.main.ingest_performance_event", ingest_performance)

    with TestClient(app) as client:
        response = client.post(
            "/v1/telemetry:batch",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "events": [
                    _valid_performance_event("performance-1"),
                    _valid_telemetry_event("binding-1"),
                ]
            },
        )

    assert response.status_code == 200
    assert set(response.json()["accepted_event_ids"]) == {"binding-1", "performance-1"}
    assert response.json()["duplicate_event_ids"] == []
    assert len(related) == 1
    assert related[0][0].event_id == "performance-1"
    assert related[0][1:] == ("device-test", "incident-1")


async def _startup_role() -> str:
    async with app.router.lifespan_context(app):
        return app.state.settings.service_role


def _set_required_environment(monkeypatch, role: str) -> None:
    monkeypatch.setattr("app.main.publish_work", lambda *_args: None)
    monkeypatch.setenv("SERVICE_ROLE", role)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
    monkeypatch.setenv("PUBSUB_PUSH_AUDIENCE", "https://worker.example.test")
    monkeypatch.setenv("PUBSUB_INVOKER_EMAIL", "pubsub-invoker@example.test")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-east1")


def _valid_telemetry_event(event_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": "binding.aggregate",
        "severity": "ERROR",
        "timestamp_utc": "2026-08-07T00:00:00Z",
        "device_id": "device-test",
        "application_id": "demo-broken-wpf-app",
        "application_version": "0.1.0",
        "app_session_id": "session-test",
        "sequence_no": 1,
        "correlation": {"binding_path": "DisplayNmae"},
        "payload": {
            "fingerprint": "binding-1",
            "occurrence_count": 1,
            "target_property": "Text",
        },
        "redaction_profile": "default-v1",
        "evidence_hash": "1" * 64,
    }


def _valid_performance_event(event_id: str) -> dict[str, object]:
    event = _valid_telemetry_event(event_id)
    event.update(
        {
            "event_type": "performance.sample",
            "severity": "INFO",
            "correlation": {"app_session_id": "session-test"},
            "payload": {
                "frame_statistics": {"p95_milliseconds": 40.0},
                "sample_duration_ms": 1000.0,
                "confidence": "MEDIUM",
                "visual_count": 1500,
            },
        }
    )
    return event
