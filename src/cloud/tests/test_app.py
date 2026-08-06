import asyncio
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import authenticate_device_token, parse_bearer_token
from app.config import Settings
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


async def _startup_role() -> str:
    async with app.router.lifespan_context(app):
        return app.state.settings.service_role


def _set_required_environment(monkeypatch, role: str) -> None:
    monkeypatch.setenv("SERVICE_ROLE", role)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
