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


def test_windows_job_builds_and_tests_release_without_container_prerequisites() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "  windows:" in workflow
    assert "dotnet restore src/windows/Reliability.Sensor.Tests/Reliability.Sensor.Tests.csproj" in workflow
    assert "dotnet build src/windows/Reliability.Sensor.Tests/Reliability.Sensor.Tests.csproj -c Release --no-restore" in workflow
    assert "dotnet test src/windows/Reliability.Sensor.Tests/Reliability.Sensor.Tests.csproj -c Release --no-build" in workflow
    assert "Docker Desktop" not in workflow
    assert "wsl" not in workflow.lower()


def test_cloud_job_lints_types_and_tests_python_package() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "  cloud:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "python -m ruff check --select E9,F63,F7,F82 app tests" in workflow
    assert "python -m mypy --ignore-missing-imports --follow-imports=skip" in workflow
    assert "app/models.py app/config.py app/contracts.py app/policy.py app/auth.py app/worker.py" in workflow
    assert "python -m pytest -q" in workflow


def test_container_job_builds_cloud_image_on_linux_runner() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "  container:" in workflow
    assert "docker build --file src/cloud/Dockerfile --build-arg GIT_SHA=${{ github.sha }} --tag reliability-agent:ci src/cloud" in workflow


def test_cloud_job_scans_tracked_files_for_secrets() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python scripts/scan_secrets.py" in workflow


def test_python_jobs_use_setup_python_dependency_cache() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert workflow.count('cache: "pip"') == 2
    assert workflow.count("cache-dependency-path: src/cloud/pyproject.toml") == 2
    assert "actions/cache" not in workflow


def test_ci_runs_for_pull_requests_and_master_pushes() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "  pull_request:" in workflow
    assert "  push:" in workflow
    assert "branches: [master]" in workflow


def test_dotnet_test_projects_do_not_install_an_unused_coverage_collector() -> None:
    package_versions = (REPO_ROOT / "Directory.Packages.props").read_text(encoding="utf-8")
    project_files = [
        REPO_ROOT / "src" / "windows" / "Reliability.Sensor.Tests" / "Reliability.Sensor.Tests.csproj",
        REPO_ROOT / "src" / "windows" / "Demo.BrokenWpfApp.Tests" / "Demo.BrokenWpfApp.Tests.csproj",
    ]

    assert "coverlet.collector" not in package_versions
    assert all("coverlet.collector" not in project.read_text(encoding="utf-8") for project in project_files)
