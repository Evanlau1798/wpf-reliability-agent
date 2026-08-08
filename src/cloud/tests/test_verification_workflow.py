from unittest.mock import Mock

from app import audit, firestore_client, workflow_state


def test_verification_run_atomically_commits_mitigated_state_and_metrics(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_collection = Mock()
    processed_collection = Mock()
    command_collection = Mock()
    incident_document = Mock()
    processed_document = Mock()
    command_document = Mock()
    audit_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: {
        firestore_client.INCIDENTS_COLLECTION: incident_collection,
        firestore_client.PROCESSED_RUNS_COLLECTION: processed_collection,
        firestore_client.COMMANDS_COLLECTION: command_collection,
    }[name]
    incident_collection.document.return_value = incident_document
    processed_collection.document.return_value = processed_document
    command_collection.document.return_value = command_document
    incident_document.collection.return_value.document.return_value = audit_document
    processed_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "state": "VERIFYING",
            "state_version": 9,
            "audit_sequence": 12,
            "audit_entry_hash": "a" * 64,
            "evidence_revision": 7,
        },
    )
    command_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "command_id": "command-1",
            "incident_id": "incident-1",
            "action_id": "action-1",
            "tool": "recovery.set_feature_flag",
            "status": "COMPLETED",
        },
    )
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)
    verification = {
        "outcome": "MITIGATED",
        "post_evidence_id": "post-1",
        "metrics": {"binding_errors_per_second": {"before": 5.0, "after": 0.2, "delta": -4.8}},
    }

    committed = workflow_state.commit_verification_run(
        client,
        run_key="incident-1:7:recovery.result",
        incident_id="incident-1",
        evidence_revision=7,
        command_id="command-1",
        action_id="action-1",
        target_state=workflow_state.IncidentState.MITIGATED,
        verification=verification,
    )

    assert committed is True
    audit_record = transaction.create.call_args_list[0].args[1]
    transaction.update.assert_called_once_with(
        incident_document,
        {
            "state": "MITIGATED",
            "state_version": 10,
            "audit_sequence": 13,
            "audit_entry_hash": audit_record["entry_hash"],
            "updated_at": firestore_client.firestore.SERVER_TIMESTAMP,
        },
    )
    assert transaction.create.call_args_list[0].args[0] is audit_document
    assert audit_record["previous_entry_hash"] == "a" * 64
    assert audit_record["verification"] == verification
    assert audit_record["entry_hash"] != audit.ZERO_HASH
    assert transaction.create.call_args_list[1].args[0] is processed_document
