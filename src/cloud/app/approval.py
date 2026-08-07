from app.models import ProposedAction


def next_proposal_version(current: int) -> int:
    if type(current) is not int or current < 0:
        raise ValueError("Proposal version is invalid")
    return current + 1


def validate_recovery_proposal(proposal: ProposedAction) -> ProposedAction:
    if proposal.arguments.get("feature") != "ExperimentalPeopleGrid":
        raise ValueError("Recovery proposal must target ExperimentalPeopleGrid")
    if proposal.arguments.get("enabled") is not False:
        raise ValueError("Recovery proposal must disable ExperimentalPeopleGrid")
    return proposal
