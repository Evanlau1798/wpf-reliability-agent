import pytest

from app import ingest
from app.ingest import ingest_performance_event, validate_telemetry_events


def test_server_allowlist_strips_ui_text_fields() -> None:
    event = _event(
        "ui.snapshot",
        {"root_element_id": "element-1"},
        {
            "nodes": [
                {
                    "ElementId": "element-1",
                    "ParentId": None,
                    "Type": "TextBox",
                    "Name": "SearchBox",
                    "Depth": 0,
                    "ChildCount": 0,
                    "IsVisible": True,
                    "IsEnabled": True,
                    "HasBindingError": False,
                    "Text": "private user text",
                }
            ],
            "truncated": False,
            "omitted_node_count": 0,
            "ui_text": "private user text",
        },
    )

    valid, rejected = validate_telemetry_events([event])

    assert rejected == []
    assert valid[0].payload == {
        "nodes": [
            {
                "ElementId": "element-1",
                "ParentId": None,
                "Type": "TextBox",
                "Name": "SearchBox",
                "Depth": 0,
                "ChildCount": 0,
                "IsVisible": True,
                "IsEnabled": True,
                "HasBindingError": False,
            }
        ],
        "truncated": False,
        "omitted_node_count": 0,
    }
    assert "private user text" not in str(valid[0].payload)


def test_server_redaction_scrubs_secret_text_in_allowed_fields() -> None:
    event = _event(
        "exception.summary",
        {"exception_fingerprint": "exception-1"},
        {
            "fingerprint": "exception-1",
            "exception_type": "InvalidOperationException",
            "message_template": "Authorization: Bearer private-token",
            "app_frames": ["Demo.MainWindow.Loaded"],
            "is_terminating": False,
            "is_unhandled": True,
        },
    )

    valid, rejected = validate_telemetry_events([event])

    assert rejected == []
    assert valid[0].payload["message_template"] == "Authorization: Bearer [REDACTED]"


def test_performance_ingest_requires_explicit_matching_app_session() -> None:
    event = _event(
        "performance.sample",
        {"app_session_id": "different-session"},
        {
            "frame_statistics": {"p95_milliseconds": 40.0},
            "sample_duration_ms": 1000.0,
            "confidence": "MEDIUM",
            "visual_count": 1500,
            "visual_scope_id": "element-session-test-1",
        },
    )
    valid, rejected = validate_telemetry_events([event])

    assert rejected == []
    assert valid[0].payload["visual_scope_id"] == "element-session-test-1"
    with pytest.raises(ValueError, match="app session"):
        ingest_performance_event(object(), valid[0], "device-test", "incident-1")


def test_binding_ingest_builds_publish_payload_after_persist(monkeypatch) -> None:
    event = _event(
        "binding.aggregate",
        {"binding_path": "DisplayNmae"},
        {
            "fingerprint": "binding-1",
            "binding_path": "DisplayNmae",
            "occurrence_count": 1,
        },
    )
    valid, rejected = validate_telemetry_events([event])
    order: list[str] = []

    def persist(*_args, **_kwargs) -> tuple[bool, int]:
        order.append("commit")
        return True, 3

    def build_payload(incident_id, evidence_revision, persisted_event):
        order.append("payload")
        return {
            "incident_id": incident_id,
            "evidence_revision": evidence_revision,
            "trigger": persisted_event.event_type.value,
            "event_id": persisted_event.event_id,
        }

    monkeypatch.setattr(ingest, "persist_incident_event", persist)
    monkeypatch.setattr(ingest, "build_publish_payload", build_payload, raising=False)

    accepted, incident_id, payload = ingest.ingest_binding_event(
        object(),
        valid[0],
        "device-test",
    )

    assert rejected == []
    assert accepted is True
    assert order == ["commit", "payload"]
    assert payload == {
        "incident_id": incident_id,
        "evidence_revision": 3,
        "trigger": "binding.aggregate",
        "event_id": "event-1",
    }


