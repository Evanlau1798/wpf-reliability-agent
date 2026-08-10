import json
from datetime import UTC, datetime, timedelta

from google.cloud import firestore

from app.agent import (
    build_evidence_command,
    create_investigator_runner,
    run_investigator_once,
)
from app.audit import build_tool_request_audit
from app.commands import command_document
from app.correlation import (
    BindingCandidate,
    NormalizedEvidenceSummary,
    build_agent_context,
    correlate_binding_incident,
)
from app.firestore_client import (
    APPROVALS_COLLECTION,
    AUDIT_COLLECTION,
    COMMANDS_COLLECTION,
    EVIDENCE_COLLECTION,
    INCIDENTS_COLLECTION,
    PROCESSED_RUNS_COLLECTION,
)
from app.models import AgentDecision, DecisionType, DiagnosticCommand
from app.workflow_approval import commit_proposed_action_run
from app.workflow_reporting import (
    commit_inconclusive_reporting_run,
    commit_reporting_decision_run,
)
from app.workflow_state import (
    MAX_INVESTIGATION_ROUNDS,
    MAX_NO_NEW_EVIDENCE_ROUNDS,
    MAX_READ_ONLY_TOOL_CALLS,
    IncidentState,
    acquire_incident_lease,
    canonical_tool_request_key,
    release_incident_lease,
    transition_incident_in_transaction,
)

TRIAGING_TRIGGER = "workflow.triaging"
COLLECTING_TRIGGER = "workflow.collecting_evidence"
INVESTIGATING_TRIGGER = "workflow.investigating"
REPORTING_TRIGGER = "workflow.reporting"
REPORT_TRIGGER = "workflow.report"
WORKFLOW_TRANSITIONS = {
    TRIAGING_TRIGGER: (IncidentState.TRIAGING, IncidentState.COLLECTING_EVIDENCE),
    COLLECTING_TRIGGER: (IncidentState.COLLECTING_EVIDENCE, IncidentState.INVESTIGATING),
}
NON_RETRYABLE_INVESTIGATION_ERRORS = {
    "Duplicate tool request",
    "Investigation round limit reached",
    "No-new-evidence limit reached",
    "Read-only tool call limit reached",
}


def _commit_inconclusive_step(
    client: firestore.Client,
    *,
    run_key: str,
    incident_id: str,
    evidence_revision: int,
    trigger: str,
    model_id: str,
    reason: str,
) -> IncidentState | None:
    committed = commit_inconclusive_reporting_run(
        client,
        run_key=run_key,
        incident_id=incident_id,
        evidence_revision=evidence_revision,
        trigger=trigger,
        model_id=model_id,
        reason=reason,
    )
    return IncidentState.REPORTING if committed else None


