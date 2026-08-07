from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from app import firestore_client
from app import workflow_state


def test_incident_state_enum_matches_proposal_states() -> None:
    assert {state.value for state in workflow_state.IncidentState} == {
        "NEW",
        "TRIAGING",
        "COLLECTING_EVIDENCE",
        "INVESTIGATING",
        "AWAITING_APPROVAL",
        "EXECUTING",
        "VERIFYING",
        "MITIGATED",
        "REPORTING",
        "REJECTED",
        "FAILED_SAFE",
        "CLOSED",
    }


def test_allowed_transition_table_covers_every_state_pair() -> None:
    state = workflow_state.IncidentState
    expected = {
        (state.NEW, state.TRIAGING),
        (state.TRIAGING, state.COLLECTING_EVIDENCE),
        (state.COLLECTING_EVIDENCE, state.INVESTIGATING),
        (state.INVESTIGATING, state.COLLECTING_EVIDENCE),
        (state.INVESTIGATING, state.AWAITING_APPROVAL),
        (state.INVESTIGATING, state.REPORTING),
        (state.AWAITING_APPROVAL, state.EXECUTING),
        (state.AWAITING_APPROVAL, state.REJECTED),
        (state.EXECUTING, state.VERIFYING),
        (state.EXECUTING, state.FAILED_SAFE),
        (state.VERIFYING, state.MITIGATED),
        (state.VERIFYING, state.INVESTIGATING),
        (state.VERIFYING, state.FAILED_SAFE),
        (state.REPORTING, state.CLOSED),
        (state.MITIGATED, state.REPORTING),
        (state.REJECTED, state.REPORTING),
        (state.FAILED_SAFE, state.REPORTING),
    }

    for source in state:
        for target in state:
            assert ((source, target) in workflow_state.ALLOWED_TRANSITIONS) is (
                (source, target) in expected
            )


def test_state_transition_rejects_stale_version(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_document = Mock()
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = incident_document
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"state": "NEW", "state_version": 2},
    )
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Stale state version"):
        workflow_state.transition_incident(
            client,
            incident_id="incident-1",
            expected_state=workflow_state.IncidentState.NEW,
            expected_version=1,
            target_state=workflow_state.IncidentState.TRIAGING,
        )

    transaction.update.assert_not_called()


def test_illegal_new_to_executing_transition_leaves_document_unchanged() -> None:
    client = Mock()

    with pytest.raises(ValueError, match="Illegal state transition"):
        workflow_state.transition_incident(
            client,
            incident_id="incident-1",
            expected_state=workflow_state.IncidentState.NEW,
            expected_version=1,
            target_state=workflow_state.IncidentState.EXECUTING,
        )

    client.collection.assert_not_called()


