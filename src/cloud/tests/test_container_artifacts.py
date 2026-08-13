from pathlib import Path


REPO_ROOT = Path(__file__).parents[3]


def test_dockerfile_uses_multistage_runtime_server() -> None:
    dockerfile = (REPO_ROOT / "src" / "cloud" / "Dockerfile").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "src" / "cloud" / "pyproject.toml").read_text(encoding="utf-8")

    assert dockerfile.count("FROM python:3.12-slim") == 2
    assert "FROM python:3.12-slim AS builder" in dockerfile
    assert "COPY --from=builder /install /usr/local" in dockerfile
    assert '"uvicorn>=0.34,<1"' in pyproject
    assert 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]' in dockerfile


def test_test_client_dependency_names_the_imported_http_library() -> None:
    pyproject = (REPO_ROOT / "src" / "cloud" / "pyproject.toml").read_text(encoding="utf-8")

    assert '"httpx>=0.27,<0.29"' in pyproject
    assert "httpx2" not in pyproject


def test_test_runner_uses_the_audited_release() -> None:
    pyproject = (REPO_ROOT / "src" / "cloud" / "pyproject.toml").read_text(encoding="utf-8")

    assert '"pytest==9.0.3"' in pyproject


def test_runtime_image_and_ci_use_non_root_user() -> None:
    dockerfile = (REPO_ROOT / "src" / "cloud" / "Dockerfile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "USER appuser" in dockerfile
    assert 'test "$(docker run --rm reliability-agent:ci id -u)" != "0"' in workflow


def test_ci_smokes_api_role_container() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "--env SERVICE_ROLE=api" in workflow
    assert "http://127.0.0.1:18080/healthz" in workflow


def test_ci_smokes_worker_role_and_worker_only_route() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "--env SERVICE_ROLE=worker" in workflow
    assert "http://127.0.0.1:18081/v1/work:push" in workflow
    assert 'test "$status" = "401"' in workflow


def test_image_records_build_git_sha() -> None:
    dockerfile = (REPO_ROOT / "src" / "cloud" / "Dockerfile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "ARG GIT_SHA=unknown" in dockerfile
    assert "LABEL org.opencontainers.image.revision=$GIT_SHA" in dockerfile
    assert "--build-arg GIT_SHA=${{ github.sha }}" in workflow
