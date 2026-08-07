import json
from pathlib import Path

from app.models import RiskLevel


def test_risk_levels_match_diagnostic_command_contract() -> None:
    schema_path = Path(__file__).parents[3] / "contracts" / "diagnostic-command.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert [level.value for level in RiskLevel] == schema["properties"]["risk_level"]["enum"]
