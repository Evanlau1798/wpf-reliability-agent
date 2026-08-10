from pathlib import Path


REPO_ROOT = Path(__file__).parents[3]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.ps1"
CLEANUP_SCRIPT = REPO_ROOT / "scripts" / "cleanup-cloud.ps1"
CLOUD_BUILD_CONFIG = REPO_ROOT / "src" / "cloud" / "cloudbuild.yaml"


def test_deploy_script_parameterizes_project_region_and_resource_names() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "[string]$ProjectId" in script
    assert '[string]$Region = "asia-east1"' in script
    assert '[string]$VertexLocation = "global"' in script
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
    assert "foreach ($Api in $RequiredApis)" in script
    assert "gcloud services enable $Api --project $ProjectId" in script


def test_deploy_script_tolerates_expected_missing_resource_probes() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    helper = script.split("function Test-GcloudResource", 1)[1].split(
        "function Assert-GcloudPrerequisites", 1
    )[0]

    assert "try {" in helper
    assert "catch {" in helper
    assert "param([string[]]$Arguments)" in helper
    assert "Get-Command gcloud.cmd -ErrorAction SilentlyContinue" in helper
    assert "Get-Command gcloud -ErrorAction Stop" in helper
    assert "& $probeCommand.Source @Arguments >$null 2>$null" in helper
    assert "& gcloud @Arguments" not in helper
    assert "$previousErrorActionPreference = $ErrorActionPreference" in helper
    assert '$ErrorActionPreference = "Continue"' in helper
    assert "finally {" in helper
    assert "$ErrorActionPreference = $previousErrorActionPreference" in helper
    assert "return $LASTEXITCODE -eq 0" in helper
    assert "[scriptblock]$Probe" not in helper
    assert script.count("Test-GcloudResource -Arguments @(") == 7


def test_deploy_script_creates_artifact_repository_only_when_missing() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert '"artifacts", "repositories", "describe", $ArtifactRepository' in script
    assert "gcloud artifacts repositories create $ArtifactRepository" in script
    assert "--repository-format=docker" in script


def test_deploy_script_preserves_existing_firestore_database() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert '"firestore", "databases", "describe", "--database=(default)"' in script
    assert 'gcloud firestore databases create --database="(default)"' in script
    assert "--type=firestore-native" in script


def test_deploy_script_creates_pubsub_topic_only_when_missing() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert '"pubsub", "topics", "describe", $Name' in script
    assert "gcloud pubsub topics create $Name" in script
    assert "Ensure-PubSubTopic $PubSubTopic" in script


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

    assert '"secrets", "describe", $Name' in script
    assert "gcloud secrets create $Name" in script
    assert "Ensure-Secret $DeviceTokenSecret" in script
    assert "Ensure-Secret $OperatorTokenSecret" in script
    assert "--role=roles/secretmanager.secretAccessor" in script
    assert "gcloud secrets versions add $DeviceTokenSecret" in script
    assert "gcloud secrets versions add $OperatorTokenSecret" in script
    assert "--data-file=-" in script


def test_deploy_script_handles_empty_secret_version_list_before_bootstrap_stop() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    block = script.split("function Assert-SecretHasEnabledVersion", 1)[1].split(
        "function Ensure-BuildSourceBucket", 1
    )[0]

    assert ").Trim()" not in block
    assert "if ($LASTEXITCODE -ne 0 -or -not $version)" in block
    assert "needs an enabled version before deployment" in block


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


def test_worker_deploy_uses_same_digest_private_identity_and_limits() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "gcloud run deploy $WorkerService" in script
    assert script.count("--image=$ImageDigestRef") == 2
    assert "--service-account=$WorkerServiceAccountEmail" in script
    assert "--memory=1Gi" in script
    assert "--timeout=120" in script
    assert "--no-allow-unauthenticated" in script
    assert "SERVICE_ROLE=worker" in script
    assert "PUBSUB_INVOKER_EMAIL=$PubSubInvokerServiceAccountEmail" in script
    assert "GOOGLE_CLOUD_LOCATION=$VertexLocation" in script
    assert "gcloud run services update $WorkerService" in script
    assert "PUBSUB_PUSH_AUDIENCE=$WorkerUrl" in script
    assert "Grant-WorkerInvoker $PubSubInvokerServiceAccountEmail" in script


