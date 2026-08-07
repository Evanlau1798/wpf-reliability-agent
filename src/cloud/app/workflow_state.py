from google.cloud import firestore

from app.firestore_client import INCIDENTS_COLLECTION, PROCESSED_RUNS_COLLECTION


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
