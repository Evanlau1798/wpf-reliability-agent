import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status

from app.auth import authenticate_device_token
from app.config import Settings
from app.firestore_client import claim_event_once, get_firestore_client
from app.ingest import ingest_binding_event, ingest_performance_event, validate_telemetry_events
from app.logging_config import configure_logging
from app.models import EventType


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
    request: Request,
    device_id: Annotated[str, Depends(authenticate_device_token)],
    events: Annotated[list[object], Depends(parse_telemetry_events)],
) -> dict[str, list[object]]:
    valid, rejected = validate_telemetry_events(events)
    accepted_event_ids: list[str] = []
    duplicate_event_ids: list[str] = []
    if valid:
        client = get_firestore_client(request.app.state.settings.google_cloud_project)
        binding_candidates: dict[tuple[str, str], set[str]] = {}
        performance_events = []
        for event in valid:
            if event.event_type is EventType.BINDING_AGGREGATE:
                try:
                    is_new, incident_id, _publish_payload = ingest_binding_event(
                        client,
                        event,
                        device_id,
                    )
                except ValueError:
                    rejected.append({"event_id": event.event_id, "code": "INVALID_EVENT"})
                    continue
                key = (event.application_id, event.app_session_id)
                binding_candidates.setdefault(key, set()).add(incident_id)
            elif event.event_type is EventType.PERFORMANCE_SAMPLE:
                performance_events.append(event)
                continue
            else:
                is_new = claim_event_once(client, event.event_id)
            target = accepted_event_ids if is_new else duplicate_event_ids
            target.append(event.event_id)

        for event in performance_events:
            candidates = binding_candidates.get((event.application_id, event.app_session_id), set())
            if len(candidates) == 1:
                try:
                    is_new, _publish_payload = ingest_performance_event(
                        client,
                        event,
                        device_id,
                        next(iter(candidates)),
                    )
                except ValueError:
                    rejected.append({"event_id": event.event_id, "code": "INVALID_EVENT"})
                    continue
            else:
                is_new = claim_event_once(client, event.event_id)
            target = accepted_event_ids if is_new else duplicate_event_ids
            target.append(event.event_id)
    return {
        "accepted_event_ids": accepted_event_ids,
        "duplicate_event_ids": duplicate_event_ids,
        "rejected": rejected,
    }
