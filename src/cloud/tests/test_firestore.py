from unittest.mock import Mock

from app import firestore_client


def test_firestore_client_provider_reuses_client_for_project(monkeypatch) -> None:
    created_projects: list[str] = []

    def create_client(*, project: str) -> object:
        created_projects.append(project)
        return object()

    monkeypatch.setattr(firestore_client.firestore, "Client", create_client)
    firestore_client.get_firestore_client.cache_clear()
    try:
        first = firestore_client.get_firestore_client("project-test")
        second = firestore_client.get_firestore_client("project-test")
    finally:
        firestore_client.get_firestore_client.cache_clear()

    assert first is second
    assert created_projects == ["project-test"]


def test_firestore_collection_names_match_model() -> None:
    assert (
        firestore_client.DEVICES_COLLECTION,
        firestore_client.INCIDENTS_COLLECTION,
        firestore_client.EVIDENCE_COLLECTION,
        firestore_client.ACTIONS_COLLECTION,
        firestore_client.APPROVALS_COLLECTION,
        firestore_client.AUDIT_COLLECTION,
        firestore_client.REPORTS_COLLECTION,
        firestore_client.COMMANDS_COLLECTION,
        firestore_client.EVENT_DEDUP_COLLECTION,
        firestore_client.PROCESSED_RUNS_COLLECTION,
    ) == (
        "devices",
        "incidents",
        "evidence",
        "actions",
        "approvals",
        "audit",
        "reports",
        "commands",
        "event_dedup",
        "processed_runs",
    )


def test_event_dedup_transaction_accepts_event_once(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    document = Mock()
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = document
    document.get.side_effect = [Mock(exists=False), Mock(exists=True)]
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    assert firestore_client.claim_event_once(client, "event-1") is True
    assert firestore_client.claim_event_once(client, "event-1") is False

    client.collection.assert_called_with(firestore_client.EVENT_DEDUP_COLLECTION)
    client.collection.return_value.document.assert_called_with("event-1")
    transaction.set.assert_called_once_with(
        document,
        {"created_at": firestore_client.firestore.SERVER_TIMESTAMP},
    )


def test_incident_create_writes_complete_initial_state() -> None:
    client = Mock()
    document = Mock()
    client.collection.return_value.document.return_value = document

    firestore_client.create_incident(
        client,
        "incident-1",
        application_id="app-1",
        app_session_id="session-1",
        severity="ERROR",
        summary="Binding error burst",
    )

    client.collection.assert_called_once_with(firestore_client.INCIDENTS_COLLECTION)
    client.collection.return_value.document.assert_called_once_with("incident-1")
    document.create.assert_called_once_with(
        {
            "state": "NEW",
            "state_version": 1,
            "evidence_revision": 0,
            "proposal_version": 0,
            "application_id": "app-1",
            "app_session_id": "session-1",
            "severity": "ERROR",
            "summary": "Binding error burst",
            "current_hypotheses": [],
            "pending_command_id": None,
            "pending_action_id": None,
            "approval_id": None,
            "lease_owner": None,
            "lease_until": None,
            "created_at": firestore_client.firestore.SERVER_TIMESTAMP,
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        }
    )
