param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$Region = "asia-east1",
    [string]$VertexLocation = "global",
    [string]$ApiService = "reliability-api",
    [string]$WorkerService = "reliability-worker",
    [string]$ArtifactRepository = "reliability-agent",
    [string]$PubSubTopic = "incident-work",
    [string]$PubSubSubscription = "incident-work-push",
    [string]$DeadLetterTopic = "incident-work-dead-letter",
    [string]$ImageName = "reliability-agent",
    [string]$ApiServiceAccount = "reliability-api-sa",
    [string]$WorkerServiceAccount = "reliability-worker-sa",
    [string]$PubSubInvokerServiceAccount = "pubsub-invoker-sa",
    [string]$BuildServiceAccount = "reliability-build-sa",
    [string]$DeviceTokenSecret = "reliability-device-token",
    [string]$OperatorTokenSecret = "reliability-operator-token",
    [string]$BuildSourceBucket = "",
    [string]$DemoDeviceId = "demo-device"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $BuildSourceBucket) {
    $BuildSourceBucket = "$ProjectId-reliability-build-source"
}
$BuildSourceBucketUri = "gs://$BuildSourceBucket"

$RequiredApis = @(
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com"
)
$ApiProjectRoles = @(
    "roles/datastore.user",
    "roles/logging.logWriter",
    "roles/pubsub.publisher"
)
$WorkerProjectRoles = @(
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/logging.logWriter"
)
$BuildProjectRoles = @(
    "roles/logging.logWriter"
)

function Test-GcloudResource {
    param([scriptblock]$Probe)

    try {
        . $Probe >$null 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Assert-GcloudPrerequisites {
    Get-Command gcloud -ErrorAction Stop | Out-Null

    $activeAccount = (& gcloud auth list --filter="status:ACTIVE" --format="value(account)" | Select-Object -First 1)
    if (-not $activeAccount) {
        throw "No active gcloud account is configured."
    }

    & gcloud projects describe $ProjectId --format="value(projectId)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Project access check failed for '$ProjectId'."
    }
}

function Assert-CloudBuildPermission {
    & gcloud builds list --project $ProjectId --limit=1 --format="value(id)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Cloud Build access check failed for '$ProjectId'."
    }
}

function Enable-RequiredApis {
    foreach ($Api in $RequiredApis) {
        & gcloud services enable $Api --project $ProjectId
        if ($LASTEXITCODE -ne 0) {
            throw "Required API enablement failed for '$Api' in '$ProjectId'."
        }
    }
}

function Ensure-ArtifactRepository {
    if (Test-GcloudResource { & gcloud artifacts repositories describe $ArtifactRepository --location $Region --project $ProjectId --format="value(name)" }) {
        return
    }

    & gcloud artifacts repositories create $ArtifactRepository --repository-format=docker --location $Region --project $ProjectId
    if ($LASTEXITCODE -ne 0) {
        throw "Artifact Registry repository creation failed."
    }
}

function Ensure-FirestoreDatabase {
    if (Test-GcloudResource { & gcloud firestore databases describe --database="(default)" --project $ProjectId --format="value(name)" }) {
        return
    }

    & gcloud firestore databases create --database="(default)" --location=$Region --type=firestore-native --project $ProjectId
    if ($LASTEXITCODE -ne 0) {
        throw "Firestore database creation failed."
    }
}

function Ensure-PubSubTopic {
    param([string]$Name)

    if (Test-GcloudResource { & gcloud pubsub topics describe $Name --project $ProjectId --format="value(name)" }) {
        return
    }

    & gcloud pubsub topics create $Name --project $ProjectId
    if ($LASTEXITCODE -ne 0) {
        throw "Pub/Sub topic creation failed for '$Name'."
    }
}

function Get-ServiceAccountEmail {
    param([string]$Name)
    return "$Name@$ProjectId.iam.gserviceaccount.com"
}

function Ensure-ServiceAccount {
    param(
        [string]$Name,
        [string]$DisplayName
    )

    $email = Get-ServiceAccountEmail $Name
    if (-not (Test-GcloudResource { & gcloud iam service-accounts describe $email --project $ProjectId --format="value(email)" })) {
        & gcloud iam service-accounts create $Name --display-name="$DisplayName" --project $ProjectId | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Service account creation failed for '$Name'."
        }
    }
    return $email
}