async def run_investigation_step(
    client: firestore.Client,
    *,
    work: dict[str, object],
    run_key: str,
    model_id: str,
) -> IncidentState | None:
    incident_id = work.get("incident_id")
    evidence_revision = work.get("evidence_revision")
    trigger = work.get("trigger")
    if not isinstance(incident_id, str) or type(evidence_revision) is not int or not isinstance(trigger, str):
        raise ValueError("Investigation work is invalid")
    now = datetime.now(UTC)
    if not acquire_incident_lease(
        client,
        incident_id=incident_id,
        owner=run_key,
        now=now,
        duration=timedelta(seconds=110),
    ):
        raise RuntimeError("Incident lease is busy")
    try:
        incident = load_incident_workflow_state(client, incident_id)
        if incident.get("state") != IncidentState.INVESTIGATING.value:
            return
        app_session_id = incident.get("app_session_id")
        if not isinstance(app_session_id, str) or not app_session_id:
            raise ValueError("Incident app session is invalid")
        round_count = incident.get("investigation_round_count", 0)
        tool_count = incident.get("read_only_tool_call_count", 0)
        last_revision = incident.get("last_investigated_evidence_revision")
        no_new_count = incident.get("no_new_evidence_count", 0)
        limit_reason = None
        if type(round_count) is int and round_count >= MAX_INVESTIGATION_ROUNDS:
            limit_reason = "Investigation round limit reached"
        elif type(tool_count) is int and tool_count >= MAX_READ_ONLY_TOOL_CALLS:
            limit_reason = "Read-only tool call limit reached"
        elif last_revision == evidence_revision and type(no_new_count) is int and no_new_count + 1 >= MAX_NO_NEW_EVIDENCE_ROUNDS:
            limit_reason = "No-new-evidence limit reached"
        if limit_reason is not None:
            return _commit_inconclusive_step(
                client, run_key=run_key, incident_id=incident_id, evidence_revision=evidence_revision,
                trigger=trigger, model_id=model_id, reason=limit_reason,
            )
        context = build_investigator_context(client, incident_id=incident_id, incident=incident)
        try:
            decision = await run_investigator_once(
                create_investigator_runner(model_id), incident_id=incident_id, run_key=run_key, context=context
            )
        except ValueError:
            return _commit_inconclusive_step(
                client, run_key=run_key, incident_id=incident_id, evidence_revision=evidence_revision,
                trigger=trigger, model_id=model_id, reason="Investigator output remained invalid",
            )
        if decision.decision is DecisionType.PROPOSE_ACTION:
            commit_proposed_action_run(
                client,
                run_key=run_key,
                incident_id=incident_id,
                evidence_revision=evidence_revision,
                trigger=trigger,
                model_id=model_id,
                decision=decision,
                now=now,
            )
            return
        if decision.decision in {DecisionType.FINALIZE, DecisionType.NO_ACTION}:
            committed = commit_reporting_decision_run(
                client,
                run_key=run_key,
                incident_id=incident_id,
                evidence_revision=evidence_revision,
                trigger=trigger,
                model_id=model_id,
                decision=decision,
            )
            return IncidentState.REPORTING if committed else None
        if decision.decision is not DecisionType.REQUEST_EVIDENCE:
            raise ValueError("Investigator decision is not wired to a durable outcome")
        try:
            command = build_evidence_command(
                decision, incident_id=incident_id, evidence_revision=evidence_revision,
                app_session_id=app_session_id, context=context, now=now,
            )
        except ValueError:
            return _commit_inconclusive_step(
                client, run_key=run_key, incident_id=incident_id, evidence_revision=evidence_revision,
                trigger=trigger, model_id=model_id, reason="Investigator requested invalid evidence",
            )
        try:
            commit_request_evidence_run(
                client, run_key=run_key, incident_id=incident_id, evidence_revision=evidence_revision,
                trigger=trigger, model_id=model_id, decision=decision, command=command,
            )
        except ValueError as exc:
            if str(exc) not in NON_RETRYABLE_INVESTIGATION_ERRORS:
                raise
            return _commit_inconclusive_step(
                client, run_key=run_key, incident_id=incident_id, evidence_revision=evidence_revision,
                trigger=trigger, model_id=model_id, reason=str(exc),
            )
    finally:
        release_incident_lease(client, incident_id=incident_id, owner=run_key)


def build_investigator_context(
    client: firestore.Client,
    *,
    incident_id: str,
    incident: dict[str, object],
):
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    normalized: list[NormalizedEvidenceSummary] = []
    live_candidates: list[BindingCandidate] = []
    for snapshot in incident_document.collection(EVIDENCE_COLLECTION).stream():
        data = snapshot.to_dict() or {}
        normalized.append(_normalize_evidence(str(snapshot.id), data))
        live_candidates.extend(_live_binding_candidates(data))
    binding = next((item for item in normalized if item.kind == "binding.aggregate"), None)
    claims = (
        correlate_binding_incident(
            binding,
            [item for item in normalized if item is not binding],
            live_candidates,
        ).candidate_claims
        if binding is not None
        else []
    )
    tool_count = incident.get("read_only_tool_call_count")
    if type(tool_count) is not int or not 0 <= tool_count <= MAX_READ_ONLY_TOOL_CALLS:
        raise ValueError("Read-only tool call count is invalid")
    return build_agent_context(
        normalized,
        claims,
        tool_calls_remaining=MAX_READ_ONLY_TOOL_CALLS - tool_count,
        max_context_bytes=65_536,
        max_context_tokens=32_768,
    )


