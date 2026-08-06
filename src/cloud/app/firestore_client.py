from functools import cache

from google.cloud import firestore


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
        "evidence_revision": evidence_revision,
        "proposal_version": 0,
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
    incident: dict[str, object],
    evidence: dict[str, object],
) -> bool:
    dedup_document = client.collection(EVENT_DEDUP_COLLECTION).document(event_id)
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    evidence_document = incident_document.collection(EVIDENCE_COLLECTION).document(evidence_id)

    @firestore.transactional
    def persist(transaction: firestore.Transaction) -> bool:
        if dedup_document.get(transaction=transaction).exists:
            return False

        incident_exists = incident_document.get(transaction=transaction).exists
        transaction.create(
            dedup_document,
            {"created_at": firestore.SERVER_TIMESTAMP, "incident_id": incident_id},
        )
        if incident_exists:
            transaction.update(
                incident_document,
                {
                    "evidence_revision": firestore.Increment(1),
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
        else:
            transaction.create(incident_document, incident)
        transaction.create(evidence_document, evidence)
        return True

    return persist(client.transaction())
