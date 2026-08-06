import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

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


async def _startup_role() -> str:
    async with app.router.lifespan_context(app):
        return app.state.settings.service_role


def _set_required_environment(monkeypatch, role: str) -> None:
    monkeypatch.setenv("SERVICE_ROLE", role)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "secret-token")
