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
WORKER_ENVIRONMENT_FIELDS = (
    ("PUBSUB_PUSH_AUDIENCE", "pubsub_push_audience"),
    ("PUBSUB_INVOKER_EMAIL", "pubsub_invoker_email"),
    ("GOOGLE_CLOUD_LOCATION", "google_cloud_location"),
)
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_role: Literal["api", "worker"]
    google_cloud_project: str = Field(min_length=1, max_length=256)
    demo_device_id: str = Field(min_length=1, max_length=256)
    demo_device_token: SecretStr = Field(min_length=1)
    demo_operator_token: SecretStr | None = Field(default=None, min_length=1)
    pubsub_topic: str = Field(min_length=1, max_length=255)
    pubsub_push_audience: str | None = Field(default=None, min_length=1, max_length=2048)
    pubsub_invoker_email: str | None = Field(default=None, min_length=3, max_length=320)
    gemini_model: str = Field(default=DEFAULT_GEMINI_MODEL, min_length=1, max_length=128)
    google_cloud_location: str | None = Field(default=None, min_length=1, max_length=128)
    build_revision: str = Field(default="0" * 40, pattern=r"^[0-9a-f]{40}$")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if environment is None else environment
        fields = ENVIRONMENT_FIELDS + (
            WORKER_ENVIRONMENT_FIELDS if source.get("SERVICE_ROLE") == "worker" else ()
        )
        missing = [name for name, _ in fields if not source.get(name, "").strip()]
        if missing:
            raise ValueError(f"Missing required cloud settings: {', '.join(missing)}")

        values = {field: source[name] for name, field in fields}
        if source.get("DEMO_OPERATOR_TOKEN", "").strip():
            values["demo_operator_token"] = source["DEMO_OPERATOR_TOKEN"]
        if source.get("GEMINI_MODEL", "").strip():
            values["gemini_model"] = source["GEMINI_MODEL"]
        if source.get("BUILD_REVISION", "").strip():
            values["build_revision"] = source["BUILD_REVISION"]
        return cls.model_validate(values)
