from collections.abc import Collection, Iterable
from datetime import datetime
from functools import cache

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.approval import validate_recovery_proposal
from app.contracts import sha256_canonical
from app.models import ApprovalRecord, ApprovalStatus, ProposedAction, RiskLevel
from app.policy import POLICY_VERSION


DEVICES_COLLECTION = "devices"
INCIDENTS_COLLECTION = "incidents"
EVIDENCE_COLLECTION = "evidence"
ACTIONS_COLLECTION = "actions"
APPROVALS_COLLECTION = "approvals"
AUDIT_COLLECTION = "audit"
REPORTS_COLLECTION = "reports"
COMMANDS_COLLECTION = "commands"
EVENT_DEDUP_COLLECTION = "event_dedup"
PROCESSED_RUNS_COLLECTION = "processed_runs"


def evidence_snapshot_hash(material_evidence: Iterable[tuple[str, str]]) -> str:
    snapshot = sorted(
        (
            {"evidence_id": evidence_id, "evidence_hash": evidence_hash}
            for evidence_id, evidence_hash in material_evidence
        ),
        key=lambda item: item["evidence_id"],
    )
    return sha256_canonical(snapshot)


def create_pending_approval(
    client: firestore.Client,
    *,
    approval_id: str,
    incident_id: str,
    proposal_version: int,
    evidence_snapshot_hash_value: str,
    action_id: str,
    proposal: ProposedAction,
    target_app_session_id: str,
    expires_at_utc: datetime,
) -> ApprovalRecord:
    proposal = validate_recovery_proposal(proposal)
    approval = ApprovalRecord(
        schema_version="1.0",
        approval_id=approval_id,
        incident_id=incident_id,
        proposal_version=proposal_version,
        evidence_snapshot_hash=evidence_snapshot_hash_value,
        action_id=action_id,
        tool=proposal.tool,
        canonical_arguments=proposal.arguments,
        canonical_arguments_hash=sha256_canonical(proposal.arguments),
        target_app_session_id=target_app_session_id,
        policy_version=POLICY_VERSION,
        risk_level=RiskLevel.HIGH,
        expected_effect=proposal.expected_effect,
        rollback_plan=proposal.rollback_plan,
        expires_at_utc=expires_at_utc,
        status=ApprovalStatus.PENDING,
    )
    incident = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    incident.collection(APPROVALS_COLLECTION).document(approval_id).create(
        approval.model_dump(mode="json")
    )
    return approval


