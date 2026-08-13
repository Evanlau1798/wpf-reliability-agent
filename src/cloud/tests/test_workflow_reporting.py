import asyncio
from datetime import UTC, datetime
from unittest.mock import Mock

from app import workflow_reporting
from app.models import AgentDecision, IncidentReport


def test_reporting_decision_commits_one_atomic_transition(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_collection = Mock()
    processed_collection = Mock()
    incident_document = Mock()
    processed_document = Mock()
    snapshot = Mock(exists=True)
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: {
        workflow_reporting.INCIDENTS_COLLECTION: incident_collection,
        workflow_reporting.PROCESSED_RUNS_COLLECTION: processed_collection,
    }[name]
    incident_collection.document.return_value = incident_document
    processed_collection.document.return_value = processed_document
    processed_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = snapshot
    snapshot.to_dict.return_value = {
        "state": "INVESTIGATING",
        "state_version": 4,
        "audit_sequence": 7,
        "audit_entry_hash": "7" * 64,
        "investigation_round_count": 1,
        "last_investigated_evidence_revision": 1,
        "no_new_evidence_count": 0,
    }
    monkeypatch.setattr(workflow_reporting.firestore, "transactional", lambda callback: callback)
    transitions: list[dict[str, object]] = []
    monkeypatch.setattr(
        workflow_reporting,
        "transition_incident_in_transaction",
        lambda *_args, **kwargs: transitions.append(kwargs) or (5, {"sequence": 8, "entry_hash": "8" * 64}),
    )
    decision = AgentDecision.model_validate({
        "schema_version": "1.0",
        "decision": "FINALIZE",
        "hypotheses": [],
        "stop_reason": "No safe mutation is required.",
        "missing_evidence": [],
    })

    committed = workflow_reporting.commit_reporting_decision_run(
        client,
        run_key="incident-1:2:workflow.investigating",
        incident_id="incident-1",
        evidence_revision=2,
        trigger="workflow.investigating",
        model_id="gemini-test",
        decision=decision,
    )

    assert committed is True
    assert transitions[0]["expected_state"] is workflow_reporting.IncidentState.INVESTIGATING
    assert transitions[0]["target_state"] is workflow_reporting.IncidentState.REPORTING
    update = transaction.update.call_args.args[1]
    assert update["investigation_round_count"] == 2
    assert update["last_investigated_evidence_revision"] == 2
    assert update["no_new_evidence_count"] == 0
    assert update["investigation_outcome"] == "FINALIZE"
    assert update["investigation_stop_reason"] == "No safe mutation is required."


def test_inconclusive_reporting_marks_manual_review_atomically(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_collection = Mock()
    processed_collection = Mock()
    incident_document = Mock()
    processed_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: {
        workflow_reporting.INCIDENTS_COLLECTION: incident_collection,
        workflow_reporting.PROCESSED_RUNS_COLLECTION: processed_collection,
    }[name]
    incident_collection.document.return_value = incident_document
    processed_collection.document.return_value = processed_document
    processed_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"state": "INVESTIGATING", "state_version": 4},
    )
    monkeypatch.setattr(workflow_reporting.firestore, "transactional", lambda callback: callback)
    transitions: list[dict[str, object]] = []
    monkeypatch.setattr(
        workflow_reporting,
        "transition_incident_in_transaction",
        lambda *_args, **kwargs: transitions.append(kwargs) or (5, {"sequence": 8, "entry_hash": "8" * 64}),
    )

    committed = workflow_reporting.commit_inconclusive_reporting_run(
        client,
        run_key="incident-1:7:workflow.investigating",
        incident_id="incident-1",
        evidence_revision=7,
        trigger="workflow.investigating",
        model_id="gemini-test",
        reason="Read-only tool call limit reached",
    )

    assert committed is True
    assert transitions[0]["target_state"] is workflow_reporting.IncidentState.REPORTING
    update = transaction.update.call_args.args[1]
    assert update["investigation_outcome"] == "INCONCLUSIVE"
    assert update["investigation_stop_reason"] == "Read-only tool call limit reached"
    assert update["manual_review_required"] is True


