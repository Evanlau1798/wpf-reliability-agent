import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import sleep
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import SecretStr

from app import firestore_client
from app.approval import ApprovalDecisionRequest
from app.auth import (
    OPERATOR_CSRF_COOKIE,
    OPERATOR_SESSION_COOKIE,
    OPERATOR_SESSION_MAX_AGE_SECONDS,
    authenticate_device_token,
    authenticate_operator_session,
    authenticate_operator_token,
    create_operator_session_value,
    validate_operator_csrf,
)
from app.commands import (
    CommandLeaseRequest,
    complete_command_once,
    lease_next_command,
)
from app.config import Settings
from app.dashboard import render_incident_detail, render_incident_list, render_report_download
from app.firestore_client import claim_event_once, get_firestore_client, is_run_processed
from app.ingest import (
    ingest_binding_event,
    ingest_performance_event,
    ingest_recovery_event,
    validate_telemetry_events,
)
from app.logging_config import configure_logging
from app.models import CommandResult, EventType
from app.pubsub import publish_work
from app.verification import (
    build_inconclusive_verification_audit,
    build_regression_verification_audit,
    build_verification_audit,
    evaluate_post_action_verification,
    is_regression,
    meets_mitigation_thresholds,
    recovery_evidence_binding,
)
from app.worker import build_run_key, decode_pubsub_envelope, pubsub_message_id
from app.worker_auth import authenticate_pubsub_push
from app.workflow import INVESTIGATING_TRIGGER, REPORTING_TRIGGER, REPORT_TRIGGER, WORKFLOW_TRANSITIONS, commit_transition_run, continuation_payload, load_incident_evidence, load_incident_workflow_state, load_rollback_guidance, run_investigation_step
from app.workflow_reporting import commit_terminal_reporting_run, run_reporting_step
from app.workflow_state import IncidentState, commit_new_incident_run, commit_verification_run

MAX_TELEMETRY_BATCH_BYTES = 512 * 1024


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.settings = Settings.from_environment()
    application.state.logger = configure_logging(application.state.settings.service_role)
    yield


app = FastAPI(title="WPF Reliability Agent", lifespan=lifespan)

@app.get("/health", include_in_schema=False)
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
        max_age=OPERATOR_SESSION_MAX_AGE_SECONDS,
    )
    response.set_cookie(
        key=OPERATOR_CSRF_COOKIE,
        value=secrets.token_urlsafe(32),
        httponly=False,
        secure=True,
        samesite="strict",
        path="/",
        max_age=OPERATOR_SESSION_MAX_AGE_SECONDS,
    )
    return response


@app.get("/console/incidents", response_class=HTMLResponse)
def console_incidents(
    request: Request,
    _operator_id: Annotated[str, Depends(authenticate_operator_session)],
) -> str:
    client = get_firestore_client(request.app.state.settings.google_cloud_project)
    return render_incident_list(client)


@app.get("/console/incidents/{incident_id}", response_class=HTMLResponse)
def console_incident_detail(
    request: Request,
    incident_id: str,
    _operator_id: Annotated[str, Depends(authenticate_operator_session)],
) -> str:
    client = get_firestore_client(request.app.state.settings.google_cloud_project)
    rendered = render_incident_detail(client, incident_id)
    if rendered is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return rendered


@app.get("/console/incidents/{incident_id}/reports/{version}.{report_format}", response_model=None)
def console_report_download(
    request: Request,
    incident_id: str,
    version: str,
    report_format: str,
    _operator_id: Annotated[str, Depends(authenticate_operator_session)],
) -> Response:
    client = get_firestore_client(request.app.state.settings.google_cloud_project)
    rendered = render_report_download(client, incident_id, version, report_format)
    if rendered is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    content, media_type = rendered
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="incident-report.{report_format}"'},
    )


