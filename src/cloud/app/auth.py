import hashlib
import hmac
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, SecretStr


bearer_scheme = HTTPBearer(auto_error=False)
OPERATOR_SESSION_COOKIE = "__Host-wpfra-operator-session"
OPERATOR_SESSION_PAYLOAD = "operator-session-v1"
OPERATOR_SESSION_MAX_AGE_SECONDS = 15 * 60
OPERATOR_CSRF_COOKIE = "__Host-wpfra-csrf"
OPERATOR_CSRF_HEADER = "X-CSRF-Token"


class OperatorLoginRequest(BaseModel):
    token: SecretStr = Field(min_length=1, max_length=4096)


def parse_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials


def authenticate_device_token(
    request: Request,
    token: Annotated[str, Depends(parse_bearer_token)],
) -> str:
    settings = request.app.state.settings
    expected_token = settings.demo_device_token.get_secret_value()
    if not hmac.compare_digest(token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return settings.demo_device_id


def authenticate_operator_token(request: Request, login: OperatorLoginRequest) -> SecretStr:
    expected = request.app.state.settings.demo_operator_token
    if expected is None or not hmac.compare_digest(
        login.token.get_secret_value(), expected.get_secret_value()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid operator token",
        )
    return expected


def create_operator_session_value(secret: SecretStr, *, now: datetime | None = None) -> str:
    issued_at = int((now or datetime.now(UTC)).timestamp())
    payload = f"{OPERATOR_SESSION_PAYLOAD}.{issued_at}"
    signature = hmac.new(
        secret.get_secret_value().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def is_operator_session_valid(
    cookie: str,
    secret: SecretStr,
    *,
    now: datetime | None = None,
) -> bool:
    try:
        payload, issued_at_value, signature = cookie.rsplit(".", 2)
        issued_at = int(issued_at_value)
    except ValueError:
        return False
    expected = hmac.new(
        secret.get_secret_value().encode("utf-8"),
        f"{payload}.{issued_at_value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    age = int((now or datetime.now(UTC)).timestamp()) - issued_at
    return (
        payload == OPERATOR_SESSION_PAYLOAD
        and 0 <= age <= OPERATOR_SESSION_MAX_AGE_SECONDS
        and hmac.compare_digest(signature, expected)
    )


def authenticate_operator_session(request: Request) -> str:
    secret = request.app.state.settings.demo_operator_token
    cookie = request.cookies.get(OPERATOR_SESSION_COOKIE)
    if secret is None or cookie is None or not is_operator_session_valid(cookie, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid operator session",
        )
    return "demo-operator"


def validate_operator_csrf(request: Request) -> None:
    cookie = request.cookies.get(OPERATOR_CSRF_COOKIE)
    header = request.headers.get(OPERATOR_CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token required",
        )
