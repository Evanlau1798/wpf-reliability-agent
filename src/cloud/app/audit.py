from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


SHA256_PATTERN = r"^[0-9a-f]{64}$"


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
