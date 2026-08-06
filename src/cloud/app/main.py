import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status

from app.auth import authenticate_device_token
from app.config import Settings
from app.logging_config import configure_logging


MAX_TELEMETRY_BATCH_BYTES = 512 * 1024


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.settings = Settings.from_environment()
    application.state.logger = configure_logging(application.state.settings.service_role)
    yield


app = FastAPI(title="WPF Reliability Agent", lifespan=lifespan)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def enforce_telemetry_body_limit(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if (
        content_length
        and content_length.isdecimal()
        and int(content_length) > MAX_TELEMETRY_BATCH_BYTES
    ):
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Telemetry batch too large",
        )
    if len(await request.body()) > MAX_TELEMETRY_BATCH_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Telemetry batch too large",
        )


async def parse_telemetry_events(
    request: Request,
    _body_limit: Annotated[None, Depends(enforce_telemetry_body_limit)],
) -> list[object]:
    try:
        body = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid telemetry batch",
        ) from exc

    if not isinstance(body, dict) or not isinstance(body.get("events"), list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid telemetry batch",
        )

    events = body["events"]
    if len(events) > 50:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Too many telemetry events",
        )
    return events


@app.post("/v1/telemetry:batch")
def telemetry_batch(
    _: Annotated[str, Depends(authenticate_device_token)],
    _events: Annotated[list[object], Depends(parse_telemetry_events)],
) -> dict[str, list[object]]:
    return {
        "accepted_event_ids": [],
        "duplicate_event_ids": [],
        "rejected": [],
    }
