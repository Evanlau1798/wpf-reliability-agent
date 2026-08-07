from datetime import datetime, timedelta
from enum import StrEnum

from google.cloud import firestore

from app.firestore_client import AUDIT_COLLECTION, INCIDENTS_COLLECTION, PROCESSED_RUNS_COLLECTION


MAX_INVESTIGATION_ROUNDS = 4
MAX_READ_ONLY_TOOL_CALLS = 6


class IncidentState(StrEnum):
    NEW = "NEW"
    TRIAGING = "TRIAGING"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    MITIGATED = "MITIGATED"
    REPORTING = "REPORTING"
    REJECTED = "REJECTED"
    FAILED_SAFE = "FAILED_SAFE"
    CLOSED = "CLOSED"


ALLOWED_TRANSITIONS = frozenset(
    {
        (IncidentState.NEW, IncidentState.TRIAGING),
        (IncidentState.TRIAGING, IncidentState.COLLECTING_EVIDENCE),
        (IncidentState.COLLECTING_EVIDENCE, IncidentState.INVESTIGATING),
        (IncidentState.INVESTIGATING, IncidentState.COLLECTING_EVIDENCE),
        (IncidentState.INVESTIGATING, IncidentState.AWAITING_APPROVAL),
        (IncidentState.INVESTIGATING, IncidentState.REPORTING),
        (IncidentState.AWAITING_APPROVAL, IncidentState.EXECUTING),
        (IncidentState.AWAITING_APPROVAL, IncidentState.REJECTED),
        (IncidentState.EXECUTING, IncidentState.VERIFYING),
        (IncidentState.EXECUTING, IncidentState.FAILED_SAFE),
        (IncidentState.VERIFYING, IncidentState.MITIGATED),
        (IncidentState.VERIFYING, IncidentState.INVESTIGATING),
        (IncidentState.VERIFYING, IncidentState.FAILED_SAFE),
        (IncidentState.REPORTING, IncidentState.CLOSED),
        (IncidentState.MITIGATED, IncidentState.REPORTING),
        (IncidentState.REJECTED, IncidentState.REPORTING),
        (IncidentState.FAILED_SAFE, IncidentState.REPORTING),
    }
)


def advance_investigation_round(client: firestore.Client, *, incident_id: str) -> int:
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)

    @firestore.transactional
    def advance(transaction: firestore.Transaction) -> int:
        snapshot = incident_document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        current = (snapshot.to_dict() or {}).get("investigation_round_count")
        if type(current) is not int or current < 0:
            raise ValueError("Investigation round count is invalid")
        if current >= MAX_INVESTIGATION_ROUNDS:
            raise ValueError("Investigation round limit reached")
        next_round = current + 1
        transaction.update(
            incident_document,
            {
                "investigation_round_count": next_round,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return next_round

    return advance(client.transaction())


def advance_read_only_tool_call(client: firestore.Client, *, incident_id: str) -> int:
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)

    @firestore.transactional
    def advance(transaction: firestore.Transaction) -> int:
        snapshot = incident_document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        current = (snapshot.to_dict() or {}).get("read_only_tool_call_count")
        if type(current) is not int or current < 0:
            raise ValueError("Read-only tool call count is invalid")
        if current >= MAX_READ_ONLY_TOOL_CALLS:
            raise ValueError("Read-only tool call limit reached")
        next_count = current + 1
        transaction.update(
            incident_document,
            {
                "read_only_tool_call_count": next_count,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return next_count

    return advance(client.transaction())


def transition_incident(
    client: firestore.Client,
    *,
    incident_id: str,
    expected_state: IncidentState,
    expected_version: int,
    target_state: IncidentState,
) -> int:
    if (expected_state, target_state) not in ALLOWED_TRANSITIONS:
        raise ValueError("Illegal state transition")
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)

    @firestore.transactional
    def transition(transaction: firestore.Transaction) -> int:
        snapshot = incident_document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        incident = snapshot.to_dict() or {}
        state_version = incident.get("state_version")
        audit_sequence = incident.get("audit_sequence")
        if incident.get("state") != expected_state.value:
            raise ValueError("Incident state does not match")
        if type(state_version) is not int or state_version != expected_version:
            raise ValueError("Stale state version")
        if type(audit_sequence) is not int or audit_sequence < 0:
            raise ValueError("Incident audit sequence is invalid")

        next_version = state_version + 1
        next_audit_sequence = audit_sequence + 1
        audit_document = incident_document.collection(AUDIT_COLLECTION).document(
            str(next_audit_sequence)
        )
        transaction.update(
            incident_document,
            {
                "state": target_state.value,
                "state_version": next_version,
                "audit_sequence": next_audit_sequence,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        transaction.create(
            audit_document,
            {
                "sequence": next_audit_sequence,
                "type": "state.transition",
                "from_state": expected_state.value,
                "to_state": target_state.value,
                "state_version": next_version,
                "created_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return next_version

    return transition(client.transaction())


def acquire_incident_lease(
    client: firestore.Client,
    *,
    incident_id: str,
    owner: str,
    now: datetime,
    duration: timedelta,
) -> bool:
    if not owner or now.tzinfo is None or duration <= timedelta(0):
        raise ValueError("Invalid lease request")
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)

    @firestore.transactional
    def acquire(transaction: firestore.Transaction) -> bool:
        snapshot = incident_document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        incident = snapshot.to_dict() or {}
        lease_owner = incident.get("lease_owner")
        lease_until = incident.get("lease_until")
        if lease_owner:
            if lease_until is not None and not isinstance(lease_until, datetime):
                raise ValueError("Incident lease is invalid")
            if lease_until is not None and lease_until > now and lease_owner != owner:
                return False

        transaction.update(
            incident_document,
            {
                "lease_owner": owner,
                "lease_until": now + duration,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return True

    return acquire(client.transaction())


def release_incident_lease(
    client: firestore.Client,
    *,
    incident_id: str,
    owner: str,
) -> None:
    if not owner:
        raise ValueError("Invalid lease owner")
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)

    @firestore.transactional
    def release(transaction: firestore.Transaction) -> None:
        snapshot = incident_document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        incident = snapshot.to_dict() or {}
        if incident.get("lease_owner") != owner:
            raise ValueError("Lease owner mismatch")
        transaction.update(
            incident_document,
            {
                "lease_owner": None,
                "lease_until": None,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )

    release(client.transaction())


def commit_new_incident_run(
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
        state_version = incident.get("state_version")
        audit_sequence = incident.get("audit_sequence")
        current_revision = incident.get("evidence_revision")
        if incident.get("state") != "NEW":
            raise ValueError("Incident is not NEW")
        if type(state_version) is not int or state_version < 1:
            raise ValueError("Incident state version is invalid")
        if type(audit_sequence) is not int or audit_sequence < 0:
            raise ValueError("Incident audit sequence is invalid")
        if type(current_revision) is not int or current_revision < evidence_revision:
            raise ValueError("Incident evidence revision is stale")

        next_version = state_version + 1
        next_audit_sequence = audit_sequence + 1
        audit_document = incident_document.collection(AUDIT_COLLECTION).document(
            str(next_audit_sequence)
        )
        transaction.update(
            incident_document,
            {
                "state": "TRIAGING",
                "state_version": next_version,
                "audit_sequence": next_audit_sequence,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        transaction.create(
            audit_document,
            {
                "sequence": next_audit_sequence,
                "type": "state.transition",
                "from_state": "NEW",
                "to_state": "TRIAGING",
                "state_version": next_version,
                "created_at": firestore.SERVER_TIMESTAMP,
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
