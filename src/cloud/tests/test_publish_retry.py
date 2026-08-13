from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app import main


def test_publish_failure_requests_device_retry(monkeypatch) -> None:
    logger = Mock()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    google_cloud_project="project-test",
                    pubsub_topic="incident-work",
                ),
                logger=logger,
            )
        )
    )
    monkeypatch.setattr(main, "publish_work", Mock(side_effect=RuntimeError("unavailable")))
    payload = {"incident_id": "incident-1", "evidence_revision": 3, "event_id": "event-1"}

    with pytest.raises(HTTPException, match="Work scheduling failed") as error:
        main._publish_after_commit(request, payload)

    assert error.value.status_code == 503
    logger.error.assert_called_once()