@app.post("/v1/approvals/{approval_id}:decide")
def decide_approval(
    request: Request,
    approval_id: str,
    decision: ApprovalDecisionRequest,
    operator_id: Annotated[str, Depends(authenticate_operator_session)],
    _csrf: Annotated[None, Depends(validate_operator_csrf)],
) -> dict[str, str]:
    client = get_firestore_client(request.app.state.settings.google_cloud_project)
    try:
        if decision.decision == "approve":
            firestore_client.approve_pending_approval(
                client,
                approval_id=approval_id,
                actor=operator_id,
                now=datetime.now(UTC),
            )
        else:
            _, incident_id, evidence_revision = firestore_client.reject_pending_approval(
                client,
                approval_id=approval_id,
                actor=operator_id,
                now=datetime.now(UTC),
            )
            _publish_worker_continuation(request, {"incident_id": incident_id, "evidence_revision": evidence_revision, "event_id": approval_id}, {"state": IncidentState.REJECTED.value})
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval decision rejected",
        ) from exc
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
    for attempt in range(lease_request.wait_seconds + 1):
        command = lease_next_command(
            client,
            app_session_id=lease_request.app_session_id,
            lease_owner=authenticated_device_id,
            now=datetime.now(UTC),
            duration=timedelta(seconds=30),
        )
        if command is not None:
            return command
        if attempt < lease_request.wait_seconds:
            sleep(1)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    request.app.state.logger.info("command_completed incident_id=%s command_id=%s idempotent=%s", result.incident_id, command_id, str(idempotent).lower())
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
    request.app.state.logger.info("worker_run incident_id=%s trigger=%s event_id=%s", work["incident_id"], work["trigger"], work["event_id"])

    run_key = build_run_key(
        work["incident_id"],
        work["evidence_revision"],
        work["trigger"],
    )
    client = get_firestore_client(request.app.state.settings.google_cloud_project)
    if is_run_processed(client, run_key):
        _publish_worker_continuation(request, work, load_incident_workflow_state(client, work["incident_id"]))
        request.app.state.logger.info("worker_duplicate_run run_key=%s", run_key)
        return
    if work["trigger"] == EventType.RECOVERY_RESULT.value:
        evidence = load_incident_evidence(client, work["incident_id"])
        verification = evaluate_post_action_verification(evidence, work["event_id"])
        if verification is None:
            binding = recovery_evidence_binding(evidence, work["event_id"])
            if binding is None:
                raise ValueError("Post-action verification binding is invalid")
            command_id, action_id = binding
            target_state = IncidentState.INVESTIGATING
            audit = build_inconclusive_verification_audit(
                work["event_id"], command_id, action_id
            )
        elif meets_mitigation_thresholds(
            verification.binding,
            verification.performance,
            verification.visual,
        ):
            command_id = verification.command_id
            action_id = verification.action_id
            target_state = IncidentState.MITIGATED
            audit = build_verification_audit(verification, target_state.value)
        elif is_regression(
            verification.binding,
            verification.performance,
            verification.visual,
        ):
            command_id = verification.command_id
            action_id = verification.action_id
            rollback_guidance = load_rollback_guidance(
                client, work["incident_id"], command_id
            )
            if rollback_guidance is None:
                raise ValueError("Rollback guidance is unavailable")
            target_state = IncidentState.FAILED_SAFE
            audit = build_regression_verification_audit(verification, rollback_guidance)
        else:
            command_id = verification.command_id
            action_id = verification.action_id
            target_state = IncidentState.INVESTIGATING
            audit = build_inconclusive_verification_audit(
                work["event_id"], command_id, action_id, verification
            )
        committed = commit_verification_run(
            client,
            run_key=run_key,
            incident_id=work["incident_id"],
            evidence_revision=work["evidence_revision"],
            command_id=command_id,
            action_id=action_id,
            target_state=target_state,
            verification=audit,
        )
        if committed:
            _publish_worker_continuation(request, work, {"state": target_state.value})
        else:
            request.app.state.logger.info("worker_duplicate_run run_key=%s", run_key)
        return
    transition = WORKFLOW_TRANSITIONS.get(work["trigger"])
    if transition is not None:
        expected_state, target_state = transition
        committed = commit_transition_run(
            client, run_key=run_key, incident_id=work["incident_id"], evidence_revision=work["evidence_revision"],
            trigger=work["trigger"], model_id=request.app.state.settings.gemini_model,
            expected_state=expected_state, target_state=target_state,
        )
        if committed:
            _publish_worker_continuation(request, work, {"state": target_state.value})
        return
    if work["trigger"] == INVESTIGATING_TRIGGER:
        target_state = await run_investigation_step(client, work=work, run_key=run_key, model_id=request.app.state.settings.gemini_model)
        if target_state is not None:
            _publish_worker_continuation(request, work, {"state": target_state.value})
        return
    if work["trigger"] == REPORTING_TRIGGER:
        committed = commit_terminal_reporting_run(
            client,
            run_key=run_key,
            incident_id=work["incident_id"],
            evidence_revision=work["evidence_revision"],
            trigger=work["trigger"],
            model_id=request.app.state.settings.gemini_model,
        )
        if committed:
            _publish_worker_continuation(request, work, {"state": IncidentState.REPORTING.value})
        return
    if work["trigger"] == REPORT_TRIGGER:
        await run_reporting_step(client, work=work, run_key=run_key, model_id=request.app.state.settings.gemini_model)
        return
    incident = load_incident_workflow_state(client, work["incident_id"])
    if incident.get("state") != IncidentState.NEW.value:
        _publish_worker_continuation(request, work, incident)
        return
    committed = commit_new_incident_run(
        client,
        run_key=run_key,
        incident_id=work["incident_id"],
        evidence_revision=work["evidence_revision"],
        trigger=work["trigger"],
        model_id=request.app.state.settings.gemini_model,
    )
    if committed:
        _publish_worker_continuation(request, work, {"state": IncidentState.TRIAGING.value})
    else:
        request.app.state.logger.info("worker_duplicate_run run_key=%s", run_key)


def _publish_worker_continuation(request: Request, work: dict[str, object], incident: dict[str, object]) -> None:
    payload = continuation_payload(work, incident)
    if payload is not None:
        settings = request.app.state.settings
        publish_work(settings.google_cloud_project, settings.pubsub_topic, payload)


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
            elif event.event_type is EventType.RECOVERY_RESULT:
                try:
                    is_new, _publish_payload = ingest_recovery_event(client, event, device_id)
                except ValueError:
                    rejected.append({"event_id": event.event_id, "code": "INVALID_EVENT"})
                    continue
                if _publish_payload is not None:
                    _publish_after_commit(request, _publish_payload)
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
