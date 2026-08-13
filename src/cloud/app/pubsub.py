import json
from functools import cache
from typing import Callable

from fastapi import HTTPException, Request, status
from google.cloud import pubsub_v1


WORK_MESSAGE_FIELDS = ("incident_id", "evidence_revision", "trigger", "event_id")


@cache
def get_publisher_client() -> pubsub_v1.PublisherClient:
    return pubsub_v1.PublisherClient()


def publish_work(project_id: str, topic_name: str, payload: dict[str, object]) -> str:
    publisher = get_publisher_client()
    topic_path = publisher.topic_path(project_id, topic_name)
    work = {field: payload[field] for field in WORK_MESSAGE_FIELDS}
    future = publisher.publish(
        topic_path,
        json.dumps(work, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        **{field: str(work[field]) for field in WORK_MESSAGE_FIELDS},
    )
    return future.result(timeout=10)


def publish_after_commit(
    request: Request,
    payload: dict[str, object],
    publisher: Callable[[str, str, dict[str, object]], str],
) -> None:
    settings = request.app.state.settings
    try:
        publisher(settings.google_cloud_project, settings.pubsub_topic, payload)
    except Exception as exc:
        request.app.state.logger.error(
            "pubsub_publish_failed incident_id=%s evidence_revision=%s event_id=%s",
            payload.get("incident_id"),
            payload.get("evidence_revision"),
            payload.get("event_id"),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Work scheduling failed",
        ) from exc
