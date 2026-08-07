from app.models import DiagnosticTool, ProposedAction
from app.approval import next_proposal_version


def test_action_proposal_model_binds_exact_required_fields() -> None:
    proposal = ProposedAction.model_validate(
        {
            "tool": "recovery.set_feature_flag",
            "arguments": {
                "feature": "ExperimentalPeopleGrid",
                "enabled": False,
                "expected_current_value": True,
            },
            "evidence_ids": ["evidence-1"],
            "expected_effect": "Reduce UI load.",
            "rollback_plan": "Re-enable the feature.",
        }
    )

    assert proposal.tool is DiagnosticTool.RECOVERY_SET_FEATURE_FLAG
    assert proposal.arguments["feature"] == "ExperimentalPeopleGrid"
    assert proposal.evidence_ids == ["evidence-1"]
    assert proposal.expected_effect == "Reduce UI load."
    assert proposal.rollback_plan == "Re-enable the feature."


def test_each_new_material_proposal_increments_version() -> None:
    assert next_proposal_version(0) == 1
    assert next_proposal_version(1) == 2
