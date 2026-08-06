from functools import cache

from google.cloud import pubsub_v1


@cache
def get_publisher_client() -> pubsub_v1.PublisherClient:
    return pubsub_v1.PublisherClient()
