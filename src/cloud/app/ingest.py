from pydantic import ValidationError

from app.models import DiagnosticEnvelope


def validate_telemetry_events(
    events: list[object],
) -> tuple[list[DiagnosticEnvelope], list[dict[str, str]]]:
    valid: list[DiagnosticEnvelope] = []
    rejected: list[dict[str, str]] = []
    for index, event in enumerate(events):
        try:
            valid.append(DiagnosticEnvelope.model_validate(event))
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
