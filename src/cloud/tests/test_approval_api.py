import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth, firestore_client
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
    csrf_cookie = response.cookies.get(auth.OPERATOR_CSRF_COOKIE)
    assert csrf_cookie is not None
    assert len(csrf_cookie) >= 32
    csrf_set_cookie = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{auth.OPERATOR_CSRF_COOKIE}=")
    )
    assert "HttpOnly" not in csrf_set_cookie
    assert "Secure" in csrf_set_cookie
    assert "SameSite=strict" in csrf_set_cookie
    assert "Path=/" in csrf_set_cookie


def test_operator_login_rejects_invalid_token(monkeypatch) -> None:
    _set_api_environment(monkeypatch)

    with TestClient(app) as client:
        response = client.post("/console/login", json={"token": "wrong-secret"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid operator token"}
    assert "set-cookie" not in response.headers


def test_approval_post_requires_matching_csrf_token() -> None:
    csrf_dependency = getattr(auth, "validate_operator_csrf", None)
    assert csrf_dependency is not None
    approval_app = FastAPI()

    @approval_app.post("/v1/approvals/{approval_id}:decide")
    def decide(
        approval_id: str,
        _: Annotated[None, Depends(csrf_dependency)],
    ) -> dict[str, str]:
        return {"approval_id": approval_id}

    with TestClient(approval_app, base_url="https://testserver") as client:
        client.cookies.set(auth.OPERATOR_CSRF_COOKIE, "csrf-token")
        missing = client.post("/v1/approvals/approval-1:decide")
        wrong = client.post(
            "/v1/approvals/approval-1:decide",
            headers={auth.OPERATOR_CSRF_HEADER: "wrong-token"},
        )
        valid = client.post(
            "/v1/approvals/approval-1:decide",
            headers={auth.OPERATOR_CSRF_HEADER: "csrf-token"},
        )

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert valid.status_code == 200


def test_approval_decide_route_accepts_only_approve_or_reject(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: object())
    monkeypatch.setattr(
        firestore_client,
        "approve_pending_approval",
        lambda _client, *, approval_id, actor, now: f"command-{approval_id}",
    )
    monkeypatch.setattr(
        firestore_client,
        "reject_pending_approval",
        lambda _client, *, approval_id, actor, now: 1,
    )

    with TestClient(app, base_url="https://testserver") as client:
        login = client.post("/console/login", json={"token": "operator-secret"})
        csrf = login.cookies.get(auth.OPERATOR_CSRF_COOKIE)
        assert csrf is not None
        approve = client.post(
            "/v1/approvals/approval-1:decide",
            headers={auth.OPERATOR_CSRF_HEADER: csrf},
            json={"decision": "approve"},
        )
        reject = client.post(
            "/v1/approvals/approval-1:decide",
            headers={auth.OPERATOR_CSRF_HEADER: csrf},
            json={"decision": "reject"},
        )
        invalid = client.post(
            "/v1/approvals/approval-1:decide",
            headers={auth.OPERATOR_CSRF_HEADER: csrf},
            json={"decision": "delete"},
        )

    assert approve.status_code == 200
    assert approve.json() == {"approval_id": "approval-1", "decision": "approve"}
    assert reject.status_code == 200
    assert reject.json() == {"approval_id": "approval-1", "decision": "reject"}
    assert invalid.status_code == 422


def test_approval_decide_route_records_authenticated_operator_actor(monkeypatch) -> None:
    _set_api_environment(monkeypatch)
    client_marker = object()
    calls: list[tuple[str, object, str, str]] = []
    monkeypatch.setattr("app.main.get_firestore_client", lambda _project_id: client_marker)

    def approve(client, *, approval_id, actor, now):
        assert now.tzinfo is not None
        calls.append(("approve", client, approval_id, actor))
        return "command-1"

    def reject(client, *, approval_id, actor, now):
        assert now.tzinfo is not None
        calls.append(("reject", client, approval_id, actor))
        return 6

    monkeypatch.setattr(firestore_client, "approve_pending_approval", approve)
    monkeypatch.setattr(firestore_client, "reject_pending_approval", reject)

    with TestClient(app, base_url="https://testserver") as client:
        login = client.post("/console/login", json={"token": "operator-secret"})
        csrf = login.cookies.get(auth.OPERATOR_CSRF_COOKIE)
        assert csrf is not None
        approve_response = client.post(
            "/v1/approvals/approval-1:decide",
            headers={auth.OPERATOR_CSRF_HEADER: csrf},
            json={"decision": "approve"},
        )
        reject_response = client.post(
            "/v1/approvals/approval-2:decide",
            headers={auth.OPERATOR_CSRF_HEADER: csrf},
            json={"decision": "reject"},
        )

    assert approve_response.status_code == 200
    assert reject_response.status_code == 200
    assert calls == [
        ("approve", client_marker, "approval-1", "demo-operator"),
        ("reject", client_marker, "approval-2", "demo-operator"),
    ]


def test_approval_decide_route_requires_valid_operator_session(monkeypatch) -> None:
    _set_api_environment(monkeypatch)

    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(auth.OPERATOR_CSRF_COOKIE, "csrf-token")
        missing = client.post(
            "/v1/approvals/approval-1:decide",
            headers={auth.OPERATOR_CSRF_HEADER: "csrf-token"},
            json={"decision": "approve"},
        )
        client.cookies.set(auth.OPERATOR_SESSION_COOKIE, "tampered.session")
        tampered = client.post(
            "/v1/approvals/approval-1:decide",
            headers={auth.OPERATOR_CSRF_HEADER: "csrf-token"},
            json={"decision": "approve"},
        )

    assert missing.status_code == 401
    assert tampered.status_code == 401


def _set_api_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ROLE", "api")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("DEMO_DEVICE_ID", "device-test")
    monkeypatch.setenv("DEMO_DEVICE_TOKEN", "device-secret")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "operator-secret")
    monkeypatch.setenv("PUBSUB_TOPIC", "incident-work")
