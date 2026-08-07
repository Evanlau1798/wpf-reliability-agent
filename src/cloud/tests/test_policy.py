import json
from pathlib import Path

from app.models import RiskLevel
from app.policy import READ_ONLY_DIAGNOSTIC_TOOLS, risk_for_tool


def test_risk_levels_match_diagnostic_command_contract() -> None:
    schema_path = Path(__file__).parents[3] / "contracts" / "diagnostic-command.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert [level.value for level in RiskLevel] == schema["properties"]["risk_level"]["enum"]


def test_all_read_only_tools_are_low_risk() -> None:
    assert READ_ONLY_DIAGNOSTIC_TOOLS
    assert {risk_for_tool(tool) for tool in READ_ONLY_DIAGNOSTIC_TOOLS} == {RiskLevel.LOW}
