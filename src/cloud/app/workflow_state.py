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
