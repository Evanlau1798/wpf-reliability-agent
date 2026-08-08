import json
from datetime import datetime
from html import escape
from urllib.parse import quote

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.auth import OPERATOR_CSRF_COOKIE, OPERATOR_CSRF_HEADER
from app.firestore_client import (
    APPROVALS_COLLECTION,
    AUDIT_COLLECTION,
    COMMANDS_COLLECTION,
    EVIDENCE_COLLECTION,
    INCIDENTS_COLLECTION,
    PROCESSED_RUNS_COLLECTION,
    REPORTS_COLLECTION,
)
from app.models import IncidentReport


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
    audit_records = _audit_records(incident_document)
    timeline = _render_timeline(audit_records)
    evidence_index = _render_evidence_index(incident_document)
    hypotheses = _render_hypotheses(incident)
    commands = _incident_commands(client, incident_id)
    tool_ledger = _render_tool_ledger(commands)
    approvals = _render_approvals(incident_document)
    verification = _render_verification(audit_records)
    reports = _render_reports(incident_document, incident_id)
    workflow_ids = _render_workflow_ids(client, incident_id, commands)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Incident {_display(incident_id)}</title></head><body><main>"
        f"<h1>Incident {_display(incident_id)}</h1><dl>"
        f"<dt>State</dt><dd>{_display(incident.get('state'))}</dd>"
        f"<dt>Summary</dt><dd>{_display(incident.get('summary'))}</dd>"
        f"<dt>Updated</dt><dd>{_display(incident.get('updated_at'))}</dd>"
        f"</dl>{timeline}{evidence_index}{hypotheses}{tool_ledger}{approvals}{verification}{reports}"
        f"{workflow_ids}"
        "</main></body></html>"
    )


def render_report_download(
    client: firestore.Client,
    incident_id: str,
    version: str,
    report_format: str,
) -> tuple[str, str] | None:
    if report_format not in {"md", "html"}:
        return None
    snapshot = (
        client.collection(INCIDENTS_COLLECTION)
        .document(incident_id)
        .collection(REPORTS_COLLECTION)
        .document(version)
        .get()
    )
    if not snapshot.exists:
        return None
    report = IncidentReport.model_validate(snapshot.to_dict() or {})
    from app.reporting import render_report_html, render_report_markdown

    if report_format == "md":
        return render_report_markdown(report), "text/markdown"
    return render_report_html(report), "text/html"


def _audit_records(incident_document: object) -> list[dict[str, object]]:
    records = [
        snapshot.to_dict() or {}
        for snapshot in incident_document.collection(AUDIT_COLLECTION).stream()
    ]
    records.sort(key=lambda record: (int(record["sequence"]), str(record["timestamp_utc"])))
    return records


def _render_timeline(records: list[dict[str, object]]) -> str:
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


def _incident_commands(
    client: firestore.Client,
    incident_id: str,
) -> list[tuple[str, dict[str, object]]]:
    query = client.collection(COMMANDS_COLLECTION).where(
        filter=FieldFilter("incident_id", "==", incident_id)
    )
    return sorted(
        ((str(snapshot.id), snapshot.to_dict() or {}) for snapshot in query.stream()),
        key=lambda item: item[0],
    )


def _render_tool_ledger(commands: list[tuple[str, dict[str, object]]]) -> str:
    items = "".join(
        "<li>"
        f"{_display(command.get('tool'))} — "
        f"Args hash: {_display(command.get('arguments_hash'))} — "
        f"Status: {_display(command.get('status'))} — "
        f"Duration: {_display(_command_duration(command))}"
        "</li>"
        for _document_id, command in commands
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
    approvals = [
        snapshot.to_dict() or {}
        for snapshot in incident_document.collection(APPROVALS_COLLECTION).stream()
    ]
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
        f"</dl>{_approval_controls(approval)}</article>"
        for approval in approvals
    )
    script = _approval_script() if any(item.get("status") == "PENDING" for item in approvals) else ""
    return f"<section><h2>Approval</h2>{cards}</section>{script}"


def _approval_controls(approval: dict[str, object]) -> str:
    if approval.get("status") != "PENDING":
        return ""
    approval_id = _display(approval.get("approval_id"))
    return (
        f'<p><button type="button" data-approval-id="{approval_id}" '
        'data-approval-decision="approve">Approve</button> '
        f'<button type="button" data-approval-id="{approval_id}" '
        'data-approval-decision="reject">Reject</button></p>'
    )


