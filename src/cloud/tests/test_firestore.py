from unittest.mock import Mock

import pytest
from google.api_core.exceptions import AlreadyExists

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


@pytest.mark.parametrize(
    ("event_type", "observed_event_types", "material_metric_delta"),
    [
        ("tool.result", frozenset({"tool.result"}), False),
        ("performance.sample", frozenset({"binding.aggregate"}), False),
        ("performance.sample", frozenset({"performance.sample"}), True),
    ],
)
def test_material_evidence_advances_revision(
    event_type: str,
    observed_event_types: frozenset[str],
    material_metric_delta: bool,
) -> None:
    assert firestore_client.next_evidence_revision(
        7,
        event_type=event_type,
        observed_event_types=observed_event_types,
        material_metric_delta=material_metric_delta,
    ) == 8


def test_processed_run_lookup_reports_existing_run() -> None:
    client = Mock()
    document = Mock()
    client.collection.return_value.document.return_value = document
    document.get.return_value = Mock(exists=True)

    assert firestore_client.is_run_processed(client, "incident-1:2:binding.aggregate") is True
    client.collection.assert_called_once_with(firestore_client.PROCESSED_RUNS_COLLECTION)
    client.collection.return_value.document.assert_called_once_with(
        "incident-1:2:binding.aggregate"
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
            "audit_sequence": 0,
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


def test_incident_evidence_append_rejects_duplicate_id() -> None:
    client = Mock()
    incident = Mock()
    evidence_document = Mock()
    client.collection.return_value.document.return_value = incident
    incident.collection.return_value.document.return_value = evidence_document
    evidence_document.create.side_effect = [None, AlreadyExists("duplicate evidence")]
    evidence = {"event_id": "event-1", "kind": "binding.aggregate"}

    firestore_client.append_incident_evidence(client, "incident-1", "evidence-1", evidence)
    with pytest.raises(AlreadyExists):
        firestore_client.append_incident_evidence(client, "incident-1", "evidence-1", evidence)

    client.collection.assert_called_with(firestore_client.INCIDENTS_COLLECTION)
    client.collection.return_value.document.assert_called_with("incident-1")
    incident.collection.assert_called_with(firestore_client.EVIDENCE_COLLECTION)
    incident.collection.return_value.document.assert_called_with("evidence-1")
    assert evidence_document.create.call_count == 2
    evidence_document.create.assert_called_with(evidence)


def test_incident_occurrence_update_increments_existing_evidence_count() -> None:
    client = Mock()
    incident = Mock()
    evidence_document = Mock()
    client.collection.return_value.document.return_value = incident
    incident.collection.return_value.document.return_value = evidence_document

    firestore_client.increment_incident_occurrence(client, "incident-1", "evidence-1", 3)

    client.collection.assert_called_once_with(firestore_client.INCIDENTS_COLLECTION)
    client.collection.return_value.document.assert_called_once_with("incident-1")
    incident.collection.assert_called_once_with(firestore_client.EVIDENCE_COLLECTION)
    incident.collection.return_value.document.assert_called_once_with("evidence-1")
    evidence_document.update.assert_called_once_with(
        {"payload.occurrence_count": firestore_client.firestore.Increment(3)}
    )


def test_incident_event_persist_is_atomic(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    dedup_collection = Mock()
    incident_collection = Mock()
    dedup_document = Mock()
    incident_document = Mock()
    evidence_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: (
        dedup_collection
        if name == firestore_client.EVENT_DEDUP_COLLECTION
        else incident_collection
    )
    dedup_collection.document.return_value = dedup_document
    incident_collection.document.return_value = incident_document
    incident_document.collection.return_value.document.return_value = evidence_document
    dedup_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(exists=False)
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)
    incident = {"state": "NEW", "evidence_revision": 1}
    evidence = {"event_id": "event-1"}

    assert firestore_client.persist_incident_event(
        client,
        event_id="event-1",
        incident_id="incident-1",
        evidence_id="event-1",
        incident=incident,
        evidence=evidence,
    ) == 1

    transaction.create.assert_any_call(
        dedup_document,
        {
            "created_at": firestore_client.firestore.SERVER_TIMESTAMP,
            "incident_id": "incident-1",
        },
    )
    transaction.create.assert_any_call(incident_document, incident)
    transaction.create.assert_any_call(evidence_document, evidence)


def test_related_event_does_not_create_missing_incident(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    dedup_collection = Mock()
    incident_collection = Mock()
    dedup_document = Mock()
    incident_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: (
        dedup_collection
        if name == firestore_client.EVENT_DEDUP_COLLECTION
        else incident_collection
    )
    dedup_collection.document.return_value = dedup_document
    incident_collection.document.return_value = incident_document
    dedup_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(exists=False)
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Incident does not exist"):
        firestore_client.persist_incident_event(
            client,
            event_id="performance-1",
            incident_id="incident-1",
            evidence_id="performance-1",
            incident=None,
            evidence={"event_id": "performance-1"},
        )

    transaction.create.assert_not_called()


def test_incident_event_persist_returns_next_evidence_revision(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    dedup_collection = Mock()
    incident_collection = Mock()
    dedup_document = Mock()
    incident_document = Mock()
    evidence_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: (
        dedup_collection
        if name == firestore_client.EVENT_DEDUP_COLLECTION
        else incident_collection
    )
    dedup_collection.document.return_value = dedup_document
    incident_collection.document.return_value = incident_document
    incident_document.collection.return_value.document.return_value = evidence_document
    dedup_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"evidence_revision": 3},
    )
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    revision = firestore_client.persist_incident_event(
        client,
        event_id="performance-1",
        incident_id="incident-1",
        evidence_id="performance-1",
        incident=None,
        evidence={"event_id": "performance-1"},
    )

    assert revision == 4
    transaction.update.assert_called_once_with(
        incident_document,
        {
            "evidence_revision": 4,
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )


def test_duplicate_incident_event_does_not_add_evidence_or_revision(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    dedup_collection = Mock()
    incident_collection = Mock()
    dedup_document = Mock()
    incident_document = Mock()
    evidence_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: (
        dedup_collection
        if name == firestore_client.EVENT_DEDUP_COLLECTION
        else incident_collection
    )
    dedup_collection.document.return_value = dedup_document
    incident_collection.document.return_value = incident_document
    incident_document.collection.return_value.document.return_value = evidence_document
    dedup_document.get.side_effect = [Mock(exists=False), Mock(exists=True)]
    incident_document.get.return_value = Mock(exists=False)
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)
    incident = {"state": "NEW", "evidence_revision": 1}
    evidence = {"event_id": "event-1"}

    first = firestore_client.persist_incident_event(
        client,
        event_id="event-1",
        incident_id="incident-1",
        evidence_id="event-1",
        incident=incident,
        evidence=evidence,
    )
    second = firestore_client.persist_incident_event(
        client,
        event_id="event-1",
        incident_id="incident-1",
        evidence_id="event-1",
        incident=incident,
        evidence=evidence,
    )

    assert first == 1
    assert second is None
    assert transaction.create.call_count == 3
    transaction.create.assert_any_call(evidence_document, evidence)
    transaction.update.assert_not_called()