def test_push_subscription_uses_worker_url_and_oidc_invoker_identity() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert '[string]$PubSubSubscription = "incident-work-push"' in script
    assert "service-$ProjectNumber@gcp-sa-pubsub.iam.gserviceaccount.com" in script
    assert "gcloud iam service-accounts add-iam-policy-binding $PubSubInvokerServiceAccountEmail" in script
    assert "--role=roles/iam.serviceAccountTokenCreator" in script
    assert '"pubsub", "subscriptions", "describe", $PubSubSubscription' in script
    assert "gcloud pubsub subscriptions create $PubSubSubscription" in script
    assert "gcloud pubsub subscriptions update $PubSubSubscription" in script
    assert '$PushEndpoint = "$WorkerUrl/v1/work:push"' in script
    assert '--push-endpoint="$PushEndpoint"' in script
    assert "--push-auth-service-account=$PubSubInvokerServiceAccountEmail" in script
    assert "--push-auth-token-audience=$WorkerUrl" in script


def test_push_subscription_has_bounded_dead_letter_delivery() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    dead_letter_access = script.split("function Grant-DeadLetterAccess", 1)[1].split(
        "function Ensure-Secret", 1
    )[0]

    assert '[string]$DeadLetterTopic = "incident-work-dead-letter"' in script
    assert "Ensure-PubSubTopic $DeadLetterTopic" in script
    assert "--dead-letter-topic=$DeadLetterTopic" in script
    assert "--max-delivery-attempts=5" in script
    assert "gcloud pubsub topics add-iam-policy-binding $DeadLetterTopic" in script
    assert "--role=roles/pubsub.publisher" in script
    assert "gcloud pubsub subscriptions add-iam-policy-binding $PubSubSubscription" in script
    assert "--role=roles/pubsub.subscriber" in script
    assert "--condition=None" not in dead_letter_access


def test_deploy_script_outputs_non_secret_deployment_and_windows_config_names() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'Write-Host "API URL: $ApiUrl"' in script
    assert 'Write-Host "Cloud Build ID: $BuildId"' in script
    assert 'Write-Host "Image digest: $ImageDigestRef"' in script
    assert 'Write-Host "API revision: $ApiRevision"' in script
    assert 'Write-Host "Worker revision: $WorkerRevision"' in script
    assert 'Write-Host "  WPF_RELIABILITY_API_BASE_URI=$ApiUrl"' in script
    assert 'Write-Host "  WPF_RELIABILITY_DEVICE_ID=$DemoDeviceId"' in script
    assert 'Write-Host "  WPF_RELIABILITY_DEVICE_TOKEN=<Secret Manager device token>"' in script


def test_deploy_script_runs_health_and_authenticated_telemetry_smoke() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'Invoke-RestMethod -Method Get -Uri "$ApiUrl/health"' in script
    assert "gcloud secrets versions access latest --secret=$DeviceTokenSecret" in script
    assert 'Authorization = "Bearer $DeviceToken"' in script
    assert 'Invoke-RestMethod -Method Post -Uri "$ApiUrl/v1/telemetry:batch"' in script
    assert "'{\"events\":[]}'" in script
    assert "$DeviceToken = $null" in script


def test_deploy_script_smokes_pubsub_worker_and_correlates_cloud_log() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "function Invoke-PubSubWorkerSmokeTest" in script
    assert "gcloud pubsub topics publish $PubSubTopic" in script
    assert '--message="malformed-smoke"' in script
    assert '--format="value(messageIds[0])"' in script
    assert "gcloud logging read" in script
    assert 'resource.labels.service_name=`"$WorkerService`"' in script
    assert 'jsonPayload.message:`"worker_message_rejected`"' in script
    assert 'jsonPayload.message:`"$RunId`"' in script
    assert '--format="value(jsonPayload.message)"' in script


def test_cleanup_script_is_confirmed_parameterized_and_project_scoped() -> None:
    script = CLEANUP_SCRIPT.read_text(encoding="utf-8")

    assert "[string]$ProjectId" in script
    assert "[string]$ConfirmProjectId" in script
    assert '[string]$Region = "asia-east1"' in script
    assert '[string]$ApiService = "reliability-api"' in script
    assert '[string]$WorkerService = "reliability-worker"' in script
    assert '[string]$ArtifactRepository = "reliability-agent"' in script
    assert '[string]$PubSubTopic = "incident-work"' in script
    assert '[string]$PubSubSubscription = "incident-work-push"' in script
    assert '[string]$DeadLetterTopic = "incident-work-dead-letter"' in script
    assert "[switch]$DeleteFirestore" in script
    assert "if ($ConfirmProjectId -cne $ProjectId)" in script
    assert "gcloud projects delete" not in script
    assert script.count("--project=$ProjectId") >= 10
    firestore_block = script.split("if ($DeleteFirestore) {", 1)[1].split("}", 1)[0]
    assert 'gcloud firestore databases delete --database="(default)"' in firestore_block
