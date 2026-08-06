import json
import logging
from typing import TextIO


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
    handler.setFormatter(JsonFormatter(role))
    logger = logging.getLogger("wpf_reliability_agent")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
