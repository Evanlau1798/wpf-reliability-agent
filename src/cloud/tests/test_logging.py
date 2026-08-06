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
