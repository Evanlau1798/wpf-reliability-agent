from app import firestore_client


def test_firestore_client_provider_reuses_client_for_project(monkeypatch) -> None:
    created_projects: list[str] = []

    def create_client(*, project: str) -> object:
        created_projects.append(project)
        return object()

    monkeypatch.setattr(firestore_client.firestore, "Client", create_client)
    firestore_client.get_firestore_client.cache_clear()
    try:
        first = firestore_client.get_firestore_client("project-test")
        second = firestore_client.get_firestore_client("project-test")
    finally:
        firestore_client.get_firestore_client.cache_clear()

    assert first is second
    assert created_projects == ["project-test"]


def test_firestore_collection_names_match_model() -> None:
    assert (
        firestore_client.DEVICES_COLLECTION,
        firestore_client.INCIDENTS_COLLECTION,
        firestore_client.EVIDENCE_COLLECTION,
        firestore_client.ACTIONS_COLLECTION,
        firestore_client.APPROVALS_COLLECTION,
        firestore_client.AUDIT_COLLECTION,
        firestore_client.REPORTS_COLLECTION,
        firestore_client.COMMANDS_COLLECTION,
        firestore_client.EVENT_DEDUP_COLLECTION,
        firestore_client.PROCESSED_RUNS_COLLECTION,
    ) == (
        "devices",
        "incidents",
        "evidence",
        "actions",
        "approvals",
        "audit",
        "reports",
        "commands",
        "event_dedup",
        "processed_runs",
    )