def test_terminal_state_commits_reporting_transition(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_collection = Mock()
    processed_collection = Mock()
    incident_document = Mock()
    processed_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: {
        workflow_reporting.INCIDENTS_COLLECTION: incident_collection,
        workflow_reporting.PROCESSED_RUNS_COLLECTION: processed_collection,
    }[name]
    incident_collection.document.return_value = incident_document
    processed_collection.document.return_value = processed_document
    processed_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "state": "MITIGATED",
            "state_version": 7,
            "audit_sequence": 12,
            "audit_entry_hash": "c" * 64,
        },
    )
    monkeypatch.setattr(workflow_reporting.firestore, "transactional", lambda callback: callback)
    transitions: list[dict[str, object]] = []
    monkeypatch.setattr(
        workflow_reporting,
        "transition_incident_in_transaction",
        lambda *_args, **kwargs: transitions.append(kwargs) or (8, {"sequence": 13, "entry_hash": "d" * 64}),
    )

    committed = workflow_reporting.commit_terminal_reporting_run(
        client,
        run_key="incident-1:7:workflow.reporting",
        incident_id="incident-1",
        evidence_revision=7,
        trigger="workflow.reporting",
        model_id="gemini-test",
    )

    assert committed is True
    assert transitions[0]["expected_state"] is workflow_reporting.IncidentState.MITIGATED
    assert transitions[0]["target_state"] is workflow_reporting.IncidentState.REPORTING
    transaction.create.assert_called_once_with(
        processed_document,
        {
            "incident_id": "incident-1",
            "evidence_revision": 7,
            "trigger": "workflow.reporting",
            "model_id": "gemini-test",
            "processed_at": workflow_reporting.firestore.SERVER_TIMESTAMP,
        },
    )


def test_reporter_input_is_built_only_from_finalized_ledgers() -> None:
    client = Mock()
    incident_collection = Mock()
    command_collection = Mock()
    incident_document = Mock()
    evidence_collection = Mock()
    approval_collection = Mock()
    audit_collection = Mock()
    command_query = Mock()
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    client.collection.side_effect = lambda name: {
        workflow_reporting.INCIDENTS_COLLECTION: incident_collection,
        workflow_reporting.COMMANDS_COLLECTION: command_collection,
    }[name]
    incident_collection.document.return_value = incident_document
    incident_document.collection.side_effect = lambda name: {
        workflow_reporting.EVIDENCE_COLLECTION: evidence_collection,
        workflow_reporting.APPROVALS_COLLECTION: approval_collection,
        workflow_reporting.AUDIT_COLLECTION: audit_collection,
    }[name]
    evidence_collection.stream.return_value = [
        _snapshot("evidence-1", {
            "event_type": "binding.aggregate",
            "timestamp_utc": now,
            "evidence_hash": "1" * 64,
            "payload": {"occurrence_count": 12},
        })
    ]
    command_collection.where.return_value = command_query
    command_query.stream.return_value = [
        _snapshot("command-1", {
            "incident_id": "incident-1",
            "tool": "binding.get_live_candidates",
            "arguments_hash": "2" * 64,
            "status": "COMPLETED",
            "issued_at_utc": now,
            "result_hash": "3" * 64,
        })
    ]
    approval_collection.stream.return_value = [
        _snapshot("approval-1", {
            "approval_id": "approval-1",
            "action_id": "action-1",
            "tool": "recovery.set_feature_flag",
            "canonical_arguments_hash": "4" * 64,
            "expected_effect": "Use the stable fallback list.",
            "rollback_plan": "Re-enable the feature.",
            "status": "APPROVED",
            "approved_at_utc": now,
        })
    ]
    audit_collection.stream.return_value = [
        _snapshot("9", {
            "sequence": 9,
            "type": "mutation.verification",
            "actor_type": "SYSTEM",
            "actor_id": "deterministic-verifier",
            "payload_hash": "5" * 64,
            "entry_hash": "6" * 64,
            "timestamp_utc": now,
            "command_id": "command-1",
            "action_id": "action-1",
            "verification_hash": "7" * 64,
            "outcome": "MITIGATED",
            "evidence_ids": ["evidence-1", "command-1"],
            "metrics": {
                "binding_errors_per_second": {
                    "before": 4.9,
                    "after": 0.0,
                    "unit": "errors_per_second",
                }
            },
        })
    ]

    reporter_input = workflow_reporting.build_reporter_input(client, "incident-1")

    assert [item.reference for item in reporter_input.evidence] == ["evidence-1"]
    assert [item.reference for item in reporter_input.tools] == ["command-1"]
    assert [item.reference for item in reporter_input.approvals] == ["approval-1"]
    assert [item.reference for item in reporter_input.verification] == ["9"]
    assert reporter_input.verification[0].kind == "mutation.verification"
    assert '"evidence_ids"' in reporter_input.verification[0].summary
    assert '"metrics"' in reporter_input.verification[0].summary


