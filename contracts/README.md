# Reliability Contracts

The six JSON Schema files in this directory are the canonical wire contracts:

1. `diagnostic-envelope.schema.json`
2. `diagnostic-command.schema.json`
3. `command-result.schema.json`
4. `agent-decision.schema.json`
5. `approval.schema.json`
6. `incident-report.schema.json`

All initial contracts use `schema_version` `1.0`. Timestamps are UTC ISO-8601
values. Identifiers are non-empty, caller-generated unpredictable strings.
Unknown enum values and missing required fields are rejected. Unknown optional
fields are ignored for forward compatibility.

Payload budgets are enforced after compact UTF-8 serialization:

| Contract | Budget |
|---|---:|
| Diagnostic envelope | 64 KiB |
| Diagnostic command | 32 KiB |
| Command result | 128 KiB |
| Agent decision | 64 KiB |
| Approval record | 32 KiB |
| Incident report | 512 KiB |

Canonical action hashing recursively sorts object keys using ordinal order,
preserves array order, writes compact UTF-8 JSON without ASCII escaping, and
rejects NaN, Infinity, and unsupported values. SHA-256 is emitted as lowercase
hexadecimal.

Shared fixtures under `fixtures/` are consumed by both the C# and Python test
suites. Schema validation is followed by semantic validation for cross-field
rules that JSON Schema cannot safely express alone.
