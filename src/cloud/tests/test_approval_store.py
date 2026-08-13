from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Lock
from unittest.mock import Mock

import pytest
from app import firestore_client
from google.api_core.exceptions import AlreadyExists

def test_pending_approval_can_be_loaded_for_decision(monkeypatch) -> None:
    client, transaction, snapshot = _approval_client(_approval_document())
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    approval = firestore_client.validate_pending_approval_decision(
        client,
        approval_id="approval-1",
        now=datetime(2026, 8, 8, 5, tzinfo=UTC),
    )

    assert approval.approval_id == "approval-1"
    assert approval.status.value == "PENDING"
    client.collection_group.assert_called_once_with(firestore_client.APPROVALS_COLLECTION)
    snapshot.reference.parent.parent.collection.return_value.order_by.assert_called_once_with("__name__")
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
    approval_document = _approval_document()
    client, transaction, snapshot = _approval_client(approval_document)
    logger = Mock()
    monkeypatch.setattr(firestore_client, "LOGGER", logger, raising=False)
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)
    now = datetime(2026, 8, 8, 5, tzinfo=UTC)
    arguments_hash = approval_document["canonical_arguments_hash"]
    command_identity_key = firestore_client.sha256_canonical({
        "incident_id": "incident-1", "proposal_version": 3,
        "tool": "recovery.set_feature_flag", "arguments_hash": arguments_hash,
    })
    mutation_execution_key = firestore_client.sha256_canonical({
        "incident_id": "incident-1", "action_id": "action-1", "arguments_hash": arguments_hash,
    })

    command_id = firestore_client.approve_pending_approval(
        client,
        approval_id="approval-1",
        actor="demo-operator",
        now=now,
    )

    assert command_id == f"cmd-{command_identity_key}"
    logger.info.assert_called_once_with(
        "approval_decided incident_id=%s approval_id=%s command_id=%s decision=approve",
        "incident-1", "approval-1", command_id,
    )
    client.collection.assert_called_once_with(firestore_client.COMMANDS_COLLECTION)
    client.collection.return_value.document.assert_called_once_with(command_id)
    assert transaction.create.call_count == 3
    command_document = next(
        call.args[1]
        for call in transaction.create.call_args_list
        if call.args[1].get("tool") == "recovery.set_feature_flag"
    )
    assert command_document == {
        "schema_version": "1.0", "command_id": command_id,
        "incident_id": "incident-1", "target_app_session_id": "session-1",
        "tool": "recovery.set_feature_flag", "arguments": approval_document["canonical_arguments"],
        "arguments_hash": arguments_hash, "risk_level": "HIGH",
        "approval_id": "approval-1", "idempotency_key": mutation_execution_key,
        "proposal_version": 3, "action_id": "action-1",
        "issued_at_utc": "2026-08-08T05:00:00Z", "expires_at_utc": "2026-08-08T05:01:00Z",
        "timeout_ms": 10_000, "status": "PENDING",
        "created_at": firestore_client.firestore.SERVER_TIMESTAMP,
        "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
    }
    transaction.update.assert_any_call(snapshot.reference, {
        "status": "APPROVED", "approved_by": "demo-operator",
        "approved_at_utc": "2026-08-08T05:00:00Z",
        "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
    })
    state_audit = next(call.args[1] for call in transaction.create.call_args_list if call.args[1].get("type") == "state.transition")
    assert state_audit["from_state"] == "AWAITING_APPROVAL"
    assert state_audit["to_state"] == "EXECUTING"
    assert any(call.args[1].get("state") == "EXECUTING"
               for call in transaction.update.call_args_list)
    audit_event = next(
        call.args[1]
        for call in transaction.create.call_args_list
        if call.args[1].get("type") == "approval.decision"
    )
    assert any(call.args[1].get("audit_entry_hash") == audit_event["entry_hash"] for call in transaction.update.call_args_list)
    assert audit_event["actor_type"] == "HUMAN"
    assert audit_event["actor_id"] == "demo-operator"
    assert audit_event["previous_entry_hash"] == state_audit["entry_hash"]
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
            "evidence_revision": 7,
            "audit_sequence": 8,
            "audit_entry_hash": "8" * 64,
        },
    )
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)
    now = datetime(2026, 8, 8, 5, tzinfo=UTC)
    result = firestore_client.reject_pending_approval(
        client,
        approval_id="approval-1",
        actor="demo-operator",
        now=now,
    )
    assert result == (6, "incident-1", 7)
    assert transaction.update.call_count == 3
    transaction.update.assert_any_call(
        snapshot.reference,
        {
            "status": "REJECTED",
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    state_audit = next(call.args[1] for call in transaction.create.call_args_list if call.args[1].get("type") == "state.transition")
    transaction.update.assert_any_call(
        snapshot.reference.parent.parent,
        {
            "state": "REJECTED",
            "state_version": 6,
            "audit_sequence": 9,
            "audit_entry_hash": state_audit["entry_hash"],
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    assert transaction.create.call_count == 2
    audit_event = next(
        call.args[1]
        for call in transaction.create.call_args_list
        if call.args[1].get("type") == "approval.decision"
    )
    assert any(call.args[1].get("audit_entry_hash") == audit_event["entry_hash"] for call in transaction.update.call_args_list)
    assert audit_event["actor_type"] == "HUMAN"
    assert audit_event["actor_id"] == "demo-operator"
    assert audit_event["previous_entry_hash"] == state_audit["entry_hash"]
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


def test_approved_action_cannot_substitute_different_arguments(monkeypatch) -> None:
    approval_document = _approval_document()
    approval_document["canonical_arguments"] = {
        "feature": "ExperimentalPeopleGrid",
        "enabled": True,
        "expected_current_value": True,
    }
    client, transaction, _ = _approval_client(approval_document)
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Approval arguments hash mismatch"):
        firestore_client.approve_pending_approval(
            client,
            approval_id="approval-1",
            actor="demo-operator",
            now=datetime(2026, 8, 8, 5, tzinfo=UTC),
        )

    client.collection.assert_not_called()
    transaction.create.assert_not_called()


def test_occurrence_only_evidence_update_does_not_stale_approval(monkeypatch) -> None:
    client, transaction, _ = _approval_client(
        _approval_document(),
        occurrence_count=42,
    )
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    command_id = firestore_client.approve_pending_approval(
        client,
        approval_id="approval-1",
        actor="demo-operator",
        now=datetime(2026, 8, 8, 5, tzinfo=UTC),
    )

    command_creates = [
        call
        for call in transaction.create.call_args_list
        if call.args[1].get("tool") == "recovery.set_feature_flag"
    ]
    assert command_id.startswith("cmd-")
    assert len(command_creates) == 1


def test_material_evidence_update_stales_approval_before_command(monkeypatch) -> None:
    client, transaction, _ = _approval_client(
        _approval_document(),
        evidence=[("evidence-1", "b" * 64)],
    )
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Approval evidence snapshot mismatch"):
        firestore_client.approve_pending_approval(
            client,
            approval_id="approval-1",
            actor="demo-operator",
            now=datetime(2026, 8, 8, 5, tzinfo=UTC),
        )

    client.collection.assert_not_called()
    transaction.create.assert_not_called()


def test_concurrent_approve_attempts_create_one_mutation_command(monkeypatch) -> None:
    monkeypatch.setattr(firestore_client.firestore, "transactional", lambda callback: callback)
    command_barrier = Barrier(2)
    command_lock = Lock()
    created_command_ids: set[str] = set()

    def approve() -> str:
        client, transaction, _ = _approval_client(_approval_document())

        def create_once(_document, payload):
            command_id = payload.get("command_id")
            if not isinstance(command_id, str):
                return
            command_barrier.wait(timeout=2)
            with command_lock:
                if command_id in created_command_ids:
                    raise AlreadyExists("mutation command already exists")
                created_command_ids.add(command_id)

        transaction.create.side_effect = create_once
        return firestore_client.approve_pending_approval(
            client,
            approval_id="approval-1",
            actor="demo-operator",
            now=datetime(2026, 8, 8, 5, tzinfo=UTC),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(approve) for _ in range(2)]
        successes: list[str] = []
        conflicts = 0
        for future in futures:
            try:
                successes.append(future.result())
            except AlreadyExists:
                conflicts += 1

    assert len(successes) == 1
    assert conflicts == 1
    assert created_command_ids == {successes[0]}


def _approval_client(
    document: dict[str, object],
    *,
    incident: dict[str, object] | None = None,
    evidence: list[tuple[str, str]] | None = None,
    occurrence_count: int | None = None,
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
            to_dict=lambda evidence_hash=evidence_hash: {
                "evidence_hash": evidence_hash,
                "payload": {"occurrence_count": occurrence_count},
            },
        )
        for evidence_id, evidence_hash in (evidence or [("evidence-1", "a" * 64), ("later", "b" * 64)])
    ]
    snapshot.reference.parent.parent = incident_document
    evidence_collection = incident_document.collection.return_value
    evidence_collection.order_by.return_value = evidence_query
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: incident
        or {
            "proposal_version": 3,
            "app_session_id": "session-1",
            "state": "AWAITING_APPROVAL",
            "state_version": 5,
            "audit_sequence": 8,
            "audit_entry_hash": "8" * 64,
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
        "evidence_ids": ["evidence-1"],
        "evidence_snapshot_hash": firestore_client.evidence_snapshot_hash([("evidence-1", "a" * 64)]),
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
