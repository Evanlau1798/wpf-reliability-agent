from enum import StrEnum

from google.cloud import firestore

from app.firestore_client import COMMANDS_COLLECTION
from app.models import DiagnosticCommand


class CommandStatus(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


def write_command(client: firestore.Client, command: DiagnosticCommand) -> None:
    document = command.model_dump(mode="json")
    document.update(
        {
            "status": CommandStatus.PENDING.value,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
    )
    client.collection(COMMANDS_COLLECTION).document(command.command_id).create(document)
