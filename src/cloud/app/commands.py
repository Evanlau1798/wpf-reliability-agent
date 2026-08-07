from enum import StrEnum


class CommandStatus(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
