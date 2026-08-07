from datetime import datetime, timezone

from app.correlation import CandidateClaim, EvidenceEdgeType, NormalizedEvidenceSummary
from app.models import Confidence


def test_normalized_evidence_summary_keeps_only_compact_fields() -> None:
    evidence = NormalizedEvidenceSummary(
        evidence_id="evidence-1",
        kind="binding.aggregate",
        app_session_id="session-1",
        observed_at_utc=datetime(2026, 8, 7, tzinfo=timezone.utc),
        summary="DisplayName binding failed repeatedly.",
    )

    assert evidence.model_dump(mode="json") == {
        "evidence_id": "evidence-1",
        "kind": "binding.aggregate",
        "app_session_id": "session-1",
        "observed_at_utc": "2026-08-07T00:00:00Z",
        "summary": "DisplayName binding failed repeatedly.",
    }


def test_evidence_edge_types_cover_gate_11_relationships() -> None:
    assert {edge.value for edge in EvidenceEdgeType} == {
        "same_element",
        "same_binding_path",
        "same_time_window",
        "performance_amplifier",
        "verifies",
        "contradicts",
    }


def test_candidate_claim_binds_support_counter_and_confidence() -> None:
    claim = CandidateClaim(
        candidate="ExperimentalPeopleGrid",
        summary="Binding failures and UI expansion point to the experimental grid.",
        supporting_evidence_ids=["binding-1", "ui-1"],
        counter_evidence_ids=["perf-unrelated"],
        confidence=Confidence.MEDIUM,
    )

    assert claim.supporting_evidence_ids == ["binding-1", "ui-1"]
    assert claim.counter_evidence_ids == ["perf-unrelated"]
    assert claim.confidence is Confidence.MEDIUM
