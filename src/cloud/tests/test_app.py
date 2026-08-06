import asyncio
import io
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

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


async def _startup_role() -> str:
    async with app.router.lifespan_context(app):
        return app.state.settings.service_role


def _set_required_environment(monkeypatch, role: str) -> None:
    monkeypatch.setenv("SERVICE_ROLE", role)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")


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
