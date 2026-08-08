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
