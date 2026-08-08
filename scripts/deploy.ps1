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

function Ensure-ArtifactRepository {
    & gcloud artifacts repositories describe $ArtifactRepository --location $Region --project $ProjectId --format="value(name)" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        return
    }

    & gcloud artifacts repositories create $ArtifactRepository --repository-format=docker --location $Region --project $ProjectId
    if ($LASTEXITCODE -ne 0) {
        throw "Artifact Registry repository creation failed."
    }
}

function Ensure-FirestoreDatabase {
    & gcloud firestore databases describe --database="(default)" --project $ProjectId --format="value(name)" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        return
    }

    & gcloud firestore databases create --database="(default)" --location=$Region --type=firestore-native --project $ProjectId
    if ($LASTEXITCODE -ne 0) {
        throw "Firestore database creation failed."
    }
}

function Ensure-PubSubTopic {
    & gcloud pubsub topics describe $PubSubTopic --project $ProjectId --format="value(name)" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        return
    }

    & gcloud pubsub topics create $PubSubTopic --project $ProjectId
    if ($LASTEXITCODE -ne 0) {
        throw "Pub/Sub topic creation failed."
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
    & gcloud iam service-accounts describe $email --project $ProjectId --format="value(email)" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
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

Assert-GcloudPrerequisites
Enable-RequiredApis
Assert-CloudBuildPermission
Ensure-ArtifactRepository
Ensure-FirestoreDatabase
Ensure-PubSubTopic
$ApiServiceAccountEmail = Ensure-ServiceAccount $ApiServiceAccount "WPF Reliability API"
Grant-ProjectRoles $ApiServiceAccountEmail $ApiProjectRoles
$WorkerServiceAccountEmail = Ensure-ServiceAccount $WorkerServiceAccount "WPF Reliability Worker"
Grant-ProjectRoles $WorkerServiceAccountEmail $WorkerProjectRoles
