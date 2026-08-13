import importlib.util
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).parents[3]
RESET_SCRIPT = REPO_ROOT / "scripts" / "reset-demo.ps1"
RESET_CLOUD = REPO_ROOT / "src" / "cloud" / "scripts" / "reset_demo.py"


def test_demo_reset_requires_explicit_cloud_confirmation_and_stopped_app() -> None:
    script = RESET_SCRIPT.read_text(encoding="utf-8")

    assert "Demo.BrokenWpfApp" in script
    assert "must be stopped" in script
    assert 'ConfirmProjectId -cne $ProjectId' in script
    assert "[switch]$ResetCloud" in script
    assert "WpfReliabilityAgent" in script
    assert "outbox.db" in script
    assert "reset_demo.py" in script


def test_cloud_reset_is_scoped_to_the_demo_application() -> None:
    script = RESET_CLOUD.read_text(encoding="utf-8")

    assert 'APPLICATION_ID = "demo-broken-wpf-app"' in script
    assert 'where(filter=FieldFilter("application_id", "==", APPLICATION_ID))' in script
    assert 'where(filter=FieldFilter("incident_id", "in", batch))' in script
    assert "recursive_delete" in script


def test_cloud_reset_deletes_only_records_linked_to_demo_incidents() -> None:
    spec = importlib.util.spec_from_file_location("reset_demo", RESET_CLOUD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    deleted: list[str] = []
    demo = SimpleNamespace(id="incident-demo", reference="incidents/incident-demo")

    class Query:
        def __init__(self, snapshots):
            self.snapshots = snapshots

        def where(self, *, filter):
            assert filter.value in (module.APPLICATION_ID, ["incident-demo"])
            return self

        def stream(self):
            return self.snapshots

    class Client:
        def collection(self, name):
            if name == "incidents":
                return Query([demo])
            linked = SimpleNamespace(reference=SimpleNamespace(delete=lambda: deleted.append(name)))
            return Query([linked])

        def recursive_delete(self, reference):
            deleted.append(reference)

    result = module.reset_demo(Client())

    assert result == {"incidents": 1, "commands": 1, "event_dedup": 1, "processed_runs": 1}
    assert deleted == ["commands", "event_dedup", "processed_runs", "incidents/incident-demo"]
