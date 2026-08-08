from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import sha256_canonical


SHA256_PATTERN = r"^[0-9a-f]{64}$"
ZERO_HASH = "0" * 64
AUDIT_FIELDS = frozenset(
    {
        "sequence",
        "type",
        "actor_type",
        "actor_id",
        "payload_hash",
        "previous_entry_hash",
        "entry_hash",
        "timestamp_utc",
    }
)


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    type: str = Field(min_length=1, max_length=128)
    actor_type: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=256)
    payload_hash: str = Field(pattern=SHA256_PATTERN)
    previous_entry_hash: str = Field(pattern=SHA256_PATTERN)
    entry_hash: str = Field(pattern=SHA256_PATTERN)
    timestamp_utc: datetime


def build_audit_record(
    *,
    sequence: int,
    event_type: str,
    actor_type: str,
    actor_id: str,
    payload: dict[str, object],
    previous_entry_hash: str,
    timestamp_utc: datetime,
) -> dict[str, object]:
    if AUDIT_FIELDS.intersection(payload):
        raise ValueError("Audit payload uses reserved fields")
    timestamp = _utc_timestamp(timestamp_utc)
    payload_hash = sha256_canonical(payload)
    core = {
        "sequence": sequence,
        "type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "payload_hash": payload_hash,
        "previous_entry_hash": previous_entry_hash,
        "timestamp_utc": timestamp,
    }
    entry_hash = sha256_canonical(core)
    event = AuditEvent.model_validate({**core, "entry_hash": entry_hash})
    return {**event.model_dump(mode="json"), **payload}


def verify_audit_chain(records: list[dict[str, object]]) -> bool:
    previous_hash = ZERO_HASH
    for expected_sequence, record in enumerate(records, start=1):
        try:
            core = {field: record[field] for field in AUDIT_FIELDS}
            event = AuditEvent.model_validate(core)
            payload = {key: value for key, value in record.items() if key not in AUDIT_FIELDS}
            material = {
                "sequence": event.sequence,
                "type": event.type,
                "actor_type": event.actor_type,
                "actor_id": event.actor_id,
                "payload_hash": event.payload_hash,
                "previous_entry_hash": event.previous_entry_hash,
                "timestamp_utc": _utc_timestamp(event.timestamp_utc),
            }
        except (KeyError, TypeError, ValueError, ValidationError):
            return False
        if event.sequence != expected_sequence or event.previous_entry_hash != previous_hash:
            return False
        if sha256_canonical(payload) != event.payload_hash:
            return False
        if sha256_canonical(material) != event.entry_hash:
            return False
        previous_hash = event.entry_hash
    return True


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Audit timestamp must be UTC")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
