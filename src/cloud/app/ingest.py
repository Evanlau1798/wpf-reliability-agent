from pydantic import ValidationError

from app.contracts import sha256_canonical
from app.firestore_client import build_incident_document, persist_incident_event
from app.logging_config import redact_text
from app.models import DiagnosticEnvelope, EventType


CORRELATION_FIELDS = {
    EventType.BINDING_AGGREGATE: frozenset({"binding_path", "element_id", "window_type"}),
    EventType.EXCEPTION_SUMMARY: frozenset({"exception_fingerprint"}),
    EventType.UI_SNAPSHOT: frozenset({"root_element_id"}),
    EventType.PERFORMANCE_SAMPLE: frozenset({"app_session_id"}),
    EventType.TOOL_RESULT: frozenset(),
    EventType.RECOVERY_RESULT: frozenset({"incident_id", "command_id", "action_id"}),
}
PAYLOAD_FIELDS = {
    EventType.BINDING_AGGREGATE: frozenset(
        {
            "fingerprint",
            "category",
            "binding_path",
            "target_property",
            "element_type",
            "element_name",
            "occurrence_count",
            "aggregation_window_ms",
            "first_seen_utc",
            "last_seen_utc",
            "message_truncated",
        }
    ),
    EventType.EXCEPTION_SUMMARY: frozenset(
        {
            "fingerprint",
            "exception_type",
            "message_template",
            "app_frames",
            "is_terminating",
            "is_unhandled",
        }
    ),
    EventType.UI_SNAPSHOT: frozenset({"nodes", "truncated", "omitted_node_count"}),
    EventType.PERFORMANCE_SAMPLE: frozenset(
        {
            "frame_statistics",
            "sample_duration_ms",
            "confidence",
            "heartbeat_delay_ms",
            "visual_count",
            "visual_count_truncated",
            "visual_scope_id",
        }
    ),
    EventType.TOOL_RESULT: frozenset(),
    EventType.RECOVERY_RESULT: frozenset(
        {
            "observation_window_ms",
            "binding_occurrence_count",
            "binding_errors_per_second",
            "frame_statistics",
            "performance_sample_duration_ms",
            "performance_confidence",
            "visual_count",
            "visual_count_truncated",
            "visual_scope_id",
        }
    ),
}
UI_NODE_FIELDS = frozenset(
    {
        "ElementId",
        "ParentId",
        "Type",
        "Name",
        "Depth",
        "ChildCount",
        "IsVisible",
        "IsEnabled",
        "HasBindingError",
        "element_id",
        "parent_id",
        "type",
        "name",
        "depth",
        "child_count",
        "is_visible",
        "is_enabled",
        "has_binding_error",
    }
)
FRAME_STATISTICS_FIELDS = frozenset(
    {
        "SampleCount",
        "AverageMilliseconds",
        "P50Milliseconds",
        "P95Milliseconds",
        "MaxMilliseconds",
        "Over16Point7Milliseconds",
        "Over33Point3Milliseconds",
        "Over50Milliseconds",
        "sample_count",
        "average_milliseconds",
        "p50_milliseconds",
        "p95_milliseconds",
        "max_milliseconds",
        "over16_point7_milliseconds",
        "over33_point3_milliseconds",
        "over50_milliseconds",
    }
)


def validate_telemetry_events(
    events: list[object],
) -> tuple[list[DiagnosticEnvelope], list[dict[str, str]]]:
    valid: list[DiagnosticEnvelope] = []
    rejected: list[dict[str, str]] = []
    for index, event in enumerate(events):
        try:
            valid.append(sanitize_telemetry_event(DiagnosticEnvelope.model_validate(event)))
        except ValidationError:
            event_id = event.get("event_id") if isinstance(event, dict) else None
            rejected.append(
                {
                    "event_id": (
                        event_id
                        if isinstance(event_id, str) and event_id
                        else f"invalid-{index}"
                    ),
                    "code": "INVALID_EVENT",
                }
            )
    return valid, rejected