def _approval_script() -> str:
    cookie_name = json.dumps(f"{OPERATOR_CSRF_COOKIE}=")
    header_name = json.dumps(OPERATOR_CSRF_HEADER)
    return (
        "<script>document.addEventListener('click',async(event)=>{"
        "const button=event.target.closest?.('button[data-approval-decision]');"
        "if(!button)return;"
        f"const csrfName={cookie_name};"
        "const csrf=document.cookie.split('; ').find(part=>part.startsWith(csrfName));"
        "if(!csrf)return;"
        "const token=decodeURIComponent(csrf.slice(csrfName.length));"
        "const approvalId=button.dataset.approvalId;"
        "const decision=button.dataset.approvalDecision;"
        "const response=await fetch('/v1/approvals/'+encodeURIComponent(approvalId)+':decide',{"
        "method:'POST',headers:{'Content-Type':'application/json',"
        f"{header_name}:token"
        "},body:JSON.stringify({decision})});"
        "if(response.ok)location.reload();"
        "});</script>"
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _render_verification(records: list[dict[str, object]]) -> str:
    verification = next(
        (
            value
            for record in reversed(records)
            if isinstance((value := record.get("verification")), dict)
        ),
        None,
    )
    if verification is None or not isinstance(verification.get("metrics"), dict):
        return ""
    metrics = verification["metrics"]
    rows = "".join(
        _metric_row(label, metrics.get(key))
        for label, key in (
            ("Binding rate", "binding_errors_per_second"),
            ("Frame p95", "frame_p95_ms"),
            ("Visual count", "visual_count"),
        )
    )
    return (
        "<section><h2>Before / After</h2>"
        '<table><thead><tr><th scope="col">Metric</th><th scope="col">Before</th>'
        '<th scope="col">After</th><th scope="col">Unit</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></section>"
    )


def _metric_row(label: str, metric: object) -> str:
    if not isinstance(metric, dict):
        return ""
    return (
        f"<tr><th scope=\"row\">{_display(label)}</th>"
        f"<td>{_display(metric.get('before'))}</td>"
        f"<td>{_display(metric.get('after'))}</td>"
        f"<td>{_display(metric.get('unit'))}</td></tr>"
    )


def _render_reports(incident_document: object, incident_id: str) -> str:
    reports = sorted(
        (
            (str(snapshot.id), snapshot.to_dict() or {})
            for snapshot in incident_document.collection(REPORTS_COLLECTION).stream()
        ),
        key=lambda item: item[0],
    )
    items = "".join(_report_item(incident_id, version, report) for version, report in reports)
    return f"<section><h2>Reports</h2><ul>{items}</ul></section>"


def _report_item(incident_id: str, version: str, report: dict[str, object]) -> str:
    metadata = report.get("metadata")
    report_hash = metadata.get("report_sha256") if isinstance(metadata, dict) else None
    base = (
        f"/console/incidents/{quote(incident_id, safe='')}/reports/"
        f"{quote(version, safe='')}"
    )
    return (
        f"<li>Version {_display(version)} — SHA-256: {_display(report_hash)} — "
        f'<a href="{_display(base)}.md" download>Markdown</a> — '
        f'<a href="{_display(base)}.html" download>HTML</a></li>'
    )


def _render_workflow_ids(
    client: firestore.Client,
    incident_id: str,
    commands: list[tuple[str, dict[str, object]]],
) -> str:
    runs = client.collection(PROCESSED_RUNS_COLLECTION).where(
        filter=FieldFilter("incident_id", "==", incident_id)
    )
    run_ids = sorted(str(snapshot.id) for snapshot in runs.stream())
    command_ids = sorted(
        {
            str(command.get("command_id") or document_id)
            for document_id, command in commands
        }
    )
    return (
        "<section><h2>Workflow IDs</h2><dl>"
        f"<dt>Incident ID</dt><dd><code>{_display(incident_id)}</code></dd>"
        f"<dt>Run IDs</dt><dd><code>{_display(', '.join(run_ids) or 'None')}</code></dd>"
        f"<dt>Command IDs</dt><dd><code>{_display(', '.join(command_ids) or 'None')}</code></dd>"
        "</dl></section>"
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
