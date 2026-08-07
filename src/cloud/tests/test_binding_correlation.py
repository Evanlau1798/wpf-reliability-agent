from datetime import datetime, timezone

from app.correlation import NormalizedEvidenceSummary, match_exact_element_id
from app.models import Confidence


def _evidence(evidence_id: str, element_id: str | None) -> NormalizedEvidenceSummary:
    return NormalizedEvidenceSummary(
        evidence_id=evidence_id,
        kind="binding.aggregate",
        app_session_id="session-1",
        observed_at_utc=datetime(2026, 8, 7, tzinfo=timezone.utc),
        summary="Binding evidence.",
        element_id=element_id,
    )


def test_exact_element_id_match_is_high_confidence() -> None:
    assert match_exact_element_id(
        _evidence("binding-1", "people-grid-row-42"),
        _evidence("ui-1", "people-grid-row-42"),
    ) is Confidence.HIGH

    assert match_exact_element_id(
        _evidence("binding-1", "people-grid-row-42"),
        _evidence("ui-1", "people-grid-row-43"),
    ) is None