def test_report_commit_persists_hash_and_closes_atomically(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_collection = Mock()
    processed_collection = Mock()
    incident_document = Mock()
    processed_document = Mock()
    report_collection = Mock()
    report_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: {
        workflow_reporting.INCIDENTS_COLLECTION: incident_collection,
        workflow_reporting.PROCESSED_RUNS_COLLECTION: processed_collection,
    }[name]
    incident_collection.document.return_value = incident_document
    processed_collection.document.return_value = processed_document
    incident_document.collection.return_value = report_collection
    report_collection.document.return_value = report_document
    processed_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"state": "REPORTING", "state_version": 8},
    )
    monkeypatch.setattr(workflow_reporting.firestore, "transactional", lambda callback: callback)
    transitions: list[dict[str, object]] = []
    monkeypatch.setattr(
        workflow_reporting,
        "transition_incident_in_transaction",
        lambda *_args, **kwargs: transitions.append(kwargs) or (9, {"sequence": 14, "entry_hash": "e" * 64}),
    )
    report = IncidentReport.model_validate({
        "schema_version": "1.0",
        "incident_id": "incident-1",
        "status": "FAILED_SAFE",
        "severity": "ERROR",
        "confidence": "LOW",
        "timeline": [],
        "evidence": [],
        "claims": [],
        "verification": [],
        "metadata": {
            "model_id": "gemini-test",
            "prompt_version": "1",
            "schema_version": "1.0",
            "policy_version": "1",
            "reuse_revision": "9" * 40,
        },
    })

    committed = workflow_reporting.commit_report_run(
        client,
        run_key="incident-1:7:workflow.report",
        incident_id="incident-1",
        evidence_revision=7,
        trigger="workflow.report",
        model_id="gemini-test",
        report=report,
        version="1",
    )

    assert committed is True
    saved = next(call.args[1] for call in transaction.create.call_args_list if call.args[0] is report_document)
    assert saved["metadata"]["report_sha256"] == workflow_reporting.sha256_canonical(
        report.model_dump(mode="json")
    )
    assert transitions[0]["expected_state"] is workflow_reporting.IncidentState.REPORTING
    assert transitions[0]["target_state"] is workflow_reporting.IncidentState.CLOSED


def test_reporting_step_runs_one_reporter_and_commits(monkeypatch) -> None:
    client = Mock()
    incident_document = Mock()
    client.collection.return_value.document.return_value = incident_document
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {"state": "REPORTING", "severity": "ERROR"},
    )
    reporter_input = workflow_reporting.ReporterInput(
        evidence=[], tools=[], approvals=[], verification=[]
    )
    report = IncidentReport.model_validate({
        "schema_version": "1.0",
        "incident_id": "incident-1",
        "status": "FAILED_SAFE",
        "severity": "ERROR",
        "confidence": "LOW",
        "timeline": [],
        "evidence": [],
        "claims": [],
        "verification": [],
        "metadata": {
            "model_id": "gemini-test",
            "prompt_version": "1",
            "schema_version": "1.0",
            "policy_version": "1",
            "reuse_revision": "9" * 40,
        },
    })
    monkeypatch.setattr(workflow_reporting, "acquire_incident_lease", lambda *_args, **_kwargs: True, raising=False)
    monkeypatch.setattr(workflow_reporting, "release_incident_lease", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(workflow_reporting, "build_reporter_input", lambda *_args: reporter_input)
    runner = object()
    monkeypatch.setattr(workflow_reporting, "create_reporter_runner", lambda _model_id: runner, raising=False)
    reporter_calls: list[dict[str, object]] = []

    async def run_reporter(*_args, **kwargs):
        reporter_calls.append(kwargs)
        return report

    monkeypatch.setattr(workflow_reporting, "run_reporter_once", run_reporter, raising=False)
    commits: list[dict[str, object]] = []
    monkeypatch.setattr(
        workflow_reporting,
        "commit_report_run",
        lambda *_args, **kwargs: commits.append(kwargs) or True,
    )

    committed = asyncio.run(workflow_reporting.run_reporting_step(
        client,
        work={
            "incident_id": "incident-1",
            "evidence_revision": 7,
            "trigger": "workflow.report",
            "event_id": "event-1",
        },
        run_key="incident-1:7:workflow.report",
        model_id="gemini-test",
        build_revision="a" * 40,
    ))

    assert committed is True
    assert reporter_calls[0]["reporter_input"] is reporter_input
    assert reporter_calls[0]["severity"].value == "ERROR"
    assert reporter_calls[0]["policy_version"] == "1"
    assert len(commits) == 1
    assert commits[0]["report"].metadata.application_version == "0.1.0"
    assert commits[0]["report"].metadata.build_revision == "a" * 40


def _snapshot(snapshot_id: str, data: dict[str, object]) -> Mock:
    snapshot = Mock(id=snapshot_id)
    snapshot.to_dict.return_value = data
    return snapshot
