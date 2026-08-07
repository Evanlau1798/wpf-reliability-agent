import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import SecretStr

from app.auth import (
    OPERATOR_CSRF_COOKIE,
    OPERATOR_SESSION_COOKIE,
    authenticate_device_token,
    authenticate_operator_session,
    authenticate_operator_token,
    create_operator_session_value,
    validate_operator_csrf,
)
from app.approval import ApprovalDecisionRequest
from app.commands import (
    CommandLeaseRequest,
    complete_command_once,
    lease_next_command,
)
from app.config import Settings
from app.firestore_client import claim_event_once, get_firestore_client, is_run_processed
from app.ingest import ingest_binding_event, ingest_performance_event, validate_telemetry_events
from app.logging_config import configure_logging
from app.models import CommandResult, EventType
from app.pubsub import publish_work
from app.worker import build_run_key, decode_pubsub_envelope, pubsub_message_id
from app.worker_auth import authenticate_pubsub_push
from app.workflow_state import commit_new_incident_run


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


@app.post("/console/login", status_code=status.HTTP_204_NO_CONTENT)
def operator_login(
    secret: Annotated[SecretStr, Depends(authenticate_operator_token)],
) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        key=OPERATOR_SESSION_COOKIE,
        value=create_operator_session_value(secret),
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        key=OPERATOR_CSRF_COOKIE,
        value=secrets.token_urlsafe(32),
        httponly=False,
        secure=True,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/v1/approvals/{approval_id}:decide")
def decide_approval(
    approval_id: str,
    decision: ApprovalDecisionRequest,
    _: Annotated[str, Depends(authenticate_operator_session)],
    _csrf: Annotated[None, Depends(validate_operator_csrf)],
) -> dict[str, str]:
    return {"approval_id": approval_id, "decision": decision.decision}


@app.post(
    "/v1/devices/{device_id}/commands:lease",
    response_model=None,
)
def lease_command(
    request: Request,
    device_id: str,
    lease_request: CommandLeaseRequest,
    authenticated_device_id: Annotated[str, Depends(authenticate_device_token)],
) -> object:
    if device_id != authenticated_device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    client = get_firestore_client(request.app.state.settings.google_cloud_project)
    command = lease_next_command(
        client,
        app_session_id=lease_request.app_session_id,
        lease_owner=authenticated_device_id,
        now=datetime.now(UTC),
        duration=timedelta(seconds=30),
    )
    if command is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return command


@app.post(
    "/v1/commands/{command_id}:complete",
    response_model=None,
)
def complete_command(
    request: Request,
    command_id: str,
    result: CommandResult,
    authenticated_device_id: Annotated[str, Depends(authenticate_device_token)],
) -> object:
    client = get_firestore_client(request.app.state.settings.google_cloud_project)
    try:
        idempotent, evidence_revision = complete_command_once(
            client,
            command_id=command_id,
            lease_owner=authenticated_device_id,
            result=result,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Command completion rejected",
        ) from exc
    _publish_after_commit(
        request,
        {
            "incident_id": result.incident_id,
            "evidence_revision": evidence_revision,
            "trigger": "TOOL_RESULT_RECEIVED",
            "event_id": command_id,
        },
    )
    return {"accepted": True, "idempotent": idempotent}


@app.post("/v1/work:push", status_code=status.HTTP_204_NO_CONTENT)
async def worker_push(request: Request) -> None:
    if request.app.state.settings.service_role != "worker":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    authenticate_pubsub_push(request)
    envelope: object = None
    try:
        envelope = await request.json()
        work = decode_pubsub_envelope(envelope)
    except ValueError:
        request.app.state.logger.warning(
            "worker_message_rejected message_id=%s reason=malformed",
            pubsub_message_id(envelope),
        )
        return

    run_key = build_run_key(
        work["incident_id"],
        work["evidence_revision"],
        work["trigger"],
    )
    client = get_firestore_client(request.app.state.settings.google_cloud_project)
    if is_run_processed(client, run_key):
        request.app.state.logger.info("worker_duplicate_run run_key=%s", run_key)
        return
    committed = commit_new_incident_run(
        client,
        run_key=run_key,
        incident_id=work["incident_id"],
        evidence_revision=work["evidence_revision"],
        trigger=work["trigger"],
        model_id=request.app.state.settings.gemini_model,
    )
    if not committed:
        request.app.state.logger.info("worker_duplicate_run run_key=%s", run_key)


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
                if _publish_payload is not None:
                    _publish_after_commit(request, _publish_payload)
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
                if _publish_payload is not None:
                    _publish_after_commit(request, _publish_payload)
            else:
                is_new = claim_event_once(client, event.event_id)
            target = accepted_event_ids if is_new else duplicate_event_ids
            target.append(event.event_id)
    return {
        "accepted_event_ids": accepted_event_ids,
        "duplicate_event_ids": duplicate_event_ids,
        "rejected": rejected,
    }


def _publish_after_commit(request: Request, payload: dict[str, object]) -> None:
    settings = request.app.state.settings
    try:
        publish_work(settings.google_cloud_project, settings.pubsub_topic, payload)
    except Exception:
        request.app.state.logger.error(
            "pubsub_publish_failed incident_id=%s evidence_revision=%s event_id=%s",
            payload.get("incident_id"),
            payload.get("evidence_revision"),
            payload.get("event_id"),
        )
