from fastapi import HTTPException, Request, status
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token


def authenticate_pubsub_push(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _unauthorized()

    settings = request.app.state.settings
    try:
        claims = id_token.verify_oauth2_token(
            parts[1],
            GoogleAuthRequest(),
            settings.pubsub_push_audience,
        )
    except (ValueError, google_auth_exceptions.GoogleAuthError):
        raise _unauthorized() from None

    if (
        claims.get("email_verified") is not True
        or claims.get("email") != settings.pubsub_invoker_email
    ):
        raise _unauthorized()
    return settings.pubsub_invoker_email


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid Pub/Sub identity",
        headers={"WWW-Authenticate": "Bearer"},
    )
