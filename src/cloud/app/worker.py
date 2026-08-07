import base64
import json

from app.pubsub import WORK_MESSAGE_FIELDS


def build_run_key(incident_id: str, evidence_revision: int, trigger: str) -> str:
    return f"{incident_id}:{evidence_revision}:{trigger}"


def decode_pubsub_envelope(envelope: object) -> dict[str, object]:
    if not isinstance(envelope, dict):
        raise ValueError("Invalid Pub/Sub envelope")
    message = envelope.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("data"), str):
        raise ValueError("Invalid Pub/Sub message")

    try:
        decoded = base64.b64decode(message["data"], validate=True)
        work = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Pub/Sub data") from exc

    if not isinstance(work, dict) or set(work) != set(WORK_MESSAGE_FIELDS):
        raise ValueError("Invalid work message")
    if not all(isinstance(work[field], str) and work[field] for field in ("incident_id", "trigger", "event_id")):
        raise ValueError("Invalid work identifiers")
    revision = work["evidence_revision"]
    if type(revision) is not int or revision < 1:
        raise ValueError("Invalid evidence revision")
    return work


def pubsub_message_id(envelope: object) -> str:
    if not isinstance(envelope, dict):
        return "unknown"
    message = envelope.get("message")
    if not isinstance(message, dict):
        return "unknown"
    message_id = message.get("messageId")
    return message_id if isinstance(message_id, str) and message_id else "unknown"
