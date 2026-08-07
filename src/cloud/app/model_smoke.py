from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import DEFAULT_GEMINI_MODEL


class SmokeResponse(BaseModel):
    status: Literal["ok"]


SMOKE_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "status": types.Schema(type=types.Type.STRING, enum=["ok"]),
    },
    required=["status"],
)


def model_smoke_target(environment: Mapping[str, str]) -> tuple[str, str, str]:
    project = environment.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = environment.get("GOOGLE_CLOUD_LOCATION", "").strip()
    missing = [
        name
        for name, value in (
            ("GOOGLE_CLOUD_PROJECT", project),
            ("GOOGLE_CLOUD_LOCATION", location),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required model smoke settings: {', '.join(missing)}")
    model = environment.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    return project, location, model


def create_vertex_client(project: str, location: str) -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
    )


def run_model_smoke(client: Any, model: str) -> SmokeResponse:
    response = client.models.generate_content(
        model=model,
        contents='Return exactly the structured status value "ok".',
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SMOKE_RESPONSE_SCHEMA,
        ),
    )
    if response.parsed is None:
        raise RuntimeError("Gemini smoke response did not contain structured output")
    return SmokeResponse.model_validate(response.parsed)
