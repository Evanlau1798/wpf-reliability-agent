from pathlib import Path


REPO_ROOT = Path(__file__).parents[3]


def test_repository_has_one_ci_workflow() -> None:
    workflows = REPO_ROOT / ".github" / "workflows"
    files = sorted(path.name for path in workflows.glob("*.y*ml")) if workflows.exists() else []

    assert files == ["ci.yml"]


def test_contracts_job_validates_python_and_dotnet_fixtures() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/setup-python@v5" in workflow
    assert "actions/setup-dotnet@v4" in workflow
    assert "python -m pytest tests/test_contracts.py -q" in workflow
    assert "FullyQualifiedName~ContractFixtureTests" in workflow
    assert "-c Release --no-build" in workflow
