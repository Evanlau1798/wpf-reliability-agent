import json
import logging
import re
from typing import TextIO


BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)[^\s,;}\]]+")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b(?:[a-z0-9_-]*token|api[_-]?key)\s*=\s*)[^\s,;}\]]+"
)


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = BEARER_PATTERN.sub(r"\1[REDACTED]", record.getMessage())
        record.msg = SECRET_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", message)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, role: str) -> None:
        super().__init__()
        self._role = role

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "level": record.levelname,
                "message": record.getMessage(),
                "role": self._role,
                "service": "wpf-reliability-agent",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def configure_logging(role: str, stream: TextIO | None = None) -> logging.Logger:
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter(role))
    logger = logging.getLogger("wpf_reliability_agent")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
