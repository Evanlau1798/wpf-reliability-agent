import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import commands
from app.commands import CommandStatus, complete_command_once, lease_next_command
from app.models import CommandResult


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_expired_lease_is_reassigned_transactionally(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    snapshot = Mock()
    snapshot.reference = Mock()
    payload = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    now = datetime(2026, 8, 7, 0, 2, tzinfo=UTC)
    payload.update(
        {
            "status": CommandStatus.LEASED.value,
            "lease_owner": "crashed-device",
            "lease_until": now - timedelta(seconds=1),
            "expires_at_utc": "2026-08-07T00:10:00Z",
        }
    )
    snapshot.to_dict.return_value = payload
    client.transaction.return_value = transaction
    transaction.get.return_value = iter([snapshot])
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)

    leased = lease_next_command(
        client,
        app_session_id="session-1",
        lease_owner="device-test",
        now=now,
        duration=timedelta(seconds=30),
    )

    assert leased is not None
    transaction.update.assert_called_once_with(
        snapshot.reference,
        {
            "status": CommandStatus.LEASED.value,
            "lease_owner": "device-test",
            "lease_until": now + timedelta(seconds=30),
            "updated_at": commands.firestore.SERVER_TIMESTAMP,
        },
    )


def test_command_completion_rejects_an_expired_lease(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    document = client.collection.return_value.document.return_value
    payload = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    now = datetime(2026, 8, 7, 0, 2, tzinfo=UTC)
    payload.update(
        {
            "status": CommandStatus.LEASED.value,
            "lease_owner": "device-a",
            "lease_until": now - timedelta(seconds=1),
        }
    )
    document.get.return_value = Mock(exists=True, to_dict=lambda: payload)
    client.transaction.return_value = transaction
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)
    result = CommandResult.model_validate_json(
        (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")
    )

    with pytest.raises(ValueError, match="Command lease expired"):
        complete_command_once(
            client,
            command_id="command-read-1",
            lease_owner="device-a",
            result=result,
            now=now,
        )
