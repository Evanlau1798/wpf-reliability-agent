from fastapi import FastAPI

from app.main import app


def test_cloud_app_exposes_health_route() -> None:
    assert isinstance(app, FastAPI)
    health = next(route for route in app.routes if route.path == "/healthz")
    assert health.endpoint() == {"status": "ok"}