def validate_pending_approval_decision(
    client: firestore.Client,
    *,
    approval_id: str,
    now: datetime,
) -> ApprovalRecord:
    query = client.collection_group(APPROVALS_COLLECTION).where(
        filter=FieldFilter("approval_id", "==", approval_id)
    ).limit(2)

    @firestore.transactional
    def validate(transaction: firestore.Transaction) -> ApprovalRecord | None:
        snapshots = list(transaction.get(query))
        if len(snapshots) != 1:
            raise ValueError("Approval does not exist")
        approval = ApprovalRecord.model_validate(snapshots[0].to_dict() or {})
        if approval.status is not ApprovalStatus.PENDING:
            raise ValueError("Approval is not pending")
        if approval.expires_at_utc <= now:
            transaction.update(
                snapshots[0].reference,
                {
                    "status": ApprovalStatus.EXPIRED.value,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return None
        if approval.policy_version != POLICY_VERSION:
            raise ValueError("Approval policy version mismatch")
        incident_document = snapshots[0].reference.parent.parent
        if incident_document is None:
            raise ValueError("Approval incident reference is invalid")
        incident_snapshot = incident_document.get(transaction=transaction)
        if not incident_snapshot.exists:
            raise ValueError("Incident does not exist")
        incident = incident_snapshot.to_dict() or {}
        if incident.get("proposal_version") != approval.proposal_version:
            raise ValueError("Approval proposal version mismatch")
        material_evidence: list[tuple[str, str]] = []
        for evidence_snapshot in transaction.get(
            incident_document.collection(EVIDENCE_COLLECTION)
        ):
            evidence = evidence_snapshot.to_dict() or {}
            evidence_hash = evidence.get("evidence_hash")
            if not isinstance(evidence_hash, str):
                raise ValueError("Incident evidence hash is invalid")
            material_evidence.append((evidence_snapshot.id, evidence_hash))
        if evidence_snapshot_hash(material_evidence) != approval.evidence_snapshot_hash:
            raise ValueError("Approval evidence snapshot mismatch")
        if sha256_canonical(approval.canonical_arguments) != approval.canonical_arguments_hash:
            raise ValueError("Approval arguments hash mismatch")
        if incident.get("app_session_id") != approval.target_app_session_id:
            raise ValueError("Approval app session mismatch")
        return approval

    approval = validate(client.transaction())
    if approval is None:
        raise ValueError("Approval expired")
    return approval


def next_evidence_revision(
    current_revision: int,
    *,
    event_type: str,
    observed_event_types: Collection[str],
    material_metric_delta: bool = False,
) -> int:
    if type(current_revision) is not int or current_revision < 0:
        raise ValueError("Incident evidence revision is invalid")
    if not event_type:
        raise ValueError("Evidence event type is required")
    is_material = (
        event_type == "tool.result"
        or event_type not in observed_event_types
        or material_metric_delta
    )
    return current_revision + int(is_material)


@cache
def get_firestore_client(project_id: str) -> firestore.Client:
    return firestore.Client(project=project_id)


def claim_event_once(client: firestore.Client, event_id: str) -> bool:
    document = client.collection(EVENT_DEDUP_COLLECTION).document(event_id)

    @firestore.transactional
    def claim(transaction: firestore.Transaction) -> bool:
        if document.get(transaction=transaction).exists:
            return False

        transaction.set(document, {"created_at": firestore.SERVER_TIMESTAMP})
        return True

    return claim(client.transaction())


def is_run_processed(client: firestore.Client, run_key: str) -> bool:
    return client.collection(PROCESSED_RUNS_COLLECTION).document(run_key).get().exists


def create_incident(
    client: firestore.Client,
    incident_id: str,
    *,
    application_id: str,
    app_session_id: str,
    severity: str,
    summary: str,
) -> None:
    client.collection(INCIDENTS_COLLECTION).document(incident_id).create(
        build_incident_document(
            application_id=application_id,
            app_session_id=app_session_id,
            severity=severity,
            summary=summary,
        )
    )


def append_incident_evidence(
    client: firestore.Client,
    incident_id: str,
    evidence_id: str,
    evidence: dict[str, object],
) -> None:
    incident = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    incident.collection(EVIDENCE_COLLECTION).document(evidence_id).create(evidence)


def increment_incident_occurrence(
    client: firestore.Client,
    incident_id: str,
    evidence_id: str,
    occurrence_count: int,
) -> None:
    incident = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    incident.collection(EVIDENCE_COLLECTION).document(evidence_id).update(
        {"payload.occurrence_count": firestore.Increment(occurrence_count)}
    )


def build_incident_document(
    *,
    application_id: str,
    app_session_id: str,
    severity: str,
    summary: str,
    evidence_revision: int = 0,
) -> dict[str, object]:
    return {
        "state": "NEW",
        "state_version": 1,
        "audit_sequence": 0,
        "evidence_revision": evidence_revision,
        "proposal_version": 0,
        "investigation_round_count": 0,
        "read_only_tool_call_count": 0,
        "read_only_tool_request_keys": [],
        "last_investigated_evidence_revision": None,
        "no_new_evidence_count": 0,
        "mutation_proposal_count": 0,
        "application_id": application_id,
        "app_session_id": app_session_id,
        "severity": severity,
        "summary": summary,
        "current_hypotheses": [],
        "pending_command_id": None,
        "pending_action_id": None,
        "approval_id": None,
        "lease_owner": None,
        "lease_until": None,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }


def persist_incident_event(
    client: firestore.Client,
    *,
    event_id: str,
    incident_id: str,
    evidence_id: str,
    incident: dict[str, object] | None,
    evidence: dict[str, object],
) -> int | None:
    dedup_document = client.collection(EVENT_DEDUP_COLLECTION).document(event_id)
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    evidence_document = incident_document.collection(EVIDENCE_COLLECTION).document(evidence_id)

    @firestore.transactional
    def persist(transaction: firestore.Transaction) -> int | None:
        if dedup_document.get(transaction=transaction).exists:
            return None

        incident_snapshot = incident_document.get(transaction=transaction)
        if not incident_snapshot.exists and incident is None:
            raise ValueError("Incident does not exist")

        if incident_snapshot.exists:
            current_revision = (incident_snapshot.to_dict() or {}).get("evidence_revision")
            if type(current_revision) is not int or current_revision < 0:
                raise ValueError("Incident evidence revision is invalid")
            evidence_revision = current_revision + 1
        else:
            evidence_revision = incident.get("evidence_revision") if incident is not None else None
            if type(evidence_revision) is not int or evidence_revision < 1:
                raise ValueError("New incident evidence revision is invalid")

        transaction.create(
            dedup_document,
            {"created_at": firestore.SERVER_TIMESTAMP, "incident_id": incident_id},
        )
        if incident_snapshot.exists:
            transaction.update(
                incident_document,
                {
                    "evidence_revision": evidence_revision,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
        else:
            transaction.create(incident_document, incident)
        transaction.create(evidence_document, evidence)
        return evidence_revision

    return persist(client.transaction())