def sanitize_telemetry_event(event: DiagnosticEnvelope) -> DiagnosticEnvelope:
    correlation = _allowlist(event.correlation, CORRELATION_FIELDS[event.event_type])
    payload = _allowlist(event.payload, PAYLOAD_FIELDS[event.event_type])
    if event.event_type is EventType.UI_SNAPSHOT:
        nodes = event.payload.get("nodes")
        payload["nodes"] = (
            [_allowlist(node, UI_NODE_FIELDS) for node in nodes if isinstance(node, dict)]
            if isinstance(nodes, list)
            else []
        )
    elif event.event_type in {EventType.PERFORMANCE_SAMPLE, EventType.RECOVERY_RESULT}:
        statistics = event.payload.get("frame_statistics")
        if isinstance(statistics, dict):
            payload["frame_statistics"] = _allowlist(statistics, FRAME_STATISTICS_FIELDS)
        else:
            payload.pop("frame_statistics", None)
    return event.model_copy(
        update={
            "correlation": _redact(correlation),
            "payload": _redact(payload),
        }
    )


def ingest_binding_event(
    client: object,
    event: DiagnosticEnvelope,
    device_id: str,
) -> tuple[bool, str, dict[str, object] | None]:
    if event.event_type is not EventType.BINDING_AGGREGATE:
        raise ValueError("Binding ingest requires a binding aggregate event")
    fingerprint = event.payload.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("Binding aggregate fingerprint is required")

    trusted_event = event.model_copy(update={"device_id": device_id})
    incident_id = sha256_canonical(
        {
            "device_id": device_id,
            "application_id": event.application_id,
            "app_session_id": event.app_session_id,
            "fingerprint": fingerprint,
        }
    )
    binding_path = event.payload.get("binding_path")
    summary = (
        f"Binding error burst: {binding_path}"
        if isinstance(binding_path, str) and binding_path
        else "Binding error burst"
    )
    is_new, evidence_revision = persist_incident_event(
        client,
        event_id=event.event_id,
        incident_id=incident_id,
        evidence_id=event.event_id,
        incident=build_incident_document(
            application_id=event.application_id,
            app_session_id=event.app_session_id,
            severity=event.severity.value,
            summary=summary,
            evidence_revision=1,
        ),
        evidence=trusted_event.model_dump(mode="json"),
    )
    return is_new, incident_id, build_publish_payload(incident_id, evidence_revision, trusted_event)


def ingest_performance_event(
    client: object,
    event: DiagnosticEnvelope,
    device_id: str,
    incident_id: str,
) -> tuple[bool, dict[str, object] | None]:
    if event.event_type is not EventType.PERFORMANCE_SAMPLE:
        raise ValueError("Performance ingest requires a performance sample event")
    if event.correlation.get("app_session_id") != event.app_session_id:
        raise ValueError("Performance app session correlation must match the event")

    trusted_event = event.model_copy(update={"device_id": device_id})
    is_new, evidence_revision = persist_incident_event(
        client,
        event_id=event.event_id,
        incident_id=incident_id,
        evidence_id=event.event_id,
        incident=None,
        evidence=trusted_event.model_dump(mode="json"),
    )
    return is_new, build_publish_payload(incident_id, evidence_revision, trusted_event)


def ingest_recovery_event(
    client: object,
    event: DiagnosticEnvelope,
    device_id: str,
) -> tuple[bool, dict[str, object] | None]:
    if event.event_type is not EventType.RECOVERY_RESULT:
        raise ValueError("Recovery ingest requires a recovery result event")
    incident_id = event.correlation.get("incident_id")
    command_id = event.correlation.get("command_id")
    action_id = event.correlation.get("action_id")
    if not all(isinstance(value, str) and value for value in (incident_id, command_id, action_id)):
        raise ValueError("Recovery result requires incident command and action references")

    trusted_event = event.model_copy(update={"device_id": device_id})
    is_new, evidence_revision = persist_incident_event(
        client,
        event_id=event.event_id,
        incident_id=incident_id,
        evidence_id=event.event_id,
        incident=None,
        evidence=trusted_event.model_dump(mode="json"),
    )
    return is_new, build_publish_payload(incident_id, evidence_revision, trusted_event)


def build_publish_payload(
    incident_id: str,
    evidence_revision: int,
    event: DiagnosticEnvelope,
) -> dict[str, object]:
    return {
        "incident_id": incident_id,
        "evidence_revision": evidence_revision,
        "trigger": event.event_type.value,
        "event_id": event.event_id,
    }


def _allowlist(source: dict[str, object], fields: frozenset[str]) -> dict[str, object]:
    return {key: source[key] for key in fields if key in source}


def _redact(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value
