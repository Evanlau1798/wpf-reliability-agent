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