function Grant-ProjectRoles {
    param(
        [string]$ServiceAccountEmail,
        [string[]]$Roles
    )

    foreach ($role in $Roles) {
        & gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$ServiceAccountEmail" --role=$role --condition=None --quiet | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "IAM binding failed for '$ServiceAccountEmail' and '$role'."
        }
    }
}

function Grant-WorkerInvoker {
    param([string]$ServiceAccountEmail)

    & gcloud run services add-iam-policy-binding $WorkerService --region $Region --project $ProjectId --member="serviceAccount:$ServiceAccountEmail" --role=roles/run.invoker --condition=None --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Worker invoker binding failed for '$ServiceAccountEmail'."
    }
}

function Get-PubSubServiceAgentEmail {
    $ProjectNumber = (& gcloud projects describe $ProjectId --format="value(projectNumber)").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $ProjectNumber) {
        throw "Unable to resolve project number for Pub/Sub service agent."
    }
    return "service-$ProjectNumber@gcp-sa-pubsub.iam.gserviceaccount.com"
}

function Grant-PubSubTokenCreator {
    $PubSubServiceAgent = Get-PubSubServiceAgentEmail
    & gcloud iam service-accounts add-iam-policy-binding $PubSubInvokerServiceAccountEmail --project=$ProjectId --member="serviceAccount:$PubSubServiceAgent" --role=roles/iam.serviceAccountTokenCreator --condition=None --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Pub/Sub OIDC token creator binding failed."
    }
}

function Ensure-PushSubscription {
    $PushEndpoint = "$WorkerUrl/v1/work:push"
    if (Test-GcloudResource { & gcloud pubsub subscriptions describe $PubSubSubscription --project=$ProjectId --format="value(name)" }) {
        & gcloud pubsub subscriptions update $PubSubSubscription --project=$ProjectId --push-endpoint="$PushEndpoint" --push-auth-service-account=$PubSubInvokerServiceAccountEmail --push-auth-token-audience=$WorkerUrl --dead-letter-topic=$DeadLetterTopic --max-delivery-attempts=5 | Out-Null
    }
    else {
        & gcloud pubsub subscriptions create $PubSubSubscription --project=$ProjectId --topic=$PubSubTopic --push-endpoint="$PushEndpoint" --push-auth-service-account=$PubSubInvokerServiceAccountEmail --push-auth-token-audience=$WorkerUrl --dead-letter-topic=$DeadLetterTopic --max-delivery-attempts=5 | Out-Null
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Authenticated Pub/Sub push subscription configuration failed."
    }
}

function Grant-DeadLetterAccess {
    $PubSubServiceAgent = Get-PubSubServiceAgentEmail
    & gcloud pubsub topics add-iam-policy-binding $DeadLetterTopic --project=$ProjectId --member="serviceAccount:$PubSubServiceAgent" --role=roles/pubsub.publisher --condition=None --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Dead-letter topic publisher binding failed."
    }
    & gcloud pubsub subscriptions add-iam-policy-binding $PubSubSubscription --project=$ProjectId --member="serviceAccount:$PubSubServiceAgent" --role=roles/pubsub.subscriber --condition=None --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Source subscription subscriber binding failed."
    }
}

function Ensure-Secret {
    param([string]$Name)

    if (Test-GcloudResource { & gcloud secrets describe $Name --project $ProjectId --format="value(name)" }) {
        return
    }

    & gcloud secrets create $Name --replication-policy=automatic --project $ProjectId | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Secret placeholder creation failed for '$Name'."
    }
}

function Grant-SecretAccess {
    param(
        [string]$Name,
        [string]$ServiceAccountEmail
    )

    & gcloud secrets add-iam-policy-binding $Name --project $ProjectId --member="serviceAccount:$ServiceAccountEmail" --role=roles/secretmanager.secretAccessor --condition=None --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Secret access binding failed for '$Name'."
    }
}

function Assert-SecretHasEnabledVersion {
    param([string]$Name)

    $version = (& gcloud secrets versions list $Name --project $ProjectId --filter="state=ENABLED" --limit=1 --format="value(name)")
    if ($LASTEXITCODE -ne 0 -or -not $version) {
        throw "Secret '$Name' needs an enabled version before deployment."
    }
}

function Ensure-BuildSourceBucket {
    if (Test-GcloudResource { & gcloud storage buckets describe $BuildSourceBucketUri --project $ProjectId }) {
        return
    }

    & gcloud storage buckets create $BuildSourceBucketUri --location=$Region --uniform-bucket-level-access --project=$ProjectId | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Cloud Build source bucket creation failed."
    }
}

