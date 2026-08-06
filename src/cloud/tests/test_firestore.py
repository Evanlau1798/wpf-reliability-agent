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
