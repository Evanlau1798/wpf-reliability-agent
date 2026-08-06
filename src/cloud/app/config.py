import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


ENVIRONMENT_FIELDS = (
    ("SERVICE_ROLE", "service_role"),
    ("GOOGLE_CLOUD_PROJECT", "google_cloud_project"),
    ("DEMO_DEVICE_ID", "demo_device_id"),
    ("DEMO_DEVICE_TOKEN", "demo_device_token"),
    ("PUBSUB_TOPIC", "pubsub_topic"),
)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_role: Literal["api", "worker"]
    google_cloud_project: str = Field(min_length=1, max_length=256)
    demo_device_id: str = Field(min_length=1, max_length=256)
    demo_device_token: SecretStr = Field(min_length=1)
    pubsub_topic: str = Field(min_length=1, max_length=255)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if environment is None else environment
        missing = [name for name, _ in ENVIRONMENT_FIELDS if not source.get(name, "").strip()]
        if missing:
            raise ValueError(f"Missing required cloud settings: {', '.join(missing)}")

        return cls.model_validate({field: source[name] for name, field in ENVIRONMENT_FIELDS})
