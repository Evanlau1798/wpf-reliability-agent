from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import AlreadyExists

from app import firestore_client
from app.models import ApprovalStatus, ProposedAction, RiskLevel


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


def test_evidence_snapshot_hash_changes_with_material_evidence() -> None:
    before = [("evidence-1", "1" * 64)]
    after = [*before, ("evidence-2", "2" * 64)]

    before_hash = firestore_client.evidence_snapshot_hash(before)
    after_hash = firestore_client.evidence_snapshot_hash(after)

    assert before_hash != after_hash
    assert after_hash == firestore_client.evidence_snapshot_hash(reversed(after))


def test_occurrence_only_update_preserves_approval_evidence_binding() -> None:
    material_evidence = [("evidence-1", "1" * 64)]
    approval_binding = (7, firestore_client.evidence_snapshot_hash(material_evidence))

    current_binding = (
        firestore_client.next_evidence_revision(
            7,
            event_type="binding.aggregate",
            observed_event_types=frozenset({"binding.aggregate"}),
        ),
        firestore_client.evidence_snapshot_hash(material_evidence),
    )

    assert current_binding == approval_binding


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


def test_pending_approval_document_binds_exact_proposal() -> None:
    client = Mock()
    incident = Mock()
    approval_document = Mock()
    client.collection.return_value.document.return_value = incident
    incident.collection.return_value.document.return_value = approval_document
    proposal = ProposedAction.model_validate(
        {
            "tool": "recovery.set_feature_flag",
            "arguments": {
                "feature": "ExperimentalPeopleGrid",
                "enabled": False,
                "expected_current_value": True,
            },
            "evidence_ids": ["evidence-1"],
            "expected_effect": "Reduce UI load.",
            "rollback_plan": "Re-enable the feature.",
        }
    )
    expires_at = datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc)

    approval = firestore_client.create_pending_approval(
        client,
        approval_id="approval-1",
        incident_id="incident-1",
        proposal_version=3,
        evidence_snapshot_hash_value="1" * 64,
        action_id="action-1",
        proposal=proposal,
        target_app_session_id="session-1",
        expires_at_utc=expires_at,
    )

    assert approval.model_dump(mode="json") == {
        "schema_version": "1.0",
        "approval_id": "approval-1",
        "incident_id": "incident-1",
        "proposal_version": 3,
        "evidence_ids": proposal.evidence_ids,
        "evidence_snapshot_hash": "1" * 64,
        "action_id": "action-1",
        "tool": "recovery.set_feature_flag",
        "canonical_arguments": proposal.arguments,
        "canonical_arguments_hash": firestore_client.sha256_canonical(proposal.arguments),
        "target_app_session_id": "session-1",
        "policy_version": "1",
        "risk_level": RiskLevel.HIGH.value,
        "expected_effect": "Reduce UI load.",
        "rollback_plan": "Re-enable the feature.",
        "expires_at_utc": "2026-08-08T01:00:00Z",
        "status": ApprovalStatus.PENDING.value,
        "approved_by": None,
        "approved_at_utc": None,
    }
    client.collection.assert_called_once_with(firestore_client.INCIDENTS_COLLECTION)
    client.collection.return_value.document.assert_called_once_with("incident-1")
    incident.collection.assert_called_once_with(firestore_client.APPROVALS_COLLECTION)
    incident.collection.return_value.document.assert_called_once_with("approval-1")
    approval_document.create.assert_called_once_with(approval.model_dump(mode="json"))


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
    ) == (True, 1)

    transaction.create.assert_any_call(
        dedup_document,
        {
            "created_at": firestore_client.firestore.SERVER_TIMESTAMP,
            "incident_id": "incident-1",
            "evidence_revision": 1,
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

    assert revision == (True, 4)
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
    dedup_document.get.side_effect = [
        Mock(exists=False),
        Mock(exists=True, to_dict=lambda: {"incident_id": "incident-1", "evidence_revision": 1}),
    ]
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

    assert first == (True, 1)
    assert second == (False, 1)
    assert transaction.create.call_count == 3
    transaction.create.assert_any_call(evidence_document, evidence)
    transaction.update.assert_not_called()
