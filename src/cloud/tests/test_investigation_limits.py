import asyncio

import pytest
from app import workflow
from app.models import AgentDecision

WORK = {
    "incident_id": "incident-1",
    "evidence_revision": 2,
    "trigger": "workflow.investigating",
    "event_id": "event-1",
}


@pytest.mark.parametrize(
    ("limit_state", "reason"),
    [
        ({"investigation_round_count": workflow.MAX_INVESTIGATION_ROUNDS}, "Investigation round limit reached"),
        ({"read_only_tool_call_count": workflow.MAX_READ_ONLY_TOOL_CALLS}, "Read-only tool call limit reached"),
        ({
            "last_investigated_evidence_revision": 2,
            "no_new_evidence_count": workflow.MAX_NO_NEW_EVIDENCE_ROUNDS - 1,
        }, "No-new-evidence limit reached"),
    ],
)
def test_limit_enters_reporting_without_another_model_call(monkeypatch, limit_state, reason: str) -> None:
    model_calls: list[object] = []
    reported: list[dict[str, object]] = []
    incident = {
        "state": "INVESTIGATING",
        "app_session_id": "session-1",
        "investigation_round_count": 0,
        "read_only_tool_call_count": 0,
        "last_investigated_evidence_revision": 1,
        "no_new_evidence_count": 0,
        **limit_state,
    }
    monkeypatch.setattr(
        workflow,
        "load_incident_workflow_state",
        lambda *_args: incident,
    )
    monkeypatch.setattr(workflow, "acquire_incident_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(workflow, "release_incident_lease", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow, "build_investigator_context", lambda *_args, **_kwargs: object())

    async def run(*_args, **_kwargs):
        model_calls.append(object())
        return AgentDecision.model_validate({
            "schema_version": "1.0",
            "decision": "FINALIZE",
            "hypotheses": [],
            "missing_evidence": [],
        })

    monkeypatch.setattr(workflow, "run_investigator_once", run)
    monkeypatch.setattr(
        workflow,
        "commit_inconclusive_reporting_run",
        lambda *_args, **kwargs: reported.append(kwargs) or True,
        raising=False,
    )

    target = asyncio.run(workflow.run_investigation_step(
        object(), work=WORK, run_key="incident-1:2:workflow.investigating", model_id="gemini-test"
    ))

    assert model_calls == []
    assert target is workflow.IncidentState.REPORTING
    assert reported[0]["reason"] == reason


@pytest.mark.parametrize("failure", ["invalid-model-output", "duplicate-tool-request"])
def test_non_retryable_investigation_failure_enters_reporting(monkeypatch, failure: str) -> None:
    reported: list[dict[str, object]] = []
    monkeypatch.setattr(
        workflow,
        "load_incident_workflow_state",
        lambda *_args: {"state": "INVESTIGATING", "app_session_id": "session-1"},
    )
    monkeypatch.setattr(workflow, "acquire_incident_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(workflow, "release_incident_lease", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow, "build_investigator_context", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(workflow, "create_investigator_runner", lambda _model_id: object())
    monkeypatch.setattr(
        workflow,
        "commit_inconclusive_reporting_run",
        lambda *_args, **kwargs: reported.append(kwargs) or True,
        raising=False,
    )

    if failure == "invalid-model-output":
        async def run(*_args, **_kwargs):
            raise ValueError("invalid model output")
    else:
        decision = AgentDecision.model_validate({
            "schema_version": "1.0",
            "decision": "REQUEST_EVIDENCE",
            "hypotheses": [],
            "next_command": {"tool": "binding.get_live_candidates", "arguments": {}},
            "missing_evidence": [],
        })

        async def run(*_args, **_kwargs):
            return decision

        monkeypatch.setattr(workflow, "build_evidence_command", lambda *_args, **_kwargs: object())
        monkeypatch.setattr(
            workflow,
            "commit_request_evidence_run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("Duplicate tool request")),
        )

    monkeypatch.setattr(workflow, "run_investigator_once", run)
    target = asyncio.run(workflow.run_investigation_step(
        object(), work=WORK, run_key="incident-1:2:workflow.investigating", model_id="gemini-test"
    ))

    assert target is workflow.IncidentState.REPORTING
    assert reported[0]["reason"] in {"Investigator output remained invalid", "Duplicate tool request"}
