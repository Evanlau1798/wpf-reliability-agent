from datetime import datetime
from html import escape

from google.cloud import firestore

from app.firestore_client import INCIDENTS_COLLECTION


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
    snapshot = client.collection(INCIDENTS_COLLECTION).document(incident_id).get()
    if not snapshot.exists:
        return None
    incident = snapshot.to_dict() or {}
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Incident {_display(incident_id)}</title></head><body><main>"
        f"<h1>Incident {_display(incident_id)}</h1><dl>"
        f"<dt>State</dt><dd>{_display(incident.get('state'))}</dd>"
        f"<dt>Summary</dt><dd>{_display(incident.get('summary'))}</dd>"
        f"<dt>Updated</dt><dd>{_display(incident.get('updated_at'))}</dd>"
        "</dl></main></body></html>"
    )


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
