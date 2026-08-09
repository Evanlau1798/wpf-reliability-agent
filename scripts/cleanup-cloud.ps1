param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [Parameter(Mandatory = $true)]
    [string]$ConfirmProjectId,
    [string]$Region = "asia-east1",
    [string]$ApiService = "reliability-api",
    [string]$WorkerService = "reliability-worker",
    [string]$ArtifactRepository = "reliability-agent",
    [string]$PubSubTopic = "incident-work",
    [string]$PubSubSubscription = "incident-work-push",
    [string]$DeadLetterTopic = "incident-work-dead-letter",
    [string]$ApiServiceAccount = "reliability-api-sa",
    [string]$WorkerServiceAccount = "reliability-worker-sa",
    [string]$PubSubInvokerServiceAccount = "pubsub-invoker-sa",
    [string]$BuildServiceAccount = "reliability-build-sa",
    [string]$DeviceTokenSecret = "reliability-device-token",
    [string]$OperatorTokenSecret = "reliability-operator-token",
    [string]$BuildSourceBucket = "",
    [switch]$DeleteFirestore
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($ConfirmProjectId -cne $ProjectId) {
    throw "ConfirmProjectId must exactly match ProjectId."
}

Get-Command gcloud -ErrorAction Stop | Out-Null
$ResolvedProjectId = (& gcloud projects describe $ProjectId --format="value(projectId)").Trim()
if ($LASTEXITCODE -ne 0 -or $ResolvedProjectId -cne $ProjectId) {
    throw "Project access check failed for '$ProjectId'."
}

if (-not $BuildSourceBucket) {
    $BuildSourceBucket = "$ProjectId-reliability-build-source"
}
$BuildSourceBucketUri = "gs://$BuildSourceBucket"
$ApiServiceAccountEmail = "$ApiServiceAccount@$ProjectId.iam.gserviceaccount.com"
$WorkerServiceAccountEmail = "$WorkerServiceAccount@$ProjectId.iam.gserviceaccount.com"
$PubSubInvokerServiceAccountEmail = "$PubSubInvokerServiceAccount@$ProjectId.iam.gserviceaccount.com"
$BuildServiceAccountEmail = "$BuildServiceAccount@$ProjectId.iam.gserviceaccount.com"

function Assert-GcloudSuccess {
    param([string]$Action)

    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed."
    }
}

& gcloud run services delete $ApiService --region=$Region --project=$ProjectId --quiet | Out-Null
Assert-GcloudSuccess "API Cloud Run deletion"
& gcloud run services delete $WorkerService --region=$Region --project=$ProjectId --quiet | Out-Null
Assert-GcloudSuccess "Worker Cloud Run deletion"

& gcloud pubsub subscriptions delete $PubSubSubscription --project=$ProjectId --quiet | Out-Null
Assert-GcloudSuccess "Pub/Sub subscription deletion"
& gcloud pubsub topics delete $PubSubTopic --project=$ProjectId --quiet | Out-Null
Assert-GcloudSuccess "Pub/Sub topic deletion"
& gcloud pubsub topics delete $DeadLetterTopic --project=$ProjectId --quiet | Out-Null
Assert-GcloudSuccess "Pub/Sub dead-letter topic deletion"

& gcloud secrets delete $DeviceTokenSecret --project=$ProjectId --quiet | Out-Null
Assert-GcloudSuccess "Device token secret deletion"
& gcloud secrets delete $OperatorTokenSecret --project=$ProjectId --quiet | Out-Null
Assert-GcloudSuccess "Operator token secret deletion"

& gcloud artifacts repositories delete $ArtifactRepository --location=$Region --project=$ProjectId --quiet | Out-Null
Assert-GcloudSuccess "Artifact Registry deletion"

& gcloud storage rm --recursive "$BuildSourceBucketUri/" --project=$ProjectId --quiet 2>$null | Out-Null
& gcloud storage buckets delete $BuildSourceBucketUri --project=$ProjectId --quiet | Out-Null
Assert-GcloudSuccess "Cloud Build source bucket deletion"

$ApiProjectRoles = @("roles/datastore.user", "roles/logging.logWriter", "roles/pubsub.publisher")
$WorkerProjectRoles = @("roles/aiplatform.user", "roles/datastore.user", "roles/logging.logWriter")
$BuildProjectRoles = @("roles/logging.logWriter")
foreach ($Role in $ApiProjectRoles) {
    & gcloud projects remove-iam-policy-binding $ProjectId --member="serviceAccount:$ApiServiceAccountEmail" --role=$Role --condition=None --project=$ProjectId --quiet | Out-Null
    Assert-GcloudSuccess "API project IAM cleanup"
}
foreach ($Role in $WorkerProjectRoles) {
    & gcloud projects remove-iam-policy-binding $ProjectId --member="serviceAccount:$WorkerServiceAccountEmail" --role=$Role --condition=None --project=$ProjectId --quiet | Out-Null
    Assert-GcloudSuccess "Worker project IAM cleanup"
}
foreach ($Role in $BuildProjectRoles) {
    & gcloud projects remove-iam-policy-binding $ProjectId --member="serviceAccount:$BuildServiceAccountEmail" --role=$Role --condition=None --project=$ProjectId --quiet | Out-Null
    Assert-GcloudSuccess "Build project IAM cleanup"
}

foreach ($ServiceAccountEmail in @($ApiServiceAccountEmail, $WorkerServiceAccountEmail, $PubSubInvokerServiceAccountEmail, $BuildServiceAccountEmail)) {
    & gcloud iam service-accounts delete $ServiceAccountEmail --project=$ProjectId --quiet | Out-Null
    Assert-GcloudSuccess "Service account deletion"
}

if ($DeleteFirestore) {
    & gcloud firestore databases delete --database="(default)" --project=$ProjectId --quiet | Out-Null
    Assert-GcloudSuccess "Firestore database deletion"
}

Write-Host "Cleanup completed for project '$ProjectId'."
