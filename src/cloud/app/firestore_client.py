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
        {
            "state": "NEW",
            "state_version": 1,
            "evidence_revision": 0,
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
    )
