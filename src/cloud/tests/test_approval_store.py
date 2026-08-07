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


def test_policy_version_mismatch_rejects_approval(monkeypatch) -> None:
    client, transaction, _ = _approval_client(_approval_document(policy_version="old-policy"))
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Approval policy version mismatch"):
        firestore_client.validate_pending_approval_decision(
            client,
            approval_id="approval-1",
            now=datetime(2026, 8, 8, 5, tzinfo=UTC),
        )

    transaction.update.assert_not_called()
    transaction.create.assert_not_called()


def test_proposal_version_mismatch_rejects_approval(monkeypatch) -> None:
    client, transaction, _ = _approval_client(
        _approval_document(),
        incident={"proposal_version": 4},
    )
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Approval proposal version mismatch"):
        firestore_client.validate_pending_approval_decision(
            client,
            approval_id="approval-1",
            now=datetime(2026, 8, 8, 5, tzinfo=UTC),
        )

    transaction.update.assert_not_called()
    transaction.create.assert_not_called()


def test_evidence_snapshot_mismatch_rejects_approval(monkeypatch) -> None:
    client, transaction, _ = _approval_client(
        _approval_document(),
        evidence=[("evidence-1", "b" * 64)],
    )
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Approval evidence snapshot mismatch"):
        firestore_client.validate_pending_approval_decision(
            client,
            approval_id="approval-1",
            now=datetime(2026, 8, 8, 5, tzinfo=UTC),
        )

    transaction.update.assert_not_called()
    transaction.create.assert_not_called()


def test_arguments_hash_mismatch_rejects_approval(monkeypatch) -> None:
    client, transaction, _ = _approval_client(
        _approval_document(canonical_arguments_hash="0" * 64)
    )
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Approval arguments hash mismatch"):
        firestore_client.validate_pending_approval_decision(
            client,
            approval_id="approval-1",
            now=datetime(2026, 8, 8, 5, tzinfo=UTC),
        )

    transaction.update.assert_not_called()
    transaction.create.assert_not_called()


def test_app_session_mismatch_rejects_approval(monkeypatch) -> None:
    client, transaction, _ = _approval_client(
        _approval_document(),
        incident={"proposal_version": 3, "app_session_id": "session-2"},
    )
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Approval app session mismatch"):
        firestore_client.validate_pending_approval_decision(
            client,
            approval_id="approval-1",
            now=datetime(2026, 8, 8, 5, tzinfo=UTC),
        )

    transaction.update.assert_not_called()
    transaction.create.assert_not_called()


