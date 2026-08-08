from pathlib import Path

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
