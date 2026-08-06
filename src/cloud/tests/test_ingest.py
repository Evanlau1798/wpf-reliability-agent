from app.ingest import validate_telemetry_events


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