def test_approved_decision_creates_exact_unique_mutation_command(monkeypatch) -> None:
    client, transaction, snapshot = _approval_client(_approval_document())
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)
    now = datetime(2026, 8, 8, 5, tzinfo=UTC)
    arguments_hash = firestore_client.sha256_canonical(
        {
            "feature": "ExperimentalPeopleGrid",
            "enabled": False,
            "expected_current_value": True,
        }
    )
    idempotency_key = firestore_client.sha256_canonical(
        {
            "incident_id": "incident-1",
            "proposal_version": 3,
            "tool": "recovery.set_feature_flag",
            "arguments_hash": arguments_hash,
        }
    )

    command_id = firestore_client.approve_pending_approval(
        client,
        approval_id="approval-1",
        actor="demo-operator",
        now=now,
    )

    assert command_id == f"cmd-{idempotency_key}"
    client.collection.assert_called_once_with(firestore_client.COMMANDS_COLLECTION)
    client.collection.return_value.document.assert_called_once_with(command_id)
    assert transaction.create.call_count == 2
    command_document = next(
        call.args[1]
        for call in transaction.create.call_args_list
        if call.args[1].get("tool") == "recovery.set_feature_flag"
    )
    assert command_document["incident_id"] == "incident-1"
    assert command_document["target_app_session_id"] == "session-1"
    assert command_document["tool"] == "recovery.set_feature_flag"
    assert command_document["arguments"] == {
        "feature": "ExperimentalPeopleGrid",
        "enabled": False,
        "expected_current_value": True,
    }
    assert command_document["arguments_hash"] == arguments_hash
    assert command_document["risk_level"] == "HIGH"
    assert command_document["approval_id"] == "approval-1"
    assert command_document["idempotency_key"] == idempotency_key
    assert command_document["issued_at_utc"] == now.isoformat().replace("+00:00", "Z")
    assert command_document["expires_at_utc"] == "2026-08-08T05:01:00Z"
    assert command_document["timeout_ms"] == 10_000
    assert command_document["status"] == "PENDING"
    transaction.update.assert_any_call(
        snapshot.reference,
        {
            "status": "APPROVED",
            "approved_by": "demo-operator",
            "approved_at_utc": "2026-08-08T05:00:00Z",
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    transaction.update.assert_any_call(
        snapshot.reference.parent.parent,
        {
            "audit_sequence": 9,
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    audit_event = next(
        call.args[1]
        for call in transaction.create.call_args_list
        if call.args[1].get("type") == "approval.decision"
    )
    assert audit_event["actor"] == "demo-operator"
    assert audit_event["status"] == "APPROVED"
    assert audit_event["timestamp_utc"] == "2026-08-08T05:00:00Z"


def test_rejected_decision_enters_rejected_reporting_path_without_command(monkeypatch) -> None:
    client, transaction, snapshot = _approval_client(
        _approval_document(),
        incident={
            "proposal_version": 3,
            "app_session_id": "session-1",
            "state": "AWAITING_APPROVAL",
            "state_version": 5,
            "audit_sequence": 8,
        },
    )
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)
    now = datetime(2026, 8, 8, 5, tzinfo=UTC)

    next_version = firestore_client.reject_pending_approval(
        client,
        approval_id="approval-1",
        actor="demo-operator",
        now=now,
    )

    assert next_version == 6
    assert transaction.update.call_count == 3
    transaction.update.assert_any_call(
        snapshot.reference,
        {
            "status": "REJECTED",
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    transaction.update.assert_any_call(
        snapshot.reference.parent.parent,
        {
            "state": "REJECTED",
            "state_version": 6,
            "audit_sequence": 9,
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    transaction.update.assert_any_call(
        snapshot.reference.parent.parent,
        {
            "audit_sequence": 10,
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    assert transaction.create.call_count == 2
    audit_event = next(
        call.args[1]
        for call in transaction.create.call_args_list
        if call.args[1].get("type") == "approval.decision"
    )
    assert audit_event["actor"] == "demo-operator"
    assert audit_event["status"] == "REJECTED"
    assert audit_event["timestamp_utc"] == "2026-08-08T05:00:00Z"
    assert client.collection.call_count == 0


def test_double_approve_conflicts_without_second_mutation_command(monkeypatch) -> None:
    approval_document = _approval_document()
    client, transaction, snapshot = _approval_client(approval_document)
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    def apply_update(reference, changes):
        if reference is snapshot.reference:
            approval_document.update(
                {key: value for key, value in changes.items() if key != "updated_at"}
            )

    transaction.update.side_effect = apply_update
    now = datetime(2026, 8, 8, 5, tzinfo=UTC)

    first_command_id = firestore_client.approve_pending_approval(
        client,
        approval_id="approval-1",
        actor="demo-operator",
        now=now,
    )
    with pytest.raises(ValueError, match="Approval is not pending"):
        firestore_client.approve_pending_approval(
            client,
            approval_id="approval-1",
            actor="demo-operator",
            now=now,
        )

    command_creates = [
        call
        for call in transaction.create.call_args_list
        if call.args[1].get("tool") == "recovery.set_feature_flag"
    ]
    assert first_command_id.startswith("cmd-")
    assert len(command_creates) == 1


def _approval_client(
    document: dict[str, object],
    *,
    incident: dict[str, object] | None = None,
    evidence: list[tuple[str, str]] | None = None,
) -> tuple[Mock, Mock, Mock]:
    client = Mock()
    transaction = Mock()
    query = Mock()
    snapshot = Mock(to_dict=lambda: document)
    incident_document = Mock()
    evidence_query = Mock()
    evidence_snapshots = [
        Mock(
            id=evidence_id,
            to_dict=lambda evidence_hash=evidence_hash: {"evidence_hash": evidence_hash},
        )
        for evidence_id, evidence_hash in (evidence or [("evidence-1", "a" * 64)])
    ]
    snapshot.reference.parent.parent = incident_document
    incident_document.collection.return_value = evidence_query
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: incident
        or {
            "proposal_version": 3,
            "app_session_id": "session-1",
            "state": "AWAITING_APPROVAL",
            "state_version": 5,
            "audit_sequence": 8,
        },
    )
    client.transaction.return_value = transaction
    client.collection_group.return_value.where.return_value.limit.return_value = query
    def transaction_get(target):
        if target is query:
            return iter([snapshot])
        if target is evidence_query:
            return iter(evidence_snapshots)
        raise AssertionError("Unexpected transaction query")

    transaction.get.side_effect = transaction_get
    return client, transaction, snapshot


def _approval_document(
    *,
    status: str = "PENDING",
    policy_version: str = "1",
    canonical_arguments_hash: str | None = None,
) -> dict[str, object]:
    canonical_arguments = {
        "feature": "ExperimentalPeopleGrid",
        "enabled": False,
        "expected_current_value": True,
    }
    return {
        "schema_version": "1.0",
        "approval_id": "approval-1",
        "incident_id": "incident-1",
        "proposal_version": 3,
        "evidence_snapshot_hash": firestore_client.evidence_snapshot_hash(
            [("evidence-1", "a" * 64)]
        ),
        "action_id": "action-1",
        "tool": "recovery.set_feature_flag",
        "canonical_arguments": canonical_arguments,
        "canonical_arguments_hash": canonical_arguments_hash
        or firestore_client.sha256_canonical(canonical_arguments),
        "target_app_session_id": "session-1",
        "policy_version": policy_version,
        "risk_level": "HIGH",
        "expected_effect": "Reduce UI load.",
        "rollback_plan": "Re-enable the feature.",
        "expires_at_utc": "2026-08-08T06:00:00Z",
        "status": status,
        "approved_by": None,
        "approved_at_utc": None,
    }
