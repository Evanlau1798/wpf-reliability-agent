def next_proposal_version(current: int) -> int:
    if type(current) is not int or current < 0:
        raise ValueError("Proposal version is invalid")
    return current + 1
