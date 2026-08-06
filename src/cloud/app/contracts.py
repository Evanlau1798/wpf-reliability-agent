from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from app.models import (
    AgentDecision,
    ApprovalRecord,
    CommandResult,
    DiagnosticCommand,
    DiagnosticEnvelope,
    IncidentReport,
)


REPOSITORY_ROOT = Path(__file__).parents[3]
CONTRACTS = REPOSITORY_ROOT / "contracts"
FIXTURES = CONTRACTS / "fixtures"
MODEL_BY_CONTRACT = {
    "diagnostic-envelope": DiagnosticEnvelope,
    "diagnostic-command": DiagnosticCommand,
    "command-result": CommandResult,
    "agent-decision": AgentDecision,
    "approval": ApprovalRecord,
    "incident-report": IncidentReport,
}


def _supported(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("numbers must be finite")
        return value
    if isinstance(value, list):
        return [_supported(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("object keys must be strings")
        return {key: _supported(item) for key, item in value.items()}
    raise ValueError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _supported(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_fixture(name: str) -> bool:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    case = next(item for item in manifest["cases"] if item["file"] == name)
    contract = case["contract"]
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    schema = json.loads((CONTRACTS / f"{contract}.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    if not validator.is_valid(value):
        return False
    try:
        MODEL_BY_CONTRACT[contract].model_validate(value)
    except ValueError:
        return False
    return True