function Grant-BuildResourceAccess {
    param([string]$ServiceAccountEmail)

    & gcloud artifacts repositories add-iam-policy-binding $ArtifactRepository --location=$Region --project=$ProjectId --member="serviceAccount:$ServiceAccountEmail" --role=roles/artifactregistry.writer --condition=None --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Artifact Registry writer binding failed for build identity."
    }

    & gcloud storage buckets add-iam-policy-binding $BuildSourceBucketUri --member="serviceAccount:$ServiceAccountEmail" --role=roles/storage.objectViewer --condition=None --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Build source bucket viewer binding failed."
    }
}

function Invoke-DeploymentSmokeTests {
    $health = Invoke-RestMethod -Method Get -Uri "$ApiUrl/health"
    if ($health.status -ne "ok") {
        throw "API health smoke test failed."
    }

    $DeviceToken = (& gcloud secrets versions access latest --secret=$DeviceTokenSecret --project=$ProjectId).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $DeviceToken) {
        throw "Unable to read the device token for authenticated smoke testing."
    }
    try {
        $headers = @{ Authorization = "Bearer $DeviceToken" }
        Invoke-RestMethod -Method Post -Uri "$ApiUrl/v1/telemetry:batch" -Headers $headers -ContentType "application/json" -Body '{"events":[]}' | Out-Null
    }
    finally {
        $DeviceToken = $null
    }
}

