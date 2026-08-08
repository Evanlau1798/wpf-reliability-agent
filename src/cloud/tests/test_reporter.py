from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import asyncio
import json
import pytest
from pydantic import ValidationError


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_reporter_instruction_forbids_side_effects_and_new_evidence() -> None:
    from app.reporting import REPORTER_INSTRUCTION

    instruction = REPORTER_INSTRUCTION.lower()

    assert "do not call tools" in instruction
    assert "do not request new evidence" in instruction
    assert "do not change the incident ledger" in instruction
    assert "finalized evidence" in instruction


def test_reporter_input_contains_only_compact_finalized_records() -> None:
    from app.reporting import ReporterInput

    record = {
        "reference": "evidence-1",
        "kind": "binding.error.aggregate",
        "summary": "Binding errors dropped after mitigation.",
        "payload_hash": "a" * 64,
        "related_ids": ["command-1"],
        "timestamp_utc": "2026-08-08T06:00:00Z",
    }
    reporter_input = ReporterInput.model_validate(
        {"evidence": [record], "tools": [], "approvals": [], "verification": []}
    )

    assert set(ReporterInput.model_fields) == {"evidence", "tools", "approvals", "verification"}
    assert reporter_input.evidence[0].reference == "evidence-1"
    with pytest.raises(ValidationError):
        ReporterInput.model_validate(
            {"evidence": [], "tools": [], "approvals": [], "verification": [], "raw_events": []}
        )


def test_reporter_agent_binds_incident_report_schema_and_valid_output_parses() -> None:
    from app.models import IncidentReport
    from app.reporting import build_reporter_agent

    agent = build_reporter_agent("gemini-test")
    report = IncidentReport.model_validate_json(
        (FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8")
    )

    assert agent.output_schema is IncidentReport
    assert report.status.value == "MITIGATED"


def test_reporter_rejects_claim_with_unknown_evidence_id() -> None:
    from app.models import IncidentReport

    payload = json.loads((FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8"))
    payload["claims"][0]["evidence_ids"] = ["missing-evidence"]

    with pytest.raises(ValidationError, match="unknown evidence IDs"):
        IncidentReport.model_validate(payload)


def test_reporter_rejects_reverse_timeline_order() -> None:
    from app.models import IncidentReport

    payload = json.loads((FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8"))
    payload["timeline"].reverse()

    with pytest.raises(ValidationError, match="timeline must be ordered"):
        IncidentReport.model_validate(payload)


def test_reporter_rejects_temporary_mitigation_without_finalized_approval() -> None:
    from app.models import IncidentReport
    from app.reporting import ReporterInput, validate_reporter_output

    report = IncidentReport.model_validate_json(
        (FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8")
    )
    reporter_input = ReporterInput(evidence=[], tools=[], approvals=[], verification=[])

    with pytest.raises(ValueError, match="unknown approval ID"):
        validate_reporter_output(reporter_input, report)


def test_reporter_rejects_mitigated_report_without_finalized_post_action_verification() -> None:
    from app.models import IncidentReport
    from app.reporting import ReporterInput, validate_reporter_output

    report = IncidentReport.model_validate_json(
        (FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8")
    )
    reporter_input = ReporterInput.model_validate(
        {
            "evidence": [],
            "tools": [],
            "approvals": [
                {
                    "reference": "approval-1",
                    "kind": "approval.approved",
                    "summary": "Approved feature rollback.",
                    "payload_hash": "a" * 64,
                    "related_ids": ["action-1"],
                    "timestamp_utc": "2026-08-08T06:00:00Z",
                }
            ],
            "verification": [],
        }
    )

    with pytest.raises(ValueError, match="post-action verification"):
        validate_reporter_output(reporter_input, report)


def test_reporter_rejects_resolved_status_for_temporary_feature_rollback() -> None:
    from app.models import IncidentReport

    payload = json.loads((FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8"))
    payload["status"] = "RESOLVED"

    with pytest.raises(ValidationError, match="temporary feature rollback must remain MITIGATED"):
        IncidentReport.model_validate(payload)


def test_reporter_repairs_schema_once_then_uses_deterministic_fallback() -> None:
    from app.models import Severity
    from app.reporting import ReporterInput, run_reporter_once

    calls: list[dict[str, object]] = []
    outputs = [{"schema_version": "1.0"}, {"schema_version": "1.0"}]

    class SessionService:
        async def create_session(self, **kwargs):
            calls.append({"create_session": kwargs})

    class Runner:
        app_name = "wpf_reliability_agent"
        session_service = SessionService()

        def run_async(self, **kwargs):
            calls.append({"run_async": kwargs})
            output = outputs.pop(0)

            async def events():
                yield SimpleNamespace(
                    is_final_response=lambda: True,
                    output=output,
                    content=None,
                )

            return events()

    report = asyncio.run(
        run_reporter_once(
            Runner(),
            incident_id="incident-1",
            run_key="incident-1:report:1",
            reporter_input=ReporterInput(evidence=[], tools=[], approvals=[], verification=[]),
            severity=Severity.ERROR,
            model_id="gemini-test",
            prompt_version="1",
            policy_version="1",
            reuse_revision="9" * 40,
        )
    )

    run_calls = [call["run_async"] for call in calls if "run_async" in call]
    assert len(run_calls) == 2
    assert "repair" in run_calls[1]["new_message"].parts[0].text.lower()
    assert report.status.value == "FAILED_SAFE"
    assert report.incident_id == "incident-1"
    assert report.claims == []
    assert report.metadata.model_id == "gemini-test"
    assert report.metadata.reuse_revision == "9" * 40


def test_report_json_persistence_keeps_required_metadata() -> None:
    from app.models import IncidentReport
    from app.reporting import persist_report_json

    client = Mock()
    incident_document = Mock()
    report_document = Mock()
    client.collection.return_value.document.return_value = incident_document
    incident_document.collection.return_value.document.return_value = report_document
    report = IncidentReport.model_validate_json(
        (FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8")
    )

    persist_report_json(client, report, version="1")

    client.collection.assert_called_once_with("incidents")
    client.collection.return_value.document.assert_called_once_with("incident-1")
    incident_document.collection.assert_called_once_with("reports")
    incident_document.collection.return_value.document.assert_called_once_with("1")
    saved = report_document.set.call_args.args[0]
    assert saved["schema_version"] == "1.0"
    assert saved["metadata"] == report.metadata.model_dump(mode="json")


def test_markdown_renderer_is_deterministic_and_model_free(monkeypatch) -> None:
    from app import reporting
    from app.models import IncidentReport

    report = IncidentReport.model_validate_json(
        (FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(
        reporting,
        "build_reporter_agent",
        lambda *_args, **_kwargs: pytest.fail("Markdown rendering must not call the model"),
    )

    first = reporting.render_report_markdown(report)
    second = reporting.render_report_markdown(report)

    assert first == second
    assert first.startswith("# Incident incident-1\n")
    assert "## Timeline" in first
    assert "## Evidence" in first
    assert "## Verification" in first
    assert "MITIGATED" in first


def test_html_renderer_escapes_untrusted_report_text() -> None:
    from app.models import IncidentReport
    from app.reporting import render_report_html

    payload = json.loads((FIXTURES / "incident-report-mitigated.json").read_text(encoding="utf-8"))
    payload["evidence"][0]["summary"] = '<script>alert("unsafe")</script>'
    report = IncidentReport.model_validate(payload)

    rendered = render_report_html(report)

    assert rendered.startswith("<!doctype html>\n")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "incident-1" in rendered
