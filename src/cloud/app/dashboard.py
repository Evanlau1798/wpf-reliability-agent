import json
from datetime import datetime
from html import escape

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.firestore_client import (
    APPROVALS_COLLECTION,
    AUDIT_COLLECTION,
    COMMANDS_COLLECTION,
    EVIDENCE_COLLECTION,
    INCIDENTS_COLLECTION,
)


def render_incident_list(client: firestore.Client) -> str:
    rows = "".join(
        _render_incident(snapshot.id, snapshot.to_dict() or {})
        for snapshot in client.collection(INCIDENTS_COLLECTION).stream()
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Incidents</title></head><body><main><h1>Incidents</h1>"
        '<table><thead><tr><th scope="col">ID</th><th scope="col">State</th>'
        '<th scope="col">Summary</th><th scope="col">Updated</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></main></body></html>"
    )


def render_incident_detail(client: firestore.Client, incident_id: str) -> str | None:
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    snapshot = incident_document.get()
    if not snapshot.exists:
        return None
    incident = snapshot.to_dict() or {}
    timeline = _render_timeline(incident_document)
    evidence_index = _render_evidence_index(incident_document)
    hypotheses = _render_hypotheses(incident)
    tool_ledger = _render_tool_ledger(client, incident_id)
    approvals = _render_approvals(incident_document)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Incident {_display(incident_id)}</title></head><body><main>"
        f"<h1>Incident {_display(incident_id)}</h1><dl>"
        f"<dt>State</dt><dd>{_display(incident.get('state'))}</dd>"
        f"<dt>Summary</dt><dd>{_display(incident.get('summary'))}</dd>"
        f"<dt>Updated</dt><dd>{_display(incident.get('updated_at'))}</dd>"
        f"</dl>{timeline}{evidence_index}{hypotheses}{tool_ledger}{approvals}</main></body></html>"
    )


def _render_timeline(incident_document: object) -> str:
    records = [
        snapshot.to_dict() or {}
        for snapshot in incident_document.collection(AUDIT_COLLECTION).stream()
    ]
    records.sort(key=lambda record: (int(record["sequence"]), str(record["timestamp_utc"])))
    items = "".join(
        "<li>"
        f"{_display(record.get('sequence'))} — "
        f"<time>{_display(record.get('timestamp_utc'))}</time> — "
        f"{_display(record.get('type'))}"
        "</li>"
        for record in records
    )
    return f"<section><h2>Timeline</h2><ol>{items}</ol></section>"


def _render_evidence_index(incident_document: object) -> str:
    items = "".join(
        "<li>"
        f"{_display(snapshot.id)} — "
        f"{_display((snapshot.to_dict() or {}).get('event_type'))} — "
        f"{_display((snapshot.to_dict() or {}).get('evidence_hash'))}"
        "</li>"
        for snapshot in incident_document.collection(EVIDENCE_COLLECTION).stream()
    )
    return f"<section><h2>Evidence</h2><ul>{items}</ul></section>"


def _render_hypotheses(incident: dict[str, object]) -> str:
    hypotheses = incident.get("current_hypotheses")
    if not isinstance(hypotheses, list):
        hypotheses = []
    items = "".join(
        "<li>"
        f"<strong>{_display(item.get('confidence'))}</strong> — "
        f"{_display(item.get('claim'))} — "
        f"Support: {_display(_ids(item.get('evidence_ids')))} — "
        f"Counter: {_display(_ids(item.get('counter_evidence_ids')))}"
        "</li>"
        for item in hypotheses
        if isinstance(item, dict)
    )
    return f"<section><h2>Hypotheses</h2><ul>{items}</ul></section>"


def _ids(value: object) -> str:
    return ", ".join(item for item in value if isinstance(item, str)) if isinstance(value, list) else ""


def _render_tool_ledger(client: firestore.Client, incident_id: str) -> str:
    query = client.collection(COMMANDS_COLLECTION).where(
        filter=FieldFilter("incident_id", "==", incident_id)
    )
    items = "".join(
        "<li>"
        f"{_display(command.get('tool'))} — "
        f"Args hash: {_display(command.get('arguments_hash'))} — "
        f"Status: {_display(command.get('status'))} — "
        f"Duration: {_display(_command_duration(command))}"
        "</li>"
        for command in (snapshot.to_dict() or {} for snapshot in query.stream())
    )
    return f"<section><h2>Tool Ledger</h2><ul>{items}</ul></section>"


def _command_duration(command: dict[str, object]) -> str:
    result = command.get("completion_result")
    if not isinstance(result, dict):
        return ""
    started = _timestamp(result.get("started_at_utc"))
    completed = _timestamp(result.get("completed_at_utc"))
    if started is None or completed is None or completed < started:
        return ""
    return f"{(completed - started).total_seconds() * 1000:.0f} ms"


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _render_approvals(incident_document: object) -> str:
    cards = "".join(
        "<article>"
        f"<h3>{_display(approval.get('approval_id'))}</h3><dl>"
        f"<dt>Status</dt><dd>{_display(approval.get('status'))}</dd>"
        f"<dt>Action</dt><dd>{_display(approval.get('action_id'))}</dd>"
        f"<dt>Tool</dt><dd>{_display(approval.get('tool'))}</dd>"
        f"<dt>Arguments</dt><dd>{_display(_json(approval.get('canonical_arguments')))}</dd>"
        f"<dt>Arguments hash</dt><dd>{_display(approval.get('canonical_arguments_hash'))}</dd>"
        f"<dt>Evidence hash</dt><dd>{_display(approval.get('evidence_snapshot_hash'))}</dd>"
        f"<dt>Rollback</dt><dd>{_display(approval.get('rollback_plan'))}</dd>"
        f"<dt>Expires</dt><dd>{_display(approval.get('expires_at_utc'))}</dd>"
        "</dl></article>"
        for approval in (
            snapshot.to_dict() or {}
            for snapshot in incident_document.collection(APPROVALS_COLLECTION).stream()
        )
    )
    return f"<section><h2>Approval</h2>{cards}</section>"


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _render_incident(incident_id: str, incident: dict[str, object]) -> str:
    return (
        "<tr>"
        f"<td>{_display(incident_id)}</td>"
        f"<td>{_display(incident.get('state'))}</td>"
        f"<td>{_display(incident.get('summary'))}</td>"
        f"<td>{_display(incident.get('updated_at'))}</td>"
        "</tr>"
    )


def _display(value: object) -> str:
    if isinstance(value, datetime):
        value = value.isoformat()
    return escape("" if value is None else str(value))
