from datetime import datetime, timezone

from app.correlation import (
    BindingCandidate,
    NormalizedEvidenceSummary,
    match_element_name_and_type,
    match_exact_element_id,
    match_nearest_named_ancestor,
    match_normalized_binding_path,
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


def test_normalized_binding_path_trims_but_preserves_case() -> None:
    left = _evidence("binding-1", None).model_copy(update={"binding_path": " DisplayName "})
    same = _evidence("binding-2", None).model_copy(update={"binding_path": "DisplayName"})
    different_case = _evidence("binding-3", None).model_copy(update={"binding_path": "displayName"})

    assert match_normalized_binding_path(left, same)
    assert not match_normalized_binding_path(left, different_case)


def test_element_name_match_requires_same_element_type() -> None:
    left = _evidence("binding-1", None).model_copy(
        update={"element_name": "PersonName", "element_type": "TextBlock"}
    )
    same = _evidence("ui-1", None).model_copy(
        update={"element_name": "PersonName", "element_type": "TextBlock"}
    )
    different_type = _evidence("ui-2", None).model_copy(
        update={"element_name": "PersonName", "element_type": "TextBox"}
    )

    assert match_element_name_and_type(left, same)
    assert not match_element_name_and_type(left, different_type)


def test_nearest_named_ancestor_match_is_medium_confidence() -> None:
    left = _evidence("binding-1", None).model_copy(
        update={"nearest_named_ancestor": "ExperimentalPeopleGrid"}
    )
    same = _evidence("ui-1", None).model_copy(
        update={"nearest_named_ancestor": "ExperimentalPeopleGrid"}
    )
    different = _evidence("ui-2", None).model_copy(
        update={"nearest_named_ancestor": "FallbackPeopleList"}
    )

    assert match_nearest_named_ancestor(left, same) is Confidence.MEDIUM
    assert match_nearest_named_ancestor(left, different) is None
