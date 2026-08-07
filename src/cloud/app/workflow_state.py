from datetime import datetime, timedelta
from enum import StrEnum

from google.cloud import firestore

from app.firestore_client import INCIDENTS_COLLECTION, PROCESSED_RUNS_COLLECTION


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
        if incident.get("state") != expected_state.value:
            raise ValueError("Incident state does not match")
        if type(state_version) is not int or state_version != expected_version:
            raise ValueError("Stale state version")

        next_version = state_version + 1
        transaction.update(
            incident_document,
            {
                "state": target_state.value,
                "state_version": next_version,
                "updated_at": firestore.SERVER_TIMESTAMP,
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
        current_revision = incident.get("evidence_revision")
        if incident.get("state") != "NEW":
            raise ValueError("Incident is not NEW")
        if type(state_version) is not int or state_version < 1:
            raise ValueError("Incident state version is invalid")
        if type(current_revision) is not int or current_revision < evidence_revision:
            raise ValueError("Incident evidence revision is stale")

        transaction.update(
            incident_document,
            {
                "state": "TRIAGING",
                "state_version": state_version + 1,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        transaction.create(
            processed_document,
            {
                "incident_id": incident_id,
                "evidence_revision": evidence_revision,
                "trigger": trigger,
                "processed_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return True

    return commit(client.transaction())
