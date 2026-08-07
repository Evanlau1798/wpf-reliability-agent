from datetime import datetime, timezone

from app.correlation import (
    BindingCandidate,
    NormalizedEvidenceSummary,
    match_exact_element_id,
    match_unique_live_candidate,
)
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


def test_unique_live_candidate_is_high_confidence_only_when_unique() -> None:
    candidate = BindingCandidate(
        element_id="people-grid-row-42",
        binding_path="DisplayNmae",
        target_property="Text",
        element_type="TextBlock",
        element_name="PersonName",
    )

    assert match_unique_live_candidate([candidate]) == (candidate, Confidence.HIGH)
    assert match_unique_live_candidate([candidate, candidate.model_copy()]) is None
