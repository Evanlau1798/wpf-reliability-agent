from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from app import firestore_client


def test_pending_approval_can_be_loaded_for_decision(monkeypatch) -> None:
    client, transaction, _ = _approval_client(_approval_document())
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    approval = firestore_client.validate_pending_approval_decision(
        client,
        approval_id="approval-1",
        now=datetime(2026, 8, 8, 5, tzinfo=UTC),
    )

    assert approval.approval_id == "approval-1"
    assert approval.status.value == "PENDING"
    client.collection_group.assert_called_once_with(firestore_client.APPROVALS_COLLECTION)
    transaction.update.assert_not_called()
    transaction.create.assert_not_called()


def test_non_pending_approval_cannot_be_decided_again(monkeypatch) -> None:
    client, transaction, _ = _approval_client(_approval_document(status="REJECTED"))
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Approval is not pending"):
        firestore_client.validate_pending_approval_decision(
            client,
            approval_id="approval-1",
            now=datetime(2026, 8, 8, 5, tzinfo=UTC),
        )

    transaction.update.assert_not_called()
    transaction.create.assert_not_called()


def test_expired_approval_is_marked_expired_without_command(monkeypatch) -> None:
    client, transaction, snapshot = _approval_client(_approval_document())
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Approval expired"):
        firestore_client.validate_pending_approval_decision(
            client,
            approval_id="approval-1",
            now=datetime(2026, 8, 8, 6, tzinfo=UTC),
        )

    transaction.update.assert_called_once_with(
        snapshot.reference,
        {
            "status": "EXPIRED",
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    transaction.create.assert_not_called()
    client.collection.assert_not_called()


def _approval_client(document: dict[str, object]) -> tuple[Mock, Mock, Mock]:
    client = Mock()
    transaction = Mock()
    query = Mock()
    snapshot = Mock(to_dict=lambda: document)
    client.transaction.return_value = transaction
    client.collection_group.return_value.where.return_value.limit.return_value = query
    transaction.get.return_value = iter([snapshot])
    return client, transaction, snapshot


def _approval_document(*, status: str = "PENDING") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "approval_id": "approval-1",
        "incident_id": "incident-1",
        "proposal_version": 3,
        "evidence_snapshot_hash": "1" * 64,
        "action_id": "action-1",
        "tool": "recovery.set_feature_flag",
        "canonical_arguments": {
            "feature": "ExperimentalPeopleGrid",
            "enabled": False,
            "expected_current_value": True,
        },
        "canonical_arguments_hash": "2" * 64,
        "target_app_session_id": "session-1",
        "policy_version": "1",
        "risk_level": "HIGH",
        "expected_effect": "Reduce UI load.",
        "rollback_plan": "Re-enable the feature.",
        "expires_at_utc": "2026-08-08T06:00:00Z",
        "status": status,
        "approved_by": None,
        "approved_at_utc": None,
    }