function Invoke-PubSubWorkerSmokeTest {
    $RunId = (& gcloud pubsub topics publish $PubSubTopic --message="malformed-smoke" --project=$ProjectId --format="value(messageIds[0])").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $RunId) {
        throw "Pub/Sub worker smoke publish failed."
    }

    $LogFilter = "resource.type=`"cloud_run_revision`" AND resource.labels.service_name=`"$WorkerService`" AND jsonPayload.message:`"worker_message_rejected`" AND jsonPayload.message:`"$RunId`""
    for ($attempt = 1; $attempt -le 6; $attempt++) {
        $LogMatch = (& gcloud logging read $LogFilter --project=$ProjectId --freshness=5m --limit=1 --format="value(jsonPayload.message)").Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Cloud Logging query failed during Pub/Sub worker smoke test."
        }
        if ($LogMatch) {
            Write-Host "Worker smoke run ID: $RunId"
            return
        }
        if ($attempt -lt 6) {
            Start-Sleep -Seconds 5
        }
    }

    throw "Worker smoke log was not found for run ID '$RunId'."
}

Assert-GcloudPrerequisites
Enable-RequiredApis
Assert-CloudBuildPermission
Ensure-ArtifactRepository
Ensure-FirestoreDatabase
Ensure-PubSubTopic $PubSubTopic
Ensure-PubSubTopic $DeadLetterTopic
$ApiServiceAccountEmail = Ensure-ServiceAccount $ApiServiceAccount "WPF Reliability API"
Grant-ProjectRoles $ApiServiceAccountEmail $ApiProjectRoles
$WorkerServiceAccountEmail = Ensure-ServiceAccount $WorkerServiceAccount "WPF Reliability Worker"
Grant-ProjectRoles $WorkerServiceAccountEmail $WorkerProjectRoles
$PubSubInvokerServiceAccountEmail = Ensure-ServiceAccount $PubSubInvokerServiceAccount "WPF Reliability PubSub Invoker"
Ensure-Secret $DeviceTokenSecret
Ensure-Secret $OperatorTokenSecret
Grant-SecretAccess $DeviceTokenSecret $ApiServiceAccountEmail
Grant-SecretAccess $OperatorTokenSecret $ApiServiceAccountEmail
Write-Host "Provision Secret Manager values through stdin before deployment:"
Write-Host "  gcloud secrets versions add $DeviceTokenSecret --project $ProjectId --data-file=-"
Write-Host "  gcloud secrets versions add $OperatorTokenSecret --project $ProjectId --data-file=-"
Assert-SecretHasEnabledVersion $DeviceTokenSecret
Assert-SecretHasEnabledVersion $OperatorTokenSecret
$BuildServiceAccountEmail = Ensure-ServiceAccount $BuildServiceAccount "WPF Reliability Cloud Build"
Grant-ProjectRoles $BuildServiceAccountEmail $BuildProjectRoles
Ensure-BuildSourceBucket
Grant-BuildResourceAccess $BuildServiceAccountEmail
Get-Command git -ErrorAction Stop | Out-Null
$GitSha = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $GitSha -notmatch "^[0-9a-f]{40}$") {
    throw "Unable to resolve the current Git SHA."
}
$ImageBase = "$Region-docker.pkg.dev/$ProjectId/$ArtifactRepository/$ImageName"
$ImageTag = "${ImageBase}:$GitSha"
$BuildId = (& gcloud builds submit src/cloud --config=src/cloud/cloudbuild.yaml --substitutions="_IMAGE_URI=$ImageTag,_GIT_SHA=$GitSha" --service-account="projects/$ProjectId/serviceAccounts/$BuildServiceAccountEmail" --gcs-source-staging-dir="$BuildSourceBucketUri/source" --project $ProjectId --region $Region --format="value(id)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $BuildId) {
    throw "Cloud Build submission failed."
}
$ImageDigest = (& gcloud artifacts docker images describe $ImageTag --project $ProjectId --format="value(image_summary.digest)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $ImageDigest) {
    throw "Unable to resolve immutable image digest."
}
$ImageDigestRef = "${ImageBase}@$ImageDigest"
& gcloud run deploy $ApiService --image=$ImageDigestRef --service-account=$ApiServiceAccountEmail --region=$Region --project=$ProjectId --min-instances=0 --max-instances=2 --memory=512Mi --port=8080 --set-env-vars="SERVICE_ROLE=api,GOOGLE_CLOUD_PROJECT=$ProjectId,DEMO_DEVICE_ID=$DemoDeviceId,PUBSUB_TOPIC=$PubSubTopic" --set-secrets="DEMO_DEVICE_TOKEN=${DeviceTokenSecret}:latest,DEMO_OPERATOR_TOKEN=${OperatorTokenSecret}:latest" --allow-unauthenticated --quiet | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "API Cloud Run deployment failed."
}
$ApiUrl = (& gcloud run services describe $ApiService --region=$Region --project=$ProjectId --format="value(status.url)").Trim()
$ApiRevision = (& gcloud run services describe $ApiService --region=$Region --project=$ProjectId --format="value(status.latestReadyRevisionName)").Trim()
& gcloud run deploy $WorkerService --image=$ImageDigestRef --service-account=$WorkerServiceAccountEmail --region=$Region --project=$ProjectId --min-instances=0 --max-instances=2 --memory=1Gi --timeout=120 --port=8080 --set-env-vars="SERVICE_ROLE=worker,GOOGLE_CLOUD_PROJECT=$ProjectId,DEMO_DEVICE_ID=worker-unused,DEMO_DEVICE_TOKEN=worker-unused,PUBSUB_TOPIC=$PubSubTopic,PUBSUB_PUSH_AUDIENCE=https://placeholder.invalid,PUBSUB_INVOKER_EMAIL=$PubSubInvokerServiceAccountEmail,GOOGLE_CLOUD_LOCATION=$VertexLocation" --no-allow-unauthenticated --quiet | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Worker Cloud Run deployment failed."
}
$WorkerUrl = (& gcloud run services describe $WorkerService --region=$Region --project=$ProjectId --format="value(status.url)").Trim()
& gcloud run services update $WorkerService --region=$Region --project=$ProjectId --update-env-vars="PUBSUB_PUSH_AUDIENCE=$WorkerUrl" --quiet | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Worker audience update failed."
}
Grant-WorkerInvoker $PubSubInvokerServiceAccountEmail
$WorkerRevision = (& gcloud run services describe $WorkerService --region=$Region --project=$ProjectId --format="value(status.latestReadyRevisionName)").Trim()
Grant-PubSubTokenCreator
Ensure-PushSubscription
Grant-DeadLetterAccess
Invoke-DeploymentSmokeTests
Invoke-PubSubWorkerSmokeTest
Write-Host "API URL: $ApiUrl"
Write-Host "Cloud Build ID: $BuildId"
Write-Host "Image digest: $ImageDigestRef"
Write-Host "API revision: $ApiRevision"
Write-Host "Worker revision: $WorkerRevision"
Write-Host "Windows configuration:"
Write-Host "  WPF_RELIABILITY_API_BASE_URI=$ApiUrl"
Write-Host "  WPF_RELIABILITY_DEVICE_ID=$DemoDeviceId"
Write-Host "  WPF_RELIABILITY_DEVICE_TOKEN=<Secret Manager device token>"
