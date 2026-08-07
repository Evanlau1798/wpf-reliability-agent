import pytest

from app.correlation import CandidateClaim, can_propose_mutation, map_correlation_confidence
from app.models import Confidence


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        ({"exact_element": True}, Confidence.HIGH),
        ({"unique_live_candidate": True}, Confidence.HIGH),
        ({"independent_evidence_matches": 2}, Confidence.HIGH),
        (
            {"binding_path": True, "named_ancestor": True, "time_window": True},
            Confidence.MEDIUM,
        ),
        ({"time_window": True}, Confidence.LOW),
    ],
)
def test_confidence_mapping_follows_deterministic_table(
    signals: dict[str, object],
    expected: Confidence,
) -> None:
    assert map_correlation_confidence(**signals) is expected


def test_low_confidence_candidate_cannot_propose_mutation() -> None:
    low = CandidateClaim(
        candidate="ExperimentalPeopleGrid",
        summary="Only weak timing evidence is available.",
        supporting_evidence_ids=["time-1"],
        confidence=Confidence.LOW,
    )
    medium = low.model_copy(update={"confidence": Confidence.MEDIUM})

    assert not can_propose_mutation(low)
    assert can_propose_mutation(medium)
