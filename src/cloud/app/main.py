from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from app.auth import authenticate_device_token
from app.config import Settings
from app.logging_config import configure_logging


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.settings = Settings.from_environment()
    application.state.logger = configure_logging(application.state.settings.service_role)
    yield


app = FastAPI(title="WPF Reliability Agent", lifespan=lifespan)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/telemetry:batch")
def telemetry_batch(_: Annotated[str, Depends(authenticate_device_token)]) -> dict[str, list[object]]:
    return {
        "accepted_event_ids": [],
        "duplicate_event_ids": [],
        "rejected": [],
    }
