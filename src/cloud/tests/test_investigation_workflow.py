import asyncio
from datetime import datetime, timezone
from unittest.mock import Mock

from app import workflow, workflow_approval
from app.agent import build_evidence_command
from app.correlation import AgentCorrelationContext, NormalizedEvidenceSummary
from app.models import AgentDecision, DecisionType

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


def test_request_evidence_decision_commits_one_atomic_durable_step(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_collection = Mock()
    command_collection = Mock()
    processed_collection = Mock()
    incident_document = Mock()
    command_document = Mock()
    processed_document = Mock()
    audit_document = Mock()
    snapshot = Mock(exists=True)
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: {
        workflow.INCIDENTS_COLLECTION: incident_collection,
        workflow.COMMANDS_COLLECTION: command_collection,
        workflow.PROCESSED_RUNS_COLLECTION: processed_collection,
    }[name]
    incident_collection.document.return_value = incident_document
    command_collection.document.return_value = command_document
    processed_collection.document.return_value = processed_document
    incident_document.get.return_value = snapshot
    incident_document.collection.return_value.document.return_value = audit_document
    processed_document.get.return_value = Mock(exists=False)
    snapshot.to_dict.return_value = {
        "state": "INVESTIGATING",
        "state_version": 4,
        "audit_sequence": 7,
        "audit_entry_hash": "7" * 64,
        "investigation_round_count": 1,
        "read_only_tool_call_count": 1,
        "read_only_tool_request_keys": ["old-request"],
        "last_investigated_evidence_revision": 1,
        "no_new_evidence_count": 0,
    }
    monkeypatch.setattr(workflow.firestore, "transactional", lambda callback: callback)
    monkeypatch.setattr(
        workflow,
        "transition_incident_in_transaction",
        lambda *_args, **_kwargs: (5, {"sequence": 8, "entry_hash": "8" * 64}),
    )
    context = AgentCorrelationContext(
        evidence=[NormalizedEvidenceSummary(
            evidence_id="binding-1",
            kind="binding.aggregate",
            app_session_id="session-1",
            observed_at_utc=NOW,
            summary="Binding error evidence.",
            element_id="element-1",
        )],
        candidate_claims=[],
        tool_calls_remaining=5,
        max_context_bytes=65_536,
        max_context_tokens=32_768,
    )
    decision = AgentDecision.model_validate({
        "schema_version": "1.0",
        "decision": DecisionType.REQUEST_EVIDENCE.value,
        "hypotheses": [{
            "claim": "Need live binding candidates.",
            "confidence": "MEDIUM",
            "evidence_ids": ["binding-1"],
            "counter_evidence_ids": [],
        }],
        "next_command": {
            "tool": "binding.get_live_candidates",
            "arguments": {"element_id": "element-1"},
        },
        "missing_evidence": ["live binding candidates"],
    })
    command = build_evidence_command(
        decision,
        incident_id="incident-1",
        evidence_revision=2,
        app_session_id="session-1",
        context=context,
        now=NOW,
    )

    committed = workflow.commit_request_evidence_run(
        client,
        run_key="incident-1:2:workflow.investigating",
        incident_id="incident-1",
        evidence_revision=2,
        trigger="workflow.investigating",
        model_id="gemini-test",
        decision=decision,
        command=command,
    )

    assert committed is True
    update = transaction.update.call_args.args[1]
    assert update["investigation_round_count"] == 2
    assert update["read_only_tool_call_count"] == 2
    assert update["pending_command_id"] == command.command_id
    assert update["current_hypotheses"][0]["claim"] == "Need live binding candidates."
    created = [call.args for call in transaction.create.call_args_list]
    assert any(args[0] is command_document and args[1]["command_id"] == command.command_id for args in created)
    assert any(args[0] is processed_document and args[1]["trigger"] == "workflow.investigating" for args in created)
    assert any(args[0] is audit_document and args[1]["type"] == "tool.request" for args in created)


def test_investigator_context_normalizes_durable_binding_and_live_candidates() -> None:
    client = Mock()
    incident_document = Mock()
    evidence_collection = Mock()
    client.collection.return_value.document.return_value = incident_document
    incident_document.collection.return_value = evidence_collection
    binding = Mock(id="binding-1")
    binding.to_dict.return_value = {
        "event_type": "binding.aggregate",
        "timestamp_utc": NOW,
        "app_session_id": "session-1",
        "correlation": {"element_id": "element-1", "binding_path": "DisplayNmae"},
        "payload": {
            "binding_path": "DisplayNmae",
            "element_type": "TextBlock",
            "element_name": "PersonName",
            "occurrence_count": 30,
            "aggregation_window_ms": 10_000,
        },
        "evidence_hash": "1" * 64,
    }
    live = Mock(id="command-1")
    live.to_dict.return_value = {
        "event_type": "tool.result",
        "tool": "binding.get_live_candidates",
        "app_session_id": "session-1",
        "evidence_hash": "2" * 64,
        "result": {
            "completed_at_utc": NOW,
            "result": {
                "candidates": [{
                    "element_id": "person-name-42",
                    "binding_path": "DisplayNmae",
                    "target_property": "Text",
                    "element_type": "TextBlock",
                    "element_name": "PersonName",
                }],
            },
        },
    }
    evidence_collection.stream.return_value = [binding, live]

    context = workflow.build_investigator_context(
        client,
        incident_id="incident-1",
        incident={"read_only_tool_call_count": 1},
    )

    assert [item.evidence_id for item in context.evidence] == ["binding-1", "command-1"]
    assert next(item for item in context.evidence if item.evidence_id == "binding-1").window_seconds == 10.0
    assert context.tool_calls_remaining == 5
    assert context.candidate_claims[0].candidate == "PersonName"
    assert context.candidate_claims[0].confidence.value == "HIGH"


def test_investigation_step_runs_one_model_decision_and_commits_request(monkeypatch) -> None:
    client = object()
    incident = {
        "state": "INVESTIGATING",
        "app_session_id": "session-1",
        "read_only_tool_call_count": 0,
    }
    context = AgentCorrelationContext(
        evidence=[NormalizedEvidenceSummary(
            evidence_id="binding-1",
            kind="binding.aggregate",
            app_session_id="session-1",
            observed_at_utc=NOW,
            summary="Binding error evidence.",
            element_id="element-1",
        )],
        candidate_claims=[],
        tool_calls_remaining=6,
        max_context_bytes=65_536,
        max_context_tokens=32_768,
    )
    decision = AgentDecision.model_validate({
        "schema_version": "1.0",
        "decision": "REQUEST_EVIDENCE",
        "hypotheses": [],
        "next_command": {
            "tool": "binding.get_live_candidates",
            "arguments": {"element_id": "element-1"},
        },
        "missing_evidence": ["live binding candidates"],
    })
    committed: list[dict[str, object]] = []
    released: list[tuple[object, str, str]] = []
    monkeypatch.setattr(workflow, "load_incident_workflow_state", lambda *_args: incident)
    monkeypatch.setattr(workflow, "build_investigator_context", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(workflow, "acquire_incident_lease", lambda *_args, **_kwargs: True, raising=False)
    monkeypatch.setattr(
        workflow,
        "release_incident_lease",
        lambda client_arg, *, incident_id, owner: released.append((client_arg, incident_id, owner)),
        raising=False,
    )
    runner = object()
    monkeypatch.setattr(workflow, "create_investigator_runner", lambda _model_id: runner, raising=False)

    async def run(*_args, **_kwargs):
        return decision

    monkeypatch.setattr(workflow, "run_investigator_once", run, raising=False)
    monkeypatch.setattr(
        workflow,
        "commit_request_evidence_run",
        lambda *_args, **kwargs: committed.append(kwargs) or True,
    )

    asyncio.run(workflow.run_investigation_step(
        client,
        work={
            "incident_id": "incident-1",
            "evidence_revision": 2,
            "trigger": "workflow.investigating",
            "event_id": "event-1",
        },
        run_key="incident-1:2:workflow.investigating",
        model_id="gemini-test",
    ))

    assert committed[0]["decision"] is decision
    assert committed[0]["command"].tool.value == "binding.get_live_candidates"
    assert committed[0]["command"].target_app_session_id == "session-1"
    assert released == [(client, "incident-1", "incident-1:2:workflow.investigating")]


def test_investigation_step_commits_proposed_action(monkeypatch) -> None:
    client = object()
    incident = {"state": "INVESTIGATING", "app_session_id": "session-1"}
    context = AgentCorrelationContext(
        evidence=[],
        candidate_claims=[],
        tool_calls_remaining=6,
        max_context_bytes=65_536,
        max_context_tokens=32_768,
    )
    decision = AgentDecision.model_validate({
        "schema_version": "1.0",
        "decision": "PROPOSE_ACTION",
        "hypotheses": [],
        "proposed_action": {
            "tool": "recovery.set_feature_flag",
            "arguments": {
                "feature": "ExperimentalPeopleGrid",
                "enabled": False,
                "expected_current_value": True,
            },
            "evidence_ids": ["binding-1"],
            "expected_effect": "Use the stable fallback people list.",
            "rollback_plan": "Re-enable ExperimentalPeopleGrid after a verified source fix.",
        },
        "missing_evidence": [],
    })
    committed: list[dict[str, object]] = []
    monkeypatch.setattr(workflow, "load_incident_workflow_state", lambda *_args: incident)
    monkeypatch.setattr(workflow, "build_investigator_context", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(workflow, "acquire_incident_lease", lambda *_args, **_kwargs: True, raising=False)
    monkeypatch.setattr(workflow, "release_incident_lease", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(workflow, "create_investigator_runner", lambda _model_id: object(), raising=False)

    async def run(*_args, **_kwargs):
        return decision

    monkeypatch.setattr(workflow, "run_investigator_once", run, raising=False)
    monkeypatch.setattr(
        workflow,
        "commit_proposed_action_run",
        lambda *_args, **kwargs: committed.append(kwargs) or True,
    )

    asyncio.run(workflow.run_investigation_step(
        client,
        work={
            "incident_id": "incident-1",
            "evidence_revision": 2,
            "trigger": "workflow.investigating",
            "event_id": "event-1",
        },
        run_key="incident-1:2:workflow.investigating",
        model_id="gemini-test",
    ))

    assert committed[0]["decision"] is decision
    assert committed[0]["now"].tzinfo is not None


def test_investigation_step_commits_reporting_decision(monkeypatch) -> None:
    client = object()
    decision = AgentDecision.model_validate({
        "schema_version": "1.0",
        "decision": "FINALIZE",
        "hypotheses": [],
        "stop_reason": "No safe mutation is required.",
        "missing_evidence": [],
    })
    monkeypatch.setattr(
        workflow,
        "load_incident_workflow_state",
        lambda *_args: {"state": "INVESTIGATING", "app_session_id": "session-1"},
    )
    monkeypatch.setattr(
        workflow,
        "build_investigator_context",
        lambda *_args, **_kwargs: AgentCorrelationContext(
            evidence=[],
            candidate_claims=[],
            tool_calls_remaining=6,
            max_context_bytes=65_536,
            max_context_tokens=32_768,
        ),
    )
    monkeypatch.setattr(workflow, "acquire_incident_lease", lambda *_args, **_kwargs: True, raising=False)
    monkeypatch.setattr(workflow, "release_incident_lease", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(workflow, "create_investigator_runner", lambda _model_id: object(), raising=False)

    async def run(*_args, **_kwargs):
        return decision

    committed: list[dict[str, object]] = []
    monkeypatch.setattr(workflow, "run_investigator_once", run, raising=False)
    monkeypatch.setattr(
        workflow,
        "commit_reporting_decision_run",
        lambda *_args, **kwargs: committed.append(kwargs) or True,
        raising=False,
    )

    target_state = asyncio.run(workflow.run_investigation_step(
        client,
        work={
            "incident_id": "incident-1",
            "evidence_revision": 2,
            "trigger": "workflow.investigating",
            "event_id": "event-1",
        },
        run_key="incident-1:2:workflow.investigating",
        model_id="gemini-test",
    ))

    assert committed[0]["decision"] is decision
    assert target_state is workflow.IncidentState.REPORTING


def test_proposed_action_creates_bound_pending_approval_without_command(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_collection = Mock()
    processed_collection = Mock()
    incident_document = Mock()
    processed_document = Mock()
    evidence_collection = Mock()
    approval_collection = Mock()
    audit_collection = Mock()
    approval_document = Mock()
    snapshot = Mock(exists=True)
    evidence = Mock(id="binding-1")
    evidence.to_dict.return_value = {"evidence_hash": "1" * 64}
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: {
        workflow.INCIDENTS_COLLECTION: incident_collection,
        workflow.PROCESSED_RUNS_COLLECTION: processed_collection,
    }[name]
    incident_collection.document.return_value = incident_document
    processed_collection.document.return_value = processed_document
    processed_document.get.return_value = Mock(exists=False)
    incident_document.get.return_value = snapshot
    incident_document.collection.side_effect = lambda name: {
        workflow.EVIDENCE_COLLECTION: evidence_collection,
        workflow_approval.APPROVALS_COLLECTION: approval_collection,
        workflow.AUDIT_COLLECTION: audit_collection,
    }[name]
    approval_collection.document.return_value = approval_document
    transaction.get.return_value = [evidence]
    snapshot.to_dict.return_value = {
        "state": "INVESTIGATING",
        "state_version": 4,
        "audit_sequence": 7,
        "audit_entry_hash": "7" * 64,
        "app_session_id": "session-1",
        "proposal_version": 0,
        "mutation_proposal_count": 0,
        "investigation_round_count": 1,
        "last_investigated_evidence_revision": 1,
        "no_new_evidence_count": 0,
    }
    monkeypatch.setattr(workflow.firestore, "transactional", lambda callback: callback)
    monkeypatch.setattr(
        workflow_approval,
        "transition_incident_in_transaction",
        lambda *_args, **_kwargs: (5, {"sequence": 8, "entry_hash": "8" * 64}),
    )
    decision = AgentDecision.model_validate({
        "schema_version": "1.0",
        "decision": "PROPOSE_ACTION",
        "hypotheses": [{
            "claim": "The experimental grid is the failing path.",
            "confidence": "HIGH",
            "evidence_ids": ["binding-1"],
            "counter_evidence_ids": [],
        }],
        "proposed_action": {
            "tool": "recovery.set_feature_flag",
            "arguments": {
                "feature": "ExperimentalPeopleGrid",
                "enabled": False,
                "expected_current_value": True,
            },
            "evidence_ids": ["binding-1"],
            "expected_effect": "Use the stable fallback people list.",
            "rollback_plan": "Re-enable ExperimentalPeopleGrid after a verified source fix.",
        },
        "missing_evidence": [],
    })

    committed = workflow_approval.commit_proposed_action_run(
        client,
        run_key="incident-1:2:workflow.investigating",
        incident_id="incident-1",
        evidence_revision=2,
        trigger="workflow.investigating",
        model_id="gemini-test",
        decision=decision,
        now=NOW,
    )

    assert committed is True
    approval = next(call.args[1] for call in transaction.create.call_args_list if call.args[0] is approval_document)
    assert approval["status"] == "PENDING"
    assert approval["proposal_version"] == 1
    assert approval["tool"] == "recovery.set_feature_flag"
    assert approval["canonical_arguments"] == decision.proposed_action.arguments
    assert approval["target_app_session_id"] == "session-1"
    update = transaction.update.call_args.args[1]
    assert update["proposal_version"] == 1
    assert update["mutation_proposal_count"] == 1
    assert update["approval_id"] == approval["approval_id"]
    assert update["pending_action_id"] == approval["action_id"]
    assert not any(call.args[1].get("tool") == "recovery.set_feature_flag" for call in transaction.create.call_args_list if isinstance(call.args[1], dict) and call.args[0] is not approval_document)
