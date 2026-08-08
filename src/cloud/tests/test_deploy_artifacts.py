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