def _normalize_evidence(evidence_id: str, data: dict[str, object]) -> NormalizedEvidenceSummary:
    event_type = data.get("event_type")
    correlation = data.get("correlation") if isinstance(data.get("correlation"), dict) else {}
    if event_type == "tool.result":
        result_record = data.get("result") if isinstance(data.get("result"), dict) else {}
        payload = result_record.get("result") if isinstance(result_record.get("result"), dict) else {}
        observed_at = result_record.get("completed_at_utc") or data.get("created_at")
        kind = data.get("tool")
    else:
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        observed_at = data.get("timestamp_utc") or data.get("created_at")
        kind = event_type
    app_session_id = data.get("app_session_id")
    if not isinstance(kind, str) or not kind or not isinstance(app_session_id, str) or not app_session_id or observed_at is None:
        raise ValueError("Incident evidence metadata is invalid")
    statistics = payload.get("frame_statistics") if isinstance(payload.get("frame_statistics"), dict) else {}
    aggregation_ms = payload.get("aggregation_window_ms")
    nodes = payload.get("nodes")
    return NormalizedEvidenceSummary(
        evidence_id=evidence_id,
        kind=kind,
        app_session_id=app_session_id,
        observed_at_utc=observed_at,
        summary=(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)[:4096] or kind),
        element_id=correlation.get("element_id") if isinstance(correlation.get("element_id"), str) else None,
        binding_path=_first_string(payload, correlation, "binding_path"),
        element_name=payload.get("element_name") if isinstance(payload.get("element_name"), str) else None,
        element_type=payload.get("element_type") if isinstance(payload.get("element_type"), str) else None,
        occurrence_count=payload.get("occurrence_count") if type(payload.get("occurrence_count")) is int else None,
        window_seconds=(aggregation_ms / 1000 if isinstance(aggregation_ms, (int, float)) and aggregation_ms > 0 else None),
        frame_p95_ms=_first_number(statistics, "p95_ms", "p95_milliseconds", "P95Milliseconds"),
        visual_count=payload.get("visual_count") if type(payload.get("visual_count")) is int else None,
        subtree_node_count=len(nodes) if isinstance(nodes, list) else None,
    )


def _live_binding_candidates(data: dict[str, object]) -> list[BindingCandidate]:
    if data.get("event_type") != "tool.result" or data.get("tool") != "binding.get_live_candidates":
        return []
    result_record = data.get("result") if isinstance(data.get("result"), dict) else {}
    payload = result_record.get("result") if isinstance(result_record.get("result"), dict) else {}
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return []
    return [BindingCandidate.model_validate(item) for item in candidates if isinstance(item, dict)]


def _first_string(*sources_and_key: object) -> str | None:
    *sources, key = sources_and_key
    for source in sources:
        if isinstance(source, dict) and isinstance(source.get(key), str):
            return source[key]
    return None


