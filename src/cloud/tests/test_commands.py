from app.commands import CommandStatus


def test_command_statuses_match_p0_lifecycle() -> None:
    assert [status.value for status in CommandStatus] == [
        "PENDING",
        "LEASED",
        "COMPLETED",
        "FAILED",
        "EXPIRED",
    ]
