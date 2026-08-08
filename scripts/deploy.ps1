param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$Region = "asia-east1",
    [string]$ApiService = "reliability-api",
    [string]$WorkerService = "reliability-worker",
    [string]$ArtifactRepository = "reliability-agent",
    [string]$PubSubTopic = "incident-work",
    [string]$ImageName = "reliability-agent",
    [string]$ApiServiceAccount = "reliability-api-sa",
    [string]$WorkerServiceAccount = "reliability-worker-sa",
    [string]$PubSubInvokerServiceAccount = "pubsub-invoker-sa",
    [string]$BuildServiceAccount = "reliability-build-sa"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RequiredApis = @(
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com"
)

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
    & gcloud services enable $RequiredApis --project $ProjectId
    if ($LASTEXITCODE -ne 0) {
        throw "Required API enablement failed for '$ProjectId'."
    }
}

Assert-GcloudPrerequisites
Enable-RequiredApis
Assert-CloudBuildPermission
