import io
import json

from app.logging_config import configure_logging


def test_structured_log_contains_service_and_role() -> None:
    output = io.StringIO()
    logger = configure_logging("api", output)

    logger.info("service ready")

    record = json.loads(output.getvalue())
    assert record == {
        "level": "INFO",
        "message": "service ready",
        "role": "api",
        "service": "wpf-reliability-agent",
    }


def test_authorization_and_token_values_are_redacted() -> None:
    output = io.StringIO()
    logger = configure_logging("worker", output)

    logger.warning(
        "Authorization: Bearer %s device_token=%s api_key=%s",
        "bearer-secret",
        "device-secret",
        "model-secret",
    )

    message = json.loads(output.getvalue())["message"]
    assert "bearer-secret" not in message
    assert "device-secret" not in message
    assert "model-secret" not in message
    assert message.count("[REDACTED]") == 3
