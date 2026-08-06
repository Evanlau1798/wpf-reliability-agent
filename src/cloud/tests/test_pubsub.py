from app import pubsub


def test_publisher_client_provider_reuses_client(monkeypatch) -> None:
    created: list[object] = []

    def create_client() -> object:
        client = object()
        created.append(client)
        return client

    monkeypatch.setattr(pubsub.pubsub_v1, "PublisherClient", create_client)
    pubsub.get_publisher_client.cache_clear()
    try:
        first = pubsub.get_publisher_client()
        second = pubsub.get_publisher_client()
    finally:
        pubsub.get_publisher_client.cache_clear()

    assert first is second
    assert len(created) == 1
