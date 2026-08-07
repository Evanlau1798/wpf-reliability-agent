import json
from pathlib import Path

import pytest

from app.models import AgentDecision, RiskLevel
from app.policy import POLICY_VERSION, READ_ONLY_DIAGNOSTIC_TOOLS, risk_for_tool


def test_risk_levels_match_diagnostic_command_contract() -> None:
    schema_path = Path(__file__).parents[3] / "contracts" / "diagnostic-command.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert [level.value for level in RiskLevel] == schema["properties"]["risk_level"]["enum"]


def test_all_read_only_tools_are_low_risk() -> None:
    assert READ_ONLY_DIAGNOSTIC_TOOLS
    assert {risk_for_tool(tool) for tool in READ_ONLY_DIAGNOSTIC_TOOLS} == {RiskLevel.LOW}


def test_feature_recovery_is_high_risk_even_if_model_hints_low() -> None:
    decision = AgentDecision.model_validate(
        {
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
                "evidence_ids": ["evidence-1"],
                "expected_effect": "Reduce UI load.",
                "rollback_plan": "Re-enable the feature.",
                "risk_hint": "LOW",
            },
            "missing_evidence": [],
        }
    )

    assert decision.proposed_action is not None
    assert risk_for_tool(decision.proposed_action.tool) is RiskLevel.HIGH


@pytest.mark.parametrize(
    "tool",
    ["shell.execute", "file.write", "process.kill", "dll.inject"],
)
def test_blocked_tool_families_are_blocked(tool: str) -> None:
    assert risk_for_tool(tool) is RiskLevel.BLOCKED


def test_unknown_tool_defaults_to_blocked() -> None:
    assert risk_for_tool("future.unreviewed_tool") is RiskLevel.BLOCKED


def test_policy_version_matches_approval_contract_fixture() -> None:
    fixture_path = Path(__file__).parents[3] / "contracts" / "fixtures" / "approval-pending.json"
    approval = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert POLICY_VERSION == approval["policy_version"]
