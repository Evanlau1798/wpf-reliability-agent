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

    snapshot.to_dict.return_value = {"read_only_tool_call_count": 5}
    assert workflow_state.advance_read_only_tool_call(client, incident_id="incident-1") == 6
    transaction.update.assert_called_once_with(
        document,
        {
            "read_only_tool_call_count": 6,
            "updated_at": workflow_state.firestore.SERVER_TIMESTAMP,
        },
    )

    snapshot.to_dict.return_value = {"read_only_tool_call_count": 6}
    with pytest.raises(ValueError, match="tool call limit"):
        workflow_state.advance_read_only_tool_call(client, incident_id="incident-1")

    assert workflow_state.MAX_READ_ONLY_TOOL_CALLS == 6
