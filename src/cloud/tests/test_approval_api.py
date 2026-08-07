import hashlib
import hmac

from fastapi.testclient import TestClient

from app import auth
from app.main import app


def test_operator_login_validates_token_from_environment(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    calls: list[tuple[str, str]] = []

    def compare_digest(candidate: str, expected: str) -> bool:
        calls.append((candidate, expected))
        return candidate == expected

    monkeypatch.setattr("app.auth.hmac.compare_digest", compare_digest)

    with TestClient(app) as client:
        response = client.post("/console/login", json={"token": "operator-secret"})

    assert response.status_code == 204
    assert calls == [("operator-secret", "operator-secret")]
    cookie = response.cookies.get(auth.OPERATOR_SESSION_COOKIE)
    assert cookie is not None
    payload, signature = cookie.rsplit(".", 1)
    expected_signature = hmac.new(
        b"operator-secret", payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert hmac.compare_digest(signature, expected_signature)
    assert "operator-secret" not in cookie
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/" in set_cookie


def test_operator_login_rejects_invalid_token(monkeypatch) -> None:
    _set_api_environment(monkeypatch)

    with TestClient(app) as client:
        response = client.post("/console/login", json={"token": "wrong-secret"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid operator token"}
    assert "set-cookie" not in response.headers


def _set_api_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "device-secret")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "operator-secret")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
