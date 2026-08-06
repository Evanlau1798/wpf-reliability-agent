from functools import cache

from google.cloud import firestore


@cache
def get_firestore_client(project_id: str) -> firestore.Client:
    return firestore.Client(project=project_id)
