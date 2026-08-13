import json
from datetime import UTC, datetime, timedelta

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app import __version__
from app.agent import decision_for_reporting
from app.contracts import sha256_canonical
from app.firestore_client import (
    APPROVALS_COLLECTION,
    AUDIT_COLLECTION,
    COMMANDS_COLLECTION,
    EVIDENCE_COLLECTION,
    INCIDENTS_COLLECTION,
    PROCESSED_RUNS_COLLECTION,
    REPORTS_COLLECTION,
)
from app.models import AgentDecision, IncidentReport, Severity
from app.policy import POLICY_VERSION
from app.reporting import (
    FinalizedReporterRecord,
    ReporterInput,
    create_reporter_runner,
    run_reporter_once,
)
from app.workflow_state import (
    MAX_INVESTIGATION_ROUNDS,
    IncidentState,
    acquire_incident_lease,
    release_incident_lease,
    transition_incident_in_transaction,
)

TERMINAL_REPORTING_STATES = {
    IncidentState.MITIGATED,
    IncidentState.REJECTED,
    IncidentState.FAILED_SAFE,
}
REPORT_VERSION = "1"
REPORT_PROMPT_VERSION = "1"
REUSE_REVISION = "900ac97cf9b69b4a3c1f4899b08c9b1e78212af3"


