from pathlib import Path


REPO_ROOT = Path(__file__).parents[3]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.ps1"


def test_deploy_script_parameterizes_project_region_and_resource_names() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "[string]$ProjectId" in script
    assert '[string]$Region = "asia-east1"' in script
    assert '[string]$ApiService = "reliability-api"' in script
    assert '[string]$WorkerService = "reliability-worker"' in script
    assert '[string]$ArtifactRepository = "reliability-agent"' in script
    assert '[string]$PubSubTopic = "incident-work"' in script
    assert '[string]$ImageName = "reliability-agent"' in script


def test_deploy_script_checks_gcloud_auth_project_and_build_permission_only() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "Get-Command gcloud -ErrorAction Stop" in script
    assert "gcloud auth list" in script
    assert "gcloud projects describe $ProjectId" in script
    assert "gcloud builds list --project $ProjectId --limit=1" in script
    assert "Docker Desktop" not in script
    assert "WSL" not in script


def test_deploy_script_enables_required_apis_idempotently() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    for api in (
        "artifactregistry.googleapis.com",
        "cloudbuild.googleapis.com",
        "firestore.googleapis.com",
        "iam.googleapis.com",
        "logging.googleapis.com",
        "pubsub.googleapis.com",
        "run.googleapis.com",
        "secretmanager.googleapis.com",
        "aiplatform.googleapis.com",
    ):
        assert api in script
    assert "gcloud services enable $RequiredApis --project $ProjectId" in script
