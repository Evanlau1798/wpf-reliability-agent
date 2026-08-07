import json
from pathlib import Path

import pytest

from app.approval import next_proposal_version, validate_recovery_proposal
from app.contracts import sha256_canonical
from app.firestore_client import evidence_snapshot_hash
from app.models import DiagnosticTool, ProposedAction


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


def test_proposal_evidence_snapshot_hash_binds_material_evidence() -> None:
    first = [("evidence-1", "1" * 64)]
    second = [*first, ("evidence-2", "2" * 64)]

    assert evidence_snapshot_hash(first) != evidence_snapshot_hash(second)
    assert evidence_snapshot_hash(second) == evidence_snapshot_hash(reversed(second))


def test_proposal_arguments_hash_matches_cross_language_fixture() -> None:
    fixture_path = Path(__file__).parents[3] / "contracts" / "fixtures" / "hash-ascii.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert sha256_canonical(fixture["input"]) == fixture["sha256"]


def test_recovery_proposal_rejects_arbitrary_feature_name() -> None:
    proposal = ProposedAction.model_validate(
        {
            "tool": "recovery.set_feature_flag",
            "arguments": {
                "feature": "UnreviewedFeature",
                "enabled": False,
                "expected_current_value": True,
            },
            "evidence_ids": ["evidence-1"],
            "expected_effect": "Reduce UI load.",
            "rollback_plan": "Restore the feature.",
        }
    )

    with pytest.raises(ValueError, match="ExperimentalPeopleGrid"):
        validate_recovery_proposal(proposal)
