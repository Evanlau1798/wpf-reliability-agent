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


def test_runtime_image_and_ci_use_non_root_user() -> None:
    dockerfile = (REPO_ROOT / "src" / "cloud" / "Dockerfile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "USER appuser" in dockerfile
    assert 'test "$(docker run --rm reliability-agent:ci id -u)" != "0"' in workflow
