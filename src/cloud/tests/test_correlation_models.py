from datetime import datetime, timezone

from app.correlation import NormalizedEvidenceSummary


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
