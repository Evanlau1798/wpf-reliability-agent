from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_role: Literal["api", "worker"]
    google_cloud_project: str = Field(min_length=1, max_length=256)
    demo_device_id: str = Field(min_length=1, max_length=256)
    demo_device_token: SecretStr = Field(min_length=1)
