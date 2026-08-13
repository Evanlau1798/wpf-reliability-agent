import argparse
import json
from collections.abc import Iterable

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter


APPLICATION_ID = "demo-broken-wpf-app"
RELATED_COLLECTIONS = ("commands", "event_dedup", "processed_runs")
FIRESTORE_IN_LIMIT = 30


def batches(values: list[str]) -> Iterable[list[str]]:
    for index in range(0, len(values), FIRESTORE_IN_LIMIT):
        yield values[index:index + FIRESTORE_IN_LIMIT]


def reset_demo(client: firestore.Client) -> dict[str, int]:
    incidents = list(
        client.collection("incidents")
        .where(filter=FieldFilter("application_id", "==", APPLICATION_ID))
        .stream()
    )
    incident_ids = [snapshot.id for snapshot in incidents]
    deleted = {"incidents": 0, **{name: 0 for name in RELATED_COLLECTIONS}}

    for collection_name in RELATED_COLLECTIONS:
        for batch in batches(incident_ids):
            query = client.collection(collection_name).where(filter=FieldFilter("incident_id", "in", batch))
            for snapshot in query.stream():
                snapshot.reference.delete()
                deleted[collection_name] += 1

    for snapshot in incidents:
        client.recursive_delete(snapshot.reference)
        deleted["incidents"] += 1
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete demo-only Firestore workflow records.")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    print(json.dumps(reset_demo(firestore.Client(project=args.project)), sort_keys=True))


if __name__ == "__main__":
    main()
