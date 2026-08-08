from pathlib import Path


REPO_ROOT = Path(__file__).parents[3]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.ps1"
CLOUD_BUILD_CONFIG = REPO_ROOT / "src" / "cloud" / "cloudbuild.yaml"


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


def test_deploy_script_creates_artifact_repository_only_when_missing() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "gcloud artifacts repositories describe $ArtifactRepository" in script
    assert "gcloud artifacts repositories create $ArtifactRepository" in script
    assert "--repository-format=docker" in script


def test_deploy_script_preserves_existing_firestore_database() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'gcloud firestore databases describe --database="(default)"' in script
    assert 'gcloud firestore databases create --database="(default)"' in script
    assert "--type=firestore-native" in script


def test_deploy_script_creates_pubsub_topic_only_when_missing() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "gcloud pubsub topics describe $PubSubTopic" in script
    assert "gcloud pubsub topics create $PubSubTopic" in script


def test_deploy_script_creates_api_service_account_with_minimal_project_roles() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "Ensure-ServiceAccount $ApiServiceAccount" in script
    assert '"roles/datastore.user"' in script
    assert '"roles/logging.logWriter"' in script
    assert '"roles/pubsub.publisher"' in script
    assert "roles/owner" not in script.lower()
    assert "roles/editor" not in script.lower()


def test_deploy_script_creates_worker_service_account_with_minimal_project_roles() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "Ensure-ServiceAccount $WorkerServiceAccount" in script
    assert '"roles/aiplatform.user"' in script
    assert "$WorkerProjectRoles" in script


def test_pubsub_invoker_has_only_worker_service_invoker_binding() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "Ensure-ServiceAccount $PubSubInvokerServiceAccount" in script
    assert "gcloud run services add-iam-policy-binding $WorkerService" in script
    assert "--role=roles/run.invoker" in script
    assert "Grant-ProjectRoles $PubSubInvokerServiceAccountEmail" not in script


def test_deploy_script_creates_secret_placeholders_without_secret_values() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "gcloud secrets describe $Name" in script
    assert "gcloud secrets create $Name" in script
    assert "Ensure-Secret $DeviceTokenSecret" in script
    assert "Ensure-Secret $OperatorTokenSecret" in script
    assert "--role=roles/secretmanager.secretAccessor" in script
    assert "gcloud secrets versions add $DeviceTokenSecret" in script
    assert "gcloud secrets versions add $OperatorTokenSecret" in script
    assert "--data-file=-" in script


def test_cloud_build_uses_explicit_minimal_identity_sha_tag_and_digest() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    config = CLOUD_BUILD_CONFIG.read_text(encoding="utf-8")

    build_roles = script.split("$BuildProjectRoles = @(", 1)[1].split(")", 1)[0]
    assert '"roles/logging.logWriter"' in build_roles
    assert "artifactregistry" not in build_roles
    assert "storage" not in build_roles
    assert "datastore" not in build_roles
    assert "secretmanager" not in build_roles
    assert "aiplatform" not in build_roles
    assert "Ensure-ServiceAccount $BuildServiceAccount" in script
    assert "gcloud artifacts repositories add-iam-policy-binding $ArtifactRepository" in script
    assert "--role=roles/artifactregistry.writer" in script
    assert "gcloud storage buckets add-iam-policy-binding $BuildSourceBucketUri" in script
    assert "--role=roles/storage.objectViewer" in script
    assert "gcloud builds submit src/cloud" in script
    assert "--service-account=\"projects/$ProjectId/serviceAccounts/$BuildServiceAccountEmail\"" in script
    assert "_GIT_SHA=$GitSha" in script
    assert "gcloud artifacts docker images describe $ImageTag" in script
    assert "GIT_SHA=${_GIT_SHA}" in config
    assert "${_IMAGE_URI}" in config


def test_api_deploy_uses_digest_identity_cost_limits_and_secret_refs() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "gcloud secrets versions list $Name" in script
    assert "gcloud run deploy $ApiService" in script
    assert "--image=$ImageDigestRef" in script
    assert "--service-account=$ApiServiceAccountEmail" in script
    assert "--min-instances=0" in script
    assert "--max-instances=2" in script
    assert "--memory=512Mi" in script
    assert "--allow-unauthenticated" in script
    assert "DEMO_DEVICE_TOKEN=${DeviceTokenSecret}:latest" in script
    assert "DEMO_OPERATOR_TOKEN=${OperatorTokenSecret}:latest" in script
