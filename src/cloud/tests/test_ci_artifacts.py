from pathlib import Path


REPO_ROOT = Path(__file__).parents[3]


def test_repository_has_one_ci_workflow() -> None:
    workflows = REPO_ROOT / ".github" / "workflows"
    files = sorted(path.name for path in workflows.glob("*.y*ml")) if workflows.exists() else []

    assert files == ["ci.yml"]