def test_investigating_incident_waits_for_approval_without_command(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_document = Mock()
    audit_document = Mock()
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = incident_document
    incident_document.collection.return_value.document.return_value = audit_document
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "state": "INVESTIGATING",
            "state_version": 4,
            "audit_sequence": 7,
        },
    )
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    next_version = workflow_state.transition_incident(
        client,
        incident_id="incident-1",
        expected_state=workflow_state.IncidentState.INVESTIGATING,
        expected_version=4,
        target_state=workflow_state.IncidentState.AWAITING_APPROVAL,
    )

    assert next_version == 5
    client.collection.assert_called_once_with(firestore_client.INCIDENTS_COLLECTION)
    transaction.update.assert_called_once_with(
        incident_document,
        {
            "state": "AWAITING_APPROVAL",
            "state_version": 5,
            "audit_sequence": 8,
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    transaction.create.assert_called_once_with(
        audit_document,
        {
            "sequence": 8,
            "type": "state.transition",
            "from_state": "INVESTIGATING",
            "to_state": "AWAITING_APPROVAL",
            "state_version": 5,
            "created_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    assert firestore_client.COMMANDS_COLLECTION not in [
        call.args[0] for call in client.collection.call_args_list
    ]


def test_incident_lease_allows_only_one_active_owner(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_document = Mock()
    now = datetime(2026, 8, 7, tzinfo=UTC)
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = incident_document
    incident_document.get.side_effect = [
        Mock(exists=True, to_dict=lambda: {"lease_owner": None, "lease_until": None}),
        Mock(
            exists=True,
            to_dict=lambda: {"lease_owner": "worker-a", "lease_until": now + timedelta(seconds=30)},
        ),
    ]
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    assert workflow_state.acquire_incident_lease(
        client,
        incident_id="incident-1",
        owner="worker-a",
        now=now,
        duration=timedelta(seconds=30),
    ) is True
    assert workflow_state.acquire_incident_lease(
        client,
        incident_id="incident-1",
        owner="worker-b",
        now=now,
        duration=timedelta(seconds=30),
    ) is False
    transaction.update.assert_called_once_with(
        incident_document,
        {
            "lease_owner": "worker-a",
            "lease_until": now + timedelta(seconds=30),
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )


def test_expired_incident_lease_can_be_acquired_by_new_owner(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_document = Mock()
    now = datetime(2026, 8, 7, tzinfo=UTC)
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = incident_document
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "lease_owner": "worker-a",
            "lease_until": now - timedelta(seconds=1),
        },
    )
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    assert workflow_state.acquire_incident_lease(
        client,
        incident_id="incident-1",
        owner="worker-b",
        now=now,
        duration=timedelta(seconds=30),
    ) is True
    transaction.update.assert_called_once_with(
        incident_document,
        {
            "lease_owner": "worker-b",
            "lease_until": now + timedelta(seconds=30),
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )


def test_release_incident_lease_clears_owned_lease(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_document = Mock()
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = incident_document
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"lease_owner": "worker-a"},
    )
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    workflow_state.release_incident_lease(
        client,
        incident_id="incident-1",
        owner="worker-a",
    )

    transaction.update.assert_called_once_with(
        incident_document,
        {
            "lease_owner": None,
            "lease_until": None,
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )


def test_release_incident_lease_rejects_owner_mismatch(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_document = Mock()
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = incident_document
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"lease_owner": "worker-b"},
    )
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Lease owner mismatch"):
        workflow_state.release_incident_lease(
            client,
            incident_id="incident-1",
            owner="worker-a",
        )

    transaction.update.assert_not_called()


def test_state_transitions_write_monotonic_audit_sequence(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_document = Mock()
    audit_collection = Mock()
    audit_documents = [Mock(), Mock()]
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = incident_document
    incident_document.collection.return_value = audit_collection
    audit_collection.document.side_effect = audit_documents
    incident_document.get.side_effect = [
        Mock(
            exists=True,
            to_dict=lambda: {"state": "NEW", "state_version": 1, "audit_sequence": 0},
        ),
        Mock(
            exists=True,
            to_dict=lambda: {"state": "TRIAGING", "state_version": 2, "audit_sequence": 1},
        ),
    ]
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    assert workflow_state.transition_incident(
        client,
        incident_id="incident-1",
        expected_state=workflow_state.IncidentState.NEW,
        expected_version=1,
        target_state=workflow_state.IncidentState.TRIAGING,
    ) == 2
    assert workflow_state.transition_incident(
        client,
        incident_id="incident-1",
        expected_state=workflow_state.IncidentState.TRIAGING,
        expected_version=2,
        target_state=workflow_state.IncidentState.COLLECTING_EVIDENCE,
    ) == 3

    assert [call.args[0] for call in audit_collection.document.call_args_list] == ["1", "2"]
    assert [call.args[1]["sequence"] for call in transaction.create.call_args_list] == [1, 2]
    assert [call.args[1]["type"] for call in transaction.create.call_args_list] == [
        "state.transition",
        "state.transition",
    ]
    assert [call.kwargs if call.kwargs else call.args[1] for call in transaction.update.call_args_list]
    assert transaction.update.call_args_list[0].args[1]["audit_sequence"] == 1
    assert transaction.update.call_args_list[1].args[1]["audit_sequence"] == 2


def test_new_incident_run_commits_transition_and_processed_marker_together(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_collection = Mock()
    processed_collection = Mock()
    incident_document = Mock()
    audit_document = Mock()
    processed_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: (
        incident_collection
        if name == firestore_client.INCIDENTS_COLLECTION
        else processed_collection
    )
    incident_collection.document.return_value = incident_document
    incident_document.collection.return_value.document.return_value = audit_document
    processed_collection.document.return_value = processed_document
    processed_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "state": "NEW",
            "state_version": 1,
            "audit_sequence": 0,
            "evidence_revision": 2,
        },
    )
    order: list[str] = []
    transaction.update.side_effect = lambda *_args, **_kwargs: order.append("transition")

    def record_create(document, _value):
        order.append("audit" if document is audit_document else "processed")

    transaction.create.side_effect = record_create
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    committed = workflow_state.commit_new_incident_run(
        client,
        run_key="incident-1:2:binding.aggregate",
        incident_id="incident-1",
        evidence_revision=2,
        trigger="binding.aggregate",
        model_id="gemini-test",
    )

    assert committed is True
    assert order == ["transition", "audit", "processed"]
    transaction.update.assert_called_once_with(
        incident_document,
        {
            "state": "TRIAGING",
            "state_version": 2,
            "audit_sequence": 1,
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    assert transaction.create.call_args_list[0].args[0] is audit_document
    assert transaction.create.call_args_list[0].args[1]["sequence"] == 1
    assert transaction.create.call_args_list[1].args == (
        processed_document,
        {
            "incident_id": "incident-1",
            "evidence_revision": 2,
            "trigger": "binding.aggregate",
            "model_id": "gemini-test",
            "processed_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )


def test_duplicate_new_incident_run_does_not_repeat_transition(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_collection = Mock()
    processed_collection = Mock()
    incident_document = Mock()
    processed_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: (
        incident_collection
        if name == firestore_client.INCIDENTS_COLLECTION
        else processed_collection
    )
    incident_collection.document.return_value = incident_document
    processed_collection.document.return_value = processed_document
    processed_document.get.return_value = Mock(exists=True)
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    committed = workflow_state.commit_new_incident_run(
        client,
        run_key="incident-1:2:binding.aggregate",
        incident_id="incident-1",
        evidence_revision=2,
        trigger="binding.aggregate",
        model_id="gemini-test",
    )

    assert committed is False
    incident_document.get.assert_not_called()
    transaction.update.assert_not_called()
    transaction.create.assert_not_called()


def test_failed_transition_does_not_mark_run_processed(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_document = Mock()
    processed_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: Mock(
        document=Mock(
            return_value=(
                incident_document
                if name == firestore_client.INCIDENTS_COLLECTION
                else processed_document
            )
        )
    )
    processed_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "state": "NEW",
            "state_version": 1,
            "audit_sequence": 0,
            "evidence_revision": 2,
        },
    )
    transaction.update.side_effect = RuntimeError("crash before commit")
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    with pytest.raises(RuntimeError, match="crash before commit"):
        workflow_state.commit_new_incident_run(
            client,
            run_key="incident-1:2:binding.aggregate",
            incident_id="incident-1",
            evidence_revision=2,
            trigger="binding.aggregate",
            model_id="gemini-test",
        )

    transaction.create.assert_not_called()
