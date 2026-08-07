from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.contracts import CONTRACTS, canonical_json, sha256_canonical, validate_fixture
from app.models import DiagnosticCommand, DiagnosticEnvelope, IncidentReport


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize(
    "name",
    ["hash-ascii.json", "hash-unicode.json", "hash-reordered.json"],
)
def test_canonical_hash_matches_golden_fixture(name: str) -> None:
    fixture = json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    assert canonical_json(fixture["input"]) == fixture["canonical"]
    assert sha256_canonical(fixture["input"]) == fixture["sha256"]


def test_non_finite_numbers_are_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        canonical_json({"value": float("nan")})


def test_high_risk_command_requires_approval() -> None:
    with pytest.raises(ValueError, match="approval"):
        DiagnosticCommand.model_validate(
            {
                "schema_version": "1.0",
                "command_id": "command-1",
                "incident_id": "incident-1",
                "target_app_session_id": "session-1",
                "tool": "recovery.set_feature_flag",
                "arguments": {
                    "feature": "ExperimentalPeopleGrid",
                    "enabled": False,
                    "expected_current_value": True,
                },
                "arguments_hash": "a" * 64,
                "risk_level": "HIGH",
                "approval_id": None,
                "idempotency_key": "command-key-1",
                "issued_at_utc": "2026-08-07T00:00:00Z",
                "expires_at_utc": "2026-08-07T00:01:00Z",
                "timeout_ms": 10000,
            }
        )


@pytest.mark.parametrize(("argument", "value"), [("max_depth", 5), ("max_nodes", 301)])
def test_ui_subtree_command_rejects_argument_budget_overflow(argument: str, value: int) -> None:
    command = json.loads((FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8"))
    command["arguments"][argument] = value

    with pytest.raises(ValueError, match="exceeds local ceiling"):
        DiagnosticCommand.model_validate(command)


def test_temporary_mitigation_cannot_be_resolved() -> None:
    report = {
        "schema_version": "1.0",
        "incident_id": "incident-1",
        "status": "RESOLVED",
        "severity": "ERROR",
        "confidence": "HIGH",
        "timeline": [],
        "evidence": [],
        "claims": [],
        "temporary_mitigation": {
            "action_id": "action-1",
            "tool": "recovery.set_feature_flag",
            "approval_id": "approval-1",
        },
        "permanent_recommendation": {"summary": "Fix the XAML binding."},
        "verification": [],
        "metadata": {
            "model_id": "gemini-test",
            "prompt_version": "1",
            "schema_version": "1.0",
            "policy_version": "1",
            "reuse_revision": "900ac97cf9b69b4a3c1f4899b08c9b1e78212af3",
        },
    }

    with pytest.raises(ValueError, match="MITIGATED"):
        IncidentReport.model_validate(report)


@pytest.mark.parametrize("case", MANIFEST, ids=lambda case: case["file"])
def test_all_shared_fixtures_match_manifest(case: dict[str, object]) -> None:
    assert validate_fixture(str(case["file"])) is case["valid"]


def test_all_schemas_are_valid_draft_2020_12() -> None:
    for path in CONTRACTS.glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_envelope_budget_is_enforced_after_utf8_serialization() -> None:
    value = json.loads((FIXTURES / "diagnostic-envelope-valid.json").read_text(encoding="utf-8"))
    value["payload"] = {"message": "x" * 70_000}

    with pytest.raises(ValueError, match="65536"):
        DiagnosticEnvelope.model_validate(value)


def test_empty_identifier_is_rejected() -> None:
    value = json.loads((FIXTURES / "diagnostic-envelope-valid.json").read_text(encoding="utf-8"))
    value["event_id"] = ""

    with pytest.raises(ValueError, match="event_id"):
        DiagnosticEnvelope.model_validate(value)


def test_conflicting_result_fixture_has_a_distinct_hash() -> None:
    original = json.loads((FIXTURES / "command-result-success.json").read_text(encoding="utf-8"))
    conflict = json.loads((FIXTURES / "command-result-conflicting.json").read_text(encoding="utf-8"))

    assert original["command_id"] == conflict["command_id"]
    assert original["result_hash"] != conflict["result_hash"]
