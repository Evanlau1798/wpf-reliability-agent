from datetime import datetime
from html import escape

from google.cloud import firestore

from app.firestore_client import AUDIT_COLLECTION, EVIDENCE_COLLECTION, INCIDENTS_COLLECTION


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
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Incident {_display(incident_id)}</title></head><body><main>"
        f"<h1>Incident {_display(incident_id)}</h1><dl>"
        f"<dt>State</dt><dd>{_display(incident.get('state'))}</dd>"
        f"<dt>Summary</dt><dd>{_display(incident.get('summary'))}</dd>"
        f"<dt>Updated</dt><dd>{_display(incident.get('updated_at'))}</dd>"
        f"</dl>{timeline}{evidence_index}</main></body></html>"
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