def _first_number(source: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def load_incident_workflow_state(client: firestore.Client, incident_id: str) -> dict[str, object]:
    snapshot = client.collection(INCIDENTS_COLLECTION).document(incident_id).get()
    if not snapshot.exists:
        raise ValueError("Incident does not exist")
    return snapshot.to_dict() or {}


def load_incident_evidence(client: object, incident_id: str) -> list[dict[str, object]]:
    incident = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    return [
        {"evidence_id": snapshot.id, **(snapshot.to_dict() or {})}
        for snapshot in incident.collection(EVIDENCE_COLLECTION).stream()
    ]


def load_rollback_guidance(client: object, incident_id: str, command_id: str) -> str | None:
    command = client.collection(COMMANDS_COLLECTION).document(command_id).get()
    if not command.exists:
        return None
    approval_id = (command.to_dict() or {}).get("approval_id")
    if not isinstance(approval_id, str) or not approval_id:
        return None
    approval = client.collection(INCIDENTS_COLLECTION).document(incident_id).collection(
        APPROVALS_COLLECTION
    ).document(approval_id).get()
    if not approval.exists:
        return None
    rollback_guidance = (approval.to_dict() or {}).get("rollback_plan")
    return rollback_guidance if isinstance(rollback_guidance, str) and rollback_guidance else None


def continuation_payload(
    work: dict[str, object],
    incident: dict[str, object],
) -> dict[str, object] | None:
    if incident.get("state") == IncidentState.COLLECTING_EVIDENCE.value and incident.get("pending_command_id") is not None:
        return None
    trigger = {
        IncidentState.TRIAGING.value: TRIAGING_TRIGGER,
        IncidentState.COLLECTING_EVIDENCE.value: COLLECTING_TRIGGER,
        IncidentState.INVESTIGATING.value: INVESTIGATING_TRIGGER,
        IncidentState.MITIGATED.value: REPORTING_TRIGGER,
        IncidentState.REJECTED.value: REPORTING_TRIGGER,
        IncidentState.FAILED_SAFE.value: REPORTING_TRIGGER,
        IncidentState.REPORTING.value: REPORT_TRIGGER,
    }.get(incident.get("state"))
    if trigger is None:
        return None
    evidence_revision = incident.get("evidence_revision")
    if type(evidence_revision) is not int:
        evidence_revision = work["evidence_revision"]
    return {
        "incident_id": work["incident_id"],
        "evidence_revision": evidence_revision,
        "trigger": trigger,
        "event_id": work["event_id"],
    }


def commit_transition_run(
    client: firestore.Client,
    *,
    run_key: str,
    incident_id: str,
    evidence_revision: int,
    trigger: str,
    model_id: str,
    expected_state: IncidentState,
    target_state: IncidentState,
) -> bool:
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    processed_document = client.collection(PROCESSED_RUNS_COLLECTION).document(run_key)

    @firestore.transactional
    def commit(transaction: firestore.Transaction) -> bool:
        if processed_document.get(transaction=transaction).exists:
            return False
        incident = incident_document.get(transaction=transaction).to_dict() or {}
        state_version = incident.get("state_version")
        if type(state_version) is not int:
            raise ValueError("Incident state version is invalid")
        transition_incident_in_transaction(
            transaction,
            incident_document=incident_document,
            expected_state=expected_state,
            expected_version=state_version,
            target_state=target_state,
        )
        transaction.create(
            processed_document,
            {
                "incident_id": incident_id,
                "evidence_revision": evidence_revision,
                "trigger": trigger,
                "model_id": model_id,
                "processed_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return True

    return commit(client.transaction())


def commit_request_evidence_run(
    client: firestore.Client,
    *,
    run_key: str,
    incident_id: str,
    evidence_revision: int,
    trigger: str,
    model_id: str,
    decision: AgentDecision,
    command: DiagnosticCommand,
) -> bool:
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    command_ref = client.collection(COMMANDS_COLLECTION).document(command.command_id)
    processed_document = client.collection(PROCESSED_RUNS_COLLECTION).document(run_key)

    @firestore.transactional
    def commit(transaction: firestore.Transaction) -> bool:
        if processed_document.get(transaction=transaction).exists:
            return False
        snapshot = incident_document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        incident = snapshot.to_dict() or {}
        state_version = incident.get("state_version")
        round_count = incident.get("investigation_round_count")
        tool_count = incident.get("read_only_tool_call_count")
        request_keys = incident.get("read_only_tool_request_keys")
        last_revision = incident.get("last_investigated_evidence_revision")
        no_new_count = incident.get("no_new_evidence_count")
        if type(state_version) is not int:
            raise ValueError("Incident state version is invalid")
        if type(round_count) is not int or not 0 <= round_count < MAX_INVESTIGATION_ROUNDS:
            raise ValueError("Investigation round limit reached")
        if type(tool_count) is not int or not 0 <= tool_count < MAX_READ_ONLY_TOOL_CALLS:
            raise ValueError("Read-only tool call limit reached")
        if not isinstance(request_keys, list) or not all(isinstance(key, str) and key for key in request_keys):
            raise ValueError("Read-only tool request keys are invalid")
        if last_revision is not None and (type(last_revision) is not int or last_revision < 0):
            raise ValueError("Last investigated evidence revision is invalid")
        if type(no_new_count) is not int or no_new_count < 0:
            raise ValueError("No-new-evidence count is invalid")
        if last_revision is not None and evidence_revision < last_revision:
            raise ValueError("Evidence revision is stale")
        request_key = canonical_tool_request_key(command.tool.value, command.arguments)
        if request_key in request_keys:
            raise ValueError("Duplicate tool request")
        next_no_new = 0 if last_revision is None or evidence_revision > last_revision else no_new_count + 1
        if next_no_new >= MAX_NO_NEW_EVIDENCE_ROUNDS:
            raise ValueError("No-new-evidence limit reached")
        _, state_audit = transition_incident_in_transaction(
            transaction,
            incident_document=incident_document,
            expected_state=IncidentState.INVESTIGATING,
            expected_version=state_version,
            target_state=IncidentState.COLLECTING_EVIDENCE,
        )
        audit_head = {"audit_sequence": state_audit["sequence"], "audit_entry_hash": state_audit["entry_hash"]}
        tool_audit = build_tool_request_audit(audit_head, tool=command.tool.value, request_hash=request_key)
        transaction.create(command_ref, command_document(command))
        transaction.update(
            incident_document,
            {
                "investigation_round_count": round_count + 1,
                "read_only_tool_call_count": tool_count + 1,
                "read_only_tool_request_keys": [*request_keys, request_key],
                "last_investigated_evidence_revision": evidence_revision,
                "no_new_evidence_count": next_no_new,
                "current_hypotheses": [item.model_dump(mode="json") for item in decision.hypotheses],
                "pending_command_id": command.command_id,
                "audit_sequence": tool_audit["sequence"],
                "audit_entry_hash": tool_audit["entry_hash"],
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        transaction.create(
            incident_document.collection(AUDIT_COLLECTION).document(str(tool_audit["sequence"])),
            tool_audit,
        )
        transaction.create(
            processed_document,
            {
                "incident_id": incident_id,
                "evidence_revision": evidence_revision,
                "trigger": trigger,
                "model_id": model_id,
                "processed_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return True

    return commit(client.transaction())
