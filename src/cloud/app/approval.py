from typing import Literal

from pydantic import BaseModel

from app.models import ProposedAction


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]


def next_proposal_version(current: int) -> int:
    if type(current) is not int or current < 0:
        raise ValueError("Proposal version is invalid")
    return current + 1


def validate_recovery_proposal(proposal: ProposedAction) -> ProposedAction:
    if proposal.arguments.get("feature") != "ExperimentalPeopleGrid":
        raise ValueError("Recovery proposal must target ExperimentalPeopleGrid")
    if proposal.arguments.get("enabled") is not False:
        raise ValueError("Recovery proposal must disable ExperimentalPeopleGrid")
    if proposal.arguments.get("expected_current_value") is not True:
        raise ValueError("Recovery proposal expected current value must be true")
    return proposal
