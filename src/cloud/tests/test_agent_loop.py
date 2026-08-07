from unittest.mock import Mock

import pytest

from app import firestore_client, workflow_state


def test_incident_starts_with_zero_investigation_rounds() -> None:
    incident = firestore_client.build_incident_document(
        application_id="app-1",
        app_session_id="session-1",
        severity="ERROR",
        summary="Binding error burst",
    )

    assert incident["investigation_round_count"] == 0


def test_investigation_round_count_is_persisted_and_capped(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    document = Mock()
    snapshot = Mock(exists=True)
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = document
    document.get.return_value = snapshot
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    snapshot.to_dict.return_value = {"investigation_round_count": 3}
    assert workflow_state.advance_investigation_round(client, incident_id="incident-1") == 4
    transaction.update.assert_called_once_with(
        document,
        {
            "investigation_round_count": 4,
            "updated_at": workflow_state.firestore.SERVER_TIMESTAMP,
        },
    )

    snapshot.to_dict.return_value = {"investigation_round_count": 4}
    with pytest.raises(ValueError, match="round limit"):
        workflow_state.advance_investigation_round(client, incident_id="incident-1")

    assert workflow_state.MAX_INVESTIGATION_ROUNDS == 4


def test_incident_starts_with_zero_read_only_tool_calls() -> None:
    incident = firestore_client.build_incident_document(
        application_id="app-1",
        app_session_id="session-1",
        severity="ERROR",
        summary="Binding error burst",
    )

    assert incident["read_only_tool_call_count"] == 0


def test_read_only_tool_call_count_is_persisted_and_capped(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    document = Mock()
    snapshot = Mock(exists=True)
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = document
    document.get.return_value = snapshot
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    arguments = {"element_id": "element-1"}
    request_key = workflow_state.canonical_tool_request_key("ui.get_subtree", arguments)
    snapshot.to_dict.return_value = {
        "read_only_tool_call_count": 5,
        "read_only_tool_request_keys": ["request-1", "request-2", "request-3", "request-4", "request-5"],
    }
    assert workflow_state.claim_read_only_tool_request(
        client,
        incident_id="incident-1",
        tool="ui.get_subtree",
        arguments=arguments,
    ) == request_key
    transaction.update.assert_called_once_with(
        document,
        {
            "read_only_tool_call_count": 6,
            "read_only_tool_request_keys": [
                "request-1",
                "request-2",
                "request-3",
                "request-4",
                "request-5",
                request_key,
            ],
            "updated_at": workflow_state.firestore.SERVER_TIMESTAMP,
        },
    )

    snapshot.to_dict.return_value = {
        "read_only_tool_call_count": 6,
        "read_only_tool_request_keys": [request_key],
    }
    with pytest.raises(ValueError, match="tool call limit"):
        workflow_state.claim_read_only_tool_request(
            client,
            incident_id="incident-1",
            tool="performance.sample",
            arguments={"duration_ms": 500},
        )

    assert workflow_state.MAX_READ_ONLY_TOOL_CALLS == 6


def test_incident_starts_with_empty_read_only_request_keys() -> None:
    incident = firestore_client.build_incident_document(
        application_id="app-1",
        app_session_id="session-1",
        severity="ERROR",
        summary="Binding error burst",
    )

    assert incident["read_only_tool_request_keys"] == []


def test_duplicate_canonical_tool_request_is_rejected(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    document = Mock()
    snapshot = Mock(exists=True)
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = document
    document.get.return_value = snapshot
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    first_arguments = {"element_id": "element-1", "max_depth": 2}
    same_arguments = {"max_depth": 2, "element_id": "element-1"}
    request_key = workflow_state.canonical_tool_request_key("ui.get_subtree", first_arguments)
    assert request_key == workflow_state.canonical_tool_request_key(
        "ui.get_subtree", same_arguments
    )

    snapshot.to_dict.return_value = {
        "read_only_tool_call_count": 0,
        "read_only_tool_request_keys": [],
    }
    assert workflow_state.claim_read_only_tool_request(
        client,
        incident_id="incident-1",
        tool="ui.get_subtree",
        arguments=first_arguments,
    ) == request_key
    transaction.update.assert_called_once_with(
        document,
        {
            "read_only_tool_call_count": 1,
            "read_only_tool_request_keys": [request_key],
            "updated_at": workflow_state.firestore.SERVER_TIMESTAMP,
        },
    )

    snapshot.to_dict.return_value = {
        "read_only_tool_call_count": 1,
        "read_only_tool_request_keys": [request_key],
    }
    with pytest.raises(ValueError, match="Duplicate tool request"):
        workflow_state.claim_read_only_tool_request(
            client,
            incident_id="incident-1",
            tool="ui.get_subtree",
            arguments=same_arguments,
        )


def test_incident_starts_with_no_new_evidence_guard_reset() -> None:
    incident = firestore_client.build_incident_document(
        application_id="app-1",
        app_session_id="session-1",
        severity="ERROR",
        summary="Binding error burst",
    )

    assert incident["last_investigated_evidence_revision"] is None
    assert incident["no_new_evidence_count"] == 0


def test_two_consecutive_rounds_without_new_evidence_stop(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    document = Mock()
    snapshot = Mock(exists=True)
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = document
    document.get.return_value = snapshot
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    snapshot.to_dict.return_value = {
        "last_investigated_evidence_revision": None,
        "no_new_evidence_count": 0,
    }
    assert workflow_state.record_investigation_evidence_progress(
        client, incident_id="incident-1", evidence_revision=3
    ) is False
    transaction.update.assert_called_once_with(
        document,
        {
            "last_investigated_evidence_revision": 3,
            "no_new_evidence_count": 0,
            "updated_at": workflow_state.firestore.SERVER_TIMESTAMP,
        },
    )

    transaction.update.reset_mock()
    snapshot.to_dict.return_value = {
        "last_investigated_evidence_revision": 3,
        "no_new_evidence_count": 0,
    }
    assert workflow_state.record_investigation_evidence_progress(
        client, incident_id="incident-1", evidence_revision=3
    ) is False
    transaction.update.assert_called_once_with(
        document,
        {
            "last_investigated_evidence_revision": 3,
            "no_new_evidence_count": 1,
            "updated_at": workflow_state.firestore.SERVER_TIMESTAMP,
        },
    )

    transaction.update.reset_mock()
    snapshot.to_dict.return_value = {
        "last_investigated_evidence_revision": 3,
        "no_new_evidence_count": 1,
    }
    assert workflow_state.record_investigation_evidence_progress(
        client, incident_id="incident-1", evidence_revision=3
    ) is True
    transaction.update.assert_called_once_with(
        document,
        {
            "last_investigated_evidence_revision": 3,
            "no_new_evidence_count": 2,
            "updated_at": workflow_state.firestore.SERVER_TIMESTAMP,
        },
    )