def commit_reporting_decision_run(
    client: firestore.Client,
    *,
    run_key: str,
    incident_id: str,
    evidence_revision: int,
    trigger: str,
    model_id: str,
    decision: AgentDecision,
) -> bool:
    decision = decision_for_reporting(decision)
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
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
        last_revision = incident.get("last_investigated_evidence_revision")
        no_new_count = incident.get("no_new_evidence_count")
        if type(state_version) is not int:
            raise ValueError("Incident state version is invalid")
        if type(round_count) is not int or not 0 <= round_count < MAX_INVESTIGATION_ROUNDS:
            raise ValueError("Investigation round limit reached")
        if last_revision is not None and (type(last_revision) is not int or last_revision < 0):
            raise ValueError("Last investigated evidence revision is invalid")
        if type(no_new_count) is not int or no_new_count < 0:
            raise ValueError("No-new-evidence count is invalid")
        if last_revision is not None and evidence_revision < last_revision:
            raise ValueError("Evidence revision is stale")
        transition_incident_in_transaction(
            transaction,
            incident_document=incident_document,
            expected_state=IncidentState.INVESTIGATING,
            expected_version=state_version,
            target_state=IncidentState.REPORTING,
        )
        next_no_new = 0 if last_revision is None or evidence_revision > last_revision else no_new_count + 1
        transaction.update(
            incident_document,
            {
                "investigation_round_count": round_count + 1,
                "last_investigated_evidence_revision": evidence_revision,
                "no_new_evidence_count": next_no_new,
                "current_hypotheses": [item.model_dump(mode="json") for item in decision.hypotheses],
                "investigation_outcome": decision.decision.value,
                "investigation_stop_reason": decision.stop_reason or decision.decision.value,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
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


def commit_inconclusive_reporting_run(
    client: firestore.Client,
    *,
    run_key: str,
    incident_id: str,
    evidence_revision: int,
    trigger: str,
    model_id: str,
    reason: str,
) -> bool:
    if not reason or len(reason) > 1024:
        raise ValueError("Investigation stop reason is invalid")
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
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
        if type(state_version) is not int:
            raise ValueError("Incident state version is invalid")
        transition_incident_in_transaction(
            transaction,
            incident_document=incident_document,
            expected_state=IncidentState.INVESTIGATING,
            expected_version=state_version,
            target_state=IncidentState.REPORTING,
        )
        transaction.update(
            incident_document,
            {
                "investigation_outcome": "INCONCLUSIVE",
                "investigation_stop_reason": reason,
                "manual_review_required": True,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
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


def commit_terminal_reporting_run(
    client: firestore.Client,
    *,
    run_key: str,
    incident_id: str,
    evidence_revision: int,
    trigger: str,
    model_id: str,
) -> bool:
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    processed_document = client.collection(PROCESSED_RUNS_COLLECTION).document(run_key)

    @firestore.transactional
    def commit(transaction: firestore.Transaction) -> bool:
        if processed_document.get(transaction=transaction).exists:
            return False
        snapshot = incident_document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        incident = snapshot.to_dict() or {}
        try:
            state = IncidentState(incident.get("state"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Incident state is invalid") from exc
        state_version = incident.get("state_version")
        if state not in TERMINAL_REPORTING_STATES:
            raise ValueError("Incident is not ready for reporting")
        if type(state_version) is not int:
            raise ValueError("Incident state version is invalid")
        transition_incident_in_transaction(
            transaction,
            incident_document=incident_document,
            expected_state=state,
            expected_version=state_version,
            target_state=IncidentState.REPORTING,
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


def build_reporter_input(client: firestore.Client, incident_id: str) -> ReporterInput:
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    evidence = [
        _evidence_record(snapshot.id, snapshot.to_dict() or {})
        for snapshot in incident_document.collection(EVIDENCE_COLLECTION).stream()
    ]
    commands = client.collection(COMMANDS_COLLECTION).where(
        filter=FieldFilter("incident_id", "==", incident_id)
    ).stream()
    tools = [_tool_record(snapshot.id, snapshot.to_dict() or {}) for snapshot in commands]
    approvals = [
        _approval_record(snapshot.id, snapshot.to_dict() or {})
        for snapshot in incident_document.collection(APPROVALS_COLLECTION).stream()
    ]
    verification = [
        _verification_record(snapshot.id, data)
        for snapshot in incident_document.collection(AUDIT_COLLECTION).stream()
        if (data := snapshot.to_dict() or {}).get("type") == "mutation.verification"
    ]
    return ReporterInput(
        evidence=sorted(evidence, key=lambda item: item.reference),
        tools=sorted(tools, key=lambda item: item.reference),
        approvals=sorted(approvals, key=lambda item: item.reference),
        verification=sorted(verification, key=lambda item: item.timestamp_utc),
    )


def commit_report_run(
    client: firestore.Client,
    *,
    run_key: str,
    incident_id: str,
    evidence_revision: int,
    trigger: str,
    model_id: str,
    report: IncidentReport,
    version: str,
) -> bool:
    if report.incident_id != incident_id:
        raise ValueError("Report incident mismatch")
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    processed_document = client.collection(PROCESSED_RUNS_COLLECTION).document(run_key)
    report_document = incident_document.collection(REPORTS_COLLECTION).document(version)
    payload = report.model_dump(mode="json")
    payload["metadata"] = {
        **payload["metadata"],
        "report_sha256": sha256_canonical(report.model_dump(mode="json")),
    }

    @firestore.transactional
    def commit(transaction: firestore.Transaction) -> bool:
        if processed_document.get(transaction=transaction).exists:
            return False
        snapshot = incident_document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        state_version = (snapshot.to_dict() or {}).get("state_version")
        if type(state_version) is not int:
            raise ValueError("Incident state version is invalid")
        transition_incident_in_transaction(
            transaction,
            incident_document=incident_document,
            expected_state=IncidentState.REPORTING,
            expected_version=state_version,
            target_state=IncidentState.CLOSED,
        )
        transaction.create(report_document, payload)
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


async def run_reporting_step(
    client: firestore.Client,
    *,
    work: dict[str, object],
    run_key: str,
    model_id: str,
    build_revision: str,
) -> bool:
    incident_id = work.get("incident_id")
    evidence_revision = work.get("evidence_revision")
    trigger = work.get("trigger")
    if not isinstance(incident_id, str) or type(evidence_revision) is not int or not isinstance(trigger, str):
        raise ValueError("Reporting work is invalid")
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
        snapshot = client.collection(INCIDENTS_COLLECTION).document(incident_id).get()
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        incident = snapshot.to_dict() or {}
        if incident.get("state") != IncidentState.REPORTING.value:
            return False
        try:
            severity = Severity(incident.get("severity"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Incident severity is invalid") from exc
        reporter_input = build_reporter_input(client, incident_id)
        report = await run_reporter_once(
            create_reporter_runner(model_id),
            incident_id=incident_id,
            run_key=run_key,
            reporter_input=reporter_input,
            severity=severity,
            model_id=model_id,
            prompt_version=REPORT_PROMPT_VERSION,
            policy_version=POLICY_VERSION,
            reuse_revision=REUSE_REVISION,
        )
        report = report.model_copy(
            update={
                "metadata": report.metadata.model_copy(
                    update={"application_version": __version__, "build_revision": build_revision}
                )
            }
        )
        return commit_report_run(
            client,
            run_key=run_key,
            incident_id=incident_id,
            evidence_revision=evidence_revision,
            trigger=trigger,
            model_id=model_id,
            report=report,
            version=REPORT_VERSION,
        )
    finally:
        release_incident_lease(client, incident_id=incident_id, owner=run_key)


def _evidence_record(reference: str, data: dict[str, object]) -> FinalizedReporterRecord:
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else data.get("result")
    summary = payload if isinstance(payload, dict) else {"event_type": data.get("event_type")}
    related = [data["command_id"]] if isinstance(data.get("command_id"), str) else []
    return FinalizedReporterRecord(
        reference=str(reference),
        kind=str(data.get("tool") or data.get("event_type") or "evidence"),
        summary=_summary(summary),
        payload_hash=_hash(data.get("evidence_hash"), summary),
        related_ids=related,
        timestamp_utc=_timestamp(
            data.get("timestamp_utc"),
            _nested(data, "result", "completed_at_utc"),
            data.get("created_at"),
        ),
    )


def _tool_record(reference: str, data: dict[str, object]) -> FinalizedReporterRecord:
    summary = {
        key: data.get(key)
        for key in ("tool", "arguments_hash", "status", "result_hash", "approval_id", "action_id")
        if data.get(key) is not None
    }
    related = [
        value
        for key in ("approval_id", "action_id")
        if isinstance((value := data.get(key)), str) and value
    ]
    return FinalizedReporterRecord(
        reference=str(reference),
        kind=str(data.get("tool") or "tool"),
        summary=_summary(summary),
        payload_hash=_hash(data.get("result_hash"), summary),
        related_ids=related,
        timestamp_utc=_timestamp(
            _nested(data, "completion_result", "completed_at_utc"),
            data.get("issued_at_utc"),
            data.get("completed_at"),
        ),
    )


def _approval_record(reference: str, data: dict[str, object]) -> FinalizedReporterRecord:
    summary = {
        key: data.get(key)
        for key in (
            "status",
            "tool",
            "action_id",
            "canonical_arguments",
            "canonical_arguments_hash",
            "expected_effect",
            "rollback_plan",
            "approved_by",
        )
        if data.get(key) is not None
    }
    action_id = data.get("action_id")
    return FinalizedReporterRecord(
        reference=str(reference),
        kind="approval",
        summary=_summary(summary),
        payload_hash=sha256_canonical(_json_value(summary)),
        related_ids=[action_id] if isinstance(action_id, str) and action_id else [],
        timestamp_utc=_timestamp(data.get("approved_at_utc"), data.get("expires_at_utc")),
    )


def _verification_record(reference: str, data: dict[str, object]) -> FinalizedReporterRecord:
    summary = {
        key: data.get(key)
        for key in ("outcome", "command_id", "action_id", "verification_hash")
        if data.get(key) is not None
    }
    related = [
        value
        for key in ("command_id", "action_id")
        if isinstance((value := data.get(key)), str) and value
    ]
    return FinalizedReporterRecord(
        reference=str(reference),
        kind="mutation.verification",
        summary=_summary(summary),
        payload_hash=_hash(data.get("payload_hash"), summary),
        related_ids=related,
        timestamp_utc=_timestamp(data.get("timestamp_utc")),
    )


def _summary(value: object) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:4096]


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _hash(value: object, fallback: object) -> str:
    if isinstance(value, str) and len(value) == 64:
        return value
    return sha256_canonical(_json_value(fallback))


def _nested(data: dict[str, object], outer: str, inner: str) -> object:
    value = data.get(outer)
    return value.get(inner) if isinstance(value, dict) else None


def _timestamp(*values: object) -> datetime:
    for value in values:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
    raise ValueError("Finalized record timestamp is invalid")
