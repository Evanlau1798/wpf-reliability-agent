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


def test_new_incident_run_commits_transition_and_processed_marker_together(monkeypatch) -> None:
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
    processed_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"state": "NEW", "state_version": 1, "evidence_revision": 2},
    )
    order: list[str] = []
    transaction.update.side_effect = lambda *_args, **_kwargs: order.append("transition")
    transaction.create.side_effect = lambda *_args, **_kwargs: order.append("processed")
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    committed = workflow_state.commit_new_incident_run(
        client,
        run_key="incident-1:2:binding.aggregate",
        incident_id="incident-1",
        evidence_revision=2,
        trigger="binding.aggregate",
    )

    assert committed is True
    assert order == ["transition", "processed"]
    transaction.update.assert_called_once_with(
        incident_document,
        {
            "state": "TRIAGING",
            "state_version": 2,
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    transaction.create.assert_called_once_with(
        processed_document,
        {
            "incident_id": "incident-1",
            "evidence_revision": 2,
            "trigger": "binding.aggregate",
            "processed_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )


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
        to_dict=lambda: {"state": "NEW", "state_version": 1, "evidence_revision": 2},
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
        )

    transaction.create.assert_not_called()