def test_duplicate_binding_ingest_rebuilds_the_same_publish_payload(monkeypatch) -> None:
    valid, _ = validate_telemetry_events([
        _event(
            "binding.aggregate",
            {"binding_path": "DisplayNmae"},
            {"fingerprint": "binding-1", "occurrence_count": 1},
        )
    ])
    monkeypatch.setattr(ingest, "persist_incident_event", lambda *_args, **_kwargs: (False, 3))

    accepted, incident_id, payload = ingest.ingest_binding_event(
        object(), valid[0], "device-test"
    )

    assert accepted is False
    assert payload == ingest.build_publish_payload(incident_id, 3, valid[0])


def test_binding_ingest_preserves_aggregation_window_for_rate_verification() -> None:
    event = _event(
        "binding.aggregate",
        {"binding_path": "DisplayNmae"},
        {
            "fingerprint": "binding-1",
            "binding_path": "DisplayNmae",
            "occurrence_count": 50,
            "aggregation_window_ms": 10_000,
        },
    )

    valid, rejected = validate_telemetry_events([event])

    assert rejected == []
    assert valid[0].payload["aggregation_window_ms"] == 10_000


def test_work_message_contains_only_durable_work_identifiers() -> None:
    event = _event(
        "binding.aggregate",
        {"binding_path": "DisplayNmae"},
        {"fingerprint": "binding-1", "occurrence_count": 1},
    )
    valid, rejected = validate_telemetry_events([event])

    assert rejected == []
    assert ingest.build_publish_payload("incident-1", 4, valid[0]) == {
        "incident_id": "incident-1",
        "evidence_revision": 4,
        "trigger": "binding.aggregate",
        "event_id": "event-1",
    }


def test_recovery_result_persists_post_snapshot_as_incident_evidence(monkeypatch) -> None:
    event = _event(
        "recovery.result",
        {
            "incident_id": "incident-1",
            "command_id": "command-1",
            "action_id": "action-1",
        },
        {
            "observation_window_ms": 10000,
            "binding_occurrence_count": 2,
            "binding_errors_per_second": 0.2,
            "frame_statistics": {"sample_count": 90, "p95_milliseconds": 18.0},
            "performance_sample_duration_ms": 1500.0,
            "performance_confidence": "HIGH",
            "visual_count": 420,
            "visual_count_truncated": False,
            "visual_scope_id": "element-session-test-1",
        },
    )
    valid, rejected = validate_telemetry_events([event])
    captured: dict[str, object] = {}

    def persist(*_args, **kwargs) -> tuple[bool, int]:
        captured.update(kwargs)
        return True, 7

    monkeypatch.setattr(ingest, "persist_incident_event", persist)

    accepted, payload = ingest.ingest_recovery_event(object(), valid[0], "device-test")

    assert rejected == []
    assert accepted is True
    assert captured["incident_id"] == "incident-1"
    assert captured["evidence_id"] == "event-1"
    assert captured["incident"] is None
    stored = captured["evidence"]
    assert stored["correlation"]["action_id"] == "action-1"
    assert stored["payload"]["performance_confidence"] == "HIGH"
    assert stored["payload"]["visual_scope_id"] == "element-session-test-1"
    assert payload == {
        "incident_id": "incident-1",
        "evidence_revision": 7,
        "trigger": "recovery.result",
        "event_id": "event-1",
    }


def _event(event_type: str, correlation: dict[str, object], payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": "event-1",
        "event_type": event_type,
        "severity": "ERROR",
        "timestamp_utc": "2026-08-07T00:00:00Z",
        "device_id": "device-test",
        "application_id": "demo-broken-wpf-app",
        "application_version": "0.1.0",
        "app_session_id": "session-test",
        "sequence_no": 1,
        "correlation": correlation,
        "payload": payload,
        "redaction_profile": "default-v1",
        "evidence_hash": "1" * 64,
    }
