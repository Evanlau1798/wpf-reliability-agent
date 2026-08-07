import pytest

from app.correlation import map_correlation_confidence
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
